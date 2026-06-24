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
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi import Path as FPath
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from ..evidence import sign_token
from ..policy import (
    AnyPolicy, ContextInjectionPolicy, EvidencePolicy, EvidenceReq,
    McpGatingPolicy, PermissionPolicy, Policy, PolicyOverride,
    ResolvedPolicySet, SubagentPolicy, Trigger,
    compile_to_managed_settings,
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
from .db import (
    EndpointHeartbeatRepo, HitlRepo, HitlStatus, LedgerRepo,
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
    """
    model_config = {"extra": "forbid"}

    ir: dict
    since: Literal["24h", "7d"] = "24h"
    limit: int = Field(default=1000, ge=1, le=10_000)


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
    # kind-specific
    pattern: str | None = Field(default=None, max_length=2000)
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


# ── middlewares ──────────────────────────────────────────────────────
class MaxBodyMiddleware(BaseHTTPMiddleware):
    """413 on Content-Length OR by accumulating a streamed/chunked body."""

    def __init__(self, app, limit: int):
        super().__init__(app); self.limit = limit

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
        super().__init__(app); self.cap = capacity; self.refill = refill_per_sec
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
    policy_store = PolicyStore(path=policy_store_path or os.environ.get(
        "MAGI_CP_POLICY_STORE", str(Path.home() / ".magi-cp" / "policies.json")))
    custom_verifier_store = CustomVerifierStore(
        path=custom_verifier_store_path or os.environ.get(
            "MAGI_CP_CUSTOM_VERIFIER_STORE",
            str(Path.home() / ".magi-cp" / "custom_verifiers.json"),
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

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.post("/policies/compile", dependencies=[Depends(require_admin_key)])
    async def policies_compile(req: "CompileReq") -> dict:
        """Authoring gate 1+2 — NL→IR compile + critic review.

        Returns {"ir": {...}, "review": {"ok": bool, "issues": [...]}}.
        NEVER persists. Gate 3 (human approval) is the dashboard editing the
        IR if needed and calling PUT /policies/{id}.

        v2.0-W5: runs via asyncio.to_thread so the sync httpx-based providers
        don't block the FastAPI event loop during the 5–60s LLM call.
        """
        if llm_compiler is None or llm_reviewer is None:
            raise HTTPException(
                503, "LLM providers not configured on this deployment",
            )
        from .nl_compiler import PrecheckError, compile_with_review
        try:
            return await asyncio.to_thread(
                compile_with_review,
                compiler=llm_compiler,
                reviewer=llm_reviewer,
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

    @app.post("/policies/dry-run", dependencies=[Depends(require_admin_key)])
    def policies_dry_run(req: "DryRunReq", request: Request) -> dict:
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
        """
        from ..policy.dry_run import evaluate_dry_run
        from ..policy.run_redaction import (
            DEFAULT_PREVIEW_MAX_CHARS, redact_payload_preview,
        )

        # Gate 1: shape check. Reuse the policies CRUD deserializer
        # so an authoring-time validation failure here mirrors the
        # one the operator would have seen on PUT. The Policy
        # dataclass's __post_init__ raises ValueError on any matrix
        # / regex / SHACL lint failure.
        try:
            policy = _deserialize_policy_from_api(req.ir)
        except (ValueError, KeyError) as e:
            raise HTTPException(422, str(e)) from e

        # Gate 2: ledger window. `since` is a closed enum to keep
        # the replay's blast radius bounded (a typo cannot widen to
        # 90d). Limit is clamped by pydantic above (1..10_000).
        window_secs = {"24h": 86_400, "7d": 7 * 86_400}[req.since]
        cutoff = int(time.time()) - window_secs
        tenant_id = getattr(request.state, "tenant_id", "default")
        rows = ledger.list_recent_window(
            tenant_id, limit=req.limit, since_ts=cutoff,
        )

        # Gate 3: pure replay. The helper returns row ids; we
        # hydrate the matched rows + redact their bodies before they
        # cross the wire.
        result = evaluate_dry_run(policy, rows, sample_limit=3)

        # Build the redacted sample list. Look the matched rows back
        # up by id from the already-hydrated `rows` window so we do
        # not need a second SQL round-trip. The redactor is
        # fail-closed; an unexpected future body field with a secret
        # cannot leak through this surface.
        rows_by_id = {r.id: r for r in rows}
        sample_matched: list[dict] = []
        for rid in result.sample_matched_ids:
            r = rows_by_id.get(rid)
            if r is None:
                continue
            body = r.body if isinstance(r.body, dict) else {}
            verdict_raw = body.get("verdict")
            _ALLOWED_VERDICTS = {
                "pass", "fail", "deny",
                "review", "needs_review", "not_applicable",
            }
            verdict = (
                verdict_raw
                if isinstance(verdict_raw, str)
                and verdict_raw in _ALLOWED_VERDICTS
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
            "by_verdict": result.by_verdict,
            "by_action": result.by_action,
            "sample_matched": sample_matched,
            "skipped_reason": result.skipped_reason,
            "since": req.since,
            "limit": req.limit,
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
        tenant_id = getattr(request.state, "tenant_id", "default")
        if tenant_id == "default":
            return {"id": "default", "status": "active", "plan": "free",
                    "expires_at": None, "synthetic": True}
        t = TenantRepo(engine).get(tenant_id)
        if t is None:
            raise HTTPException(404, "tenant not found")
        return {
            "id": t.id, "status": t.status, "plan": t.plan,
            "expires_at": t.expires_at, "synthetic": False,
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
        try:
            verdict = v.run(req.payload)
        except Exception as e:
            # Verifier blew up on a malformed payload → treat as deny, record.
            async with chain_lock:
                ledger.append(subject=subj,
                              body={"step": step, "verdict": "deny",
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
                )
            result["reasons"] = list(verdict.reasons)
            return result
        if verdict.status == "review":
            async with chain_lock:
                result = _issue_token(
                    subj, phash, "review",
                    ledger=ledger, keystore=ks, kid=kid, step=step,
                    tenant_id=tenant_id,
                )
            result["reasons"] = list(verdict.reasons)
            return result
        # deny
        async with chain_lock:
            ledger.append(subject=subj,
                          body={"step": step, "verdict": "deny",
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
        # SHACL works on the dict shape directly.
        payload_text = ""
        try:
            txt = req.payload.get("text") if isinstance(req.payload, dict) else None
            if isinstance(txt, str):
                payload_text = txt
            else:
                import json as _json
                payload_text = _json.dumps(req.payload, ensure_ascii=False)[:8000]
        except Exception:
            payload_text = ""

        verdict_status: str = "deny"
        reasons: list[str] = []
        if kind == "regex":
            if not req.pattern:
                raise HTTPException(422, "kind=regex requires pattern")
            try:
                rx = re.compile(req.pattern)
            except re.error as e:
                raise HTTPException(422, f"pattern fails to compile: {e}")
            if rx.search(payload_text):
                verdict_status = "pass"
                reasons = [f"pattern matched: {req.pattern[:80]}"]
            else:
                verdict_status = "deny"
                reasons = [f"pattern did not match: {req.pattern[:80]}"]
        elif kind == "llm_critic":
            if not req.criterion:
                raise HTTPException(422, "kind=llm_critic requires criterion")
            if llm_compiler is None:
                verdict_status = "review"
                reasons = [
                    "llm_critic preview: MAGI_CP_LLM_COMPILER not configured — "
                    "policy authored but runtime evaluation deferred to HITL.",
                ]
            else:
                # Lightweight one-call yes/no critic. The compiler-side
                # provider already handles auth + timeout; we use it for
                # judgment too.
                prompt = (
                    "You are a strict gate. Reply with exactly YES or NO on "
                    "the first line, then a one-sentence rationale.\n\n"
                    f"CRITERION: {req.criterion}\n\n"
                    f"PAYLOAD:\n{payload_text[:4000]}"
                )
                try:
                    raw = await asyncio.to_thread(
                        llm_compiler.complete, prompt,
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
                import pyshacl, rdflib  # type: ignore[import-not-found]
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
                                    present = True; break
                            if not present:
                                for ln in targets["targetClass"]:
                                    if (None, rdflib.RDF.type, ns[ln]) in data:
                                        present = True; break
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
        if verdict_status in ("pass", "review"):
            async with chain_lock:
                result = _issue_token(
                    subj, phash, verdict_status,
                    ledger=ledger, keystore=ks, kid=kid, step=step_label,
                    tenant_id=tenant_id,
                )
            result["reasons"] = reasons
            return result
        async with chain_lock:
            ledger.append(subject=subj,
                          body={"step": step_label, "verdict": "deny",
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
        # Closed-set verdict allowlist. Mirrors the frontend's
        # `verdictLabel` map; any string outside this set is collapsed
        # to None at the cloud boundary so a misbehaving producer
        # cannot leak a novel string through this surface.
        _ALLOWED_VERDICTS = {
            "pass", "fail", "deny",
            "review", "needs_review", "not_applicable",
        }
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
                and verdict_raw in _ALLOWED_VERDICTS
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

    # ── /policies CRUD (v1) ──────────────────────────────────────
    _attach_policy_routes(app, policy_store, policy_lock,
                          verifier_registry=verifier_registry)

    # ── /admin/tenants (v2-W6a) — HMAC-signed; clawy webhook calls these ──
    _attach_admin_tenant_routes(app, engine)

    # ── /catalog/* — derived (read-only) evidence-type + condition view ──
    _attach_catalog_routes(
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
    )

    # ── /endpoints — P10 endpoint attestation ─────────────────────────
    _attach_endpoint_routes(app, engine, policy_store=policy_store)

    return app


# ── helpers ──────────────────────────────────────────────────────────
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
                 extra: dict | None = None) -> dict:
    """Issue a cloud-signed verdict token.

    PR4: legacy `matter`/`doc_hash` mirror fields removed from the signed
    body. Gates that haven't rolled forward past PR2 will no longer find
    a verifying token — operators must upgrade gate binaries before
    flipping to a PR4 cloud.
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
    entry = ledger.append(subject=subject, body=body, token=token,
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
from ..policy.precedence import SOURCE_PRECEDENCE as _SP
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
        pattern=r"^(evidence|permission|subagent|mcp_gating|context_injection)$",
    )


class PutPolicyReq(BaseModel):
    """PUT body. `policy` is loosely-typed at the boundary (see
    PolicyIn) and re-validated archetype-specifically via
    `_deserialize_policy_from_api`."""
    model_config = {"extra": "forbid"}
    policy: dict
    source: str = Field(..., pattern=_SOURCE_REGEX)
    enabled: bool = True


class PatchEnabledReq(BaseModel):
    enabled: bool


_RESERVED_ID_SUFFIXES = ("/compiled", "/enabled")


def _attach_policy_routes(app: FastAPI, store: PolicyStore,
                           policy_lock: asyncio.Lock,
                           *,
                           verifier_registry: "VerifierRegistry | None" = None,
                           ) -> None:

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
            items.append(entry)
        return {"items": items}

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
        return {"id": policy.id, "source": body.source, "enabled": body.enabled,
                "enforcement": resolved_enforcement,
                "type": getattr(policy, "type", "evidence")}

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
        import hmac as _hmac, hashlib as _hashlib
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
    from ..verifier.descriptors import all_descriptors, get_descriptor

    @app.get("/verifier-descriptors")
    def list_verifier_descriptors() -> dict:
        return {"descriptors": all_descriptors()}

    @app.get("/verifier-descriptors/{step}")
    def get_verifier_descriptor(step: str) -> dict:
        d = get_descriptor(step)
        if d is None:
            raise HTTPException(
                404,
                f"no descriptor for verifier step {step!r}",
            )
        return d


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


def _attach_custom_verifier_routes(
    app: FastAPI, store: "CustomVerifierStore",
    custom_verifier_lock: asyncio.Lock,
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
    return app


def run() -> None:  # pragma: no cover
    import uvicorn
    uvicorn.run(_build_production_app(), host="127.0.0.1", port=8787)
