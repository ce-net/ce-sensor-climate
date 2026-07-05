"""The hardware seam for the climate sensor — mock and REAL I2C drivers, auto-detected.

A sensor ceapp is JUST a producer of readings. Everything above the driver is
hardware-agnostic. The SAME app runs on the Mac (no sensor -> mock) and on an Arduino UNO Q
with a real I2C temp/humidity chip wired to the MPU's `/dev/i2c-*` (auto-detected -> real).
That is the plug-and-play Leif asked for: deploy the app, and if the hardware is present it
is used, with no edit and no per-device config.

Real chips supported by stdlib I2C (no pip): SHT3x (0x44/0x45), BME280 (0x76/0x77),
AHT20 (0x38). Add another by writing one small class + one probe entry — nothing else moves.
The I2C access is via `fcntl.ioctl` so there is no external dependency.
"""

from __future__ import annotations

import math
import os
import struct
import time
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

_I2C_SLAVE = 0x0703  # linux/i2c-dev.h


@dataclass(frozen=True)
class Sample:
    """One instantaneous reading from the environment."""

    temperature_c: float
    humidity_pct: float


@runtime_checkable
class Driver(Protocol):
    """Reads the current temperature/humidity. The only hardware-specific interface."""

    def read(self) -> Sample: ...


# --------------------------------------------------------------------------- mock

class MockDriver:
    """Deterministic synthetic climate data — no hardware required.

    Plausible values that drift smoothly over successive reads (a slow sine) so a live demo
    looks real and tests are reproducible. Each :meth:`read` advances one step.
    """

    def __init__(self, base_temp_c: float = 21.0, base_humidity_pct: float = 45.0,
                 step: int = 0) -> None:
        self.base_temp_c = base_temp_c
        self.base_humidity_pct = base_humidity_pct
        self.step = step

    def read(self) -> Sample:
        t = self.base_temp_c + 1.5 * math.sin(self.step / 12.0)
        h = self.base_humidity_pct + 5.0 * math.sin(self.step / 7.0 + 1.0)
        self.step += 1
        return Sample(temperature_c=round(t, 2), humidity_pct=round(h, 2))


# ----------------------------------------------------------------------- real I2C

class I2CBus:
    """A minimal Linux I2C bus over `/dev/i2c-<n>` using `fcntl.ioctl` (stdlib only).

    Injectable: chip drivers take a bus object with `write`/`read`/`write_read`, so their
    decode logic is unit-tested with a fake bus and no hardware.
    """

    def __init__(self, bus: int) -> None:
        self.bus = bus
        self.fd = os.open(f"/dev/i2c-{bus}", os.O_RDWR)

    def _select(self, addr: int) -> None:
        import fcntl
        fcntl.ioctl(self.fd, _I2C_SLAVE, addr)

    def write(self, addr: int, data: bytes) -> None:
        self._select(addr)
        os.write(self.fd, data)

    def read(self, addr: int, length: int) -> bytes:
        self._select(addr)
        return os.read(self.fd, length)

    def write_read(self, addr: int, data: bytes, length: int, delay_s: float = 0.0) -> bytes:
        self.write(addr, data)
        if delay_s:
            time.sleep(delay_s)
        return self.read(addr, length)

    def probe(self, addr: int) -> bool:
        try:
            self._select(addr)
            os.read(self.fd, 1)
            return True
        except OSError:
            return False

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass


def _crc8_sht(data: bytes) -> int:
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


class Sht3xDriver:
    """Sensirion SHT3x (SHT30/31/35) — single-shot, high repeatability, no clock stretch."""

    def __init__(self, bus: I2CBus, addr: int = 0x44) -> None:
        self.bus = bus
        self.addr = addr

    def read(self) -> Sample:
        raw = self.bus.write_read(self.addr, b"\x24\x00", 6, delay_s=0.02)
        if len(raw) != 6:
            raise OSError("SHT3x: short read")
        if _crc8_sht(raw[0:2]) != raw[2] or _crc8_sht(raw[3:5]) != raw[5]:
            raise OSError("SHT3x: CRC mismatch")
        t_raw = (raw[0] << 8) | raw[1]
        h_raw = (raw[3] << 8) | raw[4]
        temp = -45.0 + 175.0 * (t_raw / 65535.0)
        hum = 100.0 * (h_raw / 65535.0)
        return Sample(round(temp, 2), round(max(0.0, min(100.0, hum)), 2))


class Aht20Driver:
    """Aosong AHT20 — trigger measurement, read 6 bytes (status + 20-bit H + 20-bit T)."""

    def __init__(self, bus: I2CBus, addr: int = 0x38) -> None:
        self.bus = bus
        self.addr = addr

    def read(self) -> Sample:
        raw = self.bus.write_read(self.addr, b"\xac\x33\x00", 7, delay_s=0.08)
        if len(raw) < 6 or (raw[0] & 0x80):
            raise OSError("AHT20: busy / short read")
        h_raw = (raw[1] << 12) | (raw[2] << 4) | (raw[3] >> 4)
        t_raw = ((raw[3] & 0x0F) << 16) | (raw[4] << 8) | raw[5]
        hum = 100.0 * (h_raw / 1048576.0)
        temp = -50.0 + 200.0 * (t_raw / 1048576.0)
        return Sample(round(temp, 2), round(max(0.0, min(100.0, hum)), 2))


