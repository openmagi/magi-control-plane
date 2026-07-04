# Operator

Deploy and operate a self-hosted `magi-control-plane`. Audience: the
engineer running the cloud.

## Topology

```
  https://<your-dashboard>          (Vercel or your own Next.js host)
                |
                |  server-side fetch
                v
  https://<your-api>                (FastAPI cloud; Docker or K8s)
                |
                v
  Postgres (multi-replica) or SQLite (single-replica)
```

The user's laptop calls `<your-api>` directly. The dashboard's only job
is policy authoring plus serving the install files.

## Deploy

### Option A: Docker Compose (single host)

For most self-hosters the one-line installer is the fastest path: it
pulls the published images and brings up both the cloud and the dashboard
locally. See [Install](./install.md).

To run from a repo clone instead:

```bash
git clone https://github.com/openmagi/magi-control-plane.git
cd magi-control-plane
cp .env.example .env
# Fill MAGI_CP_API_KEY, MAGI_CP_ADMIN_API_KEY, MAGI_CP_ADMIN_HMAC_SECRET,
# ANTHROPIC_API_KEY, OPENAI_API_KEY.
docker compose up --build
```

The repo-root compose file builds the cloud on `:8787` (plus an optional
`postgres` profile). The dashboard is a separate deploy: either the
served self-host template (which the installer uses; it binds the
dashboard to `127.0.0.1:3000`) or the Vercel path below.

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
| `MAGI_CP_AUTO_ACTIVATE_PACKS` | Local gate | Comma-separated pack ids auto-activated on `SessionStart`. |

## Tenant provisioning

The single-operator installer generates your key locally, so a fresh
self-host has no tenants to provision. Multi-tenant deploys mint keys via
the HMAC-signed admin endpoints (see [API](./api.md)):

1. `POST /admin/tenants` with `{tenant_id, plan, expires_at}`.
2. `POST /admin/tenants/{tenant_id}/keys` returns the cleartext key.
3. Deliver the key to the subscriber.

## Key rotation

Run quarterly or after a suspected key compromise. `rotate` mints a new
active key and keeps prior keys so in-flight tokens still verify; revoke
the old key once its tokens have expired.

```bash
docker compose exec cloud magi-cp keys rotate
# Emits the new kid. Ledger entries sign under it; signature verify still
# accepts the old kid until you revoke it.

# Wait at least TOKEN_TTL_SECONDS (600s), then:
docker compose exec cloud magi-cp keys revoke <old_kid>
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

The counters carry a `tenant_id` label and `/metrics` shares the API
port (it cannot be network-isolated from the API by path), so it is
**fail-closed by default**: an unauthenticated scrape gets `401` unless
you configure one of:

- `MAGI_CP_METRICS_TOKEN=<token>` and have Prometheus send
  `Authorization: Bearer <token>` (the recommended path), or
- `MAGI_CP_METRICS_PUBLIC=1` as an explicit opt-out when you isolate
  `/metrics` at the network layer (private listener, `networkPolicy.enabled`
  in the Helm chart, scrape-only network). Do not set this on a network
  where untrusted clients can reach `:8787`.

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

magi-cp is **self-host, single-operator** software. The dashboard is designed
for one operator running it on their own machine. Its server process holds
ambient credentials (`MAGI_CP_API_KEY`, `MAGI_CP_ADMIN_API_KEY`,
`MAGI_CP_ADMIN_HMAC_SECRET`) that the BFF injects into backend calls, so a
reachable dashboard is effectively an admin console. Sign-in at `/login`
accepts any valid tenant API key and unlocks the operator console: this is
BY DESIGN for the single-operator model (the operator IS the tenant). It is
not a multi-tenant privilege boundary, and the console must never be exposed
as a shared multi-user surface.

- **Localhost (default): no sign-in.** A request with a loopback `Host` opens
  the console without a login. This is the common single-operator case.
- **The security boundary is the network bind, not the Host header.** The
  `docker-compose.yml` template binds the dashboard to `127.0.0.1` only, so it
  is physically unreachable from other machines and the `Host` header cannot be
  spoofed from outside (this is what makes the loopback trust safe, and is the
  proper fix for WEB-1). `localhost:<port>` on the host still works.
- **Exposing it deliberately (LAN / remote / reverse proxy):** change the
  dashboard port mapping to `0.0.0.0` (`"${DASHBOARD_PORT:-3000}:3000"`) OR
  front it with a proxy, AND set `MAGI_CP_TRUST_LOOPBACK_HEADER=0` so a signed
  session is required for every request, plus `MAGI_CP_DASHBOARD_SESSION_SECRET`
  (falls back to `MAGI_CP_ADMIN_HMAC_SECRET`). Sign in at `/login` and enforce
  auth at the proxy too. With no secret configured the console denies every
  request (fail-closed). Do not rely on the loopback exception once the console
  is reachable off-host.
- Note: the Next.js standalone server injects `x-forwarded-*` headers on every
  request even with no proxy, so those headers are NOT used as a proxy signal.
