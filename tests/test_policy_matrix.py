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
        assert action in {"block", "ask", "audit"}


def test_supported_events_covers_full_8():
    s = supported_events()
    assert s == {
        "PreToolUse", "PostToolUse",
        "Stop", "SubagentStop",
        "UserPromptSubmit",
        "PreCompact",
        "SessionStart", "SessionEnd",
    }


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
