"""Policy IR legal-combination matrix.

Pattern from magi-agent customize/custom_rules.py::_LEGAL: a small tabular
declaration of *what trigger × matcher × decision triples are even meaningful*.
The builder UI uses this to constrain dropdowns; the IR loader uses it to
reject illegal authoring before anything reaches the gate.

Adding a new event or matcher class? Update this file in one place.
"""
from __future__ import annotations
import enum
import re


class MatcherClass(enum.Enum):
    """Categories of `trigger.matcher` strings that have shared semantics."""
    tool = "tool"             # built-in tools (Bash, Read, Edit, Write)
    mcp_tool = "mcp_tool"     # MCP namespaced (mcp__server__name)
    wildcard = "wildcard"     # "*"
    tool_alt = "tool_alt"     # "Bash|Edit|..." pipe alternation


_BUILTIN_TOOLS = frozenset({
    "Bash", "Read", "Edit", "Write", "Glob", "Grep",
    "NotebookEdit", "TodoWrite", "WebFetch", "WebSearch",
})
_MCP_TOOL_RE = re.compile(r"^mcp__[A-Za-z0-9_]+__[A-Za-z0-9_]+$")


def matcher_class_of(matcher: str) -> MatcherClass:
    if matcher == "*":
        return MatcherClass.wildcard
    if "|" in matcher:
        parts = [p.strip() for p in matcher.split("|") if p.strip()]
        if all(p in _BUILTIN_TOOLS for p in parts):
            return MatcherClass.tool_alt
        raise ValueError(f"unknown matcher class: {matcher!r}")
    if matcher in _BUILTIN_TOOLS:
        return MatcherClass.tool
    if _MCP_TOOL_RE.match(matcher):
        return MatcherClass.mcp_tool
    raise ValueError(f"unknown matcher class: {matcher!r}")


# ── _LEGAL matrix ────────────────────────────────────────────────────
# (event, matcher_class, action) — *meaningful* combinations only.
# Builder UI dropdowns enumerate this; IR loader rejects anything missing.
#
# D31: triples now use the action archetype vocabulary (block / ask /
# audit) instead of the prior decision wording (deny / ask / log /
# allow). Migration is mechanical at deserialization (`_coerce_action`
# in ir.py). Audit is the universal "record only" action and is legal
# for every event × matcher_class pair the runtime supports.
#
# Event scope: Claude Code's 9 hook points minus Notification. The
# remaining 8 split into two families:
#
#   tool-context events    — Pre/PostToolUse. Carry a tool name, so
#                            tool / mcp_tool / tool_alt matchers apply.
#   no-tool-context events — Stop, UserPromptSubmit, SubagentStop,
#                            PreCompact, SessionStart, SessionEnd. The
#                            hook has no tool, so matcher is forced "*".
#
# Action availability follows the lifecycle: pre-event hooks (PreTool,
# UserPromptSubmit, PreCompact) can block or ask; post-event hooks can
# only audit. Every event can audit.
LEGAL_COMBINATIONS: frozenset[tuple[str, MatcherClass, str]] = frozenset({
    # PreToolUse — block / ask / audit on every matcher class; wildcard
    # can audit but not block (would be too broad to surface in UI).
    ("PreToolUse", MatcherClass.tool,       "block"),
    ("PreToolUse", MatcherClass.tool,       "ask"),
    ("PreToolUse", MatcherClass.tool,       "audit"),
    ("PreToolUse", MatcherClass.mcp_tool,   "block"),
    ("PreToolUse", MatcherClass.mcp_tool,   "ask"),
    ("PreToolUse", MatcherClass.mcp_tool,   "audit"),
    ("PreToolUse", MatcherClass.tool_alt,   "block"),
    ("PreToolUse", MatcherClass.tool_alt,   "ask"),
    ("PreToolUse", MatcherClass.tool_alt,   "audit"),
    ("PreToolUse", MatcherClass.wildcard,   "audit"),

    # PostToolUse — tool already ran; only audit is legal. (strip will
    # land here in a follow-up once verifier-protocol mutation lands.)
    ("PostToolUse", MatcherClass.tool,      "audit"),
    ("PostToolUse", MatcherClass.mcp_tool,  "audit"),

    # No-tool-context events all use wildcard. Their action set follows
    # whether the hook fires before or after the moment the policy is
    # interested in.
    ("UserPromptSubmit", MatcherClass.wildcard, "block"),
    ("UserPromptSubmit", MatcherClass.wildcard, "ask"),
    ("UserPromptSubmit", MatcherClass.wildcard, "audit"),
    ("PreCompact",       MatcherClass.wildcard, "block"),
    ("PreCompact",       MatcherClass.wildcard, "audit"),
    ("Stop",             MatcherClass.wildcard, "audit"),
    ("SubagentStop",     MatcherClass.wildcard, "audit"),
    ("SessionStart",     MatcherClass.wildcard, "audit"),
    ("SessionEnd",       MatcherClass.wildcard, "audit"),
})


def supported_events() -> frozenset[str]:
    return frozenset(ev for ev, _, _ in LEGAL_COMBINATIONS)


def validate_combination(event: str, matcher: str, decision: str) -> None:
    """Raise ValueError with a precise message if the triple is not legal."""
    kls = matcher_class_of(matcher)
    if (event, kls, decision) not in LEGAL_COMBINATIONS:
        raise ValueError(
            f"illegal combination: event={event!r} matcher={matcher!r}"
            f" (class={kls.value}) decision={decision!r}"
        )
