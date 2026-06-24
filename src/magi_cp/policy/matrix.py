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
# D58 — event scope expanded from 8 (pre-D58 verified) to a 30-event
# candidate surface. CC version anchor: Claude Code 2.1.170, sha
# 1cda84def004ef3a8f569f8e8284a153a6b98c3a.
#
# Truth source priority (D58-followup, in order):
#
#   1. `entrypoints/sdk/coreTypes.ts` in the bundled SDK type union —
#      this is the *authoring contract*. Whatever event names that
#      Literal accepts is what `settings.json -> hooks` can name.
#   2. Documented hook table in
#      docs/architecture/claude-code-cli/08-coding-harness-internals.md
#      (currently anchored on 23 events; lags 2.1.x).
#   3. `strings(1)` output from the binary — SUPERSET of (1)+(2). Binary
#      strings include internal event-bus topics, log keys, and
#      telemetry markers that the runtime fires but does NOT expose as
#      authorable settings.json keys.
#
# Of the 30 names listed below, only the pre-D58 8 (named in
# `_VERIFIED_EVENTS` below) are end-to-end verified to be authorable
# via `settings.json -> hooks` against a real CC binary. The other 22
# are CANDIDATE names extracted from binary strings whose authoring
# behavior has NOT been demonstrated. They are kept in the matrix as
# the working hypothesis so the wizard can surface them, but two
# silent-fail-open paths the reviewer flagged are real:
#
#   (a) if CC silently drops unknown hook event keys, an unverified
#       candidate event would round-trip as "saved" but the hook would
#       never fire (operator sees a green check, no enforcement);
#   (b) if CC rejects unknown event keys at settings.json load, an
#       unverified candidate would refuse the whole file, sending the
#       gate fail-open across every policy in it.
#
# Mitigations applied in this commit:
#   - the unverified-21 are flagged via `_UNVERIFIED_EVENTS` so future
#     readers see exactly which entries lack a binary fixture proof;
#   - `ContextInjectionPolicy` is wired to the full hook surface (see
#     `_CONTEXT_EVENT_LITERALS` in ir.py = sorted `_SUPPORTED_EVENTS`)
#     because CC's hookSpecificOutput JSON schema accepts
#     `additionalContext` on every hook event; the unverified-22
#     silent-fail-open paths still apply to context_injection
#     authoring, mitigated by the matrix-coherence gate added to
#     `ContextInjectionPolicy.validate()` (per-tool matcher classes
#     are illegal on no-tool-context events even when the event name
#     is recognized);
#   - tests/test_policy_matrix.py asserts set-equality (not just the
#     count) so a future binary refresh has to explicitly name added /
#     removed events.
#
# Required follow-up before flipping any candidate event to verified:
# a CC-binary integration fixture that authors a hook on the candidate
# event and observes either (i) the hook firing on the corresponding
# runtime event, or (ii) CC raising a `Hook JSON output had unrecognized
# keys` / unknown-hook-event error. Until then the candidate stays in
# `_UNVERIFIED_EVENTS`.
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

# D58-followup — verification status, per-event. The matrix-fidelity
# floor (8 events) is exactly the pre-D58 surface, all of which the
# existing test suite + the docs/architecture/claude-code-cli/
# 08-coding-harness-internals.md table covers end-to-end. The 22 in
# `_UNVERIFIED_EVENTS` are the binary-strings candidate names whose
# authorability has NOT been demonstrated against a real CC binary;
# see the module docstring above for the silent-fail-open paths they
# expose. Adding a name here without a binary fixture is intentional
# (we'd rather wire the wizard surface than refuse it), but every
# entry in `_UNVERIFIED_EVENTS` is on notice: future cycles MUST
# either prove it authorable (move to `_VERIFIED_EVENTS`) or drop it.
_VERIFIED_EVENTS: frozenset[str] = frozenset({
    "PreToolUse", "PostToolUse",
    "Stop", "SubagentStop",
    "UserPromptSubmit",
    "PreCompact",
    "SessionStart", "SessionEnd",
})
_UNVERIFIED_EVENTS: frozenset[str] = frozenset({
    # Tool-context observability variants
    "PostToolUseFailure", "PostToolBatch",
    # Permission gate family
    "PermissionRequest", "PermissionDenied",
    # Content-flow extensions
    "UserPromptExpansion", "PostCompact",
    "Elicitation", "ElicitationResult",
    # Subagent / Stop boundary
    "SubagentStart", "StopFailure",
    # Lifecycle / observability surface
    "Setup", "Notification",
    "TeammateIdle", "TaskCreated", "TaskCompleted",
    "ConfigChange",
    "WorktreeCreate", "WorktreeRemove",
    "InstructionsLoaded",
    "CwdChanged", "FileChanged",
    "MessageDisplay",
})

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

    # D57f-2: input_rewrite — CC PreToolUse hook stdout supports
    # `updatedInput` which lets the gate rewrite the tool's input before
    # the tool runs. Legal on per-tool matchers only (the rewriter targets
    # a single named field in the tool_input dict; the field grammar
    # only makes sense once you've picked a tool family). Wildcard is
    # intentionally NOT registered — see InputRewritePolicy.validate()
    # for the rationale (a wildcard rewriter would mutate every tool's
    # input field of the same name, which is rarely intended).
    for kls in (MatcherClass.tool, MatcherClass.mcp_tool, MatcherClass.tool_alt):
        out.add(("PreToolUse", kls, "input_rewrite"))

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


def matcher_covers(matcher: str, tool_name: str) -> bool:
    """Predicate: does `matcher` cover `tool_name`?

    Single source of truth for the runtime "this hook fired for this
    tool" comparison. Built on `matcher_class_of` so any future matcher
    class (wildcard variants, regex matchers, ...) lands here once
    instead of being re-implemented at every call site.

    Semantics per matcher class:
      - wildcard ("*")     → True for any tool_name. Disallowed for
        input_rewrite at authoring time; defensive callers should
        refuse this BEFORE calling matcher_covers if a wildcard rewrite
        would be a corrupted store row.
      - tool / mcp_tool    → exact string equality.
      - tool_alt           → tool_name is one of the `|`-separated
        parts.

    Returns False on any classification error (unknown matcher shape)
    rather than raising — the runtime endpoint's contract is "soft
    fail to no-op", and a corrupted matcher should not crash the
    request handler. Authoring-time validation is the place to refuse.
    """
    try:
        kls = matcher_class_of(matcher)
    except ValueError:
        return False
    if kls is MatcherClass.wildcard:
        return True
    if kls is MatcherClass.tool_alt:
        return tool_name in {
            p.strip() for p in matcher.split("|") if p.strip()
        }
    # tool / mcp_tool → exact string match.
    return matcher == tool_name
