"""Policy IR — declarative spec of *what* the gate enforces.

Compiler in `compiler.py` turns IR → CC managed-settings.json. LLM never sees
runtime. Authoring tools (NL assist / pack picker / structured builder) only
*produce* IR with human review.
"""
from __future__ import annotations
import json
import os
import re
from dataclasses import dataclass, field
from typing import Literal


_POLICY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,127}$")
_RESERVED_SUFFIXES = ("/compiled", "/enabled")


def _validate_id(s: str) -> None:
    """Cloud-canonical policy id check. Mirrors web/lib/policy-id.ts but is
    the source of truth — the dashboard's check is a UX nicety."""
    if not isinstance(s, str) or not s:
        raise ValueError(f"policy id required (got {s!r})")
    if not _POLICY_ID_RE.match(s):
        raise ValueError(f"policy id must match [A-Za-z0-9][A-Za-z0-9._\\-/]{{0,127}}; got {s!r}")
    if ".." in s:
        raise ValueError(f"policy id must not contain '..': {s!r}")
    for suf in _RESERVED_SUFFIXES:
        if s.endswith(suf):
            raise ValueError(f"policy id must not end with {suf!r}: {s!r}")


# D58 — full CC hook surface (30 events, CC 2.1.170 binary `nV` enum).
# See matrix.py for the family-by-family breakdown. The legal action set
# per event still lives in matrix.LEGAL_COMBINATIONS — this list ONLY
# decides whether the runtime *recognizes* the event name. The doc
# (docs/architecture/claude-code-cli/08-coding-harness-internals.md:233)
# names "23 hook events"; that copy was authored for an earlier 2.1.x
# build and lags the live binary. Truth source is the binary, not the
# doc.
EventLiteral = Literal[
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
]
_SUPPORTED_EVENTS: frozenset[str] = frozenset({
    "PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch",
    "PermissionRequest", "PermissionDenied",
    "UserPromptSubmit", "UserPromptExpansion",
    "PreCompact", "PostCompact",
    "Elicitation", "ElicitationResult",
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
})


@dataclass
class Trigger:
    host: Literal["claude-code"] = "claude-code"
    event: EventLiteral = "PreToolUse"
    matcher: str = "Bash"


# D35: EvidenceReq becomes a discriminated union. v0 was step-ref only;
# now policies can carry inline conditions of four kinds:
#
#   step        — reference a wired verifier by name (default; original).
#   regex       — Python regex; matched against the payload text. Cheap,
#                 evaluated at gate time without an LLM round-trip.
#   llm_critic  — free-text rule, judged by the configured LLM provider
#                 ("does this output satisfy: <criterion>"). Requires
#                 MAGI_CP_LLM_COMPILER / REVIEWER to be configured.
#   shacl       — Turtle SHACL shape; validated against the payload dict
#                 with pyshacl. Catches structural violations that regex
#                 can't express.
#
# All four shapes share the empty-list = "emit signal" semantics from D31.
EvidenceKindLiteral = Literal["step", "regex", "llm_critic", "shacl"]


# D82c fix: shape of a valid `field_path` on a regex EvidenceReq. Same
# grammar as the marker regex but anchored, so `"tool_response.output"`
# / `"tool_input.command"` / `"prompt"` all pass and `"foo.."` / `""` /
# `"a b"` do not. Empty string is allowed as a legitimate "whole-payload"
# back-compat signal — handled by the regex evaluator below.
_FIELD_PATH_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$"
)


@dataclass
class EvidenceReq:
    """One condition that must hold for the policy gate to allow.

    Discriminated by `kind`. Unknown / empty kind defaults to "step" so
    legacy `{step, verdict}` rows keep round-tripping through the loader
    without churn.
    """
    kind: EvidenceKindLiteral = "step"
    # kind=step — verifier reference
    step: str = ""
    verdict: str = "pass"
    # kind=regex — inline regex
    pattern: str = ""
    # D82c fix: kind=regex — optional dotted path scoping the match.
    # Empty (default) preserves the pre-D82c behaviour of matching the
    # whole-payload projection; a non-empty value scopes `re.search` to
    # the resolved field only (`tool_response.output`, `tool_input.command`,
    # `prompt`, …). Catches the overmatch hole the wizard's picker UI
    # promised but the runtime never delivered.
    field_path: str = ""
    # kind=llm_critic — natural-language rule
    criterion: str = ""
    # kind=shacl — Turtle SHACL shape
    shape_ttl: str = ""

    def validate(self) -> None:
        if self.kind == "step":
            if not self.step:
                raise ValueError("EvidenceReq kind=step requires non-empty `step`")
        elif self.kind == "regex":
            if not self.pattern:
                raise ValueError("EvidenceReq kind=regex requires non-empty `pattern`")
            if len(self.pattern) > 2000:
                raise ValueError("EvidenceReq kind=regex pattern too long (>2000 chars)")
            try:
                re.compile(self.pattern)
            except re.error as e:
                raise ValueError(f"EvidenceReq kind=regex pattern fails to compile: {e}") from e
            # D82c fix: scope check. Empty is fine (legacy behaviour);
            # any non-empty value MUST be a dotted-identifier chain so
            # the runtime's `_resolve_dotted_path` can walk it without
            # surprises. We reject obvious garbage at validate-time
            # rather than letting it silently degrade to whole-payload
            # match at eval-time.
            if self.field_path:
                if len(self.field_path) > 256:
                    raise ValueError(
                        "EvidenceReq kind=regex field_path too long (>256 chars)"
                    )
                if not _FIELD_PATH_RE.match(self.field_path):
                    raise ValueError(
                        f"EvidenceReq kind=regex field_path must be a "
                        f"dotted-identifier chain (e.g. 'tool_response.output'); "
                        f"got {self.field_path!r}"
                    )
        elif self.kind == "llm_critic":
            if not self.criterion:
                raise ValueError("EvidenceReq kind=llm_critic requires non-empty `criterion`")
            if len(self.criterion) > 4000:
                raise ValueError("EvidenceReq kind=llm_critic criterion too long (>4000 chars)")
        elif self.kind == "shacl":
            if not self.shape_ttl:
                raise ValueError("EvidenceReq kind=shacl requires non-empty `shape_ttl`")
            if len(self.shape_ttl) > 16000:
                raise ValueError("EvidenceReq kind=shacl shape_ttl too long (>16000 chars)")
        else:
            raise ValueError(f"EvidenceReq unsupported kind: {self.kind!r}")


# D31: action archetypes. Replaces the prior `on_missing` field which
# conflated "what happens when the verifier fails" with "what the policy
# is fundamentally trying to do." Action is now the primary intent.
#
#   block — when the verifier doesn't all-pass, prevent the host action
#           (tool runs / prompt sends / compaction starts). The strongest
#           pre-event gate.
#   ask   — when the verifier doesn't all-pass, interrupt for human
#           approval (HITL). Used for legal-significant filings, etc.
#   audit — record the verdict to the evidence ledger; never blocks.
#           Combined with `requires=[]` this expresses the "emit signal"
#           archetype (unconditional ledger marker every time the trigger
#           fires).
#
# Reserved for a follow-up cycle (requires verifier-protocol mutation
# support before it can be wired through the runtime gate):
#   strip — intercept tool output and redact / transform it before the
#           agent sees it. PostToolUse-only.
ActionLiteral = Literal["block", "ask", "audit"]


# Legacy → archetype migration. Older JSON fixtures + persisted policies
# still carry the on_missing wording; deserialization accepts the key
# and folds it into `action` so we don't strand existing rows. The
# allow/log distinction collapses to `audit` — at runtime both meant
# "verifier ran, log the verdict, don't gate," so they were
# operationally interchangeable.
_LEGACY_ON_MISSING_TO_ACTION = {
    "deny":  "block",
    "ask":   "ask",
    "log":   "audit",
    "allow": "audit",
}


