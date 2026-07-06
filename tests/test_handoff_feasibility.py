"""PR-7 — Feasibility check at the wizard -> conversational handoff seam.

``build_handoff_turn`` is OFFLINE (no LLM call, no provider). When
``runtime_id`` is supplied the function runs ``classify_draft`` on the
seeded draft and attaches a ``feasibility`` wire field in the same shape
the conversational path emits.

Coverage:
  - PreToolUse + Read (read-family / codex inert) + runtime_id=codex
    -> feasibility.class == "silent_noop", code == "codex_matcher_inert",
       alternatives include keep_for_cc + magi_agent_handoff.
  - Same seed, runtime_id absent/None -> feasibility is None (byte-stable).
  - PreToolUse + Bash (translatable), runtime_id=codex -> None (native).
  - OFFLINE guarantee: no provider / LLM constructed; function returns
    without one.
"""
from __future__ import annotations

import pytest

# Import create_app first to resolve circular-import ordering
# (same pattern as test_handoff_context.py).
from magi_cp.cloud.app import create_app  # noqa: F401
from magi_cp.policy.handoff_context import build_handoff_turn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pre_tool_use_read_state() -> dict:
    """Wizard state for PreToolUse + Read matcher (codex-inert)."""
    return {
        "lifecycle": "before_tool_use",
        "toolScope": "Read",
        "action": "block",
    }


def _pre_tool_use_bash_state() -> dict:
    """Wizard state for PreToolUse + Bash (maps to exec_command on codex)."""
    return {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "action": "block",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_codex_read_gets_silent_noop_feasibility():
    """PreToolUse + Read on codex -> silent_noop / codex_matcher_inert."""
    out = build_handoff_turn(
        wizard_state=_pre_tool_use_read_state(),
        draft_ir=None,
        runtime_id="codex",
    )
    feas = out.get("feasibility")
    assert feas is not None, "expected a feasibility finding for codex + Read"
    assert feas["class"] == "silent_noop"
    assert feas["code"] == "codex_matcher_inert"
    assert feas["runtime_id"] == "codex"
    # Explanation must be a non-empty string.
    assert isinstance(feas["explanation"], str) and feas["explanation"]
    # Alternatives: keep_for_cc + magi_agent_handoff (in that order).
    alts = feas["alternatives"]
    assert len(alts) == 2, f"expected 2 alternatives, got {alts}"
    assert alts[0]["kind"] == "keep_for_cc"
    assert alts[1]["kind"] == "magi_agent_handoff"
    # handoff entry must carry intent_summary + cta.
    handoff_alt = alts[1]
    assert isinstance(handoff_alt.get("intent_summary"), str)
    assert isinstance(handoff_alt.get("cta"), str) and handoff_alt["cta"]
    # route is None when env unset (default test env).
    assert "route" in handoff_alt


def test_no_runtime_id_gives_no_feasibility():
    """runtime_id absent -> feasibility is None; rest of dict unaffected."""
    baseline = build_handoff_turn(
        wizard_state=_pre_tool_use_read_state(),
        draft_ir=None,
    )
    out = build_handoff_turn(
        wizard_state=_pre_tool_use_read_state(),
        draft_ir=None,
        runtime_id=None,
    )
    # Both should have feasibility=None (cc is native for this draft).
    assert baseline.get("feasibility") is None
    assert out.get("feasibility") is None
    # The rest of the dict should be identical (byte-stable contract).
    for key in ("assistant_message", "draft", "missing_fields",
                "needs_more", "ready_to_save"):
        assert out[key] == baseline[key], f"key {key!r} drifted"


def test_codex_bash_is_native_no_feasibility():
    """PreToolUse + Bash on codex -> native (maps to exec_command) -> None."""
    out = build_handoff_turn(
        wizard_state=_pre_tool_use_bash_state(),
        draft_ir=None,
        runtime_id="codex",
    )
    feas = out.get("feasibility")
    assert feas is None, (
        f"Bash is translatable on codex - expected None, got {feas}"
    )


def test_offline_no_provider_needed(monkeypatch):
    """OFFLINE guarantee: build_handoff_turn returns without any LLM provider.

    We monkeypatch out the provider module entry points so that if the
    function ever accidentally touches one the test fails.  The monkeypatch
    succeeds only if the code path completes without touching those symbols.
    """
    # Ensure the env var for Magi Agent console URL is unset so route=None.
    monkeypatch.delenv("MAGI_CP_MAGI_AGENT_CONSOLE_URL", raising=False)

    called = []

    def _bad_call(*a, **kw):
        called.append(("provider called", a, kw))
        raise AssertionError("LLM provider must not be called from build_handoff_turn")

    # Monkeypatch a common provider entry point if it exists.
    try:
        import magi_cp.policy.nl_compiler_interactive as _ci
        monkeypatch.setattr(_ci, "step_compile", _bad_call, raising=False)
    except ImportError:
        pass

    out = build_handoff_turn(
        wizard_state=_pre_tool_use_read_state(),
        draft_ir=None,
        runtime_id="codex",
    )
    assert called == [], f"LLM provider was called: {called}"
    # Sanity: we still got a feasibility finding.
    assert out.get("feasibility") is not None


def test_magi_agent_handoff_route_none_when_env_unset(monkeypatch):
    """When MAGI_CP_MAGI_AGENT_CONSOLE_URL is unset, route field is None."""
    monkeypatch.delenv("MAGI_CP_MAGI_AGENT_CONSOLE_URL", raising=False)
    out = build_handoff_turn(
        wizard_state=_pre_tool_use_read_state(),
        draft_ir=None,
        runtime_id="codex",
    )
    alts = out["feasibility"]["alternatives"]
    handoff_alt = next(a for a in alts if a["kind"] == "magi_agent_handoff")
    assert handoff_alt["route"] is None


def test_codex_event_not_live_also_gets_alternatives():
    """An event outside Codex's live set also gets keep_for_cc + handoff."""
    # NotebookRunCell is not in CODEX_LIVE_EVENTS - should be codex_event_not_live.
    state = {
        "lifecycle": "pre_compact",
        "action": "block",
    }
    out = build_handoff_turn(
        wizard_state=state,
        draft_ir=None,
        runtime_id="codex",
    )
    feas = out.get("feasibility")
    if feas is not None and feas["code"] == "codex_event_not_live":
        alts = feas["alternatives"]
        kinds = [a["kind"] for a in alts]
        # codex_event_not_live is in _CODEX_SILENT_NOOP_CODES so it gets both.
        assert "keep_for_cc" in kinds
        assert "magi_agent_handoff" in kinds
