"""Unit tests for ClimateService: cap-gating, control ops, announce, and the push tick.

Pure: the service's handle/tick take a Message and return data, so tests need no live
node, no ce-iam, and no hardware — just a Message, a fake authorizer, and the mock driver.
"""

from __future__ import annotations

import json

from ce import Message

from capauth import AllowAll, DenyAll
from climate.driver import MockDriver
from climate.reading import decode_reading
from climate.service import ACTION_READ, ANNOUNCE_TOPIC, ClimateService

SENSOR_NODE = "aa" * 32
CONSUMER = "bb" * 32


def _clock():
    t = {"v": 1000.0}
    return t, (lambda: t["v"])


def _service(authorizer, now=None):
    return ClimateService(MockDriver(), authorizer, SENSOR_NODE, "test",
                          interval=5.0, lease=60.0, now=now or (lambda: 1000.0))


def _req(payload: bytes, token: int = 1) -> Message:
    return Message(sender=CONSUMER, topic="ce.sensor/climate/ctl", payload=payload,
                   reply_token=token)


def test_read_requires_capability():
    reply = _service(DenyAll()).handle(_req(b'{"op":"read"}'))
    assert b"unauthorized" in reply and ACTION_READ.encode() in reply


def test_read_returns_valid_reading_frame_when_cleared():
    frame = decode_reading(_service(AllowAll()).handle(_req(b'{"op":"read","cap":"x"}')))
    assert frame["sensor"] == "ce-sensor-climate"
    assert frame["node"] == SENSOR_NODE
    assert {r["metric"] for r in frame["readings"]} == {"temperature", "humidity"}


def test_subscribe_registers_and_tick_pushes_to_cleared_subscriber():
    svc = _service(AllowAll())
    svc.handle(_req(b'{"op":"subscribe","cap":"x"}'))
    sends = svc.tick()
    assert len(sends) == 1
    to, payload = sends[0]
    assert to == CONSUMER
    assert decode_reading(payload)["sensor"] == "ce-sensor-climate"


def test_subscription_lease_expires_and_stops_pushing():
    t, now = _clock()
    svc = _service(AllowAll(), now=now)
    svc.handle(_req(b'{"op":"subscribe","cap":"x"}'))
    assert len(svc.tick()) == 1
    t["v"] += 61.0
    assert svc.tick() == []
    assert svc.subscribers == {}


def test_unauthorized_subscribe_never_receives_pushes():
    svc = _service(DenyAll())
    svc.handle(_req(b'{"op":"subscribe"}'))
    assert svc.tick() == []


def test_status_reports_subscriber_count():
    svc = _service(AllowAll())
    svc.handle(_req(b'{"op":"subscribe","cap":"x"}'))
    status = json.loads(svc.handle(_req(b'{"op":"status","cap":"x"}')))
    assert status["subscribers"] == 1 and status["service"] == "ce-sensor-climate"


def test_unknown_op_and_bad_json():
    svc = _service(AllowAll())
    assert b"unknown op" in svc.handle(_req(b'{"op":"nope","cap":"x"}'))
    assert b"bad request" in svc.handle(_req(b"not json"))


def test_set_source_switches_driver_on_demand_via_api():
    calls = []

    def selector(mode):
        calls.append(mode)
        return MockDriver(base_temp_c=99.0)  # a distinguishable driver

    svc = ClimateService(MockDriver(), AllowAll(), SENSOR_NODE, "test",
                         selector=selector, source="auto", now=lambda: 1000.0)
    reply = json.loads(svc.handle(_req(b'{"op":"set_source","source":"mock","cap":"x"}')))
    assert reply["source"] == "mock" and calls == ["mock"]
    assert svc.source_mode == "mock"
    # the switched-in driver is now the one producing readings
    assert svc.reading_frame()["readings"][0]["value"] > 90.0


def test_set_source_rejects_bad_mode_and_reports_in_status():
    svc = ClimateService(MockDriver(), AllowAll(), SENSOR_NODE, "test",
                         selector=lambda m: MockDriver(), source="auto")
    assert b"one of auto|mock|real" in svc.handle(_req(b'{"op":"set_source","source":"x","cap":"x"}'))
    status = json.loads(svc.handle(_req(b'{"op":"status","cap":"x"}')))
    assert status["source"] == "auto" and status["driver"] == "MockDriver"


def test_set_source_unavailable_without_selector():
    # unit-test services (no selector) refuse the switch cleanly
    assert b"not available" in _service(AllowAll()).handle(
        _req(b'{"op":"set_source","source":"mock","cap":"x"}'))


def test_announce_payload_advertises_by_name_not_address():
    ann = json.loads(_service(AllowAll()).announce_payload())
    assert ann["service"] == "ce-sensor-climate"
    assert ann["node"] == SENSOR_NODE
    assert ann["ctl_topic"] == "ce.sensor/climate/ctl"
    assert ann["action"] == ACTION_READ
    assert "temperature" in ann["metrics"]
    # It is a discovery record — carries no IP address / port.
    assert "ip" not in ann and "addr" not in ann
    assert ANNOUNCE_TOPIC == "ce.sensor/announce"
