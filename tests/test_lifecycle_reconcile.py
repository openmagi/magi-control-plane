"""Cluster A regression: reconcile the accepted lifecycle event set.

The extractor, the wizard handoff, and the IR validator all speak a wide
(~30) hook-event vocabulary, but the turn-engine gates historically only
accepted the 3 common buckets {PreToolUse, PostToolUse, Stop}. A draft
carrying a legally-wider event (e.g. SessionStart, PermissionRequest) was
then treated as still-missing its lifecycle: Save stayed disabled while
the state machine said "ready", the sanitizer silently deleted the
operator's chosen event, and run_command on a wider lifecycle could never
be finished in chat.

Kevin's decision was WIDEN (not clamp): accept the full matrix-legal event
set for the evidence archetype and the full run_command lifecycle set for
run_command. These tests lock the widened, reconciled behavior in place and
assert the 3-bucket common case is unchanged.
"""

from __future__ import annotations

# The policy package has a latent import cycle: importing
# `nl_compiler_interactive` first pulls `cloud.__init__` -> `app` ->
# `schemas` back into the partially-initialised module. Warm the chain via
# the documented entry point before importing the module under test.
import magi_cp.cloud.app  # noqa: F401
import magi_cp.policy.nl_compiler_interactive as nci
from magi_cp.policy.handoff_context import _RUN_COMMAND_LIFECYCLE_TO_EVENT
from magi_cp.policy.matrix import supported_events


# ── source-of-truth event sets ────────────────────────────────────────


def test_run_command_legal_events_equals_canonical_map_values():
    """The run_command event set the gates consult must stay in lock-step
    with the canonical `_RUN_COMMAND_LIFECYCLE_TO_EVENT` map (which mirrors
    page.tsx RUN_COMMAND_LEGAL_BY_LIFECYCLE 1:1). If someone adds a slug to
    the map, the gate predicate must pick it up automatically."""
    assert nci._run_command_legal_events() == frozenset(
        _RUN_COMMAND_LIFECYCLE_TO_EVENT.values()
    )


def test_evidence_legal_events_equals_matrix_supported_events():
    """The evidence event set the gates consult is exactly the matrix's
    legal event vocabulary."""
    assert nci._evidence_legal_events() == supported_events()


def test_three_buckets_are_a_subset_of_both_archetype_sets():
    """Widening must be a superset move: the 3 common buckets stay legal
    for both archetypes so the common case cannot regress."""
    buckets = {"PreToolUse", "PostToolUse", "Stop"}
    assert buckets <= nci._evidence_legal_events()
    assert buckets <= nci._run_command_legal_events()


# ── shared predicate ──────────────────────────────────────────────────


def test_event_complete_predicate_accepts_wide_evidence_event():
    draft = {
        "trigger": {"host": "claude-code", "event": "SessionStart", "matcher": "*"},
    }
    assert nci._event_is_complete_for_archetype(draft) is True


def test_event_complete_predicate_accepts_wide_run_command_event():
    draft = {
        "type": "run_command",
        "trigger": {
            "host": "claude-code", "event": "PermissionRequest", "matcher": "*",
        },
    }
    assert nci._event_is_complete_for_archetype(draft) is True


def test_event_complete_predicate_rejects_unknown_event():
    draft = {
        "trigger": {"host": "claude-code", "event": "MadeUpEvent", "matcher": "*"},
    }
    assert nci._event_is_complete_for_archetype(draft) is False


def test_event_complete_predicate_rejects_missing_event():
    assert nci._event_is_complete_for_archetype({"trigger": {"matcher": "*"}}) is False
    assert nci._event_is_complete_for_archetype({}) is False


# ── _missing_fields_for_draft (evidence) ──────────────────────────────


def _wide_evidence_draft() -> dict:
    return {
        "id": "sess-scan",
        "version": "0.1",
        "trigger": {"host": "claude-code", "event": "SessionStart", "matcher": "*"},
        "requires": [
            {"kind": "llm_critic", "criterion": "the session context is safe"},
        ],
        "action": "audit",
    }


def test_missing_fields_does_not_list_lifecycle_for_wide_evidence_event():
    draft = _wide_evidence_draft()
    missing = nci._missing_fields_for_draft(draft)
    assert "lifecycle" not in missing, missing
    # A fully-specified wide-event draft has nothing left to ask.
    assert missing == [], missing