@dataclass
class EvidencePolicy:
    """Gate-binary policy: a runtime hook fires `gate_binary` against the
    payload and the policy passes/fails based on `requires[]` outcomes.

    This is the original `Policy` shape — kept under its functional name
    (P2/P3 hybrid compilation introduces siblings that compile to *native*
    CC managed-settings surfaces instead of the gate-binary route).
    The `Policy = EvidencePolicy` alias preserves every existing import.
    """
    id: str
    description: str
    trigger: Trigger
    # D43 (issue #1, P1): sentinel_re is now Optional. Pre-D43 policies
    # carried a sentinel pattern with named groups like
    # `(?P<matter>...)_(?P<doc_id>...)` so the legal-document workflow
    # could extract the case + document identifiers from the tool payload
    # at runtime. That's a vertical concern; a general-purpose "block
    # rm -rf" or "audit Bash" policy has no subject / payload binding to
    # extract. Policies without sentinel_re now load cleanly; the runtime
    # falls back to context-synthesized (subject, payload_hash) labels
    # via `_synth_subject_and_hash` in cloud/app.py. Legacy policies
    # WITH a sentinel_re still validate — any named groups in the regex
    # are fine; the runtime no longer reads specific group names.
    sentinel_re: str | None = None
    requires: list[EvidenceReq] = field(default_factory=list)
    action: ActionLiteral = "block"
    on_signature_invalid: Literal["deny"] = "deny"
    gate_binary: str = "/usr/local/bin/magi-gate.sh"
    version: str = "0.1"
    # P2/P3 hybrid compilation: discriminator. Default keeps existing
    # JSON byte-stable; the union (de)serializers only write the key
    # for the sibling types.
    type: Literal["evidence"] = "evidence"

    def __post_init__(self) -> None:
        # Fail-fast on construction so REST inputs / on-disk policies can't
        # quietly carry illegal IR past the surface that accepts them.
        self.validate()

    def validate(self) -> None:
        # v1: id format must match the same shape the JS dashboard enforces.
        # The cloud is the *canonical* boundary — a direct admin-key holder
        # bypasses the JS layer, so this check is the real gate.
        _validate_id(self.id)
        # sentinel_re, if present, must compile. Named groups are no
        # longer prescribed; the runtime extracts whatever groups exist
        # and synthesizes the rest from request context.
        if self.sentinel_re is not None:
            try:
                re.compile(self.sentinel_re)
            except re.error as e:
                raise ValueError(
                    f"policy '{self.id}': sentinel_re is not a valid regex: {e}"
                ) from e
        if self.trigger.event not in _SUPPORTED_EVENTS:
            raise ValueError(f"policy '{self.id}': trigger.event 미지원: {self.trigger.event}")
        # D31: requires CAN be empty — that's the unconditional ("emit
        # signal") archetype. The matrix decides whether the combination
        # makes sense for the chosen action; this validator just gates
        # the shape.
        if self.action not in ("block", "ask", "audit"):
            raise ValueError(f"policy '{self.id}': action 미지원: {self.action}")
        # D35: each requires entry must individually validate by kind.
        for i, req in enumerate(self.requires):
            try:
                req.validate()
            except ValueError as e:
                raise ValueError(f"policy '{self.id}': requires[{i}] {e}") from e
        # P7 (issue #1): SHACL shapes are linted against the payload
        # schema for this trigger so a shape anchored on a path the
        # runtime never delivers can't slip past authoring. Default
        # mode is collect-only (warnings surface via shacl_lint_issues
        # so the dashboard can render a banner); set
        # `MAGI_CP_STRICT_SHACL_TARGETS=1` in the env to hard-fail
        # `Policy.__post_init__` instead.
        self._shacl_lint_issues: list[str] = []
        strict = os.environ.get("MAGI_CP_STRICT_SHACL_TARGETS") == "1"
        for i, req in enumerate(self.requires):
            if req.kind != "shacl" or not req.shape_ttl:
                continue
            from .payload_schemas import lint_shacl_targets
            issues = lint_shacl_targets(
                req.shape_ttl, self.trigger.event, self.trigger.matcher,
            )
            for msg in issues:
                tagged = f"requires[{i}] SHACL lint: {msg}"
                self._shacl_lint_issues.append(tagged)
                if strict:
                    raise ValueError(
                        f"policy '{self.id}': {tagged} "
                        f"(MAGI_CP_STRICT_SHACL_TARGETS=1)"
                    )
        if self.on_signature_invalid != "deny":
            raise ValueError(
                f"policy '{self.id}': on_signature_invalid는 'deny'만 허용 (v0)"
            )
        from .matrix import validate_combination
        try:
            validate_combination(self.trigger.event, self.trigger.matcher,
                                  self.action)
        except ValueError as e:
            raise ValueError(f"policy '{self.id}': {e}") from e


# P2/P3 — back-compat alias. Every existing import / call site uses
# `Policy`; the alias points at the original gate-binary shape so no
# call site needs touching. The new sibling types are unrelated dataclasses
# (PermissionPolicy / SubagentPolicy / McpGatingPolicy / ContextInjectionPolicy)
# and the union `AnyPolicy` is what the compiler iterates over.
Policy = EvidencePolicy


# ── P2/P3 native-surface policy archetypes ──────────────────────────
#
# These compile into CC managed-settings WITHOUT a runtime gate-binary
# hop. The cost is expressiveness (a declarative permission can't run
# an LLM), the win is the gate-binary path stops being load-bearing for
# coarse policies like "deny `rm -rf /`" or "scope subagent X to Read+Grep".


