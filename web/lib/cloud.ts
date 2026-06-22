/**
 * Server-side cloud client.
 *
 * Keys are server-only — never imported from client components. Every call has
 * an explicit timeout to avoid wedging the dashboard on a hung cloud.
 */
const _cloudUrl = (): string =>
  process.env.MAGI_CP_CLOUD_URL || "http://127.0.0.1:8787"

const FETCH_TIMEOUT_MS = 5000

/** Sentinel for missing-key. Mapped to a generic user-facing string by errMsg();
 * the actual env var name is logged to stderr only (never sent to browser). */
export class CloudConfigError extends Error {
  constructor() { super("cloud config error") }
}

function _readKey(envVar: string): string {
  const k = process.env[envVar]
  if (!k) {
    console.error(`dashboard server: ${envVar} not set`)
    throw new CloudConfigError()
  }
  return k
}

function _hitlKey(): string { return _readKey("MAGI_CP_HITL_API_KEY") }
function _apiKey(): string { return _readKey("MAGI_CP_API_KEY") }
function _adminKey(): string { return _readKey("MAGI_CP_ADMIN_API_KEY") }
function _hmacSecret(): string { return _readKey("MAGI_CP_ADMIN_HMAC_SECRET") }

/** HMAC-signed admin POST (tenant create / key issue / suspend / etc).
 *
 * Backend contract (cloud.app._attach_admin_tenant_routes.require_hmac):
 *   x-magi-signature = hex(hmac_sha256(MAGI_CP_ADMIN_HMAC_SECRET, body))
 * Body MUST be the exact bytes signed; pass JSON-serialised once and reuse.
 */
