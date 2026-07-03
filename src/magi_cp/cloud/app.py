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

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from ..verifier import (Citation, EntailmentClassifier, score_review_citations,
                        verify_document)
from ..verifier.protocol import VerifierRegistry
from ..verifier.sources import DictResolver
from .custom_verifier_store import (
    CustomVerifierStore,
)
from .policy_store import PolicyStore
from .pack_store import (
    PackStore,
)
from .script_store import (
    ScriptStore,
)
from .db import (
    HitlRepo, LedgerRepo, SharedRunRepo,
    init_schema, make_engine,
)
from .keys import KeyStore
from .presets_catalog import vendor_catalog


# Shared limits + token constants now live in cloud/constants.py so the
# request schemas can import them without a circular dependency. Re-exported
# here (import *) so existing `app.MAX_...` / `app._KEY_PATTERN` references and
# the test suite keep resolving unchanged.
from .constants import (  # noqa: E402,F401
    TOKEN_TTL_SECONDS,
    MAX_REQUEST_BYTES,
    MAX_CITATIONS_PER_REQUEST,
    MAX_QUOTE_LEN,
    MAX_REF_LEN,
    MAX_DOCUMENT_LEN,
    MAX_CORPUS_OVERRIDE_BYTES,
    MAX_VERIFIER_PAYLOAD_BYTES,
    _KEY_PATTERN,
    _POLICY_ID_PATTERN,
    _RESERVED_ID_SUFFIXES,
    PROTECTED_TOKEN_FIELDS,
)


# ── request/response schemas moved out (modularization 2026-07-03) ───
# cloud/schemas.py holds all shared Pydantic request models + _SOURCE_REGEX.
# Re-exported here so every existing reference (route bodies, tests
# importing from magi_cp.cloud.app) keeps working unchanged.
from .schemas import (  # noqa: E402,F401
    CitationIn,
    VerifyReq,
    DecideReq,
    PriorTurnIn,
    CompileReq,
    InteractiveTurnIn,
    InteractiveCompileReq,
    HandoffContextReq,
    DryRunReq,
    VerifyDispatchReq,
    VerifyInlineReq,
    LlmKeysPutReq,
    LlmKeysTestReq,
    PolicyIn,
    PutPolicyReq,
    PatchEnabledReq,
    InputRewriteReq,
    RunCommandReq,
    CustomVerifierTriggerIn,
    CustomVerifierFieldCheckIn,
    CreateCustomVerifierReq,
    HeartbeatReq,
    _ScriptUploadReq,
    _SOURCE_REGEX,
)


# ── serialization / token helpers moved out (modularization 2026-07-03) ─
# cloud/serialization.py holds the policy (de)serialize + compile-with-sha +
# token-issue + request-normalisation helpers. Re-exported here so route
# bodies (which reference these as bare module-level names) and tests
# importing e.g. _issue_token / _synth_subject_and_hash from
# magi_cp.cloud.app keep working unchanged.
from .serialization import (  # noqa: E402,F401
    _canonical_json_bytes,
    _synth_subject_and_hash,
    _deserialize_policy_from_api,
    _frame_meta_for_ledger,
    _iso_ts,
    _citations_summary,
    _issue_token,
    _enforcement_label,
    _serialize_policy_for_api,
    _compile_with_sha,
    _compile_set_with_sha,
)


# ── middleware + auth deps moved out (modularization 2026-07-03) ──────
# cloud/middleware.py + cloud/deps.py hold these now. Re-exported here so
# every existing reference (route decorators, tests importing from
# magi_cp.cloud.app) keeps working unchanged.
from .middleware import (  # noqa: E402,F401
    MaxBodyMiddleware,
    TokenBucketLimiter,
    _BodyTooLarge,
    _json_response,
    _bounded_regex_search,
)
from .deps import (  # noqa: E402,F401
    _check_key,
    require_api_key,
    require_hitl_key,
    require_admin_key,
    require_tenant_auth,
    _resolve_tenant_id_from_request,
)

