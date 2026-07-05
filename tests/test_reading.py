"""Unit tests for the reading wire schema."""

from __future__ import annotations

import pytest

from climate.reading import READING_SCHEMA, decode_reading, encode_reading


def test_encode_decode_roundtrip():
    frame = {
        "schema": READING_SCHEMA, "sensor": "ce-sensor-climate", "node": "aa",
        "instance": "x", "ts": 1.0,
        "readings": [{"metric": "temperature", "value": 21.4, "unit": "C"}],
    }
    assert decode_reading(encode_reading(frame)) == frame


def test_decode_rejects_wrong_schema():
    with pytest.raises(ValueError):
        decode_reading(b'{"schema":"other/9"}')
