"""v1-P1 — Policy IR _LEGAL matrix validation.

Pattern from magi-agent customize/custom_rules.py::_LEGAL: declare a tabular
matrix of allowed (trigger.event × trigger.matcher-class × on_missing) triples
so a future builder UI can dropdown the legal combinations and the IR loader
rejects illegal ones with a clear message.

magi-agent's matrix sat in-loop; ours sits at IR validation time. Same shape,
different home.
"""
import pytest

from magi_cp.policy.matrix import (
    LEGAL_COMBINATIONS, MatcherClass,
    matcher_class_of, validate_combination, supported_events,
)


# ── matcher_class_of: classifies a matcher string into a known class ──
@pytest.mark.parametrize("matcher,kls", [
    ("Bash", MatcherClass.tool),
    ("Read", MatcherClass.tool),
    ("Edit", MatcherClass.tool),
    ("Write", MatcherClass.tool),
    ("mcp__court__file", MatcherClass.mcp_tool),
    ("mcp__magi__verify_citations", MatcherClass.mcp_tool),
    ("*", MatcherClass.wildcard),
    ("Bash|Edit", MatcherClass.tool_alt),
])
def test_matcher_class_known(matcher, kls):
    assert matcher_class_of(matcher) is kls


def test_matcher_class_unknown_raises():
    with pytest.raises(ValueError, match="unknown matcher class"):
        matcher_class_of("FooBar")


# ── supported_events declared explicitly ─────────────────────────────
def test_supported_events_contains_v0_events():
    s = supported_events()
    assert "PreToolUse" in s
    assert "PostToolUse" in s
    assert "Stop" in s


# ── legal combinations enumerated ────────────────────────────────────
def test_legal_combinations_includes_v0_legal_filing():
    """The v0 hard-coded policy (PreToolUse × Bash × block) must be legal."""
    assert ("PreToolUse", MatcherClass.tool, "block") in LEGAL_COMBINATIONS


def test_legal_combinations_includes_mcp_tool():
    assert ("PreToolUse", MatcherClass.mcp_tool, "block") in LEGAL_COMBINATIONS


def test_validate_combination_accepts_legal():
    validate_combination("PreToolUse", "Bash", "block")
    validate_combination("PreToolUse", "mcp__court__file", "block")


def test_validate_combination_rejects_illegal_event_matcher():
    """Stop + tool matcher is meaningless (Stop has no tool context)."""
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination("Stop", "Bash", "audit")


def test_validate_combination_rejects_illegal_action():
    """PostToolUse cannot block — the tool already ran."""
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination("PostToolUse", "Bash", "block")


def test_validate_combination_unknown_matcher_clear_error():
    with pytest.raises(ValueError, match="unknown matcher class"):
        validate_combination("PreToolUse", "GhostTool", "block")


# ── pluggable: tests cover the pattern, not the exact contents ───────
def test_legal_combinations_are_tuples_of_three():
    for combo in LEGAL_COMBINATIONS:
        assert len(combo) == 3
        ev, kls, action = combo
        assert isinstance(ev, str)
        assert isinstance(kls, MatcherClass)
        # D57f-2 widened the action vocabulary to include input_rewrite
        # (PreToolUse only). D63 added run_command (legal on every
        # supported event). Earlier rounds covered block / ask / audit.
        assert action in {
            "block", "ask", "audit", "input_rewrite", "run_command",
        }


def test_supported_events_covers_full_cc_surface():
    """D58: matrix supports the full CC hook surface (30 events as of
    CC 2.1.170; the doc still says 23 — the binary's `nV` enum is the
    truth source). Each name MUST appear unchanged; CC validates
    against this enum at managed-settings load time so a misspelled
    event would either be silently dropped (audit policy never fires)
    or reject the whole settings.json (gate goes fail-open until the
    operator notices)."""
    s = supported_events()
    expected = {
        # Tool-context family
        "PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch",
        # Permission gate family
        "PermissionRequest", "PermissionDenied",
        # Content-flow family
        "UserPromptSubmit", "UserPromptExpansion",
        "PreCompact", "PostCompact",
        "Elicitation", "ElicitationResult",
        # Subagent / Stop boundary family
        "SubagentStart", "SubagentStop",
        "Stop", "StopFailure",
        # Lifecycle / observability family
        "Setup", "Notification",
        "SessionStart", "SessionEnd",
        "TeammateIdle", "TaskCreated", "TaskCompleted",
        "ConfigChange",
        "WorktreeCreate", "WorktreeRemove",
        "InstructionsLoaded",
        "CwdChanged", "FileChanged",
        "MessageDisplay",
    }
    assert s == expected
    # And the pre-D58 8 events stay intact (back-compat).
    legacy_eight = {
        "PreToolUse", "PostToolUse",
        "Stop", "SubagentStop",
        "UserPromptSubmit",
        "PreCompact",
        "SessionStart", "SessionEnd",
    }
    assert legacy_eight.issubset(s)


