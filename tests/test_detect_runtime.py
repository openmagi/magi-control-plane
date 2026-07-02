"""P1 Codex adapter: runtime detection order + kill switch.

Design brief: docs/plans/2026-06-30-codex-runtime-adapter-design.md
Section 3.4. Detection order (highest first): explicit ``MAGI_CP_RUNTIME``
env → Codex payload sniff → ``CLAUDE_CODE_SESSION_ID`` env → CC fallback.
The global kill switch ``MAGI_CP_CODEX_RUNTIME_ENABLED`` (default OFF)
short-circuits everything to ``"cc"`` so the Codex path is dead code by
default.
"""
from __future__ import annotations

import pytest

from magi_cp.runtime.detect import detect_runtime


# A Codex-shaped envelope carries the ``matcher_aliases`` marker CC never
# sends; a CC-shaped one does not.
_CODEX_PAYLOAD = b'{"hook_event_name":"PreToolUse","matcher_aliases":["Bash"]}'
_CC_PAYLOAD = b'{"hook_event_name":"PreToolUse","tool_name":"Bash"}'


@pytest.fixture
def codex_on(monkeypatch):
    """Flip the global kill switch ON so the detection tiers are live."""
    monkeypatch.setenv("MAGI_CP_CODEX_RUNTIME_ENABLED", "1")


# ── kill switch ──────────────────────────────────────────────────────
def test_flag_default_unset_is_on(monkeypatch):
    """Default-ON flip (2026-07-01): with the flag UNSET the adapter is
    globally available, so an explicit ``MAGI_CP_RUNTIME=codex`` resolves
    to Codex. The disabled path now requires an explicit falsy token
    (see ``test_flag_explicit_falsy_returns_cc``)."""
    monkeypatch.delenv("MAGI_CP_CODEX_RUNTIME_ENABLED", raising=False)
    assert detect_runtime(_CODEX_PAYLOAD, env={"MAGI_CP_RUNTIME": "codex"}) == "codex"


def test_flag_explicit_falsy_returns_cc(monkeypatch):
    """Kill switch: an explicit falsy token forces the dispatcher to
    "CC only", even with a Codex payload + explicit env."""
    monkeypatch.setenv("MAGI_CP_CODEX_RUNTIME_ENABLED", "0")
    assert detect_runtime(_CODEX_PAYLOAD, env={"MAGI_CP_RUNTIME": "codex"}) == "cc"


# ── tier 1: explicit env override ────────────────────────────────────
def test_env_var_overrides_codex_payload_sniff(codex_on):
    # Payload is Codex-shaped, but the env explicitly names CC → CC wins.
    assert detect_runtime(_CODEX_PAYLOAD, env={"MAGI_CP_RUNTIME": "cc"}) == "cc"


def test_env_var_selects_codex_on_cc_shaped_payload(codex_on):
    # Env explicitly names Codex even though the payload looks like CC.
    assert detect_runtime(_CC_PAYLOAD, env={"MAGI_CP_RUNTIME": "codex"}) == "codex"


# ── tier 2: payload sniff beats session-id env ───────────────────────
def test_payload_sniff_overrides_session_id_env(codex_on):
    env = {"CLAUDE_CODE_SESSION_ID": "cc-session-xyz"}
    assert detect_runtime(_CODEX_PAYLOAD, env=env) == "codex"


def test_turn_id_marker_also_sniffs_codex(codex_on):
    payload = b'{"hook_event_name":"PreToolUse","turn_id":"t-1"}'
    assert detect_runtime(payload, env={}) == "codex"


# ── tier 3 + 4: session-id env, then CC fallback ─────────────────────
def test_session_id_env_yields_cc(codex_on):
    assert detect_runtime(_CC_PAYLOAD, env={"CLAUDE_CODE_SESSION_ID": "x"}) == "cc"


def test_no_signals_falls_back_to_cc(codex_on):
    assert detect_runtime(_CC_PAYLOAD, env={}) == "cc"


def test_blank_stdin_falls_back_to_cc(codex_on):
    assert detect_runtime(b"", env={}) == "cc"


def test_malformed_json_is_not_a_codex_signal(codex_on):
    assert detect_runtime(b"not json at all", env={}) == "cc"