# ── route groups moved out (modularization 2026-07-03, design PR2) ────
# create_app calls routes.<group>.attach(app, deps). See
# docs/plans/2026-07-03-cloud-app-modularization-design.md.
from .routes import (  # noqa: E402
    runtime as routes_runtime,
    admin_tenant as routes_admin_tenant,
    catalog as routes_catalog,
    check_evidence as routes_check_evidence,
    payload_schema as routes_payload_schema,
    verifier_descriptor as routes_verifier_descriptor,
    custom_verifier as routes_custom_verifier,
    endpoint as routes_endpoint,
    session_pack as routes_session_pack,
    policy as routes_policy,
    script_store as routes_script_store,
)


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
    # Helm chart (or docker-compose env); test/library callers leave it
    # unset and keep the lenient "registry=None → enforcing" path for
    # fixture back-compat. The runtime invariant in _build_production_app
    # is the deploy-shape guarantee; this env hook is the override for
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
    # pack -> policy -> rule: the policy-tier store (groupings of rules a user
    # authored as one intent), alongside the rule store.
    from .policy_group_store import PolicyGroupStore
    # Derive a SIBLING file (dirname/policy-groups.json) so a custom
    # policy_store_path filename can't collide the two stores onto one file.
    _pg_path = (
        str(Path(policy_store_path).parent / "policy-groups.json")
        if policy_store_path
        else os.environ.get("MAGI_CP_POLICY_GROUP_STORE",
                            str(Path.home() / ".magi-cp" / "policy-groups.json")))
    policy_group_store = PolicyGroupStore(path=_pg_path)
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
    # Default public run-share links to a 30-day TTL (SHARE-1). A leaked share
    # URL is otherwise valid forever. Operators who want permanent links set
    # MAGI_CP_SHARE_TTL_SECONDS=0 (explicit no-expiry opt-in).
    _SHARE_TTL_SECONDS = int(os.environ.get("MAGI_CP_SHARE_TTL_SECONDS", "2592000")) or None

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
        # Best-effort GC of revoked / expired rows so stored redacted views do
        # not linger forever (SHARE-1). Never fail share creation on a GC error.
        try:
            share_repo.purge_expired()
        except Exception:  # pragma: no cover - defensive
            pass
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
                    if await _bounded_regex_search(rx, scoped_text):
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
                if await _bounded_regex_search(rx, payload_text):
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
            # Scope by the item's tenant: `subject` is a cross-tenant
            # namespace, so an unscoped read would surface another tenant's
            # ledger bodies here (TENANT-1).
            for e in ledger.list_by_subject(subj, tenant_id=item.tenant_id):
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
                                tenant_id=item.tenant_id,
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
                          token="",
                          tenant_id=item.tenant_id)
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
    routes_runtime.attach(app, engine,
                           policy_store=policy_store,
                           pack_store=pack_store)

    routes_policy.attach(app, policy_store, policy_lock,
                         verifier_registry=verifier_registry,
                         keystore=ks,
                         kid=kid,
                         script_store=script_store,
                         script_store_lock=script_store_lock,
                         pack_store=pack_store,
                         pack_store_lock=pack_store_lock,
                         policy_group_store=policy_group_store)

    # ── /admin/tenants (v2-W6a) — HMAC-signed; clawy webhook calls these ──
    routes_admin_tenant.attach(app, engine)

    # ── /catalog/* — derived (read-only) evidence-type + condition view ──
    routes_catalog.attach(
        app, policy_store, verifier_registry,
        custom_verifier_store=custom_verifier_store,
    )

    # ── /checks + /evidence-types (D56e) — new Rules page tabs ──────────
    routes_check_evidence.attach(
        app, policy_store, verifier_registry,
        custom_verifier_store=custom_verifier_store,
    )

    # ── /payload-schemas — P7 CC hook payload field menu (read-only) ──
    routes_payload_schema.attach(app)

    # ── /verifier-descriptors: D52b per-verifier expander descriptors ──
    routes_verifier_descriptor.attach(app)

    # ── /custom-verifiers: D52b step-only authoring (tenant-scoped) ──
    routes_custom_verifier.attach(
        app, custom_verifier_store, custom_verifier_lock,
        verifier_registry=verifier_registry,
    )

    # ── /endpoints — P10 endpoint attestation ─────────────────────────
    routes_endpoint.attach(app, engine, policy_store=policy_store)

    # ── /scripts — D63 run_command policy script storage ────────────
    routes_script_store.attach(
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
    routes_session_pack.attach(
        app, engine,
        pack_store=pack_store,
        pack_store_lock=pack_store_lock,
        policy_store=policy_store,
    )

    return app


# _run_command_allowed moved to config.py (alongside the other runtime env
# gates). Re-exported so the policy + script_store groups still in this file
# resolve it until they move to routes/ in the same change.
from ..config import _run_command_allowed  # noqa: E402,F401


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
    # Cross-replica safety: when two or more pods boot against the same
    # Postgres they would otherwise run this migration concurrently and
    # race on the shared tenants/packs state. Serialize with a Postgres
    # advisory lock (a single named lock for the whole migration). SQLite
    # is single-writer per file and cannot be shared across replicas (the
    # helm chart's replicaGuard enforces that), so there is nothing to
    # serialize there and we run directly.
    _run_pack_centric_migration_locked(
        engine, policy_store, pack_store,
    )


# Stable 64-bit advisory-lock key for the boot-time pack-centric migration.
# Any constant works; keeping it named makes the intent legible in pg logs.
_PACK_CENTRIC_MIGRATION_LOCK_KEY = 0x6D616769_5F706B63  # "magi_pkc"


def _run_pack_centric_migration_locked(
    engine, policy_store, pack_store,
) -> None:
    """Run migrate_tenants_to_pack_centric under a cross-process lock on
    Postgres; run it directly on SQLite (single-writer, single-node)."""
    from .pack_centric_migration import migrate_tenants_to_pack_centric

    if engine.dialect.name != "postgresql":
        migrate_tenants_to_pack_centric(engine, policy_store, pack_store)
        return
    from sqlalchemy import text
    key = _PACK_CENTRIC_MIGRATION_LOCK_KEY
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": key})
        try:
            migrate_tenants_to_pack_centric(engine, policy_store, pack_store)
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
            conn.commit()


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


