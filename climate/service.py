"""ClimateService — cap-gated temperature/humidity producer over the CE mesh.

The service exposes a small control protocol on a request/reply topic and pushes readings
to cleared subscribers. There is no address list and no HTTP: a consumer discovers the
sensor by service name, presents a capability, and — if it grants ``building:climate:read``
rooted at the building-org root — receives readings. Adding a consumer is "hold a cap and
subscribe"; adding another climate sensor is "install this app again" — consumers never
change.

The logic is split into two pure methods so it is trivially testable without threads or a
live node:
- :meth:`handle` — dispatch one inbound control message, return the reply bytes.
- :meth:`tick`   — produce the directed sends (reading pushes) due this interval.

The runtime (``main.py``) wires these to a real :class:`ce.Node`: ``serve`` feeds
``handle``, and a timer loop feeds ``tick`` and performs the sends.
"""

from __future__ import annotations

import json
import time
from typing import Callable, Optional

from ce import Message

from capauth import Authorizer
from .driver import Driver
from .reading import READING_SCHEMA, encode_reading

SERVICE = "ce-sensor-climate"
CTL_TOPIC = "ce.sensor/climate/ctl"
DATA_TOPIC = "ce.sensor/climate/data"
ANNOUNCE_TOPIC = "ce.sensor/announce"
ACTION_READ = "building:climate:read"

DEFAULT_LEASE_SECONDS = 60.0
DEFAULT_INTERVAL_SECONDS = 5.0


def _err(message: str) -> bytes:
    return json.dumps({"error": message}).encode("utf-8")


def _ok(**fields: object) -> bytes:
    body = {"ok": True}
    body.update(fields)
    return json.dumps(body).encode("utf-8")


class ClimateService:
    def __init__(self, driver: Driver, authorizer: Authorizer, node_id: str,
                 instance: str = "climate", *, interval: float = DEFAULT_INTERVAL_SECONDS,
                 lease: float = DEFAULT_LEASE_SECONDS,
                 now: Callable[[], float] = time.time) -> None:
        self.driver = driver
        self.authorizer = authorizer
        self.node_id = node_id
        self.instance = instance
        self.interval = interval
        self.lease = lease
        self._now = now
        self.subscribers: dict[str, float] = {}  # subscriber NodeId -> lease expiry (ts)

    # ----- reading production -----

    def reading_frame(self) -> dict:
        s = self.driver.read()
        return {
            "schema": READING_SCHEMA,
            "sensor": SERVICE,
            "node": self.node_id,
            "instance": self.instance,
            "ts": round(self._now(), 3),
            "readings": [
                {"metric": "temperature", "value": s.temperature_c, "unit": "C"},
                {"metric": "humidity", "value": s.humidity_pct, "unit": "%RH"},
            ],
        }

    def announce_payload(self) -> bytes:
        """A discovery announce a consumer can read to learn this sensor exists — by name,
        never by address. The consumer then presents a capability to :meth:`handle`."""
        return json.dumps({
            "schema": "ce.sensor.announce/1",
            "service": SERVICE,
            "kind": "climate",
            "node": self.node_id,
            "instance": self.instance,
            "ctl_topic": CTL_TOPIC,
            "data_topic": DATA_TOPIC,
            "action": ACTION_READ,
            "metrics": ["temperature", "humidity"],
        }, separators=(",", ":")).encode("utf-8")

    # ----- control plane (cap-gated) -----

    def handle(self, msg: Message) -> Optional[bytes]:
        """Dispatch one control request. Returns reply bytes (JSON)."""
        try:
            req = json.loads(msg.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return _err("bad request: expected JSON")
        if not isinstance(req, dict):
            return _err("bad request: expected object")
        op = req.get("op")
        cap = req.get("cap", "")

        # Every op is at least read-level; gate before doing anything.
        if not self.authorizer.authorize(cap, ACTION_READ, msg.sender, self.node_id):
            return _err("unauthorized: present a capability granting " + ACTION_READ)

        if op == "read":
            return encode_reading(self.reading_frame())
        if op == "status":
            return _ok(service=SERVICE, instance=self.instance,
                       interval=self.interval, subscribers=len(self._live()))
        if op == "subscribe":
            self.subscribers[msg.sender] = self._now() + self.lease
            return _ok(subscribed=True, interval=self.interval, lease=self.lease,
                       data_topic=DATA_TOPIC)
        if op == "unsubscribe":
            self.subscribers.pop(msg.sender, None)
            return _ok(subscribed=False)
        return _err(f"unknown op: {op!r}")

    # ----- data plane (push to cleared subscribers) -----

    def tick(self) -> list[tuple[str, bytes]]:
        """Return the directed (subscriber_node_id, reading_bytes) sends due now.

        Expired leases are pruned. One reading frame is produced per tick and pushed to
        every live subscriber, so all consumers see a consistent value.
        """
        live = self._live()
        if not live:
            return []
        payload = encode_reading(self.reading_frame())
        return [(node_id, payload) for node_id in live]

    def _live(self) -> list[str]:
        now = self._now()
        expired = [n for n, exp in self.subscribers.items() if exp <= now]
        for n in expired:
            del self.subscribers[n]
        return list(self.subscribers.keys())