_PERMISSION_LITERALS = ("allow", "deny", "ask")
_MCP_ACTION_LITERALS = ("allow", "deny")
# D57f-1 — context_injection lives on the CC hookSpecificOutput JSON
# schema's `additionalContext` channel (per the bundled CC docs
# referenced in docs/architecture/claude-code-cli/08-coding-harness-
# internals.md:233 — "JSON stdout returns {decision, updatedInput,
# additionalContext, continue}"). For most events that wire-shape is
# the right one: authoring an injection on PreToolUse turns into a
# per-tool note prepended to the model's view of the tool input; on
# SubagentStart it documents the spawned child's mandate; on
# Notification it tags the runtime's notification record.
#
# D59 — four hooks are SPECIALIZED. Their hookSpecificOutput shape
# carries a different channel and `additionalContext` is silently
# ignored at runtime ("Hook JSON output had unrecognized keys
# (ignored)" in the CC binary). Authoring a ContextInjectionPolicy on
# any of these would compile and persist cleanly, then no-op at
# runtime with no operator-visible feedback — exactly the silent
# fail-open the matrix gate exists to prevent. We narrow the
# authoring surface:
#
#   Elicitation        — uses hookSpecificOutput.elicitationDecision
#                        (accept / decline an MCP elicitation request).
#   ElicitationResult  — uses hookSpecificOutput to override the action
#                        or content BEFORE the response is sent to the
#                        MCP server. Not an injection target.
#   WorktreeCreate     — uses hookSpecificOutput.worktreePath (the gate
#                        returns the path of the worktree the runtime
#                        should use).
#   MessageDisplay     — display-only. CC replaces the on-screen delta
#                        without changing the stored message or feeding
#                        anything back into the model context.
#
# D70 — four MORE hooks are excluded for a separate reason: there is
# no downstream model turn within the same session for the
# `additionalContext` to land in. CC's stdout JSON does carry the
# `additionalContext` field uniformly, but the runtime can only feed
# additional context into a future model turn that has not yet
# materialized — at these four end-of-life events the next turn lives
# in a different session (Stop = end of execution; SessionEnd =
# session teardown; SubagentStop = the child has returned; StopFailure
# mirrors Stop's timing). CC silently drops the additional context;
# the operator sees a green check and nothing fires. To re-admit any
# of these, a binary fixture must confirm CC actually surfaces
# `additionalContext` from one of them to a downstream message within
# the same session.
#
#   Stop          — fires at end-of-execution; no downstream model turn
#                   inside the same session for additionalContext.
#   StopFailure   — mirrors Stop's timing.
#   SessionEnd    — fires at session teardown.
#   SubagentStop  — fires after the child has returned; the spawning
#                   parent's next turn is not bound to the child's
#                   additionalContext output (use SubagentStart for
#                   the parent-side carry-over).
#
# EvidencePolicy (audit-only) is NOT narrowed on these eight — see
# matrix.LEGAL_COMBINATIONS — because audit only records the trigger
# firing; it does not need `additionalContext` at all.
#
# The wizard authoring surface mirrors this set — see
# web/app/(console)/policies/new/page.tsx Step 4 "Inject extra
# context" action card; the picker is greyed out for these eight
# lifecycles with a tooltip naming the alternate output channel /
# reason the channel does not apply.
_CONTEXT_INJECTION_EXCLUDED_EVENTS: frozenset[str] = frozenset({
    # D59 — specialized hookSpecificOutput shape
    "Elicitation", "ElicitationResult",
    "WorktreeCreate", "MessageDisplay",
    # D70 — end-of-life events with no downstream same-session model turn
    "Stop", "StopFailure", "SessionEnd", "SubagentStop",
})
_CONTEXT_EVENT_LITERALS: tuple[str, ...] = tuple(sorted(
    _SUPPORTED_EVENTS - _CONTEXT_INJECTION_EXCLUDED_EVENTS,
))
# D59 follow-up (#6 type safety): mirror `_CONTEXT_EVENT_LITERALS` as a
# typing.Literal so a direct dataclass call site
# (`ContextInjectionPolicy(event="Elicitation", ...)`) gets a lint-time
# refusal that matches the runtime gate below. The JSON deserialization
# path stays funneled through `policy_from_dict` which calls the
# runtime gate in `validate()`, so the asymmetric authoring path
# (dict vs dataclass) keeps the runtime as the canonical truth.
ContextEventLiteral = Literal[
    "ConfigChange",
    "CwdChanged",
    "FileChanged",
    "InstructionsLoaded",
    "Notification",
    "PermissionDenied",
    "PermissionRequest",
    "PostCompact",
    "PostToolBatch",
    "PostToolUse",
    "PostToolUseFailure",
    "PreCompact",
    "PreToolUse",
    "SessionStart",
    "Setup",
    "SubagentStart",
    "TaskCompleted",
    "TaskCreated",
    "TeammateIdle",
    "UserPromptExpansion",
    "UserPromptSubmit",
    "WorktreeRemove",
]
# Per-event alternate channel description used in the ValueError when
# an operator tries to author a ContextInjectionPolicy on an excluded
# event. The description names the actual hookSpecificOutput field
# that hook uses so the error tells the operator where to look next.
#
# D59 follow-up (#8, #9, code-style): every entry stays a noun phrase so
# the splice into the `this hook uses {channel}, not additionalContext`
# template reads grammatically. The MessageDisplay entry is rephrased to
# "no model-context channel" since there is literally no `hookSpecificOutput`
# field that feeds the model view; the operator's options are EvidencePolicy
# audit or a different hook event. No em-dashes per CLAUDE.md hard rule.
_CONTEXT_INJECTION_ALTERNATE_CHANNEL: dict[str, str] = {
    # D59 — specialized hookSpecificOutput shape.
    "Elicitation": (
        "hookSpecificOutput.elicitationDecision (accept / decline an "
        "MCP elicitation request)"
    ),
    "ElicitationResult": (
        "hookSpecificOutput action / content override (applied before "
        "the response is sent to the MCP server)"
    ),
    "WorktreeCreate": (
        "hookSpecificOutput.worktreePath (the gate returns a worktree "
        "path)"
    ),
    "MessageDisplay": (
        "no model-context channel (this hook is display-only; CC "
        "replaces the on-screen delta without changing the stored "
        "message)"
    ),
    # D70 — end-of-life events. The channel exists in the JSON but
    # there is no downstream same-session model turn for CC to inject
    # the context into, so the additionalContext field is silently
    # dropped. Noun-phrase form keeps the spliced sentence ("this hook
    # uses {channel}, not additionalContext") grammatical.
    "Stop": (
        "no downstream same-session model turn (this hook fires at "
        "end-of-execution; CC silently drops additionalContext because "
        "there is no future turn to inject into)"
    ),
    "StopFailure": (
        "no downstream same-session model turn (this hook mirrors "
        "Stop's end-of-execution timing; CC silently drops "
        "additionalContext for the same reason)"
    ),
    "SessionEnd": (
        "no downstream same-session model turn (this hook fires at "
        "session teardown; CC silently drops additionalContext because "
        "the session is closing)"
    ),
    "SubagentStop": (
        "no downstream same-session model turn (this hook fires after "
        "the child has returned; for parent-side carry-over, author "
        "the injection on SubagentStart instead)"
    ),
}
_SUBAGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$")
_MCP_SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$")


# Whitelist of CC permission rule prefixes per the public managed-settings
# permission grammar. Issue #1 P1: a malformed `pattern` would otherwise
# silently land in managed-settings and either be ignored (silent
# fail-open: "deny" rules don't deny anything) or cause CC to reject the
# whole file (revert to default-permissive). Anchoring on a strict
# `Verb(...)` shape catches both cases at authoring time.
_PERMISSION_TOOL_NAMES = (
    # Built-in tools
    "Bash", "Read", "Write", "Edit", "WebFetch", "WebSearch",
    "Glob", "Grep", "Task", "TodoWrite", "NotebookEdit",
    # Subagent gating (also expressible via SubagentPolicy)
    "Agent",
    # MCP tool prefix — matches `mcp__<server>(...)` and
    # `mcp__<server>__<tool>(...)`.
    "mcp",
)
_PERMISSION_PATTERN_RE = re.compile(
    r"^(?:"
    + r"Agent"  # bare `Agent` (disable subagents fleet-wide)
    + r"|(?P<tool>"
    + "|".join(re.escape(t) for t in _PERMISSION_TOOL_NAMES)
    + r")"
    + r"(?:__[A-Za-z0-9_\-]{1,64}){0,2}"  # mcp__server or mcp__server__tool
    + r"(?:\([^)\n]{0,2000}\))?"
    + r")$"
)


def _validate_permission_pattern(policy_id: str, pattern: str) -> None:
    """Issue #1 P1 — anchor every PermissionPolicy.pattern on the official
    CC permission grammar. The check intentionally allows long argument
    bodies (some MCP tool names get verbose) but refuses anything that
    doesn't open with a known verb. Raises ValueError on mismatch.
    """
    if not _PERMISSION_PATTERN_RE.match(pattern):
        raise ValueError(
            f"PermissionPolicy '{policy_id}': pattern {pattern!r} does not "
            f"match CC permission grammar (expected `<Tool>(<args>)` or "
            f"`mcp__server(__tool)?(<args>)`; tools: "
            f"{', '.join(_PERMISSION_TOOL_NAMES)})"
        )


