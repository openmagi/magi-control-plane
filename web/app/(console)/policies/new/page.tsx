import Link from "next/link"
import { revalidatePath } from "next/cache"
import { redirect } from "next/navigation"
import { XMarkIcon, ArrowLeftIcon, SparklesIcon, CodeBracketIcon, AdjustmentsHorizontalIcon, CheckIcon } from "@heroicons/react/24/outline"
import PolicyBuilder from "@/components/PolicyBuilder"
import { codeForError, resolveFlash } from "@/lib/flash"
import { validatePolicyId } from "@/lib/policy-id"
import {
  classifyMatcher, isLegal,
  validateDraft, type PolicyDraft,
} from "@/lib/policy-builder"
import { CloudConfigError, cloud, type CompileResult } from "@/lib/cloud"
import { getT } from "@/lib/i18n/server"
import {
  Badge, Card, CodeBlock, ErrorState, PageHeader,
  SubmitButton, Textarea,
} from "@/components/ui"
import SentinelModeSection from "./_components/SentinelModeSection"
import MinOneSubmit from "./_components/MinOneSubmit"
import ConditionKindSection from "./_components/ConditionKindSection"

export const dynamic = "force-dynamic"

type Mode = "nl" | "guided" | "advanced"
const WIZARD_TOTAL = 6
// Built-in Claude Code tool list. MUST stay in sync with the backend's
// matrix._BUILTIN_TOOLS (otherwise wizard-built policies trip the IR
// loader's "unknown matcher class" guard).
const TOOL_PRESETS = [
  "Bash", "Read", "Edit", "Write", "Glob", "Grep",
  "NotebookEdit", "TodoWrite", "WebFetch", "WebSearch",
] as const
const ACTION_PRESETS = ["block", "ask", "audit"] as const
type Action = (typeof ACTION_PRESETS)[number]
type EventKind =
  | "PreToolUse" | "PostToolUse"
  | "Stop" | "SubagentStop"
  | "UserPromptSubmit"
  | "PreCompact"
  | "SessionStart" | "SessionEnd"

// Step 1 event picker is grouped by "level" of the lifecycle moment.
// Tool actions are concrete pre/post moments; content-flow moments are
// about what's being sent or summarised; boundary markers are zero-
// action lifecycle events (Stop / SubagentStop / Session*). Visual
// grouping prevents "Stop" looking like it sits at the same level as
// "PreToolUse."
type EventGroup = {
  labelKey: import("@/lib/i18n/dict").TKey
  events: readonly EventKind[]
}
const EVENT_GROUPS: readonly EventGroup[] = [
  {
    labelKey: "newPolicy.wizard.step1.group.toolActions",
    events: ["PreToolUse", "PostToolUse"],
  },
  {
    labelKey: "newPolicy.wizard.step1.group.contentFlow",
    events: ["UserPromptSubmit", "PreCompact"],
  },
  {
    labelKey: "newPolicy.wizard.step1.group.boundaries",
    events: ["Stop", "SubagentStop", "SessionStart", "SessionEnd"],
  },
]
const EVENT_KINDS: readonly EventKind[] = EVENT_GROUPS.flatMap(g => g.events)

// Events that carry a tool name in their hook payload. The wizard's
// Step 2 (matcher chips) only shows for these. For the rest, matcher
// is forced to "*" and Step 2 is auto-skipped (Next on Step 1 lands
// the user directly on Step 3).
const TOOL_CONTEXT_EVENTS: ReadonlySet<EventKind> = new Set([
  "PreToolUse", "PostToolUse",
])

// Verifier metadata as the wizard receives it from /verifiers.
type VerifierCategory = import("@/lib/cloud").PresetEntry["category"]
interface WiredStep {
  step: string
  description: string
  category: VerifierCategory
}

// D32: archetype is the user-facing "What to do?" picked in Step 2.
// It maps to the IR's (action, requires-shape) pair:
//
//   block         → action=block,  requires=[…verifiers] (Step 3 required)
//   ask           → action=ask,    requires=[…verifiers] (Step 3 required)
//   audit         → action=audit,  requires=[…verifiers] (Step 3 required)
//   emit-signal   → action=audit,  requires=[]            (Step 3 auto-skipped)
//   strip         → reserved for the verifier-protocol-mutation cycle;
//                   rendered as a disabled "Coming soon" card on
//                   PostToolUse and never sent to the backend.
const ARCHETYPE_PRESETS = ["block", "ask", "audit", "emit-signal", "strip"] as const
type Archetype = (typeof ARCHETYPE_PRESETS)[number]

