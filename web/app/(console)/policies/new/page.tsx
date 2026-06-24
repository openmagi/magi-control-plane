import Link from "next/link"
import { revalidatePath } from "next/cache"
import { redirect } from "next/navigation"
import PayloadFieldChipsClient from "./_components/PayloadFieldChipsClient"
import SteeringAwareField from "./_components/SteeringAwareField"
import { XMarkIcon, ArrowLeftIcon, SparklesIcon, CodeBracketIcon, AdjustmentsHorizontalIcon, CheckIcon } from "@heroicons/react/24/outline"
import { VerifierFieldChecks } from "../../_components/VerifierFieldChecks"
import NlAuthoringGuide from "../../_components/NlAuthoringGuide"
import PolicyBuilder from "@/components/PolicyBuilder"
import { codeForError, resolveFlash } from "@/lib/flash"
import { validatePolicyId } from "@/lib/policy-id"
import {
  validateDraft, type PolicyDraft,
} from "@/lib/policy-builder"
import { CloudConfigError, cloud, type CompileResult } from "@/lib/cloud"
import {
  availableFields as payloadAvailableFields,
  lifecycleToEvent as payloadLifecycleToEvent,
  lintShaclTargets as payloadLintShaclTargets,
  type FieldDescriptor as PayloadFieldDescriptor,
} from "@/lib/payload-schemas"
import type { SteerableConditionKind } from "@/lib/payload-steering"
import { getT } from "@/lib/i18n/server"
import {
  Badge, Card, CodeBlock, ErrorState,
  SubmitButton, Textarea,
} from "@/components/ui"

export const dynamic = "force-dynamic"

type Mode = "nl" | "guided" | "advanced"
const WIZARD_TOTAL = 6

/* ─────────────────────────────────────────────────────────────────────
 * New guided model (D41).
 *
 * Step 1  Lifecycle  before_tool_use / after_tool_use / pre_final
 * Step 2  ConditionKind  (varies by lifecycle, see below)
 * Step 3  Specifics  per-kind form (auto-skip when kind=none)
 * Step 4  Action  block / ask / audit / strip  (lifecycle-filtered)
 * Step 5  Name  policy id + optional description
 * Step 6  Review  plain English + IR preview
 *
 * The 8 hook events map down to 3 lifecycles for the guided path:
 *
 *   before_tool_use  →  trigger.event = PreToolUse
 *   after_tool_use   →  trigger.event = PostToolUse
 *   pre_final        →  trigger.event = Stop
 *
 * Other events (UserPromptSubmit, PreCompact, Subagent*, Session*) stay
 * in Raw mode only; guided is opinionated.
 * ───────────────────────────────────────────────────────────────────── */

type Lifecycle = "before_tool_use" | "after_tool_use" | "pre_final"
const LIFECYCLES: readonly Lifecycle[] = ["before_tool_use", "after_tool_use", "pre_final"]

const LIFECYCLE_TO_EVENT: Record<Lifecycle, string> = {
  before_tool_use: "PreToolUse",
  after_tool_use:  "PostToolUse",
  pre_final:       "Stop",
}

type ConditionKind =
  | "none"
  | "regex" | "llm_critic"
  | "fetch_domain" | "domain_allowlist"
  | "evidence_ref" | "shacl"

// D42 restructure. Tool scope is now its OWN step (Step 2). Condition
// kinds are about WHAT TO CHECK, not WHICH TOOL.
//   before_tool_use → tool scope first, then any condition that makes
//                     sense on the tool input.
//   after_tool_use  → tool scope first, then check on the tool output.
//   pre_final       → tool scope is irrelevant (fires once before the
//                     agent's final answer); Step 2 auto-skips.
//
// fetch_domain / domain_allowlist still surface as condition kinds but
// only when WebFetch is in the picked tool scope; they're convenience
// shortcuts that build a URL regex for you.
const CONDITION_KINDS_BY_LIFECYCLE: Record<Lifecycle, readonly ConditionKind[]> = {
  before_tool_use: ["none", "regex", "llm_critic", "fetch_domain", "domain_allowlist"],
  after_tool_use:  ["none", "regex", "llm_critic"],
  pre_final:       ["none", "evidence_ref", "regex", "shacl", "llm_critic"],
}

const ALL_CONDITION_KINDS: readonly ConditionKind[] = [
  "none", "regex", "llm_critic",
  "fetch_domain", "domain_allowlist",
  "evidence_ref", "shacl",
]

type Action = "block" | "ask" | "audit" | "strip"

const ACTIONS_BY_LIFECYCLE: Record<Lifecycle, readonly Action[]> = {
  before_tool_use: ["block", "ask", "audit"],
  after_tool_use:  ["block", "audit", "strip"],
  pre_final:       ["block", "ask", "audit"],
}

// Strip needs a verifier-protocol mutation channel that isn't built
// yet. Renders as a "Coming soon" card and is rejected on save.
const STRIP_AVAILABLE = false

const TOOL_PRESETS = [
  "Bash", "Read", "Edit", "Write", "Glob", "Grep",
  "NotebookEdit", "TodoWrite", "WebFetch", "WebSearch",
] as const

// Common tool list for the tool_name kind. WebFetch is the only one
// fetch_domain / domain_allowlist target.
const FETCH_TOOLS = ["WebFetch"] as const

type VerifierCategory = import("@/lib/cloud").PresetEntry["category"]
interface WiredStep {
  step: string
  description: string
  category: VerifierCategory
}

/* ─── wizard state ────────────────────────────────────────────────── */

interface WizardState {
  lifecycle?: Lifecycle
  // D42: Step 2. Which tool(s) this policy applies to.
  //   undefined / "" / "*"  →  any tool
  //   "Bash,Edit,Write"     →  alternation (matcher = "Bash|Edit|Write")
  toolScope?: string
  // D42: Step 3. What to check, with inline specifics in the same step.
  conditionKind?: ConditionKind
  // per-kind specifics (filled inline on Step 3)
  fetchDomain?: string              // fetch_domain
  allowlist?: string                // domain_allowlist (csv)
  pattern?: string                  // regex
  llmCriterion?: string             // llm_critic
  evidenceRefs?: string[]           // evidence_ref (multi)
  shaclTtl?: string                 // shacl
  action?: Action
  id?: string
  description?: string
  // P9 (D49): suppression of the cumulative-judgment steering tip is
  // session-scoped, owned by SteeringAwareField via sessionStorage.
  // Intentionally not part of URL state — a Cmd-R / paste-link should
  // not survive a dismissal.
}

/* ─── IR + summary builders ───────────────────────────────────────── */

// D43 (issue #1, P1): sentinel_re is no longer required in core IR.
// The wizard previously auto-emitted a fake "GATE_(?P<subject>…)_(?P<payload_hash>…)"
// to satisfy a named-group requirement that PR1 removed. New policies
// are authored without sentinel_re. Raw mode still lets legacy / domain
// customers carry a sentinel pattern explicitly.

function deriveMatcher(s: WizardState): string {
  // pre_final has no tool scope (fires once on the final answer).
  if (s.lifecycle === "pre_final") return "*"
  const scope = (s.toolScope ?? "").trim()
  if (!scope || scope === "*") return "*"
  // Single tool → use as-is. Multi → alternation.
  const tools = parseCsv(scope).filter(Boolean)
  if (tools.length === 0) return "*"
  if (tools.length === 1) return tools[0]
  return tools.join("|")
}

function toolScopeIncludesWebFetch(s: WizardState): boolean {
  const scope = (s.toolScope ?? "").trim()
  if (!scope || scope === "*") return true
  return parseCsv(scope).some((t) => t === "WebFetch")
}

function deriveRequires(s: WizardState): PolicyDraft["requires"] {
  const kind = s.conditionKind
  switch (kind) {
    case "none":
      return []
    case "fetch_domain": {
      const d = (s.fetchDomain ?? "").trim()
      if (!d) return []
      return [{ kind: "regex", pattern: `https?://([^/]+\\.)?${escapeRegex(d)}(/|$)` }]
    }
    case "domain_allowlist": {
      const list = parseCsv(s.allowlist ?? "")
      if (list.length === 0) return []
      const alts = list.map(escapeRegex).join("|")
      return [{ kind: "regex", pattern: `^(?!https?://([^/]+\\.)?(${alts})(/|$)).*$` }]
    }
    case "regex": {
      const p = (s.pattern ?? "").trim()
      return p ? [{ kind: "regex", pattern: p }] : []
    }
    case "llm_critic": {
      const c = (s.llmCriterion ?? "").trim()
      return c ? [{ kind: "llm_critic", criterion: c }] : []
    }
    case "evidence_ref": {
      const refs = (s.evidenceRefs ?? []).filter(Boolean)
      return refs.map((step) => ({ kind: "step", step, verdict: "pass" }))
    }
    case "shacl": {
      const ttl = (s.shaclTtl ?? "").trim()
      return ttl ? [{ kind: "shacl", shape_ttl: ttl }] : []
    }
    default:
      return []
  }
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}
function parseCsv(raw: string): string[] {
  return raw.split(",").map((s) => s.trim()).filter(Boolean)
}

// Step 4 dynamic header phrasing.
function actionHeaderEN(s: WizardState): string {
  if (s.conditionKind === "none") return "On every matching tool call,"
  if (s.lifecycle === "before_tool_use" && s.conditionKind === "regex") return "When the tool args match,"
  if (s.lifecycle === "before_tool_use" && s.conditionKind === "llm_critic") return "When the LLM critic on tool args returns NO,"
  if (s.lifecycle === "before_tool_use" && s.conditionKind === "fetch_domain") return "When the fetch domain matches,"
  if (s.lifecycle === "before_tool_use" && s.conditionKind === "domain_allowlist") return "When the domain is NOT in the allowlist,"
  if (s.lifecycle === "after_tool_use"  && s.conditionKind === "regex") return "When the output matches,"
  if (s.lifecycle === "after_tool_use"  && s.conditionKind === "llm_critic") return "When the LLM critic returns NO,"
  if (s.lifecycle === "pre_final"       && s.conditionKind === "evidence_ref") return "When the evidence ref FAILS,"
  if (s.lifecycle === "pre_final"       && s.conditionKind === "regex") return "When the final answer matches,"
  if (s.lifecycle === "pre_final"       && s.conditionKind === "shacl") return "When the SHACL shape does NOT conform,"
  if (s.lifecycle === "pre_final"       && s.conditionKind === "llm_critic") return "When the LLM critic returns NO,"
  return "When the condition fires,"
}

function actionHeaderKO(s: WizardState): string {
  if (s.conditionKind === "none") return "조건에 매칭되는 도구 호출마다,"
  if (s.lifecycle === "before_tool_use" && s.conditionKind === "regex") return "도구 인자가 패턴에 매칭될 때,"
  if (s.lifecycle === "before_tool_use" && s.conditionKind === "llm_critic") return "도구 인자에 대한 LLM critic이 NO를 반환할 때,"
  if (s.lifecycle === "before_tool_use" && s.conditionKind === "fetch_domain") return "fetch 도메인이 매칭될 때,"
  if (s.lifecycle === "before_tool_use" && s.conditionKind === "domain_allowlist") return "도메인이 허용 목록에 없을 때,"
  if (s.lifecycle === "after_tool_use"  && s.conditionKind === "regex") return "출력이 패턴에 매칭될 때,"
  if (s.lifecycle === "after_tool_use"  && s.conditionKind === "llm_critic") return "LLM critic이 NO를 반환할 때,"
  if (s.lifecycle === "pre_final"       && s.conditionKind === "evidence_ref") return "Evidence ref가 FAIL일 때,"
  if (s.lifecycle === "pre_final"       && s.conditionKind === "regex") return "최종 응답이 패턴에 매칭될 때,"
  if (s.lifecycle === "pre_final"       && s.conditionKind === "shacl") return "SHACL shape에 conform 하지 않을 때,"
  if (s.lifecycle === "pre_final"       && s.conditionKind === "llm_critic") return "LLM critic이 NO를 반환할 때,"
  return "조건이 발동할 때,"
}