@dataclass
class PermissionPolicy:
    """Declarative CC permission rule. Compiles to
    managed-settings `permissions.{allow,deny,ask}` — no gate-binary hop.

    `pattern` is the raw CC permission string (e.g. `Bash(rm -rf /*)`,
    `Read(/etc/**)`, `WebFetch(https://api.example.com/*)`). Issue #1 P1:
    the value is anchored against the CC permission grammar so a
    malformed entry can't silently land in managed-settings.
    """
    id: str
    description: str
    trigger: Trigger
    permission: Literal["allow", "deny", "ask"]
    pattern: str
    version: str = "0.1"
    type: Literal["permission"] = "permission"
    # Issue #1 P1 / fix-cycle non-blocking #b: when True (default), pair
    # this archetype's compile output with
    # `allowManagedPermissionRulesOnly` so a user-level `permissions.allow`
    # can't loosen the floor. Off opt-out for tenants who explicitly
    # *want* their managed `ask` to be overridable by users.
    exclusive: bool = True

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        _validate_id(self.id)
        if self.permission not in _PERMISSION_LITERALS:
            raise ValueError(
                f"PermissionPolicy '{self.id}': permission must be one of "
                f"{_PERMISSION_LITERALS}; got {self.permission!r}"
            )
        if not isinstance(self.pattern, str) or not self.pattern.strip():
            raise ValueError(
                f"PermissionPolicy '{self.id}': pattern required (non-empty)"
            )
        if len(self.pattern) > 4000:
            raise ValueError(
                f"PermissionPolicy '{self.id}': pattern too long (>4000)"
            )
        _validate_permission_pattern(self.id, self.pattern)


@dataclass
class SubagentPolicy:
    """Disable a specific CC subagent via managed-settings.

    Issue #1 P0 (#9): the original design emitted a top-level
    `agents.<subagent_type> = {"tools": [...]}` key. That key does NOT
    exist in the public CC managed-settings schema — subagents are
    defined in Markdown files (`.claude/agents/<name>.md`), not in
    settings.json. Settings.json supports `permissions.deny: ["Agent(<name>)"]`
    to *disable* a subagent fleet-wide, but cannot scope its tools.
    v1 therefore narrows this archetype to a binary disable: a non-empty
    `tool_allowlist` is rejected on construction (we don't have a place
    to emit it). The Markdown-sidecar route is tracked as a follow-up
    (see compiler.py docstring).
    """
    id: str
    description: str
    subagent_type: str
    # Issue #1 P0 (#9): retained for API back-compat but MUST be empty in
    # v1 (the field has no valid compile target). A non-empty value
    # raises at validation; the operator must drop into the Markdown
    # sidecar workflow (out of v1 scope).
    tool_allowlist: list[str] = field(default_factory=list)
    version: str = "0.1"
    type: Literal["subagent"] = "subagent"

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        _validate_id(self.id)
        if not _SUBAGENT_NAME_RE.match(self.subagent_type or ""):
            raise ValueError(
                f"SubagentPolicy '{self.id}': subagent_type "
                f"{self.subagent_type!r} invalid; must match "
                f"[A-Za-z0-9][A-Za-z0-9._\\-]{{0,63}}"
            )
        if not isinstance(self.tool_allowlist, list):
            raise ValueError(
                f"SubagentPolicy '{self.id}': tool_allowlist must be a list"
            )
        # Issue #1 P0 (#9): managed-settings has no per-subagent tool
        # allowlist. We accept the field for API stability but reject a
        # non-empty value — the only honest compile target is the
        # binary `permissions.deny: ["Agent(<name>)"]`.
        if self.tool_allowlist:
            raise ValueError(
                f"SubagentPolicy '{self.id}': tool_allowlist is not "
                f"compilable to managed-settings in v1 (CC settings.json "
                f"has no per-subagent tool scope; the schema's "
                f"`permissions.deny: [\"Agent(<name>)\"]` route is "
                f"disable-only). Drop the allowlist or move the subagent "
                f"definition to `.claude/agents/<name>.md`."
            )


@dataclass
class McpGatingPolicy:
    """Allow/deny a whole MCP server at the managed-settings level.

    Issue #1 P0 (#10): the original design emitted a top-level
    `mcp.<server>.permissions = "allow"|"deny"` map. That key does NOT
    exist in the public CC managed-settings schema. MCP gating uses
    `allowedMcpServers` / `deniedMcpServers` — top-level arrays of
    `{"serverName": "<name>"}` entries.
    """
    id: str
    description: str
    server: str
    action: Literal["allow", "deny"]
    version: str = "0.1"
    type: Literal["mcp_gating"] = "mcp_gating"
    # Issue #1 P0 (#11): when True (default) and action="allow", pair
    # this archetype with `allowManagedMcpServersOnly` so a user can't
    # silently add untracked MCP servers. Disable to leave the floor
    # additive.
    exclusive: bool = True

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        _validate_id(self.id)
        if self.action not in _MCP_ACTION_LITERALS:
            raise ValueError(
                f"McpGatingPolicy '{self.id}': action must be allow|deny; "
                f"got {self.action!r}"
            )
        if not _MCP_SERVER_NAME_RE.match(self.server or ""):
            raise ValueError(
                f"McpGatingPolicy '{self.id}': server {self.server!r} "
                f"invalid; must match [A-Za-z0-9][A-Za-z0-9._\\-]{{0,63}}"
            )


