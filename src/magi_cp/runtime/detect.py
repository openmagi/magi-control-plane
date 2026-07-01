"""Runtime detection for the gate dispatcher.

Design brief: docs/plans/2026-06-30-codex-runtime-adapter-design.md
Section 3.4. Detection order (highest priority first):

  1. Explicit ``MAGI_CP_RUNTIME`` env var (managed configs set this so a
     sandbox never guesses wrong).
  2. Codex-specific fields in the JSON envelope (``matcher_aliases`` +
     Codex-shaped ``hook_event_name`` markers).
  3. Presence of ``CLAUDE_CODE_SESSION_ID`` env var (CC sets this).
  4. Fallback: ``"cc"``.

GLOBAL KILL SWITCH: ``MAGI_CP_CODEX_RUNTIME_ENABLED`` is default-ON
(2026-07-01 flip), so an unset flag leaves the Codex path available and
the tiers above run normally. The kill switch is now an explicit falsy
token (``0`` / ``false`` / ``no`` / ``off`` / empty): when set falsy this
returns ``"cc"`` unconditionally BEFORE any sniffing, so the Codex path
is dead code and the CC path is byte-identical to the pre-adapter gate.
Note this is only an AVAILABILITY switch: a tenant still reaches Codex
only when its own routing (``MAGI_CP_RUNTIME`` / payload sniff /
``tenants.runtime_id``) selects it; a bare CC payload with no Codex
markers still resolves to ``"cc"`` even with the flag on.
"""
from __future__ import annotations

import json
import os
from typing import Mapping

from ..config import codex_runtime_enabled


# Env values that explicitly name the Codex runtime.
_CODEX_ENV_TOKENS = frozenset({"codex"})
# Env values that explicitly name the Claude Code runtime.
_CC_ENV_TOKENS = frozenset({"cc", "claude-code", "claude_code", "claudecode"})


def _sniff_codex_payload(raw_stdin: bytes) -> bool:
    """Return True when the decoded JSON envelope carries a
    Codex-specific marker.

    Codex sends ``matcher_aliases`` (which CC never does) and a
    ``turn_id`` on turn-scoped events. Either marker on a well-formed
    JSON object is treated as a Codex signal. A parse failure or a
    non-object payload is NOT a Codex signal (fall through to the next
    detection tier).
    """
    text = raw_stdin.decode("utf-8", errors="replace").strip()
    if not text:
        return False
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    if "matcher_aliases" in payload:
        return True
    # ``turn_id`` is Codex-only among the two runtimes' shared events; CC
    # uses ``session_id`` alone on its turn-scoped hooks.
    if "turn_id" in payload:
        return True
    return False


def detect_runtime(
    raw_stdin: bytes,
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve the active runtime id: ``"cc"`` or ``"codex"``.

    See the module docstring for the detection order + kill-switch
    contract. ``env`` defaults to ``os.environ``.
    """
    if env is None:
        env = os.environ

    # Global kill switch: Codex path is disabled → always CC.
    if not codex_runtime_enabled():
        return "cc"

    # 1. Explicit env override.
    explicit = (env.get("MAGI_CP_RUNTIME") or "").strip().lower()
    if explicit in _CODEX_ENV_TOKENS:
        return "codex"
    if explicit in _CC_ENV_TOKENS:
        return "cc"

    # 2. Payload sniff.
    if _sniff_codex_payload(raw_stdin):
        return "codex"

    # 3. CC session-id env marker.
    if env.get("CLAUDE_CODE_SESSION_ID"):
        return "cc"

    # 4. Fallback.
    return "cc"


__all__ = ["detect_runtime"]
