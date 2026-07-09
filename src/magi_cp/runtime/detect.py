"""Runtime detection for the gate dispatcher.

Design briefs: 2026-06-30-codex-runtime-adapter-design +
2026-07-06-magi-cp-hermes-runtime-adapter-design (private planning repo)
Section 3.4 / 3.5. Detection order (highest priority first):

  1. Explicit ``MAGI_CP_RUNTIME`` env var (managed configs set this so a
     sandbox never guesses wrong): ``codex`` / ``hermes`` / ``cc``.
  2. Codex-specific fields in the JSON envelope (``matcher_aliases`` +
     ``turn_id`` markers).
  3. Hermes-specific payload sniff: a snake_case ``hook_event_name``
     (Hermes ``VALID_HOOKS`` value, e.g. ``pre_tool_call``) carrying the
     Hermes-specific ``extra`` key. CC and Codex both use PascalCase
     ``hook_event_name`` values, so this sniff is collision-free.
  4. Presence of ``CLAUDE_CODE_SESSION_ID`` env var (CC sets this).
  5. Fallback: ``"cc"``.

GLOBAL KILL SWITCHES: ``MAGI_CP_CODEX_RUNTIME_ENABLED`` and
``MAGI_CP_HERMES_RUNTIME_ENABLED`` are both default-ON (no-default-OFF
policy), so unset flags leave those paths available. Each kill switch is
an explicit falsy token (``0`` / ``false`` / ``no`` / ``off`` / empty).
The Codex switch, when falsy, returns ``"cc"`` unconditionally BEFORE any
sniffing (the pre-adapter byte-identical CC path). The Hermes switch, when
falsy, disables ONLY the Hermes tiers (its env token + snake_case sniff)
and leaves CC/Codex routing untouched. Both are AVAILABILITY switches: a
tenant reaches a non-CC runtime only when its own routing
(``MAGI_CP_RUNTIME`` / payload sniff / ``tenants.runtime_id``) selects it.
"""
from __future__ import annotations

import json
import os
from typing import Mapping

from ..config import codex_runtime_enabled, hermes_runtime_enabled


# Env values that explicitly name the Codex runtime.
_CODEX_ENV_TOKENS = frozenset({"codex"})
# Env values that explicitly name the Hermes runtime.
_HERMES_ENV_TOKENS = frozenset({"hermes"})
# Env values that explicitly name the Claude Code runtime.
_CC_ENV_TOKENS = frozenset({"cc", "claude-code", "claude_code", "claudecode"})

# Hermes snake_case ``hook_event_name`` values (its ``VALID_HOOKS``, design
# Section 2.2 / 3.5). CC and Codex both send PascalCase event names, so a
# snake_case name here is a collision-free Hermes signal. Vendored copy;
# the P2 upstream-drift check keeps it in sync with Hermes ``VALID_HOOKS``.
_HERMES_EVENT_NAMES = frozenset({
    "pre_tool_call", "post_tool_call", "transform_terminal_output",
    "transform_tool_result", "transform_llm_output", "pre_llm_call",
    "post_llm_call", "pre_verify", "pre_api_request", "post_api_request",
    "api_request_error", "on_session_start", "on_session_end",
    "on_session_finalize", "on_session_reset", "subagent_start",
    "subagent_stop", "pre_gateway_dispatch", "pre_approval_request",
    "post_approval_response", "kanban_task_claimed",
    "kanban_task_completed", "kanban_task_blocked",
})


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


def _sniff_hermes_payload(raw_stdin: bytes) -> bool:
    """Return True when the decoded JSON envelope carries a
    Hermes-specific marker (design Section 3.5).

    Hermes sends a snake_case ``hook_event_name`` (one of its
    ``VALID_HOOKS`` values, ``pre_tool_call`` ...) AND a Hermes-specific
    ``extra`` key (``shell_hooks.py:527-543``). CC and Codex both use
    PascalCase event names and never send ``extra``, so requiring BOTH
    keeps this sniff disjoint from the CC / Codex tiers. A parse failure or
    a non-object payload is NOT a Hermes signal (fall through).
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
    event = payload.get("hook_event_name")
    return event in _HERMES_EVENT_NAMES and "extra" in payload


def detect_runtime(
    raw_stdin: bytes,
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve the active runtime id: ``"cc"`` / ``"codex"`` / ``"hermes"``.

    See the module docstring for the detection order + kill-switch
    contract. ``env`` defaults to ``os.environ``.
    """
    if env is None:
        env = os.environ

    # Global kill switch: Codex path is disabled → always CC.
    if not codex_runtime_enabled():
        return "cc"

    hermes_on = hermes_runtime_enabled()

    # 1. Explicit env override.
    explicit = (env.get("MAGI_CP_RUNTIME") or "").strip().lower()
    if explicit in _CODEX_ENV_TOKENS:
        return "codex"
    # Hermes env token only when its availability switch is on; otherwise
    # fall through so CC/Codex routing is untouched.
    if explicit in _HERMES_ENV_TOKENS and hermes_on:
        return "hermes"
    if explicit in _CC_ENV_TOKENS:
        return "cc"

    # 2. Codex payload sniff.
    if _sniff_codex_payload(raw_stdin):
        return "codex"

    # 3. Hermes payload sniff (snake_case event + extra); gated by the
    #    Hermes availability switch so a falsy flag disables ONLY this tier.
    if hermes_on and _sniff_hermes_payload(raw_stdin):
        return "hermes"

    # 4. CC session-id env marker.
    if env.get("CLAUDE_CODE_SESSION_ID"):
        return "cc"

    # 5. Fallback.
    return "cc"


__all__ = ["detect_runtime"]