@dataclass
class ContextInjectionPolicy:
    """Static text injected into a CC hook handler via
    `additionalContext`.

    Issue #1 P0 (#3, #8): the original design emitted
    `{"type": "write", "content": <template>}`. That hook type does NOT
    exist in the public CC managed-settings hook schema (valid types
    include `command`, `http`, `mcp_tool`, `prompt`, `agent`). Trying to
    emit it would either be rejected on settings load or silently
    ignored. v1 falls back to the `command` hook + an installed shim
    binary (`magi-cp-context-write`) that prints the template via
    `additionalContext` so CC actually reads it.

    The template is materialized to a sidecar file alongside the
    managed-settings JSON, keyed by sha256(template); the shim reads it
    by hash so the hook entry stays constant-time and the template
    bytes never need to fit in the settings file.

    D57f-1: event is the full CC hook surface. The hookSpecificOutput
    JSON schema accepts `additionalContext` on every hook event per
    the bundled CC docs (08-coding-harness-internals.md:233 — "JSON
    stdout returns {decision, updatedInput, additionalContext,
    continue}"). The previous narrowing to UserPromptSubmit /
    SessionStart was an artificial limit, and the wizard's
    "Inject extra context" action archetype now routes to this
    archetype on every lifecycle Step 1 surfaces.
    """
    id: str
    description: str
    # D58 / D59: type-checker sees the narrowed event surface
    # (`ContextEventLiteral` = full 26-event subset) so a
    # `ContextInjectionPolicy(event="Elicitation", ...)` call site
    # catches typos AND the four specialized-channel events at lint
    # time, matching the runtime gate below. The runtime check below
    # is still the ONLY guard against JSON-deserialized events that
    # bypass the Literal (mypy enforces the union at construction
    # time but `policy_from_dict` accepts any string). D57f-1
    # fix-followup: `_CONTEXT_EVENT_LITERALS` is the canonical
    # `_SUPPORTED_EVENTS` frozen-set sorted minus the four hooks whose
    # hookSpecificOutput shape is specialized; widening the candidate
    # set MUST also expand the matrix-coherence gate in `validate()`
    # (per-tool matcher classes are illegal on no-tool-context events
    # even when the event name is recognized).
    event: ContextEventLiteral
    template: str
    matcher: str = "*"
    version: str = "0.1"
    type: Literal["context_injection"] = "context_injection"

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        _validate_id(self.id)
        if self.event not in _CONTEXT_EVENT_LITERALS:
            # D59: the excluded set (Elicitation / ElicitationResult /
            # WorktreeCreate / MessageDisplay) has a SPECIALIZED
            # hookSpecificOutput shape — additionalContext is the wrong
            # channel and CC silently ignores it at runtime ("Hook JSON
            # output had unrecognized keys (ignored)"). Name the actual
            # channel that hook uses in the error so the operator can
            # pivot to the right archetype (EvidencePolicy audit, or
            # the alternate output channel) without a round-trip
            # through the docs.
            if self.event in _CONTEXT_INJECTION_EXCLUDED_EVENTS:
                channel = _CONTEXT_INJECTION_ALTERNATE_CHANNEL[self.event]
                # D59 follow-up (#2, #10, code-style): no em-dash. The
                # error reaches operators verbatim via the dashboard's
                # flash redirect path, Python tracebacks, and direct
                # REST PUT responses. D59 follow-up (#10): name BOTH
                # recovery paths the operator has. One is a different
                # archetype (EvidencePolicy audit). The other is a
                # different hook event (PreToolUse / SessionStart /
                # UserPromptSubmit are the canonical
                # additionalContext-bearing hooks per the CC binary).
                # The dashboard's disabled-card tooltip names only the
                # alternate archetype, so this string is the operator's
                # only signpost on the non-UI path.
                raise ValueError(
                    f"ContextInjectionPolicy '{self.id}': event "
                    f"{self.event!r} does not accept additionalContext "
                    f"injection. This hook uses {channel}, not "
                    f"additionalContext. EvidencePolicy (audit) is "
                    f"still legal on this event if you want to record "
                    f"the trigger firing; if you need additionalContext "
                    f"injection, switch to a hook event that supports "
                    f"it (e.g. PreToolUse, SessionStart, UserPromptSubmit)."
                )
            raise ValueError(
                f"ContextInjectionPolicy '{self.id}': event {self.event!r} "
                f"is not a recognized CC hook"
            )
        if not isinstance(self.template, str) or not self.template:
            raise ValueError(
                f"ContextInjectionPolicy '{self.id}': template required"
            )
        if len(self.template) > 16_000:
            raise ValueError(
                f"ContextInjectionPolicy '{self.id}': template too long "
                f"(>16000)"
            )
        # D57f-1 follow-up (P1): matrix-coherence gate. Without this,
        # a hand-rolled IR (direct PUT, NL-compiled draft, or a stale
        # persisted dict) can land event=SessionStart with matcher=Bash
        # — the compiler dutifully emits hooks.SessionStart=[{matcher:
        # "Bash", ...}] and CC silently drops it (no enforcement) or
        # rejects the whole managed-settings bundle (cascading
        # fail-open across every policy in it). We mirror the
        # EvidencePolicy gate by routing through matcher_class_of +
        # only allowing per-tool matcher classes on the four
        # tool-context events; everything else must be wildcard.
        from .matrix import (
            MatcherClass, _AUDIT_ONLY_WILDCARD_EVENTS, matcher_class_of,
        )
        _TOOL_CONTEXT_EVENTS = frozenset({
            "PreToolUse", "PostToolUse",
            "PostToolUseFailure", "PostToolBatch",
        })
        try:
            kls = matcher_class_of(self.matcher)
        except ValueError as e:
            raise ValueError(
                f"ContextInjectionPolicy '{self.id}': matcher "
                f"{self.matcher!r} {e}"
            ) from e
        if self.event in _TOOL_CONTEXT_EVENTS:
            # tool / mcp_tool / tool_alt / wildcard all legal here
            return
        # Every other event family is keyed without a per-tool matcher
        # in the CC binary's payload — only wildcard is meaningful.
        if kls is not MatcherClass.wildcard:
            raise ValueError(
                f"ContextInjectionPolicy '{self.id}': event {self.event!r} "
                f"has no per-tool matcher in the CC payload; matcher must "
                f"be '*' (got {self.matcher!r}, class={kls.value})"
            )


@dataclass
class InputRewritePolicy:
    """Mutate a tool's input BEFORE the tool runs.

    D57f-2: CC's PreToolUse hook stdout supports a ``updatedInput`` field
    on its ``hookSpecificOutput`` JSON. When the gate emits the new input
    dict, CC runs the tool with that input instead of the one the agent
    proposed. The classic use case is "strip ``sudo`` from a Bash command
    so the agent's instinct to escalate becomes a no-op", but the same
    seam handles "force a URL to https://", "trim a path to a workspace-
    relative segment", etc.

    Security model:
      - ``event`` is pinned to ``PreToolUse``. CC only honors
        ``updatedInput`` on the pre-tool hook (the post-tool hook fires
        AFTER the tool already ran).
      - ``matcher`` follows the regular tool / mcp_tool / tool_alt /
        wildcard rules so the operator can scope to one tool family.
      - ``rewriter`` is a small bounded DSL (see
        :mod:`magi_cp.policy.rewriters`). NO jinja, NO code-eval. A
        leaked policy file cannot translate into arbitrary mutation —
        the worst it can do is rewrite a single tool-input field via
        one of three known operations (prefix_strip / scheme_force /
        regex_substitute).
      - The cloud applies the rewriter spec server-side and returns the
        new tool_input shape to the gate. The gate.py shim forwards
        whatever the cloud returned; it does NOT interpret rewriter
        config locally (a compromised gate runtime cannot inject novel
        operations either way).
    """
    id: str
    description: str
    trigger: Trigger
    rewriter: dict
    version: str = "0.1"
    type: Literal["input_rewrite"] = "input_rewrite"

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        _validate_id(self.id)
        # event pin: CC only supports updatedInput on PreToolUse.
        if self.trigger.event != "PreToolUse":
            raise ValueError(
                f"InputRewritePolicy '{self.id}': event must be PreToolUse "
                f"(CC only honors updatedInput on the pre-tool hook); got "
                f"{self.trigger.event!r}"
            )
        # Matcher: tool / mcp_tool / tool_alt — wildcard would let a
        # rewriter chew on every tool's input, which is rarely what the
        # author intended and rules out the per-tool field name in the
        # rewriter config. Reject wildcard so authoring stays explicit.
        from .matrix import MatcherClass, matcher_class_of
        try:
            kls = matcher_class_of(self.trigger.matcher)
        except ValueError as e:
            raise ValueError(
                f"InputRewritePolicy '{self.id}': matcher "
                f"{self.trigger.matcher!r} {e}"
            ) from e
        if kls is MatcherClass.wildcard:
            raise ValueError(
                f"InputRewritePolicy '{self.id}': matcher='*' is not allowed "
                f"(rewriters target a specific tool's input field; pick a tool "
                f"or alternation)"
            )
        # Rewriter DSL: hand off to the bounded validator. Raises
        # ValueError with a precise reason on any structural problem;
        # we surface that under our policy id for easier debugging.
        from .rewriters import validate_rewriter_spec
        try:
            validate_rewriter_spec(self.rewriter)
        except ValueError as e:
            raise ValueError(
                f"InputRewritePolicy '{self.id}': rewriter {e}"
            ) from e
        # Matrix coherence: even though the matcher class is constrained
        # above, ensure the (event, matcher_class, "input_rewrite") triple
        # is registered as legal so a future LEGAL_COMBINATIONS edit can't
        # silently leave this archetype unwired.
        from .matrix import validate_combination
        try:
            validate_combination(self.trigger.event, self.trigger.matcher,
                                  "input_rewrite")
        except ValueError as e:
            raise ValueError(f"policy '{self.id}': {e}") from e


