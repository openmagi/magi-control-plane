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
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from ..evidence import sign_token
from ..policy import (
    EvidenceReq, Policy, PolicyOverride, ResolvedPolicySet, Trigger,
    compile_to_managed_settings,
)
from ..verifier import (Citation, EntailmentClassifier, score_review_citations,
                        verify_document)
from ..verifier.protocol import VerifierRegistry
from ..verifier.sources import DictResolver
from .policy_store import PolicyStore
from .db import HitlRepo, HitlStatus, LedgerRepo, init_schema, make_engine
from .keys import KeyStore
from .presets_catalog import vendor_catalog


TOKEN_TTL_SECONDS = 600   # short, refreshable. License expiry = fail-closed.
MAX_REQUEST_BYTES = 256 * 1024
MAX_CITATIONS_PER_REQUEST = 50
MAX_QUOTE_LEN = 8_000
MAX_REF_LEN = 1_000
MAX_DOCUMENT_LEN = 200_000
MAX_CORPUS_OVERRIDE_BYTES = 200_000

PROTECTED_TOKEN_FIELDS = {"step", "matter", "doc_hash", "verdict", "iat", "exp", "issuer", "kid"}


# ── request/response shapes (size-bounded per P3 #C2) ────────────────
class CitationIn(BaseModel):
    quote: str = Field(..., min_length=1, max_length=MAX_QUOTE_LEN)
    ref: str = Field(..., min_length=1, max_length=MAX_REF_LEN)


class VerifyReq(BaseModel):
    matter: str = Field(..., min_length=1, max_length=64,
                        pattern=r"^[A-Za-z0-9_\-]+$")
    doc_id: str = Field(..., min_length=1, max_length=64,
                        pattern=r"^[A-Za-z0-9_\-]+$")
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


# v2.0-W7: verifier payload cap (regex DoS defense). 20K is plenty for any
# realistic filing-time payload and tight enough that pathological regex
# inputs can't push past the deterministic-time budget.
MAX_VERIFIER_PAYLOAD_BYTES = 20_000


# v1.2-W3: generic verifier dispatch.
class VerifyDispatchReq(BaseModel):
    # The verifier's input_schema is verifier-specific — we accept any dict
    # and let the verifier handle shape errors with a deny verdict.
    payload: dict = Field(..., description="opaque payload passed to verifier.run()")
    matter: str = Field(default="generic", min_length=1, max_length=128)
    doc_id: str = Field(default="generic", min_length=1, max_length=128)

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


