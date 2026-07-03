"""Shared Pydantic request/response schemas for the cloud FastAPI app.

Extracted verbatim from ``app.py`` (modularization design
2026-07-03-cloud-app-modularization-design.md). Behavior-preserving: the
models + the request-normalisation helpers they call are byte-identical to
their former in-``app.py`` form; ``app.py`` re-imports them so every existing
reference (routes, tests importing from ``magi_cp.cloud.app``) keeps working
unchanged.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..policy.nl_compiler_interactive import (
    MAX_ANSWER_KEY_CHARS as _D55A_MAX_ANSWER_KEY_CHARS,
    MAX_ANSWER_VALUE_CHARS as _D55A_MAX_ANSWER_VALUE_CHARS,
    MAX_ANSWERS as _D55A_MAX_ANSWERS,
    MAX_ASSISTANT_MESSAGE_CHARS as _D55A_MAX_ASSISTANT_MESSAGE_CHARS,
    MAX_HISTORY_TURNS as _D55A_MAX_HISTORY_TURNS,
    MAX_USER_MESSAGE_CHARS as _D55A_MAX_USER_MESSAGE_CHARS,
)
from .constants import (
    MAX_CITATIONS_PER_REQUEST,
    MAX_DOCUMENT_LEN,
    MAX_QUOTE_LEN,
    MAX_REF_LEN,
    MAX_VERIFIER_PAYLOAD_BYTES,
    _KEY_PATTERN,
    _POLICY_ID_PATTERN,
)
# The request-normalisation + policy-deserialize helpers live in
# serialization.py (design 2026-07-03). schemas -> serialization is the only
# import direction; serialization never imports schemas. VerifyDispatchReq /
# VerifyInlineReq call `_synth_subject_and_hash` in their model_post_init.
from .serialization import _synth_subject_and_hash  # noqa: F401


# ── request/response shapes (size-bounded per P3 #C2) ────────────────
class CitationIn(BaseModel):
    quote: str = Field(..., min_length=1, max_length=MAX_QUOTE_LEN)
    ref: str = Field(..., min_length=1, max_length=MAX_REF_LEN)


# Shared regex for both old and new key fields — kept identical so the
# alias path doesn't smuggle in shapes the legacy path would reject.
# (_KEY_PATTERN now lives in cloud/constants.py, imported at module top.)


class VerifyReq(BaseModel):
    """v1 citation_verify request shape.

    PR4: legacy `matter`/`doc_id` aliases removed. Only `subject` and
    `payload_hash` are accepted. A request that still carries the legacy
    fields is a clean 422 (pydantic's `extra="forbid"` rejects unknown
    keys) so a caller stuck on the old vocabulary surfaces immediately
    rather than silently winning under a mirror.
    """
    model_config = {"extra": "forbid"}

    subject: str = Field(..., min_length=1, max_length=64,
                          pattern=_KEY_PATTERN)
    payload_hash: str = Field(..., min_length=1, max_length=64,
                               pattern=_KEY_PATTERN)
    document: str = Field(default="", max_length=MAX_DOCUMENT_LEN)
    citations: list[CitationIn] = Field(default_factory=list,
                                         max_length=MAX_CITATIONS_PER_REQUEST)
    corpus_override: dict[str, str] | None = None


class DecideReq(BaseModel):
    approver: str = Field(..., min_length=1, max_length=256)
    note: str | None = Field(default=None, max_length=2_000)


# v1.1-PD: NL→IR compile + review.
class PriorTurnIn(BaseModel):
    role: str = Field(..., pattern=r"^(user|assistant)$")
    content: str = Field(..., min_length=1, max_length=10_000)


class CompileReq(BaseModel):
    # Bounded so a runaway NL can't pin LLM tokens or push past the body cap.
    nl: str = Field(..., min_length=1, max_length=20_000)
    prior_turns: list[PriorTurnIn] | None = Field(default=None, max_length=20)


# D55a: conversational compile (turn-by-turn variant of /policies/compile).
# Shares its caps with the library module so the boundary is single-
# source-of-truth (the _D55A_* aliases are imported at the top of this file).


class InteractiveTurnIn(BaseModel):
    """One {role, content} pair in the conversational compile history.

    Per-turn caps are SYMMETRIC: both user and assistant turns share
    the user-message cap. Earlier versions used `max(user_cap, 10_000)`
    on assistant turns on the theory that they're echoes of server
    output. That is not a guarantee at the library boundary, since a
    direct caller (not via FastAPI) can ship a 50K-char assistant turn
    and use it as a prompt-injection surface (the LLM is steered by
    fenced assistant content). The library's `_validate_history` also
    enforces symmetric caps; both boundaries agree.
    """
    model_config = {"extra": "forbid"}

    role: str = Field(..., pattern=r"^(user|assistant)$")
    content: str = Field(
        ..., min_length=1,
        max_length=max(_D55A_MAX_USER_MESSAGE_CHARS,
                        _D55A_MAX_ASSISTANT_MESSAGE_CHARS),
    )


class InteractiveCompileReq(BaseModel):
    """Body for POST /policies/compile-interactive.

    `draft_so_far` is a loose dict at this boundary; the library
    module's `_sanitize_draft_so_far` drops unknown top-level keys and
    coerces subtrees to safe shapes (so a client cannot pre-seed
    `gate_binary`, `pattern`, or other archetype-specific fields by
    stuffing them into the draft).

    `answers` is constrained at the pydantic boundary AND in the
    library so a runaway request 422s before the library's aggregate
    cap deep-copies a multi-MB payload. The library's
    `_validate_answers_shape` enforces the same bounds for direct
    callers.
    """
    model_config = {"extra": "forbid"}

    history: list[InteractiveTurnIn] | None = Field(
        default=None, max_length=_D55A_MAX_HISTORY_TURNS,
    )
    draft_so_far: dict | None = None
    answers: dict[str, str] | None = Field(
        default=None, max_length=_D55A_MAX_ANSWERS,
    )

    @field_validator("answers")
    @classmethod
    def _bound_answer_keys_and_values(
        cls, v: dict[str, str] | None,
    ) -> dict[str, str] | None:
        """Per-key / per-value length cap for `answers`.

        Pydantic v2 cannot enforce a per-key or per-value length cap
        on a `dict[str, str]` via `Field(max_length=...)` alone (that
        only bounds the number of keys). A field_validator gives us a
        clean 422 on the same boundary as the rest of the request.
        """
        if v is None:
            return v
        for k, val in v.items():
            if len(k) > _D55A_MAX_ANSWER_KEY_CHARS:
                raise ValueError(
                    f"answer key too long ({len(k)} > "
                    f"{_D55A_MAX_ANSWER_KEY_CHARS} chars)"
                )
            if len(val) > _D55A_MAX_ANSWER_VALUE_CHARS:
                raise ValueError(
                    f"answer {k!r} too long ({len(val)} > "
                    f"{_D55A_MAX_ANSWER_VALUE_CHARS} chars)"
                )
        return v


# D57g: handoff from wizard / raw editor → conversational. The body
# is a snapshot of in-progress authoring state; the response is the
# same wire shape `step_compile` emits so the conversational client
# mounts it as a first assistant turn.
class HandoffContextReq(BaseModel):
    """Body for POST /policies/handoff-context.

    Both fields are loose dicts at this pydantic boundary; the library
    module's `build_handoff_turn` reuses the same sanitisers /
    per-field allowlists `step_compile` does so a malicious client
    cannot smuggle `gate_binary` or other archetype-specific fields
    past the merge by stuffing them into the draft.

    `origin` is the authoring surface the user just left
    ("guided" / "advanced" / "review"). Used by the cloud serialiser
    to vary the summary headline. Optional.

    `locale` is an explicit "ko" / "en" override forwarded from the
    dashboard so a Korean-locale operator authoring an English-only
    policy still receives a Korean seed (the draft-content heuristic
    is too weak to detect that case on its own). Optional.
    """
    model_config = {"extra": "forbid"}

    wizard_state: dict | None = None
    draft_ir: dict | None = None
    origin: Literal["guided", "advanced", "review"] | None = None
    locale: Literal["ko", "en"] | None = None


# D53b: replay-against-last-24h dry-run authoring affordance.
class DryRunReq(BaseModel):
    """POST /policies/dry-run body. Replays a draft IR over recent
    ledger rows to estimate "if this policy were enabled, how many of
    the last 24h's tool calls would it have action'd?"

    `ir` is intentionally a loose dict at the pydantic boundary - the
    archetype-specific shape check happens via
    `_deserialize_policy_from_api` (which routes through
    `policy_from_dict` + Policy.__post_init__) inside the route. That
    keeps the validation surface identical to `/policies` PUT so an
    operator who can save the policy can also dry-run it.

    `since`: human-readable window selector. Closed enum (24h / 7d)
    so a typo cannot quietly widen the replay scope. Default 24h.

    `limit`: cap on rows replayed inside the window. The replay is
    Python-side per row so a 7d window with thousands of rows would
    pin a worker - the cap (max 10_000) is the safety net.

    `tenant_id`: which tenant's ledger to replay against. The route
    is admin-key gated (no per-request tenant resolution from the
    api key), so without this field the replay used to silently
    target the synthetic `default` tenant - producing a wrong-tenant
    count on every multi-tenant deployment. The route validates the
    value against the tenants table and 422s on an unknown id.
    Defaults to None; the route accepts None on single-tenant
    deployments (empty tenants table) for back-compat with the
    `default`-tenant single-tenant flow.
    """
    model_config = {"extra": "forbid"}

    ir: dict
    since: Literal["24h", "7d"] = "24h"
    limit: int = Field(default=1000, ge=1, le=10_000)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=64,
                                   pattern=r"^[A-Za-z0-9_\-\.]+$")


# v2.0-W7: verifier payload cap (regex DoS defense). 20K is plenty for any
# realistic filing-time payload and tight enough that pathological regex
# inputs can't push past the deterministic-time budget.
# (MAX_VERIFIER_PAYLOAD_BYTES now lives in cloud/constants.py.)


# v1.2-W3: generic verifier dispatch.
class VerifyDispatchReq(BaseModel):
    """PR4: legacy `matter`/`doc_id` aliases removed. Only `subject` and
    `payload_hash` are accepted (still optional — when omitted the route
    synthesises a (subject, payload_hash) from the payload + session id
    so the ledger entry is bound to the actual call rather than a
    literal "generic" string). `extra="forbid"` makes a request that
    still carries the legacy field names a clean 422 instead of a
    silent accept.

    Storage alignment: `max_length=64` matches `LedgerEntry.matter` /
    `HitlItem.subject` String(64) columns. `pattern=_KEY_PATTERN`
    rejects characters that would smuggle bytes into the cloud-signed
    token body or ledger column.
    """
    model_config = {"extra": "forbid"}

    # The verifier's input_schema is verifier-specific — we accept any dict
    # and let the verifier handle shape errors with a deny verdict.
    payload: dict = Field(..., description="opaque payload passed to verifier.run()")
    subject: str | None = Field(default=None, min_length=1, max_length=64,
                                pattern=_KEY_PATTERN)
    payload_hash: str | None = Field(default=None, min_length=1, max_length=64,
                                      pattern=_KEY_PATTERN)
    # D53b follow-up: frame metadata. The gate writes the hook event +
    # matcher pattern it fired on so the offline dry-run replay can
    # scope ledger rows to a specific (event, matcher) frame instead
    # of admitting every tenant row. Both are bounded short strings
    # because the cloud projects them onto the ledger body verbatim.
    hook_event: str | None = Field(default=None, min_length=1, max_length=64,
                                    pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    matcher: str | None = Field(default=None, min_length=1, max_length=256)

    def model_post_init(self, _ctx) -> None:
        # Pydantic v2: enforce payload's serialized size after construction.
        # JSON encoding is cheap relative to the regex pass that would follow.
        import json as _json
        encoded = _json.dumps(self.payload, ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_VERIFIER_PAYLOAD_BYTES:
            raise ValueError(
                f"verifier payload too large: {len(encoded)} > "
                f"{MAX_VERIFIER_PAYLOAD_BYTES} bytes"
            )
        # If neither key was supplied, synthesise from the payload so the
        # ledger entry is bound to the actual call rather than a literal
        # "generic" string. When a session_id is in the payload we use it.
        if self.subject is None and self.payload_hash is None:
            sid = self.payload.get("session_id") if isinstance(self.payload, dict) else None
            subj, phash = _synth_subject_and_hash(self.payload, session_id=sid)
            object.__setattr__(self, "subject", subj)
            object.__setattr__(self, "payload_hash", phash)
            return
        # Partial supply: synth the missing side so downstream code can
        # rely on both being present (matches pre-PR4 "generic" default
        # behaviour minus the literal "generic" string).
        if self.subject is None:
            sid = self.payload.get("session_id") if isinstance(self.payload, dict) else None
            subj, _ = _synth_subject_and_hash(self.payload, session_id=sid)
            object.__setattr__(self, "subject", subj)
        if self.payload_hash is None:
            _, phash = _synth_subject_and_hash(self.payload)
            object.__setattr__(self, "payload_hash", phash)


class VerifyInlineReq(BaseModel):
    """D35: dispatch an inline EvidenceReq (regex / llm_critic / shacl).

    The gate sends this for any non-`step` requires entry on a policy.
    Step-kind entries continue to use the existing /verify/{step}
    endpoint so the registered verifier instance handles them with
    no closure into the cloud layer.

    PR4: legacy `matter`/`doc_id` aliases removed (extra="forbid"). Only
    `subject`/`payload_hash` are accepted; both optional with payload
    synth filling the gap, same shape as VerifyDispatchReq."""
    model_config = {"extra": "forbid"}

    kind: str = Field(..., pattern="^(regex|llm_critic|shacl)$")
    payload: dict
    subject: str | None = Field(default=None, min_length=1, max_length=64,
                                pattern=_KEY_PATTERN)
    payload_hash: str | None = Field(default=None, min_length=1, max_length=64,
                                      pattern=_KEY_PATTERN)
    # D53b follow-up: frame metadata. Same shape + semantics as the
    # one on VerifyDispatchReq above. Gates that haven't rolled forward
    # past the runtime-write contract simply omit these fields; the
    # ledger row will be excluded from offline regex/llm_critic/shacl
    # dry-run replays (the replay refuses to admit rows whose frame
    # cannot be reconstructed).
    hook_event: str | None = Field(default=None, min_length=1, max_length=64,
                                    pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    matcher: str | None = Field(default=None, min_length=1, max_length=256)
    # kind-specific
    pattern: str | None = Field(default=None, max_length=2000)
    # D82c fix: optional dotted-identifier scoping for kind=regex. Empty
    # / unset → match whole-payload projection (legacy). Non-empty →
    # scope `re.search` to the resolved field only, so an operator who
    # picks `tool_response.output` does NOT also match SSN strings in
    # `tool_input.command` / `tool_input.description` / etc.
    field_path: str | None = Field(
        default=None,
        max_length=256,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$",
    )
    criterion: str | None = Field(default=None, max_length=4000)
    shape_ttl: str | None = Field(default=None, max_length=16000)

    def model_post_init(self, _ctx) -> None:
        import json as _json
        encoded = _json.dumps(self.payload, ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_VERIFIER_PAYLOAD_BYTES:
            raise ValueError(
                f"verifier payload too large: {len(encoded)} > "
                f"{MAX_VERIFIER_PAYLOAD_BYTES} bytes"
            )
        # Same synth contract as VerifyDispatchReq above.
        if self.subject is None and self.payload_hash is None:
            sid = self.payload.get("session_id") if isinstance(self.payload, dict) else None
            subj, phash = _synth_subject_and_hash(self.payload, session_id=sid)
            object.__setattr__(self, "subject", subj)
            object.__setattr__(self, "payload_hash", phash)
            return
        if self.subject is None:
            sid = self.payload.get("session_id") if isinstance(self.payload, dict) else None
            subj, _ = _synth_subject_and_hash(self.payload, session_id=sid)
            object.__setattr__(self, "subject", subj)
        if self.payload_hash is None:
            _, phash = _synth_subject_and_hash(self.payload)
            object.__setattr__(self, "payload_hash", phash)


# ── Q97a: /admin/llm-keys body shapes (module-scope so FastAPI's
# get_type_hints can resolve them on Python 3.14) ────────────────────
class LlmKeysPutReq(BaseModel):
    """PUT body for /admin/llm-keys.

    Both fields optional. A missing field LEAVES the prior value
    unchanged (NOT cleared). An empty string CLEARS that key. A
    non-empty string overwrites. The store performs no validation
    beyond a length cap; the LLM provider will raise on first call if
    the value is malformed.
    """
    model_config = {"extra": "forbid"}
    anthropic_api_key: str | None = Field(default=None, max_length=4096)
    openai_api_key: str | None = Field(default=None, max_length=4096)


class LlmKeysTestReq(BaseModel):
    """POST body for /admin/llm-keys/test. Optional `provider` field
    narrows the probe to anthropic or openai; absent / null runs both."""
    model_config = {"extra": "forbid"}
    provider: Literal["anthropic", "openai"] | None = None

# Derive the source regex from SOURCE_PRECEDENCE so the two cannot drift.
from ..policy.precedence import SOURCE_PRECEDENCE as _SP  # noqa: E402  paired-with-regex-below
_SOURCE_REGEX = "^(" + "|".join(_SP) + ")$"

class PolicyIn(BaseModel):
    """Request body for PUT /policies/{id}.

    Issue #1 P0 (#12): the boundary is intentionally loose — `type`
    discriminates and we route through `policy_from_dict` /
    `policy.validate()` for archetype-specific shape checks (each
    archetype's dataclass has fields the others don't). The pydantic
    layer only asserts the universal id shape + the discriminator;
    everything else is checked by Policy.__post_init__ via the
    matrix.

    Pre-P2 clients omit `type` and ship the EvidencePolicy shape —
    `policy_from_dict` defaults `type="evidence"` so the existing
    contract still passes.
    """
    model_config = {"extra": "allow"}

    id: str = Field(..., min_length=1, max_length=128,
                     pattern=_POLICY_ID_PATTERN)
    type: str | None = Field(
        default=None,
        pattern=r"^(evidence|permission|subagent|mcp_gating|context_injection|input_rewrite|run_command)$",
    )

class PutPolicyReq(BaseModel):
    """PUT body. `policy` is loosely-typed at the boundary (see
    PolicyIn) and re-validated archetype-specifically via
    `_deserialize_policy_from_api`."""
    model_config = {"extra": "forbid"}
    policy: dict
    source: str = Field(..., pattern=_SOURCE_REGEX)
    enabled: bool = True
    # P4 (pack-centric authoring): 0..n user-pack ids the saved policy
    # should join. On save the cloud appends the policy id to each named
    # pack's member list in the SAME critical section as the policy
    # write. Empty / omitted = orphan (no pack membership) — a legitimate
    # "author now, wire up later" state. Built-in ``pack/…`` ids are
    # rejected (immutable membership); the floor pack (a ``user-pack/…``
    # row) is accepted so an operator can pin a policy to "always-on".
    pack_ids: list[str] | None = None


class CompoundPolicyReq(BaseModel):
    """POST /policies/compound body. `draft` is a policy draft (a compound like
    type=evidence_gate, or a single rule). The server expands it into its member
    rules and saves the PolicyRecord + rules atomically."""
    model_config = {"extra": "forbid"}
    draft: dict
    source: str = Field(..., pattern=_SOURCE_REGEX)
    enabled: bool = True
    pack_ids: list[str] | None = None


class PatchEnabledReq(BaseModel):
    enabled: bool

class InputRewriteReq(BaseModel):
    """D57f-2 — request body for the `magi-cp-input-rewrite` shim's
    POST /policies/input_rewrite call.

    P2 follow-up: per-field length cap on `tool_input` values. The
    middleware body cap (`MAX_REQUEST_BYTES`, 256KB) is the ambient
    ceiling, but a single string field inside `tool_input` can still
    be ~250KB at that level, which is the amplification factor for
    the regex_substitute ReDoS lane. We cap individual string values
    fed to the rewriter; oversize values are silently rejected
    (validation maps to 422) so a crafted blob can't burn CPU on
    `re.sub` even when the matcher does cover the tool_name.
    """
    model_config = {"extra": "forbid"}
    policy_id: str = Field(..., min_length=1, max_length=128,
                            pattern=_POLICY_ID_PATTERN)
    tool_name: str = Field(..., min_length=1, max_length=128)
    tool_input: dict

    @field_validator("tool_input")
    @classmethod
    def _cap_field_value_lengths(cls, v: dict) -> dict:
        # Match the `_MAX_REWRITE_INPUT_LEN` cap in rewriters.py. Any
        # single value larger than the cap is outside the rewriter's
        # safe operating envelope; refusing at the boundary closes the
        # amplification surface BEFORE we walk policy lookup or regex
        # engine. We only cap top-level string values; nested dicts
        # are rare in CC PreToolUse payloads and would otherwise let
        # an attacker hide a blow-up inside `tool_input["nested"]`.
        # `apply_rewriter` already gates against the same cap as
        # defense in depth.
        _MAX = 64 * 1024
        for k, val in v.items():
            if isinstance(val, str) and len(val) > _MAX:
                raise ValueError(
                    f"tool_input[{k!r}] exceeds {_MAX}-byte cap"
                )
            if isinstance(val, (dict, list)) and len(
                str(val)
            ) > _MAX * 2:
                raise ValueError(
                    f"tool_input[{k!r}] nested value too large"
                )
        return v

class RunCommandReq(BaseModel):
    """D63 — request body for the `magi-cp-run-command` shim's POST
    /policies/run_command call.

    The shim sends the policy id + the raw CC hook payload it
    received on stdin; the cloud resolves the RunCommandPolicy and
    returns the spec the shim should execute. `payload` is kept as a
    free-form dict — the shim may forward the CC payload as additional
    context for future conditional run_command logic, but the v1
    cloud-side resolver does not inspect it.
    """
    model_config = {"extra": "forbid"}
    policy_id: str = Field(..., min_length=1, max_length=128,
                            pattern=_POLICY_ID_PATTERN)
    payload: dict = Field(default_factory=dict)


# (_RESERVED_ID_SUFFIXES now lives in cloud/constants.py, imported at module top.)

class CustomVerifierTriggerIn(BaseModel):
    """One trigger row on a /custom-verifiers POST body. Mirrors the
    validators in custom_verifier_store: event whitelist + matcher_class
    enum are still enforced at the store layer (single source of truth);
    this model adds Pydantic's standard 422 shape so the dashboard's
    error renderer can key off `detail[].loc` like it does for
    /policies."""
    model_config = {"extra": "forbid"}

    event: str = Field(..., min_length=1, max_length=64)
    matcher_class: str = Field(..., pattern=r"^(tool|no_tool|final)$")

class CustomVerifierFieldCheckIn(BaseModel):
    """D52d: one (path, check_description) pair on a /custom-verifiers
    POST body. Mirrors the catalog descriptor `FieldCheck` shape so the
    dashboard renderer can reuse the same component over both data
    sources (built-in catalog + authored custom row).

    `path` is a free-form string today (e.g. `tool_input.url`); we do
    NOT enforce the CC payload-schema vocabulary at this boundary so an
    operator authoring a verifier for a domain-specific MCP tool can
    describe paths the cloud has no schema for yet. `check_description`
    is bounded at 200 chars to match the catalog cell budget and to
    keep the dashboard's tree rendering predictable.
    """
    model_config = {"extra": "forbid"}

    path: str = Field(..., min_length=1, max_length=128)
    check_description: str = Field(..., min_length=1, max_length=200)

class CreateCustomVerifierReq(BaseModel):
    """Request body for POST /custom-verifiers.

    `extra='forbid'` so a hand-rolled body that includes legacy keys
    (`kind`, `pattern`, `criterion`, `shape_ttl`) is rejected with a
    clear field-level 422 instead of silently honouring the step-shape
    keys and dropping the rest — surfaces the design lock at the wire
    boundary. regex / llm_critic / shacl checks belong inline in a
    policy's `requires[]`, not in a registerable verifier row.
    """
    model_config = {"extra": "forbid"}

    name: str = Field(..., min_length=1, max_length=64,
                       pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(..., min_length=1, max_length=500)
    triggers: list[CustomVerifierTriggerIn] = Field(..., min_length=1, max_length=32)
    verdict_set: list[str] = Field(..., min_length=1, max_length=8)
    body_type: str = Field(..., pattern=r"^preview$")
    # D52d: per-field check rows (>=1). The store re-validates the same
    # invariants; the Pydantic body keeps a per-field 422 path for the
    # dashboard error renderer.
    field_checks: list[CustomVerifierFieldCheckIn] = Field(
        ..., min_length=1, max_length=32,
    )
    # D57c: input-assembly contract. Optional on the wire so a
    # pre-D57c client keeps working (defaults to cc_stdin). Authors who
    # want to document a caller_assembled verifier opt in by sending
    # `caller_assembled` + a non-empty caller_assembly_hint. Store
    # re-validates the (assembly, hint) pair for the invariants
    # (caller_assembled needs hint, cc_stdin must leave hint blank).
    input_assembly: str = Field(
        default="cc_stdin", pattern=r"^(cc_stdin|caller_assembled)$",
    )
    caller_assembly_hint: str = Field(default="", max_length=500)

class HeartbeatReq(BaseModel):
    """Gate → cloud heartbeat body.

    `active_policy_digest` is sha256(managed-settings.json)[:64]. The gate
    computes this off whatever JSON file it just read; missing → None
    (gate hasn't loaded settings yet, e.g. first boot before initial
    `compile`). `agent_version` is informational only — the dashboard
    surfaces it so operators can spot stale gates.

    Issue #1 P0 (#1): the heartbeat trust model is TOFU-over-tenant-key
    until a per-endpoint enrollment keypair is wired. We accept
    `signed_attestation` + `nonce` + `ts` as optional fields so a
    later cloud version that enforces enrollment can run without a
    wire format change. Today the cloud stores the attestation
    opaquely. Replay-resistance: `ts` is checked against a ±5min wall
    window; older heartbeats are rejected so a captured payload can't
    be replayed by a man-in-the-middle.
    """
    model_config = {"extra": "forbid"}

    endpoint_id: str = Field(..., min_length=1, max_length=64,
                              pattern=r"^[A-Za-z0-9_\-]+$")
    active_policy_digest: str | None = Field(
        default=None, min_length=64, max_length=64,
        pattern=r"^[a-f0-9]{64}$",
    )
    agent_version: str | None = Field(default=None, max_length=64)
    label: str | None = Field(default=None, max_length=128)
    # Issue #1 P0 (#1): replay window + signed attestation. Both
    # optional today; `signed_attestation` becomes required once
    # enrollment ships. `ts` enables ±5min window check now.
    ts: int | None = Field(default=None, ge=0)
    nonce: str | None = Field(
        default=None, min_length=8, max_length=64,
        pattern=r"^[A-Za-z0-9_\-]+$",
    )
    signed_attestation: str | None = Field(
        default=None, max_length=256,
    )

class _ScriptUploadReq(BaseModel):
    """D63 — POST /scripts body. The browser-facing wizard ships the
    script as multipart/form-data through the Next.js proxy route; the
    proxy decodes the file bytes, base64-encodes them, and re-POSTs to
    this endpoint as JSON. This keeps the cloud free of a
    `python-multipart` dependency without losing the upload UX.
    """
    model_config = {"extra": "forbid"}
    name: str = Field(..., min_length=1, max_length=64)
    runtime: Literal["bash", "python3", "node"]
    body_b64: str = Field(..., min_length=1, max_length=256_000)

