import Link from "next/link"
import { revalidatePath } from "next/cache"
import { redirect } from "next/navigation"
import { XMarkIcon, ArrowLeftIcon, SparklesIcon, CodeBracketIcon, AdjustmentsHorizontalIcon, CheckIcon } from "@heroicons/react/24/outline"
import PolicyBuilder from "@/components/PolicyBuilder"
import { codeForError, resolveFlash } from "@/lib/flash"
import { validatePolicyId } from "@/lib/policy-id"
import { validateDraft, type PolicyDraft } from "@/lib/policy-builder"
import { CloudConfigError, cloud, type CompileResult } from "@/lib/cloud"
import { getT } from "@/lib/i18n/server"
import {
  Badge, Card, CodeBlock, ErrorState, PageHeader,
  SubmitButton, Textarea,
} from "@/components/ui"

export const dynamic = "force-dynamic"

type Mode = "nl" | "guided" | "advanced"
const WIZARD_TOTAL = 6
// Built-in Claude Code tool list — MUST stay in sync with the backend's
// matrix._BUILTIN_TOOLS (otherwise wizard-built policies trip the IR
// loader's "unknown matcher class" guard).
const TOOL_PRESETS = [
  "Bash", "Read", "Edit", "Write", "Glob", "Grep",
  "NotebookEdit", "TodoWrite", "WebFetch", "WebSearch",
] as const
const ON_MISSING_PRESETS = ["deny", "ask", "log", "allow"] as const
type OnMissing = (typeof ON_MISSING_PRESETS)[number]
type EventKind =
  | "PreToolUse" | "PostToolUse"
  | "Stop" | "SubagentStop"
  | "UserPromptSubmit"
  | "PreCompact"
  | "SessionStart" | "SessionEnd"

const EVENT_KINDS: readonly EventKind[] = [
  "PreToolUse", "PostToolUse",
  "UserPromptSubmit",
  "PreCompact",
  "Stop", "SubagentStop",
  "SessionStart", "SessionEnd",
]

// Events that carry a tool name in their hook payload. The wizard's
// Step 2 (matcher chips) only shows for these. For the rest, matcher
// is forced to "*" and Step 2 is auto-skipped (Next on Step 1 lands
// the user directly on Step 3).
const TOOL_CONTEXT_EVENTS: ReadonlySet<EventKind> = new Set([
  "PreToolUse", "PostToolUse",
])

// matrix.LEGAL_COMBINATIONS, narrowed by event. Mirrors the backend's
// policy/matrix.py — keep in sync. No-tool-context events all use the
// wildcard matcher class so the decision options follow the lifecycle:
// "before X" can deny/ask, "after X" can only log/allow.
const LEGAL_ON_MISSING_BY_EVENT: Record<EventKind, readonly OnMissing[]> = {
  PreToolUse:       ["deny", "ask"],
  PostToolUse:      ["log", "allow"],
  UserPromptSubmit: ["deny", "ask", "log"],
  PreCompact:       ["deny", "log"],
  Stop:             ["log"],
  SubagentStop:     ["log"],
  SessionStart:     ["log"],
  SessionEnd:       ["log"],
}

interface WizardState {
  event?: EventKind
  matcher?: string
  /** N verifiers (backend's `requires: list[EvidenceReq]` is len>=1).
   * Comma-joined in the URL so the hidden carry-over stays one field. */
  verifiers?: string[]
  on_missing?: OnMissing
  id?: string
  description?: string
}

function parseVerifierList(raw: string | undefined): string[] {
  if (!raw) return []
  return raw.split(",").map((s) => s.trim()).filter(Boolean)
}

// ── server actions ──────────────────────────────────────────────────

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

/** Move the wizard one step forward. All accumulated fields ride in the
 * URL so browser back works as a natural "previous step" affordance. */
