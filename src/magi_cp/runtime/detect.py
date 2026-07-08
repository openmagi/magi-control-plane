"""Runtime detection for the gate dispatcher.

Design briefs: 2026-06-30-codex-runtime-adapter-design +
2026-07-06-magi-cp-hermes-runtime-adapter-design +
2026-07-08-magi-cp-gajae-code-runtime-adapter-design (private planning repo).

Detection order (highest priority first):

  1. Explicit ``MAGI_CP_RUNTIME`` env var (managed configs set this so a
     sandbox never guesses wrong): ``gjc`` / ``codex`` / ``hermes`` / ``cc``.
  2. Payload sniff, each gated by its runtime's availability switch:
       a. gjc: a well-formed JSON object carrying the ``gjc_event`` key
          (disjoint from Codex's ``matcher_aliases`` / ``turn_id``).
       b. Codex: ``matcher_aliases`` or ``turn_id`` markers.
       c. Hermes: a snake_case ``hook_event_name`` (a ``VALID_HOOKS``
          value, e.g. ``pre_tool_call``) AND the Hermes-specific ``extra``
          key. CC and Codex both use PascalCase names and never send
          ``extra``, so this sniff is collision-free.
  3. Presence of ``CLAUDE_CODE_SESSION_ID`` env var (CC sets this).
  4. Fallback: ``"cc"``.

GLOBAL KILL SWITCHES — one per non-CC runtime, all default-ON
(no-default-OFF policy), each an explicit falsy token (``0`` / ``false`` /
``no`` / ``off`` / empty):

  ``MAGI_CP_CODEX_RUNTIME_ENABLED``   (2026-07-01 flip)
  ``MAGI_CP_HERMES_RUNTIME_ENABLED``  (2026-07-06 flip)
  ``MAGI_CP_GJC_RUNTIME_ENABLED``     (2026-07-08 flip, D5)

The three switches are INDEPENDENT: a falsy flag degrades ONLY its own
runtime's tiers to ``"cc"`` and never affects the other runtimes'
routing. (This supersedes the earlier single-early-return Codex kill
switch, which coupled all non-CC routing to the Codex flag; the
per-runtime structure is required so gjc/Hermes/Codex kill switches do
not clobber one another in the four-runtime dispatcher.) All three are
AVAILABILITY switches: a tenant reaches a non-CC runtime only when its
own routing (``MAGI_CP_RUNTIME`` / payload sniff / ``tenants.runtime_id``)
selects it, and the CC path stays byte-identical when every non-CC
runtime is either unselected or disabled.
"""
from __future__ import annotations

import json
import os
from typing import Mapping

from ..config import (
    codex_runtime_enabled,
    gjc_runtime_enabled,
    hermes_runtime_enabled,
)


# Env values that explicitly name the gjc runtime.
_GJC_ENV_TOKENS = frozenset({"gjc", "gajae", "gajae-code", "gajae_code"})
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


def _sniff_gjc_payload(raw_stdin: bytes) -> bool:
    """Return True when the decoded JSON envelope carries a gjc-specific marker.

    gjc sends a ``gjc_event`` key (§4.3 wire, §4.6 detection tier 3a), which
    CC and Codex never send.  A parse failure or a non-object payload is NOT
    a gjc signal (fall through).
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
    return "gjc_event" in payload


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
    """Resolve the active runtime id: ``"cc"`` / ``"codex"`` / ``"hermes"``
    / ``"gjc"``.

    See the module docstring for the detection order + kill-switch
    contract. ``env`` defaults to ``os.environ``.

    Kill switches are per-runtime and INDEPENDENT: a falsy
    ``MAGI_CP_GJC_RUNTIME_ENABLED`` / ``MAGI_CP_CODEX_RUNTIME_ENABLED`` /
    ``MAGI_CP_HERMES_RUNTIME_ENABLED`` degrades only its own runtime's
    signals to ``"cc"`` and leaves the other runtimes' routing untouched.

    The gjc sniff fires BEFORE the Codex sniff (tier 2a before 2b) because
    the two are disjoint by construction (§4.6): a gjc payload carries
    ``gjc_event``, which Codex never sends; a Codex payload carries
    ``matcher_aliases`` / ``turn_id``, which the gjc wire never sends. The
    Hermes sniff (2c) requires a snake_case event + ``extra``, disjoint
    from both. A payload matching more than one is pathological; the
    earlier tier wins, and the explicit ``MAGI_CP_RUNTIME`` env flag
    (tier 1) is always the authoritative override.
    """
    if env is None:
        env = os.environ

    gjc_on = gjc_runtime_enabled()
    codex_on = codex_runtime_enabled()
    hermes_on = hermes_runtime_enabled()

    # 1. Explicit env override. Each token is gated by its own availability
    #    switch so a disabled runtime degrades to "cc" rather than routing.
    explicit = (env.get("MAGI_CP_RUNTIME") or "").strip().lower()
    if explicit in _GJC_ENV_TOKENS:
        return "gjc" if gjc_on else "cc"
    if explicit in _CODEX_ENV_TOKENS:
        return "codex" if codex_on else "cc"
    if explicit in _HERMES_ENV_TOKENS:
        return "hermes" if hermes_on else "cc"
    if explicit in _CC_ENV_TOKENS:
        return "cc"

    # 2. Payload sniff — gjc first (disjoint markers; §4.6 tier 2a before
    #    2b), then Codex, then Hermes. Each gated by its own switch.
    if gjc_on and _sniff_gjc_payload(raw_stdin):
        return "gjc"
    if codex_on and _sniff_codex_payload(raw_stdin):
        return "codex"
    if hermes_on and _sniff_hermes_payload(raw_stdin):
        return "hermes"

    # 3. CC session-id env marker.
    if env.get("CLAUDE_CODE_SESSION_ID"):
        return "cc"

    # 4. Fallback.
    return "cc"


__all__ = ["detect_runtime"]
