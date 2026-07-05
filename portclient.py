"""Claim a board port from the local ce-arduino coordinator (vendored helper).

ce-arduino runs on the same board node as the module ceapps, so a claim is a self-request to
`ce.arduino/ctl` on the local node — no cross-node discovery. Returns the assigned port + exact
wiring, or None if the coordinator is not up (the app then runs port-less until it can claim).
"""

from __future__ import annotations

import json
import time
from typing import Optional

import ce

CTL_TOPIC = "ce.arduino/ctl"


def claim_port(client, module: str, instance: str, ctl_topic: str, *, board: str = "8port",
               cap: str = "", retries: int = 8, delay: float = 0.5) -> Optional[dict]:
    body = json.dumps({"op": "claim", "module": module, "instance": instance,
                       "node": client.node_id, "ctl_topic": ctl_topic, "cap": cap}).encode("utf-8")
    for _ in range(retries):
        try:
            reply = client.request(client.node_id, CTL_TOPIC, body, timeout_ms=3000)
            result = json.loads(reply)
            if result.get("ok"):
                return result
        except (ce.CeError, ValueError):
            pass
        time.sleep(delay)
    return None
