# cloud/app.py modularization (design)

Status: **DESIGN.** Behavior-preserving refactor, no functional change.
Author: Kevin
Date: 2026-07-03

## 1. Why

`src/magi_cp/cloud/app.py` is 6642 lines: the whole FastAPI surface (~90
routes), all request models, auth deps, middleware, serialization helpers,
and `create_app`. It is the single biggest maintenance liability in the repo.
This splits it into focused modules WITHOUT changing any behavior (same
routes, same responses, same tests green).

## 2. Current structure (the seams already exist)

The file is already half-modular. Route groups are registered by
`_attach_<group>_routes(app, deps...)` closures:

| function | lines (approx) | routes |
| --- | --- | --- |
| `create_app` | 705-2445 (~1740) | middleware + ~30 INLINE routes (share, compile, verify, hitl, ledger, llm-keys, healthz, pubkey, tenants/me) + the 11 `_attach_*` calls |
| `_attach_policy_routes` | 2819-4603 (~1800) | /policies, /policy-packs |
| `_attach_session_pack_routes` | 4604-5093 | session packs |
| `_attach_runtime_routes` | 5094-5290 | /tenants/{}/runtime |
| `_attach_admin_tenant_routes` | 5291-5432 | /admin/tenants |
| `_attach_catalog_routes` | 5460-5680 | /catalog |
| `_attach_check_evidence_routes` | 5681-5739 | /checks, /evidence-types |
| `_attach_payload_schema_routes` | 5740-5773 | /payload-schemas |
| `_attach_verifier_descriptor_routes` | 5774-5853 | /verifier-descriptors |
| `_attach_custom_verifier_routes` | 5922-6018 | custom verifiers |
| `_attach_script_store_routes` | 6073-6214 | /scripts |
| `_attach_endpoint_routes` | 6215-6370 | /endpoints |

Shared, currently module-level in app.py:
- Auth deps: `_check_key`, `require_api_key`, `require_hitl_key`,
  `require_admin_key`, `require_tenant_auth`, `_resolve_tenant_id_from_request`
  (632-704). Referenced by every route group via `Depends(...)`.
- Middleware: `MaxBodyMiddleware`, `TokenBucketLimiter`, `_BodyTooLarge`,
  `_bounded_regex_search`, `_json_response` (519-631).
- Serialization/token helpers: `_serialize_policy_for_api`,
  `_deserialize_policy_from_api`, `_compile_with_sha`, `_compile_set_with_sha`,
  `_issue_token`, `_frame_meta_for_ledger`, `_iso_ts`, `_citations_summary`,
  `_enforcement_label`, `_canonical_json_bytes`, `_synth_subject_and_hash`
  (90-152, 2446-2690).
- Request models: scattered (153-511, 2691-2804, 5854-5921, 6019-6072).

The `_attach_*` closures are cleanly parameterized (they take `app, engine,
store, ...` and import DB deps locally), so moving them is a near-pure
cut-paste. The ONLY coupling is that route decorators use the module-level
`require_*` deps; those must live somewhere both app.py and the new route
modules can import without a cycle.

## 3. Target layout

```
src/magi_cp/cloud/
  app.py              # create_app() ONLY: build app, add middleware, call
                      # every routes.<group>.attach(app, deps). ~250 lines.
  deps.py             # auth deps (require_*, _check_key, _resolve_tenant_id)
  middleware.py       # MaxBodyMiddleware, TokenBucketLimiter, _bounded_regex_search
  schemas.py          # all shared Pydantic request models
  serialization.py    # policy (de)serialize + compile-with-sha + token issue
  routes/
    __init__.py
    share.py          # attach(app, ...) for /v1/runs/share, /share/run
    compile.py        # /policies/compile*, /handoff-context, /dry-run
    verify.py         # /verify, /verify_inline, /citation_verify, /verifiers
    hitl.py           # /hitl/*
    ledger.py         # /ledger/*, /metrics/summary
    llm_keys.py       # /admin/llm-keys
    policy.py         # the 1800-line policy/pack group
    session_pack.py   runtime.py   admin_tenant.py   catalog.py
    check_evidence.py payload_schema.py verifier_descriptor.py
    custom_verifier.py script_store.py endpoint.py
```

Each `routes/<group>.py` exposes `def attach(app, *deps) -> None` (renamed
from `_attach_<group>_routes` but same body). app.py imports and calls them.

## 4. Sequencing (small, individually-verifiable, behavior-preserving PRs)

Every PR: pure move, run the full backend suite, assert same pass count
(no test edits beyond import paths). No PR mixes a move with a behavior change.

- **PR1 (foundation):** extract `deps.py` + `middleware.py` +
  `serialization.py` + `schemas.py` from app.py. app.py imports from them.
  This breaks the import cycle so later route modules can import deps. Biggest
  single risk (shared symbols), so it goes first and alone.
- **PR2:** create `routes/` package; move the 11 ALREADY-parameterized
  `_attach_*` groups (policy, session_pack, runtime, admin_tenant, catalog,
  check_evidence, payload_schema, verifier_descriptor, custom_verifier,
  script_store, endpoint) into `routes/<group>.py`. Each is a cut-paste +
  `attach(app, ...)` rename; app.py calls them. This alone drops app.py from
  ~6642 to ~2500. Can be split into 2-3 PRs by group if the diff is too big
  to review at once (policy.py alone is 1800 lines, so: PR2a policy, PR2b the
  other 10).
- **PR3:** extract create_app's INLINE route groups (share, compile, verify,
  hitl, ledger, llm_keys) into `routes/<group>.py` with the same `attach`
  pattern, threading their closed-over deps as parameters. app.py's
  create_app shrinks to middleware + attach calls (~250 lines).
- **PR4 (cleanup):** move the last stragglers (healthz/pubkey/tenants-me into
  a tiny `routes/core.py`), dedupe imports, final size check.

## 5. Risks + guards

- **Import cycles.** deps/serialization must NOT import app.py. Route modules
  import from deps/schemas/serialization + the stores, never from app.py.
  create_app imports route modules. One-directional. PR1 establishes this.
- **Test imports.** Some tests import helpers from `magi_cp.cloud.app`
  (e.g. `_serialize_policy_for_api`). Keep backward-compat re-exports in
  app.py (`from .serialization import _serialize_policy_for_api  # noqa`) so
  no test import breaks. Audit with `grep -rn "from magi_cp.cloud.app import"`.
- **Behavior drift.** The guard is the test suite: each PR must show the SAME
  pass/fail count as before (currently 2163 passed, 5 pre-existing failures).
  A changed count = the move altered behavior = revert.
- **Decorator order / middleware order.** Middleware is added in create_app in
  a specific order (limiter, max-body, CORS). Preserve exact order. Route
  registration order does not matter for FastAPI path matching (exact paths).
- **No functional change rule.** If a move surfaces a latent bug, fix it in a
  SEPARATE follow-up PR, never bundled into a move (keeps the move reviewable
  as a no-op).

## 6. Non-goals

- No new routes, no response-shape changes, no auth changes, no dependency
  changes.
- Not splitting the stores (policy_store/pack_store/etc.), which are already
  separate modules.
- Not touching `_build_production_app` beyond following the imports.
