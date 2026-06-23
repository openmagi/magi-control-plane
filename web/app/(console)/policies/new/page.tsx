import Link from "next/link"
import { revalidatePath } from "next/cache"
import { redirect } from "next/navigation"
import { XMarkIcon, ArrowLeftIcon, SparklesIcon, CodeBracketIcon, AdjustmentsHorizontalIcon, CheckIcon } from "@heroicons/react/24/outline"
import PolicyBuilder from "@/components/PolicyBuilder"
import { codeForError, resolveFlash } from "@/lib/flash"
import { validatePolicyId } from "@/lib/policy-id"
import {
  validateDraft, type PolicyDraft,
} from "@/lib/policy-builder"
import { CloudConfigError, cloud, type CompileResult } from "@/lib/cloud"
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
  | "tool_name" | "fetch_domain" | "domain_allowlist"
  | "none" | "regex" | "llm_critic"
  | "evidence_ref" | "shacl"

// Coverage audit (D41+): before_tool_use needs regex + llm_critic on
// the tool call args too. "Block git push when AWS key in diff" and
// "block rm -rf /" are the most common Claude Code policies and they
// gate on the tool INPUT, not on tool name alone. pre_final gets regex
// too so "no secrets in the final answer" is one click away.
const CONDITION_KINDS_BY_LIFECYCLE: Record<Lifecycle, readonly ConditionKind[]> = {
  before_tool_use: ["tool_name", "regex", "llm_critic", "fetch_domain", "domain_allowlist"],
  after_tool_use:  ["none", "regex", "llm_critic"],
  pre_final:       ["evidence_ref", "regex", "shacl", "llm_critic"],
}

const ALL_CONDITION_KINDS: readonly ConditionKind[] = [
  "tool_name", "fetch_domain", "domain_allowlist",
  "none", "regex", "llm_critic",
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
  conditionKind?: ConditionKind
  // per-kind specifics
  toolName?: string                 // tool_name
  fetchDomain?: string              // fetch_domain
  allowlist?: string                // domain_allowlist (csv)
  pattern?: string                  // regex
  llmCriterion?: string             // llm_critic
  evidenceRefs?: string[]           // evidence_ref (multi)
  shaclTtl?: string                 // shacl
  action?: Action
  id?: string
  description?: string
}

/* ─── IR + summary builders ───────────────────────────────────────── */

const SENTINEL_RE_DEFAULT =
  "GATE_(?P<matter>[A-Za-z0-9]+)_(?P<doc_id>[A-Za-z0-9]+)"

function deriveMatcher(s: WizardState): string {
  if (s.lifecycle === "before_tool_use") {
    if (s.conditionKind === "tool_name") {
      return (s.toolName ?? "").trim() || "Bash"
    }
    if (s.conditionKind === "fetch_domain" || s.conditionKind === "domain_allowlist") {
      return "WebFetch"
    }
  }
  return "*"
}

