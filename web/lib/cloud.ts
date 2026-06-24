/**
 * Server-side cloud client.
 *
 * Keys are server-only. never imported from client components. Every call has
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
    const body = await r.text().catch(() => "")
    console.error(`cloud ${r.status} ${path}: ${body}`)
    // Append a SAFE classifier marker for known backend states so
    // codeForError can map them to a friendly error code, without
    // echoing arbitrary body content to the client.
    let marker = ""
    if (r.status === 503 && /provider.*not configured/i.test(body)) {
      marker = " providers not configured"
    }
    throw new Error(`cloud ${r.status}${marker}`)
  }
  return r.json() as Promise<T>
}

async function _fetch<T>(
  path: string,
  init: RequestInit & {
    keyType: "api" | "hitl" | "admin"
    timeoutMs?: number
    /** D53b follow-up: when present, the fetch is aborted on EITHER
     *  the configured timeout OR this signal firing. Used by the
     *  dry-run proxy so a client-side navigation-abort actually
     *  cancels the upstream cloud request (without this, _fetch's
     *  hard `AbortSignal.timeout` overrode every external signal
     *  and the request kept running on the cloud until the 30s
     *  deadline). Backwards compatible: existing callers leave it
     *  undefined and get the timeout-only behaviour. */
    externalSignal?: AbortSignal
  },
): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.keyType === "hitl") headers.set("X-Hitl-Api-Key", _hitlKey())
  else if (init.keyType === "admin") headers.set("X-Admin-Api-Key", _adminKey())
  else headers.set("X-Api-Key", _apiKey())
  headers.set("Content-Type", "application/json")
  // Combine the timeout signal with the optional external signal.
  // `AbortSignal.any` short-circuits on either firing; Node 20+ /
  // modern browsers both ship it. When externalSignal is undefined
  // we keep the original timeout-only path so behaviour is byte-
  // identical for non-dry-run callers.
  const timeoutSignal = AbortSignal.timeout(
    init.timeoutMs ?? FETCH_TIMEOUT_MS,
  )
  const combinedSignal: AbortSignal = init.externalSignal
    ? AbortSignal.any([timeoutSignal, init.externalSignal])
    : timeoutSignal
  const r = await fetch(`${_cloudUrl()}${path}`, {
    ...init, headers, cache: "no-store",
    signal: combinedSignal,
  })
  if (!r.ok) {
    // Do not echo cloud response body to callers. could include details
    // useful for reconnaissance. Log to server stderr; expose status only.
    console.error(`cloud ${r.status} ${path}: ${await r.text().catch(() => "")}`)
    throw new Error(`cloud ${r.status}`)
  }
  return r.json() as Promise<T>
}

export type HitlDetail = {
  id: number
  // PR4: canonical keying only. Legacy `matter` / `doc_id` columns were
  // dropped from the DB (see scripts/migrate_pr4_drop_legacy.py) and
  // removed from the wire. `subject` / `payload_hash` are non-null
  // because the PR4 cut-over refuses to run with any NULL-subject row.
  subject: string
  payload_hash: string
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
  // PR4: see HitlDetail.subject — canonical-only.
  subject: string
  payload_hash: string
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
  // PR4: canonical wire field. The underlying DB column is still named
  // `matter` (deeper rename deferred) but the surface is canonical.
  subject: string
  prev: string
  h: string
  body?: Record<string, unknown>
}

export type LedgerPage = {
  chain_ok: boolean
  next_since_id: number
  /** D52c follow-up: true when the server trimmed an over-fetched row,
   * meaning more pages exist beyond `next_since_id`. Optional so older
   * cloud versions that don't yet emit the field keep deserializing. */
  has_more?: boolean
  entries: LedgerEntry[]
}

/** D53a: one row of the verifier samples response.
 *
 * `redacted_payload_preview` is already redacted server-side via
 * `magi_cp.policy.run_redaction.redact_payload_preview` (allowlist
 * projection + linear regex masking + 240-char truncation). The
 * dashboard renders it verbatim - never re-extracts or attempts to
 * de-redact. `policy_id` is reserved for a future producer that
 * records the policy that fired this verifier; today it is always
 * `null` for built-in / inline kinds. */