// Per-event archetype filter. Mirrors magi-agent's "Step 2 archetypes
// filter" table:
//   PreToolUse           → block / ask / audit / emit-signal
//   PostToolUse          → audit / emit-signal / (strip coming-soon)
//   UserPromptSubmit     → block / ask / audit / emit-signal
//   PreCompact           → block / audit / emit-signal
//   Stop / SubagentStop  → audit / emit-signal
//   SessionStart/End     → audit / emit-signal
function archetypesFor(event: EventKind): readonly Archetype[] {
  switch (event) {
    case "PreToolUse":       return ["block", "ask", "audit", "emit-signal"]
    case "PostToolUse":      return ["audit", "emit-signal", "strip"]
    case "UserPromptSubmit": return ["block", "ask", "audit", "emit-signal"]
    case "PreCompact":       return ["block", "audit", "emit-signal"]
    case "Stop":             return ["audit", "emit-signal"]
    case "SubagentStop":     return ["audit", "emit-signal"]
    case "SessionStart":     return ["audit", "emit-signal"]
    case "SessionEnd":       return ["audit", "emit-signal"]
  }
}

// Strip is reserved. backend Verifier protocol has no mutated-payload
// channel yet, so picking it would save a policy the runtime can't
// honor. We render the card with a Coming-soon badge instead.
const STRIP_AVAILABLE = false

// Map an archetype pick to the IR action that gets saved.
function archetypeToAction(arch: Archetype): Action {
  if (arch === "block") return "block"
  if (arch === "ask") return "ask"
  return "audit"  // audit and emit-signal both map to audit; requires shape differs
}

// True when Step 3 (condition / verifier picker) should be auto-skipped
// for this archetype.
function archetypeSkipsCondition(arch: Archetype): boolean {
  return arch === "emit-signal" || arch === "strip"
}

// Per-event recommended verifier categories (soft signal on Step 3).
const RECOMMENDED_CATEGORIES_BY_EVENT: Record<EventKind, ReadonlySet<VerifierCategory>> = {
  PreToolUse:       new Set<VerifierCategory>(["SECURITY", "RESEARCH", "OUTPUT", "CODING"]),
  PostToolUse:      new Set<VerifierCategory>(["SECURITY", "FACT", "OUTPUT", "RESEARCH"]),
  UserPromptSubmit: new Set<VerifierCategory>(["SECURITY"]),
  PreCompact:       new Set<VerifierCategory>(["SECURITY", "MEMORY"]),
  Stop:             new Set<VerifierCategory>(["ANSWER", "FACT", "OUTPUT"]),
  SubagentStop:     new Set<VerifierCategory>(["TASK", "OUTPUT"]),
  SessionStart:     new Set<VerifierCategory>(["MEMORY"]),
  SessionEnd:       new Set<VerifierCategory>(["MEMORY"]),
}

// Step 4 (Specifics) matcher chip palette is filtered by the picked
// action so the chosen archetype + matcher are guaranteed to land in a
// legal triple. Helper queries the policy-builder mirror.
function legalMatchersFor(event: EventKind, action: Action): readonly string[] {
  const candidates = [
    ...TOOL_PRESETS,
    "*",  // wildcard surfaced when legal for this (event, action) pair
  ]
  return candidates.filter((m) => isLegal(event, m, action))
}

type ConditionKind = "step" | "regex" | "llm_critic" | "shacl"
const CONDITION_KINDS: readonly ConditionKind[] = ["step", "regex", "llm_critic", "shacl"]

interface WizardState {
  event?: EventKind
  archetype?: Archetype
  matcher?: string
  /** D35: which kind of condition this policy carries. step is the
   * existing multi-verifier path; regex/llm_critic/shacl are inline. */
  condition_kind?: ConditionKind
  /** N verifiers (backend's `requires: list[EvidenceReq]` step kind).
   * Comma-joined in the URL. Used only when condition_kind=step. */
  verifiers?: string[]
  /** Inline regex pattern (condition_kind=regex). */
  cond_pattern?: string
  /** LLM critic criterion (condition_kind=llm_critic). */
  cond_criterion?: string
  /** SHACL shape Turtle (condition_kind=shacl). */
  cond_shape_ttl?: string
  id?: string
  description?: string
  /** D34: sentinel authoring split into two modes.
   *   tag   . pick a prefix, wizard builds `<TAG>_(?P<matter>…)_(?P<doc_id>…)`.
   *   custom. operator writes the full regex by hand; must still
   *            include both named groups (backend invariant).
   */
  sentinel_mode?: "tag" | "custom"
  sentinel_tag?: string
  sentinel_re_custom?: string
}

const SENTINEL_TAG_DEFAULT = "GATE"
const SENTINEL_TAG_RE = /^[A-Z][A-Z0-9_]{0,31}$/

function buildSentinelReFromTag(tag: string): string {
  return `${tag}_(?P<matter>[A-Za-z0-9]+)_(?P<doc_id>[A-Za-z0-9]+)`
}

