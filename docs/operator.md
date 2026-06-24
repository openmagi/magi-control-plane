# Operator runbook

Internal playbook for running the alpha pilot. Audience: Kevin (and the
small alpha-ops team when it grows). Not user-facing.

## Topology

Two surfaces, two hostnames, one user-facing URL:

```
                    cloud.openmagi.ai (CNAME → Vercel)
                                 │
                                 ▼
                       Next.js dashboard (web/)
                                 │
            server-side fetch (in-cluster or public)
                                 │
                                 ▼
                  api.openmagi.ai (A → K8s Ingress)
                                 │
                                 ▼
                     FastAPI cloud (charts/magi-cp)
                                 │
                                 ▼
                  Postgres (multi-replica) or SQLite (single)
```

The runtime gate on the user's laptop calls `api.openmagi.ai`
directly — never via the dashboard. The dashboard's only job is
authoring (policies, presets) + serving the install files.

Tenant provisioning is automatic: the Clawy Stripe webhook calls
`POST /admin/tenants` on subscription start (HMAC-signed; see
`src/magi_cp/cloud/app.py` `_attach_admin_tenant_routes`). There is no
operator triage queue — see `docs/clawy-integration.md` for the
end-to-end contract.

## 1. First deploy

### 1. Paste-and-go (fly.io interim + Vercel, recommended for now)

K8s prod cluster (204.168.161.172) is currently unreachable from the
dev laptop; backend rides fly.io until the network path is fixed. One
script handles everything:

```bash
fly auth login   # once
vercel login     # once
./scripts/deploy-alpha.sh
```

The script:
1. Generates secrets at `.deploy/secrets.env` (gitignored, 0600).
   Pauses so you can paste `ANTHROPIC_API_KEY` + `OPENAI_API_KEY`.
2. Creates fly.io app `magi-cp` in `nrt`, allocates dedicated IPs,
   provisions 3GiB `magi_data` volume, stages secrets, deploys.
3. Prints DNS records (A/AAAA for `api`, CNAME for `cloud`) and
   waits for you to add them at the registrar.
4. Adds Let's Encrypt cert for `api.openmagi.ai` via fly.
5. Vercel links the dashboard, pushes all 7 env vars, deploys to
   prod, registers `cloud.openmagi.ai` domain.
6. Smoke-tests `/healthz` on api + `/welcome` on dash.

Idempotent — re-run on any step failure; existing fly app + Vercel
project + secrets are picked up.

### 1a. K8s cluster (FastAPI cloud)  ← future migration

When the prod cluster is reachable + `helm` installed:

```bash
# In the existing magi-control-plane namespace on Kevin's K8s cluster.
kubectl create namespace magi-cp

# Sealed secret with all keys at once. Generate locally, then seal.
kubectl create secret generic magi-cp-secrets \
  --namespace magi-cp \
  --from-literal=MAGI_CP_API_KEY=$(uuidgen) \
  --from-literal=MAGI_CP_HITL_API_KEY=$(uuidgen) \
  --from-literal=MAGI_CP_ADMIN_API_KEY=$(uuidgen) \
  --from-literal=MAGI_CP_ADMIN_HMAC_SECRET=$(python3 -c 'import secrets;print(secrets.token_hex(32))') \
  --from-literal=ANTHROPIC_API_KEY=sk-ant-… \
  --from-literal=OPENAI_API_KEY=sk-…

# Apply cert-manager Issuers once per cluster (skip if already present).
kubectl apply -f charts/magi-cp/examples/cert-manager-issuer.yaml

# Helm install with worked production values.
helm install magi-cp ./charts/magi-cp \
  --namespace magi-cp \
  -f charts/magi-cp/examples/production-values.yaml \
  --set ingress.host=api.openmagi.ai \
  --set ingress.clusterIssuer=letsencrypt-prod

# DNS: point api.openmagi.ai A → Ingress LoadBalancer IP.
kubectl get svc -n ingress-nginx ingress-nginx-controller \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}'

# Verify (after DNS + cert propagation, ~5min).
curl -fsS https://api.openmagi.ai/healthz
```

Multi-replica needs Postgres (`postgres.dsn` in values). Single-replica
falls back to SQLite on the PVC (`persistence.enabled: true`).

### 1b. Vercel (Next.js dashboard)

```bash
cd web
vercel login
vercel link --project magi-cp-dashboard
vercel env add MAGI_CP_PUBLIC_SITE_URL production    # → https://cloud.openmagi.ai
vercel env add MAGI_CP_PUBLIC_CLOUD_URL production   # → https://api.openmagi.ai
vercel env add MAGI_CP_API_KEY production            # mirror from K8s magi-cp-secrets
vercel env add MAGI_CP_HITL_API_KEY production
vercel env add MAGI_CP_ADMIN_API_KEY production
vercel env add MAGI_CP_ADMIN_HMAC_SECRET production
vercel --prod
vercel domains add cloud.openmagi.ai
# DNS: CNAME cloud.openmagi.ai → cname.vercel-dns.com
```

The dashboard talks to the K8s cloud via the public api.openmagi.ai
hostname. If you'd rather keep that traffic in-cluster, Vercel
unfortunately cannot reach private networks — either run the dashboard
on-cluster too (separate Helm chart, not shipped) or leave the public
api hostname as the integration point.

## 2. Tenant provisioning

Automatic on Clawy Pro+ subscription start. The Clawy Stripe webhook:

1. POSTs `/admin/tenants` (HMAC-signed with `MAGI_CP_ADMIN_HMAC_SECRET`)
   with `{tenant_id, plan: "pro_plus"}`
