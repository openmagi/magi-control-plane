# Security — v0 known limits

Explicit deferments (not blockers for first design partner; must close before public beta).

## Approver identity
`X-Hitl-Api-Key` is a single shared org key. The `approver` string in the
request body is caller-asserted (logged into ledger but not bound to the
verified principal). **Insider with HITL key can pretend to be any partner.**
v0 acceptable when reviewers are colocated and key holders are partners
themselves. Pre-beta: per-reviewer JWT or WebAuthn.

## kid rotation history
`/pubkey` returns one active kid. No grace-window for in-flight tokens signed
by the previous kid. Rotation today = brief outage. Pre-beta: `/pubkey` returns
`{keys:[…], active_kid}`.

## Multi-tenant
No `tenant_id` column. Single org per deployment. Pre-multi-tenant:
add `tenant_id` to all tables, fold into chain hash, derive from auth.

## SourceResolver
v0 routes verification through `corpus_override` for tests/offline use. P6 wires
the cloud-side `LboxResolver` so source text comes from a trusted resolver, not
the caller. Without this fix, a caller controlling `corpus_override` can have
ANY verdict signed.

## Real SourceResolver injection point
`create_app(...)` does not currently accept a `resolver` kwarg. P6 must add it.

## Postgres validation
Schema is Postgres-shaped (`BigInteger`, `native_enum=False`, `UNIQUE(prev)`),
WAL guidance for SQLite is wired. But Postgres path is not covered by CI.
Pre-beta: docker-compose with `pg-test` + parametrize the test suite over both.

## Ledger O(N) verify per `/ledger` GET
`verify_chain` rescans entire history on each call. P5 dashboard will hit this.
Fix: incremental verification with cached `(last_verified_id, last_h)` checkpoint.

## Append-only at API surface, not DB level
LedgerRepo has no `UPDATE`/`DELETE` methods, and `/ledger` has no mutating
verb. Operator with DB access can still rewrite the tail. Pre-beta: write
periodic Ed25519-signed checkpoints to S3 with object-lock so external
auditors can pin progress.

## Admin key scope (v1)
`X-Admin-Api-Key` / `MAGI_CP_ADMIN_API_KEY` gates all `/policies` CRUD
endpoints (list, get, compiled, put, patch-enabled). It is a **third shared
key**, distinct from:
  - `X-Api-Key` / `MAGI_CP_API_KEY` — verifier surface (`/citation_verify`, `/ledger`)
  - `X-Hitl-Api-Key` / `MAGI_CP_HITL_API_KEY` — review surface (`/hitl/*`)

Fail-closed if env unset (503 with no env name leak). v0 caveat: a single
shared key per role, not per-operator. Pre-beta plan: bind admin actions to
per-operator JWT and record the principal in the ledger entry that writes
each policy change (parallels the Approver identity section).

## Local pubkey cache trust anchor
The local gate (`magi-cp gate`) caches the cloud's public verify key at
`$MAGI_CP_LOCAL_DIR/pubkey-<kid>.pem`. v0 mitigations: files are written
with `O_EXCL | O_NOFOLLOW | 0o600`; loose modes cause re-fetch. A token's
`kid` is pinned across a single (matter, doc_id) — later kid drift is
rejected. **Residual**: first fetch is TOFU over HTTP; for production
require `https://` to `MAGI_CP_CLOUD_URL` and/or pre-seed the cache via the
plugin installer using a managed-settings-bundled pubkey pin. Until then,
do not deploy with `MAGI_CP_CLOUD_URL=http://...` outside loopback.

## Dashboard front-door auth
The `web/` Next.js dashboard has **no app-level auth gate** on ANY of its
mutating server actions:
- `/hitl` server actions `approve` / `reject` (v0)
- `/policies` server action `toggleEnabled` (v1)
- future v1.x policy builder writes

Anyone who can reach the dashboard port can submit any of these; the server
attaches the appropriate shared key (`X-Hitl-Api-Key` for hitl,
`X-Admin-Api-Key` for policies) on their behalf. v0/v1 acceptable only when
the dashboard is bound to **localhost or a private VPN** with operator-only
access. Pre-beta: SSO / WebAuthn + per-operator JWT, with the verified
principal bound into the `approver` field (hitl) and into the ledger entry
that records each policy change (policies). Cross-reference the
"Approver identity" and "Admin key scope" sections.

## Dispatch_verdict not extracted
`/citation_verify` interleaves verifier + HITL dispatch + ledger + token issue.
P6 NLI integration will want a clean seam (`dispatch_verdict(verdict, req, deps)`).
Refactor when adding NLI rather than upfront.
