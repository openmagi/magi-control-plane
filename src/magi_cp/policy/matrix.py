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


# D70 — built-in tool registry. Source = the same CC 2.1.170 binary
# strings extraction D58 used for the event surface; pulling matchers
# from the same artifact keeps the audit symmetric across events and
# tools. Adding tools here lets the wizard's per-tool matcher classes
# (`MatcherClass.tool` / `tool_alt`) cover the full built-in surface
# instead of refusing legitimate tool names at IR load time.
#
# The previous floor (10 tools) was the pre-D58 wizard chip-grid
# subset; the binary actually surfaces 17 named built-ins. The 7 added
# below (Task / MultiEdit / BashOutput / KillBash / NotebookRead /
# ExitPlanMode / AskUser) all appear in CC 2.1.170's tool registry and
# in the strings(1) output. The D69 Common-tier TaskCompleted promo
# implicitly tells operators that `Task` is a legal matcher value;
# refusing it at `matcher_class_of("Task")` would land that promo on a
# silent-fail-open path.
_BUILTIN_TOOLS = frozenset({
    "Bash", "Read", "Edit", "Write", "Glob", "Grep",
    "NotebookEdit", "TodoWrite", "WebFetch", "WebSearch",
    # D70 additions — CC 2.1.170 binary tool registry catch-up.
    "Task", "MultiEdit", "BashOutput", "KillBash",
    "NotebookRead", "ExitPlanMode", "AskUser",
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
#   - `ContextInjectionPolicy` was originally wired to the full hook
#     surface, then narrowed in D59 to exclude four hooks whose
#     hookSpecificOutput shape carries a SPECIALIZED channel that
#     ignores `additionalContext` at runtime (Elicitation /
#     ElicitationResult / WorktreeCreate / MessageDisplay; see
#     `_CONTEXT_INJECTION_EXCLUDED_EVENTS` and
#     `_CONTEXT_EVENT_LITERALS` in ir.py). The 26 remaining events
#     are the full `_SUPPORTED_EVENTS` minus those four. EvidencePolicy
#     (audit-only) still works on all 30 because audit just records
#     the trigger firing — it does not need additionalContext at all,
#     so the matrix is asymmetric on purpose: EvidencePolicy = 30,
#     ContextInjectionPolicy = 26. The matrix-coherence gate added to
#     `ContextInjectionPolicy.validate()` is still in place (per-tool
#     matcher classes are illegal on no-tool-context events even when
#     the event name is recognized);
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
# The 30 names split into 5 families (gate-action availability;
# inject_context + run_command are layered on top per D63 + D69 and
# follow the additionalContext channel rule, not the gate-action rule):
#
#   tool-context events       — Pre/Post/Failure/Batch. Tool name in
#                               the payload, so tool / mcp_tool /
#                               tool_alt matchers apply on the pre/post
#                               pair; the failure + batch variants are
#                               audit-only for the gate actions but
#                               still accept inject_context + run_command
#                               (failure recovery scripts are common).
#   permission gate events    — PermissionRequest (pre) /
#                               PermissionDenied (post). The pre side
#                               accepts block/ask/audit because the
#                               PreToolUse "override permission"
#                               contract is the same channel; the post
#                               side is audit-only for gate actions.
#   content-flow events       — UserPromptSubmit / UserPromptExpansion
#                               / PreCompact / PostCompact /
#                               Elicitation / ElicitationResult. The
#                               *pre* sides accept gate actions; the
#                               *post* sides audit-only for gate actions.
#   subagent / stop boundary  — SubagentStart / SubagentStop / Stop /
#                               StopFailure. Audit-only for gate actions
#                               — by the time these fire the runtime
#                               cannot rewind. SubagentStart still
#                               accepts inject_context + run_command
#                               (carry mandate over to the child).
#   lifecycle / observability — Setup / Notification / SessionStart /
#                               SessionEnd / TeammateIdle / TaskCreated
#                               / TaskCompleted / ConfigChange /
#                               WorktreeCreate / WorktreeRemove /
#                               InstructionsLoaded / CwdChanged /
#                               FileChanged / MessageDisplay. Boundary
#                               markers; audit-only for gate actions.
#                               All except WorktreeCreate + MessageDisplay
#                               also accept inject_context (the four
#                               D59-excluded events route additional
#                               output through a specialized
#                               hookSpecificOutput field instead).
#
# Action availability rule remains the same: pre-event hooks (PreTool,
# PermissionRequest, UserPromptSubmit, UserPromptExpansion, PreCompact,
# Elicitation) can block/ask/audit; post-event + observability hooks
# audit-only for the gate-style actions. Every event can audit.
#
# D69 — matrix re-audit:
#   The action vocabulary on `LEGAL_COMBINATIONS` is widened to surface
#   `inject_context` and `run_command` on the observational hooks too.
#   Today's `_AUDIT_ONLY_WILDCARD_EVENTS` list was over-narrow: the
#   D58 re-audit treated TaskCreated / TaskCompleted / SubagentStart /
#   PostToolUseFailure / Notification / Setup / TeammateIdle /
#   ConfigChange / InstructionsLoaded / CwdChanged / FileChanged as
#   pure audit slots. The CC hook stdout JSON (decision /
#   updatedInput / additionalContext / continue / hookSpecificOutput)
#   is uniform across every hook — so an operator authoring "fire a
#   recovery script after a failed tool" (run_command on
#   PostToolUseFailure) or "carry an audit summary back into the next
#   turn" (inject_context on TaskCompleted) is a legal hook stdout
#   pattern. run_command is uniformly legal on all 30 hooks (D63).
#
# D69 widening accounting (replaces the prior conservative "9 widened"
# commit subject — the actual blast radius is larger):
#   - inject_context is registered on 22 events
#     (`_SUPPORTED_EVENTS - _CONTEXT_INJECTION_EXCLUDED_EVENTS`). The
#     excluded set was 4 in the original D69 cut (Elicitation /
#     ElicitationResult / WorktreeCreate / MessageDisplay — specialized
#     hookSpecificOutput shapes); D70 extends it by 4 more end-of-life
#     events (Stop / StopFailure / SessionEnd / SubagentStop) because
#     CC silently drops `additionalContext` at end-of-execution / session
#     teardown / child return — there is no downstream model turn to
#     inject into within the same session.
#   - Of the 22 inject_context-legal events, the four tool-context
#     events (PreToolUse / PostToolUse / PostToolUseFailure /
#     PostToolBatch) also accept per-tool / mcp_tool / tool_alt
#     matchers; the other 18 are wildcard-only.
#   - 18 of the 22 widened events were previously audit-only-wildcard
#     (the legacy `_AUDIT_ONLY_WILDCARD_EVENTS` list minus the four
#     D70 end-of-life additions); 4 were already on richer-matcher
#     surfaces. D58's pin-the-audit goal stands: the widened set is
#     enumerated below as the difference of two named frozensets so a
#     future re-narrow has to drop a name explicitly.
#
# D70 — _CONTEXT_INJECTION_EXCLUDED_EVENTS extended for end-of-life:
#   D69 narrowed only the four specialized-channel events. Stop /
#   StopFailure / SessionEnd / SubagentStop still routed
#   inject_context through the matrix gate, which created a silent-
#   fail-open: CC's stdout JSON DOES carry the `additionalContext`
#   field uniformly, but at these four end-of-life events there is no
#   downstream model turn to inject into within the same session
#   (Stop fires at end-of-execution; SessionEnd at session teardown;
#   SubagentStop after the child returned; StopFailure mirrors Stop's
#   timing). CC silently drops the additional context, the operator
#   sees a green check, and zero enforcement fires — exactly the
#   silent-fail-open this module docstring warned about. D70 promotes
#   these four to the excluded set so the matrix and
#   `ContextInjectionPolicy.validate()` refuse them at authoring time.
#   To promote any of these back to legal, a binary fixture must
#   confirm CC actually surfaces SessionEnd / Stop additionalContext
#   to a downstream message inside the same session.
#
# D69 matrix-internal-coherence fix:
#   The original D69 loop widened PostToolUseFailure / PostToolBatch +
#   per-tool matcher + run_command / inject_context to legal while the
#   matching audit triple stayed wildcard-only. The wizard now refused
#   "audit Edit-only failures" but accepted "inject context on Edit-only
#   failures", which was hard to reason about. D70 brings the audit side
#   into lockstep: PostToolUseFailure / PostToolBatch now accept the
#   full tool-context matcher set on audit too, mirroring run_command /
#   inject_context. _AUDIT_ONLY_WILDCARD_EVENTS retains only the events
#   whose payload genuinely has no tool name to filter on.
#
#   block + ask stay narrow: ask requires a routable interactive
#   surface (PreToolUse, UserPromptSubmit, PermissionRequest,
#   Elicitation) and is intentionally NOT registered on PostToolUse*
#   events — by the time the tool ran, an "ask a human" interrupt
#   leaves no surface the runtime can usefully route to.
#
# D82d — PostToolUse / PostToolUseFailure / PostToolBatch admit
#   block as a 4th gate action. CC's hook stdout JSON contract on
#   these three events accepts `{"decision": "block", "reason": "…"}`
#   and surfaces the reason as a retry-feedback message back to the
#   model. This is a real action surface today's matrix narrowly
#   refused; the prior "PostToolUse cannot block — the tool already
#   ran" wording conflated "cannot retract the call" (true) with
#   "cannot signal the model" (false).
#
#   D82d follow-up (runtime contract): the IR triple is wired to the
#   runtime emitter in `src/magi_cp/local/gate.py::_deny` /
#   `_deny_dict`, which dispatches on `hook_event_name` to emit the
#   CC-canonical retry-feedback shape on the three PostToolUse* events
#   and the historical PreToolUse `hookSpecificOutput.permissionDecision`
#   shape everywhere else. The list of events that get the top-level
#   `decision`+`reason` shape lives at
#   `gate._RETRY_FEEDBACK_EVENTS` so a future widening lands in one
#   place. PostToolUseFailure / PostToolBatch are still listed in
#   `_UNVERIFIED_EVENTS` below — no CC binary fixture has captured
#   either event's stdout-channel contract end-to-end. The matrix
#   admits the triples as the working hypothesis; flipping them to
#   verified MUST follow the same fixture protocol the other
#   `_UNVERIFIED_EVENTS` entries are blocked on.
#
#   The matcher set follows the shape of each event's payload:
#     PostToolUse        → per-tool (tool / mcp_tool / tool_alt) — the
#                          gate decision is scoped to one named tool.
#     PostToolUseFailure → per-tool (tool / mcp_tool) — failure
#                          recovery scripts target a specific tool.
#                          tool_alt stays excluded because batching
#                          the retry across multiple tools is what
#                          PostToolBatch is for.
#     PostToolBatch      → wildcard only — the batch event covers the
#                          whole turn's tool calls, no single named
#                          tool to scope to. block here asks the model
#                          to redo the whole batch with the reason.
#   block stays illegal on Stop / SessionEnd / SubagentStop /
#   TaskCompleted — by the time those fire there is no downstream
#   session turn for the retry-feedback message to land in.
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
#
# D70 — PostToolUseFailure and PostToolBatch are removed from this list
# and routed through `_AUDIT_TOOL_CONTEXT_EVENTS` below so the audit
# triple stays in lockstep with the run_command / inject_context triples
# that D63 + D69 already widened to per-tool matchers. Without this,
# "audit Edit-only failures" was refused while "inject context on
# Edit-only failures" was accepted — same event, sibling actions,
# opposite verdicts at the matrix gate.
_AUDIT_ONLY_WILDCARD_EVENTS = (
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

# D70 — tool-context observability events whose payload carries a tool
# name. The audit archetype now accepts the same matcher set the
# run_command + inject_context archetypes do, so the matrix triples
# stay in lockstep across actions. Mirrors `_TOOL_CONTEXT_EVENTS_RC`
# inside `_build_legal()`.
_AUDIT_TOOL_CONTEXT_EVENTS = (
    "PostToolUseFailure",
    "PostToolBatch",
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

    # PostToolUse — tool already ran. audit is legal on every matcher
    # class; D82d also admits block on per-tool matchers so operators
    # can author "tell the model the result is unusable and let it
    # retry with this reason" via CC's PostToolUse decision channel
    # (stdout JSON `{"decision":"block","reason":"…"}` surfaces the
    # reason to the model as a retry-feedback message). Wildcard +
    # block is intentionally left off: a "block every PostToolUse"
    # rule would force a retry on every tool call in the session,
    # which is rarely the operator's intent.
    out.add(("PostToolUse", MatcherClass.tool, "audit"))
    out.add(("PostToolUse", MatcherClass.mcp_tool, "audit"))
    out.add(("PostToolUse", MatcherClass.tool_alt, "audit"))
    out.add(("PostToolUse", MatcherClass.wildcard, "audit"))
    for kls in (
        MatcherClass.tool, MatcherClass.mcp_tool, MatcherClass.tool_alt,
    ):
        out.add(("PostToolUse", kls, "block"))

    # D82d — PostToolUseFailure / PostToolBatch admit block for the
    # same retry-feedback channel. The matcher set differs per event:
    #   PostToolUseFailure → per-tool (tool / mcp_tool). The failure
    #     surfaces a specific tool name; tool_alt stays excluded
    #     because authoring "retry on failure of any of A | B | C"
    #     is what PostToolBatch is for.
    #   PostToolBatch → wildcard only. The event covers the whole
    #     turn's tool calls; there is no single named tool to scope
    #     to. block here asks the model to redo the whole batch with
    #     the supplied reason.
    for kls in (MatcherClass.tool, MatcherClass.mcp_tool):
        out.add(("PostToolUseFailure", kls, "block"))
    out.add(("PostToolBatch", MatcherClass.wildcard, "block"))

    # D70 — Tool-context observability events keep audit in lockstep
    # with run_command + inject_context. PostToolUseFailure / PostToolBatch
    # payloads carry a tool name; the prior matrix accepted per-tool
    # matchers on the newer actions but refused them on audit, which
    # was hard to reason about for operators authoring "audit Edit-only
    # failures".
    for ev in _AUDIT_TOOL_CONTEXT_EVENTS:
        for kls in (
            MatcherClass.tool,
            MatcherClass.mcp_tool,
            MatcherClass.tool_alt,
            MatcherClass.wildcard,
        ):
            out.add((ev, kls, "audit"))

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

    # D63 — run_command. The CC hook stdout JSON contract
    # (`hookSpecificOutput`) is uniform across all 30 events; an
    # operator can author a run_command policy on any hook. The
    # matcher class follows the same rule the existing archetypes
    # use: the four tool-context events accept per-tool / mcp_tool /
    # tool_alt / wildcard matchers; every other event is wildcard
    # only (the payload has no tool name to filter on).
    _TOOL_CONTEXT_EVENTS_RC = frozenset({
        "PreToolUse", "PostToolUse",
        "PostToolUseFailure", "PostToolBatch",
    })
    # Use _SUPPORTED_EVENTS as the canonical list of 30. Import lazily
    # at call time to avoid an import cycle with ir.py.
    from .ir import _SUPPORTED_EVENTS, _CONTEXT_INJECTION_EXCLUDED_EVENTS
    for ev in _SUPPORTED_EVENTS:
        if ev in _TOOL_CONTEXT_EVENTS_RC:
            for kls in (
                MatcherClass.tool,
                MatcherClass.mcp_tool,
                MatcherClass.tool_alt,
                MatcherClass.wildcard,
            ):
                out.add((ev, kls, "run_command"))
        else:
            out.add((ev, MatcherClass.wildcard, "run_command"))

    # D69 — inject_context. The CC hook stdout JSON contract accepts
    # `additionalContext` on every hook EXCEPT four whose
    # hookSpecificOutput shape is specialized (Elicitation /
    # ElicitationResult / WorktreeCreate / MessageDisplay). The list is
    # owned by ir.py::_CONTEXT_INJECTION_EXCLUDED_EVENTS so the matrix
    # and the ContextInjectionPolicy gate stay in lockstep. Today's
    # `_AUDIT_ONLY_WILDCARD_EVENTS` only registered (event, wildcard,
    # audit) for observational hooks; D69 adds inject_context on the 26
    # non-excluded events. Tool-context events still also accept per-tool
    # / mcp_tool / tool_alt matchers so a hook authored on PreToolUse +
    # Bash + inject_context survives the matrix gate (ContextInjection
    # already accepts those classes on the tool-context family).
    for ev in _SUPPORTED_EVENTS:
        if ev in _CONTEXT_INJECTION_EXCLUDED_EVENTS:
            continue
        if ev in _TOOL_CONTEXT_EVENTS_RC:
            for kls in (
                MatcherClass.tool,
                MatcherClass.mcp_tool,
                MatcherClass.tool_alt,
                MatcherClass.wildcard,
            ):
                out.add((ev, kls, "inject_context"))
        else:
            out.add((ev, MatcherClass.wildcard, "inject_context"))

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