@pytest.mark.parametrize("event,action", [
    ("UserPromptSubmit", "block"),  # block the prompt
    ("UserPromptSubmit", "ask"),    # interrupt for approval
    ("UserPromptSubmit", "audit"),  # log only
    ("PreCompact", "block"),        # protect the evidence chain
    ("PreCompact", "audit"),
    ("SubagentStop", "audit"),
    ("SessionStart", "audit"),
    ("SessionEnd", "audit"),
])
def test_validate_combination_accepts_no_tool_events_with_wildcard(event, action):
    """The 6 no-tool-context events require matcher='*' — wildcard is
    the only meaningful matcher class. validate_combination must
    accept those triples."""
    validate_combination(event, "*", action)


@pytest.mark.parametrize("event", [
    "UserPromptSubmit", "PreCompact",
    "SubagentStop", "SessionStart", "SessionEnd",
])
def test_no_tool_events_reject_tool_matcher(event):
    """A tool matcher on a no-tool-context event is meaningless and
    must be rejected by the IR loader before reaching the gate."""
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination(event, "Bash", "audit")


def test_session_end_cannot_block():
    """SessionEnd is observe-only — blocking a session that has already
    closed has no semantics."""
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination("SessionEnd", "*", "block")


def test_subagent_stop_cannot_block():
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination("SubagentStop", "*", "block")


def test_audit_is_legal_on_every_event():
    """D31: audit is the universal observe-only action."""
    for ev in supported_events():
        # Use the matcher class that the event supports
        matcher = "Bash" if ev in ("PreToolUse", "PostToolUse") else "*"
        validate_combination(ev, matcher, "audit")


# ── D58: full CC hook surface coverage ───────────────────────────────


@pytest.mark.parametrize("event,action", [
    # Gate-style pre-hooks the wizard surfaces with block/ask/audit.
    ("PermissionRequest", "block"),
    ("PermissionRequest", "ask"),
    ("PermissionRequest", "audit"),
    ("Elicitation", "block"),
    ("Elicitation", "ask"),
    ("Elicitation", "audit"),
])
def test_d58_pre_gate_events_accept_block_ask_audit(event, action):
    """PermissionRequest + Elicitation share the PreToolUse
    "override" channel (CC's hook stdout schema returns
    `{decision, updatedInput, additionalContext, continue}` on every
    hook); the wizard surfaces the full block/ask/audit set."""
    validate_combination(event, "*", action)


@pytest.mark.parametrize("event,action", [
    # Mid-process hooks: block is meaningful, ask has no interactive
    # surface to interrupt to (the prompt is already being expanded;
    # the compaction is already running).
    ("UserPromptExpansion", "block"),
    ("UserPromptExpansion", "audit"),
    ("PreCompact",          "block"),
    ("PreCompact",          "audit"),
])
def test_d58_block_audit_only_events(event, action):
    validate_combination(event, "*", action)


@pytest.mark.parametrize("event,action", [
    # ask is not legal on these (no interactive surface).
    ("UserPromptExpansion", "ask"),
    ("PreCompact",          "ask"),
])
def test_d58_block_audit_events_reject_ask(event, action):
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination(event, "*", action)


@pytest.mark.parametrize("event", [
    # The full audit-only wildcard surface.
    "PostToolUseFailure", "PostToolBatch",
    "PermissionDenied",
    "PostCompact", "ElicitationResult",
    "SubagentStart", "SubagentStop",
    "Stop", "StopFailure",
    "Setup", "Notification",
    "SessionStart", "SessionEnd",
    "TeammateIdle", "TaskCreated", "TaskCompleted",
    "ConfigChange",
    "WorktreeCreate", "WorktreeRemove",
    "InstructionsLoaded",
    "CwdChanged", "FileChanged",
    "MessageDisplay",
])
def test_d58_audit_only_events_legal(event):
    validate_combination(event, "*", "audit")