function isValidCustomSentinelRe(raw: string): boolean {
  return raw.length > 0
    && raw.length <= 2000
    && raw.includes("?P<matter>")
    && raw.includes("?P<doc_id>")
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

  // Multi-verifier merge. Step 3 (Condition) emits N checkboxes named
  // "verifier"; other steps carry the comma-joined "verifiers" hidden
  // field. Step 3 is authoritative when it's the submitter so flipping
  // a checkbox off actually unsets it.
  const verifierChecks = formData
    .getAll("verifier")
    .filter((v): v is string => typeof v === "string")
    .map((v) => v.trim())
    .filter(Boolean)
  const verifiersCarry = (formData.get("verifiers")?.toString() ?? "")
    .split(",").map((s) => s.trim()).filter(Boolean)
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

  // D32 step routing. Step 2 picks an archetype, Step 3 is the
  // condition (skipped for emit-signal / strip), Step 4 is the
  // matcher/specifics form. Auto-skip rules:
  const archetype = (params.get("archetype") || "block") as Archetype
  if (stepIn === 2 && archetypeSkipsCondition(archetype)) {
    // emit-signal and strip don't have a condition step. jump from
    // "What to do?" straight to "Specifics".
    nextStep = 4
  }

  params.set("step", String(nextStep))
  redirect(`/policies/new?${params.toString()}`)
}

