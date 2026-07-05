"""The hardware seam for the climate sensor.

A sensor ceapp is JUST a producer of readings. Everything above the driver is
hardware-agnostic, so swapping the mock for a real I2C sensor (SHT31 / BME280 on the
UNO Q's ``/dev/i2c-*``) is a one-class change — plug and play — with no edits to the
service, the wire schema, or any consumer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Sample:
    """One instantaneous reading from the environment."""

    temperature_c: float
    humidity_pct: float


@runtime_checkable
class Driver(Protocol):
    """Reads the current temperature/humidity. The only hardware-specific interface."""

    def read(self) -> Sample: ...


class MockDriver:
    """Deterministic synthetic climate data — no hardware required.

    Produces plausible values that drift smoothly over successive reads (a slow sine
    around a base point) so a live demo looks real and tests are reproducible. Each
    :meth:`read` advances one step.
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


class I2cDriver:
    """Placeholder for a real I2C temperature/humidity sensor (SHT31 / BME280).

    Wiring the real device is deliberately isolated here: implement :meth:`read` against
    ``/dev/i2c-<bus>`` and swap this in for :class:`MockDriver` in ``main.py``. Nothing
    else in the app or the mesh changes.
    """

    def __init__(self, bus: int = 1, address: int = 0x44) -> None:
        self.bus = bus
        self.address = address

    def read(self) -> Sample:  # pragma: no cover - hardware path
        raise NotImplementedError(
            "I2cDriver is a hardware plug-in point: implement read() against "
            f"/dev/i2c-{self.bus} (addr {hex(self.address)}) for a real SHT31/BME280."
        )
