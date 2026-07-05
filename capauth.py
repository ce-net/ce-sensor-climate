"""Capability authorization for cap-gated ceapps (drop-in, stdlib only).

CE's only trust primitive is a signed, attenuating capability chain verified against a
node's accepted roots (the ``ce-cap`` model). A sensor is cap-gated: a consumer presents a
capability and, if it grants the required action rooted at the building-org root, it has
clearance. No address list, no central ACL — "provide a capability, and whoever has
clearance can use it" (Leif, 2026-07-05).

Python has no in-process ``ce-cap`` verifier yet, so :class:`CeIamAuthorizer` shells out to
the real ``ce-iam verify`` CLI (offline chain check + on-chain revocation, fail-closed).
This module is vendored alongside ``ce.py`` in each sensor app; the intent is to fold
``authorize(...)`` into the shared ``ce`` client so every ceapp gets it for free (see the
note to Cobalt in AGENTS.md). Apps depend on the interface, so that move is transparent.

Test/dev implementations avoid any external process. Everything is fail-closed on error.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class Authorizer(Protocol):
    """Decides whether a presented capability grants ``action`` on ``on_node``.

    ``cap`` is the token the requester presented (hex chain, or "" if none). ``requester``
    is the authenticated NodeId of the caller. ``on_node`` is the resource node the action
    targets (the sensor's own NodeId). Returns True only if clearance is proven; any error
    or ambiguity returns False (fail-closed).
    """

    def authorize(self, cap: str, action: str, requester: str, on_node: str) -> bool: ...


class CeIamAuthorizer:
    """Verify capabilities with the real ``ce-iam verify`` CLI (fail-closed).

    Runs ``ce-iam verify --token <cap> --requester <requester> --action <action>
    --on-node <on_node> --use-roots --fail-closed --json`` and grants only when the CLI
    reports the chain authorizes the action. The building-org root must be an accepted root
    on this node for chains rooted there to verify.
    """

    def __init__(self, iam_bin: str = "ce-iam", extra_args: Optional[list] = None) -> None:
        self.iam_bin = iam_bin
        self.extra_args = extra_args or []

    def authorize(self, cap: str, action: str, requester: str, on_node: str) -> bool:
        if not cap or not requester:
            return False
        exe = shutil.which(self.iam_bin) or self.iam_bin
        cmd = [exe, "verify", "--token", cap, "--requester", requester,
               "--action", action, "--use-roots", "--fail-closed", "--json"]
        if on_node:
            cmd += ["--on-node", on_node]
        cmd += self.extra_args
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return False
        if proc.returncode != 0:
            return False
        out = proc.stdout.strip()
        if out:
            try:
                verdict = json.loads(out)
            except json.JSONDecodeError:
                return proc.returncode == 0
            if isinstance(verdict, dict):
                for key in ("authorized", "allowed", "ok", "granted"):
                    if key in verdict:
                        return bool(verdict[key])
        return proc.returncode == 0


class AllowlistAuthorizer:
    """Grant clearance to an explicit set of NodeIds (org membership by id).

    Dependency-free bring-up gate: the capability is ignored and clearance is whether the
    authenticated requester is a known member. Not attenuable — prefer
    :class:`CeIamAuthorizer` in production; useful before a full cap chain is provisioned.
    """

    def __init__(self, allowed: Optional[set] = None) -> None:
        self.allowed = {a.lower() for a in (allowed or set())}

    def authorize(self, cap: str, action: str, requester: str, on_node: str) -> bool:
        return bool(requester) and requester.lower() in self.allowed


class AllowAll:
    """Grant everything. DEVELOPMENT ONLY — never deploy with this."""

    def authorize(self, cap: str, action: str, requester: str, on_node: str) -> bool:
        return True


class DenyAll:
    """Deny everything (the safe default when no authorizer is configured)."""

    def authorize(self, cap: str, action: str, requester: str, on_node: str) -> bool:
        return False


def authorizer_from_env(env: Optional[dict] = None) -> Authorizer:
    """Build an :class:`Authorizer` from ``$CE_SENSOR_AUTH``.

    - ``capiam`` (default): real ``ce-iam verify`` chain check, fail-closed.
    - ``allowlist``: org membership from ``$CE_SENSOR_ALLOW`` (comma-separated NodeIds).
    - ``allow``: grant everything (development only).
    - ``deny``: deny everything.
    """
    e = env if env is not None else os.environ
    mode = (e.get("CE_SENSOR_AUTH") or "capiam").strip().lower()
    if mode == "allow":
        return AllowAll()
    if mode == "deny":
        return DenyAll()
    if mode == "allowlist":
        raw = e.get("CE_SENSOR_ALLOW", "")
        return AllowlistAuthorizer({p.strip() for p in raw.split(",") if p.strip()})
    return CeIamAuthorizer()
