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
# D58 — event scope expanded from 8 to the full Claude Code hook
# surface (30 events as of CC 2.1.170; the architecture doc still says
# "23 hook events" because the doc was written before the four 2.1.x
# rounds of additions). Names come from the canonical `nV` enum in the
# bundled CC binary (Claude Code 2.1.170, sha
# 1cda84def004ef3a8f569f8e8284a153a6b98c3a), extracted via
# `strings(1)`. That list IS the truth source — the cloud refuses to
# author a policy on an event CC never fires, and the binary's runtime
# refuses to even load a settings.json that names an unknown event.
#
# The 30 names split into 5 families:
#
#   tool-context events       — Pre/Post/Failure/Batch. Tool name in
#                               the payload, so tool / mcp_tool /
#                               tool_alt matchers apply on the pre/post
#                               pair; the failure + batch variants are
#                               audit-only.
#   permission gate events    — PermissionRequest (pre) /
#                               PermissionDenied (post). The pre side
#                               accepts block/ask/audit because the
#                               PreToolUse "override permission"
#                               contract is the same channel; the post
#                               side is audit-only.
#   content-flow events       — UserPromptSubmit / UserPromptExpansion
#                               / PreCompact / PostCompact /
#                               Elicitation / ElicitationResult. The
#                               *pre* sides accept gate actions; the
#                               *post* sides audit-only.
#   subagent / stop boundary  — SubagentStart / SubagentStop / Stop /
#                               StopFailure. Audit-only — by the time
#                               these fire the runtime cannot rewind.
#   lifecycle / observability — Setup / Notification / SessionStart /
#                               SessionEnd / TeammateIdle / TaskCreated
#                               / TaskCompleted / ConfigChange /
#                               WorktreeCreate / WorktreeRemove /
#                               InstructionsLoaded / CwdChanged /
#                               FileChanged / MessageDisplay. Boundary
#                               markers; audit-only.
#
# Action availability rule remains the same: pre-event hooks (PreTool,
# PermissionRequest, UserPromptSubmit, UserPromptExpansion, PreCompact,
# Elicitation) can block/ask/audit; post-event + observability hooks
# audit-only. Every event can audit.
_BLOCK_ASK_AUDIT = ("block", "ask", "audit")
_BLOCK_AUDIT = ("block", "audit")
_AUDIT_ONLY = ("audit",)

# Lifecycle / boundary observability hooks — wildcard + audit-only.
_AUDIT_ONLY_WILDCARD_EVENTS = (
    # Tool-context observability variants (no per-tool matcher in v1;
    # the Failure + Batch payloads do carry tool data but the wizard
    # doesn't expose a per-tool surface for them yet — they round-trip
    # as wildcard audit). The base Pre/PostToolUse entries below
    # still carry their richer matcher set.
    "PostToolUseFailure",
    "PostToolBatch",
    # Permission gate post-side
    "PermissionDenied",
    # Content-flow post-side
    "PostCompact",
    "ElicitationResult",
    # Subagent / Stop boundary
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "StopFailure",
    # Lifecycle / observability surface
    "Setup",
    "Notification",
    "SessionStart",
    "SessionEnd",
    "TeammateIdle",
    "TaskCreated",
    "TaskCompleted",
    "ConfigChange",
    "WorktreeCreate",
    "WorktreeRemove",
    "InstructionsLoaded",
    "CwdChanged",
    "FileChanged",
    "MessageDisplay",
)


def _build_legal() -> frozenset[tuple[str, MatcherClass, str]]:
    out: set[tuple[str, MatcherClass, str]] = set()
    # PreToolUse — block / ask / audit on every matcher class; wildcard
    # can audit but not block (would be too broad to surface in UI).
    for kls in (MatcherClass.tool, MatcherClass.mcp_tool, MatcherClass.tool_alt):
        for act in _BLOCK_ASK_AUDIT:
            out.add(("PreToolUse", kls, act))
    out.add(("PreToolUse", MatcherClass.wildcard, "audit"))

    # PostToolUse — tool already ran; only audit is legal. (strip will
    # land here in a follow-up once verifier-protocol mutation lands.)
    out.add(("PostToolUse", MatcherClass.tool, "audit"))
    out.add(("PostToolUse", MatcherClass.mcp_tool, "audit"))

    # Pre-side gate hooks (no-tool-context) — block / ask / audit on
    # wildcard.
    for ev in ("UserPromptSubmit", "PermissionRequest", "Elicitation"):
        for act in _BLOCK_ASK_AUDIT:
            out.add((ev, MatcherClass.wildcard, act))

    # Pre-side gate hooks that can block but not ask (the prompt is
    # mid-expansion or the compaction is already running; "ask" has no
    # interactive surface to interrupt to).
    for ev in ("UserPromptExpansion", "PreCompact"):
        for act in _BLOCK_AUDIT:
            out.add((ev, MatcherClass.wildcard, act))

    # Audit-only wildcard surface (the long tail).
    for ev in _AUDIT_ONLY_WILDCARD_EVENTS:
        out.add((ev, MatcherClass.wildcard, "audit"))

    return frozenset(out)


LEGAL_COMBINATIONS: frozenset[tuple[str, MatcherClass, str]] = _build_legal()


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
