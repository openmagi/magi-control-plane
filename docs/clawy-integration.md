# Clawy Pro+ ↔ magi-control-plane integration

Authoritative contract between Clawy's Stripe webhook and the hosted
magi-control-plane instance.

Audience: Clawy backend engineers wiring the Pro+ subscription flow.

## TL;DR

```
Stripe customer.subscription.created (Pro+ tier)
        ↓
Clawy webhook handler
        ↓ (HMAC over body)
POST cloud.api/admin/tenants  { tenant_id, plan: "pro_plus", expires_at }
        ↓ (HMAC over empty body)
POST cloud.api/admin/tenants/{tenant_id}/keys  {}
        ↓
Email subscriber the cleartext mcp_… key + link to /install
```

End-to-end target: under 30 seconds from Stripe charge succeeded to
subscriber email in inbox.

## Hostnames

| Env | Cloud (API) | Dashboard |
|-----|-------------|-----------|
| Prod | `https://api.openmagi.ai` | `https://cloud.openmagi.ai` |
| Staging | `https://api-staging.openmagi.ai` | `https://cloud-staging.openmagi.ai` |

Clawy reads these from `MAGI_CP_CLOUD_URL` and `MAGI_CP_PUBLIC_SITE_URL`.

## Auth

All `/admin/tenants*` routes are HMAC-SHA256 signed. The signature is
hex-encoded and sent in header `x-magi-signature`:

```
signature = hmac_sha256(MAGI_CP_ADMIN_HMAC_SECRET, raw_request_body).hex()
```

The body MUST be the exact bytes signed. Serialise JSON once, hash that
buffer, send that same buffer as the request body — do not re-serialise
between signing and sending.

`MAGI_CP_ADMIN_HMAC_SECRET` is shared 1:1 between clawy and the hosted
cloud's K8s `magi-cp-secrets` Secret. Rotate via:

1. Generate new secret
2. Set as `MAGI_CP_ADMIN_HMAC_SECRET_NEXT` in the cloud (dual-accept window)
3. Update Clawy env to use the new secret
4. Remove the old secret from the cloud (single-accept again)

(Dual-accept is on the magi-cp roadmap; until then plan a 30-second
window where both apps redeploy near-simultaneously.)

## Endpoint reference

### POST /admin/tenants

Create or look up a tenant. Idempotent — re-POSTing with the same
`tenant_id` returns the existing record without creating a duplicate.

Request:
```json
{
  "tenant_id": "t-cus-NhP3oABC123",
  "plan": "pro_plus",
  "expires_at": 1769990400
}
```

- `tenant_id`: `^[A-Za-z0-9_\-:]+$`, 1–64 chars. Derive deterministically
  from Stripe `customer.id` (recommended: `"t-" + customer.id.toLowerCase()`).
- `plan`: free-form string used by the dashboard for display + retention
  rules. Use `"pro_plus"` for Pro+ subscribers.
- `expires_at`: Unix timestamp (seconds). Optional — null means no
  expiry. Set to `subscription.current_period_end + grace_period_seconds`
  so the gate fails closed promptly after a missed renewal.

Response:
```json
{
  "id": "t-cus-NhP3oABC123",
  "status": "active",
  "plan": "pro_plus",
  "expires_at": 1769990400
}
```

Errors:
- 401 — HMAC signature mismatch
- 422 — invalid tenant_id format
- 503 — `MAGI_CP_ADMIN_HMAC_SECRET` not configured on cloud (operator fault)

### POST /admin/tenants/{tenant_id}/keys

Issue a fresh API key for the tenant. **NOT idempotent** — every call
returns a new key. Clawy MUST track whether a key has already been
issued for this subscription (e.g. row in `magi_cp_keys` table keyed on
Stripe subscription id) and skip if already provisioned.

Request body: `{}` (still HMAC-signed over the literal `"{}"` bytes).

Response:
```json
{
  "id": 42,
  "tenant_id": "t-cus-NhP3oABC123",
  "api_key": "mcp_3F8K9-fullCleartextSecret-DoNotLog",
  "prefix": "mcp_3F8K9"
}
```

The cleartext `api_key` is returned ONCE. Clawy MUST:
1. Email it to the subscriber immediately
2. NOT log it anywhere (mask via `prefix` if structured logging is
   wired through the response object)
3. NOT store cleartext at rest (store `prefix` for support lookup; if
   the user loses the key, issue a new one via this endpoint and
   revoke the old via the next endpoint)

### POST /admin/tenants/{tenant_id}/keys/{key_id}/revoke

Revoke a previously-issued key. Use when:
- Subscriber lost their key + clawy issues a replacement
- Subscription cancelled with revocation policy (vs grace-period suspend)

Request body: `{}` (HMAC over `"{}"`).

Response: `{ "id": 42, "revoked": true }`

### POST /admin/tenants/{tenant_id}/suspend

Soft-disable the tenant (gate denies, key remains issued). Use on
Stripe `subscription.deleted` / payment failure beyond grace period.

Request:
```json
{ "reason": "stripe-subscription-cancelled" }
```

Backend keeps the audit ledger entries; chain stays valid.

### POST /admin/tenants/{tenant_id}/reactivate

Inverse of suspend. Use on payment recovery.

Request body: `{}` (HMAC over `"{}"`).

## Stripe → magi-cp event map

