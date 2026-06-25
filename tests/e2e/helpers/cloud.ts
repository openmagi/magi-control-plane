/**
 * D73. thin HTTP client mirroring web/lib/cloud.ts.
 *
 * The dashboard's server-side cloud client (web/lib/cloud.ts) reads keys
 * from process.env and signs HMAC posts; for E2E we want a minimal
 * surface to assert backend state independent of the dashboard.
 *
 * Wire shape parity with web/lib/cloud.ts is what we care about: if
 * the cloud changes a response field, the scenarios catch it instead of
 * a silent dashboard regression.
 *
 * Reads keys from MAGI_CP_API_KEY / MAGI_CP_ADMIN_API_KEY env vars
 * (same names docker-compose.yml requires) so a single .env file feeds
 * both the stack and the harness.
 */
import { createHmac } from "node:crypto"

const CLOUD_URL = process.env.MAGI_CP_CLOUD_URL ?? "http://127.0.0.1:8787"
const FETCH_TIMEOUT_MS = 10_000

function _key(name: string): string {
  const v = process.env[name]
  if (!v) throw new Error(`e2e cloud client: ${name} not set`)
  return v
}

export type PolicyListItem = {
  id: string
  description: string
  source: string
  enabled: boolean
  trigger: { event: string; matcher: string }
  enforcement: string
}

export type LedgerEntry = {
  id: number
  ts: number
  subject: string
  prev: string
  h: string
  body?: Record<string, unknown>
}

export type LedgerPage = {
  chain_ok: boolean
  next_since_id: number
  has_more?: boolean
  entries: LedgerEntry[]
}

export type ScriptEntry = {
  id: string
  name: string
  runtime: "bash" | "python3" | "node"
  size_bytes: number
  hash: string
  created_at: number
}

async function _get<T>(path: string, keyType: "api" | "admin"): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  }
  if (keyType === "admin") headers["X-Admin-Api-Key"] = _key("MAGI_CP_ADMIN_API_KEY")
  else headers["X-Api-Key"] = _key("MAGI_CP_API_KEY")
  const r = await fetch(`${CLOUD_URL}${path}`, {
    method: "GET",
    headers,
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  })
  if (!r.ok) {
    const body = await r.text().catch(() => "")
    throw new Error(`cloud ${r.status} ${path}: ${body.slice(0, 200)}`)
  }
  return r.json() as Promise<T>
}

async function _send<T>(
  path: string,
  method: "POST" | "PATCH" | "DELETE" | "PUT",
  body: Record<string, unknown> | null,
  keyType: "api" | "admin",
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  }
  if (keyType === "admin") headers["X-Admin-Api-Key"] = _key("MAGI_CP_ADMIN_API_KEY")
  else headers["X-Api-Key"] = _key("MAGI_CP_API_KEY")
  const r = await fetch(`${CLOUD_URL}${path}`, {
    method,
    headers,
    body: body == null ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  })
  if (!r.ok) {
    const t = await r.text().catch(() => "")
    throw new Error(`cloud ${r.status} ${path}: ${t.slice(0, 200)}`)
  }
  return r.json() as Promise<T>
}

export async function listPolicies(): Promise<PolicyListItem[]> {
  const d = await _get<{ items: PolicyListItem[] }>("/policies", "admin")
  return d.items
}

export async function getPolicy(id: string): Promise<Record<string, unknown>> {
  const enc = id.split("/").map(encodeURIComponent).join("/")
  return _get<Record<string, unknown>>(`/policies/${enc}`, "admin")
}

export async function setPolicyEnabled(
  id: string,
  enabled: boolean,
): Promise<{ id: string; enabled: boolean }> {
  const enc = id.split("/").map(encodeURIComponent).join("/")
  return _send(`/policies/${enc}/enabled`, "PATCH", { enabled }, "admin")
}

export async function deletePolicy(id: string): Promise<unknown> {
  const enc = id.split("/").map(encodeURIComponent).join("/")
  // Disable acts as soft-delete for tenant policies; cloud doesn't
  // expose a DELETE on /policies/<id> for user-authored entries today
  // (admin can DELETE prebuilt slugs only). Disable is the universal
  // path the dashboard uses for the "delete" button on user policies.
  return setPolicyEnabled(id, false)
}

export async function enablePrebuilt(prebuiltId: string): Promise<{
  id: string; enabled: boolean
}> {
  const slug = prebuiltId.startsWith("prebuilt/")
    ? prebuiltId.slice("prebuilt/".length)
    : prebuiltId
  return _send(`/policies/prebuilt/${encodeURIComponent(slug)}/enable`,
    "POST", {}, "admin")
}

export async function disablePrebuilt(prebuiltId: string): Promise<{
  id: string; enabled: boolean
}> {
  const slug = prebuiltId.startsWith("prebuilt/")
    ? prebuiltId.slice("prebuilt/".length)
    : prebuiltId
  return _send(`/policies/prebuilt/${encodeURIComponent(slug)}`,
    "DELETE", null, "admin")
}

export async function ledger(
  sinceId = 0,
  limit = 200,
): Promise<LedgerPage> {
  const params = new URLSearchParams()
  params.set("since_id", String(sinceId))
  params.set("limit", String(limit))
  return _get<LedgerPage>(`/ledger?${params.toString()}`, "api")
}

export async function listScripts(): Promise<ScriptEntry[]> {
  const d = await _get<{ items: ScriptEntry[] }>("/scripts", "admin")
  return d.items
}

export async function uploadScript(
  name: string,
  runtime: "bash" | "python3" | "node",
  body: string,
): Promise<ScriptEntry> {
  const body_b64 = Buffer.from(body, "utf8").toString("base64")
  return _send("/scripts", "POST", { name, runtime, body_b64 }, "admin")
}

export async function deleteScript(id: string): Promise<{ id: string }> {
  return _send(`/scripts/${encodeURIComponent(id)}`, "DELETE", null, "admin")
}

/** Direct HMAC admin tenant create, used by scenarios that need to
 *  provision a fresh tenant before the rest of the harness runs. */
export async function createTenantHmac(
  tenantId: string,
  plan = "alpha",
): Promise<unknown> {
  const secret = _key("MAGI_CP_ADMIN_HMAC_SECRET")
  const raw = JSON.stringify({ tenant_id: tenantId, plan, expires_at: null })
  const sig = createHmac("sha256", secret).update(raw).digest("hex")
  const r = await fetch(`${CLOUD_URL}/admin/tenants`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-magi-signature": sig,
    },
    body: raw,
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  })
  if (!r.ok) {
    const t = await r.text().catch(() => "")
    throw new Error(`cloud ${r.status} /admin/tenants: ${t.slice(0, 200)}`)
  }
  return r.json()
}
