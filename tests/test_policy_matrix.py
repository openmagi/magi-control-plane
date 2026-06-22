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
    """The v0 hard-coded policy (PreToolUse × Bash × deny) must be legal."""
    assert ("PreToolUse", MatcherClass.tool, "deny") in LEGAL_COMBINATIONS


def test_legal_combinations_includes_mcp_tool():
    assert ("PreToolUse", MatcherClass.mcp_tool, "deny") in LEGAL_COMBINATIONS


def test_validate_combination_accepts_legal():
    validate_combination("PreToolUse", "Bash", "deny")
    validate_combination("PreToolUse", "mcp__court__file", "deny")


def test_validate_combination_rejects_illegal_event_matcher():
    """Stop event + tool matcher is meaningless (Stop fires once per turn)."""
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination("Stop", "Bash", "deny")


def test_validate_combination_rejects_illegal_decision():
    """PostToolUse cannot deny — the tool already ran."""
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination("PostToolUse", "Bash", "deny")


def test_validate_combination_unknown_matcher_clear_error():
    with pytest.raises(ValueError, match="unknown matcher class"):
        validate_combination("PreToolUse", "GhostTool", "deny")


# ── pluggable: tests cover the pattern, not the exact contents ───────
def test_legal_combinations_are_tuples_of_three():
    for combo in LEGAL_COMBINATIONS:
        assert len(combo) == 3
        ev, kls, dec = combo
        assert isinstance(ev, str)
        assert isinstance(kls, MatcherClass)
        assert dec in {"deny", "ask", "allow", "log"}


# ── post-D28: scope expanded from 3 events to 8 (Claude Code's full
# hook set minus Notification, which has no governance signal). ───────
def test_supported_events_covers_full_8():
    s = supported_events()
    assert s == {
        "PreToolUse", "PostToolUse",
        "Stop", "SubagentStop",
        "UserPromptSubmit",
        "PreCompact",
        "SessionStart", "SessionEnd",
    }


@pytest.mark.parametrize("event,decision", [
    ("UserPromptSubmit", "deny"),   # block the prompt
    ("UserPromptSubmit", "ask"),    # interrupt for approval
    ("UserPromptSubmit", "log"),    # audit
    ("PreCompact", "deny"),         # protect the evidence chain
    ("PreCompact", "log"),
    ("SubagentStop", "log"),
    ("SessionStart", "log"),
    ("SessionEnd", "log"),
])
def test_validate_combination_accepts_no_tool_events_with_wildcard(event, decision):
    """The 6 no-tool-context events require matcher='*' — wildcard is
    the only meaningful matcher class. validate_combination must
    accept those triples."""
    validate_combination(event, "*", decision)


@pytest.mark.parametrize("event", [
    "UserPromptSubmit", "PreCompact",
    "SubagentStop", "SessionStart", "SessionEnd",
])
def test_no_tool_events_reject_tool_matcher(event):
    """A tool matcher on a no-tool-context event is meaningless and
    must be rejected by the IR loader before reaching the gate."""
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination(event, "Bash", "log")


def test_session_end_cannot_deny():
    """SessionEnd is observe-only — denying a session that has already
    closed has no semantics."""
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination("SessionEnd", "*", "deny")


def test_subagent_stop_cannot_deny():
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination("SubagentStop", "*", "deny")
