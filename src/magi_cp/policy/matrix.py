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
# (event, matcher_class, decision) — *meaningful* combinations only.
# Builder UI dropdowns enumerate this; IR loader rejects anything missing.
LEGAL_COMBINATIONS: frozenset[tuple[str, MatcherClass, str]] = frozenset({
    # PreToolUse — fires before tool execution; deny/ask both useful.
    ("PreToolUse", MatcherClass.tool,       "deny"),
    ("PreToolUse", MatcherClass.tool,       "ask"),
    ("PreToolUse", MatcherClass.mcp_tool,   "deny"),
    ("PreToolUse", MatcherClass.mcp_tool,   "ask"),
    ("PreToolUse", MatcherClass.tool_alt,   "deny"),
    ("PreToolUse", MatcherClass.tool_alt,   "ask"),
    ("PreToolUse", MatcherClass.wildcard,   "log"),   # broad observation only
    # PostToolUse — tool already ran; allow/log only (cannot retroactively deny).
    ("PostToolUse", MatcherClass.tool,      "log"),
    ("PostToolUse", MatcherClass.tool,      "allow"),
    ("PostToolUse", MatcherClass.mcp_tool,  "log"),
    ("PostToolUse", MatcherClass.mcp_tool,  "allow"),
    # Stop — turn end. Matcher is conventionally "*"; can request a continue.
    ("Stop", MatcherClass.wildcard, "log"),
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