function deriveRequires(s: WizardState): PolicyDraft["requires"] {
  const kind = s.conditionKind
  switch (kind) {
    case "tool_name":
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
  if (s.conditionKind === "none") return "On every trigger,"
  if (s.lifecycle === "before_tool_use" && s.conditionKind === "tool_name") return "When the tool runs,"
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
  if (s.conditionKind === "none") return "트리거가 일어날 때마다,"
  if (s.lifecycle === "before_tool_use" && s.conditionKind === "tool_name") return "도구가 실행되려고 할 때,"
  if (s.lifecycle === "before_tool_use" && s.conditionKind === "regex") return "도구 인자가 패턴에 매칭될 때,"
  if (s.lifecycle === "before_tool_use" && s.conditionKind === "llm_critic") return "도구 인자에 대한 LLM critic이 NO를 반환할 때,"
  if (s.lifecycle === "before_tool_use" && s.conditionKind === "fetch_domain") return "fetch 도메인이 매칭될 때,"
  if (s.lifecycle === "before_tool_use" && s.conditionKind === "domain_allowlist") return "도메인이 허용 목록에 없을 때,"
  if (s.lifecycle === "after_tool_use"  && s.conditionKind === "regex") return "출력이 패턴에 매칭될 때,"
  if (s.lifecycle === "after_tool_use"  && s.conditionKind === "llm_critic") return "LLM critic이 NO를 반환할 때,"
  if (s.lifecycle === "pre_final"       && s.conditionKind === "evidence_ref") return "Evidence ref 가 FAIL일 때,"
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

  for (const [k, v] of formData.entries()) {
    if (typeof v !== "string") continue
    if (k.startsWith("$ACTION") || k === "_step") continue
    if (k === "evidence_ref" || k === "evidence_refs") continue
    if (!v.trim()) continue
    params.set(k, v.trim())
  }

  // Auto-skip Step 3 (specifics) when conditionKind === "none".
  const condK = params.get("conditionKind") as ConditionKind | null
  if (stepIn === 2 && condK === "none") {
    nextStep = 4
  }

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

  const state: WizardState = {
    lifecycle,
    conditionKind,
    toolName: String(formData.get("toolName") ?? "").trim() || undefined,
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
    sentinel_re: SENTINEL_RE_DEFAULT,
    requires,
    action: irAction as PolicyDraft["action"],
    on_signature_invalid: "deny",
    gate_binary: "/usr/local/bin/magi-gate.sh",
  }
  const source = String(formData.get("source") ?? "org")
  await persistDraft(draft, source)
}

function validateSpecifics(s: WizardState): string | null {
  switch (s.conditionKind) {
    case "none":
      return null
    case "tool_name":
      if (!s.toolName) return "invalid_input"
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
  if (mode === "advanced" || mode === "guided") {
    try {
      const presets = await cloud.listPresets()
      const seen = new Set<string>()
      for (const p of presets) {
        if (p.enforcement !== "enforcing" || !p.step || seen.has(p.step)) continue
        seen.add(p.step)
        wiredSteps.push({ step: p.step, description: p.description, category: p.category })
      }
      wiredSteps.sort((a, b) => a.step.localeCompare(b.step))
    } catch { /* best-effort */ }
  }

  return (
    <>
      {flash?.kind === "error" && (
        <ErrorState title={flash.text} severity="error" />
      )}

      {mode === null && <PickerLanding t={t} />}

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
                  "Python regex; must contain (?P<matter>…) and (?P<doc_id>…)",
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

function PickerLanding({
  t,
}: { t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string }) {
  return (
    <section className="rounded-2xl border border-[var(--color-accent)]/20 bg-[var(--color-accent)]/[0.02] p-5 shadow-sm">
      <header className="mb-4 flex items-start justify-between">
        <div>
          <h1 className="text-lg font-bold text-[var(--color-text-primary)] m-0">
            {t("newPolicy.picker.title")}
          </h1>
          <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
            {t("newPolicy.picker.subtitle")}
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
  if (state.toolName) params.set("toolName", state.toolName)
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
      {state.toolName && <input type="hidden" name="toolName" value={state.toolName} />}
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
    conditionKind,
    toolName: searchParams.toolName || undefined,
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

  // Auto-skip Step 3 visually when conditionKind === "none". User
  // navigating from Step 4 ← back lands on Step 2 instead.
  const effectiveStep = state.conditionKind === "none" && step === 3 ? 4 : step

  return (
    <div className="max-w-2xl mx-auto">
      <WizardHeader t={t} step={effectiveStep} total={WIZARD_TOTAL} />

      {effectiveStep === 1 && <Step1Lifecycle t={t} locale={locale} state={state} action={advanceAction} />}
      {effectiveStep === 2 && <Step2ConditionKind t={t} locale={locale} state={state} action={advanceAction} />}
      {effectiveStep === 3 && <Step3Specifics t={t} locale={locale} state={state} wiredSteps={wiredSteps} action={advanceAction} />}
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

function Step2ConditionKind({
  t, locale, state, action,
}: {
  state: WizardState; locale: "ko" | "en"
  action: (fd: FormData) => Promise<void>
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const lifecycle = state.lifecycle ?? "before_tool_use"
  const kinds = CONDITION_KINDS_BY_LIFECYCLE[lifecycle]
  const defaultPick: ConditionKind = state.conditionKind && kinds.includes(state.conditionKind)
    ? state.conditionKind : kinds[0]
  const ko = locale === "ko"
  const labels: Record<ConditionKind, { label: string; sub: string }> = ko ? {
    tool_name:         { label: "도구 이름",        sub: "특정 도구가 호출될 때 (예: Bash, Edit, Write)." },
    fetch_domain:      { label: "Fetch 도메인",     sub: "WebFetch가 특정 도메인에 접근하려고 할 때." },
    domain_allowlist:  { label: "도메인 allowlist", sub: "허용 목록에 없는 외부 도메인 접근 차단." },
    none:              { label: "조건 없이",        sub: "모든 트리거에 대해 발동합니다 (조건 없음)." },
    regex:             { label: "정규식",           sub: "도구 출력이 Python re 패턴에 매칭되면." },
    llm_critic:        { label: "LLM critic",      sub: "자연어 기준을 LLM에 물어보고 NO면 발동." },
    evidence_ref:      { label: "Evidence ref",    sub: "프리셋 verifier 결과가 FAIL이면 발동." },
    shacl:             { label: "SHACL shape",     sub: "Turtle로 작성한 시맨틱 제약을 위반하면." },
  } : {
    tool_name:         { label: "Tool name",       sub: "Fires when a specific tool is invoked (e.g. Bash, Edit, Write)." },
    fetch_domain:      { label: "Fetch domain",    sub: "Fires when WebFetch tries to hit a specific domain." },
    domain_allowlist:  { label: "Domain allowlist", sub: "Blocks fetches to any domain not on the allowlist." },
    none:              { label: "No condition",    sub: "Fires on every trigger (no per-call check)." },
    regex:             { label: "Regex",           sub: "Fires when the tool output matches a Python re pattern." },
    llm_critic:        { label: "LLM critic",      sub: "Asks an LLM a yes/no criterion; fires on NO." },
    evidence_ref:      { label: "Evidence ref",    sub: "Fires when a wired verifier returns FAIL." },
    shacl:             { label: "SHACL shape",     sub: "Fires when the evidence graph doesn't conform to a Turtle shape." },
  }
  const badgeNone   = ko ? "Step 3 건너뜀" : "step 3 skipped"
  const badgePrev   = ko ? "프리뷰" : "preview"
  return (
    <StepShell
      t={t}
      prevHref={buildWizardHref(state, 1)}
      heading={t("newPolicy.wizard.step2.heading")}
      helper={ko
        ? `${lifecycle} 라이프사이클에서 검사할 조건을 고르세요.`
        : `Pick the condition to check in the ${lifecycle} lifecycle.`}
    >
      <form action={action} className="space-y-3">
        <input type="hidden" name="_step" value="2" />
        <HiddenState state={{ lifecycle: state.lifecycle }} />
        {kinds.map((k) => (
          <RadioCard
            key={k}
            name="conditionKind"
            value={k}
            defaultChecked={defaultPick === k}
            label={labels[k].label}
            sub={labels[k].sub}
            badge={k === "none" ? { variant: "info", text: badgeNone }
              : k === "llm_critic" || k === "shacl" ? { variant: "info", text: badgePrev }
              : undefined}
          />
        ))}
        <NextButton label={t("newPolicy.wizard.next")} />
      </form>
    </StepShell>
  )
}

/* ─── Step 3. Specifics ──────────────────────────────────────────── */

function Step3Specifics({
  t, locale, state, wiredSteps, action,
}: {
  state: WizardState; locale: "ko" | "en"
  wiredSteps: WiredStep[]
  action: (fd: FormData) => Promise<void>
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const ko = locale === "ko"
  const kind = state.conditionKind ?? "tool_name"
  return (
    <StepShell
      t={t}
      prevHref={buildWizardHref(state, 2)}
      heading={t("newPolicy.wizard.step3.heading")}
      helper={t("newPolicy.wizard.step3.helper")}
    >
      <form action={action} className="space-y-4">
        <input type="hidden" name="_step" value="3" />
        <HiddenState state={{ lifecycle: state.lifecycle, conditionKind: state.conditionKind }} />
        {kind === "tool_name" && (
          <div>
            <FieldLabel>{ko ? "도구 이름" : "Tool name"}</FieldLabel>
            <input
              name="toolName"
              required
              maxLength={128}
              defaultValue={state.toolName ?? "Bash"}
              list="tool-list"
              placeholder="Bash"
              spellCheck={false}
              autoComplete="off"
              autoFocus
              className={inputCls() + " font-mono"}
            />
            <datalist id="tool-list">
              {TOOL_PRESETS.map((m) => <option key={m} value={m} />)}
            </datalist>
            <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
              {ko
                ? <>빌트인 도구 이름 또는 <code className="font-mono">mcp__server__tool</code> 패턴.</>
                : <>A built-in tool name or an <code className="font-mono">mcp__server__tool</code> pattern.</>}
            </p>
          </div>
        )}
        {kind === "fetch_domain" && (
          <div>
            <FieldLabel>{ko ? "Fetch 도메인" : "Fetch domain"}</FieldLabel>
            <input
              name="fetchDomain"
              required
              maxLength={256}
              defaultValue={state.fetchDomain ?? ""}
              placeholder="example.com"
              spellCheck={false}
              autoComplete="off"
              autoFocus
              className={inputCls() + " font-mono"}
            />
            <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
              {ko
                ? "WebFetch가 해당 도메인 (또는 서브도메인)에 접근하려고 할 때 발동."
                : "Fires when WebFetch tries to hit this domain (or its subdomains)."}
            </p>
          </div>
        )}
        {kind === "domain_allowlist" && (
          <div>
            <FieldLabel>{ko ? "허용 도메인 (쉼표 구분)" : "Allowed domains (comma-separated)"}</FieldLabel>
            <input
              name="allowlist"
              required
              maxLength={2000}
              defaultValue={state.allowlist ?? ""}
              placeholder="api.openai.com, github.com, npmjs.com"
              spellCheck={false}
              autoComplete="off"
              autoFocus
              className={inputCls() + " font-mono"}
            />
            <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
              {ko
                ? "여기 명시한 도메인이 아니면 정책이 발동합니다 (negative match)."
                : "Anything not on this list fires the policy (negative match)."}
            </p>
          </div>
        )}
        {kind === "regex" && (
          <div>
            <FieldLabel>{ko ? "정규식 패턴 (Python re)" : "Regex pattern (Python re)"}</FieldLabel>
            <input
              name="pattern"
              required
              maxLength={2000}
              defaultValue={state.pattern ?? ""}
              placeholder="AKIA[A-Z0-9]{16}"
              spellCheck={false}
              autoComplete="off"
              autoFocus
              className={inputCls() + " font-mono"}
            />
          </div>
        )}
        {kind === "llm_critic" && (
          <div>
            <FieldLabel>{ko ? "LLM critic 기준" : "LLM critic criterion"}</FieldLabel>
            <Textarea
              id="w-llm"
              name="llmCriterion"
              rows={3}
              required
              defaultValue={state.llmCriterion ?? ""}
              placeholder={ko
                ? "예: 출력에 사용자가 묻지 않은 추측이 포함되어 있는가?"
                : "e.g. Does the output contain a guess the user did not ask for?"}
              spellCheck={false}
              autoComplete="off"
              monospace
              label=""
            />
            <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
              {ko
                ? "자연어 기준 → 백엔드가 LLM에 yes/no로 물어봄. preview 단계."
                : "Natural-language criterion. Backend asks an LLM yes/no. Preview."}
            </p>
          </div>
        )}
        {kind === "evidence_ref" && (
          <div className="space-y-3">
            <FieldLabel>{ko ? "참조할 evidence verifier (1개 이상)" : "Evidence verifier(s) to reference"}</FieldLabel>
            <p className="text-xs text-[var(--color-text-tertiary)] -mt-1">
              {ko
                ? "아래 verifier의 결과가 FAIL이면 정책이 발동합니다. 여러 개 고르면 ALL이 PASS여야 통과."
                : "Fires when any picked verifier returns FAIL. If you pick multiple, ALL must PASS to clear."}
            </p>
            <div className="space-y-2">
              {wiredSteps.length === 0 && (
                <p className="text-xs text-amber-700">
                  {ko
                    ? "연결된 verifier가 없습니다. 먼저 /presets에서 verifier를 enable 하세요."
                    : "No wired verifiers yet. Enable one under /presets first."}
                </p>
              )}
              {wiredSteps.map((w) => (
                <CheckboxCard
                  key={w.step}
                  name="evidence_ref"
                  value={w.step}
                  defaultChecked={state.evidenceRefs?.includes(w.step) ?? false}
                  label={w.step}
                  sub={w.description}
                />
              ))}
            </div>
          </div>
        )}
        {kind === "shacl" && (
          <div>
            <FieldLabel>{ko ? "SHACL shape (Turtle)" : "SHACL shape (Turtle)"}</FieldLabel>
            <Textarea
              id="w-shacl"
              name="shaclTtl"
              rows={6}
              required
              defaultValue={state.shaclTtl ?? ""}
              placeholder={"@prefix sh: <http://www.w3.org/ns/shacl#> .\n…"}
              spellCheck={false}
              autoComplete="off"
              monospace
              label=""
            />
            <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
              {ko
                ? "evidence 그래프가 이 shape에 conform하지 않으면 정책 발동. preview 단계."
                : "Fires when the evidence graph does not conform to this shape. Preview."}
            </p>
          </div>
        )}
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
      prevHref={buildWizardHref(state, state.conditionKind === "none" ? 2 : 3)}
      heading={t("newPolicy.wizard.step4.heading")}
      helper={header + (ko ? " 어떤 동작을 할까요?" : " what should this policy do?")}
    >
      <form action={action} className="space-y-3">
        <input type="hidden" name="_step" value="4" />
        <HiddenState state={{
          lifecycle: state.lifecycle,
          conditionKind: state.conditionKind,
          toolName: state.toolName,
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
  const tail = state.toolName
    ? state.toolName.toLowerCase().replace(/[^a-z0-9]+/g, "-")
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

          <dt className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold">trigger (IR)</dt>
          <dd><code className="font-mono">{event} · {matcher}</code></dd>

          <dt className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold">condition</dt>
          <dd className="text-[var(--color-text-secondary)]">
            {state.conditionKind === "none" ? "—" : state.conditionKind}
            {state.conditionKind === "tool_name" && state.toolName && <> · <code className="font-mono">{state.toolName}</code></>}
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

          <dt className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold">sentinel_re</dt>
          <dd>
            <code className="font-mono text-[11.5px] break-all bg-gray-50 px-1.5 py-0.5 rounded border border-black/[0.06]">
              {SENTINEL_RE_DEFAULT}
            </code>
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
