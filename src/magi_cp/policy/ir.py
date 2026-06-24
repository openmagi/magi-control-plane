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


EventLiteral = Literal[
    "PreToolUse", "PostToolUse",
    "Stop", "SubagentStop",
    "UserPromptSubmit",
    "PreCompact",
    "SessionStart", "SessionEnd",
]
_SUPPORTED_EVENTS: frozenset[str] = frozenset({
    "PreToolUse", "PostToolUse",
    "Stop", "SubagentStop",
    "UserPromptSubmit",
    "PreCompact",
    "SessionStart", "SessionEnd",
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
_CONTEXT_EVENT_LITERALS = ("UserPromptSubmit", "SessionStart")
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
    """Static text injected into UserPromptSubmit / SessionStart hook
    handlers.

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
    """
    id: str
    description: str
    event: Literal["UserPromptSubmit", "SessionStart"]
    template: str
    matcher: str = "*"
    version: str = "0.1"
    type: Literal["context_injection"] = "context_injection"

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        _validate_id(self.id)
        if self.event not in _CONTEXT_EVENT_LITERALS:
            raise ValueError(
                f"ContextInjectionPolicy '{self.id}': event must be "
                f"UserPromptSubmit or SessionStart; got {self.event!r}"
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


# Union of every IR policy type. The compiler dispatches on
# `isinstance(p, X)` rather than on the `type` field so the runtime
# stays string-key-free internally — `type` only matters when crossing
# JSON / REST boundaries.
AnyPolicy = (
    EvidencePolicy | PermissionPolicy | SubagentPolicy
    | McpGatingPolicy | ContextInjectionPolicy
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
                return {"kind": "regex", "pattern": r.pattern}
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
    raise ValueError(f"unknown policy type: {type(p).__name__}")