| Stripe event | magi-cp call | Notes |
|--------------|--------------|-------|
| `customer.subscription.created` (Pro+ price) | createTenant → issueKey → email | Only act on Pro+ price IDs; ignore other tiers |
| `customer.subscription.updated` status=active | createTenant (idempotent) | Reaffirms tenant; no new key |
| `customer.subscription.updated` status=past_due | (no-op until grace expires) | Grace handled in Clawy's billing logic |
| `customer.subscription.deleted` | suspend(reason="stripe-cancelled") | Audit ledger preserved |
| `invoice.payment_succeeded` (recurring) | createTenant w/ new expires_at | Bumps the fail-closed deadline |
| `invoice.payment_failed` past grace | suspend(reason="payment-failed") | |
| Subscription reactivated after suspension | reactivate | |

## Reference impl (TypeScript, Clawy side)

```ts
// /api/billing/magi-cp.ts inside the Clawy backend

import { createHmac } from "node:crypto"

const CLOUD = process.env.MAGI_CP_CLOUD_URL!          // https://api.openmagi.ai
const SECRET = process.env.MAGI_CP_ADMIN_HMAC_SECRET! // shared with cloud
const PROVISIONED_TABLE = "magi_cp_provisioned"        // Clawy-side state

async function magiPost<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const raw = JSON.stringify(body)
  const sig = createHmac("sha256", SECRET).update(raw).digest("hex")
  const r = await fetch(`${CLOUD}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-magi-signature": sig },
    body: raw,
  })
  if (!r.ok) throw new Error(`magi-cp ${r.status} ${path}`)
  return r.json() as Promise<T>
}

export async function provisionForStripeSubscription(sub: Stripe.Subscription, customer: Stripe.Customer) {
  const tenantId = `t-${sub.customer}`.toLowerCase().replace(/[^a-z0-9-]/g, "-")
  const expiresAt = sub.current_period_end + 7 * 24 * 3600   // 7-day grace

  await magiPost("/admin/tenants", {
    tenant_id: tenantId,
    plan: "pro_plus",
    expires_at: expiresAt,
  })

  // De-dupe key issuance — only mint once per subscription.
  const already = await db.first(
    "SELECT 1 FROM magi_cp_provisioned WHERE stripe_subscription_id = $1", [sub.id])
  if (already) return

  const { api_key, prefix, id: keyId } = await magiPost<{
    api_key: string; prefix: string; id: number
  }>(`/admin/tenants/${tenantId}/keys`, {})

  await db.exec(
    "INSERT INTO magi_cp_provisioned(stripe_subscription_id, tenant_id, key_prefix, key_id) VALUES ($1,$2,$3,$4)",
    [sub.id, tenantId, prefix, keyId])

  await mailer.send({
    to: customer.email!,
    subject: "Your magi-control-plane API key (Clawy Pro+)",
    html: tenantWelcomeEmail({ apiKey: api_key, dashUrl: process.env.MAGI_CP_PUBLIC_SITE_URL! }),
  })
  // NOTE: do NOT log api_key. It exists only in this function scope and the email body.
}

export async function suspendForStripeSubscription(sub: Stripe.Subscription, reason: string) {
  const tenantId = `t-${sub.customer}`.toLowerCase().replace(/[^a-z0-9-]/g, "-")
  await magiPost(`/admin/tenants/${tenantId}/suspend`, { reason })
}
```

## Welcome email skeleton

```html
<p>Welcome to Clawy Pro+ governance via magi-control-plane.</p>

<p>Your hosted instance is at <a href="{{dashUrl}}">cloud.openmagi.ai</a>.</p>

<p>Install in one line:</p>
<pre>curl -fsSL {{dashUrl}}/install.sh | bash -s -- {{apiKey}}</pre>

<p><b>Save this key now.</b> We don't store it after this email — losing
it means contacting support (kevin@openmagi.ai) to issue a replacement.</p>

<p>Next steps: <a href="{{dashUrl}}/install">{{dashUrl}}/install</a></p>
```

## Testing the contract

Locally, point Clawy at a magi-cp dev instance:

```bash
# In magi-control-plane/
make cloud-dev    # FastAPI on :8787

# In clawy:
export MAGI_CP_CLOUD_URL=http://127.0.0.1:8787
export MAGI_CP_ADMIN_HMAC_SECRET=test-secret
# (cloud reads MAGI_CP_ADMIN_HMAC_SECRET from same env)

# Fire a Stripe test webhook via the Stripe CLI:
stripe trigger customer.subscription.created
```

Then verify the tenant landed:

```bash
curl -fsS http://127.0.0.1:8787/healthz
# In a separate magi-cp shell:
.venv/bin/python -c "
from magi_cp.cloud.db import open_engine, init_schema
from magi_cp.cloud.tenants import TenantRepo
e = open_engine()
init_schema(e)
for t in TenantRepo(e).list(): print(t.id, t.status, t.plan)
"
```

You should see your test tenant and the key prefix recorded on the Clawy
side in `magi_cp_provisioned`.

## Operational concerns

- **Webhook retries**: Stripe retries failed deliveries with exponential
  backoff. Because `createTenant` is idempotent and key issuance is
  guarded by the `magi_cp_provisioned` table, retries are safe.
- **Out-of-order delivery**: Stripe does not guarantee order. Use
  Stripe's `event.created` timestamp + idempotency keys; latest event
  for a subscription wins (suspend/reactivate state transitions).
- **Cleartext key in flight**: TLS-only on both ends. HMAC over body
  protects integrity but not confidentiality — the api_key in the
  response IS the secret; rely on HTTPS for confidentiality.
- **HMAC secret rotation**: see auth section above. Until dual-accept
  ships, plan a tight redeploy window or schedule rotation during a
  Clawy webhook outage you can absorb (Stripe will retry).
