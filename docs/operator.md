# Operator

Deploy and operate a self-hosted `magi-control-plane`. Audience: the
engineer running the cloud.

## Topology

```
  https://<your-dashboard>          (Vercel or your own Next.js host)
                |
                |  server-side fetch
                v
  https://<your-api>                (FastAPI cloud; Docker, K8s, or fly.io)
                |
                v
  Postgres (multi-replica) or SQLite (single-replica)
```

The user's laptop calls `<your-api>` directly. The dashboard's only job
is policy authoring plus serving the install files.

## Deploy

### Option A: Docker Compose (single host)

```bash
git clone https://github.com/openmagi/magi-control-plane.git
cd magi-control-plane
cp .env.example .env
# Fill MAGI_CP_API_KEY, MAGI_CP_ADMIN_API_KEY, MAGI_CP_ADMIN_HMAC_SECRET,
# ANTHROPIC_API_KEY, OPENAI_API_KEY.
docker compose up --build
```

The compose file ships the cloud at `:8787` and the dashboard at `:3787`.

### Option B: Kubernetes

```bash
kubectl create namespace magi-cp

kubectl create secret generic magi-cp-secrets \
  --namespace magi-cp \
  --from-literal=MAGI_CP_API_KEY=$(uuidgen) \
  --from-literal=MAGI_CP_HITL_API_KEY=$(uuidgen) \
  --from-literal=MAGI_CP_ADMIN_API_KEY=$(uuidgen) \
  --from-literal=MAGI_CP_ADMIN_HMAC_SECRET=$(python3 -c 'import secrets;print(secrets.token_hex(32))') \
  --from-literal=ANTHROPIC_API_KEY=sk-ant-... \
  --from-literal=OPENAI_API_KEY=sk-...

helm install magi-cp ./charts/magi-cp \
  --namespace magi-cp \
  -f charts/magi-cp/examples/production-values.yaml \
  --set ingress.host=<your-api-host> \
  --set ingress.clusterIssuer=letsencrypt-prod
```

Multi-replica needs Postgres (`postgres.dsn` in values). Single-replica
falls back to SQLite on a PVC (`persistence.enabled: true`).

### Option C: fly.io

`deploy/fly.toml` brings up the FastAPI cloud on fly.io for solo
self-hosters without K8s.

```bash
fly auth login
fly launch --copy-config --no-deploy
# Set secrets on fly:
fly secrets set MAGI_CP_API_KEY=... MAGI_CP_ADMIN_HMAC_SECRET=...
fly deploy
```

### Dashboard (Next.js)

```bash
cd web
vercel login
vercel link --project magi-cp-dashboard
vercel env add MAGI_CP_PUBLIC_SITE_URL production    # https://<your-dashboard>
vercel env add MAGI_CP_PUBLIC_CLOUD_URL production   # https://<your-api>
vercel env add MAGI_CP_API_KEY production
vercel env add MAGI_CP_HITL_API_KEY production
vercel env add MAGI_CP_ADMIN_API_KEY production
vercel env add MAGI_CP_ADMIN_HMAC_SECRET production
vercel --prod
```

## Environment

| Variable | Where | Purpose |
|----------|-------|---------|
| `MAGI_CP_API_KEY` | Cloud + clients | Tenant key for `X-Api-Key` auth. |
| `MAGI_CP_HITL_API_KEY` | Cloud | Auth key for HITL approver UI. |
| `MAGI_CP_ADMIN_API_KEY` | Cloud + dashboard | Admin endpoints (`/policies/*`, `/admin/*`). |
| `MAGI_CP_ADMIN_HMAC_SECRET` | Cloud + integrations | HMAC over admin POST bodies. |
| `MAGI_CP_KEY_DIR` | Cloud | Path to Ed25519 keypair directory. Files are 0600. |
| `MAGI_CP_LLM_COMPILER` | Cloud | `module:factory` for NL -> IR compile. |
| `MAGI_CP_LLM_REVIEWER` | Cloud | `module:factory` for the critic review. |
| `ANTHROPIC_API_KEY` | Cloud | LLM provider for authoring. |
| `OPENAI_API_KEY` | Cloud | LLM provider for authoring. |
| `MAGI_CP_PUBLIC_CLOUD_URL` | Dashboard | Server-side fetch target. |
| `MAGI_CP_SHARE_BASE_URL` | Cloud | Public base URL stamped on share links. |

## Tenant provisioning

```bash
docker compose exec cloud magi-cp keys provision \
  --tenant-id $(uuidgen | tr A-Z a-z) \
  --plan default \
  --email <subscriber_email>
# Emits tenant_id + api_key + key_id. Copy and email the key immediately.
```

For an automated flow, use the HMAC-signed admin endpoints (see [API](./api.md)):

1. `POST /admin/tenants` with `{tenant_id, plan, expires_at}`.
2. `POST /admin/tenants/{tenant_id}/keys` returns the cleartext key.
3. Deliver the key to the subscriber.

