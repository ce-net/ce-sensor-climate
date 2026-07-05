"""Unit tests for the climate driver seam — mock + real chip decode via a fake I2C bus.

The chip drivers take an injectable bus, so their real conversion math is tested with canned
register bytes and no hardware.
"""

from __future__ import annotations

from climate.driver import (
    Aht20Driver,
    MockDriver,
    Sample,
    Sht3xDriver,
    _crc8_sht,
    select_driver,
)


def test_mock_driver_is_deterministic_and_advances():
    a, b = MockDriver(), MockDriver()
    s1, s2 = a.read(), b.read()
    assert s1 == s2 and a.step == 1
    assert a.read() != s1  # drifts across reads


def test_mock_driver_values_in_plausible_range():
    d = MockDriver()
    for _ in range(50):
        s = d.read()
        assert isinstance(s, Sample)
        assert 15.0 <= s.temperature_c <= 27.0 and 30.0 <= s.humidity_pct <= 60.0


class _FakeBus:
    """Returns canned bytes for write_read; records writes."""

    def __init__(self, response: bytes):
        self.response = response
        self.writes = []

    def write(self, addr, data):
        self.writes.append((addr, data))

    def read(self, addr, length):
        return self.response[:length]

    def write_read(self, addr, data, length, delay_s=0.0):
        self.writes.append((addr, data))
        return self.response[:length]


def test_sht3x_decodes_known_frame():
    # 25 C, 50 %RH -> t_raw ~0x6666, h_raw ~0x8000, with correct CRCs.
    t_raw = round((25.0 + 45.0) / 175.0 * 65535)
    h_raw = round(50.0 / 100.0 * 65535)
    tb = bytes([t_raw >> 8, t_raw & 0xFF])
    hb = bytes([h_raw >> 8, h_raw & 0xFF])
    frame = tb + bytes([_crc8_sht(tb)]) + hb + bytes([_crc8_sht(hb)])
    s = Sht3xDriver(_FakeBus(frame)).read()
    assert abs(s.temperature_c - 25.0) < 0.1
    assert abs(s.humidity_pct - 50.0) < 0.1


def test_sht3x_rejects_bad_crc():
    bad = bytes([0x66, 0x66, 0x00, 0x80, 0x00, 0x00])  # wrong CRC bytes
    try:
        Sht3xDriver(_FakeBus(bad)).read()
        assert False, "expected CRC failure"
    except OSError:
        pass


def test_aht20_decodes_known_frame():
    # status ok (0x00), H=50% -> h_raw=0.5*2^20, T=25C -> t_raw=(25+50)/200*2^20
    h_raw = round(0.5 * 1048576)
    t_raw = round((25.0 + 50.0) / 200.0 * 1048576)
    b1 = (h_raw >> 12) & 0xFF
    b2 = (h_raw >> 4) & 0xFF
    b3 = ((h_raw & 0x0F) << 4) | ((t_raw >> 16) & 0x0F)
    b4 = (t_raw >> 8) & 0xFF
    b5 = t_raw & 0xFF
    frame = bytes([0x00, b1, b2, b3, b4, b5, 0x00])
    s = Aht20Driver(_FakeBus(frame)).read()
    assert abs(s.temperature_c - 25.0) < 0.2 and abs(s.humidity_pct - 50.0) < 0.2


def test_select_driver_mock_forces_synthetic():
    assert isinstance(select_driver("mock"), MockDriver)


def test_select_driver_auto_falls_back_to_mock_without_hardware():
    # No /dev/i2c on the test host -> auto yields mock, never raises.
    assert isinstance(select_driver("auto", buses=(99,)), MockDriver)