# D63 — RunCommandPolicy. Lets the operator point a CC hook at an inline
# shell command or an uploaded script file. The command's stdout JSON
# becomes the hook's `hookSpecificOutput` payload (the uniform CC stdout
# contract across all 30 hook events), so this archetype is legal on
# every event in `_SUPPORTED_EVENTS`. There is no allowlist or registry
# gate beyond what CC itself enforces:
#
#   - Self-host single-tenant: the operator installs and owns the
#     scripts on their own machine, equivalent to a CC native hook
#     `{type: "command"}` entry. There is no RCE concern that CC's own
#     hook surface does not already expose.
#   - Hosted (future): the cloud factory respects the `MAGI_CP_ALLOW_RUN_COMMAND`
#     env knob (default "1" on the OSS / self-host docker compose image;
#     hosted overrides to "0"). When "0", /scripts and any
#     RunCommandPolicy save are refused at the REST boundary.
RunCommandRuntime = Literal["bash", "python3", "node"]
_RUN_COMMAND_RUNTIMES: tuple[str, ...] = ("bash", "python3", "node")
_MAX_RUN_COMMAND_INLINE_LEN = 4_000
_MAX_RUN_COMMAND_TIMEOUT_MS = 30_000
_MIN_RUN_COMMAND_TIMEOUT_MS = 100
_DEFAULT_RUN_COMMAND_TIMEOUT_MS = 5_000
_MAX_RUN_COMMAND_ARGS = 16
_MAX_RUN_COMMAND_ARG_LEN = 256
# Script id shape: full 64-hex sha256 hash (the canonical script id is
# the sha256 of the file body). D63 review (P2 validator-mismatch):
# previously accepted 16..64 hex, but ScriptStore.add only ever emits
# 64-hex and the cloud resolver checks exact-string equality. Accepting
# a shorter prefix here would let an IR pass validate() that the
# runtime cannot resolve — and DELETE /scripts then orphans the policy
# because the prefix scan finds no match. A future short-id scheme
# should land as a sibling lookup on ScriptStore.get / body_path /
# the DELETE reference scan TOGETHER with this widening.
_SCRIPT_ID_RE = re.compile(r"^[A-Fa-f0-9]{64}$")


@dataclass
class RunCommandPolicy:
    """Run an inline shell command or attached script in response to a
    CC hook event.

    The command's stdout is interpreted as CC's standard hook
    `hookSpecificOutput` JSON. The local gate executes it under the
    runtime named in `runtime` and returns whatever the command printed
    (subject to the stdout/stderr cap in :mod:`magi_cp.local.gate`).

    Security model (self-host): identical to CC's own
    `{type: "command"}` hook entries — the operator owns the machine
    and the file on disk. No allowlist; the only gate that applies is
    matrix-coherence (event must accept run_command; matcher class must
    match the per-event rule). Hosted deployments add a cloud-side
    env-gated refusal (see `MAGI_CP_ALLOW_RUN_COMMAND`).

    Exactly one of `command` or `script_path` must be set. The
    runtime selects how the inline command is interpreted (`bash -c` /
    `python3 -c` / `node -e`); script_path runs as `runtime <path>
    <args...>`.
    """
    id: str
    description: str
    trigger: Trigger
    runtime: RunCommandRuntime = "bash"
    command: str = ""
    script_path: str = ""
    args: list[str] = field(default_factory=list)
    timeout_ms: int = _DEFAULT_RUN_COMMAND_TIMEOUT_MS
    fail_closed: bool = False
    version: str = "0.1"
    type: Literal["run_command"] = "run_command"

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        _validate_id(self.id)
        if self.runtime not in _RUN_COMMAND_RUNTIMES:
            raise ValueError(
                f"RunCommandPolicy '{self.id}': runtime must be one of "
                f"{_RUN_COMMAND_RUNTIMES}; got {self.runtime!r}"
            )
        has_command = bool(self.command and self.command.strip())
        has_script = bool(self.script_path and self.script_path.strip())
        if has_command == has_script:
            raise ValueError(
                f"RunCommandPolicy '{self.id}': exactly one of `command` "
                f"or `script_path` must be set (got "
                f"command={'yes' if has_command else 'no'}, "
                f"script_path={'yes' if has_script else 'no'})"
            )
        if has_command and len(self.command) > _MAX_RUN_COMMAND_INLINE_LEN:
            raise ValueError(
                f"RunCommandPolicy '{self.id}': inline command too long "
                f"(>{_MAX_RUN_COMMAND_INLINE_LEN} chars)"
            )
        if has_script and not _SCRIPT_ID_RE.match(self.script_path):
            raise ValueError(
                f"RunCommandPolicy '{self.id}': script_path must be a "
                f"16..64 hex script id (got {self.script_path!r})"
            )
        if not isinstance(self.args, list):
            raise ValueError(
                f"RunCommandPolicy '{self.id}': args must be a list"
            )
        if len(self.args) > _MAX_RUN_COMMAND_ARGS:
            raise ValueError(
                f"RunCommandPolicy '{self.id}': too many args "
                f"(>{_MAX_RUN_COMMAND_ARGS})"
            )
        for i, a in enumerate(self.args):
            if not isinstance(a, str):
                raise ValueError(
                    f"RunCommandPolicy '{self.id}': args[{i}] must be a string"
                )
            if len(a) > _MAX_RUN_COMMAND_ARG_LEN:
                raise ValueError(
                    f"RunCommandPolicy '{self.id}': args[{i}] too long "
                    f"(>{_MAX_RUN_COMMAND_ARG_LEN} chars)"
                )
        if not isinstance(self.timeout_ms, int) or isinstance(
            self.timeout_ms, bool,
        ):
            raise ValueError(
                f"RunCommandPolicy '{self.id}': timeout_ms must be an int"
            )
        if not (
            _MIN_RUN_COMMAND_TIMEOUT_MS
            <= self.timeout_ms
            <= _MAX_RUN_COMMAND_TIMEOUT_MS
        ):
            raise ValueError(
                f"RunCommandPolicy '{self.id}': timeout_ms must be in "
                f"[{_MIN_RUN_COMMAND_TIMEOUT_MS}, "
                f"{_MAX_RUN_COMMAND_TIMEOUT_MS}] (got {self.timeout_ms})"
            )
        if self.trigger.event not in _SUPPORTED_EVENTS:
            raise ValueError(
                f"RunCommandPolicy '{self.id}': trigger.event "
                f"unsupported: {self.trigger.event}"
            )
        from .matrix import validate_combination
        try:
            validate_combination(
                self.trigger.event, self.trigger.matcher, "run_command",
            )
        except ValueError as e:
            raise ValueError(f"policy '{self.id}': {e}") from e


# ── session-evidence pair (audit writes, precondition reads) ─────────
# Two coupled archetypes that make "one policy depends on what another
# recorded earlier in the SAME session" authorable. The audit records
# evidence of a named `kind`; the precondition denies an event unless
# that kind is on record at the required verdict. They join on the
# `kind` string. Compile to the `magi-cp-session-audit` /
# `magi-cp-session-gate` binaries (see magi_cp.local.session_evidence).
_EVIDENCE_EXTRACTS: tuple[str, ...] = ("url",)
_EVIDENCE_JUDGES: tuple[str, ...] = ("domain-credibility",)
_EVIDENCE_VERDICTS: tuple[str, ...] = ("pass", "fail", "review")
_MAX_EVIDENCE_KIND_LEN = 128
_MAX_EVIDENCE_REASON_LEN = 400
_EVIDENCE_KIND_RE = re.compile(r"^[a-z0-9_]+$")