export type VerifierSample = {
  id: number
  ts: string
  verdict: "pass" | "fail" | "deny" | "review" | "needs_review" | "not_applicable" | null
  redacted_payload_preview: string
  policy_id: string | null
}

export type VerifierSamplesResponse = {
  samples: VerifierSample[]
}

export type PolicyTrigger = {
  host: string
  event: string
  matcher: string
}

export type PolicyEvidenceReq = { step: string; verdict: string }

/** Issue #1 P0 (#12): policies can be any of the 5 archetypes. The
 * evidence shape (legacy) carries trigger / sentinel / requires; the
 * declarative siblings carry their own fields. We keep the type loose
 * here because the detail page renders the raw JSON; downstream forms
 * narrow by `type`. */
export type PolicyBody = {
  id: string
  description: string
  version: string
  type?: "evidence" | "permission" | "subagent" | "mcp_gating" | "context_injection"
  // evidence fields (legacy default)
  trigger?: PolicyTrigger
  sentinel_re?: string | null
  requires?: PolicyEvidenceReq[]
  action?: string
  on_missing?: string
  on_signature_invalid?: string
  gate_binary?: string
  // permission archetype
  permission?: "allow" | "deny" | "ask"
  pattern?: string
  exclusive?: boolean
  // subagent archetype
  subagent_type?: string
  tool_allowlist?: string[]
  // mcp_gating archetype
  server?: string
  // context_injection archetype
  event?: string
  matcher?: string
  template?: string
}

/** P8 fix-cycle #5: the enforcement vocabulary depends on a hidden
 * branch in the cloud (any kind=step req → P8 resolver; all non-step
 * → legacy (action, event) label). The union below is the closed set
 * of values the dashboard renders for; widening it is a deliberate
 * type-changed change so a future cloud refactor that drifts the
 * vocabulary is caught at `tsc --noEmit` time, not silently rendered
 * as the default Badge variant.
 *
 *   "enforcing"          — P8: at least one step req resolves to a
 *                          wired+active verifier (or registry was
 *                          absent at PUT time).
 *   "preview"            — P8: at least one step req carried the
 *                          `preview:` prefix at PUT time.
 *   "unresolved-legacy"  — P8 fix-cycle #1: pre-P8 row whose step
 *                          ref no longer resolves against the live
 *                          registry. Row is effectively disabled at
 *                          compile; operator must re-PUT.
 *   "deterministic-gate" — Legacy (action ∈ {block, ask}) label.
 *   "observe-only"       — Legacy (PostToolUse + audit) label.
 *   "log-only"           — Legacy fallthrough (PreToolUse + audit etc).
 *   "missing"            — /catalog/evidence-types only; surfaced for
 *                          a policy-referenced step that has no live
 *                          verifier behind it.
 */
export type EnforcementLabel =
  | "enforcing"
  | "preview"
  | "unresolved-legacy"
  | "deterministic-gate"
  | "observe-only"
  | "log-only"
  | "missing"

export type PolicyListItem = {
  id: string
  description: string
  source: string
  enabled: boolean
  trigger: { event: string; matcher: string }
  enforcement: EnforcementLabel
}

export type PolicyDetail = {
  id: string
  source: string
  enabled: boolean
  policy: PolicyBody
  enforcement: EnforcementLabel
  compiled_sha256: string
}

export type CompiledManagedSettings = {
  managed_settings: Record<string, unknown>
  sha256: string
}

/** D54: prebuilt policy templates returned by GET /policies/prebuilt.
 *
 * A prebuilt is a (verifier, event, matcher, action) tuple the operator
 * REVIEWS and then saves via the regular /policies POST. Nothing is
 * auto-installed. The "Use this" button on the Policies tab links to
 * /policies/new?mode=advanced&draft=<encoded JSON of `ir`> so the
 * PolicyBuilder picks the prefill up like any other draft.
 *
 *   id              : short stable slug. Used as the React key + the
 *                     draft id suggestion; the operator can edit before
 *                     save.
 *   title           : short label rendered on the prebuilt card.
 *   summary         : one-sentence "what this does in practice", in
 *                     plain English (verifier + event + matcher + action).
 *   verifier_step   : the step name of the underlying verifier. Useful
 *                     for cross-linking back to the Verifiers tab.
 *   ir              : the policy IR, ready to feed the PolicyBuilder
 *                     draft prefill. Same shape as PolicyBody.
 */
