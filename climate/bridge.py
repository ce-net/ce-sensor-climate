"""BridgeDriver — read AHT20 readings from the STM32 via the Arduino Router bridge (UNO Q).

On the Arduino UNO Q the sensor is wired to the STM32 microcontroller's I2C (header pins), not
the Linux processor. Our STM32 sketch (ce-uno-firmware/aht20) reads the AHT20 and sends each
reading over the Router Bridge; on Linux the bridge exposes it on /var/run/arduino-router.sock
as a msgpack "aht20_reading" notification carrying "T:<c>,H:<%>". This driver registers for that
event and returns the latest reading — so the same ce-sensor-climate app publishes it to the mesh
exactly like a direct-I2C sensor. No pyserial/msgpack dependency: the minimal codec is inline.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Optional

from .driver import Sample

SOCKET_PATH = "/var/run/arduino-router.sock"
EVENT = "aht20_reading"


def _enc(o) -> bytes:
    """Minimal msgpack encode (ints<128, str<32, list<16 — enough for the $/register call)."""
    if isinstance(o, int):
        return bytes([o])
    if isinstance(o, str):
        b = o.encode("utf-8")
        return bytes([0xA0 | len(b)]) + b
    if isinstance(o, list):
        return bytes([0x90 | len(o)]) + b"".join(_enc(x) for x in o)
    raise ValueError(o)


def _dec(buf: bytes, i: int = 0):
    """Minimal msgpack decode. Raises IndexError if the buffer is incomplete."""
    b = buf[i]
    if b < 0x80:
        return b, i + 1                                   # positive fixint
    if 0xA0 <= b <= 0xBF:                                 # fixstr
        n = b & 0x1F
        return buf[i + 1:i + 1 + n].decode("utf-8", "replace"), i + 1 + n
    if 0x90 <= b <= 0x9F:                                 # fixarray
        n = b & 0x0F
        i += 1
        out = []
        for _ in range(n):
            v, i = _dec(buf, i)
            out.append(v)
        return out, i
    if b == 0xC0:
        return None, i + 1                                # nil
    if b in (0xC2, 0xC3):
        return b == 0xC3, i + 1                           # bool
    if b == 0xCC:
        return buf[i + 1], i + 2                          # uint8
    if b == 0xD9:                                         # str8
        n = buf[i + 1]
        return buf[i + 2:i + 2 + n].decode("utf-8", "replace"), i + 2 + n
    if b == 0xDC:                                         # array16
        n = (buf[i + 1] << 8) | buf[i + 2]
        i += 3
        out = []
        for _ in range(n):
            v, i = _dec(buf, i)
            out.append(v)
        return out, i
    raise ValueError(f"unhandled msgpack byte 0x{b:02x}")


def parse_payload(payload: str):
    """"T:23.71,H:51.41" -> (23.71, 51.41), or None on a bad/ERR payload."""
    if not payload.startswith("T:"):
        return None
    try:
        parts = payload.split(",")
        return float(parts[0][2:]), float(parts[1][2:])
    except (ValueError, IndexError):
        return None


class BridgeDriver:
    """Streams the latest AHT20 reading from the Arduino Router bridge in a background thread."""

    def __init__(self, socket_path: str = SOCKET_PATH, event: str = EVENT) -> None:
        self.socket_path = socket_path
        self.event = event
        self.temperature: Optional[float] = None
        self.humidity: Optional[float] = None
        self.updated_at = 0.0
        threading.Thread(target=self._reader, name="aht20-bridge", daemon=True).start()

    def _reader(self) -> None:
        while True:
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(self.socket_path)
                s.sendall(_enc([0, 1, "$/register", [self.event]]))
                buf = b""
                while True:
                    data = s.recv(4096)
                    if not data:
                        break
                    buf += data
                    while buf:
                        try:
                            msg, ni = _dec(buf, 0)
                        except IndexError:
                            break
                        except ValueError:
                            buf = buf[1:]
                            continue
                        buf = buf[ni:]
                        if (isinstance(msg, list) and len(msg) >= 3 and msg[0] == 2
                                and msg[1] == self.event and msg[2]):
                            parsed = parse_payload(str(msg[2][0]))
                            if parsed:
                                self.temperature, self.humidity = parsed
                                self.updated_at = time.time()
            except OSError:
                time.sleep(2)  # bridge/router not ready; retry

    def read(self) -> Sample:
        if self.temperature is None or self.humidity is None:
            raise OSError("no bridge reading yet (is the STM32 aht20 sketch flashed + running?)")
        return Sample(round(self.temperature, 2), round(self.humidity, 2))


def detect_bridge() -> Optional[BridgeDriver]:
    """Return a bridge driver if the router socket is present AND a reading arrives shortly."""
    import os
    if not os.path.exists(SOCKET_PATH):
        return None
    drv = BridgeDriver()
    deadline = time.time() + 4.0
    while time.time() < deadline:
        try:
            drv.read()
            return drv
        except OSError:
            time.sleep(0.2)
    return None
