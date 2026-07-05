"""The reading wire schema, shared by every consumer.

All sensors emit the same envelope: an identity block plus a list of ``{metric, value,
unit}`` readings. That uniformity is what makes the building modular — a consumer parses
one shape and does not care which sensor produced it, so adding a tenth climate sensor or a
new metric never breaks a consumer.
"""

from __future__ import annotations

import json

READING_SCHEMA = "ce.sensor.reading/1"


def encode_reading(frame: dict) -> bytes:
    return json.dumps(frame, separators=(",", ":")).encode("utf-8")


def decode_reading(payload: bytes) -> dict:
    frame = json.loads(payload.decode("utf-8"))
    if frame.get("schema") != READING_SCHEMA:
        raise ValueError(f"unexpected schema: {frame.get('schema')!r}")
    return frame