async function _hmacPost<T>(path: string, body: Record<string, unknown>, timeoutMs?: number): Promise<T> {
  const crypto = await import("node:crypto")
  const raw = JSON.stringify(body)
  const sig = crypto.createHmac("sha256", _hmacSecret()).update(raw).digest("hex")
  const r = await fetch(`${_cloudUrl()}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-magi-signature": sig },
    body: raw,
    cache: "no-store",
    signal: AbortSignal.timeout(timeoutMs ?? FETCH_TIMEOUT_MS),
  })
  if (!r.ok) {
    console.error(`cloud ${r.status} ${path}: ${await r.text().catch(() => "")}`)
    throw new Error(`cloud ${r.status}`)
  }
  return r.json() as Promise<T>
}

async function _fetch<T>(
  path: string,
  init: RequestInit & { keyType: "api" | "hitl" | "admin"; timeoutMs?: number },
): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.keyType === "hitl") headers.set("X-Hitl-Api-Key", _hitlKey())
  else if (init.keyType === "admin") headers.set("X-Admin-Api-Key", _adminKey())
  else headers.set("X-Api-Key", _apiKey())
  headers.set("Content-Type", "application/json")
  const r = await fetch(`${_cloudUrl()}${path}`, {
    ...init, headers, cache: "no-store",
    signal: AbortSignal.timeout(init.timeoutMs ?? FETCH_TIMEOUT_MS),
  })
  if (!r.ok) {
    // Do not echo cloud response body to callers — could include details
    // useful for reconnaissance. Log to server stderr; expose status only.
    console.error(`cloud ${r.status} ${path}: ${await r.text().catch(() => "")}`)
    throw new Error(`cloud ${r.status}`)
  }
  return r.json() as Promise<T>
}

export type HitlDetail = {
  id: number
  matter: string
  doc_id: string
  reason: string
  payload: HitlItem["payload"]
  status: "pending" | "approved" | "rejected"
  approver: string | null
  note: string | null
  ts_created: number
  ts_decided: number | null
  ledger_context: Array<{
    id: number; ts: number; h: string; prev: string;
    body: Record<string, unknown>
  }>
}

export type HitlItem = {
  id: number
  matter: string
  doc_id: string
  reason: string
  payload: { citations?: Array<{
    ref: string
    status: string
    reasons: string[]
    nli_label?: string         // advisory (P6): entailment | neutral | contradiction | no-source
    nli_score?: number
  }> }
  ts_created: number
}

export type LedgerEntry = {
  id: number
  ts: number
  matter: string
  prev: string
  h: string
  body?: Record<string, unknown>
}

export type LedgerPage = {
  chain_ok: boolean
  next_since_id: number
  entries: LedgerEntry[]
}

export type PolicyTrigger = {
  host: string
  event: string
  matcher: string
}

export type PolicyEvidenceReq = { step: string; verdict: string }

export type PolicyBody = {
  id: string
  description: string
  version: string
  trigger: PolicyTrigger
  sentinel_re: string
  requires: PolicyEvidenceReq[]
  on_missing: string
  on_signature_invalid: string
  gate_binary: string
}

export type PolicyListItem = {
  id: string
  description: string
  source: string
  enabled: boolean
  trigger: { event: string; matcher: string }
  enforcement: string   // "deterministic-gate" | "observe-only" | "log-only"
}

export type PolicyDetail = {
  id: string
  source: string
  enabled: boolean
  policy: PolicyBody
  enforcement: string
  compiled_sha256: string
}

export type CompiledManagedSettings = {
  managed_settings: Record<string, unknown>
  sha256: string
}

type HitlListResp = { items: HitlItem[] }
type DecideResp = { verdict?: string; token?: string | null; hitl_id?: number }
type PolicyListResp = { items: PolicyListItem[] }

export const cloud = {
  listHitl: (): Promise<HitlItem[]> =>
    _fetch<HitlListResp>("/hitl", { method: "GET", keyType: "hitl" })
      .then(d => d.items),

  getHitlDetail: (id: number): Promise<HitlDetail> =>
    _fetch<HitlDetail>(`/hitl/${id}/detail`, { method: "GET", keyType: "hitl" }),

  approve: (id: number, approver: string, note?: string): Promise<DecideResp> =>
    _fetch<DecideResp>(`/hitl/${id}/approve`, {
      method: "POST", keyType: "hitl",
      body: JSON.stringify({ approver, note }),
    }),

  reject: (id: number, approver: string, note?: string): Promise<DecideResp> =>
    _fetch<DecideResp>(`/hitl/${id}/reject`, {
      method: "POST", keyType: "hitl",
      body: JSON.stringify({ approver, note }),
    }),

  ledger: (sinceId: number = 0, limit: number = 100): Promise<LedgerPage> =>
    _fetch<LedgerPage>(`/ledger?since_id=${sinceId}&limit=${limit}`,
                       { method: "GET", keyType: "api" }),

  listPolicies: (): Promise<PolicyListItem[]> =>
    _fetch<PolicyListResp>("/policies", { method: "GET", keyType: "admin" })
      .then(d => d.items),

  getPolicy: (id: string): Promise<PolicyDetail> =>
    _fetch<PolicyDetail>(`/policies/${_encId(id)}`, { method: "GET", keyType: "admin" }),

  getCompiled: (id: string): Promise<CompiledManagedSettings> =>
    _fetch<CompiledManagedSettings>(`/policies/${_encId(id)}/compiled`,
                                     { method: "GET", keyType: "admin" }),

  setEnabled: (id: string, enabled: boolean): Promise<{ id: string; enabled: boolean }> =>
    _fetch(`/policies/${_encId(id)}/enabled`, {
      method: "PATCH", keyType: "admin",
      body: JSON.stringify({ enabled }),
    }),

  /** Compile a NL description to a Policy IR + critic review + schema issues.
   *
   * Long timeout: the compile path runs two sequential LLM calls (compiler +
   * critic) which routinely take 5–20s. The default 5s fetch budget is for
   * fast endpoints only; this needs a much wider window. */
  compilePolicy: (nl: string, priorTurns?: Array<{ role: "user" | "assistant"; content: string }>):
    Promise<CompileResult> =>
    _fetch<CompileResult>("/policies/compile", {
      method: "POST", keyType: "admin",
      timeoutMs: 90_000,
      body: JSON.stringify({ nl, prior_turns: priorTurns ?? null }),
    }),

  /** Generic verifier dispatch — produces a signed token on pass/review. */
  verifyDispatch: (
    step: string,
    payload: Record<string, unknown>,
    matter?: string,
    docId?: string,
  ): Promise<{
    verdict: "pass" | "review" | "deny" | "error";
    token: string | null;
    reasons: string[];
    exp?: number;
    kid?: string;
    ledger_h?: string;
    hitl_id?: number;
  }> =>
    _fetch("/verify/" + encodeURIComponent(step), {
      method: "POST", keyType: "api",
      body: JSON.stringify({
        payload,
        matter: matter ?? "dashboard",
        doc_id: docId ?? "dashboard",
      }),
    }),

  /** Fetch the calling tenant's identity. Used by /setup. */
  getMyTenant: (apiKey: string): Promise<{
    id: string
    status: string
    plan: string
    expires_at: number | null
    synthetic: boolean
  }> => {
    return fetch(`${_cloudUrl()}/tenants/me`, {
      method: "GET",
      headers: { "X-Api-Key": apiKey },
      cache: "no-store",
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    }).then(async r => {
      if (!r.ok) {
        console.error(`cloud ${r.status} /tenants/me`)
        throw new Error(`cloud ${r.status}`)
      }
      return r.json()
    })
  },

  /** Create tenant (HMAC). Idempotent — returns current state if exists. */
  createTenant: (tenantId: string, plan: string = "alpha",
                 expiresAt: number | null = null): Promise<{
    id: string; status: string; plan: string; expires_at: number | null
  }> =>
    _hmacPost("/admin/tenants", { tenant_id: tenantId, plan, expires_at: expiresAt }),

  /** Issue API key for an existing tenant (HMAC). Cleartext key in response
   * is shown ONCE — operator must hand it to applicant immediately. */
  issueKey: (tenantId: string): Promise<{
    id: number; tenant_id: string; api_key: string; prefix: string
  }> =>
    _hmacPost(`/admin/tenants/${encodeURIComponent(tenantId)}/keys`, {}),

  /** Provision a tenant + first API key in one operator click. Combines
   * createTenant + issueKey. Used by the /admin/signups approve action. */
  provisionTenant: async (tenantId: string, plan: string = "alpha"): Promise<{
    tenantId: string; apiKey: string; keyId: number; prefix: string
  }> => {
    await cloud.createTenant(tenantId, plan)
    const key = await cloud.issueKey(tenantId)
    return {
      tenantId, apiKey: key.api_key, keyId: key.id, prefix: key.prefix,
    }
  },

  /** Read-only verifier catalog from the registry + vendor preview
   * entries. Pure-derivation pivot: tenant-scoped rows live under the
   * /catalog/* surfaces below, not embedded here. */
  listVerifiers: async (): Promise<PresetEntry[]> => {
    const d = await _fetch<{ presets: PresetEntry[] }>(
      "/verifiers", { method: "GET", keyType: "api" },
    )
    return d.presets
  },

  /** Back-compat alias. /presets page still references this; /rules
   * calls listVerifiers() directly. */
  listPresets: async (): Promise<PresetEntry[]> => {
    return await cloud.listVerifiers()
  },

  /** Evidence-type catalog. Walks built-ins + steps referenced in
   * stored policies and tags policy-derived steps that have no
   * matching verifier as `enforcement: "missing"` (operators should
   * see the broken reference). */
  listEvidenceTypes: async (): Promise<EvidenceTypeEntry[]> => {
    const d = await _fetch<{ items: EvidenceTypeEntry[] }>(
      "/catalog/evidence-types", { method: "GET", keyType: "api" },
    )
    return d.items
  },

  /** Condition catalog. v1 surfaces sentinel_re patterns + tool
   * matchers extracted from every stored policy. Read-only — entries
   * change only when the originating policy is edited. */
  listConditions: async (): Promise<ConditionEntry[]> => {
    const d = await _fetch<{ items: ConditionEntry[] }>(
      "/catalog/conditions", { method: "GET", keyType: "api" },
    )
    return d.items
  },
}

export type CompileResult = {
  ir: Record<string, unknown>
  review: { ok: boolean; issues: string[] }
  schema_issues: string[]
}

export type PresetEntry = {
  id: string
  category: "ANSWER" | "FACT" | "CODING" | "TASK" | "OUTPUT"
          | "RESEARCH" | "MEMORY" | "SECURITY"
  description: string
  enforcement: "enforcing" | "always-on" | "preview" | "capability"
  step: string | null
  /** JSON Schema for the verifier's payload. Wired presets only. */
  input_schema?: Record<string, unknown> | null
  /** Verifier class name (e.g. "verify_privilege_scan"). Wired only. */
  name?: string | null
}

/** Pure-derivation catalog row: an evidence-type step the runtime can
 * fire. Either provided by a built-in verifier or referenced by a
 * stored policy (in which case `enforcement = "missing"` until an
 * operator wires a verifier for that step). */
export type EvidenceTypeEntry = {
  step: string
  category: PresetEntry["category"] | null
  description: string
  enforcement: "enforcing" | "always-on" | "preview" | "missing"
  name: string | null
  source: "builtin" | "policy-derived"
  used_by_policies: string[]
}

/** Pure-derivation catalog row: a condition extracted from a stored
 * policy. v1 covers the two condition shapes the policy IR carries
 * inline today — sentinel_re patterns and tool matchers. */
export type ConditionEntry = {
  kind: "sentinel_re" | "tool_match"
  value: string
  policy_id: string
  trigger_event: string
  tool_matcher: string
}

function _encId(id: string): string {
  // Defensive: encode each segment so weird chars never reach the cloud raw.
  return id.split("/").map(encodeURIComponent).join("/")
}