# ── factory ──────────────────────────────────────────────────────────
def create_app(
    *,
    keystore: KeyStore | None = None,
    dsn: str | None = None,
    nli_classifier: EntailmentClassifier | None = None,
    policy_store_path: str | None = None,
    verifier_registry: "VerifierRegistry | None" = None,
    llm_compiler: "object | None" = None,
    llm_reviewer: "object | None" = None,
) -> FastAPI:
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

    # cache pubkey + derive kid (key id)
    pubkey_pem = ks.public_pem()
    kid = hashlib.sha256(pubkey_pem.encode("utf-8")).hexdigest()[:16]

    # H1: chain-head serialization
    chain_lock = asyncio.Lock()
    # v1: policy mutation serialization — prevents lost-update race on /policies PUT|PATCH.
    policy_lock = asyncio.Lock()

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

    @app.get("/presets")
    def get_presets() -> dict:
        """Merge live VerifierRegistry (wired) + vendored magi-agent catalog
        (preview). Read-only, no auth — operator-facing overview, no secrets.

        Sort: 5 wired first (operator sees what they have), then vendor
        entries alphabetical by id.
        """
        wired: list[dict] = []
        seen_ids: set[str] = set()
        if verifier_registry is not None:
            for v in verifier_registry.all():
                # Catalog ID = step with underscores → hyphens (magi-agent style).
                # The step (not the name) is the policy-IR binding key, so basing
                # the public ID on step keeps `/presets` ID stable across name
                # renames like the legacy verify_citations alias.
                pid = v.step.replace("_", "-")
                wired.append({
                    "id": pid,
                    "category": v.category,
                    "description": v.description,
                    "enforcement": v.enforcement.value,
                    "step": v.step,
                })
                seen_ids.add(pid)
        vendor = sorted(
            (
                {
                    "id": vp.id,
                    "category": vp.category,
                    "description": vp.description,
                    "enforcement": "preview",
                    "step": None,
                }
                for vp in vendor_catalog()
                if vp.id not in seen_ids   # wired ID shadows vendor entry
            ),
            key=lambda p: p["id"],
        )
        return {"presets": wired + vendor}

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
        v = verifier_registry.get_by_step(step)
        if v is None:
            raise HTTPException(404, f"no verifier registered for step {step!r}")
        tenant_id = getattr(request.state, "tenant_id", "default")
        try:
            verdict = v.run(req.payload)
        except Exception as e:
            # Verifier blew up on a malformed payload → treat as deny, record.
            async with chain_lock:
                ledger.append(matter=req.matter,
                              body={"step": step, "verdict": "deny",
                                    "doc_id": req.doc_id, "error": str(e)[:200]},
                              token="", tenant_id=tenant_id)
            return {"verdict": "deny", "token": None,
                    "reasons": [f"verifier error: {type(e).__name__}"]}
        if verdict.status == "pass":
            async with chain_lock:
                result = _issue_token(
                    req.matter, req.doc_id, "pass",
                    ledger=ledger, keystore=ks, kid=kid, step=step,
                    tenant_id=tenant_id,
                )
            result["reasons"] = list(verdict.reasons)
            return result
        if verdict.status == "review":
            async with chain_lock:
                result = _issue_token(
                    req.matter, req.doc_id, "review",
                    ledger=ledger, keystore=ks, kid=kid, step=step,
                    tenant_id=tenant_id,
                )
            result["reasons"] = list(verdict.reasons)
            return result
        # deny
        async with chain_lock:
            ledger.append(matter=req.matter,
                          body={"step": step, "verdict": "deny",
                                "doc_id": req.doc_id,
                                "reasons": list(verdict.reasons)},
                          token="", tenant_id=tenant_id)
        return {"verdict": "deny", "token": None,
                "reasons": list(verdict.reasons)}

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
        # doc_hash binding: if a document is supplied, doc_hash MUST match its sha256.
        # If only doc_id is supplied (no document), doc_id is used as the binding — gate
        # callers can opt in to content-binding by passing the document.
        if req.document:
            content_hash = hashlib.sha256(req.document.encode("utf-8")).hexdigest()[:32]
            if req.doc_id != content_hash:
                raise HTTPException(400, "doc_id must equal sha256(document)[:32] when document is supplied")
        if doc.verdict == "pass":
            async with chain_lock:
                return _issue_token(req.matter, req.doc_id, "pass",
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
            item = hitl.enqueue(
                matter=req.matter, doc_id=req.doc_id, reason="citation_review",
                payload={"citations": review_payload},
                tenant_id=tenant_id,
            )
            async with chain_lock:
                ledger.append(matter=req.matter,
                              body={"step": "citation_verify", "verdict": "review",
                                    "doc_id": req.doc_id, "hitl_id": item.id},
                              token="", tenant_id=tenant_id)
            return {"verdict": "review", "token": None, "hitl_id": item.id,
                    "citations": _citations_summary(doc)}
        # deny
        async with chain_lock:
            ledger.append(matter=req.matter,
                          body={"step": "citation_verify", "verdict": "deny",
                                "doc_id": req.doc_id},
                          token="", tenant_id=tenant_id)
        return {"verdict": "deny", "token": None,
                "citations": _citations_summary(doc)}

    @app.get("/hitl/{item_id}/detail", dependencies=[Depends(require_hitl_key)])
    def get_hitl_detail(item_id: int) -> dict:
        item = hitl.get(item_id)
        if item is None:
            raise HTTPException(404, f"hitl item {item_id} not found")
        # Pull ledger entries for this matter so reviewers see context (the
        # citation_verify=review entry + neighbors). Body redacted by default
        # for general /ledger; here we include because the reviewer is gated.
        ctx_entries = []
        for e in ledger.list_by_matter(item.matter):
            ctx_entries.append({
                "id": e.id, "ts": e.ts, "h": e.h, "prev": e.prev,
                "body": e.body,
            })
        return {
            "id": item.id, "matter": item.matter, "doc_id": item.doc_id,
            "reason": item.reason, "payload": item.payload,
            "status": item.status.value,
            "approver": item.approver, "note": item.note,
            "ts_created": item.ts_created, "ts_decided": item.ts_decided,
            "ledger_context": ctx_entries,
        }

    @app.get("/hitl", dependencies=[Depends(require_hitl_key)])
    def list_hitl() -> dict:
        return {"items": [
            {"id": i.id, "matter": i.matter, "doc_id": i.doc_id,
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
        async with chain_lock:
            return _issue_token(item.matter, item.doc_id, "pass",
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
        async with chain_lock:
            ledger.append(matter=item.matter,
                          body={"step": "hitl_decision", "decision": "rejected",
                                "doc_id": item.doc_id, "hitl_id": item_id,
                                "approver": body.approver},
                          token="")
        return {"verdict": "rejected", "token": None, "hitl_id": item_id}

    @app.get("/ledger", dependencies=[Depends(require_tenant_auth)])
    def list_ledger(request: Request, since_id: int = 0, limit: int = 100,
                     include_body: bool = False) -> dict:
        """Per-tenant ledger view. chain_ok validates the GLOBAL chain (so
        cross-tenant tampering is still detectable), but `entries` is scoped
        to the requesting tenant."""
        limit = max(1, min(int(limit), 1000))
        tenant_id = getattr(request.state, "tenant_id", "default")
        tenant_entries = ledger.list_by_tenant(tenant_id)
        chain_ok = ledger.verify_chain()   # global integrity, not per-tenant
        page = [e for e in tenant_entries if e.id > since_id][:limit]
        return {"chain_ok": chain_ok,
                "next_since_id": page[-1].id if page else since_id,
                "entries": [
                    {"id": e.id, "ts": e.ts, "matter": e.matter,
                     "prev": e.prev, "h": e.h,
                     **({"body": e.body, "token": e.token} if include_body else {})}
                    for e in page
                ]}

    # ── /policies CRUD (v1) ──────────────────────────────────────
    _attach_policy_routes(app, policy_store, policy_lock)

    # ── /admin/tenants (v2-W6a) — HMAC-signed; clawy webhook calls these ──
    _attach_admin_tenant_routes(app, engine)

    return app


# ── helpers ──────────────────────────────────────────────────────────
def _citations_summary(doc) -> list[dict]:
    return [
        {"ref": v.citation.ref, "case_number": v.case_number,
         "status": v.status, "reasons": v.reasons}
        for v in doc.verdicts
    ]


def _issue_token(matter: str, doc_id: str, verdict: str, *,
                 ledger: LedgerRepo, keystore: KeyStore, kid: str,
                 step: str = "citation_verify",
                 tenant_id: str = "default",
                 extra: dict | None = None) -> dict:
    now = int(time.time())
    # L2: extras are *base*; protected fields go LAST so they always win.
    base = dict(extra) if extra else {}
    leaked = PROTECTED_TOKEN_FIELDS & base.keys()
    if leaked:
        raise HTTPException(500, f"protected field clash: {leaked}")
    body = {
        **base,
        "step": step,
        "matter": matter,
        "doc_hash": doc_id,
        "verdict": verdict,
        "iat": now,
        "exp": now + TOKEN_TTL_SECONDS,
        "issuer": os.environ.get("MAGI_CP_ISSUER", "magi-cloud-dev"),
        "kid": kid,
    }
    token = sign_token(body, keystore.load_private())
    entry = ledger.append(matter=matter, body=body, token=token,
                           tenant_id=tenant_id)
    return {"verdict": verdict, "token": token, "exp": body["exp"],
            "kid": kid, "ledger_h": entry.h}


def _enforcement_label(policy: Policy) -> str:
    """Short human label for the enforcement character of a policy.

    v1 surface: simple mapping. Future v1.x will use the matrix to label each
    rule individually (deterministic-gate / advisory / log-only).
    """
    if policy.trigger.event == "PreToolUse" and policy.on_missing in ("deny", "ask"):
        return "deterministic-gate"
    if policy.trigger.event == "PostToolUse":
        return "observe-only"
    return "log-only"


def _serialize_policy_for_api(p: Policy) -> dict:
    return {
        "id": p.id,
        "description": p.description,
        "version": p.version,
        "trigger": {"host": p.trigger.host, "event": p.trigger.event,
                    "matcher": p.trigger.matcher},
        "sentinel_re": p.sentinel_re,
        "requires": [{"step": r.step, "verdict": r.verdict} for r in p.requires],
        "on_missing": p.on_missing,
        "on_signature_invalid": p.on_signature_invalid,
        "gate_binary": p.gate_binary,
    }


def _deserialize_policy_from_api(d: dict) -> Policy:
    return Policy(
        id=d["id"], description=d.get("description", ""),
        version=d.get("version", "0.1"),
        trigger=Trigger(**d["trigger"]),
        sentinel_re=d["sentinel_re"],
        requires=[EvidenceReq(**r) for r in d["requires"]],
        on_missing=d.get("on_missing", "deny"),
        on_signature_invalid=d.get("on_signature_invalid", "deny"),
        gate_binary=d.get("gate_binary", "/usr/local/bin/magi-gate.sh"),
    )


def _compile_with_sha(policy: Policy) -> tuple[dict, str]:
    import json as _json
    ms = compile_to_managed_settings([policy])
    blob = _json.dumps(ms, ensure_ascii=False, indent=2, sort_keys=True)
    return ms, hashlib.sha256(blob.encode("utf-8")).hexdigest()


# Derive the source regex from SOURCE_PRECEDENCE so the two cannot drift.
from ..policy.precedence import SOURCE_PRECEDENCE as _SP
_SOURCE_REGEX = "^(" + "|".join(_SP) + ")$"


class PolicyIn(BaseModel):
    """Request body for PUT /policies/{id}. Loose at the boundary; validation
    runs in Policy.__post_init__ via the matrix."""
    # Mirror Policy._validate_id at the boundary so pydantic rejects with a
    # 422 (not a 400 from the matrix layer) on obviously bad inputs.
    id: str = Field(..., min_length=1, max_length=128,
                     pattern=r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,127}$")
    description: str = Field(default="", max_length=2000)
    version: str = Field(default="0.1", max_length=32)
    trigger: dict
    sentinel_re: str = Field(..., min_length=1, max_length=2000)
    requires: list[dict]
    on_missing: str = Field(default="deny")
    on_signature_invalid: str = Field(default="deny")
    gate_binary: str = Field(default="/usr/local/bin/magi-gate.sh", max_length=1000)


class PutPolicyReq(BaseModel):
    policy: PolicyIn
    source: str = Field(..., pattern=_SOURCE_REGEX)
    enabled: bool = True


class PatchEnabledReq(BaseModel):
    enabled: bool


_RESERVED_ID_SUFFIXES = ("/compiled", "/enabled")


def _attach_policy_routes(app: FastAPI, store: PolicyStore,
                           policy_lock: asyncio.Lock) -> None:

    @app.get("/policies", dependencies=[Depends(require_admin_key)])
    def list_policies() -> dict:
        items = []
        for ov in store.load():
            items.append({
                "id": ov.policy.id,
                "description": ov.policy.description,
                "source": ov.source,
                "enabled": ov.enabled,
                "trigger": {"event": ov.policy.trigger.event,
                            "matcher": ov.policy.trigger.matcher},
                "enforcement": _enforcement_label(ov.policy),
            })
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
                return {
                    "id": ov.policy.id,
                    "source": ov.source,
                    "enabled": ov.enabled,
                    "policy": _serialize_policy_for_api(ov.policy),
                    "enforcement": _enforcement_label(ov.policy),
                    "compiled_sha256": sha,
                }
        raise HTTPException(404, f"policy {policy_id!r} not found")

    @app.put("/policies/{policy_id:path}", dependencies=[Depends(require_admin_key)])
    async def put_policy(policy_id: str, body: PutPolicyReq) -> dict:
        if body.policy.id != policy_id:
            raise HTTPException(400, "id mismatch between url and body")
        if any(policy_id.endswith(s) for s in _RESERVED_ID_SUFFIXES):
            raise HTTPException(400, f"policy id must not end in {_RESERVED_ID_SUFFIXES}")
        try:
            policy = _deserialize_policy_from_api(body.policy.model_dump())
        except ValueError as e:
            # Matrix violation or any other __post_init__ failure
            raise HTTPException(400, str(e))
        async with policy_lock:
            existing = store.load()
            existing = [ov for ov in existing if ov.policy.id != policy_id]
            existing.append(PolicyOverride(policy=policy, source=body.source,  # type: ignore[arg-type]
                                            enabled=body.enabled))
            store.save(existing)
        return {"id": policy.id, "source": body.source, "enabled": body.enabled}

    @app.patch("/policies/{policy_id:path}/enabled",
               dependencies=[Depends(require_admin_key)])
    async def patch_enabled(policy_id: str, body: PatchEnabledReq) -> dict:
        async with policy_lock:
            existing = store.load()
            found = False
            new_list: list[PolicyOverride] = []
            for ov in existing:
                if ov.policy.id == policy_id:
                    found = True
                    new_list.append(PolicyOverride(
                        policy=ov.policy, source=ov.source, enabled=body.enabled,
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
    """
    from ..verifier.builtins import register_builtins
    from ..verifier.protocol import VerifierRegistry
    from .observability import attach_metrics, configure_structlog
    configure_structlog()
    reg = VerifierRegistry()
    register_builtins(reg)
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
