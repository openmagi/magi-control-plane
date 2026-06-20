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

  /** Read-only preset catalog — backend has no auth requirement on /presets. */
  listPresets: async (): Promise<PresetEntry[]> => {
    const r = await fetch(`${_cloudUrl()}/presets`, {
      method: "GET",
      cache: "no-store",
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    })
    if (!r.ok) {
      console.error(`cloud ${r.status} /presets`)
      throw new Error(`cloud ${r.status}`)
    }
    const d = await r.json() as { presets: PresetEntry[] }
    return d.presets
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
}

function _encId(id: string): string {
  // Defensive: encode each segment so weird chars never reach the cloud raw.
  return id.split("/").map(encodeURIComponent).join("/")
}