class Bme280Driver:
    """Bosch BME280 — reads factory calibration once, then compensates T + H per datasheet."""

    def __init__(self, bus: I2CBus, addr: int = 0x76) -> None:
        self.bus = bus
        self.addr = addr
        self._load_calibration()
        self.bus.write(addr, b"\xf2\x01")        # ctrl_hum: humidity oversampling x1
        self.bus.write(addr, b"\xf4\x27")        # ctrl_meas: T/P oversampling x1, normal mode

    def _cal(self, reg: int, length: int) -> bytes:
        return self.bus.write_read(self.addr, bytes([reg]), length)

    def _load_calibration(self) -> None:
        t = self._cal(0x88, 6)
        self.dig_T1 = struct.unpack("<H", t[0:2])[0]
        self.dig_T2, self.dig_T3 = struct.unpack("<hh", t[2:6])
        h1 = self._cal(0xA1, 1)[0]
        h = self._cal(0xE1, 7)
        self.dig_H1 = h1
        self.dig_H2 = struct.unpack("<h", h[0:2])[0]
        self.dig_H3 = h[2]
        self.dig_H4 = (h[3] << 4) | (h[4] & 0x0F)
        self.dig_H5 = (h[5] << 4) | (h[4] >> 4)
        self.dig_H6 = struct.unpack("b", bytes([h[6]]))[0]

    def read(self) -> Sample:
        d = self._cal(0xF7, 8)
        adc_t = (d[3] << 12) | (d[4] << 4) | (d[5] >> 4)
        adc_h = (d[6] << 8) | d[7]
        var1 = (adc_t / 16384.0 - self.dig_T1 / 1024.0) * self.dig_T2
        var2 = ((adc_t / 131072.0 - self.dig_T1 / 8192.0) ** 2) * self.dig_T3
        t_fine = var1 + var2
        temp = t_fine / 5120.0
        h = t_fine - 76800.0
        h = ((adc_h - (self.dig_H4 * 64.0 + self.dig_H5 / 16384.0 * h)) *
             (self.dig_H2 / 65536.0 * (1.0 + self.dig_H6 / 67108864.0 * h *
              (1.0 + self.dig_H3 / 67108864.0 * h))))
        h = h * (1.0 - self.dig_H1 * h / 524288.0)
        return Sample(round(temp, 2), round(max(0.0, min(100.0, h)), 2))


# Probe order: (address, factory) — first that responds AND reads cleanly wins. AHT20 (0x38)
# is the atech temperature/humidity module (docs/atech-modules.md), so it is tried first; the
# others are generic (non-atech) chips supported as a convenience.
_PROBES = [
    (0x38, lambda bus, a: Aht20Driver(bus, a)),   # atech AHT20 module
    (0x44, lambda bus, a: Sht3xDriver(bus, a)),
    (0x45, lambda bus, a: Sht3xDriver(bus, a)),
    (0x76, lambda bus, a: Bme280Driver(bus, a)),
    (0x77, lambda bus, a: Bme280Driver(bus, a)),
]


def detect_i2c_driver(buses=(1, 0)) -> Optional[Driver]:
    """Find a supported real temp/humidity chip on the given I2C buses. None if absent.

    Tries each known (address, chip): the device must both ACK on the bus and return a
    physically sane first reading, so a wrong-chip-at-that-address does not match.
    """
    for bus_no in buses:
        try:
            bus = I2CBus(bus_no)
        except OSError:
            continue
        for addr, make in _PROBES:
            if not bus.probe(addr):
                continue
            try:
                drv = make(bus, addr)
                s = drv.read()
            except OSError:
                continue
            if -40.0 <= s.temperature_c <= 85.0 and 0.0 <= s.humidity_pct <= 100.0:
                return drv
        bus.close()
    return None


SOURCE_MODES = ("auto", "mock", "real")


def select_driver(mode: str = "auto", buses=(1, 0)) -> Driver:
    """Pick the driver by source mode — switchable on demand (startup env or the live API):

    - ``auto``  : atech serial board if present, else a direct I2C chip, else mock.
    - ``mock``  : synthetic data (lets an end-to-end test run with no hardware).
    - ``real`` / ``atech`` : the atech USB-serial AHT20 (falls back to direct I2C); raises if
      no hardware — this is the SDK-faithful atech-module path (docs/atech-modules.md).
    - ``i2c``   : a temp/humidity chip wired directly to the node's I2C header (AHT20 first).
    """
    mode = (mode or "auto").lower()
    if mode == "mock":
        return MockDriver()

    # UNO Q: the sensor is on the STM32; read it via the Arduino Router bridge.
    if mode in ("auto", "real", "bridge"):
        from .bridge import detect_bridge  # lazy import
        bridge = detect_bridge()
        if bridge is not None:
            return bridge
        if mode == "bridge":
            raise OSError("no AHT20 reading on the Arduino Router bridge (STM32 sketch flashed?)")

    if mode in ("auto", "real", "atech"):
        from .atech import detect_atech_serial  # lazy: avoids a circular import
        atech = detect_atech_serial()
        if atech is not None:
            return atech

    real = detect_i2c_driver(buses)
    if real is not None:
        return real

    if mode in ("real", "atech", "i2c"):
        raise OSError("no bridge, atech serial, or I2C temp/humidity sensor found")
    return MockDriver()