@dataclass
class EvidenceAuditPolicy:
    """Record evidence about the tool calls it matches, to the session ledger.

    On each matched call the runtime extracts a subject (``extract``, e.g. the
    URL a WebFetch/Bash retrieved), judges it (``judge``), and appends an
    evidence record under ``kind`` to this session's ledger. Observational:
    never blocks. A precondition gate later reads these records as session state.
    """
    id: str
    description: str
    trigger: Trigger
    kind: str
    extract: str = "url"
    judge: str = "domain-credibility"
    version: str = "0.1"
    type: Literal["evidence_audit"] = "evidence_audit"

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not (isinstance(self.kind, str) and _EVIDENCE_KIND_RE.match(self.kind)
                and len(self.kind) <= _MAX_EVIDENCE_KIND_LEN):
            raise ValueError(
                f"EvidenceAuditPolicy '{self.id}': kind must match [a-z0-9_]+ "
                f"(<= {_MAX_EVIDENCE_KIND_LEN} chars), got {self.kind!r}"
            )
        if self.extract not in _EVIDENCE_EXTRACTS:
            raise ValueError(
                f"EvidenceAuditPolicy '{self.id}': extract must be one of "
                f"{_EVIDENCE_EXTRACTS}, got {self.extract!r}"
            )
        if self.judge not in _EVIDENCE_JUDGES:
            raise ValueError(
                f"EvidenceAuditPolicy '{self.id}': judge must be one of "
                f"{_EVIDENCE_JUDGES}, got {self.judge!r}"
            )
        if self.trigger.event not in _SUPPORTED_EVENTS:
            raise ValueError(
                f"EvidenceAuditPolicy '{self.id}': trigger.event "
                f"unsupported: {self.trigger.event}"
            )
        from .matrix import validate_combination
        try:
            validate_combination(self.trigger.event, self.trigger.matcher, "audit")
        except ValueError as e:
            raise ValueError(f"policy '{self.id}': {e}") from e


@dataclass
class EvidencePreconditionPolicy:
    """Deny an event unless the session ledger holds required evidence.

    The gate: on ``trigger``, if no ``require_kind`` record at ``require_verdict``
    exists for this session, the action (``block`` / ``ask``) fires; otherwise the
    call falls through to the normal permission rules. Pairs with an
    :class:`EvidenceAuditPolicy` that records ``require_kind`` upstream.
    """
    id: str
    description: str
    trigger: Trigger
    require_kind: str
    require_verdict: str = "pass"
    reason: str = ""
    action: Literal["block", "ask"] = "block"
    version: str = "0.1"
    type: Literal["evidence_precondition"] = "evidence_precondition"

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not (isinstance(self.require_kind, str) and _EVIDENCE_KIND_RE.match(self.require_kind)
                and len(self.require_kind) <= _MAX_EVIDENCE_KIND_LEN):
            raise ValueError(
                f"EvidencePreconditionPolicy '{self.id}': require_kind must match "
                f"[a-z0-9_]+, got {self.require_kind!r}"
            )
        if self.require_verdict not in _EVIDENCE_VERDICTS:
            raise ValueError(
                f"EvidencePreconditionPolicy '{self.id}': require_verdict must be "
                f"one of {_EVIDENCE_VERDICTS}, got {self.require_verdict!r}"
            )
        if self.action not in ("block", "ask"):
            raise ValueError(
                f"EvidencePreconditionPolicy '{self.id}': action must be block/ask, "
                f"got {self.action!r}"
            )
        # The gate binary emits a PreToolUse decision envelope. Authoring it on
        # any other event would file a hook whose output CC ignores -> a silent
        # no-op (a green policy that enforces nothing). Pin it, like the other
        # decision-emitting shims.
        if self.trigger.event != "PreToolUse":
            raise ValueError(
                f"EvidencePreconditionPolicy '{self.id}': trigger.event must be "
                f"PreToolUse (the gate emits a PreToolUse decision), got "
                f"{self.trigger.event!r}"
            )
        if not (isinstance(self.reason, str) and len(self.reason) <= _MAX_EVIDENCE_REASON_LEN):
            raise ValueError(
                f"EvidencePreconditionPolicy '{self.id}': reason must be a string "
                f"<= {_MAX_EVIDENCE_REASON_LEN} chars"
            )
        if self.trigger.event not in _SUPPORTED_EVENTS:
            raise ValueError(
                f"EvidencePreconditionPolicy '{self.id}': trigger.event "
                f"unsupported: {self.trigger.event}"
            )
        from .matrix import validate_combination
        try:
            validate_combination(self.trigger.event, self.trigger.matcher, self.action)
        except ValueError as e:
            raise ValueError(f"policy '{self.id}': {e}") from e


# Union of every IR policy type. The compiler dispatches on
# `isinstance(p, X)` rather than on the `type` field so the runtime
# stays string-key-free internally — `type` only matters when crossing
# JSON / REST boundaries.
AnyPolicy = (
    EvidencePolicy | PermissionPolicy | SubagentPolicy
    | McpGatingPolicy | ContextInjectionPolicy | InputRewritePolicy
    | RunCommandPolicy | EvidenceAuditPolicy | EvidencePreconditionPolicy
)


def _coerce_evidence_req(raw: dict) -> EvidenceReq:
    """Build an EvidenceReq from a raw dict, defaulting kind to "step"
    so legacy `{step, verdict}` rows still load."""
    kind = raw.get("kind", "step")
    return EvidenceReq(
        kind=kind,
        step=raw.get("step", ""),
        verdict=raw.get("verdict", "pass"),
        pattern=raw.get("pattern", ""),
        # D82c fix: regex field_path threads through deser so a saved
        # policy round-trips its scoping choice. Missing → "" (legacy
        # whole-payload behaviour); on-disk byte stability against
        # pre-D82c regex rows is preserved by the serializer omitting
        # the key when it's the default.
        field_path=raw.get("field_path", ""),
        criterion=raw.get("criterion", ""),
        shape_ttl=raw.get("shape_ttl", ""),
    )


def _coerce_action(raw: dict) -> ActionLiteral:
    """Accept either the new `action` key or the legacy `on_missing`.
    When both are present, `action` wins."""
    if "action" in raw:
        return raw["action"]
    if "on_missing" in raw:
        legacy = raw["on_missing"]
        mapped = _LEGACY_ON_MISSING_TO_ACTION.get(legacy)
        if mapped is None:
            raise ValueError(f"unknown legacy on_missing value: {legacy!r}")
        return mapped  # type: ignore[return-value]
    return "block"


def load_policy(path: str) -> "AnyPolicy":
    raw = json.loads(open(path, "r", encoding="utf-8").read())
    return policy_from_dict(raw)


def _require_keys(raw: dict, keys: tuple[str, ...], type_: str) -> None:
    """Raise a clear ValueError (not a bare KeyError) on a missing field."""
    missing = [k for k in keys if k not in raw]
    if missing:
        raise ValueError(f"{type_} policy missing required field(s): {', '.join(missing)}")


