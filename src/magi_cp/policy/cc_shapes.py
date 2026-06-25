"""Canonical CC hook stdout shapes.

CC's hook stdout JSON contract is split by event:

  - PreToolUse / PermissionRequest / SessionStart / Stop / etc. →
    `hookSpecificOutput` carries the gate decision as
    ``{"hookEventName": <event>, "permissionDecision": "deny"|"ask"|"allow",
       "permissionDecisionReason": "..."}``. CC's permission flow
    consumes that shape and refuses / asks / allows the call.
  - PostToolUse / PostToolUseFailure / PostToolBatch →
    CC does NOT consume `hookSpecificOutput.permissionDecision` on
    these three events (the tool already ran; there is no permission
    lane to gate). Instead CC reads top-level
    ``{"decision": "block", "reason": "..."}`` and surfaces the reason
    to the model as retry-feedback.

Three surfaces emit these shapes:

  - `src/magi_cp/local/gate.py` — the runtime path (live CC hook).
  - `src/magi_cp/policy/test_runner.py` — the synthetic
    "Test this policy" simulator (D77).
  - downstream serializers that need to mirror what CC will see.

This module centralizes the shape so a future widening (or narrowing)
lands in one place. A contract test in
`tests/test_policy_cc_shapes.py` pins byte-equality between gate.py
and the simulator across the `_RETRY_FEEDBACK_EVENTS` set.
"""
from __future__ import annotations


# Event set whose CC channel consumes top-level `{decision, reason}`
# instead of `hookSpecificOutput.permissionDecision`. Reused by gate.py
# (the runtime emitter) and test_runner.py (the simulator).
RETRY_FEEDBACK_EVENTS: frozenset[str] = frozenset({
    "PostToolUse", "PostToolUseFailure", "PostToolBatch",
})


def emit_deny_payload(reason: str, *, hook_event_name: str) -> dict:
    """Return the canonical deny JSON for a given CC hook event.

    `reason` is the policy-supplied human reason (we prepend `MAGI: `
    so the operator sees a stable provenance marker in the CC
    transcript).

    `hook_event_name` selects the channel:
      - in `RETRY_FEEDBACK_EVENTS` → top-level decision/reason.
      - otherwise → hookSpecificOutput.permissionDecision (the
        historical default for PreToolUse + permission-lane hooks).
    """
    if hook_event_name in RETRY_FEEDBACK_EVENTS:
        # CC's PostToolUse* channel reads top-level decision + reason
        # and surfaces the reason to the model as retry-feedback.
        return {
            "decision": "block",
            "reason": f"MAGI: {reason}",
        }
    # PreToolUse + the rest of the pre-side gate hooks consume
    # hookSpecificOutput.permissionDecision. We default unknown event
    # names to this shape because the historical default was PreToolUse;
    # CC's authoring contract guards the unknown-name case server-side.
    return {
        "hookSpecificOutput": {
            "hookEventName": hook_event_name or "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"MAGI: {reason}",
        }
    }


def emit_ask_payload(reason: str, *, hook_event_name: str) -> dict:
    """Return the canonical ask JSON for a given CC hook event.

    HITL ask shape only exists on the permission-lane channel; the
    PostToolUse* channel has no "ask" verb (the tool already ran).
    For PostToolUse* events we fall back to a deny shape so the
    operator sees retry-feedback rather than a silent allow.
    """
    if hook_event_name in RETRY_FEEDBACK_EVENTS:
        return emit_deny_payload(reason, hook_event_name=hook_event_name)
    return {
        "hookSpecificOutput": {
            "hookEventName": hook_event_name or "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": f"MAGI: {reason}",
        }
    }


def emit_allow_payload(*, hook_event_name: str) -> dict:
    """Return the canonical allow JSON for a given CC hook event.

    On the permission-lane channel this is the explicit-allow shape
    (rare; CC defaults to allow on silent gate exit). On the
    PostToolUse* channel an explicit allow is structurally a no-op
    (the tool already ran) — we still return the explicit shape so
    the simulator can round-trip it through the dashboard pill.
    """
    return {
        "hookSpecificOutput": {
            "hookEventName": hook_event_name or "PreToolUse",
            "permissionDecision": "allow",
        },
    }


__all__ = [
    "RETRY_FEEDBACK_EVENTS",
    "emit_allow_payload",
    "emit_ask_payload",
    "emit_deny_payload",
]