## Key rotation

Run quarterly or after a suspected key compromise.

```bash
docker compose exec cloud magi-cp keys rotate-active \
  --reason "scheduled-2026Q3"
# emits new kid. Ledger entries sign under the new kid; signature verify
# still accepts the old kid until you retire it.

docker compose exec cloud magi-cp keys retire <old_kid> \
  --after "2026-10-01T00:00Z"
```

Local gates fetch `GET /pubkey?kid=...` on demand and pin per
`(subject, payload_hash)`, so rotation does not break in-flight tokens.

## Backups

The cloud holds three pieces of irreplaceable state:

1. The Ed25519 keypair directory (`MAGI_CP_KEY_DIR`).
2. The policies on disk (`policies/`).
3. The database (ledger plus tenant rows plus issued tokens).

```bash
# Postgres
pg_dump --no-owner --no-acl "$MAGI_CP_DSN_PG" > magi-cp-$(date +%Y-%m-%d).sql

# SQLite
cp magi-cp.sqlite magi-cp.sqlite.$(date +%Y-%m-%d).bak

# Keypair dir (always include)
tar czf keys-$(date +%Y-%m-%d).tar.gz "$MAGI_CP_KEY_DIR"
```

Restore: re-extract the keypair dir to the same path, restore the DB,
restart the cloud. `GET /ledger` should return `chain_ok: true`.

## Observability

Install the `[observability]` extra to expose Prometheus metrics:

```bash
pip install -e .[observability]
```

`/metrics` becomes available on the cloud's HTTP surface. The endpoint
is attached only inside `_build_production_app`. Bare `create_app` is
the test factory and intentionally omits metrics so the suite stays
deterministic.

Key counters:

| Metric | What it means |
|--------|---------------|
| `magi_cp_verifier_calls_total{step, status}` | One per verifier call. |
| `magi_cp_ledger_appends_total{kid}` | Ledger entries by signing key. |
| `magi_cp_hitl_pending` | Gauge of pending HITL items. |
| `magi_cp_policies_compiled_total` | Compiler runs (policy edits). |

## Authoring against in-development verifiers

`PUT /policies/{id}` fails closed on unknown or inactive `requires[].step`
refs. Two flavors of 422:

| Reason | Message contains | Operator action |
|--------|------------------|-----------------|
| inactive | `not active` | Activate under `/rules`, or prefix with `preview:`. |
| unknown | `not in catalog` | Pick from `/verifiers`, or prefix with `preview:`. |

`requires[].step = "preview:my_new_check"` is the explicit opt-in for
authoring against a verifier that does NOT exist yet. The cloud stamps
`enforcement="preview"` on the row. At runtime the route 404s and the
gate denies, by design. Drop the prefix and re-PUT once the verifier
ships.

## Incident response

| Symptom | First check | Likely cause |
|---------|-------------|--------------|
| 5xx surge on `/verify/*` | Cloud logs | PG connection saturated; bump replicas or pool size. |
| Gate denying everyone with `cloud unreachable` | `curl https://<your-api>/pubkey?kid=...` | DNS regression, cert expiry, or Ingress down. |
| Dashboard shows stale tenant data | `MAGI_CP_PUBLIC_CLOUD_URL` env | Post-deploy env var not propagated; redeploy. |
| Ledger reports `chain_ok: false` | DB integrity | Restore from the most recent clean backup. |

Rollback:

- K8s: `helm rollback magi-cp` (one revision back).
- Vercel: `vercel rollback <previous-deployment-url>`.
- Schema migrations (`scripts/migrate_*.py`): document migrations are
  one-way. Helm rollback alone is not sufficient; restore the DB from
  the backup taken before the migration ran.

## Dashboard exposure

The dashboard is designed for **operator localhost use**. Its server process
holds ambient credentials (`MAGI_CP_API_KEY`, `MAGI_CP_ADMIN_API_KEY`,
`MAGI_CP_ADMIN_HMAC_SECRET`) that the BFF injects into backend calls, so a
reachable dashboard is effectively an admin console.

- **Localhost (default):** loopback requests are trusted; no sign-in needed.
- **Exposed over a network:** any non-loopback console request must present a
  signed session cookie. Set `MAGI_CP_DASHBOARD_SESSION_SECRET` (falls back to
  `MAGI_CP_ADMIN_HMAC_SECRET`) on the dashboard server and sign in at `/login`
  with a tenant API key. With no secret configured the console **fails closed**
  and denies every non-loopback request.
- **Behind a reverse proxy:** the `Host` header is set by the proxy and can be
  spoofed to look like loopback. Set `MAGI_CP_TRUST_LOOPBACK_HEADER=0` so a
  session is required for **every** console request, and enforce authentication
  at the proxy as well. Do not rely on the loopback exception behind a proxy.