@pytest.mark.parametrize("event,bad_action", [
    # By policy intent, audit-only observability hooks cannot block.
    # The runtime can't refuse a Notification it already fired; it
    # can't undo a WorktreeCreate that already mutated the filesystem.
    ("Notification",       "block"),
    ("SessionStart",       "block"),
    ("WorktreeCreate",     "block"),
    ("FileChanged",        "block"),
    ("InstructionsLoaded", "block"),
    ("ConfigChange",       "ask"),
])
def test_d58_audit_only_events_reject_gate_actions(event, bad_action):
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination(event, "*", bad_action)


def test_d58_extended_events_reject_tool_matcher():
    """Every D58 no-tool-context event must reject a tool matcher.
    Same constraint the pre-D58 6 events carried."""
    new_events = [
        "PostToolUseFailure", "PostToolBatch",
        "PermissionRequest", "PermissionDenied",
        "UserPromptExpansion", "PostCompact",
        "Elicitation", "ElicitationResult",
        "SubagentStart",
        "StopFailure",
        "Setup", "Notification",
        "TeammateIdle", "TaskCreated", "TaskCompleted",
        "ConfigChange",
        "WorktreeCreate", "WorktreeRemove",
        "InstructionsLoaded",
        "CwdChanged", "FileChanged",
        "MessageDisplay",
    ]
    for ev in new_events:
        with pytest.raises(ValueError, match="illegal combination"):
            validate_combination(ev, "Bash", "audit")


# CC version this matrix was last calibrated against. Bump these
# constants in lock-step with any change to the binary candidate list
# (verified or unverified) so future maintainers know exactly which
# refresh they have to redo before adjusting the matrix.
CC_VERSION = "2.1.170"
CC_SHA = "1cda84def004ef3a8f569f8e8284a153a6b98c3a"


def test_d58_supported_events_count_is_30():
    """Bookmark the count + the EXACT expected event set so a future
    refresh has to explicitly name additions / removals. Anchoring on
    a count alone (the pre-D58-followup behavior) was wrong: a future
    maintainer's first instinct would be to bump 30 → 31 without
    re-verifying matrix entries. The expected-set assertion
    `test_supported_events_covers_full_cc_surface` is the strict
    contract; this test pins the magnitude as a sanity check.

    Calibrated against Claude Code CC_VERSION + CC_SHA above. Bump
    those constants when refreshing the matrix off a newer binary."""
    assert len(supported_events()) == 30
    # Witness that the constants exist in this module so reviewers
    # see the binary they came from at the same time they see the
    # count being changed.
    assert CC_VERSION == "2.1.170"
    assert CC_SHA == "1cda84def004ef3a8f569f8e8284a153a6b98c3a"


def test_d58_verified_vs_unverified_event_split():
    """D58-followup: matrix.py partitions the 30 names into a
    `_VERIFIED_EVENTS` set (the pre-D58 8 covered by the existing
    test fixtures + docs/architecture/claude-code-cli) and a larger
    `_UNVERIFIED_EVENTS` set (22 candidate names extracted from the
    binary's `strings(1)` output whose authorability has not been
    demonstrated against a real CC binary). The split is informational
    today; the wizard still surfaces unverified candidates. Any
    candidate moved to `_VERIFIED_EVENTS` MUST come with a binary
    fixture proving it authorable — see the matrix.py module docstring.
    """
    from magi_cp.policy.matrix import _VERIFIED_EVENTS, _UNVERIFIED_EVENTS
    # Sanity: the two sets partition supported_events() exactly.
    assert _VERIFIED_EVENTS.isdisjoint(_UNVERIFIED_EVENTS)
    assert _VERIFIED_EVENTS | _UNVERIFIED_EVENTS == supported_events()
    # Verified floor is exactly the pre-D58 8. This is the level the
    # matrix can safely fall back to without losing existing fixtures.
    assert _VERIFIED_EVENTS == {
        "PreToolUse", "PostToolUse",
        "Stop", "SubagentStop",
        "UserPromptSubmit",
        "PreCompact",
        "SessionStart", "SessionEnd",
    }
    assert len(_UNVERIFIED_EVENTS) == 22
