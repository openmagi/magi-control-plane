# Operator runbook

Internal playbook for running the alpha pilot. Audience: Kevin (and the
small alpha-ops team when it grows). Not user-facing.

## 1. First deploy (fly.io path)

```bash
cd deploy
fly launch --copy-config --no-deploy --name magi-cp
fly secrets set \
  MAGI_CP_API_KEY=$(uuidgen) \
  MAGI_CP_HITL_API_KEY=$(uuidgen) \
  MAGI_CP_ADMIN_API_KEY=$(uuidgen) \
  MAGI_CP_ADMIN_HMAC_SECRET=$(python3 -c 'import secrets;print(secrets.token_hex(32))') \
  ANTHROPIC_API_KEY=sk-ant-… \
  OPENAI_API_KEY=sk-…
fly vol create magi_data --region nrt --size 3
fly deploy
fly cert add cloud.openmagi.ai          # Let's Encrypt via fly.io
```

Point `cloud.openmagi.ai` A/AAAA at the fly.io app IPs (`fly ips list`).

DNS is propagated in ~5 minutes; cert issuance takes another ~30 seconds
once propagation completes. Verify with:

```bash
curl -fsS https://cloud.openmagi.ai/healthz
```

The dashboard frontend deploys separately (Vercel-style) and reads
`MAGI_CP_PUBLIC_CLOUD_URL=https://cloud.openmagi.ai` from its env.

## 2. Triaging alpha signups (per applicant)

1. Visit dashboard `/admin/signups?status=pending`.
2. Read the application. Apply triage criteria:
   - Real firm? (gmail-only addresses go to `rejected` unless firm name resolves)
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
   `https://cloud.openmagi.ai/welcome` + `https://cloud.openmagi.ai/docs/install`.

> Prereqs for the one-click flow: the dashboard process needs **both**
> `MAGI_CP_ADMIN_API_KEY` AND `MAGI_CP_ADMIN_HMAC_SECRET` env vars set;
> the cloud already has these from `magi-cp-secrets`. If the HMAC secret
> is missing, the dashboard falls back to approval-only and you must
> provision manually via `fly ssh console -C "magi-cp keys provision …"`.

## 3. Key rotation (planned quarterly)

```bash
fly ssh console
$ magi-cp keys rotate-active --reason "scheduled-2026Q3"
# emits new kid; dashboard /ledger entries will sign under the new kid
# but verify old kid until rotated-out
$ magi-cp keys retire <old_kid> --after "2026-10-01T00:00Z"
```

Local gates re-fetch `/pubkey?kid=…` on demand and pin per-`(matter, doc_id)`,
so rotation is non-disruptive for running clients.

## 4. Incident response

| Symptom | First check | Likely cause |
|---------|------------|--------------|
| 5xx surge on `/verify/*` | `fly logs` → look for `cloud unreachable` warnings | PG connection saturated; bump `replicaCount` or pg pool size |
| HITL queue stuck pending | `/admin/signups` page renders 401 → admin key not configured | rotate the admin key + redeploy with secret |
| Gate denying everyone with `cloud unreachable` | run `curl /pubkey?kid=…` from a customer-adjacent network | DNS or firewall regression; check fly.io status |
| Signup rate-limit triggering legit users | `select count(*) from alpha_signups where source_ip=$ip` | someone behind a corporate NAT; raise IP-based limit or add per-domain bypass |

Rollback: fly.io deploys are atomic. `fly releases` → `fly deploy --image <prev>`.

## 5. Off-boarding (future GA migration)

When GA launches and free-tier closes:
1. Email all `alpha_signups.status='approved'` users with the migration window.
2. Mark their tenants `status='grace'` (60 days) — gate still allows but
   shows a deprecation banner in the dashboard.
3. After grace: tenant → `disabled`. Existing audit ledger entries preserved
   for 30 days then archived to cold storage.

## 6. Data retention checklist (monthly)

```bash
fly ssh console
$ magi-cp evidence prune --older-than 90d
$ magi-cp signups prune --status rejected --older-than 365d
```

PIPA-aligned retention windows (matches `/legal/privacy`):
- Audit ledger: tenant lifetime + 30 days
- Operational logs: 90 days
- Signup records: 3 years (rejected immediately on opt-out request)
