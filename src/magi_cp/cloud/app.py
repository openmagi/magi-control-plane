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
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..verifier import (EntailmentClassifier)
from ..verifier.protocol import VerifierRegistry
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
    _resolve_llm_provider_from_env,
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
# 2026-07-03-cloud-app-modularization-design (private planning repo).
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
    core as routes_core,
    share as routes_share,
    ledger as routes_ledger,
    hitl as routes_hitl,
    llm_keys as routes_llm_keys,
    compile as routes_compile,
    verify as routes_verify,
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

    # Core routes (health, pubkey, tenant self-identity).
    routes_core.attach(app, engine, ks=ks)

    # Run-share links (create/list/get/edit/revoke + public GET).
    routes_share.attach(app, share_repo=share_repo)

    # Policy authoring: compile / interactive / handoff / dry-run.
    routes_compile.attach(app, engine, ledger=ledger,
                          verifier_registry=verifier_registry,
                          llm_compiler=llm_compiler, llm_reviewer=llm_reviewer,
                          policy_group_store=policy_group_store,
                          policy_store=policy_store)

    # LLM API-key dashboard surface (status / write+hot-reload / test).
    routes_llm_keys.attach(app, llm_keys_lock=llm_keys_lock)

    # Verification: catalog + /verify/{step} + /verify_inline + citation_verify.
    routes_verify.attach(app, engine, ledger=ledger, hitl=hitl, ks=ks,
                         kid=kid, chain_lock=chain_lock,
                         verifier_registry=verifier_registry,
                         custom_verifier_store=custom_verifier_store,
                         nli_classifier=nli_classifier,
                         llm_compiler=llm_compiler)

    # HITL review queue (detail/list/approve/reject).
    routes_hitl.attach(app, hitl=hitl, ledger=ledger, ks=ks, kid=kid,
                       chain_lock=chain_lock)

    # Ledger + metrics-summary (audit views, integrity, aggregate).
    routes_ledger.attach(app, engine, ledger=ledger,
                         policy_store=policy_store, pack_store=pack_store,
                         script_store=script_store,
                         policy_group_store=policy_group_store)

    # ── /policies CRUD (v1) ──────────────────────────────────────
    # ── Codex runtime adapter (P4) - coverage + per-tenant runtime ────
    # Registered BEFORE _attach_policy_routes so the specific
    # `/policies/{id}/coverage/{runtime}` route is matched ahead of that
    # helper's greedy `/policies/{policy_id:path}` catch-all.
    routes_runtime.attach(app, engine,
                           policy_store=policy_store,
                           pack_store=pack_store,
                           policy_group_store=policy_group_store)

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
    # 2026-06-30-pack-centric-session-scoped-runtime (private planning repo).
    # P2 folds the ``/session/{id}/resolved`` gate-cache feeder into
    # the same attach helper so the pack + policy stores share one
    # closure (the resolver reads BOTH to fold pack membership into a
    # (event, matcher) -> policies map for the gate binary cache).
    routes_session_pack.attach(
        app, engine,
        pack_store=pack_store,
        pack_store_lock=pack_store_lock,
        policy_store=policy_store,
        policy_group_store=policy_group_store,
    )

    return app


# _run_command_allowed moved to config.py (alongside the other runtime env
# gates). Re-exported so the policy + script_store groups still in this file
# resolve it until they move to routes/ in the same change.
from ..config import _run_command_allowed  # noqa: E402,F401


def _resolve_llm_provider_optional(env_var: str) -> "object | None":
    """Boot-safe wrapper around ``_resolve_llm_provider_from_env``.

    The LLM compiler / reviewer are OPTIONAL — ``/policies/compile`` returns
    503 when absent, and the rest of the control plane (dashboard, policies,
    packs, HITL, ledger, gate compile) does not need them. So a provider that
    is wired-but-unconfigured must NOT take the whole cloud down.

    This is the fix for the self-host boot crash: the served
    docker-compose.yml defaults ``MAGI_CP_LLM_COMPILER`` to
    ``anthropic_default`` (and the reviewer to ``openai_default``), but a
    fresh install has no ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY``, so the
    eager key check in the provider ``__init__`` raised ``LlmProviderError``
    at ``uvicorn --factory`` boot → the container crash-looped (restarting,
    unhealthy). Treat any resolution failure (missing key, bad import path,
    factory raise) as "no provider configured": log a warning and return
    None so the cloud boots and ``/policies/compile`` reports its honest 503
    until the operator adds a key (via .env or the Settings key store) and
    restarts.
    """
    try:
        return _resolve_llm_provider_from_env(env_var)
    except Exception as e:  # pragma: no cover - exercised via boot test
        import logging
        logging.getLogger(__name__).warning(
            "magi-cp: %s is set but its provider could not be constructed "
            "(%s); /policies/compile will return 503 until it is configured. "
            "Add the provider's API key (e.g. ANTHROPIC_API_KEY / "
            "OPENAI_API_KEY in .env, or via the dashboard Settings key "
            "store) and restart the cloud service.",
            env_var, e,
        )
        return None


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
        llm_compiler=_resolve_llm_provider_optional("MAGI_CP_LLM_COMPILER"),
        llm_reviewer=_resolve_llm_provider_optional("MAGI_CP_LLM_REVIEWER"),
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


