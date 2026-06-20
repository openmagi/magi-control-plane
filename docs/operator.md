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
authoring (policies, presets, signup triage) + serving the install
files.

## 1. First deploy

### 1a. K8s cluster (FastAPI cloud)

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

## 2. Triaging alpha signups (per applicant)

1. Visit dashboard `https://cloud.openmagi.ai/admin/signups?status=pending`.
2. Read the application. Apply triage criteria:
   - Real firm? (gmail-only addresses → `rejected` unless firm name resolves)
   - Use case names a real Claude Code workflow they're already running?
   - Geography KO/JP-adjacent (alpha is KR-first; English-only ROW = waitlist)
3. Approve → click "승인 / Approve" with the **"provision"** checkbox
   left checked (default). The dashboard:
   1. POSTs `/admin/signups/N/status?status=approved&notes=…` (admin key)
   2. POSTs `/admin/tenants` (HMAC) with a derived `tenant_id` from the
      applicant's email (alphanumeric + 4 random chars, idempotent)
   3. POSTs `/admin/tenants/{tenant_id}/keys` (HMAC) → cleartext `mcp_…`
   4. Displays the cleartext key in a short-lived (10 min, HttpOnly)
      banner — copy it now, the backend never re-emits it
4. Email the applicant the `mcp_…` key + a link to
   `https://cloud.openmagi.ai/welcome` and the install guide.

> Prereqs for the one-click flow: the Vercel dashboard env needs **both**
> `MAGI_CP_ADMIN_API_KEY` AND `MAGI_CP_ADMIN_HMAC_SECRET`. If the HMAC
> secret is missing, the dashboard falls back to approval-only and you
> must provision manually via `kubectl exec` (see §3).

## 3. Manual provisioning (fallback)

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

Local gates re-fetch `/pubkey?kid=…` on demand and pin per-`(matter,
doc_id)`, so rotation is non-disruptive for clients already in flight.

## 5. Incident response

| Symptom | First check | Likely cause |
|---------|------------|--------------|
| 5xx surge on `/verify/*` | `kubectl logs -n magi-cp deploy/magi-cp` | PG connection saturated; bump replicas or pool size |
| HITL queue stuck pending | Vercel logs → `/admin/signups` rendering 401 | `MAGI_CP_ADMIN_API_KEY` rotated on K8s but not on Vercel |
| Gate denying everyone with `cloud unreachable` | `curl https://api.openmagi.ai/pubkey?kid=…` | DNS regression, cert expiry, or Ingress controller down |
| Signup rate-limit triggering legit users | `kubectl exec deploy/magi-cp -- magi-cp signups list-by-ip $ip` | Corporate NAT; raise IP-based limit or add per-domain bypass |
| Dashboard shows stale tenant data | check `MAGI_CP_PUBLIC_CLOUD_URL` env on Vercel | post-deploy env var not propagated; redeploy |

Rollback paths:
- K8s: `helm rollback magi-cp` (one revision back)
- Vercel: `vercel rollback <previous-deployment-url>`

## 6. Off-boarding (future GA migration)

When GA launches and free-tier closes:
1. Email all `alpha_signups.status='approved'` users with the migration window.
2. Mark their tenants `status='grace'` (60 days) — gate still allows but
   shows a deprecation banner in the dashboard.
3. After grace: tenant → `disabled`. Existing audit ledger entries preserved
   for 30 days then archived to cold storage.

## 7. Data retention checklist (monthly)

```bash
kubectl exec -n magi-cp deploy/magi-cp -- magi-cp evidence prune --older-than 90d
kubectl exec -n magi-cp deploy/magi-cp -- magi-cp signups prune \
  --status rejected --older-than 365d
```

PIPA-aligned retention windows (matches `/legal/privacy`):
- Audit ledger: tenant lifetime + 30 days
- Operational logs: 90 days
- Signup records: 3 years (rejected immediately on opt-out request)

## 8. Alternative single-binary deploy (fly.io)

`deploy/fly.toml` brings up the FastAPI cloud on fly.io for external
contributors or solo self-hosters without K8s + Vercel. Not used by the
openmagi.ai alpha pilot; kept so the OSS install path works for non-K8s
users. See README "Alternative" section.