def test_conversation_state_and_missing_agree_for_wide_evidence_event():
    """R2-01: previously state=S4_ready while missing listed 'lifecycle'.
    They must never contradict."""
    draft = _wide_evidence_draft()
    ok, _ = nci._draft_passes_ir_validator(draft)
    assert ok
    assert nci._conversation_state(draft) == "S4_ready"
    assert nci._missing_fields_for_draft(draft) == []


def test_missing_fields_still_lists_lifecycle_for_unknown_event():
    draft = _wide_evidence_draft()
    draft["trigger"]["event"] = "TotallyMadeUp"
    assert "lifecycle" in nci._missing_fields_for_draft(draft)


# ── _run_command_missing_fields ───────────────────────────────────────


def _wide_run_command_draft() -> dict:
    return {
        "type": "run_command",
        "id": "perm-audit",
        "version": "0.1",
        "trigger": {
            "host": "claude-code", "event": "PermissionRequest", "matcher": "*",
        },
        "command": "echo hi",
    }


def test_run_command_missing_fields_does_not_list_lifecycle_for_wide_event():
    draft = _wide_run_command_draft()
    missing = nci._run_command_missing_fields(draft)
    assert "lifecycle" not in missing, missing
    # Also via the dispatching entry point.
    assert "lifecycle" not in nci._missing_fields_for_draft(draft)


def test_run_command_missing_fields_still_lists_lifecycle_for_unknown_event():
    draft = _wide_run_command_draft()
    draft["trigger"]["event"] = "TotallyMadeUp"
    assert "lifecycle" in nci._run_command_missing_fields(draft)


# ── _sanitize_draft_so_far ────────────────────────────────────────────


def test_sanitize_preserves_every_matrix_legal_evidence_event():
    """R2-02: the sanitizer must NOT delete a legal wider event on echo."""
    for event in sorted(supported_events()):
        raw = {"trigger": {"host": "claude-code", "event": event, "matcher": "*"}}
        out = nci._sanitize_draft_so_far(raw)
        assert out["trigger"].get("event") == event, event


def test_sanitize_preserves_every_run_command_legal_event():
    for event in sorted(_RUN_COMMAND_LIFECYCLE_TO_EVENT.values()):
        raw = {
            "type": "run_command",
            "command": "echo hi",
            "trigger": {"host": "claude-code", "event": event, "matcher": "*"},
        }
        out = nci._sanitize_draft_so_far(raw)
        assert out["trigger"].get("event") == event, event


def test_sanitize_drops_unknown_event():
    """Widen to the LEGAL set, not to anything: an illegal/unknown event is
    still stripped so it cannot ride onto a saved draft."""
    raw = {"trigger": {"host": "claude-code", "event": "MadeUpEvent", "matcher": "*"}}
    out = nci._sanitize_draft_so_far(raw)
    assert "event" not in out["trigger"], out["trigger"]
    # host is still pinned.
    assert out["trigger"]["host"] == "claude-code"


def test_sanitize_round_trips_wide_evidence_event_across_echo():
    """Full-fidelity round trip: a complete SessionStart draft survives a
    sanitize pass with its event intact (the operator's 'when' does not
    vanish)."""
    draft = _wide_evidence_draft()
    out = nci._sanitize_draft_so_far(draft)
    assert out["trigger"]["event"] == "SessionStart"


# ── 3-bucket common case is byte-identical ────────────────────────────


def test_common_bucket_case_unchanged():
    """The shipped 3-bucket path must behave exactly as before. Each draft
    below is a matrix-legal (event, matcher, decision) combination for its
    bucket (Stop is wildcard/audit only)."""
    cases = [
        ("PreToolUse", "Bash", "block"),
        ("PostToolUse", "Bash", "block"),
        ("Stop", "*", "audit"),
    ]
    for event, matcher, action in cases:
        draft = {
            "id": "block-bash",
            "version": "0.1",
            "trigger": {"host": "claude-code", "event": event, "matcher": matcher},
            "requires": [{"kind": "regex", "pattern": "^ok$"}],
            "action": action,
        }
        ok, _ = nci._draft_passes_ir_validator(draft)
        assert ok, event
        assert nci._missing_fields_for_draft(draft) == [], event
        assert nci._conversation_state(draft) == "S4_ready", event
        san = nci._sanitize_draft_so_far(draft)
        assert san["trigger"]["event"] == event, event


def test_incomplete_bucket_draft_still_lists_lifecycle_when_no_event():
    draft = {
        "trigger": {"host": "claude-code", "matcher": "Bash"},
        "requires": [{"kind": "regex", "pattern": "^ok$"}],
        "action": "block",
    }
    assert "lifecycle" in nci._missing_fields_for_draft(draft)
