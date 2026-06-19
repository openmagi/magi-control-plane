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
The `web/` Next.js dashboard has **no app-level auth gate** on `/hitl`
server actions (`approve`/`reject`). Anyone who can reach the dashboard
port can submit approvals; the server attaches the shared
`X-Hitl-Api-Key` on their behalf. v0 acceptable only when the dashboard
is bound to **localhost or a private VPN** with reviewer-only access.
Pre-beta: SSO / WebAuthn + per-reviewer JWT bound to the `approver` field.
This subsumes the "Approver identity" section's network-side assumption.

## Dispatch_verdict not extracted
`/citation_verify` interleaves verifier + HITL dispatch + ledger + token issue.
P6 NLI integration will want a clean seam (`dispatch_verdict(verdict, req, deps)`).
Refactor when adding NLI rather than upfront.
