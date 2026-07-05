"""atech-native driver: read temperature/humidity from the atech USB-serial envelope.

This is the SDK-faithful integration for an atech AHT20 module (see docs/atech-modules.md):
an atech ESP32 board streams one compact JSON line per event over USB-serial —
``{"type":"event","payload":{"event_type":"sensor","key":"<inst>_temperature","value":..,
"unit":"C","source":"aht20"}}`` (and ``_humidity``, unit ``%``), every ~2 s. This driver reads
those lines and caches the latest AHT20 temperature + humidity.

The envelope parser and the cache are pure (no I/O), so they are unit-tested by feeding lines;
:class:`AtechSerialDriver` adds the stdlib serial transport (no pyserial dependency).
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional

from .driver import Sample

AHT20_SOURCE = "aht20"
DEFAULT_DEVICES = ("/dev/ttyACM0", "/dev/ttyUSB0")


def parse_atech_line(line: str) -> Optional[dict]:
    """Unwrap one atech serial line to its event payload dict, or None if it is not an event.

    Handles the wrapped envelope (``{"type":"event","payload":{...}}``) and ignores boot lines
    and non-JSON log noise.
    """
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    if obj.get("type") == "event" and isinstance(obj.get("payload"), dict):
        return obj["payload"]
    return None


class AtechCache:
    """Caches the latest AHT20 temperature + humidity from fed envelope lines (pure)."""

    def __init__(self) -> None:
        self.temperature: Optional[float] = None
        self.humidity: Optional[float] = None
        self.updated_at: float = 0.0

    def feed(self, line: str, now: float) -> bool:
        payload = parse_atech_line(line)
        if (not payload or payload.get("event_type") != "sensor"
                or payload.get("source") != AHT20_SOURCE):
            return False
        try:
            value = float(payload.get("value"))
        except (TypeError, ValueError):
            return False
        key = str(payload.get("key", ""))
        if key.endswith("temperature"):
            self.temperature = value
        elif key.endswith("humidity"):
            self.humidity = value
        else:
            return False
        self.updated_at = now
        return True

    def sample(self) -> Sample:
        if self.temperature is None or self.humidity is None:
            raise OSError("atech: no AHT20 temperature+humidity received yet")
        return Sample(round(self.temperature, 2), round(self.humidity, 2))


class AtechSerialDriver:
    """Reads the atech USB-serial envelope from a board and returns cached AHT20 readings."""

    def __init__(self, device: str = DEFAULT_DEVICES[0], baud: int = 115200,
                 now=time.time) -> None:
        self.device = device
        self.cache = AtechCache()
        self._now = now
        self._fd = self._open_serial(device, baud)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._read_loop, name="atech-serial", daemon=True)
        self._thread.start()

    @staticmethod
    def _open_serial(device: str, baud: int) -> int:
        fd = os.open(device, os.O_RDONLY | os.O_NOCTTY)
        try:  # best-effort raw + baud; USB-CDC (/dev/ttyACM*) often streams without it
            import termios
            attrs = termios.tcgetattr(fd)
            speed = getattr(termios, f"B{baud}", termios.B115200)
            attrs[4] = speed
            attrs[5] = speed
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
        except Exception:  # noqa: BLE001 - termios unavailable / not a tty: read raw
            pass
        return fd

    def _read_loop(self) -> None:
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = os.read(self._fd, 256)
            except OSError:
                break
            if not chunk:
                time.sleep(0.05)
                continue
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self.cache.feed(line.decode("utf-8", "replace"), self._now())

    def read(self) -> Sample:
        return self.cache.sample()

    def close(self) -> None:
        self._stop.set()
        try:
            os.close(self._fd)
        except OSError:
            pass


def detect_atech_serial(devices=DEFAULT_DEVICES) -> Optional[AtechSerialDriver]:
    """Open the first present atech serial device and wait briefly for a valid AHT20 reading."""
    for device in devices:
        if not os.path.exists(device):
            continue
        try:
            drv = AtechSerialDriver(device)
        except OSError:
            continue
        # give the board up to ~2.5 s to stream a temperature+humidity pair
        deadline = time.time() + 2.5
        while time.time() < deadline:
            try:
                drv.read()
                return drv
            except OSError:
                time.sleep(0.1)
        drv.close()
    return None