async function advanceWizard(formData: FormData): Promise<void> {
  "use server"
  const params = new URLSearchParams()
  params.set("mode", "guided")
  const stepIn = Number(formData.get("_step") ?? "1")
  let nextStep = stepIn + 1
  // Multi-verifier merge: Step 3 emits N checkboxes named "verifier";
  // earlier steps carry the comma-joined "verifiers" hidden field.
  // Merge both into one ordered, deduped list. First-seen order
  // preserved so editing earlier picks does not churn the URL.
  const verifierChecks = formData
    .getAll("verifier")
    .filter((v): v is string => typeof v === "string")
    .map((v) => v.trim())
    .filter(Boolean)
  const verifiersCarry = (formData.get("verifiers")?.toString() ?? "")
    .split(",").map((s) => s.trim()).filter(Boolean)
  // On Step 3 the checkbox set is the new authoritative pick. Earlier
  // visits' carry-over only applies when Step 3 itself is not the
  // submitter (i.e. user is moving forward from Step 1 / 2 / 4 / 5).
  const mergedVerifiers: string[] = []
  const sourceList = stepIn === 3 ? verifierChecks : [...verifierChecks, ...verifiersCarry]
  for (const v of sourceList) {
    if (!mergedVerifiers.includes(v)) mergedVerifiers.push(v)
  }
  if (mergedVerifiers.length > 0) params.set("verifiers", mergedVerifiers.join(","))
  for (const [k, v] of formData.entries()) {
    if (typeof v !== "string") continue
    if (k.startsWith("$ACTION") || k === "_step") continue
    if (k === "verifier" || k === "verifiers") continue
    if (!v.trim()) continue
    params.set(k, v.trim())
  }
  // Auto-skip Step 2 (matcher chips) for events that don't carry a
  // tool context. matcher is forced to "*" because that's the only
  // matcher class the backend accepts for these events.
  const pickedEvent = (params.get("event") || "PreToolUse") as EventKind
  if (stepIn === 1 && !TOOL_CONTEXT_EVENTS.has(pickedEvent)) {
    params.set("matcher", "*")
    nextStep = 3
  }
  params.set("step", String(nextStep))
  redirect(`/policies/new?${params.toString()}`)
}

/** Final step → build a complete PolicyDraft from the URL state and PUT. */
async function saveWizard(formData: FormData): Promise<void> {
  "use server"
  const event = String(formData.get("event") ?? "PreToolUse") as EventKind
  const matcher = String(formData.get("matcher") ?? "").trim()
  const verifiers = (formData.get("verifiers")?.toString() ?? "")
    .split(",").map((s) => s.trim()).filter(Boolean)
  const on_missing = (String(formData.get("on_missing") ?? "deny")) as OnMissing
  const id = String(formData.get("id") ?? "").trim()
  const description = String(formData.get("description") ?? "").trim()
  const source = String(formData.get("source") ?? "org")
  const sentinelTag = "FILE_COURT"

  if (!id || !matcher || verifiers.length === 0) {
    redirect("/policies/new?mode=guided&step=1&err=invalid_input"); return
  }
  const sentinel_re = `${sentinelTag}_(?P<matter>[A-Za-z0-9]+)_(?P<doc_id>[A-Za-z0-9]+)`

  const summary = verifiers.length === 1
    ? `Require ${verifiers[0]}=pass before ${event}|${matcher}`
    : `Require ${verifiers.length} verifiers (all pass) before ${event}|${matcher}`
  const draft: PolicyDraft = {
    id,
    version: "0.1",
    description: description || summary,
    trigger: { host: "claude-code", event, matcher },
    sentinel_re,
    requires: verifiers.map((step) => ({ step, verdict: "pass" })),
    on_missing,
    on_signature_invalid: "deny",
    gate_binary: "/usr/local/bin/magi-gate.sh",
  }
  await persistDraft(draft, source)
}

// ── decoders ────────────────────────────────────────────────────────

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

// ── page ────────────────────────────────────────────────────────────