function plainSummary(s: WizardState, locale: "ko" | "en"): string {
  const ko = locale === "ko"
  const header = ko ? actionHeaderKO(s) : actionHeaderEN(s)
  const act = s.action ?? "audit"
  const lifeLabel = ko
    ? ({ before_tool_use: "도구 실행 전", after_tool_use: "도구 실행 후", pre_final: "최종 응답 직전" }[s.lifecycle ?? "before_tool_use"])
    : ({ before_tool_use: "before a tool runs", after_tool_use: "after a tool runs", pre_final: "before the final answer" }[s.lifecycle ?? "before_tool_use"])
  const actLabel = ko
    ? ({ block: "차단", ask: "사람 승인 요청", audit: "원장에만 기록", strip: "출력에서 제거" }[act])
    : ({ block: "block", ask: "ask a human", audit: "record to the ledger only", strip: "strip from the output" }[act])
  return ko
    ? `${lifeLabel}, ${header} 이 정책은 ${actLabel} 합니다.`
    : `${capitalize(lifeLabel)}: ${header} this policy will ${actLabel}.`
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}

function summaryForBackend(s: WizardState): string {
  const ev = LIFECYCLE_TO_EVENT[s.lifecycle ?? "before_tool_use"]
  const m = deriveMatcher(s)
  const condK = s.conditionKind ?? "none"
  return `${s.action ?? "audit"} on ${ev}|${m} (cond=${condK})`
}

/* ─── server actions ─────────────────────────────────────────────── */

async function compileNL(formData: FormData): Promise<void> {
  "use server"
  const nl = String(formData.get("nl") ?? "").trim()
  if (!nl) {
    redirect("/policies/new?mode=nl&err=invalid_input&nl=" + encodeURIComponent(nl))
  }
  let result: CompileResult
  try {
    result = await cloud.compilePolicy(nl)
  } catch (e: unknown) {
    redirect(`/policies/new?mode=nl&err=${codeForError(e)}&nl=${encodeURIComponent(nl)}`)
  }
  const payload = JSON.stringify({ nl, ...result })
  if (payload.length > 1500) {
    const { cookies } = await import("next/headers")
    cookies().set({
      name: "magi-cp-compile-result",
      value: payload,
      path: "/policies/new",
      sameSite: "lax",
      maxAge: 60 * 5,
    })
    revalidatePath("/policies/new")
    redirect("/policies/new?mode=nl&msg=large")
  }
  revalidatePath("/policies/new")
  redirect(`/policies/new?mode=nl&r=${encodeURIComponent(payload)}`)
}

