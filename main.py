#!/usr/bin/env python3
"""ce-sensor-climate runtime — a pure, cap-gated producer of temperature/humidity.

Wires the hardware-agnostic :class:`ClimateService` to the local CE node via the shared
``ce`` client:
- an ANNOUNCE loop publishes "this sensor exists" on a well-known topic (discovery by
  name, never by address);
- a SERVE loop streams inbound control requests into ``service.handle`` (cap-gated
  read / subscribe / status);
- a PUSH loop feeds ``service.tick`` and sends each reading to cleared subscribers.

Config (all optional, env-driven — no flags, no addresses):
- ``CE_SENSOR_INSTANCE``  a name for this physical unit (e.g. ``climate-lobby``).
- ``CE_SENSOR_INTERVAL``  seconds between pushed readings (default 5).
- ``CE_SENSOR_AUTH``      ``capiam`` (default, real ce-iam verify) | ``allowlist`` | ``allow`` | ``deny``.
"""

from __future__ import annotations

import logging
import os
import threading

import ce

from capauth import authorizer_from_env
from climate.driver import MockDriver
from climate.service import (
    ANNOUNCE_TOPIC,
    CTL_TOPIC,
    DATA_TOPIC,
    SERVICE,
    ClimateService,
)

log = logging.getLogger("ce-sensor-climate")


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    client = ce.connect().wait_ready()
    node_id = client.node_id
    instance = os.environ.get("CE_SENSOR_INSTANCE", "climate")
    interval = float(os.environ.get("CE_SENSOR_INTERVAL", "5"))
    authorizer = authorizer_from_env()

    # Swap MockDriver -> I2cDriver(bus, address) to read a real SHT31/BME280. Nothing else changes.
    driver = MockDriver()
    service = ClimateService(driver, authorizer, node_id, instance, interval=interval)
    log.info("%s (%s) up on node %s; interval=%ss", SERVICE, instance, node_id[:16], interval)

    def announce_loop() -> None:
        while True:
            try:
                client.publish(ANNOUNCE_TOPIC, service.announce_payload())
            except ce.CeError as e:
                log.warning("announce failed: %s", e)
            _sleep(interval * 2)

    def push_loop() -> None:
        while True:
            for to, payload in service.tick():
                try:
                    client.send(to, DATA_TOPIC, payload)
                except ce.CeError as e:
                    log.warning("push to %s failed: %s", to[:12], e)
            _sleep(interval)

    threading.Thread(target=announce_loop, name="announce", daemon=True).start()
    threading.Thread(target=push_loop, name="push", daemon=True).start()

    # Blocks forever, serving cap-gated control requests. The supervisor restarts on exit.
    client.serve([CTL_TOPIC], service.handle)
    return 0


def _sleep(seconds: float) -> None:
    import time
    time.sleep(seconds)


if __name__ == "__main__":
    raise SystemExit(main())