export default async function NewPolicyPage({
  searchParams,
}: { searchParams: Record<string, string | undefined> }) {
  const { t } = await getT()
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

  let wiredSteps: { step: string; description: string }[] = []
  if (mode === "advanced" || mode === "guided") {
    try {
      const presets = await cloud.listPresets()
      const seen = new Set<string>()
      for (const p of presets) {
        if (p.enforcement !== "enforcing" || !p.step || seen.has(p.step)) continue
        seen.add(p.step)
        wiredSteps.push({ step: p.step, description: p.description })
      }
      wiredSteps.sort((a, b) => a.step.localeCompare(b.step))
    } catch { /* best-effort; empty datalist is fine */ }
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
          wiredSteps={wiredSteps.length > 0 ? wiredSteps : [{ step: "citation_verify", description: "Cite verifier" }]}
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
              wiredSteps={wiredSteps.map(w => w.step)}
              labels={{
                irFields: "IR fields",
                compiledPreview: "Compiled preview",
                compiledPreviewHint:
                  "Live mirror of what the cloud compiler will emit. The cloud is authoritative.",
                id: "id",
                description: "description",
                triggerEvent: "trigger.event",
                triggerMatcher: "trigger.matcher",
                onMissing: "on_missing (decision)",
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

// ── picker landing (no mode) ────────────────────────────────────────

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

// ── authoring shell ────────────────────────────────────────────────

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

// ── guided wizard ─────────────────────────────────────────────────

function buildWizardHref(state: WizardState, step: number): string {
  const params = new URLSearchParams()
  params.set("mode", "guided")
  params.set("step", String(step))
  if (state.event) params.set("event", state.event)
  if (state.matcher) params.set("matcher", state.matcher)
  if (state.verifiers && state.verifiers.length > 0) {
    params.set("verifiers", state.verifiers.join(","))
  }
  if (state.on_missing) params.set("on_missing", state.on_missing)
  if (state.id) params.set("id", state.id)
  if (state.description) params.set("description", state.description)
  return `/policies/new?${params.toString()}`
}

function HiddenState({ state }: { state: WizardState }) {
  return (
    <>
      {state.event && <input type="hidden" name="event" value={state.event} />}
      {state.matcher && <input type="hidden" name="matcher" value={state.matcher} />}
      {state.verifiers && state.verifiers.length > 0 && (
        <input type="hidden" name="verifiers" value={state.verifiers.join(",")} />
      )}
      {state.on_missing && <input type="hidden" name="on_missing" value={state.on_missing} />}
      {state.id && <input type="hidden" name="id" value={state.id} />}
      {state.description && <input type="hidden" name="description" value={state.description} />}
    </>
  )
}

function WizardHeader({
  t, step, total, state,
}: {
  step: number; total: number; state: WizardState
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
  t, wiredSteps, searchParams, advanceAction, saveAction,
}: {
  wiredSteps: { step: string; description: string }[]
  searchParams: Record<string, string | undefined>
  advanceAction: (fd: FormData) => Promise<void>
  saveAction: (fd: FormData) => Promise<void>
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const step = Math.max(1, Math.min(WIZARD_TOTAL, Number(searchParams.step ?? 1)))
  const state: WizardState = {
    event: (searchParams.event as EventKind) || undefined,
    matcher: searchParams.matcher || undefined,
    verifiers: ((): string[] | undefined => {
      const list = parseVerifierList(
        searchParams.verifiers ?? searchParams.verifier,
      )
      return list.length > 0 ? list : undefined
    })(),
    on_missing: (searchParams.on_missing as OnMissing) || undefined,
    id: searchParams.id || undefined,
    description: searchParams.description || undefined,
  }

  return (
    <div className="max-w-2xl mx-auto">
      <WizardHeader t={t} step={step} total={WIZARD_TOTAL} state={state} />

      {step === 1 && <Step1Event t={t} state={state} action={advanceAction} />}
      {step === 2 && <Step2Matcher t={t} state={state} action={advanceAction} />}
      {step === 3 && <Step3Verifier t={t} state={state} wiredSteps={wiredSteps} action={advanceAction} />}
      {step === 4 && <Step4OnMissing t={t} state={state} action={advanceAction} />}
      {step === 5 && <Step5Naming t={t} state={state} action={advanceAction} />}
      {step === 6 && <Step6Review t={t} state={state} action={saveAction} wiredSteps={wiredSteps} />}
    </div>
  )
}

function StepShell({
  t, step, prevHref, heading, helper, children,
}: {
  step: number; prevHref: string | null; heading: string; helper?: string
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
  name, value, defaultChecked, label, sub, recommended,
}: {
  name: string; value: string; defaultChecked?: boolean
  label: string; sub: string; recommended?: boolean
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
          {recommended && (
            <Badge variant="ok">recommended</Badge>
          )}
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

function Step1Event({
  t, state, action,
}: {
  state: WizardState; action: (fd: FormData) => Promise<void>
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const current = state.event ?? "PreToolUse"
  return (
    <StepShell
      t={t}
      step={1}
      prevHref={null}
      heading={t("newPolicy.wizard.step1.heading")}
      helper={t("newPolicy.wizard.step1.helper")}
    >
      <form action={action} className="space-y-3">
        <input type="hidden" name="_step" value="1" />
        {EVENT_KINDS.map((ev) => (
          <RadioCard
            key={ev}
            name="event"
            value={ev}
            defaultChecked={current === ev}
            label={ev}
            sub={t(`newPolicy.wizard.step1.event.${ev}.sub` as never)}
            recommended={ev === "PreToolUse"}
          />
        ))}
        <NextButton label={t("newPolicy.wizard.next")} />
      </form>
    </StepShell>
  )
}

function Step2Matcher({
  t, state, action,
}: {
  state: WizardState; action: (fd: FormData) => Promise<void>
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  return (
    <StepShell
      t={t}
      step={2}
      prevHref={buildWizardHref(state, 1)}
      heading={t("newPolicy.wizard.step2.heading")}
      helper={t("newPolicy.wizard.step2.helper")}
    >
      <form action={action} className="space-y-4">
        <input type="hidden" name="_step" value="2" />
        <HiddenState state={{ event: state.event }} />
        <input
          name="matcher"
          required
          maxLength={128}
          defaultValue={state.matcher ?? ""}
          list="matcher-list"
          placeholder="Bash"
          spellCheck={false}
          autoComplete="off"
          autoFocus
          className="w-full rounded-xl border border-black/[0.08] bg-white px-4 py-3 text-base leading-6 text-[var(--color-text-primary)] focus:border-[var(--color-accent)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]/20 font-mono"
        />
        <datalist id="matcher-list">
          {TOOL_PRESETS.map(tool => <option key={tool} value={tool} />)}
        </datalist>
        <div className="flex flex-wrap gap-1.5">
          {TOOL_PRESETS.map(tool => (
            <button
              key={tool}
              type="submit"
              name="matcher"
              value={tool}
              formAction={action}
              formNoValidate
              className="rounded-full border border-black/[0.08] bg-white px-3 py-1 text-xs font-mono text-[var(--color-text-secondary)] hover:border-[var(--color-accent)]/40 hover:bg-[var(--color-accent)]/[0.04] cursor-pointer transition-colors"
            >
              {tool}
            </button>
          ))}
        </div>
        <NextButton label={t("newPolicy.wizard.next")} />
      </form>
    </StepShell>
  )
}

function Step3Verifier({
  t, state, wiredSteps, action,
}: {
  state: WizardState; wiredSteps: { step: string; description: string }[]
  action: (fd: FormData) => Promise<void>
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  // First visit (no prior picks) → preselect the first wired verifier
  // so the user can hit Next immediately. Returning visits keep their
  // multi-pick. The backend rejects an empty `requires` list so we
  // need at least one box ticked; client-side enforcement happens at
  // the form's `data-min-checked` attribute (defensive — server-side
  // saveWizard also redirects to err=invalid_input).
  const picked: Set<string> = new Set(
    state.verifiers && state.verifiers.length > 0
      ? state.verifiers
      : wiredSteps.length > 0 ? [wiredSteps[0].step] : [],
  )
  return (
    <StepShell
      t={t}
      step={3}
      prevHref={buildWizardHref(state, 2)}
      heading={t("newPolicy.wizard.step3.heading")}
      helper={t("newPolicy.wizard.step3.helper")}
    >
      <form action={action} className="space-y-3">
        <input type="hidden" name="_step" value="3" />
        <HiddenState state={{ event: state.event, matcher: state.matcher }} />
        <p className="text-xs text-[var(--color-text-tertiary)]">
          {t("newPolicy.wizard.step3.multiHint")}
        </p>
        {wiredSteps.map((v) => (
          <CheckboxCard
            key={v.step}
            name="verifier"
            value={v.step}
            defaultChecked={picked.has(v.step)}
            label={v.step}
            sub={v.description}
          />
        ))}
        <NextButton label={t("newPolicy.wizard.next")} />
      </form>
    </StepShell>
  )
}

function Step4OnMissing({
  t, state, action,
}: {
  state: WizardState; action: (fd: FormData) => Promise<void>
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  // Filter to options the backend will accept for this event — keeps
  // the wizard from minting policies the IR loader rejects with 422.
  const allowed: readonly OnMissing[] = LEGAL_ON_MISSING_BY_EVENT[state.event ?? "PreToolUse"]
  const defaultPick: OnMissing = state.on_missing && allowed.includes(state.on_missing)
    ? state.on_missing
    : allowed[0]
  const OPTIONS: Record<OnMissing, { label: string; sub: string; recommended?: boolean }> = {
    deny:  { label: t("newPolicy.wizard.step4.deny.label"),
             sub:   t("newPolicy.wizard.step4.deny.sub"),  recommended: true },
    ask:   { label: t("newPolicy.wizard.step4.ask.label"),
             sub:   t("newPolicy.wizard.step4.ask.sub") },
    log:   { label: t("newPolicy.wizard.step4.log.label"),
             sub:   t("newPolicy.wizard.step4.log.sub") },
    allow: { label: t("newPolicy.wizard.step4.allow.label"),
             sub:   t("newPolicy.wizard.step4.allow.sub") },
  }
  return (
    <StepShell
      t={t}
      step={4}
      prevHref={buildWizardHref(state, 3)}
      heading={t("newPolicy.wizard.step4.heading")}
      helper={t("newPolicy.wizard.step4.helper")}
    >
      <form action={action} className="space-y-3">
        <input type="hidden" name="_step" value="4" />
        <HiddenState state={{ event: state.event, matcher: state.matcher, verifiers: state.verifiers }} />
        {allowed.map((opt) => (
          <RadioCard
            key={opt}
            name="on_missing"
            value={opt}
            defaultChecked={defaultPick === opt}
            label={OPTIONS[opt].label}
            sub={OPTIONS[opt].sub}
            recommended={opt === allowed[0] && OPTIONS[opt].recommended}
          />
        ))}
        <NextButton label={t("newPolicy.wizard.next")} />
      </form>
    </StepShell>
  )
}

function Step5Naming({
  t, state, action,
}: {
  state: WizardState; action: (fd: FormData) => Promise<void>
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  return (
    <StepShell
      t={t}
      step={5}
      prevHref={buildWizardHref(state, 4)}
      heading={t("newPolicy.wizard.step5.heading")}
      helper={t("newPolicy.wizard.step5.helper")}
    >
      <form action={action} className="space-y-4">
        <input type="hidden" name="_step" value="5" />
        <HiddenState state={{
          event: state.event, matcher: state.matcher,
          verifiers: state.verifiers, on_missing: state.on_missing,
        }} />
        <div>
          <label htmlFor="w-id" className="block text-xs font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)] mb-1.5">
            {t("newPolicy.guided.field.id")}
          </label>
          <input
            id="w-id"
            name="id"
            required
            maxLength={128}
            pattern="[A-Za-z0-9._\-/]{1,128}"
            defaultValue={state.id ?? ""}
            placeholder="legal-filing/v1"
            spellCheck={false}
            autoComplete="off"
            autoFocus
            className="w-full rounded-xl border border-black/[0.08] bg-white px-4 py-3 text-base leading-6 text-[var(--color-text-primary)] focus:border-[var(--color-accent)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]/20 font-mono"
          />
          <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">{t("newPolicy.guided.field.idHint")}</p>
        </div>
        <div>
          <label htmlFor="w-desc" className="block text-xs font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)] mb-1.5">
            {t("newPolicy.guided.field.description")}
          </label>
          <input
            id="w-desc"
            name="description"
            maxLength={256}
            defaultValue={state.description ?? ""}
            placeholder={t("newPolicy.guided.field.descriptionPh")}
            className="w-full rounded-xl border border-black/[0.08] bg-white px-4 py-3 text-base leading-6 text-[var(--color-text-primary)] focus:border-[var(--color-accent)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]/20"
          />
        </div>
        <NextButton label={t("newPolicy.wizard.next")} />
      </form>
    </StepShell>
  )
}

function Step6Review({
  t, state, action, wiredSteps,
}: {
  state: WizardState; action: (fd: FormData) => Promise<void>
  wiredSteps: { step: string; description: string }[]
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const picked = state.verifiers ?? []
  const verifierSummary = picked.length === 1
    ? picked[0]
    : picked.length === 0
      ? "(none)"
      : `${picked.join(" + ")} (all=pass)`
  return (
    <StepShell
      t={t}
      step={6}
      prevHref={buildWizardHref(state, 5)}
      heading={t("newPolicy.wizard.step6.heading")}
      helper={t("newPolicy.wizard.step6.helper")}
    >
      <Card>
        <p className="text-sm font-semibold mb-3">{t("newPolicy.wizard.step6.summaryHead")}</p>
        <p className="text-sm leading-relaxed text-[var(--color-text-secondary)]">
          {t("newPolicy.wizard.step6.summary", {
            event: state.event ?? "PreToolUse",
            matcher: state.matcher ?? "",
            verifier: verifierSummary,
            on_missing: state.on_missing ?? "deny",
          })}
        </p>
        <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1.5 text-xs mt-4 pt-4 border-t border-black/[0.06]">
          <dt className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold">id</dt>
          <dd className="font-mono text-[12.5px]" translate="no">{state.id}</dd>
          <dt className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold">trigger</dt>
          <dd><code className="font-mono">{state.event} · {state.matcher}</code></dd>
          <dt className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold">requires</dt>
          <dd>
            <ul className="space-y-1">
              {picked.map((v) => {
                const desc = wiredSteps.find((w) => w.step === v)?.description ?? ""
                return (
                  <li key={v}>
                    <code className="font-mono">{v}=pass</code>{" "}
                    <span className="text-[var(--color-text-tertiary)]">— {desc}</span>
                  </li>
                )
              })}
            </ul>
          </dd>
          <dt className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold">on_missing</dt>
          <dd className="text-[var(--color-text-secondary)]">{state.on_missing}</dd>
        </dl>
      </Card>
      <form action={action}>
        <HiddenState state={state} />
        <NextButton label={t("newPolicy.wizard.savePolicy")} />
      </form>
    </StepShell>
  )
}

// ── compile result block ────────────────────────────────────────

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
        <h2 className="text-md font-semibold m-0">
          {t("compile.result.title")}
        </h2>
        <Badge variant={data.review.ok ? "ok" : "review"}>
          {data.review.ok
            ? t("compile.result.reviewerOk")
            : t("compile.result.reviewerFlagged")}
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
              {draft.requires.map(r => `${r.step}=${r.verdict}`).join(", ")}
            </dd>
          </>
        )}
        <dt className="text-[var(--color-text-tertiary)] text-xs uppercase tracking-wider font-semibold pt-0.5">on_missing</dt>
        <dd className="text-[var(--color-text-secondary)]">{draft.on_missing}</dd>
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