2. POSTs `/admin/tenants/{tenant_id}/keys` (HMAC) → cleartext `mcp_…`
3. Emails the subscriber the key + install link

Wire contract details: `docs/clawy-integration.md`. The cloud's HMAC
contract: `src/magi_cp/cloud/app.py` `_attach_admin_tenant_routes`.

## 3. Manual provisioning (operator fallback for self-host or recovery)

```bash
kubectl exec -n magi-cp deploy/magi-cp -- \
  magi-cp keys provision \
    --tenant-id $(uuidgen | tr A-Z a-z) \
    --plan alpha \
    --email <applicant_email>
# emits tenant_id + api_key + key_id; copy and email immediately
```

## 4. Key rotation (planned quarterly)

```bash
kubectl exec -n magi-cp deploy/magi-cp -- magi-cp keys rotate-active \
  --reason "scheduled-2026Q3"
# emits new kid; ledger entries sign under the new kid but
# verify old kid until you retire it
kubectl exec -n magi-cp deploy/magi-cp -- magi-cp keys retire <old_kid> \
  --after "2026-10-01T00:00Z"
```

Local gates re-fetch `/pubkey?kid=…` on demand and pin per-`(subject,
payload_hash)`, so rotation is non-disruptive for clients already in flight.

## 5. Incident response

| Symptom | First check | Likely cause |
|---------|------------|--------------|
| 5xx surge on `/verify/*` | `kubectl logs -n magi-cp deploy/magi-cp` | PG connection saturated; bump replicas or pool size |
| Pro+ subscribers not auto-provisioned | Clawy logs for the Stripe webhook | HMAC secret rotated on cloud but not on Clawy; check signature mismatch |
| Gate denying everyone with `cloud unreachable` | `curl https://api.openmagi.ai/pubkey?kid=…` | DNS regression, cert expiry, or Ingress controller down |
| Dashboard shows stale tenant data | check `MAGI_CP_PUBLIC_CLOUD_URL` env on Vercel | post-deploy env var not propagated; redeploy |

Rollback paths:
- K8s: `helm rollback magi-cp` (one revision back)
- Vercel: `vercel rollback <previous-deployment-url>`
- PR4 schema migration (`scripts/migrate_pr4_drop_legacy.py`):
  `helm rollback` ALONE is NOT sufficient. The script DROPs `matter` /
  `doc_id` columns from `hitl_item`; reverting the application code
  re-introduces the ORM columns but `Base.metadata.create_all` is
  `CREATE TABLE IF NOT EXISTS` and will NOT re-add the dropped columns
  — every `/hitl` read crashes with `no such column: matter`. The only
  safe rollback is a DB restore from a backup taken BEFORE the script
  ran. Take the backup as the first step of cut-over:

  ```bash
  # Postgres
  pg_dump --no-owner --no-acl "$MAGI_CP_DSN_PG" > pr4-pre-drop.sql

  # SQLite
  cp magi-cp.sqlite magi-cp.sqlite.pr4-pre-drop.bak
  ```

## 5a. PR2 → PR4 deploy ordering (one-time, post-D45 cut-over)

PR2 changed the cloud's `_issue_token` body shape from legacy
`(matter, doc_hash)` to canonical `(subject, payload_hash)`. PR4
removes the legacy mirror entirely. The local gate (`magi_cp.local.gate`)
matches ONLY on the canonical fields, so any PR4-era cloud paired with
a pre-PR2 gate binary will fail-closed silently on every PreToolUse
sentinel.

Cut-over checklist:

1. Roll gate binaries forward to the PR4 release on every user
   workstation BEFORE flipping the cloud to PR4. The install script
   (`docs/install.md`) handles this for fresh installs; existing
   installs need `magi-cp install --upgrade`.
2. If a workstation may still have legacy tokens cached in
   `~/.magi-cp/local/wal.jsonl`, set
   `MAGI_CP_ACCEPT_LEGACY_TOKEN_SHAPE_UNTIL=<unix_ts>` to the end of
   the deploy window. Tokens carrying only legacy `matter`/`doc_hash`
   body fields will match for the window, after which the gate flips
   back to strict canonical without operator action. Default-OFF.
3. Run `scripts/migrate_pr4_drop_legacy.py --dry-run` against prod
   DB to confirm `subject IS NULL` count == 0. If non-zero, re-run
   `scripts/migrate_pr3_backfill.py` first.
4. Take a DB backup (see Rollback paths above), then run the
   migration with `--yes`.

## 6. Off-boarding (future GA migration)

When GA launches and free-tier closes:
1. Email all `tenants.status='active' AND plan='alpha'` users with the migration window.
2. Mark their tenants `status='grace'` (60 days) — gate still allows but
   shows a deprecation banner in the dashboard.
3. After grace: tenant → `disabled`. Existing audit ledger entries preserved
   for 30 days then archived to cold storage.

## 7. Data retention checklist (monthly)

```bash
kubectl exec -n magi-cp deploy/magi-cp -- magi-cp evidence prune --older-than 90d
```

PIPA-aligned retention windows (matches `/legal/privacy`):
- Audit ledger: tenant lifetime + 30 days
- Operational logs: 90 days
- Pro+ subscription records: while active + 3 years (deleted on opt-out)

## 8. Alternative single-binary deploy (fly.io)

`deploy/fly.toml` brings up the FastAPI cloud on fly.io for external
contributors or solo self-hosters without K8s + Vercel. Not used by the
openmagi.ai alpha pilot; kept so the OSS install path works for non-K8s
users. See README "Alternative" section.
