"""ce — the CE mesh client for Python ceapps.

A single dependency-free module (Python 3.8+, stdlib only) that lets a Python `script`-tier
ceapp talk to its local CE node exactly like the Rust (`ce-rs`) and TypeScript (`@ce-net/sdk`)
clients do: publish/subscribe telemetry, directed request/reply, and a `serve` loop — all over
the node's local HTTP API. No pip install, no build: drop this file next to your `main.py`, `import
ce`, and you are on the mesh.

    import ce
    c = ce.connect()
    c.publish("building/temp", b"21.5")          # fan out to every subscriber
    for m in c.subscribe("building/temp"):        # stream readings from the mesh
        print(m.sender, m.text)

Auth + endpoint mirror the other SDKs: `Authorization: Bearer <api-token>`; the token is read from
`$CE_API_TOKEN`, else the node's `api.token` in the CE data dir. The node URL is `$CE_NODE_URL`
(default `http://127.0.0.1:8844`). Payloads are `bytes` on the wire (hex-encoded in the JSON body,
handled here). `str` payloads are UTF-8 encoded for convenience.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional, Sequence, Union

__all__ = ["Client", "Message", "connect", "CeError"]

Payload = Union[bytes, bytearray, str]


class CeError(Exception):
    """A CE node API error (non-2xx response, or the node is unreachable)."""


def _as_bytes(p: Payload) -> bytes:
    if isinstance(p, str):
        return p.encode("utf-8")
    return bytes(p)


def _data_dir() -> Path:
    """The CE data dir, resolved the same way the `ce` binary does per-OS."""
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "ce"
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        return Path(base) / "ce" if base else home / ".ce"
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else home / ".local" / "share") / "ce"


def _discover_token() -> Optional[str]:
    tok = os.environ.get("CE_API_TOKEN")
    if tok:
        return tok.strip()
    f = _data_dir() / "api.token"
    try:
        return f.read_text().strip()
    except OSError:
        return None


@dataclass
class Message:
    """One inbound mesh message (a published telemetry item or a directed request)."""

    sender: str
    topic: str
    payload: bytes
    reply_token: Optional[int] = None

    @property
    def text(self) -> str:
        """The payload decoded as UTF-8 (lossy), for text telemetry."""
        return self.payload.decode("utf-8", "replace")

    def json(self):
        """The payload parsed as JSON."""
        return json.loads(self.payload)

    @property
    def wants_reply(self) -> bool:
        return self.reply_token is not None


class Client:
    """A connection to the local CE node. Cheap to construct; holds no socket until used."""

    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None):
        self.base = (base_url or os.environ.get("CE_NODE_URL") or "http://127.0.0.1:8844").rstrip("/")
        self.token = token if token is not None else _discover_token()

    # ---- low-level HTTP ----

    def _req(self, method: str, path: str, body: Optional[dict] = None, timeout: float = 35.0):
        url = self.base + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        # The node gates non-GET calls on Bearer auth; sending it on GETs too is harmless.
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise CeError(f"{method} {path} -> {e.code} {e.reason}: {detail}") from None
        except urllib.error.URLError as e:
            raise CeError(f"{method} {path} -> node unreachable at {self.base}: {e.reason}") from None
        raw = resp.read()
        if not raw:
            return None
        ctype = resp.headers.get("Content-Type", "")
        return json.loads(raw) if "json" in ctype else raw

    # ---- node ----

    def status(self) -> dict:
        """`GET /status` — node id, height, balance. Also the liveness check."""
        return self._req("GET", "/status")

    @property
    def node_id(self) -> str:
        return self.status().get("node_id", "")

    def wait_ready(self, timeout: float = 30.0) -> "Client":
        """Block until the node answers `/status`, so a daemon started at boot doesn't race it."""
        deadline = time.time() + timeout
        while True:
            try:
                self.status()
                return self
            except CeError:
                if time.time() >= deadline:
                    raise
                time.sleep(0.5)

    # ---- pub/sub (fan-out telemetry) ----

    def publish(self, topic: str, payload: Payload) -> None:
        """`POST /mesh/publish` — sign + fan this out to every subscriber of `topic`."""
        self._req("POST", "/mesh/publish", {"topic": topic, "payload_hex": _as_bytes(payload).hex()})

    def subscribe(self, *topics: str) -> Iterator[Message]:
        """Subscribe to one or more topics and yield inbound `Message`s forever (a generator).

        Best-effort fan-out: for latest-value telemetry a dropped item is superseded by the next.
        Use `serve` for request/reply. Reconnects the stream automatically on transient drops.
        """
        want = set(topics)
        for m in self.messages():
            if not want or m.topic in want:
                yield m

    # ---- directed request/reply (reliable) ----

    def send(self, to: str, topic: str, payload: Payload) -> None:
        """`POST /mesh/send` — a one-way directed message to node `to`."""
        self._req("POST", "/mesh/send", {"to": to, "topic": topic, "payload_hex": _as_bytes(payload).hex()})

    def request(self, to: str, topic: str, payload: Payload = b"", timeout_ms: int = 30000) -> bytes:
        """`POST /mesh/request` — reliable request to node `to`; returns the reply payload."""
        r = self._req(
            "POST",
            "/mesh/request",
            {"to": to, "topic": topic, "payload_hex": _as_bytes(payload).hex(), "timeout_ms": timeout_ms},
            timeout=timeout_ms / 1000.0 + 5.0,
        )
        return bytes.fromhex(r.get("payload_hex", "")) if isinstance(r, dict) else b""

    def reply(self, token: int, payload: Payload) -> None:
        """`POST /mesh/reply` — answer an inbound request by its `reply_token`."""
        self._req("POST", "/mesh/reply", {"token": token, "payload_hex": _as_bytes(payload).hex()})

    # ---- serve loop (be a mesh responder) ----

    def serve(self, topics: Sequence[str], handler: Callable[[Message], Optional[Payload]]) -> None:
        """Subscribe to `topics` and answer every inbound request with `handler(msg)`.

        `handler` returns the reply payload (`bytes`/`str`) for a request, or `None` to not reply
        (e.g. for a fire-and-forget publish on the same topic). Runs until the process is stopped.
        """
        subs = set(topics)
        for m in self.messages(subscribe=topics):
            if subs and m.topic not in subs:
                continue
            try:
                out = handler(m)
            except Exception as e:  # a handler bug must not kill the responder
                sys.stderr.write(f"ce.serve handler error on {m.topic}: {e}\n")
                continue
            if out is not None and m.reply_token is not None:
                self.reply(m.reply_token, out)

    # ---- inbound SSE stream ----

    def messages(self, subscribe: Optional[Sequence[str]] = None) -> Iterator[Message]:
        """Yield every inbound mesh message from `GET /mesh/messages/stream` (SSE), reconnecting
        with backoff on drops. Optionally `subscribe` to topics first so they start arriving."""
        for t in subscribe or ():
            self.subscribe_topic(t)
        backoff = 0.5
        while True:
            try:
                yield from self._stream_once()
                backoff = 0.5
            except CeError as e:
                sys.stderr.write(f"ce stream reconnecting: {e}\n")
                time.sleep(backoff)
                backoff = min(backoff * 2, 10.0)

    def subscribe_topic(self, topic: str) -> None:
        """`POST /mesh/subscribe` — idempotent; lasts the node's lifetime."""
        self._req("POST", "/mesh/subscribe", {"topic": topic})

    def _stream_once(self) -> Iterator[Message]:
        req = urllib.request.Request(self.base + "/mesh/messages/stream", method="GET")
        req.add_header("Accept", "text/event-stream")
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        try:
            resp = urllib.request.urlopen(req, timeout=None)
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            raise CeError(f"open stream: {e}") from None
        data_lines = []
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\n").rstrip("\r")
            if line == "":  # event boundary
                if data_lines:
                    payload = "\n".join(data_lines)
                    data_lines = []
                    msg = _parse_message(payload)
                    if msg is not None:
                        yield msg
                continue
            if line.startswith(":"):  # SSE comment / keepalive
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip(" "))


def _parse_message(data: str) -> Optional[Message]:
    try:
        r = json.loads(data)
    except json.JSONDecodeError:
        return None
    return Message(
        sender=r.get("from", ""),
        topic=r.get("topic", ""),
        payload=bytes.fromhex(r.get("payload_hex", "") or ""),
        reply_token=r.get("reply_token"),
    )


def connect(base_url: Optional[str] = None, token: Optional[str] = None) -> Client:
    """Open a client to the local CE node. The one call a ceapp starts with."""
    return Client(base_url, token)