def policy_from_dict(raw: dict) -> "AnyPolicy":
    """Discriminated deserializer for any IR policy type.

    `type` selects the dataclass; missing → "evidence" so pre-P2 JSON
    files keep round-tripping with no migration. New types REQUIRE
    `type` because their fields are disjoint from the evidence shape.
    """
    type_ = raw.get("type", "evidence")
    if type_ == "evidence":
        p = EvidencePolicy(
            id=raw["id"],
            description=raw.get("description", ""),
            trigger=Trigger(**raw["trigger"]),
            sentinel_re=raw.get("sentinel_re"),
            requires=[_coerce_evidence_req(r) for r in raw.get("requires", [])],
            action=_coerce_action(raw),
            on_signature_invalid=raw.get("on_signature_invalid", "deny"),
            gate_binary=raw.get("gate_binary", "/usr/local/bin/magi-gate.sh"),
            version=raw.get("version", "0.1"),
        )
        p.validate()
        return p
    if type_ == "permission":
        return PermissionPolicy(
            id=raw["id"],
            description=raw.get("description", ""),
            trigger=Trigger(**raw["trigger"]),
            permission=raw["permission"],
            pattern=raw["pattern"],
            version=raw.get("version", "0.1"),
            exclusive=bool(raw.get("exclusive", True)),
        )
    if type_ == "subagent":
        return SubagentPolicy(
            id=raw["id"],
            description=raw.get("description", ""),
            subagent_type=raw["subagent_type"],
            tool_allowlist=list(raw.get("tool_allowlist", [])),
            version=raw.get("version", "0.1"),
        )
    if type_ == "mcp_gating":
        return McpGatingPolicy(
            id=raw["id"],
            description=raw.get("description", ""),
            server=raw["server"],
            action=raw["action"],
            version=raw.get("version", "0.1"),
            exclusive=bool(raw.get("exclusive", True)),
        )
    if type_ == "context_injection":
        return ContextInjectionPolicy(
            id=raw["id"],
            description=raw.get("description", ""),
            event=raw["event"],
            template=raw["template"],
            matcher=raw.get("matcher", "*"),
            version=raw.get("version", "0.1"),
        )
    if type_ == "input_rewrite":
        return InputRewritePolicy(
            id=raw["id"],
            description=raw.get("description", ""),
            trigger=Trigger(**raw["trigger"]),
            rewriter=raw["rewriter"],
            version=raw.get("version", "0.1"),
        )
    if type_ == "run_command":
        return RunCommandPolicy(
            id=raw["id"],
            description=raw.get("description", ""),
            trigger=Trigger(**raw["trigger"]),
            runtime=raw.get("runtime", "bash"),
            command=raw.get("command", ""),
            script_path=raw.get("script_path", ""),
            args=list(raw.get("args", [])),
            timeout_ms=int(raw.get("timeout_ms", _DEFAULT_RUN_COMMAND_TIMEOUT_MS)),
            fail_closed=bool(raw.get("fail_closed", False)),
            version=raw.get("version", "0.1"),
        )
    if type_ == "evidence_audit":
        _require_keys(raw, ("id", "trigger", "kind"), "evidence_audit")
        return EvidenceAuditPolicy(
            id=raw["id"],
            description=raw.get("description", ""),
            trigger=Trigger(**raw["trigger"]),
            kind=raw["kind"],
            extract=raw.get("extract", "url"),
            judge=raw.get("judge", "domain-credibility"),
            version=raw.get("version", "0.1"),
        )
    if type_ == "evidence_precondition":
        _require_keys(raw, ("id", "trigger", "require_kind"), "evidence_precondition")
        return EvidencePreconditionPolicy(
            id=raw["id"],
            description=raw.get("description", ""),
            trigger=Trigger(**raw["trigger"]),
            require_kind=raw["require_kind"],
            require_verdict=raw.get("require_verdict", "pass"),
            reason=raw.get("reason", ""),
            action=raw.get("action", "block"),
            version=raw.get("version", "0.1"),
        )
    raise ValueError(f"unknown policy type: {type_!r}")


def policy_to_dict(p: "AnyPolicy") -> dict:
    """Symmetric dict serializer for any IR policy type.

    EvidencePolicy stays byte-stable with pre-P2 fixtures: we OMIT
    the new `type` discriminator when it's the default ("evidence")
    so on-disk stores diff to zero. The native-surface siblings
    always carry `type`.
    """
    if isinstance(p, EvidencePolicy):
        def _req_to_dict(r: EvidenceReq) -> dict:
            # Keep the original on-disk shapes per kind so byte stability
            # holds against pre-P2/P3 fixtures.
            if r.kind == "step":
                return {"step": r.step, "verdict": r.verdict}
            if r.kind == "regex":
                # D82c fix: only emit field_path when set so pre-D82c
                # regex rows round-trip byte-identical. Saved policies
                # that scope their match to a specific field carry the
                # field_path key; legacy whole-payload policies don't.
                out: dict = {"kind": "regex", "pattern": r.pattern}
                if r.field_path:
                    out["field_path"] = r.field_path
                return out
            if r.kind == "llm_critic":
                return {"kind": "llm_critic", "criterion": r.criterion}
            if r.kind == "shacl":
                return {"kind": "shacl", "shape_ttl": r.shape_ttl}
            raise ValueError(f"unsupported evidence kind: {r.kind!r}")
        d: dict = {
            "id": p.id,
            "description": p.description,
            "version": p.version,
            "trigger": {"host": p.trigger.host, "event": p.trigger.event,
                        "matcher": p.trigger.matcher},
            "sentinel_re": p.sentinel_re,
            "requires": [_req_to_dict(r) for r in p.requires],
            "action": p.action,
            "on_signature_invalid": p.on_signature_invalid,
            "gate_binary": p.gate_binary,
        }
        return d
    if isinstance(p, PermissionPolicy):
        return {
            "type": "permission",
            "id": p.id, "description": p.description, "version": p.version,
            "trigger": {"host": p.trigger.host, "event": p.trigger.event,
                        "matcher": p.trigger.matcher},
            "permission": p.permission, "pattern": p.pattern,
            "exclusive": p.exclusive,
        }
    if isinstance(p, SubagentPolicy):
        return {
            "type": "subagent",
            "id": p.id, "description": p.description, "version": p.version,
            "subagent_type": p.subagent_type,
            "tool_allowlist": list(p.tool_allowlist),
        }
    if isinstance(p, McpGatingPolicy):
        return {
            "type": "mcp_gating",
            "id": p.id, "description": p.description, "version": p.version,
            "server": p.server, "action": p.action,
            "exclusive": p.exclusive,
        }
    if isinstance(p, ContextInjectionPolicy):
        return {
            "type": "context_injection",
            "id": p.id, "description": p.description, "version": p.version,
            "event": p.event, "matcher": p.matcher, "template": p.template,
        }
    if isinstance(p, InputRewritePolicy):
        return {
            "type": "input_rewrite",
            "id": p.id, "description": p.description, "version": p.version,
            "trigger": {"host": p.trigger.host, "event": p.trigger.event,
                        "matcher": p.trigger.matcher},
            "rewriter": p.rewriter,
        }
    if isinstance(p, RunCommandPolicy):
        return {
            "type": "run_command",
            "id": p.id, "description": p.description, "version": p.version,
            "trigger": {"host": p.trigger.host, "event": p.trigger.event,
                        "matcher": p.trigger.matcher},
            "runtime": p.runtime,
            "command": p.command,
            "script_path": p.script_path,
            "args": list(p.args),
            "timeout_ms": p.timeout_ms,
            "fail_closed": p.fail_closed,
        }
    if isinstance(p, EvidenceAuditPolicy):
        return {
            "type": "evidence_audit",
            "id": p.id, "description": p.description, "version": p.version,
            "trigger": {"host": p.trigger.host, "event": p.trigger.event,
                        "matcher": p.trigger.matcher},
            "kind": p.kind, "extract": p.extract, "judge": p.judge,
        }
    if isinstance(p, EvidencePreconditionPolicy):
        return {
            "type": "evidence_precondition",
            "id": p.id, "description": p.description, "version": p.version,
            "trigger": {"host": p.trigger.host, "event": p.trigger.event,
                        "matcher": p.trigger.matcher},
            "require_kind": p.require_kind, "require_verdict": p.require_verdict,
            "reason": p.reason, "action": p.action,
        }
    raise ValueError(f"unknown policy type: {type(p).__name__}")
