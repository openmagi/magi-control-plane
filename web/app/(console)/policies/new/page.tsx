import Link from "next/link"
import { revalidatePath } from "next/cache"
import { redirect } from "next/navigation"
import PayloadFieldChipsClient from "./_components/PayloadFieldChipsClient"
import SteeringAwareField from "./_components/SteeringAwareField"
import { XMarkIcon, ArrowLeftIcon, CodeBracketIcon, AdjustmentsHorizontalIcon, CheckIcon, ChatBubbleLeftRightIcon } from "@heroicons/react/24/outline"
import { VerifierFieldChecks } from "../../_components/VerifierFieldChecks"
import { verifierFiresOnLifecycle } from "@/lib/verifier-descriptors"
import { DryRunPanel } from "../_components/DryRunPanel"
import PolicyBuilder from "@/components/PolicyBuilder"
import ConversationalCompose from "./_components/ConversationalCompose"
import { codeForError, resolveFlash } from "@/lib/flash"
import { validatePolicyId } from "@/lib/policy-id"
import {
  validateDraft, type PolicyDraft,
} from "@/lib/policy-builder"
import { CloudConfigError, cloud } from "@/lib/cloud"
import {
  availableFields as payloadAvailableFields,
  lintShaclTargets as payloadLintShaclTargets,
  type FieldDescriptor as PayloadFieldDescriptor,
} from "@/lib/payload-schemas"
import type { SteerableConditionKind } from "@/lib/payload-steering"
import { getT } from "@/lib/i18n/server"
import {
  Badge, Card, ErrorState,
} from "@/components/ui"

export const dynamic = "force-dynamic"

type Mode = "guided" | "advanced" | "conversational"
const WIZARD_TOTAL = 6

/* ─────────────────────────────────────────────────────────────────────
 * New guided model (D41, expanded in D56c).
 *
 * Step 1  Lifecycle  one of the 8 CC hook events
 * Step 2  ConditionKind  (varies by lifecycle, see below)
 * Step 3  Specifics  per-kind form (auto-skip when kind=none)
 * Step 4  Action  block / ask / audit / strip  (lifecycle-filtered)
 * Step 5  Name  policy id + optional description
 * Step 6  Review  plain English + IR preview
 *
 * D56c: full 8-hook coverage. Lifecycle slugs map 1:1 to CC events:
 *
 *   before_tool_use  →  PreToolUse        (tool-context, recommended)
 *   after_tool_use   →  PostToolUse       (tool-context, audit-only)
 *   pre_final        →  Stop              (no-tool-context, audit-only)
 *   subagent_stop    →  SubagentStop      (no-tool-context, audit-only)
 *   user_prompt      →  UserPromptSubmit  (no-tool-context, block/ask/audit)
 *   pre_compact      →  PreCompact        (no-tool-context, block/audit)
 *   session_start    →  SessionStart      (no-tool-context, audit-only)
 *   session_end      →  SessionEnd        (no-tool-context, audit-only)
 *
 * Tool scope is only meaningful for the two tool-context lifecycles
 * (before_tool_use, after_tool_use); the other 6 auto-skip Step 2 and
 * use matcher="*". Action set is matrix-filtered (see ACTIONS_BY_
 * LIFECYCLE below + src/magi_cp/policy/matrix.py LEGAL_COMBINATIONS
 * which the cloud uses to validate on save).
 * ───────────────────────────────────────────────────────────────────── */

type Lifecycle =
  | "before_tool_use" | "after_tool_use" | "pre_final"
  | "subagent_stop"   | "user_prompt"    | "pre_compact"
  | "session_start"   | "session_end"
const LIFECYCLES: readonly Lifecycle[] = [
  "before_tool_use", "after_tool_use", "pre_final",
  "subagent_stop",   "user_prompt",    "pre_compact",
  "session_start",   "session_end",
]

const LIFECYCLE_TO_EVENT: Record<Lifecycle, string> = {
  before_tool_use: "PreToolUse",
  after_tool_use:  "PostToolUse",
  pre_final:       "Stop",
  subagent_stop:   "SubagentStop",
  user_prompt:     "UserPromptSubmit",
  pre_compact:     "PreCompact",
  session_start:   "SessionStart",
  session_end:     "SessionEnd",
}

// D56c: which lifecycles carry a tool context (Step 2 makes sense).
// Everything else auto-skips Step 2 and uses matcher="*".
const TOOL_CONTEXT_LIFECYCLES: ReadonlySet<Lifecycle> = new Set<Lifecycle>([
  "before_tool_use", "after_tool_use",
])