export type PrebuiltPolicyEntry = {
  id: string
  title: string
  summary: string
  verifier_step: string
  ir: PolicyBody
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

  ledger: (
    sinceId: number = 0,
    limit: number = 100,
    verifier?: string[],
  ): Promise<LedgerPage> => {
    // D52c: `verifier=<step>` (repeatable) filters the chain to records
    // emitted by the named verifier(s). Empty/undefined → no filter,
    // mirroring the URL state on the /ledger page (zero chips picked =
    // full view).
    const params = new URLSearchParams()
    params.set("since_id", String(sinceId))
    params.set("limit", String(limit))
    if (verifier && verifier.length > 0) {
      for (const v of verifier) {
        if (v) params.append("verifier", v)
      }
    }
    return _fetch<LedgerPage>(`/ledger?${params.toString()}`,
                               { method: "GET", keyType: "api" })
  },

  /** D52c: count of ledger entries matching the given verifier filter.
   *
   * Used by the Rules → Verifiers expander to render a "Recent
   * emissions (last 24h)" widget. The cloud side now uses a SQL
   * COUNT(*) (no body decode, no token verify) so this stays cheap
   * even on a large chain.
   *
   * D52c follow-up: prefer `ledgerCounts(steps, sinceSecs)` for the
   * dashboard fan-out (one HTTP call + one GROUP BY query instead
   * of K calls + K full-table walks). */
  ledgerCount: (
    verifier?: string,
    sinceSecs?: number,
  ): Promise<{ count: number }> => {
    const params = new URLSearchParams()
    if (verifier) params.set("verifier", verifier)
    if (typeof sinceSecs === "number" && sinceSecs > 0) {
      params.set("since_secs", String(Math.floor(sinceSecs)))
    }
    const qs = params.toString()
    return _fetch<{ count: number }>(
      `/ledger/count${qs ? `?${qs}` : ""}`,
      { method: "GET", keyType: "api" },
    )
  },

  /** D52c follow-up: batched per-step count. One HTTP round-trip + one
   * SQL GROUP BY for every verifier on the Rules → Verifiers tab.
   *
   * Returns `{counts: {step: n}}`. Steps with zero emissions in the
   * window still appear (value 0) so the dashboard can render dashes
   * for "no emissions" without a follow-up call. */
  ledgerCounts: (
    verifiers: string[],
    sinceSecs?: number,
  ): Promise<{ counts: Record<string, number> }> => {
    const params = new URLSearchParams()
    for (const v of verifiers) if (v) params.append("verifier", v)
    if (typeof sinceSecs === "number" && sinceSecs > 0) {
      params.set("since_secs", String(Math.floor(sinceSecs)))
    }
    const qs = params.toString()
    return _fetch<{ counts: Record<string, number> }>(
      `/ledger/counts${qs ? `?${qs}` : ""}`,
      { method: "GET", keyType: "api" },
    )
  },

  /** D53a: most-recent N redacted samples for one verifier.
   *
   * Powers the inline sample list on the verifier catalog expander.
   * Each sample's `redacted_payload_preview` has already passed
   * through the cloud-side redactor; the dashboard never sees raw
   * evidence payloads. Unknown verifier names come back as
   * `{samples: []}` (NOT a 404), matching the count endpoint's
   * "empty filter view is valid" contract.
   *
   * `limit` is clamped server-side to [1, 25]; the dashboard's
   * collapsed list shows 5. */
  listVerifierSamples: (
    verifier: string,
    limit: number = 5,
    sinceSecs: number = 24 * 60 * 60,
  ): Promise<VerifierSamplesResponse> => {
    const params = new URLSearchParams()
    params.set("verifier", verifier)
    params.set("limit", String(Math.max(1, Math.min(25, Math.floor(limit)))))
    if (sinceSecs > 0) {
      params.set("since_secs", String(Math.floor(sinceSecs)))
    }
    return _fetch<VerifierSamplesResponse>(
      `/ledger/samples?${params.toString()}`,
      { method: "GET", keyType: "api" },
    )
  },

  /** D52c follow-up: dedicated chain-integrity endpoint. The
   * dashboard can poll this at low frequency for the chain-ok badge
   * so paginated `/ledger` reads stay cheap (the `/ledger` route
   * skips the chain re-walk when `since_id > 0`). */
  ledgerIntegrity: (): Promise<{ chain_ok: boolean }> =>
    _fetch<{ chain_ok: boolean }>(
      "/ledger/integrity", { method: "GET", keyType: "api" },
    ),

  listPolicies: (): Promise<PolicyListItem[]> =>
    _fetch<PolicyListResp>("/policies", { method: "GET", keyType: "admin" })
      .then(d => d.items),

  /** D54: 5 prebuilt policy templates (one per built-in verifier).
   * Static catalog; the cloud computes it from
   * src/magi_cp/policy/prebuilt.py on every request.
   *
   * The dashboard renders these in a "Prebuilt" section above the
   * tenant's own policies on the Policies tab. Each row's `ir` is the
   * draft prefill the "Use this" button passes to /policies/new?mode=
   * advanced&draft=... and nothing here is auto-installed; the operator
   * reviews and saves through the normal PolicyBuilder flow. */
  listPrebuiltPolicies: (): Promise<PrebuiltPolicyEntry[]> =>
    _fetch<{ items: PrebuiltPolicyEntry[] }>(
      "/policies/prebuilt", { method: "GET", keyType: "admin" },
    ).then(d => d.items),

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

  // D56b: the client-side `compilePolicy()` wrapper for the one-shot
  // NL compose endpoint (/policies/compile) was retired together with
  // the dashboard NL mode. The cloud's POST /policies/compile route is
  // still served by src/magi_cp/cloud/app.py for external SDK
  // consumers; the dashboard now reaches the LLM compose surface via
  // `/api/policies/compile-interactive` (D55a / conversational mode).
  // CompileResult was deleted from this file's exports for the same
  // reason. If you need to re-introduce a client wrapper, route it
  // through the interactive endpoint, not the one-shot compile.

  /** D53b: replay a draft Policy IR against the last 24h / 7d of ledger
   * rows and report how many would have triggered the action.
   *
   * Read-only: the cloud route never writes to the ledger or policy
   * store. Sample payloads in the response have already passed
   * through D50's `redact_payload_preview` (allowlist projection +
   * linear masking) so raw evidence bodies never reach the browser.
   *
   * Reuses the same validation surface as PUT /policies; an IR that
   * would fail to save also fails to dry-run (422). The endpoint is
   * admin-key gated. The dashboard's same-origin proxy at
   * /api/policies/dry-run is the only browser-reachable entry. */
  dryRunPolicy: (
    ir: Record<string, unknown>,
    since: "24h" | "7d" = "24h",
    limit: number = 1000,
    /** D53b follow-up: optional client-side abort signal. When the
     *  panel's AbortController.signal is passed through here, a
     *  navigation-abort actually cancels the upstream cloud fetch
     *  instead of letting it run to the 30s deadline. */
    externalSignal?: AbortSignal,
    /** D53b follow-up: explicit tenant scoping. The cloud's
     *  /policies/dry-run is admin-key gated (no per-request tenant
     *  resolution), so without this field the replay runs against
     *  the synthetic "default" tenant — wrong-tenant count on every
     *  multi-tenant deployment. */
    tenantId?: string,
  ): Promise<DryRunResult> =>
    _fetch<DryRunResult>("/policies/dry-run", {
      method: "POST", keyType: "admin",
      timeoutMs: 30_000,
      externalSignal,
      body: JSON.stringify({
        ir, since, limit,
        ...(tenantId ? { tenant_id: tenantId } : {}),
      }),
    }),

  /** Generic verifier dispatch. produces a signed token on pass/review.
   *
   * PR4: canonical fields only. Legacy `matter` / `doc_id` aliases have
   * been removed from the cloud's request schema (`extra="forbid"` 422s
   * unknown keys), so this client sends `subject` / `payload_hash`
   * directly. */
  verifyDispatch: (
    step: string,
    payload: Record<string, unknown>,
    subject?: string,
    payloadHash?: string,
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
        subject: subject ?? "dashboard",
        payload_hash: payloadHash ?? "dashboard",
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

  /** Create tenant (HMAC). Idempotent. returns current state if exists. */
  createTenant: (tenantId: string, plan: string = "alpha",
                 expiresAt: number | null = null): Promise<{
    id: string; status: string; plan: string; expires_at: number | null
  }> =>
    _hmacPost("/admin/tenants", { tenant_id: tenantId, plan, expires_at: expiresAt }),

  /** Issue API key for an existing tenant (HMAC). Cleartext key in response
   * is shown ONCE. operator must hand it to applicant immediately. */
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
   * matchers extracted from every stored policy. Read-only. entries
   * change only when the originating policy is edited. */
  listConditions: async (): Promise<ConditionEntry[]> => {
    const d = await _fetch<{ items: ConditionEntry[] }>(
      "/catalog/conditions", { method: "GET", keyType: "api" },
    )
    return d.items
  },

  /** P10: list endpoint heartbeats for the calling tenant. Read-only. */
  listEndpoints: async (): Promise<EndpointEntry[]> => {
    const d = await _fetch<EndpointListing>(
      "/endpoints", { method: "GET", keyType: "api" },
    )
    return d.items
  },

  /** Issue #1 P0 (#2): full /endpoints response including the
   * cloud-active digest + threshold meta. Used by the
   * dashboard's `confirmed/stale-policy/unknown/not-loaded`
   * classification UI. */
  listEndpointsListing: async (): Promise<EndpointListing> => {
    return await _fetch<EndpointListing>(
      "/endpoints", { method: "GET", keyType: "api" },
    )
  },

  /** P7: CC hook payload schema menu. Reference data — no auth needed.
   *
   * The dashboard ships a static mirror in lib/payload-schemas.ts for
   * the Server-Component wizard (synchronous render). This client
   * exists for third-party tooling / linters that want the cloud's
   * authoritative copy at runtime. */
  listPayloadSchemas: async (): Promise<{ schemas: unknown[] }> => {
    const r = await fetch(`${_cloudUrl()}/payload-schemas`, {
      method: "GET", cache: "no-store",
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    })
    if (!r.ok) throw new Error(`cloud ${r.status}`)
    return r.json() as Promise<{ schemas: unknown[] }>
  },
}

// D56b: `CompileResult` (the one-shot NL→IR response type) was deleted
// alongside the dashboard NL mode. The conversational compose surface
// uses its own response shape declared inline in
// app/(console)/policies/new/_components/ConversationalCompose.tsx
// (InteractiveTurnResponse). External SDK consumers that still want a
// typed wrapper around POST /policies/compile should declare it
// against the cloud's OpenAPI spec.

/** D53b: response from POST /policies/dry-run.
 *
 * `sample_matched` rows have already been run through D50's
 * `redact_payload_preview` server-side (allowlist projection + linear
 * regex masking + 240-char truncation); raw evidence payloads never
 * reach the browser. Each sample row mirrors the D53a verifier-samples
 * shape (id / ts / closed-set verdict / redacted preview) so the
 * dashboard can reuse the same row component if it wants to.
 *
 * `skipped_reason` is non-null when the dry-run could not produce a
 * meaningful count: today the two reasons surfaced are
 *   - "archetype-not-dry-runnable" (permission / mcp / subagent /
 *     context_injection compile to managed-settings, no requires[]
 *     to replay), and
 *   - "no-records-in-trigger-frame" (the window had no rows whose
 *     hook_event + matcher fall under the policy trigger).
 */
export type DryRunSampleRow = {
  id: number
  ts: string
  verdict:
    | "pass"
    | "fail"
    | "deny"
    | "review"
    | "needs_review"
    | "not_applicable"
    | null
  redacted_payload_preview: string
}

export type DryRunResult = {
  total_records: number
  matched: number
  /** Follow-up: rows where >=1 requires entry was offline-unevaluable.
   *  Optional so older cloud versions deserialize cleanly. */
  indeterminate?: number
  by_verdict: Record<
    "pass" | "fail" | "deny" | "review" | "needs_review"
      | "not_applicable" | "unknown",
    number
  >
  by_action: Record<"block" | "ask" | "audit" | "strip", number>
  sample_matched: DryRunSampleRow[]
  skipped_reason:
    | "archetype-not-dry-runnable"
    | "no-records-in-trigger-frame"
    | "multi-requires-not-replayable"
    | "requires-indeterminate"
    | "no-frame-metadata-on-rows"
    | null
  /** Follow-up: requires-entry kinds that produced indeterminate
   *  results during the replay (subset of
   *  {"llm_critic","shacl","regex"}). */
  skipped_kinds?: string[]
  since: "24h" | "7d"
  limit: number
  /** Follow-up: tenant the replay was scoped to. Echoed for audit. */
  tenant_id?: string
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
 * fire. Provided by:
 *   - a built-in verifier (`source: "builtin"`),
 *   - a tenant-scoped custom verifier authored via /verifiers/new
 *     (`source: "custom"`, `enforcement: "preview"`),
 *   - or referenced by a stored policy with no matching verifier
 *     (`source: "policy-derived"`, `enforcement: "missing"`). */
export type EvidenceTypeEntry = {
  step: string
  category: PresetEntry["category"] | null
  description: string
  enforcement: "enforcing" | "always-on" | "preview" | "missing"
  name: string | null
  source: "builtin" | "custom" | "policy-derived"
  used_by_policies: string[]
  /** D52d follow-up: present on `source: "custom"` rows so the catalog
   * expander can render the operator's author-supplied (path,
   * check_description) tree instead of the "preview mode" placeholder.
   * Absent on built-ins (those resolve via getVerifierDescriptor) and
   * on policy-derived rows (no descriptor exists). */
  field_checks?: Array<{
    path: string
    check_description: string
  }>
}

/** Pure-derivation catalog row: a condition extracted from a stored
 * policy. v1 covers the two condition shapes the policy IR carries
 * inline today. sentinel_re patterns and tool matchers. */
export type ConditionEntry = {
  kind: "sentinel_re" | "tool_match" | "regex" | "llm_critic" | "shacl"
  value: string
  policy_id: string
  trigger_event: string
  tool_matcher: string
}

/** P10: a single endpoint heartbeat as surfaced by /endpoints.
 *
 * Issue #1 P0 (#2): `policy_status` is the operator-visible label
 * classifying the gate-reported digest against the cloud's current
 * compile + the snapshot history. Replaces the prior "Healthy / Stale"
 * binary which never compared digests.
 *
 *   confirmed     — gate digest == current cloud-active compile
 *   stale-policy  — gate digest matches a historical compile the
 *                   cloud authored but has since superseded
 *   unknown       — gate digest matches nothing the cloud authored
 *                   (drifted gate or someone editing managed-settings
 *                   by hand)
 *   not-loaded    — gate posted a null digest (first boot before
 *                   `compile` ran)
 *
 * `attested` is True iff the gate supplied a signed_attestation in
 * its last heartbeat. Today the cloud doesn't verify the signature
 * (TOFU-over-tenant-key); the field reserves room for the future
 * per-endpoint enrollment keypair (Issue #1 P0 #1).
 */
export type EndpointPolicyStatus =
  | "confirmed" | "stale-policy" | "unknown" | "not-loaded"

export type EndpointEntry = {
  endpoint_id: string
  tenant_id: string
  last_seen: number
  active_policy_digest: string | null
  agent_version: string | null
  label: string | null
  stale: boolean
  policy_status?: EndpointPolicyStatus
  attested?: boolean
}

export type EndpointListing = {
  items: EndpointEntry[]
  cloud_active_digest: string | null
  stale_threshold_s: number
  recommended_heartbeat_interval_s: number
}

function _encId(id: string): string {
  // Defensive: encode each segment so weird chars never reach the cloud raw.
  return id.split("/").map(encodeURIComponent).join("/")
}

