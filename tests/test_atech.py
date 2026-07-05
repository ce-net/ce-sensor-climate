"""Unit tests for the atech serial envelope parser + cache (no serial port needed).

Feeds the exact wire lines the atech SDK emits (docs/atech-modules.md) and checks the AHT20
temperature/humidity are extracted with the right values.
"""

from __future__ import annotations

import pytest

from climate.atech import AtechCache, parse_atech_line

TEMP_LINE = ('{"type":"event","payload":{"event_type":"sensor","key":"thermo_temperature",'
             '"value":21.37,"unit":"C","source":"aht20"}}')
HUM_LINE = ('{"type":"event","payload":{"event_type":"sensor","key":"thermo_humidity",'
            '"value":48.9,"unit":"%","source":"aht20"}}')


def test_parse_unwraps_event_payload():
    p = parse_atech_line(TEMP_LINE)
    assert p["event_type"] == "sensor" and p["source"] == "aht20"
    assert p["key"] == "thermo_temperature" and p["value"] == 21.37


def test_parse_ignores_boot_and_noise():
    assert parse_atech_line('{"type":"boot","payload":{"modules":[]}}') is None
    assert parse_atech_line("I (123) wifi: some log line") is None
    assert parse_atech_line("") is None
    assert parse_atech_line("{not json") is None


def test_cache_extracts_aht20_temperature_and_humidity():
    c = AtechCache()
    assert c.feed(TEMP_LINE, 1.0) is True
    assert c.feed(HUM_LINE, 1.0) is True
    s = c.sample()
    assert s.temperature_c == 21.37 and s.humidity_pct == 48.9


def test_cache_needs_both_before_sampling():
    c = AtechCache()
    c.feed(TEMP_LINE, 1.0)
    with pytest.raises(OSError):
        c.sample()  # humidity not seen yet


def test_cache_ignores_other_sources_and_pir():
    c = AtechCache()
    pir = ('{"type":"event","payload":{"event_type":"sensor","key":"motion","value":1,'
           '"source":"pir"}}')
    assert c.feed(pir, 1.0) is False
    assert c.feed('{"type":"event","payload":{"event_type":"button","key":"b","value":1,'
                  '"source":"button"}}', 1.0) is False


def test_cache_accepts_stringified_value():
    # firmware may stringify the value; the cache tolerates it
    c = AtechCache()
    line = ('{"type":"event","payload":{"event_type":"sensor","key":"t_temperature",'
            '"value":"22.5","source":"aht20"}}')
    hum = ('{"type":"event","payload":{"event_type":"sensor","key":"t_humidity",'
           '"value":"50","source":"aht20"}}')
    c.feed(line, 1.0)
    c.feed(hum, 1.0)
    assert c.sample().temperature_c == 22.5
