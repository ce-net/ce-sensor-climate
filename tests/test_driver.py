"""Unit tests for the climate driver seam."""

from __future__ import annotations

import pytest

from climate.driver import I2cDriver, MockDriver, Sample


def test_mock_driver_is_deterministic_and_advances():
    a = MockDriver()
    b = MockDriver()
    s1 = a.read()
    s2 = b.read()
    assert s1 == s2  # same seed -> same first sample
    assert a.step == 1
    s3 = a.read()
    assert s3 != s1  # value drifts across reads


def test_mock_driver_values_in_plausible_range():
    d = MockDriver()
    for _ in range(50):
        s = d.read()
        assert isinstance(s, Sample)
        assert 15.0 <= s.temperature_c <= 27.0
        assert 30.0 <= s.humidity_pct <= 60.0


def test_i2c_driver_is_an_unimplemented_plugin_point():
    with pytest.raises(NotImplementedError):
        I2cDriver().read()
