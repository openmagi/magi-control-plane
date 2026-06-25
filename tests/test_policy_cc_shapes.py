"""Contract tests for the canonical CC hook stdout shapes.

`magi_cp.policy.cc_shapes` is the single source of truth for the
deny / ask / allow JSON shapes the runtime gate emits and the
synthetic D77 simulator predicts. CC's PostToolUse / PostToolUseFailure
/ PostToolBatch channel consumes a DIFFERENT shape (top-level
`{decision, reason}`) than PreToolUse / PermissionRequest / etc.
(`hookSpecificOutput.permissionDecision`); previously the simulator
unconditionally emitted the hookSpecificOutput shape regardless of
event, and the dashboard's "Hook Specific Output (what CC sees)" panel
would have shown a different JSON than CC would actually receive.

This file pins the contract: a parametrized test across the
`RETRY_FEEDBACK_EVENTS` set asserts the simulator's
`hook_specific_output` is byte-equal to `emit_deny_payload` for both
event families.
"""
from __future__ import annotations

import pytest

from magi_cp.policy.cc_shapes import (
    RETRY_FEEDBACK_EVENTS,
    emit_allow_payload,
    emit_ask_payload,
    emit_deny_payload,
)


# ── emit_deny_payload shape ─────────────────────────────────────────


@pytest.mark.parametrize("event", ["PostToolUse", "PostToolUseFailure",
                                     "PostToolBatch"])
def test_deny_uses_top_level_decision_for_post_tool_events(event):
    out = emit_deny_payload("oops", hook_event_name=event)
    assert out == {"decision": "block", "reason": "MAGI: oops"}


@pytest.mark.parametrize("event", [
    "PreToolUse", "PermissionRequest", "SessionStart", "Stop",
    "UserPromptSubmit", "PreCompact",
])
def test_deny_uses_hook_specific_output_for_other_events(event):
    out = emit_deny_payload("oops", hook_event_name=event)
    assert "hookSpecificOutput" in out
    assert out["hookSpecificOutput"]["hookEventName"] == event
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert out["hookSpecificOutput"]["permissionDecisionReason"] == "MAGI: oops"


def test_deny_unknown_event_defaults_to_pretooluse_shape():
    out = emit_deny_payload("oops", hook_event_name="")
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


# ── runtime / simulator parity contract ─────────────────────────────


@pytest.mark.parametrize("event", sorted(RETRY_FEEDBACK_EVENTS))
def test_gate_runtime_and_simulator_emit_byte_equal_deny_shapes(event):
    """Both the runtime emitter (gate._emit_deny_payload) and the
    simulator (test_runner._evidence_policy_test → cc_shapes) MUST
    emit byte-equal JSON for the same (reason, event) pair. Drift
    here would mean the dashboard's "what CC sees" panel lies about
    what the runtime would actually send.
    """
    from magi_cp.local.gate import _emit_deny_payload as gate_emit
    runtime_shape = gate_emit("test reason", hook_event_name=event)
    simulator_shape = emit_deny_payload(
        "test reason", hook_event_name=event,
    )
    assert runtime_shape == simulator_shape


@pytest.mark.parametrize("event", ["PreToolUse", "PermissionRequest",
                                     "SessionStart"])
def test_gate_runtime_and_simulator_emit_byte_equal_deny_shapes_pre(event):
    """Same contract on the PreToolUse-style events."""
    from magi_cp.local.gate import _emit_deny_payload as gate_emit
    runtime_shape = gate_emit("test reason", hook_event_name=event)
    simulator_shape = emit_deny_payload(
        "test reason", hook_event_name=event,
    )
    assert runtime_shape == simulator_shape


# ── emit_ask_payload shape ──────────────────────────────────────────


def test_ask_uses_hook_specific_output_on_permission_lane():
    out = emit_ask_payload("HITL needed", hook_event_name="PreToolUse")
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert out["hookSpecificOutput"]["permissionDecisionReason"] == (
        "MAGI: HITL needed"
    )


@pytest.mark.parametrize("event", sorted(RETRY_FEEDBACK_EVENTS))
def test_ask_falls_back_to_deny_shape_on_post_tool_events(event):
    """The PostToolUse* channel has no "ask" verb (the tool already
    ran), so an ask request falls back to a deny shape so the operator
    sees retry-feedback rather than a silent allow.
    """
    out = emit_ask_payload("HITL needed", hook_event_name=event)
    assert out == {"decision": "block", "reason": "MAGI: HITL needed"}


# ── emit_allow_payload shape ────────────────────────────────────────


def test_allow_shape_carries_event_name_and_allow_verb():
    out = emit_allow_payload(hook_event_name="PreToolUse")
    assert out == {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
    }}