function lifecycleHasToolScope(life: Lifecycle | undefined): boolean {
  return life !== undefined && TOOL_CONTEXT_LIFECYCLES.has(life)
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
// D56c: 5 more no-tool-context lifecycles also skip Step 2. Their
// condition surface is matched to the runtime payload they carry:
//   user_prompt   → regex / llm_critic on the prompt string
//   pre_compact   → regex on the transcript window
//   subagent_stop → audit-style: regex / llm_critic on the child's
//                   transcript_path
//   session_*     → boundary marker; "none" is the meaningful default
//
// fetch_domain / domain_allowlist still surface as condition kinds but
// only when WebFetch is in the picked tool scope; they're convenience
// shortcuts that build a URL regex for you.
const CONDITION_KINDS_BY_LIFECYCLE: Record<Lifecycle, readonly ConditionKind[]> = {
  before_tool_use: ["none", "regex", "llm_critic", "fetch_domain", "domain_allowlist"],
  after_tool_use:  ["none", "regex", "llm_critic"],
  pre_final:       ["none", "evidence_ref", "regex", "shacl", "llm_critic"],
  subagent_stop:   ["none", "regex", "llm_critic"],
  user_prompt:     ["none", "regex", "llm_critic"],
  pre_compact:     ["none", "regex", "llm_critic"],
  session_start:   ["none"],
  session_end:     ["none"],
}

const ALL_CONDITION_KINDS: readonly ConditionKind[] = [
  "none", "regex", "llm_critic",
  "fetch_domain", "domain_allowlist",
  "evidence_ref", "shacl",
]

type Action = "block" | "ask" | "audit" | "strip"

// D56c: action set follows the matrix.py LEGAL_COMBINATIONS table.
//   before_tool_use → block / ask / audit (the runtime can refuse)
//   after_tool_use  → audit (tool already ran)
//   pre_final       → audit (Stop fires after the agent has chosen its
//                     final answer; the runtime cannot rewind.)
//   user_prompt     → block / ask / audit (prompt hasn't reached the LLM)
//   pre_compact     → block / audit (compaction hasn't fired yet)
//   subagent_stop / session_* → audit only (boundary markers)
const ACTIONS_BY_LIFECYCLE: Record<Lifecycle, readonly Action[]> = {
  before_tool_use: ["block", "ask", "audit"],
  after_tool_use:  ["audit"],
  pre_final:       ["audit"],
  subagent_stop:   ["audit"],
  user_prompt:     ["block", "ask", "audit"],
  pre_compact:     ["block", "audit"],
  session_start:   ["audit"],
  session_end:     ["audit"],
}

// D56d (P1 #1 + #2 fidelity follow-up): matrix.py LEGAL_COMBINATIONS
// constrains the action set per (event, matcher_class) — not just per
// event. The wizard's coarse ACTIONS_BY_LIFECYCLE keys off lifecycle
// alone, which passes block/ask through for (PreToolUse, wildcard) even
// though matrix.py only legalizes audit there. Similarly the wizard
// allowed tool_alt / wildcard matchers for after_tool_use even though
// matrix.py only legalizes single tool / mcp_tool for PostToolUse.
//
// We mirror the per-matcher narrowing here. Step 4 reads
// allowedActionsForCombination(lifecycle, toolScope) and Step 2 reads
// allowedMatcherClassesForLifecycle(lifecycle) so the matrix surface
// shows up at authoring time instead of as a generic 4xx flash on save.
//
// D56d (single-tool wizard): tool_alt (alternation matcher A|B|C) is
// retired from the wizard. One tool per policy. Multi-tool coverage is
// authored as separate policies, which keeps Step 3's payload-field
// suggestions per-tool and removes the matrix corner that PostToolUse
// alternation never legalized. The matcher class set is now exactly
// {tool, mcp_tool, wildcard}.
type MatcherClassKey = "tool" | "mcp_tool" | "wildcard"

function matcherClassForToolScope(scope: string | undefined): MatcherClassKey {
  const raw = (scope ?? "").trim()
  if (!raw || raw === "*") return "wildcard"
  // D56d single-tool: scope is a single tool name now. Any embedded
  // comma is a legacy URL or paste artifact; the canonical state-build
  // seam in GuidedWizard normalizes incoming `?toolScope=A,B` and IR
  // alternation matchers to their first entry before this helper sees
  // it, so the classifier here is honest: `A,B` only reaches us via a
  // direct caller that bypassed the normalization (e.g. server-action
  // bodies). saveWizard's early multi-input guard catches that path
  // explicitly so we still classify on the first parsed entry as a
  // defensive default.
  const first = parseCsv(raw)[0]?.trim() || raw
  if (first.startsWith("mcp__")) return "mcp_tool"
  return "tool"
}

// D56d follow-up (P1): true predicate for "more than one tool name
// arrived in the raw toolScope string." Used by saveWizard's early
// refusal so a stale CSV URL (`?toolScope=Bash,Edit`) that bypassed
// the GuidedWizard normalization seam cannot persist a single-tool
// matcher under a multi-tool display string.
function toolScopeIsMulti(scope: string | undefined): boolean {
  const raw = (scope ?? "").trim()
  if (!raw || raw === "*") return false
  return parseCsv(raw).length > 1
}

// Per (lifecycle, matcher_class) action allowlist. Mirror of
// matrix.LEGAL_COMBINATIONS in src/magi_cp/policy/matrix.py — adding a
// new event/matcher there must be reflected here too.
const ACTIONS_BY_COMBINATION: Record<
  Lifecycle, Record<MatcherClassKey, readonly Action[]>
> = {
  before_tool_use: {
    tool:     ["block", "ask", "audit"],
    mcp_tool: ["block", "ask", "audit"],
    wildcard: ["audit"],
  },
  after_tool_use: {
    tool:     ["audit"],
    mcp_tool: ["audit"],
    wildcard: [],
  },
  pre_final:     { tool: [], mcp_tool: [], wildcard: ["audit"] },
  subagent_stop: { tool: [], mcp_tool: [], wildcard: ["audit"] },
  user_prompt:   { tool: [], mcp_tool: [], wildcard: ["block", "ask", "audit"] },
  pre_compact:   { tool: [], mcp_tool: [], wildcard: ["block", "audit"] },
  session_start: { tool: [], mcp_tool: [], wildcard: ["audit"] },
  session_end:   { tool: [], mcp_tool: [], wildcard: ["audit"] },
}

function allowedActionsForCombination(
  lifecycle: Lifecycle, toolScope: string | undefined,
): readonly Action[] {
  const klass = matcherClassForToolScope(toolScope)
  const fromMatrix = ACTIONS_BY_COMBINATION[lifecycle][klass] ?? []
  // Intersect with lifecycle defaults so a future widening of one
  // table without the other never silently surfaces an action the
  // wizard cannot save.
  const lifeAllowed = new Set<Action>(ACTIONS_BY_LIFECYCLE[lifecycle])
  return fromMatrix.filter((a) => lifeAllowed.has(a))
}

// D56d: which matcher classes are matrix-legal for a given lifecycle.
// after_tool_use cannot use wildcard. Step 2 must refuse the "any
// tool" radio for that lifecycle. With single-tool authoring the
// alternation matcher is no longer reachable from the wizard.
function allowedMatcherClassesForLifecycle(
  lifecycle: Lifecycle,
): ReadonlySet<MatcherClassKey> {
  const out = new Set<MatcherClassKey>()
  for (const klass of ["tool", "mcp_tool", "wildcard"] as MatcherClassKey[]) {
    if ((ACTIONS_BY_COMBINATION[lifecycle][klass] ?? []).length > 0) {
      out.add(klass)
    }
  }
  return out
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
  // D56d (P2 #4): when _irToWizardState dropped a conditionKind
  // because the inbound lifecycle does not surface it, we carry the
  // dropped kind here so Step 3 can surface a "we dropped X" banner.
  // Read-only — not part of URL state.
  _droppedConditionKind?: ConditionKind
  // D56d follow-up (P1): when the inbound toolScope (from an IR
  // alternation matcher `A|B|C`, a legacy CSV URL, or a hand-edited
  // bookmark) carried more than one tool, we collapse to the first
  // entry and carry the original raw value here so Step 2 can surface
  // a one-shot "we trimmed your alternation" banner. Read-only.
  _droppedAlternation?: string
}

/* ─── IR + summary builders ───────────────────────────────────────── */

// D43 (issue #1, P1): sentinel_re is no longer required in core IR.
// The wizard previously auto-emitted a fake "GATE_(?P<subject>…)_(?P<payload_hash>…)"
// to satisfy a named-group requirement that PR1 removed. New policies
// are authored without sentinel_re. Raw mode still lets legacy / domain
// customers carry a sentinel pattern explicitly.

function deriveMatcher(s: WizardState): string {
  // D56c: only tool-context lifecycles (PreToolUse / PostToolUse) carry
  // a tool matcher. Every no-tool-context event (Stop, SubagentStop,
  // UserPromptSubmit, PreCompact, SessionStart, SessionEnd) is forced
  // to wildcard per the cloud's LEGAL_COMBINATIONS matrix.
  //
  // D56d (single-tool wizard): the wizard authors one tool per policy
  // now. Step 2's UI is a single-select radio; alternation is no
  // longer reachable from the wizard. Multi-tool coverage is authored
  // as separate policies, which keeps Step 3's payload-field
  // suggestions specific to the picked tool.
  if (!lifecycleHasToolScope(s.lifecycle)) return "*"
  const scope = (s.toolScope ?? "").trim()
  if (!scope || scope === "*") return "*"
  // Treat the first parsed entry as the single tool name. A legacy
  // CSV URL (from before this change) collapses to its first tool;
  // saveWizard's matcher-class guard catches the case where a stale
  // alternation matcher tries to land via the URL.
  const first = parseCsv(scope)[0]?.trim() || scope
  return first || "*"
}

function toolScopeIncludesWebFetch(s: WizardState): boolean {
  const scope = (s.toolScope ?? "").trim()
  if (!scope || scope === "*") return true
  const first = parseCsv(scope)[0]?.trim() || scope
  return first === "WebFetch"
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

/** D53b: best-effort draft IR builder for the Guided wizard's Step 6
 *  Dry-run panel. Mirrors the shape `saveWizard` writes (event,
 *  matcher, requires, action), without re-running the per-kind
 *  spec validation - we want the panel to surface the same 422 the
 *  save would, not pre-empt it.
 *
 *  Strip action is collapsed to `audit` here too (the backend has
 *  no payload-mutation channel today; saveWizard does the same).
 *  Description falls back to a plain summary so the cloud's
 *  Pydantic model has all the required keys.
 */
function buildGuidedDraftForDryRun(s: WizardState): Record<string, unknown> {
  const event = LIFECYCLE_TO_EVENT[s.lifecycle ?? "before_tool_use"]
  const matcher = deriveMatcher(s)
  const requires = deriveRequires(s)
  const action = s.action === "strip" ? "audit" : (s.action ?? "audit")
  return {
    id: s.id ?? "",
    description: s.description || summaryForBackend(s),
    version: "0.1",
    trigger: { host: "claude-code", event, matcher },
    sentinel_re: null,
    requires,
    action,
    on_signature_invalid: "deny",
    gate_binary: "/usr/local/bin/magi-gate.sh",
  }
}

// Step 4 dynamic header phrasing.
function actionHeaderEN(s: WizardState): string {
  if (s.conditionKind === "none") {
    // D56c: tone the header to the lifecycle when there is no per-call
    // check. The action runs every time the hook fires.
    switch (s.lifecycle) {
      case "user_prompt":   return "On every user prompt,"
      case "pre_compact":   return "Right before each context compaction,"
      case "subagent_stop": return "Each time a subagent finishes,"
      case "session_start": return "When the session opens,"
      case "session_end":   return "When the session closes,"
      case "pre_final":     return "When the agent has just finished its answer,"
      case "after_tool_use":return "On every matching tool call,"
      default:              return "On every matching tool call,"
    }
  }
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
  if (s.lifecycle === "user_prompt"     && s.conditionKind === "regex") return "When the user prompt matches,"
  if (s.lifecycle === "user_prompt"     && s.conditionKind === "llm_critic") return "When the LLM critic on the prompt returns NO,"
  if (s.lifecycle === "pre_compact"     && s.conditionKind === "regex") return "When the transcript window matches,"
  if (s.lifecycle === "pre_compact"     && s.conditionKind === "llm_critic") return "When the LLM critic on the transcript returns NO,"
  if (s.lifecycle === "subagent_stop"   && s.conditionKind === "regex") return "When the subagent transcript matches,"
  if (s.lifecycle === "subagent_stop"   && s.conditionKind === "llm_critic") return "When the LLM critic on the subagent transcript returns NO,"
  return "When the condition fires,"
}

function actionHeaderKO(s: WizardState): string {
  if (s.conditionKind === "none") {
    switch (s.lifecycle) {
      case "user_prompt":   return "유저 프롬프트가 도착할 때마다,"
      case "pre_compact":   return "컨텍스트 컴팩션 직전마다,"
      case "subagent_stop": return "서브에이전트가 끝날 때마다,"
      case "session_start": return "세션이 시작될 때,"
      case "session_end":   return "세션이 종료될 때,"
      case "pre_final":     return "에이전트가 최종 응답을 마쳤을 때,"
      case "after_tool_use":return "도구 호출이 끝날 때마다,"
      default:              return "조건에 매칭되는 도구 호출마다,"
    }
  }
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
  if (s.lifecycle === "user_prompt"     && s.conditionKind === "regex") return "유저 프롬프트가 패턴에 매칭될 때,"
  if (s.lifecycle === "user_prompt"     && s.conditionKind === "llm_critic") return "프롬프트에 대한 LLM critic이 NO를 반환할 때,"
  if (s.lifecycle === "pre_compact"     && s.conditionKind === "regex") return "컴팩션 대상 트랜스크립트가 패턴에 매칭될 때,"
  if (s.lifecycle === "pre_compact"     && s.conditionKind === "llm_critic") return "트랜스크립트에 대한 LLM critic이 NO를 반환할 때,"
  if (s.lifecycle === "subagent_stop"   && s.conditionKind === "regex") return "서브에이전트 트랜스크립트가 패턴에 매칭될 때,"
  if (s.lifecycle === "subagent_stop"   && s.conditionKind === "llm_critic") return "서브에이전트 트랜스크립트에 대한 LLM critic이 NO를 반환할 때,"
  return "조건이 발동할 때,"
}

// D56c: localized lifecycle labels for both languages, one place.
// D56d: pre_final label corrected to "agent turn ends" — CC's Stop
// hook fires AFTER the main agent has finished responding, not before.
const LIFECYCLE_LABEL_KO: Record<Lifecycle, string> = {
  before_tool_use: "도구 실행 전",
  after_tool_use:  "도구 실행 후",
  pre_final:       "에이전트 응답 직후",
  subagent_stop:   "서브에이전트 종료 시점",
  user_prompt:     "유저 프롬프트 직전",
  pre_compact:     "컨텍스트 컴팩션 직전",
  session_start:   "세션 시작 시점",
  session_end:     "세션 종료 시점",
}
const LIFECYCLE_LABEL_EN: Record<Lifecycle, string> = {
  before_tool_use: "before a tool runs",
  after_tool_use:  "after a tool runs",
  pre_final:       "after the agent finishes responding",
  subagent_stop:   "when a subagent stops",
  user_prompt:     "before a user prompt reaches the LLM",
  pre_compact:     "before context compaction",
  session_start:   "when the session opens",
  session_end:     "when the session closes",
}

function plainSummary(s: WizardState, locale: "ko" | "en"): string {
  const ko = locale === "ko"
  const header = ko ? actionHeaderKO(s) : actionHeaderEN(s)
  const act = s.action ?? "audit"
  const life = s.lifecycle ?? "before_tool_use"
  const lifeLabel = ko ? LIFECYCLE_LABEL_KO[life] : LIFECYCLE_LABEL_EN[life]
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
 * so browser back works as "previous step." Auto-skips Step 2 when the
 * lifecycle has no tool context (Stop, SubagentStop, UserPromptSubmit,
 * PreCompact, SessionStart, SessionEnd) so the matcher stays wildcard
 * per the cloud's LEGAL_COMBINATIONS matrix. */
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

  // Tool scope (Step 2). D56d single-tool: the chip row is a radio
  // group now (one builtin tool) plus an MCP free-text input (one
  // MCP tool). Mode radio (`any` / `specific`) picks which surface
  // applies; specific takes the typed MCP value first, otherwise the
  // builtin radio pick. MCP-wins matches the helper text on both
  // locales ("If both are set, the MCP name wins") and treats the
  // operator's most recent typing as the authoritative intent.
  // Multi-tool URLs collapse to the first entry on parse (matches
  // matcherClassForToolScope).
  const scopeMode = String(formData.get("toolScope_mode") ?? "")
  const scopeChip = String(formData.get("toolScope_chip") ?? "").trim()
  const scopeCustom = String(formData.get("toolScope_custom") ?? "").trim()
  const scopeSubmitted = stepIn === 2 && scopeMode !== ""
  if (scopeSubmitted) {
    if (scopeMode === "any") {
      params.set("toolScope", "*")
    } else if (scopeMode === "specific") {
      // P1 follow-up: typed MCP value wins so the helper text and
      // the runtime stay aligned. An operator who clicked a Bash
      // chip and then typed `mcp__court__file` lands on Step 3 with
      // the MCP tool, not Bash.
      const single = scopeCustom || scopeChip
      if (single) {
        params.set("toolScope", single)
      } else {
        // P2 follow-up (matrix gate, empty specific): the user
        // picked "specific" but left both inputs empty. Refuse the
        // advance and carry over every other URL param so wizard
        // progress (conditionKind, action, id, description, etc.) is
        // not dropped on the floor.
        for (const [k, v] of formData.entries()) {
          if (typeof v !== "string") continue
          if (k.startsWith("$ACTION") || k === "_step") continue
          if (k === "evidence_ref" || k === "evidence_refs") continue
          if (k === "toolScope_mode" || k === "toolScope_chip" || k === "toolScope_custom") continue
          if (k === "toolScope") continue
          if (!v.trim()) continue
          params.set(k, v.trim())
        }
        if (evMerged.length > 0) params.set("evidence_refs", evMerged.join(","))
        params.set("step", "2")
        params.set("err", "invalid_input")
        redirect(`/policies/new?${params.toString()}`); return
      }
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

  // D56c: every no-tool-context lifecycle auto-skips Step 2 (Stop,
  // SubagentStop, UserPromptSubmit, PreCompact, SessionStart, SessionEnd).
  // Only PreToolUse and PostToolUse carry a tool matcher.
  const lifecycle = params.get("lifecycle") as Lifecycle | null
  if (stepIn === 1 && lifecycle && !lifecycleHasToolScope(lifecycle)) {
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
  const lifecycleRaw = String(formData.get("lifecycle") ?? "before_tool_use")
  const lifecycle: Lifecycle = (LIFECYCLES as readonly string[]).includes(lifecycleRaw)
    ? (lifecycleRaw as Lifecycle) : "before_tool_use"
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
  let toolScope = toolScopeRaw || undefined

  // D56d follow-up (P1): refuse a multi-tool toolScope (`Bash,Edit`,
  // alternation `Bash|Edit`, etc.) BEFORE we slip into the matcher-
  // class classifier. The classifier collapses to first-token and
  // would otherwise silently persist a single-tool matcher under the
  // multi-tool display string. The GuidedWizard state-build seam
  // normalizes incoming URLs before render so this guard fires only
  // on a server-action body that bypassed normalization. Redirect
  // carries over every other state field so wizard progress is not
  // dropped.
  if (toolScope && (parseCsv(toolScope).length > 1 || toolScope.includes("|"))) {
    const carry = new URLSearchParams()
    carry.set("mode", "guided")
    carry.set("step", "2")
    carry.set("err", "invalid_input")
    for (const [k, v] of formData.entries()) {
      if (typeof v !== "string") continue
      if (k.startsWith("$ACTION") || k === "_step") continue
      if (k === "toolScope") continue
      if (k === "evidence_ref") continue
      if (!v.trim()) continue
      carry.set(k, v.trim())
    }
    redirect(`/policies/new?${carry.toString()}`); return
  }

  // D56d follow-up (P2): a wildcard-only lifecycle (pre_final,
  // subagent_stop, user_prompt, pre_compact, session_start,
  // session_end) does not surface a tool scope. A stale toolScope
  // param riding along in the URL (bookmark, copied draft prefill,
  // hand-edited link) would otherwise classify as `tool` / `mcp_tool`,
  // miss the wildcard-only matrix row, and force the operator back to
  // Step 2 even though deriveMatcher would have forced matcher='*'
  // anyway and the save was safe. Normalize toolScope against the
  // lifecycle before the matcher-class check, matching deriveMatcher's
  // existing behavior.
  if (!lifecycleHasToolScope(lifecycle)) {
    toolScope = undefined
  }

  // D56c+D56d: refuse matrix-illegal action choices before the round-
  // trip to the cloud. The cloud's policy.validate() enforces the same
  // table canonically (matrix.LEGAL_COMBINATIONS); this client-side
  // check just lands the error on the right step. D56d widens from a
  // per-lifecycle check to a per-(lifecycle, matcher_class) check so
  // (PreToolUse, wildcard, block) — matrix-illegal but lifecycle-legal
  // under the coarser table — gets caught here too.
  const allowedActions = allowedActionsForCombination(lifecycle, toolScope)
  if (!allowedActions.includes(action)) {
    redirect("/policies/new?mode=guided&step=4&err=invalid_input"); return
  }

  // D56d (P1 #2): after_tool_use only accepts single-tool / mcp_tool
  // matchers per matrix.py. Refuse wildcard / tool_alt here so the
  // operator lands back on Step 2 with the right correction hint
  // instead of a generic 4xx.
  const klass = matcherClassForToolScope(toolScope)
  const matrixMatchers = allowedMatcherClassesForLifecycle(lifecycle)
  if (!matrixMatchers.has(klass)) {
    redirect("/policies/new?mode=guided&step=2&err=invalid_input"); return
  }
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

function _parseDraftQuery(draft: string | undefined): PolicyDraft | null {
  if (!draft) return null
  try {
    const obj = JSON.parse(decodeURIComponent(draft))
    if (typeof obj !== "object" || !obj) return null
    return obj as PolicyDraft
  } catch { return null }
}

/** D56a: convert a PolicyDraft IR (the shape emitted by the cloud
 * for prebuilt templates, and what saveWizard would round-trip on
 * reload) into the WizardState shape the guided wizard renders. We
 * only consume the prebuilt's IR; anything we can't map cleanly we
 * leave undefined (the wizard's downstream code already tolerates
 * partial state and surfaces "—" placeholders on Step 6). */
function _irToWizardState(ir: PolicyDraft | null): WizardState | null {
  if (!ir) return null
  // event -> lifecycle. D56c: the wizard now covers all 8 CC hooks
  // so every prebuilt / advanced IR shape round-trips cleanly. Anything
  // outside the 8-event surface degrades to undefined and Step 1's
  // default (`before_tool_use`) takes over.
  let lifecycle: Lifecycle | undefined
  switch (ir.trigger?.event) {
    case "PreToolUse":       lifecycle = "before_tool_use"; break
    case "PostToolUse":      lifecycle = "after_tool_use";  break
    case "Stop":             lifecycle = "pre_final";       break
    case "SubagentStop":     lifecycle = "subagent_stop";   break
    case "UserPromptSubmit": lifecycle = "user_prompt";     break
    case "PreCompact":       lifecycle = "pre_compact";     break
    case "SessionStart":     lifecycle = "session_start";   break
    case "SessionEnd":       lifecycle = "session_end";     break
    default:                 lifecycle = undefined
  }

  // matcher -> toolScope. `*` (wildcard) is "any tool" which we
  // model as undefined; a bare tool name stays as-is. No-tool-context
  // lifecycles (pre_final + the 5 D56c additions) have no toolScope.
  //
  // D56d (single-tool wizard): the wizard authors one tool per
  // policy, so an inbound alternation matcher (legacy `A|B|C` shape
  // from a hand-authored IR) collapses to its first tool. The
  // operator finishes authoring on that tool and creates additional
  // policies for the rest. The original alternation string rides on
  // _droppedAlternation so Step 2 can surface a "we trimmed your
  // alternation" banner (P1 follow-up: silent trim was a data-loss
  // hazard; banner closes the gap).
  let toolScope: string | undefined
  let droppedAlternation: string | undefined
  const matcher = ir.trigger?.matcher ?? "*"
  if (lifecycleHasToolScope(lifecycle) && matcher && matcher !== "*") {
    if (matcher.includes("|")) {
      const parts = matcher.split("|").map((s) => s.trim()).filter(Boolean)
      toolScope = parts[0] ?? undefined
      if (parts.length > 1) droppedAlternation = matcher
    } else {
      toolScope = matcher
    }
  }

  // requires -> conditionKind + per-kind specifics. The wizard
  // models the requires list as a single conditionKind with one set
  // of inline specifics. The prebuilts all emit kind=step requires
  // pointing at a single verifier; we widen to also lift a leading
  // regex / llm_critic / shacl entry so a hand-authored draft from
  // /policies/new?mode=advanced also round-trips.
  const requires = ir.requires ?? []
  let conditionKind: ConditionKind = "none"
  let evidenceRefs: string[] | undefined
  let pattern: string | undefined
  let llmCriterion: string | undefined
  let shaclTtl: string | undefined
  if (requires.length === 0) {
    conditionKind = "none"
  } else {
    const stepRefs: string[] = []
    let sawNonStep = false
    for (const r of requires) {
      const k = "kind" in r ? r.kind : "step"
      if (k === "step" && "step" in r) {
        stepRefs.push(r.step)
      } else if (k === "regex" && "pattern" in r) {
        conditionKind = "regex"
        pattern = r.pattern
        sawNonStep = true
        break
      } else if (k === "llm_critic" && "criterion" in r) {
        conditionKind = "llm_critic"
        llmCriterion = r.criterion
        sawNonStep = true
        break
      } else if (k === "shacl" && "shape_ttl" in r) {
        conditionKind = "shacl"
        shaclTtl = r.shape_ttl
        sawNonStep = true
        break
      }
    }
    if (!sawNonStep && stepRefs.length > 0) {
      conditionKind = "evidence_ref"
      evidenceRefs = stepRefs
    }
  }

  const actionRaw = ir.action
  let action: Action | undefined =
    actionRaw === "block" || actionRaw === "ask" ||
    actionRaw === "audit" || actionRaw === "strip"
      ? actionRaw : undefined

  // D56d (P2 #3): if the inbound action is not legal for the mapped
  // lifecycle (e.g. prebuilt Stop+block round-tripping to pre_final),
  // drop to undefined so Step 4's defaultPick picks the matrix-legal
  // default instead. Without this Step 6's Dry-run panel would post
  // an illegal IR before the user ever visits Step 4.
  if (action && lifecycle && !ACTIONS_BY_LIFECYCLE[lifecycle].includes(action)) {
    action = undefined
  }

  // D56d (P2 #3): same normalization for conditionKind. If the
  // inbound IR carries a kind the wizard's CONDITION_KINDS_BY_LIFECYCLE
  // does not surface for the mapped lifecycle, drop to "none" so the
  // Step 3 picker default lands cleanly. We forward a flash hint via
  // searchParams (kindDropped) so Step 3 surfaces a banner — see
  // GuidedWizard for the wiring.
  let conditionKindDroppedFrom: ConditionKind | undefined
  if (lifecycle && conditionKind !== "none") {
    const kindsForLife = CONDITION_KINDS_BY_LIFECYCLE[lifecycle]
    if (!kindsForLife.includes(conditionKind)) {
      conditionKindDroppedFrom = conditionKind
      conditionKind = "none"
      // Wipe per-kind specifics so the dropped condition doesn't
      // leak its inputs into a different kind.
      pattern = undefined
      llmCriterion = undefined
      shaclTtl = undefined
      evidenceRefs = undefined
    }
  }

  // Drop the prebuilt's `prebuilt/...` slug as the suggested id so
  // the operator picks a fresh policy id on Step 5; description
  // copies through verbatim so the review summary reads well.
  const rawId = (ir.id ?? "").toString()
  const suggestedId = rawId.startsWith("prebuilt/")
    ? "" : rawId

  return {
    lifecycle,
    toolScope,
    conditionKind,
    pattern,
    llmCriterion,
    evidenceRefs,
    shaclTtl,
    action,
    id: suggestedId || undefined,
    description: ir.description?.toString() || undefined,
    _droppedConditionKind: conditionKindDroppedFrom,
    _droppedAlternation: droppedAlternation,
  } as WizardState
}

/* ─── page ────────────────────────────────────────────────────────── */

export default async function NewPolicyPage({
  searchParams,
}: { searchParams: Record<string, string | undefined> }) {
  const { t, locale } = await getT()
  const flash = resolveFlash(undefined, searchParams.err)

  const rawMode = searchParams.mode
  // D56b: NL compose mode retired. Conversational compose (D55) absorbs
  // its use case. When the user types a complete, unambiguous
  // description, the conversational compiler returns ready_to_save=true
  // on turn 1. URL backcompat: `/policies/new?mode=nl` lands users on
  // the new conversational page, preserving any incoming nl= seed so a
  // bookmarked `?mode=nl&nl=block+sudo` renders the seed in the input.
  if (rawMode === "nl") {
    const seed = searchParams.nl
    const tail = seed ? `&nl=${encodeURIComponent(seed)}` : ""
    redirect(`/policies/new?mode=conversational${tail}`)
  }
  // D56b hotfix: first-time visitors land on the PickerLanding which
  // shows Conversational (recommended) / Guided / Raw side by side.
  // The earlier auto-redirect to mode=conversational removed the user's
  // ability to pick Guided or Raw without typing a query param.
  // PickerLanding renders when rawMode is undefined; draft= prefill
  // still routes straight into the advanced editor below.
  type ResolvedMode = Mode | "picker"
  // D56b hotfix: unknown / undefined mode falls back to picker, not
  // conversational. Conversational is the recommended first option on
  // the picker, but the operator still gets Guided + Raw alongside.
  const mode: ResolvedMode =
    rawMode === "advanced" || (rawMode === undefined && searchParams.draft != null)
      ? "advanced"
      : rawMode === "guided"
        ? "guided"
        : rawMode === "conversational"
          ? "conversational"
          : "picker"

  const initialDraft =
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

      {mode === "picker" && <PickerLanding t={t} locale={locale === "ko" ? "ko" : "en"} />}

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
              dryRunSlot={({ draft, isValid }) => (
                <DryRunPanel
                  locale={locale}
                  ir={isValid ? (draft as unknown as Record<string, unknown>) : null}
                  disabled={!isValid}
                  action={(draft.action ?? "audit") as "block" | "ask" | "audit" | "strip"}
                />
              )}
            />
          </Card>
        </AuthoringShell>
      )}

      {mode === "conversational" && (
        <AuthoringShell
          t={t}
          modeTitle={t("newPolicy.mode.conversational")}
          info={{
            tone: "info",
            title: t("newPolicy.conv.info.title"),
            body: t("newPolicy.conv.info.body"),
          }}
        >
          {/* D55b: chat + live IR draft pane + dry-run on the same page.
              The save CTA at the bottom of the IrDraftPane posts to
              saveCompiled, the same server action the NL mode uses
              (writes via persistDraft + PUT /policies).

              D56b follow-up: when the page is reached via the
              ?mode=nl backcompat redirect, the legacy `nl=` query
              param is forwarded as the initial user message so a
              bookmarked NL seed actually prefills the conversational
              input instead of landing on an empty chat. */}
          <ConversationalCompose
            locale={locale === "ko" ? "ko" : "en"}
            saveAction={saveCompiled}
            initialUserMessage={searchParams.nl ?? ""}
          />
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
    // D56d (P1 #11): pre_final (Stop) is audit-only per matrix.py.
    // Previously this template hardcoded action=block, which the
    // client guard now refuses before the cloud round-trip — landing
    // the operator on Step 4 with no action selected. Two honest
    // options: audit the failure to the ledger here, or hard-block
    // by checking citations earlier (before_tool_use on WebFetch).
    // We keep the pre_final shape (its surface is "did the agent
    // satisfy a check before answering?") and demote to audit.
    ko: { title: "인용 감사", sub: "최종 응답이 citation 검증 통과 못하면 원장에 기록" },
    en: { title: "Audit citations", sub: "Record to the ledger when the final answer misses citation_verify" },
    params: {
      lifecycle: "pre_final", conditionKind: "evidence_ref",
      evidence_refs: "citation_verify", action: "audit",
    },
  },
  {
    id: "no-secret-in-answer",
    // D56d (P1 #11): same matrix constraint — pre_final is audit-only.
    // For a hard block on secret patterns, author at before_tool_use
    // on the tools that emit them (Bash, Edit, Write).
    ko: { title: "응답 시크릿 감사", sub: "최종 응답에 시크릿 패턴이 있으면 원장에 기록" },
    en: { title: "Audit secrets in answer", sub: "Record to the ledger when a final answer contains AKIA… patterns" },
    params: {
      lifecycle: "pre_final", conditionKind: "regex",
      pattern: "AKIA[A-Z0-9]+", action: "audit",
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

      {/* D56b: NL compose mode retired. Conversational is the default
          authoring path and lives first; Guided + Raw IR remain for
          power users. */}
      <div className="rounded-2xl border border-black/[0.08] bg-white p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] m-0 mb-1">
          {ko ? "직접 만들기" : "Build it yourself"}
        </h2>
        <p className="text-xs text-[var(--color-text-secondary)] mb-2">
          {t("newPolicy.picker.subtitle")}
        </p>
        {/* D55b: landing-copy nudge toward Conversational for first-time
            users. The brief explicitly asks for this. */}
        <p
          data-testid="picker-conversational-nudge"
          className="text-xs text-[var(--color-accent)] mb-4 font-medium"
        >
          {t("newPolicy.picker.conversationalNudge")}
        </p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <ChoiceCard
            href="/policies/new?mode=conversational"
            icon={<ChatBubbleLeftRightIcon className="h-5 w-5" />}
            label={t("newPolicy.picker.conversational.label")}
            description={t("newPolicy.picker.conversational.description")}
            backing={t("newPolicy.picker.conversational.backing")}
            testId="picker-card-conversational"
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
  href, icon, label, description, backing, testId,
}: {
  href: string
  icon: React.ReactNode
  label: string
  description: string
  backing: string
  testId?: string
}) {
  return (
    <Link
      href={href}
      data-testid={testId}
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

  // D56a: prebuilt "Use this" routes here with a `draft=<encoded IR>`
  // querystring. We decode it once into a PolicyDraft, project to a
  // WizardState, and use each field as a FALLBACK for the matching
  // URL param. URL params win (so Edit jumps from Step 6 -> earlier
  // step -> back round-trip cleanly without the draft re-overriding
  // the operator's edit). The HiddenState carry on each step then
  // re-serializes the merged state into the URL, so once Step 6
  // hands off control to another step the draft prefill is no
  // longer needed.
  const draftState = _irToWizardState(_parseDraftQuery(searchParams.draft))

  // D56d follow-up (P1): normalize toolScope at the state-build seam.
  // A stale CSV URL (`?toolScope=Bash,Edit`) or alternation matcher
  // riding through to this point collapses to its first parsed entry
  // so every downstream consumer (Step 6 display, suggestPolicyId,
  // payloadAvailableFields, deriveMatcher) sees the canonical single-
  // tool form. The original raw value is preserved on
  // _droppedAlternation so Step 2 can surface a "we trimmed your
  // alternation" banner mirroring _droppedConditionKind.
  const rawToolScope = searchParams.toolScope || draftState?.toolScope
  let normalizedToolScope: string | undefined = rawToolScope
  let droppedAlternationCarry: string | undefined =
    searchParams._droppedAlternation || draftState?._droppedAlternation
  if (rawToolScope && rawToolScope !== "*") {
    const trimmed = rawToolScope.trim()
    if (trimmed.includes(",") || trimmed.includes("|")) {
      const parts = trimmed.split(/[,|]/).map((s) => s.trim()).filter(Boolean)
      if (parts.length > 1) {
        normalizedToolScope = parts[0]
        droppedAlternationCarry = droppedAlternationCarry ?? trimmed
      } else if (parts.length === 1) {
        normalizedToolScope = parts[0]
      }
    }
  }

  const state: WizardState = {
    lifecycle: lifecycle ?? draftState?.lifecycle,
    toolScope: normalizedToolScope,
    conditionKind: conditionKind ?? draftState?.conditionKind,
    fetchDomain: searchParams.fetchDomain || draftState?.fetchDomain,
    allowlist: searchParams.allowlist || draftState?.allowlist,
    pattern: searchParams.pattern || draftState?.pattern,
    llmCriterion: searchParams.llmCriterion || draftState?.llmCriterion,
    evidenceRefs: evidenceRefs.length > 0 ? evidenceRefs : draftState?.evidenceRefs,
    shaclTtl: searchParams.shaclTtl || draftState?.shaclTtl,
    action: action ?? draftState?.action,
    id: searchParams.id || draftState?.id,
    description: searchParams.description || draftState?.description,
    // D56d (P2 #4): if the prebuilt draft carried a conditionKind that
    // does not survive the lifecycle (e.g. SessionStart + step ref),
    // surface a one-shot banner on Step 3 with the dropped kind name.
    // URL params take precedence as usual, but if neither URL nor the
    // user has navigated yet, the draft's dropped kind wins.
    _droppedConditionKind:
      !conditionKind && draftState?._droppedConditionKind
        ? draftState._droppedConditionKind : undefined,
    _droppedAlternation: droppedAlternationCarry,
  }

  // D56c: every no-tool-context lifecycle auto-skips Step 2 (tool
  // scope is irrelevant when matcher is forced to wildcard).
  const effectiveStep =
    step === 2 && state.lifecycle && !lifecycleHasToolScope(state.lifecycle)
      ? 3 : step

  return (
    <div className="max-w-2xl mx-auto">
      <WizardHeader t={t} step={effectiveStep} total={WIZARD_TOTAL} />

      {effectiveStep === 1 && <Step1Lifecycle t={t} locale={locale} state={state} action={advanceAction} />}
      {effectiveStep === 2 && <Step2ToolScope t={t} locale={locale} state={state} action={advanceAction} />}
      {effectiveStep === 3 && <Step3Condition t={t} locale={locale} state={state} wiredSteps={wiredSteps} action={advanceAction} />}
      {effectiveStep === 4 && <Step4Action t={t} locale={locale} state={state} action={advanceAction} />}
      {effectiveStep === 5 && <Step5Naming t={t} state={state} action={advanceAction} />}
      {effectiveStep === 6 && <Step6Review t={t} locale={locale} state={state} action={saveAction} advanceAction={advanceAction} wiredSteps={wiredSteps} />}
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

/** D52e follow-up: optional action-archetype tone matches the
 * NlAuthoringGuide pill colors so the vocabulary (block=red, ask=amber,
 * audit=blue, strip=purple) is consistent end-to-end. The accent color
 * still wins when the card is selected so the "this is the picked one"
 * affordance reads first. */
type ActionTone = "block" | "ask" | "audit" | "strip"

function actionCardClasses(tone?: ActionTone): string {
  // Idle border / hover hue per archetype. Selected state is still
  // owned by `peer-checked` (accent border + accent tint) below.
  switch (tone) {
    case "block":
      return "border-red-300 hover:border-red-400 peer-checked:border-[var(--color-accent)] peer-checked:bg-[var(--color-accent)]/[0.05]"
    case "ask":
      return "border-amber-300 hover:border-amber-400 peer-checked:border-[var(--color-accent)] peer-checked:bg-[var(--color-accent)]/[0.05]"
    case "audit":
      return "border-blue-300 hover:border-blue-400 peer-checked:border-[var(--color-accent)] peer-checked:bg-[var(--color-accent)]/[0.05]"
    case "strip":
      return "border-purple-300 hover:border-purple-400 peer-checked:border-[var(--color-accent)] peer-checked:bg-[var(--color-accent)]/[0.05]"
    default:
      return "border-black/[0.08] hover:border-[var(--color-accent)]/40 peer-checked:border-[var(--color-accent)] peer-checked:bg-[var(--color-accent)]/[0.05]"
  }
}

function RadioCard({
  name, value, defaultChecked, label, sub, badge, tone,
}: {
  name: string; value: string; defaultChecked?: boolean
  label: string; sub: string; badge?: { variant: "ok" | "info" | "muted"; text: string }
  tone?: ActionTone
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
      <span
        data-action-tone={tone}
        className={
          "block rounded-xl border bg-white p-4 transition-colors " +
          actionCardClasses(tone)
        }
      >
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

// D56c: per-lifecycle label + sub copy for both languages. We keep the
// label and helper local to the wizard (rather than threading 16 new
// i18n keys) so the future event additions land in one place. The 8
// lifecycle slugs match LIFECYCLE_TO_EVENT 1:1.
function lifecycleCardCopy(
  locale: "ko" | "en",
): Record<Lifecycle, { label: string; sub: string }> {
  return locale === "ko" ? {
    before_tool_use: {
      label: "도구 실행 전 (PreToolUse)",
      sub: "Bash, Edit, WebFetch 등 도구 호출이 실행되기 직전에 게이트가 발동합니다.",
    },
    after_tool_use: {
      label: "도구 실행 후 (PostToolUse)",
      sub: "도구가 결과를 돌려준 직후, 출력을 검사하거나 후속 동작을 정합니다.",
    },
    user_prompt: {
      label: "유저 프롬프트 직전 (UserPromptSubmit)",
      sub: "유저 프롬프트가 LLM 으로 가기 직전. PII / 특권 정보 누출을 차단합니다.",
    },
    pre_compact: {
      label: "컨텍스트 컴팩션 직전 (PreCompact)",
      sub: "컨텍스트 컴팩션 직전에 발동. evidence 체인 보존에 사용합니다.",
    },
    pre_final: {
      label: "에이전트 턴 종료 (Stop)",
      sub: "메인 에이전트 턴 종료 시점. 감사용으로만 사용합니다 (런타임은 차단 불가).",
    },
    subagent_stop: {
      label: "서브에이전트 종료 (SubagentStop)",
      sub: "서브에이전트(Task) 호출이 응답을 마쳤을 때 발동. 결과 트랜스크립트 감사 용도.",
    },
    session_start: {
      label: "세션 시작 (SessionStart)",
      sub: "세션이 시작·재개·초기화 될 때 발동. 감사 경계 마커로 사용합니다.",
    },
    session_end: {
      label: "세션 종료 (SessionEnd)",
      sub: "세션이 종료될 때 한 번 발동. 감사 경계 마커로 사용합니다.",
    },
  } : {
    before_tool_use: {
      label: "Before a tool runs (PreToolUse)",
      sub: "Fires right before a tool call (Bash, Edit, WebFetch, …) executes.",
    },
    after_tool_use: {
      label: "After a tool returns (PostToolUse)",
      sub: "Fires right after a tool returns; inspect or react to the output.",
    },
    user_prompt: {
      label: "Before a user prompt (UserPromptSubmit)",
      sub: "Right before a user prompt reaches the LLM. Catch PII or privileged content.",
    },
    pre_compact: {
      label: "Before context compaction (PreCompact)",
      sub: "Fires before the runtime compacts the transcript; preserve evidence chains.",
    },
    pre_final: {
      label: "When the agent stops (Stop)",
      sub: "Main agent turn ends. Audit-only (the runtime cannot rewind the answer).",
    },
    subagent_stop: {
      label: "When a subagent stops (SubagentStop)",
      sub: "Fires when a subagent task ends. Use it to audit child transcripts.",
    },
    session_start: {
      label: "When the session opens (SessionStart)",
      sub: "Fires on session startup, resume, or clear. Audit boundary marker.",
    },
    session_end: {
      label: "When the session closes (SessionEnd)",
      sub: "Fires once at session end. Audit boundary marker.",
    },
  }
}

// D56c: lifecycles grouped by family so the 8-card grid stays scannable.
// Group headers come from the dict (newPolicy.wizard.step1.group.*).
const LIFECYCLE_GROUPS: ReadonlyArray<{
  groupKey:
    | "newPolicy.wizard.step1.group.toolActions"
    | "newPolicy.wizard.step1.group.contentFlow"
    | "newPolicy.wizard.step1.group.boundaries"
  members: readonly Lifecycle[]
}> = [
  {
    groupKey: "newPolicy.wizard.step1.group.toolActions",
    members: ["before_tool_use", "after_tool_use"],
  },
  {
    groupKey: "newPolicy.wizard.step1.group.contentFlow",
    members: ["user_prompt", "pre_compact"],
  },
  // D56d (P2 #10): pre_final (Stop) moved into the audit-only group so
  // the group header honestly signals the action constraint. Operators
  // scanning groups for a hard-block hook will land on Tool actions
  // (PreToolUse / PostToolUse) or Content flow (UserPromptSubmit /
  // PreCompact) instead of being misled into a Stop+block save bounce.
  {
    groupKey: "newPolicy.wizard.step1.group.boundaries",
    members: ["pre_final", "subagent_stop", "session_start", "session_end"],
  },
]

function Step1Lifecycle({
  t, locale, state, action,
}: {
  state: WizardState; locale: "ko" | "en"
  action: (fd: FormData) => Promise<void>
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const current = state.lifecycle ?? "before_tool_use"
  const ko = locale === "ko"
  const labels = lifecycleCardCopy(locale)
  return (
    <StepShell
      t={t}
      prevHref={null}
      heading={t("newPolicy.wizard.step1.heading")}
      helper={t("newPolicy.wizard.step1.helper")}
    >
      <form action={action} className="space-y-5">
        <input type="hidden" name="_step" value="1" />
        {LIFECYCLE_GROUPS.map((group) => (
          <div key={group.groupKey} className="space-y-2">
            <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--color-text-tertiary)] m-0">
              {t(group.groupKey)}
            </p>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {group.members.map((life) => (
                <RadioCard
                  key={life}
                  name="lifecycle"
                  value={life}
                  defaultChecked={current === life}
                  label={labels[life].label}
                  sub={labels[life].sub}
                  badge={life === "before_tool_use"
                    ? { variant: "ok", text: ko ? "추천" : "recommended" }
                    : undefined}
                />
              ))}
            </div>
          </div>
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
  // D56d (single-tool wizard): scope is a single tool name. The
  // GuidedWizard state-build seam already collapsed any inbound CSV /
  // alternation to its first entry, so `state.toolScope` is canonical
  // here. Builtin pick = preset chip string; MCP pick = the free-text
  // input. The two are mutually exclusive. The form picks the typed
  // MCP value first in advanceWizard (matching the helper copy) so an
  // operator who typed into the MCP box wins over a stale chip.
  const rawScope = (state.toolScope ?? "").trim()
  const firstPick = rawScope
  const isAny = !rawScope || rawScope === "*"
  const builtinPick = (TOOL_PRESETS as readonly string[]).includes(firstPick)
    ? firstPick : ""
  const customStr = !isAny && !builtinPick ? firstPick : ""
  // D56d (P1 #2): after_tool_use only legalizes (PostToolUse, tool,
  // audit) and (PostToolUse, mcp_tool, audit). No wildcard. Step 2
  // refuses "Any tool" for that lifecycle. With single-tool authoring
  // the alternation matcher is no longer reachable from the wizard.
  const lifecycle = state.lifecycle ?? "before_tool_use"
  const matrixMatchers = allowedMatcherClassesForLifecycle(lifecycle)
  const wildcardLegal = matrixMatchers.has("wildcard")
  // The picked-tool helper hint reflects the URL-persisted previous
  // pick (server-rendered, no live form mirroring). Past-tense copy
  // so the operator understands this is the *current* persisted
  // value and a re-pick takes effect on submit.
  const helperTool = firstPick && firstPick !== "*" ? firstPick : ""
  // D56d follow-up (P1): if state-build seam dropped an alternation
  // matcher (multi-tool URL collapsed to first entry), surface a
  // one-shot banner so the operator sees the trim instead of guessing.
  const droppedAlternation = state._droppedAlternation
  return (
    <StepShell
      t={t}
      prevHref={buildWizardHref(state, 1)}
      heading={ko ? "어떤 도구에 적용할까요?" : "Which tool does this policy apply to?"}
      helper={ko
        ? "정책 한 건은 도구 하나만 다룹니다. 모든 도구에 적용하려면 'Any tool' 을 고르세요."
        : "Each policy targets exactly one tool. Pick Any tool to match every call."}
    >
      <form action={action} className="space-y-4">
        <input type="hidden" name="_step" value="2" />
        <HiddenState state={{ lifecycle: state.lifecycle }} />

        {droppedAlternation && (
          <p
            data-testid="step2-dropped-alternation-banner"
            className="rounded-xl border border-amber-300 bg-amber-50/60 px-3 py-2 text-xs text-amber-900"
          >
            {ko
              ? `원래 정책에 있던 멀티-도구 매처(${droppedAlternation})는 단일-도구 위저드에서 첫 번째 도구(${firstPick})로 축소되었습니다. 나머지 도구는 별도 정책으로 만드세요.`
              : `The original multi-tool matcher (${droppedAlternation}) was trimmed to the first tool (${firstPick}) by the single-tool wizard. Create separate policies for the rest.`}
          </p>
        )}

        <p
          data-testid="step2-single-tool-note"
          className="rounded-xl border border-blue-300 bg-blue-50/60 px-3 py-2 text-xs text-blue-900"
        >
          {ko
            ? "정책 한 건당 도구 하나입니다. 여러 도구에 같은 검사가 필요하면 정책을 도구별로 만드세요."
            : "One tool per policy. For multi-tool coverage, create separate policies."}
        </p>

        <label className={`block ${wildcardLegal ? "cursor-pointer" : "cursor-not-allowed opacity-60"}`}>
          <input
            type="radio"
            name="toolScope_mode"
            value="any"
            defaultChecked={wildcardLegal && isAny}
            disabled={!wildcardLegal}
            className="peer sr-only"
          />
          <span className="block rounded-xl border border-black/[0.08] bg-white p-4 transition-colors hover:border-[var(--color-accent)]/40 peer-checked:border-[var(--color-accent)] peer-checked:bg-[var(--color-accent)]/[0.05]">
            <span className="block text-sm font-semibold text-[var(--color-text-primary)]">
              {ko ? "모든 도구" : "Any tool"}
            </span>
            <span className="mt-1 block text-xs text-[var(--color-text-secondary)]">
              {wildcardLegal
                ? (ko ? "도구 종류 상관없이 모든 호출을 검사합니다 (wildcard matcher)." : "Match every tool call regardless of name (wildcard matcher).")
                : (ko ? "이 라이프사이클에서는 사용할 수 없습니다." : "Not available for this lifecycle.")}
            </span>
          </span>
        </label>

        <label className="block cursor-pointer">
          <input
            type="radio"
            name="toolScope_mode"
            value="specific"
            defaultChecked={(!wildcardLegal) || (!isAny && !!firstPick)}
            className="peer sr-only"
          />
          <span className="block rounded-xl border border-black/[0.08] bg-white p-4 transition-colors hover:border-[var(--color-accent)]/40 peer-checked:border-[var(--color-accent)] peer-checked:bg-[var(--color-accent)]/[0.05]">
            <span className="block text-sm font-semibold text-[var(--color-text-primary)]">
              {ko ? "특정 도구 하나" : "One specific tool"}
            </span>
            <span className="mt-1 block text-xs text-[var(--color-text-secondary)]">
              {ko ? "아래에서 빌트인 도구 하나, 또는 MCP 도구 하나를 입력하세요." : "Pick one builtin tool below, or type one MCP tool name."}
            </span>
          </span>
          <span className="mt-3 hidden peer-checked:block space-y-3">
            <div
              role="radiogroup"
              aria-label={ko ? "빌트인 도구" : "Builtin tool"}
              className="grid grid-cols-2 gap-2 sm:grid-cols-3"
            >
              {TOOL_PRESETS.map((tool) => (
                <label key={tool} className="block cursor-pointer">
                  <input
                    type="radio"
                    name="toolScope_chip"
                    value={tool}
                    defaultChecked={builtinPick === tool}
                    className="peer sr-only"
                  />
                  <span className="block rounded-lg border border-black/[0.08] bg-white px-3 py-2 text-center text-sm font-mono text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-accent)]/40 peer-checked:border-[var(--color-accent)] peer-checked:bg-[var(--color-accent)]/[0.06] peer-checked:text-[var(--color-text-primary)]">
                    {tool}
                  </span>
                </label>
              ))}
            </div>
            <div>
              <FieldLabel>
                {ko ? "또는 MCP 도구 하나 (mcp__server__name)" : "Or one MCP tool (mcp__server__name)"}
              </FieldLabel>
              <input
                name="toolScope_custom"
                maxLength={256}
                defaultValue={customStr}
                placeholder="mcp__court__file"
                spellCheck={false}
                autoComplete="off"
                pattern="(mcp__[A-Za-z0-9_]+__[A-Za-z0-9_]+|[A-Za-z][A-Za-z0-9_]*)"
                title={ko
                  ? "MCP 도구는 mcp__server__name 형식. 빈 값이면 위에서 선택한 빌트인 도구가 사용됩니다."
                  : "MCP tool follows mcp__server__name. Leave empty to use the builtin pick above."}
                className={inputCls() + " font-mono text-sm"}
              />
              <p className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">
                {ko
                  ? "빌트인 도구를 골랐다면 비워두세요. 두 칸 다 채워지면 MCP 도구 이름이 이깁니다."
                  : "Leave empty when you picked a builtin chip. If both are set, the MCP name wins."}
              </p>
            </div>
            {helperTool && (
              <p
                data-testid="step2-tool-helper"
                className="rounded-lg border border-[var(--color-accent)]/30 bg-[var(--color-accent)]/[0.04] px-3 py-2 text-xs text-[var(--color-text-secondary)]"
              >
                {ko
                  ? `현재 ${helperTool} 로 저장되어 있습니다. Step 3 는 이 도구의 페이로드에 맞춘 검사 옵션을 보여줍니다. 위에서 다른 도구를 고르고 Next 를 누르면 갱신됩니다.`
                  : `Currently saved as ${helperTool}. Step 3 will suggest checks specific to this tool; pick a different one above and submit to refresh.`}
              </p>
            )}
          </span>
        </label>

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
  const ccEvent = LIFECYCLE_TO_EVENT[lifecycle]
  const ccMatcher = lifecycleHasToolScope(lifecycle) ? state.toolScope : undefined
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
  // D56c: every no-tool-context lifecycle skips Step 2, so the
  // "Back" link from Step 3 jumps to Step 1.
  const prevStep = lifecycleHasToolScope(state.lifecycle) ? 2 : 1

  // P9 (D49): the per-kind cumulative-judgment tip lives in a client
  // island (SteeringAwareField). It needs two same-page hrefs as a
  // starting point — the island then splices live in-flight text in.
  const { switchHref: baseSwitchHref, switchPreFinalHref: baseSwitchPreFinalHref }
    = steeringBaseHrefs(state)
  const evidenceAllowed = (CONDITION_KINDS_BY_LIFECYCLE[lifecycle] as readonly ConditionKind[])
    .includes("evidence_ref")
  const wizardSnap = steeringSnapshot(state)
  const fieldInputCls = inputCls()

  // D56d (P2 #4): if _irToWizardState dropped a conditionKind because
  // the inbound lifecycle does not surface it, surface a one-shot
  // banner explaining the drop. Operator may have loaded a prebuilt
  // that crosses lifecycles.
  const droppedKind = state._droppedConditionKind
  const lifecycleLabel = ko
    ? LIFECYCLE_LABEL_KO[lifecycle]
    : LIFECYCLE_LABEL_EN[lifecycle]

  return (
    <StepShell
      t={t}
      prevHref={buildWizardHref(state, prevStep)}
      heading={ko ? "어떤 조건일 때 검사하나요?" : "Under what condition?"}
      helper={ko
        ? "조건을 고르면 바로 아래에 기준 입력 칸이 열립니다."
        : "Pick a condition and the criteria input opens right below."}
    >
      {droppedKind && (
        <div
          data-testid="step3-dropped-kind-banner"
          className="rounded-xl border border-amber-300 bg-amber-50/60 px-3 py-2 text-xs text-amber-900"
        >
          {ko
            ? `원래 정책에 있던 ${droppedKind} 조건은 ${lifecycleLabel} 라이프사이클에 적용되지 않아 제거되었습니다.`
            : `The original policy carried a ${droppedKind} requirement that does not apply ${lifecycleLabel}; it has been dropped.`}
        </div>
      )}
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
                    {/* D57e: filter wiredSteps to only those whose
                        descriptor declares a field_checks group for
                        the current lifecycle. A Stop-lifecycle wizard
                        does not show source_allowlist (PreToolUse
                        only); a PreToolUse-lifecycle wizard does not
                        show citation_verify (Stop only). Verifiers
                        with no registered descriptor degrade to
                        "show" so the picker does not silently drop
                        a wired preset the descriptor mirror has not
                        been updated for. */}
                    {(() => {
                      const filtered = wiredSteps.filter((w) =>
                        verifierFiresOnLifecycle(w.step, ccEvent),
                      )
                      const droppedCount = wiredSteps.length - filtered.length
                      return (
                        <>
                          {wiredSteps.length === 0 && (
                            <p className="text-xs text-amber-700">
                              {ko
                                ? "연결된 verifier가 없습니다. 먼저 /presets에서 verifier를 enable 하세요."
                                : "No wired verifiers yet. Enable one under /presets first."}
                            </p>
                          )}
                          {wiredSteps.length > 0 && filtered.length === 0 && (
                            <p
                              data-testid="step3-verifier-picker-no-lifecycle-match"
                              className="text-xs text-amber-700"
                            >
                              {ko
                                ? `${lifecycleLabel} 라이프사이클에서 발동하는 verifier 가 없습니다. 다른 라이프사이클을 고르거나 새 verifier 를 enable 하세요.`
                                : `No wired verifier fires on the ${lifecycleLabel} lifecycle. Pick a different lifecycle or enable a verifier that does.`}
                            </p>
                          )}
                          {droppedCount > 0 && filtered.length > 0 && (
                            <p
                              data-testid="step3-verifier-picker-dropped-note"
                              className="text-[11px] italic text-[var(--color-text-tertiary)]"
                            >
                              {ko
                                ? `${droppedCount} 개의 verifier 가 이 라이프사이클에서 발동하지 않아 숨김 처리되었습니다.`
                                : `${droppedCount} verifier(s) hidden because they do not fire on this lifecycle.`}
                            </p>
                          )}
                        </>
                      )
                    })()}
                    <div className="space-y-2">
                      {wiredSteps
                        .filter((w) => verifierFiresOnLifecycle(w.step, ccEvent))
                        .map((w) => {
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
                                lifecycle={ccEvent}
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
  // D56d (P1 #1): allowed actions follow (lifecycle, matcher_class).
  // For wildcard toolScope on before_tool_use that narrows from
  // [block, ask, audit] down to [audit] — surfacing the matrix
  // constraint at authoring time instead of as a save-time 4xx.
  // Fall back to the lifecycle default when the combination has no
  // entry (Step 2 will catch the invalid matcher first; this keeps
  // the action card render non-empty so the operator can still see
  // what's allowed if they navigate back here directly).
  const combinationAllowed = allowedActionsForCombination(lifecycle, state.toolScope)
  const allowed = combinationAllowed.length > 0
    ? combinationAllowed
    : ACTIONS_BY_LIFECYCLE[lifecycle]
  const defaultPick: Action = state.action && allowed.includes(state.action)
    ? state.action : allowed[0]
  const ko = locale === "ko"
  const header = ko ? actionHeaderKO(state) : actionHeaderEN(state)
  // D56d (P2 #5): "recommended" badge only renders when block is
  // actually in the legal action set for the current combination.
  const blockLegal = allowed.includes("block")
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
                <span data-action-tone="strip" className="block rounded-xl border border-purple-300 bg-purple-50/40 p-4">
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
              tone={a}
              badge={a === "block" && lifecycle === "before_tool_use" && blockLegal ? { variant: "ok", text: ko ? "추천" : "recommended" } : undefined}
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
  // D56d follow-up (P2): slugify the canonical matcher (single tool /
  // mcp tool / wildcard) instead of the raw state.toolScope so a
  // stale CSV URL (`Bash,Edit`) does not bake a multi-tool slug into
  // a single-tool policy id. GuidedWizard already normalizes
  // state.toolScope at the state-build seam, so deriveMatcher returns
  // the canonical form here too.
  //
  // D57d: append the chosen action archetype as a third segment so
  // the auto-suggested id reflects WHAT the policy does, not just
  // WHEN + WHICH TOOL. Format:
  //   {lifecycle-kebab}-{toolScope-kebab-or-any}-{action}/v1
  // - When the matcher is wildcard, the tool segment is skipped so a
  //   no-tool-context lifecycle reads cleanly (e.g. `stop-block/v1`).
  // - When action is undefined (still on Step 4 or earlier), the
  //   action segment is skipped which keeps back-compat with pre-D57d
  //   auto-suggestions.
  const matcher = deriveMatcher(state)
  const action = state.action
  let toolPart = ""
  if (matcher && matcher !== "*") {
    toolPart = matcher.toLowerCase().replace(/[^a-z0-9]+/g, "-")
  } else if (!action) {
    // Pre-D57d back-compat: when no action is picked yet, fall back to
    // fetchDomain / conditionKind so the suggestion still differentiates
    // wildcard policies. Once the operator picks an action, the action
    // segment carries that signal and the tool segment can be skipped.
    if (state.fetchDomain) {
      toolPart = state.fetchDomain.toLowerCase().replace(/[^a-z0-9]+/g, "-")
    } else if (state.conditionKind) {
      // Kebab-case the conditionKind so internal underscored tokens
      // (e.g. `llm_critic`, `fetch_domain`) don't leak as-is into the
      // auto-suggested id. The id field is an end-user surface; the
      // operator can still rewrite it. AGENTS.md forbids surfacing
      // internal terms verbatim, so this stays as a slug fallback only
      // when no domain is set and no action has been chosen yet.
      toolPart = state.conditionKind.replace(/_/g, "-")
    } else {
      toolPart = "any"
    }
  }
  // D57d follow-up: slice BEFORE stripping leading/trailing dashes so
  // the 24-char cap doesn't reintroduce a trailing `-` (which would
  // join into a `--` artifact against the action segment).
  const toolCleaned = toolPart.slice(0, 24).replace(/^-+|-+$/g, "").replace(/-+/g, "-")
  const segments = [lifeSlug, toolCleaned, action ?? ""].filter(Boolean)
  return `${segments.join("-")}/v1`
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

/** D56a: per-row "Edit" affordance on Step 6. Jumps to the step that
 *  owns the field (1=lifecycle, 2=tool scope, 3=condition, 4=action,
 *  5=name) with the full WizardState carried in the URL. Round-trip
 *  is preserved because `buildWizardHref` writes every populated
 *  field, and each earlier step's HiddenState re-emits the carry on
 *  its way back to Step 6. */
function EditLink({
  t, state, step,
}: {
  state: WizardState; step: number
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  return (
    <Link
      href={buildWizardHref(state, step)}
      className="inline-flex items-center rounded-md border border-black/[0.08] bg-white px-2 py-0.5 text-[10.5px] font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)] hover:border-[var(--color-accent)]/40 hover:text-[var(--color-accent)] hover:no-underline"
    >
      {t("newPolicy.wizard.step6.editField")}
    </Link>
  )
}

/** D56a: small expandable inline editor for sub-config that lives
 *  inside the IR but isn't a wizard step (regex pattern, allowlist
 *  CSV, llm_critic prompt, SHACL ttl, fetch_domain). The form posts
 *  back to advanceWizard with `_step=6` plus a hidden `_intent` so
 *  the action recognizes a Step-6 inline edit. We piggy-back on the
 *  existing advanceWizard URL-merging code path: it already drops
 *  every form field into URL params (HiddenState carries the rest),
 *  so any sub-config name we name-collide with the wizard's URL
 *  contract (`pattern`, `allowlist`, `llmCriterion`, `shaclTtl`,
 *  `fetchDomain`) just round-trips back into the wizard state. We
 *  push `_step=5` so the advance bumps to step 6 — i.e. the user
 *  lands back on Step 6 after the inline save. */
function InlineSubConfigPanel({
  t, locale, state, advanceAction,
}: {
  state: WizardState; locale: "ko" | "en"
  advanceAction: (fd: FormData) => Promise<void>
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const ko = locale === "ko"
  const kind = state.conditionKind ?? "none"
  if (kind === "none" || kind === "evidence_ref") return null

  // Pick the right field name + label + control per kind. Stays in
  // sync with Step 3's specifics block (deriveRequires reads the
  // same field names). NOT a textarea by default — these are
  // typically short strings; the SHACL ttl is the exception.
  let label: string
  let helper: string
  let element: "input" | "textarea"
  let name: string
  let initial: string
  let placeholder: string
  let useChips = false
  let chipVariant: "path" | "shacl-stub" = "path"
  let textareaId = "w-step6-sub-config"

  switch (kind) {
    case "regex":
      label = ko ? "정규식 패턴" : "Regex pattern"
      helper = ko ? "Python `re` 문법. 비우면 condition 이 만족 안 됨." : "Python `re` syntax. Empty pattern means no condition."
      element = "input"
      name = "pattern"
      initial = state.pattern ?? ""
      placeholder = "AKIA[A-Z0-9]{16}"
      useChips = true
      textareaId = "w-step6-pattern"
      break
    case "llm_critic":
      label = ko ? "LLM critic 기준" : "LLM critic criterion"
      helper = ko ? "자연어 기준. LLM 이 NO 를 반환하면 발동." : "Plain-English criterion. The condition fires when the LLM answers NO."
      element = "textarea"
      name = "llmCriterion"
      initial = state.llmCriterion ?? ""
      placeholder = ko
        ? "예: 출력에 사용자가 묻지 않은 추측이 포함되어 있는가?"
        : "e.g. Does the output contain a guess the user did not ask for?"
      useChips = true
      textareaId = "w-step6-llm"
      break
    case "shacl":
      label = "SHACL shape (Turtle)"
      helper = ko
        ? "magi: 네임스페이스에 anchor 되어야 vacuous-satisfaction 을 피합니다."
        : "Anchor on the magi: namespace so the shape can't be vacuously satisfied."
      element = "textarea"
      name = "shaclTtl"
      initial = state.shaclTtl ?? ""
      placeholder = "@prefix sh:   <http://www.w3.org/ns/shacl#> .\n@prefix magi: <https://magi.openmagi.ai/cc/hook#> .\n…"
      useChips = true
      chipVariant = "shacl-stub"
      textareaId = "w-step6-shacl"
      break
    case "fetch_domain":
      label = ko ? "Fetch 도메인" : "Fetch domain"
      helper = ko ? "WebFetch 가 이 도메인에 접근할 때 발동." : "Fires when WebFetch hits this exact domain."
      element = "input"
      name = "fetchDomain"
      initial = state.fetchDomain ?? ""
      placeholder = "example.com"
      textareaId = "w-step6-fetch"
      break
    case "domain_allowlist":
      label = ko ? "허용 도메인 (쉼표 구분)" : "Allowed domains (comma-separated)"
      helper = ko ? "이 목록에 없는 도메인 접근은 condition 이 만족 안 됨." : "A fetch outside this list does not satisfy the condition."
      element = "input"
      name = "allowlist"
      initial = state.allowlist ?? ""
      placeholder = "api.openai.com, github.com, npmjs.com"
      textareaId = "w-step6-allow"
      break
    default:
      return null
  }

  // Per-event payload chip context (matches Step 3's wiring).
  const lifecycle = state.lifecycle ?? "before_tool_use"
  const ccEvent = LIFECYCLE_TO_EVENT[lifecycle]
  const ccMatcher = lifecycleHasToolScope(lifecycle) ? state.toolScope : undefined
  const fields = useChips ? payloadAvailableFields(ccEvent, ccMatcher) : []

  return (
    <details
      data-testid="step6-subconfig-editor"
      className="mt-2 rounded-lg border border-black/[0.08] bg-[var(--color-surface-1,#f9fafb)]/40"
    >
      <summary className="cursor-pointer list-none px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)] hover:text-[var(--color-accent)]">
        {t("newPolicy.wizard.step6.editSubConfig.summary", { field: label })}
      </summary>
      <form action={advanceAction} className="space-y-2 px-3 py-3">
        {/* _step=5 -> advanceWizard nudges nextStep to 6, so the
            operator lands back on Step 6 after the inline save. */}
        <input type="hidden" name="_step" value="5" />
        <HiddenState
          state={{
            ...state,
            // Clear the field we're about to edit so the freshly
            // submitted value wins. The rest of the wizard state
            // (lifecycle / toolScope / conditionKind / action / id /
            // …) round-trips intact.
            pattern: name === "pattern" ? undefined : state.pattern,
            allowlist: name === "allowlist" ? undefined : state.allowlist,
            llmCriterion: name === "llmCriterion" ? undefined : state.llmCriterion,
            shaclTtl: name === "shaclTtl" ? undefined : state.shaclTtl,
            fetchDomain: name === "fetchDomain" ? undefined : state.fetchDomain,
          }}
        />
        <FieldLabel>{label}</FieldLabel>
        <p className="text-[11px] text-[var(--color-text-tertiary)] m-0">{helper}</p>
        {useChips && fields.length > 0 && (
          <PayloadFieldChips
            fields={fields}
            locale={locale}
            targetTextareaId={textareaId}
            variant={chipVariant}
          />
        )}
        {element === "input" ? (
          <input
            id={textareaId}
            name={name}
            defaultValue={initial}
            placeholder={placeholder}
            spellCheck={false}
            autoComplete="off"
            maxLength={2000}
            className={inputCls() + " font-mono text-sm"}
          />
        ) : (
          <textarea
            id={textareaId}
            name={name}
            defaultValue={initial}
            placeholder={placeholder}
            spellCheck={false}
            autoComplete="off"
            rows={name === "shaclTtl" ? 8 : 3}
            className={inputCls() + " font-mono text-sm leading-relaxed"}
          />
        )}
        <button
          type="submit"
          className="inline-flex items-center justify-center rounded-lg bg-[var(--color-accent)] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[var(--color-accent-hover)] cursor-pointer transition-colors"
        >
          {t("newPolicy.wizard.step6.editSubConfig.save")}
        </button>
      </form>
    </details>
  )
}

function Step6Review({
  t, locale, state, action, advanceAction, wiredSteps,
}: {
  state: WizardState
  locale: "ko" | "en"
  action: (fd: FormData) => Promise<void>
  advanceAction: (fd: FormData) => Promise<void>
  wiredSteps: WiredStep[]
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const ko = locale === "ko"
  const event = LIFECYCLE_TO_EVENT[state.lifecycle ?? "before_tool_use"]
  const matcher = deriveMatcher(state)
  const requires = deriveRequires(state)
  const summary = plainSummary(state, locale)
  const evidenceList = state.evidenceRefs ?? []
  // D56a: Step 6 surfaces per-row Edit affordances jumping back to
  // the step that owns the field. We render the rows ourselves so
  // each row gets its own Edit button + (for sub-config that lives
  // INSIDE the IR but isn't a wizard step) an inline editor.
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
        <ul className="m-0 mt-4 list-none p-0 space-y-2 border-t border-black/[0.06] pt-4 text-xs">
          {/* Name row → Step 5 */}
          <li data-testid="step6-row-name" className="grid grid-cols-[max-content_1fr_max-content] items-start gap-x-3">
            <span className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold pt-0.5">{ko ? "이름" : "name"}</span>
            <span className="font-mono text-[12.5px]" translate="no">
              {state.id ?? <em className="text-[var(--color-text-tertiary)] not-italic">{ko ? "(아직 미정)" : "(not set yet)"}</em>}
            </span>
            <EditLink t={t} state={state} step={5} />
          </li>

          {/* Lifecycle row → Step 1 */}
          <li data-testid="step6-row-lifecycle" className="grid grid-cols-[max-content_1fr_max-content] items-start gap-x-3">
            <span className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold pt-0.5">{ko ? "시점" : "lifecycle"}</span>
            <span className="text-[var(--color-text-secondary)]">
              {state.lifecycle ?? <em className="text-[var(--color-text-tertiary)] not-italic">—</em>}
              <span className="ml-2 font-mono text-[11px] text-[var(--color-text-tertiary)]">{event}</span>
            </span>
            <EditLink t={t} state={state} step={1} />
          </li>

          {/* Tool scope row → Step 2 (only when the lifecycle carries
              a tool context. D56c broadened from `!== "pre_final"` to
              cover the 5 added no-tool-context events too). */}
          {lifecycleHasToolScope(state.lifecycle) && (
            <li data-testid="step6-row-tool-scope" className="grid grid-cols-[max-content_1fr_max-content] items-start gap-x-3">
              <span className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold pt-0.5">{ko ? "도구" : "tool scope"}</span>
              <span className="text-[var(--color-text-secondary)]">
                {!state.toolScope || state.toolScope === "*"
                  ? <em>{ko ? "모든 도구" : "any tool"}</em>
                  : <code className="font-mono">{state.toolScope}</code>}
                <span className="ml-2 font-mono text-[11px] text-[var(--color-text-tertiary)]">matcher={matcher}</span>
              </span>
              <EditLink t={t} state={state} step={2} />
            </li>
          )}

          {/* Condition row → Step 3 (one row per condition entry; inline editor for sub-config). */}
          <li data-testid="step6-row-condition" className="grid grid-cols-[max-content_1fr_max-content] items-start gap-x-3">
            <span className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold pt-0.5">{ko ? "조건" : "condition"}</span>
            <div className="text-[var(--color-text-secondary)] min-w-0">
              <span>{state.conditionKind === "none" ? "—" : (state.conditionKind ?? "—")}</span>
              {state.conditionKind === "fetch_domain" && (
                <> · <code className="font-mono break-all">{state.fetchDomain || (ko ? "(비어있음)" : "(empty)")}</code></>
              )}
              {state.conditionKind === "domain_allowlist" && (
                <> · <code className="font-mono break-all">{state.allowlist || (ko ? "(비어있음)" : "(empty)")}</code></>
              )}
              {state.conditionKind === "regex" && (
                <> · <code className="font-mono break-all">{state.pattern || (ko ? "(비어있음)" : "(empty)")}</code></>
              )}
              {state.conditionKind === "llm_critic" && (
                <> · <em className="break-words">{state.llmCriterion || (ko ? "(비어있음)" : "(empty)")}</em></>
              )}
              {state.conditionKind === "evidence_ref" && evidenceList.length > 0 && (
                <ul className="mt-1 space-y-0.5 list-disc pl-5">
                  {evidenceList.map((v) => {
                    const desc = wiredSteps.find((w) => w.step === v)?.description ?? ""
                    return <li key={v}><code className="font-mono">{v}</code> {desc && <span className="text-[var(--color-text-tertiary)]">· {desc}</span>}</li>
                  })}
                </ul>
              )}
              {state.conditionKind === "shacl" && state.shaclTtl && (
                <> · SHACL ({state.shaclTtl.length} chars)</>
              )}
              {state.conditionKind === "shacl" && !state.shaclTtl && (
                <> · <em>{ko ? "(비어있음)" : "(empty)"}</em></>
              )}
              <InlineSubConfigPanel t={t} locale={locale} state={state} advanceAction={advanceAction} />
            </div>
            <EditLink t={t} state={state} step={3} />
          </li>

          {/* Action row → Step 4 */}
          <li data-testid="step6-row-action" className="grid grid-cols-[max-content_1fr_max-content] items-start gap-x-3">
            <span className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold pt-0.5">{ko ? "동작" : "action"}</span>
            <span className="text-[var(--color-text-secondary)]">
              {state.action ?? <em className="text-[var(--color-text-tertiary)] not-italic">—</em>}
            </span>
            <EditLink t={t} state={state} step={4} />
          </li>

          {/* IR-derived requires (read-only summary). No Edit row;
              edits happen via the condition row above. */}
          <li className="grid grid-cols-[max-content_1fr] items-start gap-x-3 pt-1 border-t border-black/[0.04]">
            <span className="text-[var(--color-text-tertiary)] uppercase tracking-wider font-semibold pt-0.5">{ko ? "IR requires" : "requires (IR)"}</span>
            <span className="text-[var(--color-text-secondary)] text-xs break-all">
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
            </span>
          </li>
        </ul>
      </Card>
      <form action={action}>
        <HiddenState state={state} />
        <NextButton label={t("newPolicy.wizard.savePolicy")} />
      </form>

      {/* D53b: Dry-run replay against the last 24h of ledger rows.
          The guided wizard's Step 6 surfaces the full draft IR; we
          re-derive it here from `state` (mirrors the persistDraft
          path) and feed it to the panel. The button is disabled
          when the wizard has not produced an id yet (saving would
          fail validation for the same reason). */}
      <DryRunPanel
        locale={locale}
        ir={state.id
          ? buildGuidedDraftForDryRun(state)
          : null}
        disabled={!state.id}
        action={(state.action === "strip" ? "strip" : (state.action ?? "audit"))}
      />
    </StepShell>
  )
}

// Suppress unused warnings (these are reserved for future kind support
// once the backend grows the explicit fetch/allowlist condition kinds).
void FETCH_TOOLS