async function persistDraft(draft: PolicyDraft, source: string): Promise<void> {
  const errs = validateDraft(draft)
  if (errs.length > 0) { redirect("/policies/new?err=invalid_input"); return }
  try { validatePolicyId(draft.id) }
  catch { redirect("/policies/new?err=invalid_id"); return }
  let adminKey: string
  try {
    if (!process.env.MAGI_CP_ADMIN_API_KEY) {
      console.error("dashboard server: MAGI_CP_ADMIN_API_KEY not set")
      throw new CloudConfigError()
    }
    adminKey = process.env.MAGI_CP_ADMIN_API_KEY
  } catch (e) {
    redirect(`/policies/new?err=${codeForError(e)}`); return
  }
  const idForUrl = draft.id.split("/").map(encodeURIComponent).join("/")
  try {
    const r = await fetch(
      `${process.env.MAGI_CP_CLOUD_URL || "http://127.0.0.1:8787"}/policies/${idForUrl}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-Admin-Api-Key": adminKey },
        cache: "no-store",
        body: JSON.stringify({ policy: draft, source, enabled: true }),
        signal: AbortSignal.timeout(8000),
      },
    )
    if (!r.ok) {
      console.error(`cloud ${r.status} PUT /policies: ${await r.text().catch(() => "")}`)
      redirect(`/policies/new?err=${codeForError(new Error(`cloud ${r.status}`))}`); return
    }
  } catch (e) {
    redirect(`/policies/new?err=${codeForError(e)}`); return
  }
  try {
    const { cookies } = await import("next/headers")
    cookies().delete("magi-cp-compile-result")
  } catch { /* no-op */ }
  revalidatePath("/policies")
  redirect(`/policies/${encodeURI(draft.id)}?msg=saved`)
}

/** P7 (issue #1, P0 #3 / P1 #4): cross-check every SHACL requires
 * entry on a draft against the payload schema menu for the draft's
 * trigger. Returns a flat list of issue strings (empty list = clean).
 *
 * Soft-fail: callers surface it as a banner. Hard-fail when
 * `MAGI_CP_STRICT_SHACL_TARGETS=1` is set in the dashboard env. The
 * Python `Policy.validate()` enforces the same flag canonically — this
 * client-side check is a fast author-time hint so the redirect lands
 * before the round-trip to the cloud. */
function lintDraftShaclTargets(draft: PolicyDraft): string[] {
  const issues: string[] = []
  const ev = draft.trigger?.event ?? "PreToolUse"
  const matcher = draft.trigger?.matcher
  for (const [i, r] of (draft.requires ?? []).entries()) {
    const kind = "kind" in r ? r.kind : "step"
    if (kind !== "shacl") continue
    const ttl = ("shape_ttl" in r ? r.shape_ttl : "") ?? ""
    if (!ttl.trim()) continue
    for (const msg of payloadLintShaclTargets(ttl, ev, matcher)) {
      issues.push(`requires[${i}]: ${msg}`)
    }
  }
  return issues
}

async function saveCompiled(formData: FormData): Promise<void> {
  "use server"
  let draft: PolicyDraft
  try { draft = JSON.parse(String(formData.get("ir_json") ?? "{}")) }
  catch { redirect("/policies/new?err=invalid_input"); return }
  const source = String(formData.get("source") ?? "org")
  await persistDraft(draft, source)
}

async function saveAdvanced(formData: FormData): Promise<void> {
  "use server"
  let draft: PolicyDraft
  try { draft = JSON.parse(String(formData.get("draft_json") ?? "{}")) }
  catch { redirect("/policies/new?err=invalid_input"); return }
  const source = String(formData.get("source") ?? "org")
  // P7 (issue #1, P1 #4): hard-fail SHACL shapes targeting paths the
  // runtime never delivers when MAGI_CP_STRICT_SHACL_TARGETS=1 is set.
  // Default mode is silent-warn (no block, no banner on this codepath —
  // server actions can't carry data back without a redirect that loses
  // the success message; canonical lint surface remains `Policy
  // .validate()` server-side on the cloud, which logs the issues even
  // when MAGI_CP_STRICT_SHACL_TARGETS is unset). Silent fail-open is
  // exactly what P7 was built to close — the cloud enforces strict at
  // the policy-store boundary too.
  const shaclIssues = lintDraftShaclTargets(draft)
  if (
    shaclIssues.length > 0 &&
    process.env.MAGI_CP_STRICT_SHACL_TARGETS === "1"
  ) {
    const params = new URLSearchParams()
    params.set("mode", "advanced")
    params.set("err", "shacl_unknown_paths")
    params.set("paths", shaclIssues.slice(0, 8).join(" | "))
    try { params.set("draft", encodeURIComponent(JSON.stringify(draft))) }
    catch { /* over-length draft → fall back to err display only */ }
    redirect(`/policies/new?${params.toString()}`)
    return
  }
  await persistDraft(draft, source)
}

/** Advance the wizard one step. Ferries all known fields via URL params
 * so browser back works as "previous step." Auto-skips Step 3 when
 * conditionKind === "none". */
async function advanceWizard(formData: FormData): Promise<void> {
  "use server"
  const params = new URLSearchParams()
  params.set("mode", "guided")
  const stepIn = Number(formData.get("_step") ?? "1")
  let nextStep = stepIn + 1

  // Evidence-ref multi-pick checkboxes ride as repeated `evidence_ref`
  // entries. Step 3 is authoritative when it submits.
  const evChecks = formData
    .getAll("evidence_ref")
    .filter((v): v is string => typeof v === "string")
    .map((v) => v.trim())
    .filter(Boolean)
  const evCarry = (formData.get("evidence_refs")?.toString() ?? "")
    .split(",").map((s) => s.trim()).filter(Boolean)
  const evMerged: string[] = []
  const evSource = stepIn === 3 ? evChecks : [...evChecks, ...evCarry]
  for (const v of evSource) if (!evMerged.includes(v)) evMerged.push(v)
  if (evMerged.length > 0) params.set("evidence_refs", evMerged.join(","))

  // Tool scope (Step 2). Mode radio (`any` / `specific`) decides the
  // shape; chip + custom CSV merge when `specific`.
  const scopeMode = String(formData.get("toolScope_mode") ?? "")
  const scopeChipsRaw = formData
    .getAll("toolScope_chip")
    .filter((v): v is string => typeof v === "string")
    .map((v) => v.trim())
    .filter(Boolean)
  const scopeCustomRaw = String(formData.get("toolScope_custom") ?? "")
    .split(",").map((s) => s.trim()).filter(Boolean)
  const scopeSubmitted = stepIn === 2 && scopeMode !== ""
  if (scopeSubmitted) {
    if (scopeMode === "any") {
      params.set("toolScope", "*")
    } else if (scopeMode === "specific") {
      const merged: string[] = []
      for (const v of [...scopeChipsRaw, ...scopeCustomRaw]) {
        if (!merged.includes(v)) merged.push(v)
      }
      if (merged.length > 0) params.set("toolScope", merged.join(","))
    }
  }

  for (const [k, v] of formData.entries()) {
    if (typeof v !== "string") continue
    if (k.startsWith("$ACTION") || k === "_step") continue
    if (k === "evidence_ref" || k === "evidence_refs") continue
    if (k === "toolScope_mode" || k === "toolScope_chip" || k === "toolScope_custom") continue
    if (k === "toolScope" && scopeSubmitted) continue
    if (!v.trim()) continue
    params.set(k, v.trim())
  }

  // pre_final has no tool scope; auto-skip Step 2.
  const lifecycle = params.get("lifecycle") as Lifecycle | null
  if (stepIn === 1 && lifecycle === "pre_final") {
    nextStep = 3
  }

  // P9 (D49): cumulative-tip dismissal lives in sessionStorage owned
  // by SteeringAwareField; nothing to scrub off the URL here.

  params.set("step", String(nextStep))
  redirect(`/policies/new?${params.toString()}`)
}

/** Final step. Build a PolicyDraft from URL state and PUT. */
async function saveWizard(formData: FormData): Promise<void> {
  "use server"
  const lifecycle = String(formData.get("lifecycle") ?? "before_tool_use") as Lifecycle
  const conditionKindRaw = String(formData.get("conditionKind") ?? "")
  const conditionKind = (ALL_CONDITION_KINDS as readonly string[]).includes(conditionKindRaw)
    ? (conditionKindRaw as ConditionKind) : "none"
  const action = String(formData.get("action") ?? "audit") as Action

  if (action === "strip" && !STRIP_AVAILABLE) {
    redirect("/policies/new?mode=guided&step=4&err=strip_unsupported"); return
  }

  // Resolve toolScope: prefer the hidden carry (most steps) over the
  // Step 2 mode submission. Step 2 itself runs through advanceWizard
  // which already wrote the merged value into the URL.
  const toolScopeRaw = String(formData.get("toolScope") ?? "").trim()
  const toolScope = toolScopeRaw || undefined
  const state: WizardState = {
    lifecycle,
    toolScope,
    conditionKind,
    fetchDomain: String(formData.get("fetchDomain") ?? "").trim() || undefined,
    allowlist: String(formData.get("allowlist") ?? "").trim() || undefined,
    pattern: String(formData.get("pattern") ?? "").trim() || undefined,
    llmCriterion: String(formData.get("llmCriterion") ?? "").trim() || undefined,
    evidenceRefs: (formData.getAll("evidence_ref") as string[])
      .map((v) => v.trim()).filter(Boolean)
      .concat((String(formData.get("evidence_refs") ?? ""))
        .split(",").map((s) => s.trim()).filter(Boolean)),
    shaclTtl: String(formData.get("shaclTtl") ?? "").trim() || undefined,
    action,
    id: String(formData.get("id") ?? "").trim(),
    description: String(formData.get("description") ?? "").trim() || undefined,
  }
  // Validate specifics per kind.
  const specErr = validateSpecifics(state)
  if (specErr) {
    redirect(`/policies/new?mode=guided&step=3&err=${specErr}`); return
  }
  if (!state.id) {
    redirect("/policies/new?mode=guided&step=5&err=invalid_input"); return
  }

  const event = LIFECYCLE_TO_EVENT[lifecycle] as PolicyDraft["trigger"]["event"]
  const matcher = deriveMatcher(state)
  const requires = deriveRequires(state) as PolicyDraft["requires"]
  // Strip is reserved. Backend doesn't have a payload-mutation channel
  // so we map to "audit" with no requires. The validation above already
  // blocked strip when STRIP_AVAILABLE=false.
  const irAction = action === "strip" ? "audit" : action
  const draft: PolicyDraft = {
    id: state.id!,
    version: "0.1",
    description: state.description || summaryForBackend(state),
    trigger: { host: "claude-code", event, matcher },
    // D43: no sentinel_re. Runtime synthesizes subject/payload_hash
    // from request context. Legal customers carrying a sentinel_re
    // pattern still author it explicitly via Raw mode.
    requires,
    action: irAction as PolicyDraft["action"],
    on_signature_invalid: "deny",
    gate_binary: "/usr/local/bin/magi-gate.sh",
  }
  const source = String(formData.get("source") ?? "org")
  // P7 (issue #1, P0 #3 / P1 #4): when the guided wizard built a
  // SHACL requires entry, lint the shape against the chosen trigger.
  // The chip stub-inserter targets canonical paths so a clean wizard
  // flow always passes — anything failing here means the author
  // hand-edited the textarea to a non-existent path. Hard-fail only
  // under MAGI_CP_STRICT_SHACL_TARGETS=1; default mode lets the cloud
  // record the warning to its log.
  if (process.env.MAGI_CP_STRICT_SHACL_TARGETS === "1") {
    const shaclIssues = lintDraftShaclTargets(draft)
    if (shaclIssues.length > 0) {
      const params = new URLSearchParams()
      params.set("mode", "guided")
      params.set("step", "3")
      params.set("err", "shacl_unknown_paths")
      params.set("paths", shaclIssues.slice(0, 8).join(" | "))
      redirect(`/policies/new?${params.toString()}`)
      return
    }
  }
  await persistDraft(draft, source)
}

function validateSpecifics(s: WizardState): string | null {
  switch (s.conditionKind) {
    case "none":
      return null
    case "fetch_domain":
      if (!s.fetchDomain) return "invalid_input"
      return null
    case "domain_allowlist":
      if (!s.allowlist || parseCsv(s.allowlist).length === 0) return "invalid_input"
      return null
    case "regex":
      if (!s.pattern) return "invalid_input"
      return null
    case "llm_critic":
      if (!s.llmCriterion) return "invalid_input"
      return null
    case "evidence_ref":
      if (!s.evidenceRefs || s.evidenceRefs.length === 0) return "invalid_input"
      return null
    case "shacl":
      if (!s.shaclTtl) return "invalid_input"
      return null
    default:
      return "invalid_input"
  }
}

/* ─── decoders for compile result ─────────────────────────────────── */

function decodeResult(r: string | undefined): (CompileResult & { nl: string }) | null {
  if (!r) return null
  try {
    const obj = JSON.parse(decodeURIComponent(r))
    if (typeof obj !== "object" || !obj || !obj.ir || !obj.review) return null
    return obj as CompileResult & { nl: string }
  } catch { return null }
}
async function readCookieResult(): Promise<(CompileResult & { nl: string }) | null> {
  const { cookies } = await import("next/headers")
  const raw = cookies().get("magi-cp-compile-result")?.value
  if (!raw) return null
  try {
    const obj = JSON.parse(raw)
    if (!obj?.ir || !obj?.review) return null
    return obj as CompileResult & { nl: string }
  } catch { return null }
}
function _parseDraftQuery(draft: string | undefined): PolicyDraft | null {
  if (!draft) return null
  try {
    const obj = JSON.parse(decodeURIComponent(draft))
    if (typeof obj !== "object" || !obj) return null
    return obj as PolicyDraft
  } catch { return null }
}

/* ─── page ────────────────────────────────────────────────────────── */

export default async function NewPolicyPage({
  searchParams,
}: { searchParams: Record<string, string | undefined> }) {
  const { t, locale } = await getT()
  const flash = resolveFlash(undefined, searchParams.err)

  const rawMode = searchParams.mode
  const mode: Mode | null =
    rawMode === "advanced" || (rawMode === undefined && searchParams.draft != null)
      ? "advanced"
      : rawMode === "nl"
        ? "nl"
        : rawMode === "guided"
          ? "guided"
          : null

  const fromQuery = decodeResult(searchParams.r)
  const compileResult =
    mode === "nl"
      ? fromQuery ?? (searchParams.msg === "large" ? await readCookieResult() : null)
      : null
  const nl = compileResult?.nl ?? searchParams.nl ?? ""

  const initialDraft =
    (compileResult?.ir as PolicyDraft | undefined) ??
    _parseDraftQuery(searchParams.draft) ??
    null

  let wiredSteps: WiredStep[] = []
  // P8: vendor catalog step names (preset id, both hyphen + snake_case
  // form). Passed to PolicyBuilder so an author who types one of these
  // without the `preview:` prefix gets the "not active — enable under
  // /presets" inline error mirroring the backend 422.
  let vendorSteps: string[] = []
  if (mode === "advanced" || mode === "guided") {
    try {
      const presets = await cloud.listPresets()
      const seen = new Set<string>()
      const vendorSeen = new Set<string>()
      for (const p of presets) {
        if (p.enforcement === "enforcing" && p.step && !seen.has(p.step)) {
          seen.add(p.step)
          wiredSteps.push({ step: p.step, description: p.description, category: p.category })
        } else if (p.enforcement === "preview") {
          // Vendor preview entries carry `id` (hyphen form) — record
          // both forms so authors hit the inactive branch regardless
          // of which slug they typed.
          const id = (p as { id?: string }).id
          if (id && !vendorSeen.has(id)) {
            vendorSeen.add(id)
            vendorSteps.push(id)
            const snake = id.replace(/-/g, "_")
            if (snake !== id && !vendorSeen.has(snake)) {
              vendorSeen.add(snake)
              vendorSteps.push(snake)
            }
          }
        }
      }
      wiredSteps.sort((a, b) => a.step.localeCompare(b.step))
      vendorSteps.sort()
    } catch { /* best-effort */ }
  }

  // P7 (issue #1, P0 #3 / P1 #4): SHACL lint hard-fail banner. Only
  // surfaces under MAGI_CP_STRICT_SHACL_TARGETS=1 (the saveAdvanced /
  // saveWizard server actions hard-fail to err=shacl_unknown_paths in
  // that mode). `paths` carries up to 8 issue strings joined by " | ".
  const shaclHardErr =
    searchParams.err === "shacl_unknown_paths"
      ? (searchParams.paths ?? "")
      : null

  return (
    <>
      {flash?.kind === "error" && (
        <ErrorState title={flash.text} severity="error" />
      )}

      {shaclHardErr && (
        <ErrorState
          title={
            locale === "ko"
              ? `SHACL shape이 런타임이 전달하지 않는 path를 target합니다 (저장 차단됨, MAGI_CP_STRICT_SHACL_TARGETS=1).`
              : `SHACL shape targets a path the runtime does not deliver (save blocked, MAGI_CP_STRICT_SHACL_TARGETS=1).`
          }
          severity="error"
        />
      )}
      {shaclHardErr && (
        <pre className="mt-2 mb-4 whitespace-pre-wrap rounded-md border border-red-400/40 bg-red-50 px-3 py-2 text-xs font-mono text-red-900">
          {shaclHardErr.split(" | ").join("\n")}
        </pre>
      )}

      {mode === null && <PickerLanding t={t} locale={locale === "ko" ? "ko" : "en"} />}

      {mode === "nl" && (
        <AuthoringShell
          t={t}
          modeTitle={t("newPolicy.mode.nlAuthoring")}
          info={{
            tone: "info",
            title: t("newPolicy.nl.info.title"),
            body: t("newPolicy.nl.info.body"),
          }}
        >
          <Card>
            {/* D52e: collapsible authoring guide. Closed by default,
                expanded/collapsed state persisted per-user in
                localStorage. Lives only on the NL compose mode: the
                Guided wizard and Raw IR PolicyBuilder are structured
                already and don't need this scaffolding. */}
            <NlAuthoringGuide t={t} targetTextareaId="nl" />
            <form action={compileNL}>
              <Textarea
                id="nl"
                name="nl"
                rows={4}
                defaultValue={nl}
                label={t("compile.field.label")}
                placeholder={t("compile.field.placeholder")}
                required
                spellCheck={false}
                autoComplete="off"
                monospace
              />
              <div className="mt-3 flex items-center gap-2">
                <SubmitButton
                  label={t("compile.submit")}
                  pendingLabel={t("compile.submit.pending")}
                  progressHint={t("compile.progressHint")}
                />
                {compileResult && (
                  <Link href="/policies/new?mode=nl" className="text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]">
                    {t("newPolicy.composeNL.clear")}
                  </Link>
                )}
              </div>
            </form>
          </Card>

          {compileResult && (
            <CompileResultBlock t={t} data={compileResult} saveAction={saveCompiled} />
          )}
        </AuthoringShell>
      )}

      {mode === "guided" && (
        <GuidedWizard
          t={t}
          locale={locale === "ko" ? "ko" : "en"}
          wiredSteps={wiredSteps.length > 0 ? wiredSteps : [{ step: "citation_verify", description: "Cite verifier", category: "FACT" }]}
          searchParams={searchParams}
          advanceAction={advanceWizard}
          saveAction={saveWizard}
        />
      )}

      {mode === "advanced" && (
        <AuthoringShell
          t={t}
          modeTitle={t("newPolicy.mode.advancedAuthoring")}
          info={{
            tone: "warn",
            title: t("newPolicy.advanced.info.title"),
            body: t("newPolicy.advanced.info.body"),
          }}
        >
          <Card>
            <PolicyBuilder
              submitAction={saveAdvanced}
              initial={initialDraft}
              wiredSteps={wiredSteps.map((w) => w.step)}
              vendorSteps={vendorSteps}
              labels={{
                irFields: "IR fields",
                compiledPreview: "Compiled preview",
                compiledPreviewHint:
                  "Live mirror of what the cloud compiler will emit. The cloud is authoritative.",
                id: "id",
                description: "description",
                triggerEvent: "trigger.event",
                triggerMatcher: "trigger.matcher",
                onMissing: "action",
                sentinelRe: "sentinel_re",
                sentinelReHint:
                  "Python regex; optional. Named groups are illustrative only — the runtime no longer reads specific group names.",
                requires: "requires (evidence)",
                addRequirement: "add requirement",
                removeRequirement: t("policies.disable"),
                source: t("policies.source"),
                save: t("newPolicy.savePolicy"),
                saving: t("newPolicy.saving"),
                fixIssueOne: "Fix 1 validation issue",
                fixIssueMany: "Fix {n} validation issues",
                unsavedWarning: t("newPolicy.unsavedWarning"),
                placeholderId: "legal-filing/v1",
                placeholderMatcher: "Bash | mcp__court__file",
              }}
            />
          </Card>
        </AuthoringShell>
      )}
    </>
  )
}

/* ─── picker landing ─────────────────────────────────────────────── */

/* Quick-start templates surfaced in the picker landing.
 * Clicking one lands the user on Guided Step 5 (Naming) with the
 * lifecycle / conditionKind / specifics / action pre-filled. The user
 * still picks an id and reviews before saving. */
type Template = {
  id: string
  ko: { title: string; sub: string }
  en: { title: string; sub: string }
  params: Record<string, string>
}
const TEMPLATES: readonly Template[] = [
  {
    id: "block-aws-keys",
    ko: { title: "AWS 키 누출 차단", sub: "Bash 인자에서 AKIA…가 보이면 차단" },
    en: { title: "Block AWS keys", sub: "Block any tool call whose args contain AKIA…" },
    params: {
      lifecycle: "before_tool_use", toolScope: "*",
      conditionKind: "regex", pattern: "AKIA[A-Z0-9]{16}",
      action: "block",
    },
  },
  {
    id: "block-sudo",
    ko: { title: "sudo 차단", sub: "Bash에서 sudo 실행 시 차단" },
    en: { title: "Block sudo", sub: "Block any Bash call containing `sudo`" },
    params: {
      lifecycle: "before_tool_use", toolScope: "Bash",
      conditionKind: "regex", pattern: "(^|\\s)sudo\\s",
      action: "block",
    },
  },
  {
    id: "audit-all-bash",
    ko: { title: "Bash 전부 감사", sub: "Bash 호출 시 원장에만 기록 (관찰 모드)" },
    en: { title: "Audit every Bash", sub: "Record every Bash call to the ledger (observe-only)" },
    params: {
      lifecycle: "before_tool_use", toolScope: "Bash",
      conditionKind: "none", action: "audit",
    },
  },
  {
    id: "webfetch-allowlist",
    ko: { title: "WebFetch allowlist", sub: "허용 외 도메인은 사람 승인" },
    en: { title: "WebFetch allowlist", sub: "Ask a human for any non-allowlisted domain" },
    params: {
      lifecycle: "before_tool_use", toolScope: "WebFetch",
      conditionKind: "domain_allowlist",
      allowlist: "github.com, npmjs.com, api.openai.com",
      action: "ask",
    },
  },
  {
    id: "require-citations",
    ko: { title: "인용 필수", sub: "최종 응답에 citation 검증 통과 강제" },
    en: { title: "Require citations", sub: "Block final answer if citation_verify fails" },
    params: {
      lifecycle: "pre_final", conditionKind: "evidence_ref",
      evidence_refs: "citation_verify", action: "block",
    },
  },
  {
    id: "no-secret-in-answer",
    ko: { title: "응답 시크릿 차단", sub: "최종 응답에 시크릿 패턴이 있으면 차단" },
    en: { title: "No secrets in answer", sub: "Block any final answer that contains AKIA… patterns" },
    params: {
      lifecycle: "pre_final", conditionKind: "regex",
      pattern: "AKIA[A-Z0-9]+", action: "block",
    },
  },
]

function templateHref(t: Template): string {
  const p = new URLSearchParams({ mode: "guided", step: "5" })
  for (const [k, v] of Object.entries(t.params)) p.set(k, v)
  // suggest a sensible default id from the template id
  p.set("id", `${t.id}/v1`)
  return `/policies/new?${p.toString()}`
}

function PickerLanding({
  t, locale,
}: {
  locale: "ko" | "en"
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const ko = locale === "ko"
  return (
    <section className="space-y-5">
      {/* Quick start templates row */}
      <div className="rounded-2xl border border-[var(--color-accent)]/20 bg-[var(--color-accent)]/[0.02] p-5 shadow-sm">
        <header className="mb-3 flex items-start justify-between">
          <div>
            <p className="text-[11px] font-bold uppercase tracking-[0.16em] text-[var(--color-accent)]">
              {ko ? "빠른 시작" : "Quick start"}
            </p>
            <h2 className="mt-1 text-sm font-semibold text-[var(--color-text-primary)] m-0">
              {ko ? "흔한 시나리오에서 바로 시작" : "Start from a common scenario"}
            </h2>
            <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
              {ko
                ? "클릭하면 wizard의 마지막 단계로 이동합니다. 이름만 정하고 저장."
                : "Each one jumps to the last wizard step pre-filled. Name it and save."}
            </p>
          </div>
          <Link
            href="/policies"
            aria-label={t("newPolicy.picker.close")}
            className="rounded-lg p-1.5 text-[var(--color-text-tertiary)] hover:bg-black/[0.04] hover:text-[var(--color-text-primary)] transition-colors"
          >
            <XMarkIcon className="h-4 w-4" />
          </Link>
        </header>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {TEMPLATES.map((tpl) => (
            <Link
              key={tpl.id}
              href={templateHref(tpl)}
              className="group flex flex-col gap-1 rounded-xl border border-black/[0.06] bg-white px-3 py-2.5 text-left transition-colors hover:border-[var(--color-accent)] hover:bg-[var(--color-accent)]/[0.04] hover:no-underline"
            >
              <span className="text-sm font-semibold text-[var(--color-text-primary)] leading-snug">
                {ko ? tpl.ko.title : tpl.en.title}
              </span>
              <span className="text-[11px] text-[var(--color-text-tertiary)] leading-snug">
                {ko ? tpl.ko.sub : tpl.en.sub}
              </span>
            </Link>
          ))}
        </div>
      </div>

      {/* 3-mode picker */}
      <div className="rounded-2xl border border-black/[0.08] bg-white p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] m-0 mb-1">
          {ko ? "직접 만들기" : "Build it yourself"}
        </h2>
        <p className="text-xs text-[var(--color-text-secondary)] mb-4">
          {t("newPolicy.picker.subtitle")}
        </p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <ChoiceCard
            href="/policies/new?mode=nl"
            icon={<SparklesIcon className="h-5 w-5" />}
            label={t("newPolicy.picker.nl.label")}
            description={t("newPolicy.picker.nl.description")}
            backing={t("newPolicy.picker.nl.backing")}
          />
          <ChoiceCard
            href="/policies/new?mode=guided&step=1"
            icon={<AdjustmentsHorizontalIcon className="h-5 w-5" />}
            label={t("newPolicy.picker.guided.label")}
            description={t("newPolicy.picker.guided.description")}
            backing={t("newPolicy.picker.guided.backing")}
          />
          <ChoiceCard
            href="/policies/new?mode=advanced"
            icon={<CodeBracketIcon className="h-5 w-5" />}
            label={t("newPolicy.picker.advanced.label")}
            description={t("newPolicy.picker.advanced.description")}
            backing={t("newPolicy.picker.advanced.backing")}
          />
        </div>
      </div>
    </section>
  )
}

function ChoiceCard({
  href, icon, label, description, backing,
}: {
  href: string
  icon: React.ReactNode
  label: string
  description: string
  backing: string
}) {
  return (
    <Link
      href={href}
      className="flex flex-col items-start gap-2 rounded-xl border border-black/[0.08] bg-white p-4 text-left transition-colors hover:border-[var(--color-accent)] hover:bg-[var(--color-accent)]/[0.05] hover:no-underline"
    >
      <span className="rounded-lg bg-[var(--color-accent)]/10 p-2 text-[var(--color-accent)]">
        {icon}
      </span>
      <span className="text-sm font-semibold text-[var(--color-text-primary)]">
        {label}
      </span>
      <span className="text-xs leading-relaxed text-[var(--color-text-secondary)]">
        {description}
      </span>
      <span className="mt-1 rounded bg-black/[0.04] px-1.5 py-0.5 text-[10px] font-mono text-[var(--color-text-tertiary)]">
        → {backing}
      </span>
    </Link>
  )
}

/* ─── authoring shell ────────────────────────────────────────────── */

function AuthoringShell({
  t, modeTitle, info, children,
}: {
  modeTitle: string
  info: { tone: "info" | "warn"; title: string; body: string }
  children: React.ReactNode
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const infoCls = info.tone === "warn"
    ? "border-amber-500/25 bg-amber-500/[0.06] text-amber-900"
    : "border-blue-500/25 bg-blue-500/[0.06] text-blue-900"
  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between gap-3 flex-wrap">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--color-text-tertiary)]">
            {t("newPolicy.authoringPrefix")}
          </p>
          <h1 className="text-lg font-bold text-[var(--color-text-primary)] m-0 mt-0.5">
            {modeTitle}
          </h1>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <Link href="/policies/new" className="inline-flex items-center gap-1 text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]">
            <ArrowLeftIcon className="h-3.5 w-3.5" />
            {t("newPolicy.pickDifferent")}
          </Link>
          <Link href="/policies" className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]">
            {t("newPolicy.close")}
          </Link>
        </div>
      </div>

      <div className={`rounded-xl border px-4 py-3 ${infoCls}`}>
        <p className="text-sm font-semibold mb-1">{info.title}</p>
        <p className="text-xs leading-relaxed">{info.body}</p>
      </div>

      {children}
    </div>
  )
}

/* ─── wizard ─────────────────────────────────────────────────────── */

function buildWizardHref(state: WizardState, step: number): string {
  const params = new URLSearchParams()
  params.set("mode", "guided")
  params.set("step", String(step))
  if (state.lifecycle) params.set("lifecycle", state.lifecycle)
  if (state.conditionKind) params.set("conditionKind", state.conditionKind)
  if (state.toolScope) params.set("toolScope", state.toolScope)
  if (state.fetchDomain) params.set("fetchDomain", state.fetchDomain)
  if (state.allowlist) params.set("allowlist", state.allowlist)
  if (state.pattern) params.set("pattern", state.pattern)
  if (state.llmCriterion) params.set("llmCriterion", state.llmCriterion)
  if (state.evidenceRefs && state.evidenceRefs.length > 0) {
    params.set("evidence_refs", state.evidenceRefs.join(","))
  }
  if (state.shaclTtl) params.set("shaclTtl", state.shaclTtl)
  if (state.action) params.set("action", state.action)
  if (state.id) params.set("id", state.id)
  if (state.description) params.set("description", state.description)
  return `/policies/new?${params.toString()}`
}

function HiddenState({ state }: { state: WizardState }) {
  return (
    <>
      {state.lifecycle && <input type="hidden" name="lifecycle" value={state.lifecycle} />}
      {state.conditionKind && <input type="hidden" name="conditionKind" value={state.conditionKind} />}
      {state.toolScope && <input type="hidden" name="toolScope" value={state.toolScope} />}
      {state.fetchDomain && <input type="hidden" name="fetchDomain" value={state.fetchDomain} />}
      {state.allowlist && <input type="hidden" name="allowlist" value={state.allowlist} />}
      {state.pattern && <input type="hidden" name="pattern" value={state.pattern} />}
      {state.llmCriterion && <input type="hidden" name="llmCriterion" value={state.llmCriterion} />}
      {state.evidenceRefs && state.evidenceRefs.length > 0 && (
        <input type="hidden" name="evidence_refs" value={state.evidenceRefs.join(",")} />
      )}
      {state.shaclTtl && <input type="hidden" name="shaclTtl" value={state.shaclTtl} />}
      {state.action && <input type="hidden" name="action" value={state.action} />}
      {state.id && <input type="hidden" name="id" value={state.id} />}
      {state.description && <input type="hidden" name="description" value={state.description} />}
    </>
  )
}

function WizardHeader({
  t, step, total,
}: {
  step: number; total: number
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  return (
    <div className="flex items-center justify-between mb-6">
      <div className="flex items-center gap-3">
        <Link href="/policies/new" className="inline-flex items-center gap-1 text-sm text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]">
          <ArrowLeftIcon className="h-4 w-4" />
          {t("newPolicy.pickDifferent")}
        </Link>
      </div>
      <div className="flex items-center gap-2">
        {Array.from({ length: total }).map((_, i) => {
          const n = i + 1
          const past = n < step
          const current = n === step
          return (
            <span
              key={n}
              aria-hidden="true"
              className={
                current
                  ? "h-2 w-6 rounded-full bg-[var(--color-accent)]"
                  : past
                    ? "h-2 w-2 rounded-full bg-[var(--color-accent)]/40"
                    : "h-2 w-2 rounded-full bg-gray-300"
              }
            />
          )
        })}
        <span className="ml-2 text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)] tabular-nums">
          {step} / {total}
        </span>
      </div>
    </div>
  )
}

function GuidedWizard({
  t, locale, wiredSteps, searchParams, advanceAction, saveAction,
}: {
  wiredSteps: WiredStep[]
  searchParams: Record<string, string | undefined>
  locale: "ko" | "en"
  advanceAction: (fd: FormData) => Promise<void>
  saveAction: (fd: FormData) => Promise<void>
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const step = Math.max(1, Math.min(WIZARD_TOTAL, Number(searchParams.step ?? 1)))
  const lifecycleParam = searchParams.lifecycle
  const lifecycle = (LIFECYCLES as readonly string[]).includes(lifecycleParam ?? "")
    ? (lifecycleParam as Lifecycle) : undefined
  const condKParam = searchParams.conditionKind
  const conditionKind = (ALL_CONDITION_KINDS as readonly string[]).includes(condKParam ?? "")
    ? (condKParam as ConditionKind) : undefined
  const actionParam = searchParams.action
  const action = (["block", "ask", "audit", "strip"] as const).includes(actionParam as Action)
    ? (actionParam as Action) : undefined
  const evidenceRefs = (searchParams.evidence_refs ?? "").split(",").map((s) => s.trim()).filter(Boolean)

  const state: WizardState = {
    lifecycle,
    toolScope: searchParams.toolScope || undefined,
    conditionKind,
    fetchDomain: searchParams.fetchDomain || undefined,
    allowlist: searchParams.allowlist || undefined,
    pattern: searchParams.pattern || undefined,
    llmCriterion: searchParams.llmCriterion || undefined,
    evidenceRefs: evidenceRefs.length > 0 ? evidenceRefs : undefined,
    shaclTtl: searchParams.shaclTtl || undefined,
    action,
    id: searchParams.id || undefined,
    description: searchParams.description || undefined,
  }

  // pre_final auto-skips Step 2 (tool scope is irrelevant).
  const effectiveStep =
    state.lifecycle === "pre_final" && step === 2 ? 3 : step

  return (
    <div className="max-w-2xl mx-auto">
      <WizardHeader t={t} step={effectiveStep} total={WIZARD_TOTAL} />

      {effectiveStep === 1 && <Step1Lifecycle t={t} locale={locale} state={state} action={advanceAction} />}
      {effectiveStep === 2 && <Step2ToolScope t={t} locale={locale} state={state} action={advanceAction} />}
      {effectiveStep === 3 && <Step3Condition t={t} locale={locale} state={state} wiredSteps={wiredSteps} action={advanceAction} />}
      {effectiveStep === 4 && <Step4Action t={t} locale={locale} state={state} action={advanceAction} />}
      {effectiveStep === 5 && <Step5Naming t={t} state={state} action={advanceAction} />}
      {effectiveStep === 6 && <Step6Review t={t} locale={locale} state={state} action={saveAction} wiredSteps={wiredSteps} />}
    </div>
  )
}

function StepShell({
  t, prevHref, heading, helper, children,
}: {
  prevHref: string | null; heading: string; helper?: string
  children: React.ReactNode
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-[var(--color-text-primary)] m-0 leading-tight">
          {heading}
        </h2>
        {helper && (
          <p className="mt-2 text-sm text-[var(--color-text-secondary)] leading-relaxed">
            {helper}
          </p>
        )}
      </div>
      {children}
      {prevHref && (
        <div>
          <Link href={prevHref} className="inline-flex items-center gap-1 text-sm text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]">
            <ArrowLeftIcon className="h-4 w-4" />
            {t("newPolicy.wizard.back")}
          </Link>
        </div>
      )}
    </div>
  )
}

function RadioCard({
  name, value, defaultChecked, label, sub, badge,
}: {
  name: string; value: string; defaultChecked?: boolean
  label: string; sub: string; badge?: { variant: "ok" | "info" | "muted"; text: string }
}) {
  return (
    <label className="block cursor-pointer">
      <input
        type="radio"
        name={name}
        value={value}
        defaultChecked={defaultChecked}
        className="peer sr-only"
        required
      />
      <span className="block rounded-xl border border-black/[0.08] bg-white p-4 transition-colors hover:border-[var(--color-accent)]/40 peer-checked:border-[var(--color-accent)] peer-checked:bg-[var(--color-accent)]/[0.05]">
        <span className="flex items-center justify-between gap-2 mb-1">
          <span className="text-sm font-semibold text-[var(--color-text-primary)]">{label}</span>
          {badge && <Badge variant={badge.variant}>{badge.text}</Badge>}
        </span>
        <span className="block text-xs text-[var(--color-text-secondary)] leading-relaxed">{sub}</span>
      </span>
    </label>
  )
}

function CheckboxCard({
  name, value, defaultChecked, label, sub,
}: {
  name: string; value: string; defaultChecked?: boolean
  label: string; sub: string
}) {
  return (
    <label className="block cursor-pointer">
      <input
        type="checkbox"
        name={name}
        value={value}
        defaultChecked={defaultChecked}
        className="peer sr-only"
      />
      <span className="block rounded-xl border border-black/[0.08] bg-white p-4 transition-colors hover:border-[var(--color-accent)]/40 peer-checked:border-[var(--color-accent)] peer-checked:bg-[var(--color-accent)]/[0.05]">
        <span className="flex items-center gap-2 mb-1">
          <span className="text-sm font-semibold text-[var(--color-text-primary)] flex-1">{label}</span>
          <span aria-hidden="true" className="hidden peer-checked:inline-flex h-4 w-4 items-center justify-center rounded-full bg-[var(--color-accent)] text-white">
            <CheckIcon className="h-3 w-3" strokeWidth={3} />
          </span>
        </span>
        <span className="block text-xs text-[var(--color-text-secondary)] leading-relaxed">{sub}</span>
      </span>
    </label>
  )
}

function NextButton({ label }: { label: string }) {
  return (
    <button
      type="submit"
      className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-[var(--color-accent)] px-5 py-3 text-sm font-semibold text-white shadow-sm hover:bg-[var(--color-accent-hover)] disabled:cursor-not-allowed disabled:opacity-60 cursor-pointer transition-colors"
    >
      {label}
    </button>
  )
}

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="block text-xs font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)] mb-1.5">
      {children}
    </span>
  )
}

function inputCls(): string {
  return "w-full rounded-xl border border-black/[0.08] bg-white px-4 py-3 text-base leading-6 text-[var(--color-text-primary)] focus:border-[var(--color-accent)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]/20"
}

/* ─── Step 1. Lifecycle ──────────────────────────────────────────── */

function Step1Lifecycle({
  t, locale, state, action,
}: {
  state: WizardState; locale: "ko" | "en"
  action: (fd: FormData) => Promise<void>
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const current = state.lifecycle ?? "before_tool_use"
  const ko = locale === "ko"
  const labels: Record<Lifecycle, { label: string; sub: string }> = ko ? {
    before_tool_use: { label: "도구 실행 전 (before_tool_use)", sub: "Bash, Edit, WebFetch 등 도구 호출이 실행되기 직전에 게이트가 발동합니다." },
    after_tool_use:  { label: "도구 실행 후 (after_tool_use)",  sub: "도구가 결과를 돌려준 직후, 출력을 검사하거나 후속 동작을 정합니다." },
    pre_final:       { label: "최종 응답 직전 (pre_final)",       sub: "에이전트가 최종 응답을 사용자에게 내놓기 직전, 마지막 검증 단계." },
  } : {
    before_tool_use: { label: "Before a tool runs (before_tool_use)", sub: "Fires right before a tool call (Bash, Edit, WebFetch, …) executes." },
    after_tool_use:  { label: "After a tool returns (after_tool_use)", sub: "Fires right after a tool returns, lets you inspect or react to the output." },
    pre_final:       { label: "Before the final answer (pre_final)",   sub: "Last-chance verification before the agent sends its final answer to the user." },
  }
  return (
    <StepShell
      t={t}
      prevHref={null}
      heading={t("newPolicy.wizard.step1.heading")}
      helper={t("newPolicy.wizard.step1.helper")}
    >
      <form action={action} className="space-y-3">
        <input type="hidden" name="_step" value="1" />
        {LIFECYCLES.map((life) => (
          <RadioCard
            key={life}
            name="lifecycle"
            value={life}
            defaultChecked={current === life}
            label={labels[life].label}
            sub={labels[life].sub}
            badge={life === "before_tool_use" ? { variant: "ok", text: ko ? "추천" : "recommended" } : undefined}
          />
        ))}
        <NextButton label={t("newPolicy.wizard.next")} />
      </form>
    </StepShell>
  )
}

/* ─── Step 2. ConditionKind ──────────────────────────────────────── */

function Step2ToolScope({
  t, locale, state, action,
}: {
  state: WizardState; locale: "ko" | "en"
  action: (fd: FormData) => Promise<void>
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const ko = locale === "ko"
  const picked = parseCsv(state.toolScope ?? "")
  const isAny = !state.toolScope || state.toolScope === "*"
  const builtinChecks = new Set(picked.filter((p) => (TOOL_PRESETS as readonly string[]).includes(p)))
  const customStr = picked.filter((p) => !(TOOL_PRESETS as readonly string[]).includes(p)).join(", ")
  return (
    <StepShell
      t={t}
      prevHref={buildWizardHref(state, 1)}
      heading={ko ? "어떤 도구에 적용할까요?" : "Which tool(s) does this policy apply to?"}
      helper={ko
        ? "모든 도구를 검사하거나, 특정 도구만 골라 좁힐 수 있습니다."
        : "Apply to every tool call, or narrow to a specific set."}
    >
      <form action={action} className="space-y-4">
        <input type="hidden" name="_step" value="2" />
        <HiddenState state={{ lifecycle: state.lifecycle }} />

        <label className="block cursor-pointer">
          <input
            type="radio"
            name="toolScope_mode"
            value="any"
            defaultChecked={isAny}
            className="peer sr-only"
          />
          <span className="block rounded-xl border border-black/[0.08] bg-white p-4 transition-colors hover:border-[var(--color-accent)]/40 peer-checked:border-[var(--color-accent)] peer-checked:bg-[var(--color-accent)]/[0.05]">
            <span className="block text-sm font-semibold text-[var(--color-text-primary)]">
              {ko ? "모든 도구" : "Any tool"}
            </span>
            <span className="mt-1 block text-xs text-[var(--color-text-secondary)]">
              {ko ? "도구 종류 상관없이 모든 호출을 검사합니다." : "Match every tool call regardless of name."}
            </span>
          </span>
          <input type="hidden" name="toolScope_any" value="1" disabled className="peer-checked:[&]:hidden hidden" />
        </label>

        <label className="block cursor-pointer">
          <input
            type="radio"
            name="toolScope_mode"
            value="specific"
            defaultChecked={!isAny && (picked.length > 0)}
            className="peer sr-only"
          />
          <span className="block rounded-xl border border-black/[0.08] bg-white p-4 transition-colors hover:border-[var(--color-accent)]/40 peer-checked:border-[var(--color-accent)] peer-checked:bg-[var(--color-accent)]/[0.05]">
            <span className="block text-sm font-semibold text-[var(--color-text-primary)]">
              {ko ? "특정 도구만" : "Specific tools"}
            </span>
            <span className="mt-1 block text-xs text-[var(--color-text-secondary)]">
              {ko ? "고른 도구 중 하나라도 호출되면 정책이 발동합니다." : "Match if any picked tool is invoked."}
            </span>
          </span>
          <span className="mt-3 hidden peer-checked:block space-y-3">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {TOOL_PRESETS.map((tool) => (
                <label key={tool} className="block cursor-pointer">
                  <input
                    type="checkbox"
                    name="toolScope_chip"
                    value={tool}
                    defaultChecked={builtinChecks.has(tool)}
                    className="peer sr-only"
                  />
                  <span className="block rounded-lg border border-black/[0.08] bg-white px-3 py-2 text-center text-sm font-mono text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-accent)]/40 peer-checked:border-[var(--color-accent)] peer-checked:bg-[var(--color-accent)]/[0.06] peer-checked:text-[var(--color-text-primary)]">
                    {tool}
                  </span>
                </label>
              ))}
            </div>
            <div>
              <FieldLabel>{ko ? "추가 / MCP 도구 (쉼표 구분)" : "Extras / MCP tools (comma-separated)"}</FieldLabel>
              <input
                name="toolScope_custom"
                maxLength={2000}
                defaultValue={customStr}
                placeholder="mcp__court__file, mcp__db__query"
                spellCheck={false}
                autoComplete="off"
                className={inputCls() + " font-mono text-sm"}
              />
            </div>
          </span>
        </label>

        {/* Carry a marker so advance/save know Step 2 is the submitter
            and should merge chip + custom into toolScope. The "any"
            mode is communicated via the toolScope_any field (separate
            radio above keeps the keyboard semantics natural). */}
        {/* If user picks "any" mode, the toolScope_any flag is filled
            by a hidden input declared inside that label. */}
        <NextButton label={t("newPolicy.wizard.next")} />
      </form>
    </StepShell>
  )
}

/* ─── Step 3. Specifics ──────────────────────────────────────────── */

/** P7 (issue #1): chip row showing the CC hook payload fields the
 * runtime actually delivers. Chips are <button>s in a client island so
 * they are keyboard-focusable AND insert the picked path into the
 * target textarea at the cursor — closing both the a11y gap (P1 #7
 * review) and the inert-select footgun (P1 #8 review). Hover keeps
 * surfacing type + description + example as a tooltip; aria-label
 * carries the same info for screen readers.
 *
 * variant="path" inserts the bare field path (for regex / llm_critic).
 * variant="shacl-stub" inserts a SHACL PropertyShape / NodeShape stub
 * anchored on the canonical `magi:` namespace the runtime materializes
 * — a shape extended from this stub is GUARANTEED to find a focus
 * node at runtime (the vacuous-satisfaction failure mode P7 was
 * built to eliminate). */
function PayloadFieldChips({
  fields, locale, intro, targetTextareaId, variant,
}: {
  fields: PayloadFieldDescriptor[]
  locale: "ko" | "en"
  intro?: string
  targetTextareaId: string
  variant: "path" | "shacl-stub"
}) {
  if (fields.length === 0) return null
  const ko = locale === "ko"
  const introText = intro ?? (ko
    ? "런타임이 stdin으로 전달하는 필드 (클릭하면 삽입):"
    : "Fields the runtime delivers on stdin (click to insert):")
  return (
    <PayloadFieldChipsClient
      fields={fields}
      targetTextareaId={targetTextareaId}
      variant={variant}
      introText={introText}
      locale={locale}
    />
  )
}

/** P9 (D49): build the two same-page switch-hrefs that the
 * SteeringAwareField client island uses as a starting point. The
 * island then splices the live in-flight text into them on each
 * keystroke so the user does not lose what they have typed when they
 * click "Switch to evidence_ref" / "Switch to pre_final + evidence_ref".
 *
 * Splitting URL construction (server) from live-text mutation
 * (client) keeps the URL contract authoritative server-side and lets
 * the client only own what it must own: the live textbox value and
 * the dismissal flag. */
function steeringBaseHrefs(state: WizardState): {
  switchHref: string
  switchPreFinalHref: string
} {
  return {
    switchHref: buildWizardHref(
      { ...state, conditionKind: "evidence_ref" }, 3,
    ),
    switchPreFinalHref: buildWizardHref(
      { ...state, lifecycle: "pre_final", conditionKind: "evidence_ref" }, 1,
    ),
  }
}

/** Snapshot of the wizard state stripped to plain JSON for the
 * SteeringAwareField client island. (`state` itself includes optional
 * `Lifecycle` / `Action` enums that are server-only types; the client
 * only needs the strings.) */
function steeringSnapshot(
  state: WizardState,
): import("./_components/SteeringAwareField").WizardSnapshot {
  return {
    lifecycle: state.lifecycle,
    toolScope: state.toolScope,
    conditionKind: state.conditionKind,
    fetchDomain: state.fetchDomain,
    allowlist: state.allowlist,
    pattern: state.pattern,
    llmCriterion: state.llmCriterion,
    evidenceRefs: state.evidenceRefs,
    shaclTtl: state.shaclTtl,
    action: state.action,
    id: state.id,
    description: state.description,
  }
}

function Step3Condition({
  t, locale, state, wiredSteps, action,
}: {
  state: WizardState; locale: "ko" | "en"
  wiredSteps: WiredStep[]
  action: (fd: FormData) => Promise<void>
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const ko = locale === "ko"
  const lifecycle = state.lifecycle ?? "before_tool_use"
  // P7: which fields the runtime actually delivers on stdin for this
  // (lifecycle, toolScope). When the scope is a specific known tool
  // (Bash / Edit / Write / Read / WebFetch) we get tool-specific
  // paths; otherwise the generic tool_input dict shape (honest about
  // what the runtime can guarantee).
  const ccEvent = payloadLifecycleToEvent(lifecycle)
  const ccMatcher = lifecycle === "pre_final" ? undefined : state.toolScope
  const payloadFields = payloadAvailableFields(ccEvent, ccMatcher)
  // Filter condition kinds by lifecycle AND by whether WebFetch is in
  // the toolScope (for fetch_domain / domain_allowlist shortcuts).
  const allowedRaw = CONDITION_KINDS_BY_LIFECYCLE[lifecycle]
  const hasWebFetch = toolScopeIncludesWebFetch(state)
  const kinds = allowedRaw.filter((k) =>
    (k !== "fetch_domain" && k !== "domain_allowlist") || hasWebFetch
  )
  const defaultPick: ConditionKind = state.conditionKind && kinds.includes(state.conditionKind)
    ? state.conditionKind : kinds[0]
  const labels: Record<ConditionKind, { label: string; sub: string }> = ko ? {
    none:              { label: "조건 없이",        sub: "도구 스코프에 매칭되는 모든 호출에 발동 (조건 없음)." },
    fetch_domain:      { label: "Fetch 도메인",     sub: "WebFetch가 특정 도메인에 접근하려고 할 때." },
    domain_allowlist:  { label: "도메인 allowlist", sub: "허용 목록에 없는 외부 도메인 접근 차단." },
    regex:             { label: "정규식 (인자/출력)", sub: "도구 인자 또는 출력이 Python re 패턴에 매칭되면." },
    llm_critic:        { label: "LLM critic",      sub: "자연어 기준을 LLM에 물어보고 NO면 발동." },
    evidence_ref:      { label: "Evidence ref",    sub: "프리셋 verifier 결과가 FAIL이면 발동." },
    shacl:             { label: "SHACL shape",     sub: "Turtle로 작성한 시맨틱 제약을 위반하면." },
  } : {
    none:              { label: "No condition",    sub: "Fires on every matching tool call (no per-call check)." },
    fetch_domain:      { label: "Fetch domain",    sub: "Fires when WebFetch tries to hit a specific domain." },
    domain_allowlist:  { label: "Domain allowlist", sub: "Blocks fetches to any domain not on the allowlist." },
    regex:             { label: "Regex (args/output)", sub: "Fires when the tool args or output match a Python re pattern." },
    llm_critic:        { label: "LLM critic",      sub: "Asks an LLM a yes/no criterion; fires on NO." },
    evidence_ref:      { label: "Evidence ref",    sub: "Fires when a wired verifier returns FAIL." },
    shacl:             { label: "SHACL shape",     sub: "Fires when the evidence graph doesn't conform to a Turtle shape." },
  }
  const previewBadge = ko ? "프리뷰" : "preview"
  const prevStep = state.lifecycle === "pre_final" ? 1 : 2

  // P9 (D49): the per-kind cumulative-judgment tip lives in a client
  // island (SteeringAwareField). It needs two same-page hrefs as a
  // starting point — the island then splices live in-flight text in.
  const { switchHref: baseSwitchHref, switchPreFinalHref: baseSwitchPreFinalHref }
    = steeringBaseHrefs(state)
  const evidenceAllowed = (CONDITION_KINDS_BY_LIFECYCLE[lifecycle] as readonly ConditionKind[])
    .includes("evidence_ref")
  const wizardSnap = steeringSnapshot(state)
  const fieldInputCls = inputCls()

  return (
    <StepShell
      t={t}
      prevHref={buildWizardHref(state, prevStep)}
      heading={ko ? "어떤 조건일 때 검사하나요?" : "Under what condition?"}
      helper={ko
        ? "조건을 고르면 바로 아래에 기준 입력 칸이 열립니다."
        : "Pick a condition and the criteria input opens right below."}
    >
      <form action={action} className="space-y-3">
        <input type="hidden" name="_step" value="3" />
        <HiddenState state={{
          lifecycle: state.lifecycle,
          toolScope: state.toolScope,
        }} />
        {kinds.map((k) => {
          const badge = (k === "llm_critic" || k === "shacl")
            ? { variant: "info" as const, text: previewBadge }
            : undefined
          return (
            <label key={k} className="block cursor-pointer">
              <input
                type="radio"
                name="conditionKind"
                value={k}
                defaultChecked={defaultPick === k}
                required
                className="peer sr-only"
              />
              <span className="block rounded-xl border border-black/[0.08] bg-white p-4 transition-colors hover:border-[var(--color-accent)]/40 peer-checked:border-[var(--color-accent)] peer-checked:bg-[var(--color-accent)]/[0.05]">
                <span className="flex items-center justify-between gap-2 mb-1">
                  <span className="text-sm font-semibold text-[var(--color-text-primary)]">{labels[k].label}</span>
                  {badge && <Badge variant={badge.variant}>{badge.text}</Badge>}
                </span>
                <span className="block text-xs text-[var(--color-text-secondary)] leading-relaxed">{labels[k].sub}</span>
              </span>
              {/* Inline specifics: shown only when this radio is the
                  peer-checked one. CSS-only reactive — no JS required. */}
              <span className="hidden peer-checked:block mt-2 rounded-xl border border-[var(--color-accent)]/30 bg-[var(--color-accent)]/[0.03] p-4 space-y-2">
                {k === "fetch_domain" && (
                  <div>
                    <FieldLabel>{ko ? "Fetch 도메인" : "Fetch domain"}</FieldLabel>
                    <input
                      name="fetchDomain"
                      maxLength={256}
                      defaultValue={state.fetchDomain ?? ""}
                      placeholder="example.com"
                      spellCheck={false}
                      autoComplete="off"
                      className={inputCls() + " font-mono"}
                    />
                  </div>
                )}
                {k === "domain_allowlist" && (
                  <div>
                    <FieldLabel>{ko ? "허용 도메인 (쉼표 구분)" : "Allowed domains (comma-separated)"}</FieldLabel>
                    <input
                      name="allowlist"
                      maxLength={2000}
                      defaultValue={state.allowlist ?? ""}
                      placeholder="api.openai.com, github.com, npmjs.com"
                      spellCheck={false}
                      autoComplete="off"
                      className={inputCls() + " font-mono"}
                    />
                  </div>
                )}
                {k === "regex" && (
                  <div>
                    <FieldLabel>{ko ? "정규식 패턴 (Python re)" : "Regex pattern (Python re)"}</FieldLabel>
                    <PayloadFieldChips
                      fields={payloadFields}
                      locale={locale}
                      targetTextareaId="w-regex-pattern"
                      variant="path"
                    />
                    <SteeringAwareField
                      kind="regex"
                      locale={locale}
                      state={wizardSnap}
                      evidenceAllowed={evidenceAllowed}
                      baseSwitchHref={baseSwitchHref}
                      baseSwitchPreFinalHref={baseSwitchPreFinalHref}
                      inputId="w-regex-pattern"
                      initialValue={state.pattern ?? ""}
                      className={fieldInputCls}
                      fieldElement="input"
                      name="pattern"
                      placeholder="AKIA[A-Z0-9]{16}"
                      maxLength={2000}
                      monospace
                    />
                  </div>
                )}
                {k === "llm_critic" && (
                  <div>
                    <FieldLabel>{ko ? "LLM critic 기준" : "LLM critic criterion"}</FieldLabel>
                    <PayloadFieldChips
                      fields={payloadFields}
                      locale={locale}
                      intro={ko
                        ? "기준에서 참조 가능한 필드 (클릭하면 삽입):"
                        : "Fields you can reference in your criterion (click to insert):"}
                      targetTextareaId={`w-llm-${k}`}
                      variant="path"
                    />
                    <SteeringAwareField
                      kind="llm_critic"
                      locale={locale}
                      state={wizardSnap}
                      evidenceAllowed={evidenceAllowed}
                      baseSwitchHref={baseSwitchHref}
                      baseSwitchPreFinalHref={baseSwitchPreFinalHref}
                      inputId={`w-llm-${k}`}
                      initialValue={state.llmCriterion ?? ""}
                      className={fieldInputCls}
                      fieldElement="textarea"
                      rows={3}
                      name="llmCriterion"
                      placeholder={ko
                        ? "예: 출력에 사용자가 묻지 않은 추측이 포함되어 있는가?"
                        : "e.g. Does the output contain a guess the user did not ask for?"}
                      monospace
                    />
                  </div>
                )}
                {k === "evidence_ref" && (
                  <div className="space-y-2">
                    <FieldLabel>{ko ? "참조할 verifier (1개 이상)" : "Verifier(s) to reference"}</FieldLabel>
                    {wiredSteps.length === 0 && (
                      <p className="text-xs text-amber-700">
                        {ko
                          ? "연결된 verifier가 없습니다. 먼저 /presets에서 verifier를 enable 하세요."
                          : "No wired verifiers yet. Enable one under /presets first."}
                      </p>
                    )}
                    <div className="space-y-2">
                      {wiredSteps.map((w) => {
                        // D52d follow-up (a11y): the field_checks tree
                        // is positioned visually below the
                        // CheckboxCard but was not programmatically
                        // linked to it. We wrap the tree in
                        // role='group' aria-labelledby pointing to a
                        // per-row label that names the verifier step,
                        // so a SR user navigating the picker hears
                        // "group, citation_verify checks: …" instead
                        // of orphaned content with no relationship to
                        // the picker entry above. The id includes the
                        // step name to keep multiple pickers on the
                        // same page distinct.
                        const labelId = `verifier-checks-label-${w.step}`
                        return (
                          <div key={w.step} className="space-y-1.5">
                            <CheckboxCard
                              name="evidence_ref"
                              value={w.step}
                              defaultChecked={state.evidenceRefs?.includes(w.step) ?? false}
                              label={w.step}
                              sub={w.description}
                            />
                            {/* D52d: surface the same field_checks tree
                                the catalog expander uses, inline below
                                the picker so the author sees what each
                                verifier actually inspects (path → check
                                description) before saving the policy.
                                No extra click; render on mount. */}
                            <div
                              role="group"
                              aria-labelledby={labelId}
                              className="ml-3 rounded-lg border border-black/[0.05] bg-[var(--color-surface-1,#f9fafb)]/40 px-3 py-2"
                            >
                              <p
                                id={labelId}
                                className="mb-1.5 text-[10px] uppercase tracking-wider font-semibold text-[var(--color-text-tertiary)]"
                              >
                                {t("newPolicy.wizard.verifier.checksLabel")}: <span className="font-mono normal-case tracking-normal text-[var(--color-text-secondary)]">{w.step}</span>
                              </p>
                              <VerifierFieldChecks
                                step={w.step}
                                t={t}
                                showFooter
                              />
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}
                {k === "shacl" && (
                  <div>
                    <FieldLabel>SHACL shape (Turtle)</FieldLabel>
                    <PayloadFieldChips
                      fields={payloadFields}
                      locale={locale}
                      intro={ko
                        ? "클릭하면 shape stub 삽입 — magi: 네임스페이스 (런타임이 stdin을 RDF로 lift 하는 경로) 에 anchor 되어 vacuous-satisfaction(조용한 fail-open)을 막습니다:"
                        : "Click to insert a SHACL stub anchored on the canonical magi: namespace (the runtime materializes stdin under it), so shapes can't be vacuously satisfied (silent fail-open):"}
                      targetTextareaId="w-shacl"
                      variant="shacl-stub"
                    />
                    <SteeringAwareField
                      kind="shacl"
                      locale={locale}
                      state={wizardSnap}
                      evidenceAllowed={evidenceAllowed}
                      baseSwitchHref={baseSwitchHref}
                      baseSwitchPreFinalHref={baseSwitchPreFinalHref}
                      inputId="w-shacl"
                      initialValue={state.shaclTtl ?? ""}
                      className={fieldInputCls}
                      fieldElement="textarea"
                      rows={6}
                      name="shaclTtl"
                      placeholder={"@prefix sh:   <http://www.w3.org/ns/shacl#> .\n@prefix magi: <https://magi.openmagi.ai/cc/hook#> .\n…"}
                      monospace
                    />
                  </div>
                )}
                {k === "none" && (
                  <p className="text-xs text-[var(--color-text-secondary)] m-0">
                    {ko ? "기준 입력 없음. 매칭된 모든 호출에 대해 그대로 다음 단계로." : "No criteria to fill. The action runs on every matching call."}
                  </p>
                )}
              </span>
            </label>
          )
        })}
        <NextButton label={t("newPolicy.wizard.next")} />
      </form>
    </StepShell>
  )
}

/* ─── Step 4. Action ─────────────────────────────────────────────── */

function Step4Action({
  t, locale, state, action,
}: {
  state: WizardState; locale: "ko" | "en"
  action: (fd: FormData) => Promise<void>
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const lifecycle = state.lifecycle ?? "before_tool_use"
  const allowed = ACTIONS_BY_LIFECYCLE[lifecycle]
  const defaultPick: Action = state.action && allowed.includes(state.action)
    ? state.action : allowed[0]
  const ko = locale === "ko"
  const header = ko ? actionHeaderKO(state) : actionHeaderEN(state)
  const labels: Record<Action, { label: string; sub: string }> = ko ? {
    block: { label: "Block",        sub: "호출 자체를 거부합니다. 에이전트가 동작을 못합니다." },
    ask:   { label: "Ask a human",  sub: "리뷰 큐로 보내고 사람이 승인해야 진행됩니다." },
    audit: { label: "Audit",        sub: "원장에만 기록하고 통과시킵니다 (관찰 모드)." },
    strip: { label: "Strip",        sub: "출력에서 매칭된 부분을 제거합니다 (after_tool_use 전용)." },
  } : {
    block: { label: "Block",        sub: "Refuse the call. The agent cannot proceed." },
    ask:   { label: "Ask a human",  sub: "Send to the review queue; a human must approve to proceed." },
    audit: { label: "Audit",        sub: "Record to the ledger only; pass through (observe mode)." },
    strip: { label: "Strip",        sub: "Remove the matched span from the output (after_tool_use only)." },
  }
  return (
    <StepShell
      t={t}
      prevHref={buildWizardHref(state, 3)}
      heading={t("newPolicy.wizard.step4.heading")}
      helper={header + (ko ? " 어떤 동작을 할까요?" : " what should this policy do?")}
    >
      <form action={action} className="space-y-3">
        <input type="hidden" name="_step" value="4" />
        <HiddenState state={{
          lifecycle: state.lifecycle,
          toolScope: state.toolScope,
          conditionKind: state.conditionKind,
          fetchDomain: state.fetchDomain,
          allowlist: state.allowlist,
          pattern: state.pattern,
          llmCriterion: state.llmCriterion,
          evidenceRefs: state.evidenceRefs,
          shaclTtl: state.shaclTtl,
        }} />
        {allowed.map((a) => {
          const stripDisabled = a === "strip" && !STRIP_AVAILABLE
          if (stripDisabled) {
            return (
              <label key={a} className="block cursor-not-allowed opacity-60">
                <input type="radio" name="action" value={a} disabled className="peer sr-only" />
                <span className="block rounded-xl border border-black/[0.08] bg-gray-50 p-4">
                  <span className="flex items-center justify-between gap-2 mb-1">
                    <span className="text-sm font-semibold text-[var(--color-text-primary)]">{labels[a].label}</span>
                    <Badge variant="info">coming soon</Badge>
                  </span>
                  <span className="block text-xs text-[var(--color-text-secondary)] leading-relaxed">{labels[a].sub}</span>
                </span>
              </label>
            )
          }
          return (
            <RadioCard
              key={a}
              name="action"
              value={a}
              defaultChecked={defaultPick === a}
              label={labels[a].label}
              sub={labels[a].sub}
              badge={a === "block" && lifecycle === "before_tool_use" ? { variant: "ok", text: "recommended" } : undefined}
            />
          )
        })}
        <NextButton label={t("newPolicy.wizard.next")} />
      </form>
    </StepShell>
  )
}

/* ─── Step 5. Name ───────────────────────────────────────────────── */

function suggestPolicyId(state: WizardState): string {
  const life = state.lifecycle ?? "before_tool_use"
  const lifeSlug = life.replace(/_/g, "-")
  const tail = state.toolScope && state.toolScope !== "*"
    ? state.toolScope.toLowerCase().replace(/[^a-z0-9]+/g, "-")
    : state.fetchDomain
      ? state.fetchDomain.replace(/[^a-z0-9]+/g, "-")
      : state.conditionKind || "any"
  const cleaned = tail.replace(/^-+|-+$/g, "").slice(0, 24) || "any"
  return `${lifeSlug}-${cleaned}/v1`
}

function Step5Naming({
  t, state, action,
}: {
  state: WizardState; action: (fd: FormData) => Promise<void>
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const idDefault = state.id ?? suggestPolicyId(state)
  return (
    <StepShell
      t={t}
      prevHref={buildWizardHref(state, 4)}
      heading={t("newPolicy.wizard.step5.heading")}
      helper={t("newPolicy.wizard.step5.helper")}
    >
      <form action={action} className="space-y-4">
        <input type="hidden" name="_step" value="5" />
        <HiddenState state={state} />
        <div>
          <label htmlFor="w-id">
            <FieldLabel>{t("newPolicy.guided.field.id")}</FieldLabel>
          </label>
          <input
            id="w-id"
            name="id"
            required
            maxLength={128}
            pattern="[A-Za-z0-9._\-/]{1,128}"
            defaultValue={idDefault}
            placeholder="legal-filing/v1"
            spellCheck={false}
            autoComplete="off"
            autoFocus
            className={inputCls() + " font-mono"}
          />
          <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
            {state.id ? t("newPolicy.guided.field.idHint") : t("newPolicy.wizard.step5.autoSuggested")}
          </p>
        </div>
        <div>
          <label htmlFor="w-desc">
            <FieldLabel>{t("newPolicy.guided.field.description")}</FieldLabel>
          </label>
          <input
            id="w-desc"
            name="description"
            maxLength={256}
            defaultValue={state.description ?? ""}
            placeholder={t("newPolicy.guided.field.descriptionPh")}
            className={inputCls()}
          />
        </div>
        <NextButton label={t("newPolicy.wizard.next")} />
      </form>
    </StepShell>
  )
}

/* ─── Step 6. Review ─────────────────────────────────────────────── */

function Step6Review({
  t, locale, state, action, wiredSteps,
}: {
  state: WizardState
  locale: "ko" | "en"
  action: (fd: FormData) => Promise<void>
  wiredSteps: WiredStep[]
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const event = LIFECYCLE_TO_EVENT[state.lifecycle ?? "before_tool_use"]
  const matcher = deriveMatcher(state)
  const requires = deriveRequires(state)
  const summary = plainSummary(state, locale)
  const evidenceList = state.evidenceRefs ?? []
  return (
    <StepShell
      t={t}
      prevHref={buildWizardHref(state, 5)}
      heading={t("newPolicy.wizard.step6.heading")}
      helper={t("newPolicy.wizard.step6.helper")}
    >
      <Card>
        <p className="text-sm font-semibold mb-3">{t("newPolicy.wizard.step6.summaryHead")}</p>
        <p className="text-sm leading-relaxed text-[var(--color-text-secondary)]">
          {summary}
        </p>
        <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1.5 text-xs mt-4 pt-4 border-t border-black/[0.06]">
          <dt className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold">id</dt>
          <dd className="font-mono text-[12.5px]" translate="no">{state.id}</dd>

          <dt className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold">lifecycle</dt>
          <dd className="text-[var(--color-text-secondary)]">{state.lifecycle}</dd>

          {state.lifecycle !== "pre_final" && (
            <>
              <dt className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold">tool scope</dt>
              <dd className="text-[var(--color-text-secondary)]">
                {!state.toolScope || state.toolScope === "*"
                  ? <em>any tool</em>
                  : <code className="font-mono">{state.toolScope}</code>}
              </dd>
            </>
          )}

          <dt className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold">trigger (IR)</dt>
          <dd><code className="font-mono">{event} · {matcher}</code></dd>

          <dt className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold">condition</dt>
          <dd className="text-[var(--color-text-secondary)]">
            {state.conditionKind === "none" ? "—" : state.conditionKind}
            {state.conditionKind === "fetch_domain" && state.fetchDomain && <> · <code className="font-mono">{state.fetchDomain}</code></>}
            {state.conditionKind === "domain_allowlist" && state.allowlist && <> · <code className="font-mono">{state.allowlist}</code></>}
            {state.conditionKind === "regex" && state.pattern && <> · <code className="font-mono">{state.pattern}</code></>}
            {state.conditionKind === "llm_critic" && state.llmCriterion && <> · <em>{state.llmCriterion}</em></>}
            {state.conditionKind === "evidence_ref" && evidenceList.length > 0 && (
              <ul className="mt-1 space-y-0.5 list-disc pl-5">
                {evidenceList.map((v) => {
                  const desc = wiredSteps.find((w) => w.step === v)?.description ?? ""
                  return <li key={v}><code className="font-mono">{v}</code> {desc && <span className="text-[var(--color-text-tertiary)]">· {desc}</span>}</li>
                })}
              </ul>
            )}
            {state.conditionKind === "shacl" && state.shaclTtl && <> · SHACL ({state.shaclTtl.length} chars)</>}
          </dd>

          <dt className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold">action</dt>
          <dd className="text-[var(--color-text-secondary)]">{state.action}</dd>

          <dt className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold">requires (IR)</dt>
          <dd className="text-[var(--color-text-secondary)] text-xs">
            {requires.length === 0
              ? "—"
              : requires.map((r) => {
                  const k = "kind" in r ? r.kind : "step"
                  if (k === "step") return `${("step" in r ? r.step : "?")}=pass`
                  if (k === "regex") return `regex(${("pattern" in r ? r.pattern : "").slice(0, 36)}…)`
                  if (k === "llm_critic") return `llm(…)`
                  if (k === "shacl") return "shacl(…)"
                  return k
                }).join(", ")}
          </dd>

        </dl>
      </Card>
      <form action={action}>
        <HiddenState state={state} />
        <NextButton label={t("newPolicy.wizard.savePolicy")} />
      </form>
    </StepShell>
  )
}

/* ─── compile-result block (NL mode) ─────────────────────────────── */

function CompileResultBlock({
  t, data, saveAction,
}: {
  data: CompileResult & { nl: string }
  saveAction: (fd: FormData) => Promise<void>
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const irJson = JSON.stringify(data.ir, null, 2)
  const hasSchemaIssues = data.schema_issues.length > 0
  const canSave = data.review.ok && !hasSchemaIssues
  const draft = data.ir as unknown as PolicyDraft

  return (
    <Card className="border-[var(--color-accent)]/20 bg-gradient-to-br from-[var(--color-accent)]/[0.02] to-white">
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <h2 className="text-md font-semibold m-0">{t("compile.result.title")}</h2>
        <Badge variant={data.review.ok ? "ok" : "review"}>
          {data.review.ok ? t("compile.result.reviewerOk") : t("compile.result.reviewerFlagged")}
        </Badge>
        <Badge variant={hasSchemaIssues ? "deny" : "ok"}>
          {hasSchemaIssues
            ? t("compile.result.schemaIssues", { n: data.schema_issues.length })
            : t("compile.result.schemaClean")}
        </Badge>
      </div>

      <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1.5 text-sm mb-3">
        <dt className="text-[var(--color-text-tertiary)] text-xs uppercase tracking-wider font-semibold pt-0.5">id</dt>
        <dd className="font-mono text-[13px]" translate="no">{draft.id}</dd>
        <dt className="text-[var(--color-text-tertiary)] text-xs uppercase tracking-wider font-semibold pt-0.5">trigger</dt>
        <dd><code className="font-mono">{draft.trigger.event}</code> · <code className="font-mono">{draft.trigger.matcher}</code></dd>
        {draft.requires && draft.requires.length > 0 && (
          <>
            <dt className="text-[var(--color-text-tertiary)] text-xs uppercase tracking-wider font-semibold pt-0.5">requires</dt>
            <dd className="text-[var(--color-text-secondary)] text-xs">
              {draft.requires.map((r) => {
                const kind = ("kind" in r ? r.kind : "step")
                if (kind === "step") return `${("step" in r ? r.step : "")}=${("verdict" in r ? r.verdict : "pass")}`
                if (kind === "regex") return `regex(${("pattern" in r ? r.pattern : "").slice(0, 24)})`
                if (kind === "llm_critic") return `llm(${("criterion" in r ? r.criterion : "").slice(0, 24)})`
                if (kind === "shacl") return "shacl(…)"
                return kind
              }).join(", ")}
            </dd>
          </>
        )}
        <dt className="text-[var(--color-text-tertiary)] text-xs uppercase tracking-wider font-semibold pt-0.5">action</dt>
        <dd className="text-[var(--color-text-secondary)]">{draft.action}</dd>
      </dl>

      <details className="mb-3 rounded-lg bg-gray-50/70 p-2">
        <summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)]">
          {t("compile.result.irLabel")}
        </summary>
        <CodeBlock maxHeight="44vh" className="mt-2">{irJson}</CodeBlock>
      </details>

      {data.review.issues.length > 0 && (
        <div className="mb-3">
          <p className="text-xs font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)] mb-1.5">
            {t("compile.result.reviewerIssuesLabel")}
          </p>
          <ul className="m-0 pl-5 text-xs list-disc text-[var(--color-text-secondary)] space-y-1 leading-relaxed">
            {data.review.issues.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </div>
      )}

      {hasSchemaIssues && (
        <div className="mb-3 rounded-lg border border-[var(--color-deny-fg)]/20 bg-[var(--color-deny-bg)]/60 p-3" role="alert">
          <p className="text-xs font-semibold uppercase tracking-wider text-[var(--color-deny-fg)] mb-1.5">
            {t("compile.result.schemaIssuesLabel")}
          </p>
          <ul className="m-0 pl-5 text-xs list-disc text-[var(--color-text-secondary)] space-y-1 leading-relaxed">
            {data.schema_issues.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </div>
      )}

      <form action={saveAction} className="mt-2 flex items-center gap-2 flex-wrap">
        <input type="hidden" name="ir_json" value={irJson} />
        <input type="hidden" name="source" value="org" />
        <SubmitButton
          label={t("compile.activate")}
          pendingLabel={t("newPolicy.saving")}
        />
        {!canSave && (
          <span className="text-xs text-[var(--color-text-tertiary)] leading-tight">
            {t("compile.cantActivate")}
          </span>
        )}
      </form>
    </Card>
  )
}

// Suppress unused warnings (these are reserved for future kind support
// once the backend grows the explicit fetch/allowlist condition kinds).
void FETCH_TOOLS