/** Final step → build a complete PolicyDraft from the URL state and PUT. */
async function saveWizard(formData: FormData): Promise<void> {
  "use server"
  const event = String(formData.get("event") ?? "PreToolUse") as EventKind
  const archetype = (String(formData.get("archetype") ?? "block")) as Archetype
  const matcher = String(formData.get("matcher") ?? "").trim()
  const conditionKindRaw = String(formData.get("condition_kind") ?? "step")
  const conditionKind: "step" | "regex" | "llm_critic" | "shacl" =
    conditionKindRaw === "regex" || conditionKindRaw === "llm_critic" || conditionKindRaw === "shacl"
      ? conditionKindRaw
      : "step"
  // For kind=step the wizard already merged checkbox picks into the
  // comma-joined `verifiers` URL field via advanceWizard. For other
  // kinds we use the dedicated cond_* fields directly.
  const verifierChecksNow = formData.getAll("verifier")
    .filter((v): v is string => typeof v === "string")
    .map(v => v.trim()).filter(Boolean)
  const verifiersCarry = (formData.get("verifiers")?.toString() ?? "")
    .split(",").map((s) => s.trim()).filter(Boolean)
  const verifiersUnique: string[] = []
  for (const v of [...verifierChecksNow, ...verifiersCarry]) {
    if (!verifiersUnique.includes(v)) verifiersUnique.push(v)
  }
  const verifiers = verifiersUnique
  const condPattern = String(formData.get("cond_pattern") ?? "").trim()
  const condCriterion = String(formData.get("cond_criterion") ?? "").trim()
  const condShapeTtl = String(formData.get("cond_shape_ttl") ?? "").trim()
  const id = String(formData.get("id") ?? "").trim()
  const description = String(formData.get("description") ?? "").trim()
  const source = String(formData.get("source") ?? "org")
  const sentinelMode = (String(formData.get("sentinel_mode") ?? "tag") === "custom")
    ? "custom" as const
    : "tag" as const
  const sentinelTagRaw = String(formData.get("sentinel_tag") ?? "").trim()
  const sentinelTag = SENTINEL_TAG_RE.test(sentinelTagRaw)
    ? sentinelTagRaw
    : SENTINEL_TAG_DEFAULT
  const sentinelCustomRaw = String(formData.get("sentinel_re_custom") ?? "").trim()

  if (archetype === "strip") {
    // Strip needs verifier-protocol mutation support that isn't built
    // yet; refuse cleanly rather than save a policy the runtime can't
    // honor.
    redirect("/policies/new?mode=guided&step=2&err=strip_unsupported"); return
  }

  const action = archetypeToAction(archetype)
  const isEmitSignal = archetype === "emit-signal"
  // D35: condition validation per kind. emit-signal bypasses (requires=[]).
  if (!isEmitSignal) {
    if (conditionKind === "step" && verifiers.length === 0) {
      redirect("/policies/new?mode=guided&step=3&err=invalid_input"); return
    }
    if (conditionKind === "regex" && !condPattern) {
      redirect("/policies/new?mode=guided&step=3&err=invalid_input"); return
    }
    if (conditionKind === "llm_critic" && !condCriterion) {
      redirect("/policies/new?mode=guided&step=3&err=invalid_input"); return
    }
    if (conditionKind === "shacl" && !condShapeTtl) {
      redirect("/policies/new?mode=guided&step=3&err=invalid_input"); return
    }
  }
  if (!id || !matcher) {
    redirect("/policies/new?mode=guided&step=4&err=invalid_input"); return
  }
  // D34: sentinel can be either tag-built or custom raw. Custom path
  // still has to carry both named groups per backend invariant; reject
  // here on validate-fail so the wizard can surface a precise error
  // instead of letting the cloud 400 the PUT.
  let sentinel_re: string
  if (sentinelMode === "custom") {
    if (!isValidCustomSentinelRe(sentinelCustomRaw)) {
      redirect("/policies/new?mode=guided&step=4&err=bad_sentinel_custom"); return
    }
    sentinel_re = sentinelCustomRaw
  } else {
    sentinel_re = buildSentinelReFromTag(sentinelTag)
  }

  let requires: PolicyDraft["requires"]
  let conditionDescription: string
  if (isEmitSignal) {
    requires = []
    conditionDescription = "no condition"
  } else if (conditionKind === "step") {
    requires = verifiers.map((step) => ({ kind: "step" as const, step, verdict: "pass" }))
    conditionDescription = verifiers.length === 1
      ? `${verifiers[0]} ≠ pass`
      : `any of ${verifiers.length} verifiers ≠ pass`
  } else if (conditionKind === "regex") {
    requires = [{ kind: "regex" as const, pattern: condPattern }]
    conditionDescription = `regex ${condPattern.slice(0, 40)} does not match`
  } else if (conditionKind === "llm_critic") {
    requires = [{ kind: "llm_critic" as const, criterion: condCriterion }]
    conditionDescription = `LLM critic fails: ${condCriterion.slice(0, 40)}`
  } else {
    requires = [{ kind: "shacl" as const, shape_ttl: condShapeTtl }]
    conditionDescription = "SHACL shape does not conform"
  }
  const summary = isEmitSignal
    ? `Emit signal on every ${event}|${matcher} (no condition)`
    : `${archetype} on ${event}|${matcher} when ${conditionDescription}`
  const draft: PolicyDraft = {
    id,
    version: "0.1",
    description: description || summary,
    trigger: { host: "claude-code", event, matcher },
    sentinel_re,
    requires,
    action,
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

  let wiredSteps: WiredStep[] = []
  if (mode === "advanced" || mode === "guided") {
    try {
      const presets = await cloud.listPresets()
      const seen = new Set<string>()
      for (const p of presets) {
        if (p.enforcement !== "enforcing" || !p.step || seen.has(p.step)) continue
        seen.add(p.step)
        wiredSteps.push({
          step: p.step,
          description: p.description,
          category: p.category,
        })
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
  if (state.archetype) params.set("archetype", state.archetype)
  if (state.matcher) params.set("matcher", state.matcher)
  if (state.verifiers && state.verifiers.length > 0) {
    params.set("verifiers", state.verifiers.join(","))
  }
  if (state.id) params.set("id", state.id)
  if (state.description) params.set("description", state.description)
  if (state.sentinel_mode) params.set("sentinel_mode", state.sentinel_mode)
  if (state.sentinel_tag) params.set("sentinel_tag", state.sentinel_tag)
  if (state.sentinel_re_custom) params.set("sentinel_re_custom", state.sentinel_re_custom)
  if (state.condition_kind) params.set("condition_kind", state.condition_kind)
  if (state.cond_pattern) params.set("cond_pattern", state.cond_pattern)
  if (state.cond_criterion) params.set("cond_criterion", state.cond_criterion)
  if (state.cond_shape_ttl) params.set("cond_shape_ttl", state.cond_shape_ttl)
  return `/policies/new?${params.toString()}`
}

function HiddenState({ state }: { state: WizardState }) {
  return (
    <>
      {state.event && <input type="hidden" name="event" value={state.event} />}
      {state.archetype && <input type="hidden" name="archetype" value={state.archetype} />}
      {state.matcher && <input type="hidden" name="matcher" value={state.matcher} />}
      {state.verifiers && state.verifiers.length > 0 && (
        <input type="hidden" name="verifiers" value={state.verifiers.join(",")} />
      )}
      {state.id && <input type="hidden" name="id" value={state.id} />}
      {state.description && <input type="hidden" name="description" value={state.description} />}
      {state.sentinel_mode && (
        <input type="hidden" name="sentinel_mode" value={state.sentinel_mode} />
      )}
      {state.sentinel_tag && (
        <input type="hidden" name="sentinel_tag" value={state.sentinel_tag} />
      )}
      {state.sentinel_re_custom && (
        <input type="hidden" name="sentinel_re_custom" value={state.sentinel_re_custom} />
      )}
      {state.condition_kind && (
        <input type="hidden" name="condition_kind" value={state.condition_kind} />
      )}
      {state.cond_pattern && (
        <input type="hidden" name="cond_pattern" value={state.cond_pattern} />
      )}
      {state.cond_criterion && (
        <input type="hidden" name="cond_criterion" value={state.cond_criterion} />
      )}
      {state.cond_shape_ttl && (
        <input type="hidden" name="cond_shape_ttl" value={state.cond_shape_ttl} />
      )}
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
  wiredSteps: WiredStep[]
  searchParams: Record<string, string | undefined>
  advanceAction: (fd: FormData) => Promise<void>
  saveAction: (fd: FormData) => Promise<void>
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const step = Math.max(1, Math.min(WIZARD_TOTAL, Number(searchParams.step ?? 1)))
  const state: WizardState = {
    event: (searchParams.event as EventKind) || undefined,
    archetype: (searchParams.archetype as Archetype) || undefined,
    matcher: searchParams.matcher || undefined,
    verifiers: ((): string[] | undefined => {
      const list = parseVerifierList(
        searchParams.verifiers ?? searchParams.verifier,
      )
      return list.length > 0 ? list : undefined
    })(),
    id: searchParams.id || undefined,
    description: searchParams.description || undefined,
    sentinel_mode: (searchParams.sentinel_mode === "custom" ? "custom" : "tag"),
    sentinel_tag: searchParams.sentinel_tag || undefined,
    sentinel_re_custom: searchParams.sentinel_re_custom || undefined,
    condition_kind: (CONDITION_KINDS as readonly string[]).includes(searchParams.condition_kind ?? "")
      ? (searchParams.condition_kind as ConditionKind)
      : undefined,
    cond_pattern: searchParams.cond_pattern || undefined,
    cond_criterion: searchParams.cond_criterion || undefined,
    cond_shape_ttl: searchParams.cond_shape_ttl || undefined,
  }

  return (
    <div className="max-w-2xl mx-auto">
      <WizardHeader t={t} step={step} total={WIZARD_TOTAL} state={state} />

      {step === 1 && <Step1Event t={t} state={state} action={advanceAction} />}
      {step === 2 && <Step2Archetype t={t} state={state} action={advanceAction} />}
      {step === 3 && <Step3Condition t={t} state={state} wiredSteps={wiredSteps} action={advanceAction} />}
      {step === 4 && <Step4Specifics t={t} state={state} action={advanceAction} />}
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
      <form action={action} className="space-y-4">
        <input type="hidden" name="_step" value="1" />
        {EVENT_GROUPS.map((group) => {
          // D36: groups with >2 events render in a 2-column grid so the
          // whole step fits one viewport. The 2-event tool-actions group
          // stays single-column so PreToolUse keeps its full-width
          // "recommended" emphasis.
          const dense = group.events.length > 2
          return (
            <section key={group.labelKey} className="space-y-1.5">
              <h3 className="text-[11px] uppercase tracking-[0.14em] font-semibold text-[var(--color-text-tertiary)]">
                {t(group.labelKey)}
              </h3>
              <div className={dense ? "grid grid-cols-2 gap-2" : "space-y-2"}>
                {group.events.map((ev) => (
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
              </div>
            </section>
          )
        })}
        <NextButton label={t("newPolicy.wizard.next")} />
      </form>
    </StepShell>
  )
}

// Step 2. What to do? Picks an archetype (block / ask / audit /
// emit-signal / strip) filtered by the event from Step 1.
function Step2Archetype({
  t, state, action,
}: {
  state: WizardState; action: (fd: FormData) => Promise<void>
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const event = state.event ?? "PreToolUse"
  const allowed = archetypesFor(event)
  const defaultPick: Archetype | undefined =
    state.archetype && allowed.includes(state.archetype)
      ? state.archetype
      : allowed[0]
  return (
    <StepShell
      t={t}
      step={2}
      prevHref={buildWizardHref(state, 1)}
      heading={t("newPolicy.wizard.step2.heading")}
      helper={t("newPolicy.wizard.step2.helper")}
    >
      <form action={action} className="space-y-3">
        <input type="hidden" name="_step" value="2" />
        <HiddenState state={{ event: state.event }} />
        {(() => {
          const excluded = ARCHETYPE_PRESETS.filter((a) => !allowed.includes(a) && a !== "strip")
          if (excluded.length === 0) return null
          return (
            <p className="text-[11px] text-[var(--color-text-tertiary)] -mt-1">
              {t("newPolicy.wizard.step2.excludedNote", {
                event,
                excluded: excluded.join(" / "),
              })}
            </p>
          )
        })()}
        {allowed.map((arc) => {
          const isStrip = arc === "strip"
          const stripDisabled = isStrip && !STRIP_AVAILABLE
          const sub = stripDisabled
            ? t("newPolicy.wizard.step2.strip.comingSoon")
            : t(`newPolicy.wizard.step2.archetype.${arc}.sub` as never)
          if (stripDisabled) {
            return (
              <label key={arc} className="block cursor-not-allowed opacity-60">
                <input type="radio" name="archetype" value={arc} disabled className="peer sr-only" />
                <span className="block rounded-xl border border-black/[0.08] bg-gray-50 p-4">
                  <span className="flex items-center justify-between gap-2 mb-1">
                    <span className="text-sm font-semibold text-[var(--color-text-primary)]">
                      {t(`newPolicy.wizard.step2.archetype.${arc}.label` as never)}
                    </span>
                    <Badge variant="info">coming soon</Badge>
                  </span>
                  <span className="block text-xs text-[var(--color-text-secondary)] leading-relaxed">{sub}</span>
                </span>
              </label>
            )
          }
          return (
            <RadioCard
              key={arc}
              name="archetype"
              value={arc}
              defaultChecked={defaultPick === arc}
              label={t(`newPolicy.wizard.step2.archetype.${arc}.label` as never)}
              sub={sub}
              recommended={arc === allowed[0] && arc !== "emit-signal"}
            />
          )
        })}
        <NextButton label={t("newPolicy.wizard.next")} />
      </form>
    </StepShell>
  )
}

// Step 3. Under what condition? Picks 1..N verifiers. Auto-skipped
// for emit-signal / strip via advanceWizard, so this only renders when
// the archetype actually has a condition.
function Step3Condition({
  t, state, wiredSteps, action,
}: {
  state: WizardState; wiredSteps: WiredStep[]
  action: (fd: FormData) => Promise<void>
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const event = state.event ?? "PreToolUse"
  const recommendedCategories = RECOMMENDED_CATEGORIES_BY_EVENT[event]
  const ordered = [...wiredSteps].sort((a, b) => {
    const ra = recommendedCategories.has(a.category) ? 0 : 1
    const rb = recommendedCategories.has(b.category) ? 0 : 1
    return ra - rb
  })
  const initialPicks: string[] = state.verifiers && state.verifiers.length > 0
    ? state.verifiers
    : ordered.length > 0 ? [ordered[0].step] : []
  return (
    <StepShell
      t={t}
      step={3}
      prevHref={buildWizardHref(state, 2)}
      heading={t("newPolicy.wizard.step3.heading")}
      helper={t("newPolicy.wizard.step3.helper")}
    >
      <form id="wizard-step3-form" action={action} className="space-y-3">
        <input type="hidden" name="_step" value="3" />
        <HiddenState state={{ event: state.event, archetype: state.archetype }} />
        <ConditionKindSection
          initialKind={state.condition_kind ?? "step"}
          wiredSteps={ordered.map(v => ({
            step: v.step,
            description: v.description,
            recommended: recommendedCategories.has(v.category),
          }))}
          initialPicks={initialPicks}
          initialPattern={state.cond_pattern ?? ""}
          initialCriterion={state.cond_criterion ?? ""}
          initialShapeTtl={state.cond_shape_ttl ?? ""}
          labels={{
            kindStep:          t("newPolicy.wizard.step3.kind.step"),
            kindRegex:         t("newPolicy.wizard.step3.kind.regex"),
            kindLlm:           t("newPolicy.wizard.step3.kind.llm"),
            kindShacl:         t("newPolicy.wizard.step3.kind.shacl"),
            pickAtLeastOne:    t("newPolicy.wizard.step3.minOneHint"),
            patternLabel:      t("newPolicy.wizard.step3.regex.label"),
            patternHint:       t("newPolicy.wizard.step3.regex.hint"),
            patternPlaceholder: t("newPolicy.wizard.step3.regex.placeholder"),
            patternInvalid:    t("newPolicy.wizard.step3.regex.invalid"),
            criterionLabel:    t("newPolicy.wizard.step3.llm.label"),
            criterionHint:     t("newPolicy.wizard.step3.llm.hint"),
            criterionPlaceholder: t("newPolicy.wizard.step3.llm.placeholder"),
            shaclLabel:        t("newPolicy.wizard.step3.shacl.label"),
            shaclHint:         t("newPolicy.wizard.step3.shacl.hint"),
            shaclPlaceholder:  t("newPolicy.wizard.step3.shacl.placeholder"),
            llmPreviewBadge:   t("newPolicy.wizard.step3.kind.llmPreview"),
            shaclPreviewBadge: t("newPolicy.wizard.step3.kind.shaclPreview"),
          }}
        />
        <NextButton label={t("newPolicy.wizard.next")} />
      </form>
    </StepShell>
  )
}

// Step 4. Specifics. Matcher input + sentinel_tag. The matcher chip
// palette and free-text default both narrow to options legal under
// (event × picked archetype's action). For no-tool events the matcher
// is locked to "*" and the user only edits sentinel_tag.
function Step4Specifics({
  t, state, action,
}: {
  state: WizardState; action: (fd: FormData) => Promise<void>
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const event = state.event ?? "PreToolUse"
  const archetype: Archetype = state.archetype ?? archetypesFor(event)[0]
  const irAction = archetypeToAction(archetype)
  const matcherCandidates = legalMatchersFor(event, irAction)
  const isNoToolEvent = !TOOL_CONTEXT_EVENTS.has(event)
  const matcherDefault = isNoToolEvent
    ? "*"
    : state.matcher && matcherCandidates.includes(state.matcher)
      ? state.matcher
      : matcherCandidates[0] ?? "Bash"
  return (
    <StepShell
      t={t}
      step={4}
      prevHref={buildWizardHref(state, archetypeSkipsCondition(archetype) ? 2 : 3)}
      heading={t("newPolicy.wizard.step4.heading")}
      helper={t("newPolicy.wizard.step4.helper")}
    >
      <form action={action} className="space-y-4">
        <input type="hidden" name="_step" value="4" />
        <HiddenState state={{
          event: state.event, archetype: state.archetype,
          verifiers: state.verifiers,
          // D35: condition kind picks made on Step 3 must ride through
          // Step 4 → 5 → 6, otherwise saveWizard re-validates with the
          // default kind=step (empty verifiers) and redirects back
          // to Step 3 with err=invalid_input.
          condition_kind: state.condition_kind,
          cond_pattern: state.cond_pattern,
          cond_criterion: state.cond_criterion,
          cond_shape_ttl: state.cond_shape_ttl,
        }} />
        {isNoToolEvent ? (
          <div className="rounded-xl border border-black/[0.08] bg-gray-50 px-4 py-3 text-sm text-[var(--color-text-secondary)]">
            <span className="font-mono">*</span>
            <span className="text-xs text-[var(--color-text-tertiary)] ml-2">
              {t("newPolicy.wizard.step4.matcherLocked")}
            </span>
            <input type="hidden" name="matcher" value="*" />
          </div>
        ) : (
          <>
            <input
              name="matcher"
              required
              maxLength={128}
              defaultValue={matcherDefault}
              list="matcher-list"
              placeholder="Bash"
              spellCheck={false}
              autoComplete="off"
              autoFocus
              className="w-full rounded-xl border border-black/[0.08] bg-white px-4 py-3 text-base leading-6 text-[var(--color-text-primary)] focus:border-[var(--color-accent)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]/20 font-mono"
            />
            <datalist id="matcher-list">
              {matcherCandidates.map((m) => <option key={m} value={m} />)}
            </datalist>
            <div className="flex flex-wrap gap-1.5">
              {matcherCandidates.map((m) => {
                const isWildcard = m === "*"
                return (
                  <button
                    key={m}
                    type="submit"
                    name="matcher"
                    value={m}
                    formAction={action}
                    formNoValidate
                    className={`rounded-full border px-3 py-1 text-xs font-mono cursor-pointer transition-colors ${
                      isWildcard
                        ? "border-amber-400/40 bg-amber-50 text-amber-900 hover:border-amber-500 hover:bg-amber-100"
                        : "border-black/[0.08] bg-white text-[var(--color-text-secondary)] hover:border-[var(--color-accent)]/40 hover:bg-[var(--color-accent)]/[0.04]"
                    }`}
                    title={isWildcard ? t("newPolicy.wizard.step4.wildcardHint") : undefined}
                  >
                    {m}
                  </button>
                )
              })}
            </div>
          </>
        )}
        {!isNoToolEvent && (
          <details className="group rounded-xl border border-black/[0.06] bg-gray-50/60 px-4 py-2">
            <summary className="cursor-pointer text-xs font-semibold uppercase tracking-[0.1em] text-[var(--color-text-tertiary)] py-2 select-none">
              {t("newPolicy.wizard.step4.otherPatterns.summary")}
            </summary>
            <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-2 text-xs py-3 border-t border-black/[0.04]">
              <dt className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold pt-0.5">MCP</dt>
              <dd className="text-[var(--color-text-secondary)]">
                {t("newPolicy.wizard.step4.otherPatterns.mcp")}{" "}
                <code className="font-mono px-1 py-0.5 rounded bg-white border border-black/[0.06]">mcp__server__tool</code>
              </dd>
              <dt className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold pt-0.5">Multi</dt>
              <dd className="text-[var(--color-text-secondary)]">
                {t("newPolicy.wizard.step4.otherPatterns.alt")}{" "}
                <code className="font-mono px-1 py-0.5 rounded bg-white border border-black/[0.06]">Bash|Edit|Write</code>
              </dd>
              <dt className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold pt-0.5">All</dt>
              <dd className="text-[var(--color-text-secondary)]">
                {t("newPolicy.wizard.step4.otherPatterns.wildcard")}{" "}
                <code className="font-mono px-1 py-0.5 rounded bg-white border border-black/[0.06]">*</code>
              </dd>
            </dl>
          </details>
        )}
        <SentinelModeSection
          initialMode={state.sentinel_mode ?? "tag"}
          initialTag={state.sentinel_tag ?? ""}
          initialCustom={state.sentinel_re_custom ?? ""}
          labels={{
            modeLabel:        t("newPolicy.wizard.step4.sentinel.modeLabel"),
            modeTag:          t("newPolicy.wizard.step4.sentinel.modeTag"),
            modeCustom:       t("newPolicy.wizard.step4.sentinel.modeCustom"),
            tagFieldLabel:    t("newPolicy.guided.field.sentinelTag"),
            tagFieldHint:     t("newPolicy.guided.field.sentinelTagHint"),
            tagPreviewIntro:  t("newPolicy.wizard.step4.sentinel.tagPreviewIntro"),
            customFieldLabel: t("newPolicy.wizard.step4.sentinel.customFieldLabel"),
            customFieldHint:  t("newPolicy.wizard.step4.sentinel.customFieldHint"),
            customGroupsHint: t("newPolicy.wizard.step4.sentinel.customGroupsMissing"),
          }}
        />
        <NextButton label={t("newPolicy.wizard.next")} />
      </form>
    </StepShell>
  )
}

function suggestPolicyId(state: WizardState): string {
  const archetype = state.archetype ?? "block"
  const event = state.event ?? "PreToolUse"
  const matcherSlug = (state.matcher || "any")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 24) || "any"
  return `${archetype}-${event.toLowerCase()}-${matcherSlug}/v1`
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
      step={5}
      prevHref={buildWizardHref(state, 4)}
      heading={t("newPolicy.wizard.step5.heading")}
      helper={t("newPolicy.wizard.step5.helper")}
    >
      <form action={action} className="space-y-4">
        <input type="hidden" name="_step" value="5" />
        <HiddenState state={{
          event: state.event, archetype: state.archetype,
          matcher: state.matcher, verifiers: state.verifiers,
          // D35: condition kind picks need to survive the Step 5 hop
          // (otherwise Step 6 saveWizard re-defaults to kind=step and
          // bounces back with err=invalid_input).
          condition_kind: state.condition_kind,
          cond_pattern: state.cond_pattern,
          cond_criterion: state.cond_criterion,
          cond_shape_ttl: state.cond_shape_ttl,
          sentinel_mode: state.sentinel_mode,
          sentinel_tag: state.sentinel_tag,
          sentinel_re_custom: state.sentinel_re_custom,
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
            defaultValue={idDefault}
            placeholder="legal-filing/v1"
            spellCheck={false}
            autoComplete="off"
            autoFocus
            className="w-full rounded-xl border border-black/[0.08] bg-white px-4 py-3 text-base leading-6 text-[var(--color-text-primary)] focus:border-[var(--color-accent)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]/20 font-mono"
          />
          <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
            {state.id
              ? t("newPolicy.guided.field.idHint")
              : t("newPolicy.wizard.step5.autoSuggested")}
          </p>
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
  wiredSteps: WiredStep[]
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const picked = state.verifiers ?? []
  const archetype: Archetype = state.archetype ?? "block"
  const isEmitSignal = archetype === "emit-signal"
  const verifierSummary = isEmitSignal
    ? "(unconditional)"
    : picked.length === 1
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
            action: archetype,
          })}
        </p>
        <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1.5 text-xs mt-4 pt-4 border-t border-black/[0.06]">
          <dt className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold">id</dt>
          <dd className="font-mono text-[12.5px]" translate="no">{state.id}</dd>
          <dt className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold">trigger</dt>
          <dd><code className="font-mono">{state.event} · {state.matcher}</code></dd>
          <dt className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold">archetype</dt>
          <dd className="text-[var(--color-text-secondary)]">{archetype}</dd>
          {!isEmitSignal && (
            <>
              <dt className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold">requires</dt>
              <dd>
                <ul className="space-y-1">
                  {picked.map((v) => {
                    const desc = wiredSteps.find((w) => w.step === v)?.description ?? ""
                    return (
                      <li key={v}>
                        <code className="font-mono">{v}=pass</code>{" "}
                        <span className="text-[var(--color-text-tertiary)]">,  {desc}</span>
                      </li>
                    )
                  })}
                </ul>
              </dd>
            </>
          )}
          <dt className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold">action (IR)</dt>
          <dd className="text-[var(--color-text-secondary)]">{archetypeToAction(archetype)}</dd>
          <dt className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold">sentinel_re</dt>
          <dd>
            <code className="font-mono text-[11.5px] break-all bg-gray-50 px-1.5 py-0.5 rounded border border-black/[0.06]">
              {state.sentinel_mode === "custom" && state.sentinel_re_custom
                ? state.sentinel_re_custom
                : buildSentinelReFromTag(state.sentinel_tag || SENTINEL_TAG_DEFAULT)}
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
              {draft.requires.map(r => {
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
