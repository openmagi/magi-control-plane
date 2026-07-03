import Link from "next/link"
import { revalidatePath } from "next/cache"
import { redirect } from "next/navigation"
import PayloadFieldChipsClient, {
  type Variant as ChipVariant,
} from "./_components/PayloadFieldChipsClient"
import { FieldPathSelect } from "./_components/FieldPathSelect"
import SteeringAwareField from "./_components/SteeringAwareField"
import Step1LifecyclePicker from "./_components/Step1LifecyclePicker"
import ToolCombobox from "./_components/ToolCombobox"
import { XMarkIcon, ArrowLeftIcon, CodeBracketIcon, AdjustmentsHorizontalIcon, CheckIcon, ChatBubbleLeftRightIcon, HomeIcon } from "@heroicons/react/24/outline"
import { VerifierFieldChecks } from "../../_components/VerifierFieldChecks"
import { verifierFiresOnLifecycle } from "@/lib/verifier-descriptors"
import { DryRunPanel } from "../_components/DryRunPanel"
import PolicyBuilder from "@/components/PolicyBuilder"
import ConversationalCompose from "./_components/ConversationalCompose"
import HandoffLink from "./_components/HandoffLink"
import AdvancedAuthoring from "./_components/AdvancedAuthoring"
import { PackMultiSelect } from "./_components/PackMultiSelect"
import { isPackCentricEnabled } from "@/lib/pack-centric"
import Step4bRunCommandFields from "./_components/Step4bRunCommandFields"
import Step4ActionAdvanced from "./_components/Step4ActionAdvanced"
import { previousLiveStep, buildBackHrefFromSearchParams } from "./wizard-nav"
import { codeForError, resolveFlash, type Step3ErrCode, type Step4ErrCode } from "@/lib/flash"
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
// D57f-1: total step count is unchanged. The "Inject extra context"
// archetype routes Step 4 into an inline template-editor sub-step on
// the same Step 4 surface; we don't add a separate progress dot for
// the template editor because the operator still completes Step 4
// before moving to Step 5 (name) → Step 6 (review).
const WIZARD_TOTAL = 6

/* ─────────────────────────────────────────────────────────────────────
 * New guided model (D41, expanded in D56c, D58 30-lifecycle surface).
 *
 * Step 1  Lifecycle  one of 30 CC hook lifecycles (5 families)
 * Step 2  ConditionKind  (varies by lifecycle, see below)
 * Step 3  Specifics  per-kind form (auto-skip when kind=none)
 * Step 4  Action  block / ask / audit / strip  (lifecycle-filtered)
 * Step 5  Name  policy id + optional description
 * Step 6  Review  plain English + IR preview
 *
 * D58 lifecycle families (slugs map 1:1 to CC events; see
 * LIFECYCLE_TO_EVENT below; src/magi_cp/policy/matrix.py
 * LEGAL_COMBINATIONS is the canonical truth source the cloud uses to
 * validate on save):
 *
 *   tool actions       — PreToolUse / PostToolUse plus the
 *                        observability variants (PostToolUseFailure,
 *                        PostToolBatch).
 *   content flow       — UserPromptSubmit / UserPromptExpansion /
 *                        PreCompact / PostCompact / Elicitation /
 *                        ElicitationResult.
 *   permissions        — PermissionRequest (gate) / PermissionDenied
 *                        (audit).
 *   subagents          — SubagentStart / SubagentStop.
 *   boundaries +       — Stop / StopFailure / SessionStart /
 *   workspace            SessionEnd / Setup / Notification /
 *                        TeammateIdle / TaskCreated / TaskCompleted /
 *                        ConfigChange / InstructionsLoaded /
 *                        MessageDisplay / WorktreeCreate /
 *                        WorktreeRemove / CwdChanged / FileChanged.
 *
 * Action-set rule:
 *   gate-style pre-hooks (PreToolUse / UserPromptSubmit /
 *     PermissionRequest / Elicitation)        → block / ask / audit
 *   mid-process pre-hooks (UserPromptExpansion / PreCompact —
 *     no interactive surface to interrupt to)  → block / audit
 *   everything else (post-hooks + observability) → audit only
 *
 * Tool scope is only meaningful for the two tool-context lifecycles
 * (before_tool_use, after_tool_use); every other lifecycle auto-skips
 * Step 2 and uses matcher="*".
 *
 * D58-followup verification status: only the pre-D58 8 events
 * (PreToolUse / PostToolUse / Stop / SubagentStop / UserPromptSubmit /
 * PreCompact / SessionStart / SessionEnd) are end-to-end verified to
 * be authorable. The other 22 are CANDIDATE names — see
 * matrix.py._UNVERIFIED_EVENTS for the full set + matrix.py module
 * docstring for the silent-fail-open path candidates expose.
 * ───────────────────────────────────────────────────────────────────── */

// D58 — full CC hook surface (30 events as of CC 2.1.170; the
// architecture doc still says "23 hook events" because the doc was
// written before the four 2.1.x rounds of additions). Names come from
// the canonical `nV` enum in the bundled CC binary. Mapping mirrors
// matrix.LEGAL_COMBINATIONS — adding a new row there must add a row
// here too. Slugs are kebab-case with a 1:1 mapping to the
// PascalCase CC event name.
type Lifecycle =
  // pre-D58 8 events (back-compat: slugs unchanged)
  | "before_tool_use" | "after_tool_use" | "pre_final"
  | "subagent_stop"   | "user_prompt"    | "pre_compact"
  | "session_start"   | "session_end"
  // D58: tool-context observability variants
  | "post_tool_use_failure" | "post_tool_batch"
  // D58: permission gate
  | "permission_request" | "permission_denied"
  // D58: content-flow extensions
  | "user_prompt_expansion" | "post_compact"
  | "elicitation" | "elicitation_result"
  // D58: subagent / stop
  | "subagent_start" | "stop_failure"
  // D58: lifecycle / observability long tail
  | "setup" | "notification"
  | "teammate_idle" | "task_created" | "task_completed"
  | "config_change"
  | "worktree_create" | "worktree_remove"
  | "instructions_loaded"
  | "cwd_changed" | "file_changed"
  | "message_display"
const LIFECYCLES: readonly Lifecycle[] = [
  // pre-D58
  "before_tool_use", "after_tool_use", "pre_final",
  "subagent_stop",   "user_prompt",    "pre_compact",
  "session_start",   "session_end",
  // D58
  "post_tool_use_failure", "post_tool_batch",
  "permission_request", "permission_denied",
  "user_prompt_expansion", "post_compact",
  "elicitation", "elicitation_result",
  "subagent_start", "stop_failure",
  "setup", "notification",
  "teammate_idle", "task_created", "task_completed",
  "config_change",
  "worktree_create", "worktree_remove",
  "instructions_loaded",
  "cwd_changed", "file_changed",
  "message_display",
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
  // D58
  post_tool_use_failure: "PostToolUseFailure",
  post_tool_batch:       "PostToolBatch",
  permission_request:    "PermissionRequest",
  permission_denied:     "PermissionDenied",
  user_prompt_expansion: "UserPromptExpansion",
  post_compact:          "PostCompact",
  elicitation:           "Elicitation",
  elicitation_result:    "ElicitationResult",
  subagent_start:        "SubagentStart",
  stop_failure:          "StopFailure",
  setup:                 "Setup",
  notification:          "Notification",
  teammate_idle:         "TeammateIdle",
  task_created:          "TaskCreated",
  task_completed:        "TaskCompleted",
  config_change:         "ConfigChange",
  worktree_create:       "WorktreeCreate",
  worktree_remove:       "WorktreeRemove",
  instructions_loaded:   "InstructionsLoaded",
  cwd_changed:           "CwdChanged",
  file_changed:          "FileChanged",
  message_display:       "MessageDisplay",
}

// D58: reverse of LIFECYCLE_TO_EVENT, computed once at module load
// so `_irToWizardState` has one place to look up event→lifecycle.
// Adding a new event only requires editing LIFECYCLE_TO_EVENT.
const EVENT_TO_LIFECYCLE: Record<string, Lifecycle | undefined> =
  Object.fromEntries(
    (Object.entries(LIFECYCLE_TO_EVENT) as [Lifecycle, string][])
      .map(([slug, ev]) => [ev, slug as Lifecycle]),
  )

// D56c: which lifecycles carry a tool context (Step 2 makes sense).
// Everything else auto-skips Step 2 and uses matcher="*".
const TOOL_CONTEXT_LIFECYCLES: ReadonlySet<Lifecycle> = new Set<Lifecycle>([
  "before_tool_use", "after_tool_use",
])

function lifecycleHasToolScope(life: Lifecycle | undefined): boolean {
  return life !== undefined && TOOL_CONTEXT_LIFECYCLES.has(life)
}

// D59 + D70: eight lifecycles map to CC hooks where authoring an
// inject_context policy is silent-fail-open. Mirrors
// `_CONTEXT_INJECTION_EXCLUDED_EVENTS` in src/magi_cp/policy/ir.py;
// adding a row there must add the lifecycle slug here too.
//
//   D59 — specialized hookSpecificOutput shape (additionalContext
//   ignored at runtime in favor of an alternate field):
//     elicitation         — hookSpecificOutput.elicitationDecision
//     elicitation_result  — hookSpecificOutput action / content override
//     worktree_create     — hookSpecificOutput.worktreePath
//     message_display     — display-only (no model-context channel)
//
//   D70 — end-of-life events with no downstream same-session model
//   turn for additionalContext to land in (CC silently drops the
//   field at these timings):
//     pre_final     (Stop)         — end of execution
//     stop_failure  (StopFailure)  — end of execution (failure variant)
//     session_end   (SessionEnd)   — session teardown
//     subagent_stop (SubagentStop) — child returned; parent-side
//                                    carry-over belongs on subagent_start
//
// Step 4's "Inject extra context" card is rendered with a disabled
// state + tooltip on these eight lifecycles. EvidencePolicy (audit) is
// still legal on every one of them — only the inject_context
// archetype is gated. The matching ContextInjectionPolicy.validate()
// raise is the canonical refusal; this set drives the dashboard's
// authoring affordance so the operator never reaches the cloud's
// 4xx flash.
const CONTEXT_INJECTION_EXCLUDED_LIFECYCLES: ReadonlySet<Lifecycle> =
  new Set<Lifecycle>([
    // D59 — specialized hookSpecificOutput shape
    "elicitation", "elicitation_result",
    "worktree_create", "message_display",
    // D70 — end-of-life events with no downstream same-session turn
    "pre_final", "stop_failure", "session_end", "subagent_stop",
  ])

function lifecycleAllowsInjectContext(life: Lifecycle | undefined): boolean {
  return life !== undefined &&
    !CONTEXT_INJECTION_EXCLUDED_LIFECYCLES.has(life)
}

// D59 follow-up (#14 code-hygiene): explicit narrow union for the
// excluded lifecycles so the switch below is compile-time exhaustive.
// A future excluded lifecycle widens this union, which propagates to
// `injectContextDisabledCopy` and the `never`-guard in the default
// branch, making the missing case loud at TS build time instead of
// silently falling through to the generic copy at runtime.
//
// D70 — extended to include the four end-of-life events (pre_final /
// stop_failure / session_end / subagent_stop). The reason copy differs
// per category: D59 entries name the alternate hookSpecificOutput
// field; D70 entries explain that there is no downstream same-session
// model turn for additionalContext to land in.
type ContextInjectionExcludedLifecycle =
  | "elicitation"
  | "elicitation_result"
  | "worktree_create"
  | "message_display"
  | "pre_final"
  | "stop_failure"
  | "session_end"
  | "subagent_stop"

// Per-lifecycle tooltip explaining the alternate channel (mirror of
// `_CONTEXT_INJECTION_ALTERNATE_CHANNEL` in src/magi_cp/policy/ir.py).
// Surfaces on the greyed-out inject_context card on Step 4 and as a
// caveat in the Step 1 lifecycleCardCopy helper text. The parameter is
// narrowed to the excluded-lifecycle union so call sites must prove
// `lifecycleAllowsInjectContext(life) === false` before reaching this
// function. The `never`-guard at the end keeps TS exhaustiveness loud
// on any future widening.
function injectContextDisabledCopy(
  life: ContextInjectionExcludedLifecycle, locale: "ko" | "en",
): string {
  const ko = locale === "ko"
  switch (life) {
    case "elicitation":
      return ko
        ? "이 hook 은 hookSpecificOutput.elicitationDecision 를 씁니다 (MCP elicitation 수락 / 거부). additionalContext 채널이 아니므로 추가 정보 주입은 불가합니다."
        : "This hook uses hookSpecificOutput.elicitationDecision (accept / decline an MCP elicitation request); the additionalContext channel does not apply here. Inject extra context is not available."
    case "elicitation_result":
      return ko
        ? "이 hook 은 MCP 서버로 응답을 보내기 전에 hookSpecificOutput 으로 action / 내용을 덮어씁니다. additionalContext 채널이 아니므로 추가 정보 주입은 불가합니다."
        : "This hook uses hookSpecificOutput to override the action or content before the response is sent to the MCP server; the additionalContext channel does not apply. Inject extra context is not available."
    case "worktree_create":
      return ko
        ? "이 hook 은 hookSpecificOutput.worktreePath 로 워크트리 경로를 반환합니다. additionalContext 채널이 아니므로 추가 정보 주입은 불가합니다."
        : "This hook uses hookSpecificOutput.worktreePath (the gate returns a worktree path); the additionalContext channel does not apply. Inject extra context is not available."
    case "message_display":
      return ko
        ? "이 hook 은 표시 전용입니다. 화면의 delta 만 바꾸고 저장된 메시지나 모델 컨텍스트는 건드리지 않습니다. 추가 정보 주입은 불가합니다."
        : "This hook is display-only. It replaces the on-screen delta without changing the stored message or feeding the model context. Inject extra context is not available."
    case "pre_final":
      return ko
        ? "이 hook 은 실행 종료 시점에 fire 됩니다. 같은 세션 안에 additionalContext 를 주입할 다음 모델 턴이 없어서 CC 는 이 필드를 무시합니다. 추가 정보 주입은 불가합니다."
        : "This hook fires at end-of-execution. There is no downstream same-session model turn for additionalContext to land in, so CC silently drops the field. Inject extra context is not available."
    case "stop_failure":
      return ko
        ? "이 hook 은 Stop 의 실패 변형으로 실행 종료 시점에 fire 됩니다. 같은 세션 안에 additionalContext 를 주입할 다음 모델 턴이 없어서 CC 는 이 필드를 무시합니다. 추가 정보 주입은 불가합니다."
        : "This hook mirrors Stop's end-of-execution timing (failure variant). There is no downstream same-session model turn for additionalContext to land in. Inject extra context is not available."
    case "session_end":
      return ko
        ? "이 hook 은 세션 종료 시점에 fire 됩니다. 세션이 닫히는 중이라 additionalContext 를 받을 모델 턴이 없습니다. 추가 정보 주입은 불가합니다."
        : "This hook fires at session teardown. The session is closing so there is no future model turn to receive additionalContext. Inject extra context is not available."
    case "subagent_stop":
      return ko
        ? "이 hook 은 child 가 반환된 직후 fire 됩니다. 부모 쪽으로 컨텍스트를 넘기려면 subagent_start 에 inject_context 를 다세요. 같은 세션 안에 additionalContext 가 들어갈 모델 턴이 없습니다."
        : "This hook fires after the child has returned. For parent-side carry-over, author the injection on subagent_start instead. There is no downstream same-session model turn for additionalContext on subagent_stop."
    default: {
      const _exhaustive: never = life
      return _exhaustive
    }
  }
}

// D59 follow-up (#14): narrow runtime guard returning a typed value so
// the call site can pass `lifecycle` to `injectContextDisabledCopy`
// without an `as` cast. Centralizes the excluded-set membership in one
// place that both UI and TS agree on.
function asContextInjectionExcludedLifecycle(
  life: Lifecycle,
): ContextInjectionExcludedLifecycle | null {
  switch (life) {
    case "elicitation":
    case "elicitation_result":
    case "worktree_create":
    case "message_display":
    case "pre_final":
    case "stop_failure":
    case "session_end":
    case "subagent_stop":
      return life
    default:
      return null
  }
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
  // D58: extended events. The condition surface for each is matched
  // to what the runtime payload actually carries — gate-style hooks
  // (permission / elicitation) accept regex + llm_critic on the
  // request body; tool-context observability hooks
  // (PostToolUseFailure / PostToolBatch) accept regex + llm_critic
  // on the failure / batch payload; lifecycle markers default to
  // "none" because they fire on every event and the wizard's IR
  // already keys per-hook.
  post_tool_use_failure: ["none", "regex", "llm_critic"],
  post_tool_batch:       ["none", "regex", "llm_critic"],
  permission_request:    ["none", "regex", "llm_critic"],
  permission_denied:     ["none", "regex"],
  user_prompt_expansion: ["none", "regex", "llm_critic"],
  post_compact:          ["none", "regex"],
  elicitation:           ["none", "regex", "llm_critic"],
  elicitation_result:    ["none", "regex"],
  subagent_start:        ["none", "regex", "llm_critic"],
  stop_failure:          ["none", "regex"],
  setup:                 ["none"],
  notification:          ["none", "regex"],
  teammate_idle:         ["none"],
  task_created:          ["none", "regex"],
  task_completed:        ["none", "regex"],
  config_change:         ["none", "regex"],
  worktree_create:       ["none", "regex"],
  worktree_remove:       ["none", "regex"],
  instructions_loaded:   ["none", "regex"],
  cwd_changed:           ["none", "regex"],
  file_changed:          ["none", "regex"],
  message_display:       ["none", "regex"],
}

const ALL_CONDITION_KINDS: readonly ConditionKind[] = [
  "none", "regex", "llm_critic",
  "fetch_domain", "domain_allowlist",
  "evidence_ref", "shacl",
]

// D57f-1: inject_context is a 5th action archetype. It's available on
// every lifecycle the matrix surfaces, because CC's hookSpecificOutput
// JSON schema accepts `additionalContext` on every hook event. The
// archetype maps to ContextInjectionPolicy at save time (NOT
// EvidencePolicy + requires), so Step 3's condition picker is skipped
// when the operator picks it on Step 4. Step 4b (an inline template
// editor) renders the static text + KO/EN labels the runtime shim
// will inject as additionalContext.
// D57f-2: input_rewrite is a 6th action archetype, available ONLY on
// before_tool_use with a per-tool matcher (tool / mcp_tool / tool_alt).
// CC supports updatedInput on the PreToolUse hook stdout, which lets
// the gate rewrite a tool's input before the tool runs (e.g. strip
// "sudo" from a Bash command, force https:// on a URL).
// D63: run_command is a 7th action archetype — execute an inline
// shell command or an attached script when the hook fires. The CC
// hookSpecificOutput JSON contract is uniform across all 30 events,
// so the wizard surfaces this action on every lifecycle. The matching
// IR shape is RunCommandPolicy; see web/app/(console)/policies/new/
// _components/RunCommandForm.tsx for Step 4b.
type Action = "block" | "ask" | "audit" | "strip" | "inject_context" | "input_rewrite" | "run_command"

// D68 follow-up: single source of truth for the Action union plus the
// subset that owns a Step 4b sub-form. Mirrors the D62 ALL_CONDITION_KINDS
// pattern. Exported via module-internal grep so the wizard-wiring test
// can iterate them and assert every sub-form-owning archetype has a case
// in validateStep4ActionSpecifics (a future archetype added to the
// union without an early-validation rule fails the test instead of
// silently passing through to Step 5).
const ALL_ACTIONS: readonly Action[] = [
  "block", "ask", "audit", "strip",
  "inject_context", "input_rewrite", "run_command",
]
const ACTIONS_WITH_SUBFORM: readonly Action[] = [
  "inject_context", "input_rewrite", "run_command",
]

// D68 follow-up: explicit narrow type for the rewriter kind union so
// validateStep4ActionSpecifics' inner switch becomes exhaustive over
// the three rewriter kinds. Adding a new kind to the WizardState type
// without a case here becomes a build-time error.
type RewriterKind = "prefix_strip" | "scheme_force" | "regex_substitute"
const ALL_REWRITER_KINDS: readonly RewriterKind[] = [
  "prefix_strip", "scheme_force", "regex_substitute",
]

// D56c: action set follows the matrix.py LEGAL_COMBINATIONS table.
//   before_tool_use → block / ask / audit (the runtime can refuse)
//   after_tool_use  → audit (tool already ran)
//   pre_final       → audit (Stop fires after the agent has chosen its
//                     final answer; the runtime cannot rewind.)
//   user_prompt     → block / ask / audit (prompt hasn't reached the LLM)
//   pre_compact     → block / audit (compaction hasn't fired yet)
//   subagent_stop / session_* → audit only (boundary markers)
// D70: derive `inject_context` membership from
// `CONTEXT_INJECTION_EXCLUDED_LIFECYCLES` so the wizard's action
// surface stays in lockstep with the runtime matrix. D57f-1's prior
// "inject_context is universally legal" assumption baked the excluded
// rows in by hand on both tables (`ACTIONS_BY_LIFECYCLE` and
// `ACTIONS_BY_COMBINATION`), letting D69's matrix narrowing diverge
// from the wizard's Step 4 surface (operator picked "Inject extra
// context" on elicitation, saw the action card, then hit a generic
// `illegal combination` save error). Centralizing the per-lifecycle
// derivation here means a future widening / re-narrowing of the
// excluded set propagates to both tables automatically.
function _allowsInjectContext(life: Lifecycle): boolean {
  return !CONTEXT_INJECTION_EXCLUDED_LIFECYCLES.has(life)
}
function _withInjectContextIf(
  life: Lifecycle,
  base: readonly Action[],
): readonly Action[] {
  return _allowsInjectContext(life)
    ? base
    : base.filter((a) => a !== "inject_context")
}

const ACTIONS_BY_LIFECYCLE: Record<Lifecycle, readonly Action[]> = {
  before_tool_use: _withInjectContextIf("before_tool_use", ["block", "ask", "audit", "inject_context", "input_rewrite", "run_command"]),
  // D82d — after_tool_use (PostToolUse) admits block as the CC
  // retry-feedback channel. The runtime cannot retract the call
  // that already ran, but it CAN tell the model the tool result is
  // unusable and surface the reason as a retry-feedback message
  // (CC stdout JSON `{"decision":"block","reason":"…"}`). ask
  // stays excluded — by the time the tool ran there is no
  // interactive surface to interrupt to.
  after_tool_use:  _withInjectContextIf("after_tool_use",  ["block", "audit", "inject_context", "run_command"]),
  // D70 — pre_final (Stop) is end-of-execution, no downstream
  // same-session model turn for additionalContext. inject_context
  // dropped by `_withInjectContextIf`.
  pre_final:       _withInjectContextIf("pre_final",       ["audit", "inject_context", "run_command"]),
  // D70 — subagent_stop similarly excluded; parent-side carry-over
  // belongs on subagent_start.
  subagent_stop:   _withInjectContextIf("subagent_stop",   ["audit", "inject_context", "run_command"]),
  user_prompt:     _withInjectContextIf("user_prompt",     ["block", "ask", "audit", "inject_context", "run_command"]),
  pre_compact:     _withInjectContextIf("pre_compact",     ["block", "audit", "inject_context", "run_command"]),
  session_start:   _withInjectContextIf("session_start",   ["audit", "inject_context", "run_command"]),
  // D70 — session_end is session teardown; inject_context dropped.
  session_end:     _withInjectContextIf("session_end",     ["audit", "inject_context", "run_command"]),
  // D58: pre-side gate hooks where CC supports decision overrides
  // (block) plus optional human review (ask). Same channel
  // PreToolUse uses.
  permission_request:    _withInjectContextIf("permission_request",    ["block", "ask", "audit", "inject_context", "run_command"]),
  // D59 — elicitation hookSpecificOutput uses .elicitationDecision;
  // inject_context dropped.
  elicitation:           _withInjectContextIf("elicitation",           ["block", "ask", "audit", "inject_context", "run_command"]),
  // D58: pre-side gates where there is no interactive surface to
  // interrupt to (the prompt is mid-expansion, the compaction is
  // already running). block + audit only.
  user_prompt_expansion: _withInjectContextIf("user_prompt_expansion", ["block", "audit", "inject_context", "run_command"]),
  // D58: everything else is audit-only — by the time CC fires the
  // hook the runtime cannot rewind.
  // D63: run_command is universally legal (CC stdout JSON contract is
  // the same on every hook).
  // D57f-1 / D59 / D70: inject_context is NOT universally legal — it
  // is gated by `_withInjectContextIf` against
  // `CONTEXT_INJECTION_EXCLUDED_LIFECYCLES`. The 8 excluded rows
  // (elicitation / elicitation_result / worktree_create /
  // message_display + pre_final / stop_failure / session_end /
  // subagent_stop) drop inject_context automatically.
  // D82d — PostToolUseFailure / PostToolBatch admit block on the same
  // retry-feedback channel. Per-event matcher narrowing is enforced by
  // ACTIONS_BY_COMBINATION below:
  //   post_tool_use_failure → block on per-tool matchers (tool/mcp_tool)
  //   post_tool_batch       → block on wildcard only (whole batch retry)
  post_tool_use_failure: _withInjectContextIf("post_tool_use_failure", ["block", "audit", "inject_context", "run_command"]),
  post_tool_batch:       _withInjectContextIf("post_tool_batch",       ["block", "audit", "inject_context", "run_command"]),
  permission_denied:     _withInjectContextIf("permission_denied",     ["audit", "inject_context", "run_command"]),
  post_compact:          _withInjectContextIf("post_compact",          ["audit", "inject_context", "run_command"]),
  // D59 — elicitation_result excluded.
  elicitation_result:    _withInjectContextIf("elicitation_result",    ["audit", "inject_context", "run_command"]),
  subagent_start:        _withInjectContextIf("subagent_start",        ["audit", "inject_context", "run_command"]),
  // D70 — stop_failure end-of-life excluded.
  stop_failure:          _withInjectContextIf("stop_failure",          ["audit", "inject_context", "run_command"]),
  setup:                 _withInjectContextIf("setup",                 ["audit", "inject_context", "run_command"]),
  notification:          _withInjectContextIf("notification",          ["audit", "inject_context", "run_command"]),
  teammate_idle:         _withInjectContextIf("teammate_idle",         ["audit", "inject_context", "run_command"]),
  task_created:          _withInjectContextIf("task_created",          ["audit", "inject_context", "run_command"]),
  task_completed:        _withInjectContextIf("task_completed",        ["audit", "inject_context", "run_command"]),
  config_change:         _withInjectContextIf("config_change",         ["audit", "inject_context", "run_command"]),
  // D59 — worktree_create excluded.
  worktree_create:       _withInjectContextIf("worktree_create",       ["audit", "inject_context", "run_command"]),
  worktree_remove:       _withInjectContextIf("worktree_remove",       ["audit", "inject_context", "run_command"]),
  instructions_loaded:   _withInjectContextIf("instructions_loaded",   ["audit", "inject_context", "run_command"]),
  cwd_changed:           _withInjectContextIf("cwd_changed",           ["audit", "inject_context", "run_command"]),
  file_changed:          _withInjectContextIf("file_changed",          ["audit", "inject_context", "run_command"]),
  // D59 — message_display excluded.
  message_display:       _withInjectContextIf("message_display",       ["audit", "inject_context", "run_command"]),
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
  //
  // D71: mcp__ prefix matched case-insensitively so the runtime
  // classifier stays in sync with the ToolCombobox badge (which uses
  // classifyCcToolName / cc-tools.ts — also case-insensitive). Without
  // this, typing 'MCP__github__search' showed a 'MCP' badge while the
  // matrix-gate classified it as 'tool', sending the policy through
  // the wrong row.
  const first = parseCsv(raw)[0]?.trim() || raw
  if (first.toLowerCase().startsWith("mcp__")) return "mcp_tool"
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

// D70: derive each `inject_context` membership from the same exclusion
// set as ACTIONS_BY_LIFECYCLE above so the two tables cannot drift.
// The prior hand-rolled rows surfaced `inject_context` on every
// lifecycle including the four D59-excluded ones, which let Step 4
// accept "Inject extra context" on (e.g.) elicitation before the
// cloud refused it via matrix.LEGAL_COMBINATIONS on save. A future
// re-add MUST go through `_filterByCombination` so the lockstep
// holds for both tables.
function _filterByCombination(
  life: Lifecycle, actions: readonly Action[],
): readonly Action[] {
  return _withInjectContextIf(life, actions)
}

// Per (lifecycle, matcher_class) action allowlist. Mirror of
// matrix.LEGAL_COMBINATIONS in src/magi_cp/policy/matrix.py — adding a
// new event/matcher there must be reflected here too.
const ACTIONS_BY_COMBINATION: Record<
  Lifecycle, Record<MatcherClassKey, readonly Action[]>
> = {
  before_tool_use: {
    tool:     _filterByCombination("before_tool_use", ["block", "ask", "audit", "inject_context", "input_rewrite", "run_command"]),
    mcp_tool: _filterByCombination("before_tool_use", ["block", "ask", "audit", "inject_context", "input_rewrite", "run_command"]),
    wildcard: _filterByCombination("before_tool_use", ["audit", "inject_context", "run_command"]),
  },
  after_tool_use: {
    // D82d — block joins audit / inject_context / run_command on
    // per-tool matchers for PostToolUse. The wizard authors one tool
    // per policy; the cloud matrix admits Bash|Edit-style alternation
    // matchers as an independent triple (matrix.py:399-401) so an
    // externally-authored raw IR can round-trip — the wizard never
    // emits one, but the cloud accepts it.
    tool:     _filterByCombination("after_tool_use", ["block", "audit", "inject_context", "run_command"]),
    mcp_tool: _filterByCombination("after_tool_use", ["block", "audit", "inject_context", "run_command"]),
    wildcard: [],
  },
  pre_final:     { tool: [], mcp_tool: [], wildcard: _filterByCombination("pre_final",     ["audit", "inject_context", "run_command"]) },
  subagent_stop: { tool: [], mcp_tool: [], wildcard: _filterByCombination("subagent_stop", ["audit", "inject_context", "run_command"]) },
  user_prompt:   { tool: [], mcp_tool: [], wildcard: _filterByCombination("user_prompt",   ["block", "ask", "audit", "inject_context", "run_command"]) },
  pre_compact:   { tool: [], mcp_tool: [], wildcard: _filterByCombination("pre_compact",   ["block", "audit", "inject_context", "run_command"]) },
  session_start: { tool: [], mcp_tool: [], wildcard: _filterByCombination("session_start", ["audit", "inject_context", "run_command"]) },
  session_end:   { tool: [], mcp_tool: [], wildcard: _filterByCombination("session_end",   ["audit", "inject_context", "run_command"]) },
  // D58 extensions — every new lifecycle is wildcard-only at the
  // matcher level (the new payloads either carry no tool name or
  // the wizard doesn't yet surface per-tool authoring on them).
  // Action set follows matrix.LEGAL_COMBINATIONS exactly.
  // D63: run_command is universally legal at the wildcard surface
  // (uniform CC stdout JSON contract).
  // D57f-1 / D59 / D70: inject_context is gated by
  // `_filterByCombination` against `CONTEXT_INJECTION_EXCLUDED_LIFECYCLES`.
  permission_request:    { tool: [], mcp_tool: [], wildcard: _filterByCombination("permission_request",    ["block", "ask", "audit", "inject_context", "run_command"]) },
  elicitation:           { tool: [], mcp_tool: [], wildcard: _filterByCombination("elicitation",           ["block", "ask", "audit", "inject_context", "run_command"]) },
  user_prompt_expansion: { tool: [], mcp_tool: [], wildcard: _filterByCombination("user_prompt_expansion", ["block", "audit", "inject_context", "run_command"]) },
  // D82d — PostToolUseFailure admits block on per-tool matchers
  // (failure recovery is scoped to a specific tool); the wildcard
  // surface stays audit / inject_context / run_command. Cross-tool
  // batched retry belongs on post_tool_batch.
  post_tool_use_failure: {
    tool:     _filterByCombination("post_tool_use_failure", ["block", "audit", "inject_context", "run_command"]),
    mcp_tool: _filterByCombination("post_tool_use_failure", ["block", "audit", "inject_context", "run_command"]),
    wildcard: _filterByCombination("post_tool_use_failure", ["audit", "inject_context", "run_command"]),
  },
  // D82d — PostToolBatch admits block on wildcard only. The batch
  // event covers the whole turn's tool calls so there is no single
  // tool name to scope to; per-tool authoring belongs on
  // post_tool_use_failure / after_tool_use instead.
  post_tool_batch:       { tool: [], mcp_tool: [], wildcard: _filterByCombination("post_tool_batch",       ["block", "audit", "inject_context", "run_command"]) },
  permission_denied:     { tool: [], mcp_tool: [], wildcard: _filterByCombination("permission_denied",     ["audit", "inject_context", "run_command"]) },
  post_compact:          { tool: [], mcp_tool: [], wildcard: _filterByCombination("post_compact",          ["audit", "inject_context", "run_command"]) },
  elicitation_result:    { tool: [], mcp_tool: [], wildcard: _filterByCombination("elicitation_result",    ["audit", "inject_context", "run_command"]) },
  subagent_start:        { tool: [], mcp_tool: [], wildcard: _filterByCombination("subagent_start",        ["audit", "inject_context", "run_command"]) },
  stop_failure:          { tool: [], mcp_tool: [], wildcard: _filterByCombination("stop_failure",          ["audit", "inject_context", "run_command"]) },
  setup:                 { tool: [], mcp_tool: [], wildcard: _filterByCombination("setup",                 ["audit", "inject_context", "run_command"]) },
  notification:          { tool: [], mcp_tool: [], wildcard: _filterByCombination("notification",          ["audit", "inject_context", "run_command"]) },
  teammate_idle:         { tool: [], mcp_tool: [], wildcard: _filterByCombination("teammate_idle",         ["audit", "inject_context", "run_command"]) },
  task_created:          { tool: [], mcp_tool: [], wildcard: _filterByCombination("task_created",          ["audit", "inject_context", "run_command"]) },
  task_completed:        { tool: [], mcp_tool: [], wildcard: _filterByCombination("task_completed",        ["audit", "inject_context", "run_command"]) },
  config_change:         { tool: [], mcp_tool: [], wildcard: _filterByCombination("config_change",         ["audit", "inject_context", "run_command"]) },
  worktree_create:       { tool: [], mcp_tool: [], wildcard: _filterByCombination("worktree_create",       ["audit", "inject_context", "run_command"]) },
  worktree_remove:       { tool: [], mcp_tool: [], wildcard: _filterByCombination("worktree_remove",       ["audit", "inject_context", "run_command"]) },
  instructions_loaded:   { tool: [], mcp_tool: [], wildcard: _filterByCombination("instructions_loaded",   ["audit", "inject_context", "run_command"]) },
  cwd_changed:           { tool: [], mcp_tool: [], wildcard: _filterByCombination("cwd_changed",           ["audit", "inject_context", "run_command"]) },
  file_changed:          { tool: [], mcp_tool: [], wildcard: _filterByCombination("file_changed",          ["audit", "inject_context", "run_command"]) },
  message_display:       { tool: [], mcp_tool: [], wildcard: _filterByCombination("message_display",       ["audit", "inject_context", "run_command"]) },
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

// D70: the legacy `TOOL_PRESETS` chip list (10 names) was retired in
// favour of the canonical CC built-in list in `@/lib/cc-tools`. The
// Step 2 surface now uses a single autocomplete combobox that covers
// every built-in tool (17 as of CC v2.1.170) plus free-typed MCP /
// custom names. Nothing else in this file references the old list.

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
  // D82c: regex condition splits into target field path + pattern.
  // Default for legacy / migrated states is "tool_response.output"
  // (the after_tool_use case), set by the IR-to-wizard adapter so
  // existing { pattern } shapes round-trip without state loss.
  regexFieldPath?: string           // regex (chip-picked or freeform)
  llmCriterion?: string             // llm_critic
  evidenceRefs?: string[]           // evidence_ref (multi)
  shaclTtl?: string                 // shacl
  action?: Action
  // D57f-1: when action=inject_context the wizard does NOT compile to
  // EvidencePolicy+requires; it compiles to ContextInjectionPolicy with
  // these three fields. injectTemplate is the static text the runtime
  // shim emits as additionalContext on the chosen hook; injectLabelKo /
  // injectLabelEn are optional human-readable names for the dashboard's
  // list view (the runtime never reads them).
  injectTemplate?: string
  injectLabelKo?: string
  injectLabelEn?: string
  // D57f-2: when action=input_rewrite the wizard compiles to an
  // InputRewritePolicy with these fields. rewriterKind picks which
  // operation runs; the rewriter*-prefixed fields are per-kind config
  // applied to a single field name in the tool_input dict.
  rewriterKind?: "prefix_strip" | "scheme_force" | "regex_substitute"
  rewriterField?: string                  // e.g. "command" for Bash, "url" for WebFetch
  rewriterPrefix?: string                 // prefix_strip
  rewriterStripRepeat?: "true" | "false"  // prefix_strip
  rewriterFrom?: string                   // scheme_force
  rewriterTo?: string                     // scheme_force
  rewriterPattern?: string                // regex_substitute
  rewriterReplacement?: string            // regex_substitute
  rewriterCount?: string                  // regex_substitute (stringified int)
  // D63: when action="run_command" the wizard compiles to a
  // RunCommandPolicy with these fields. They ride on the URL state the
  // same way the rewriter fields do so Edit-jumps from Step 6 back to
  // Step 4b preserve the command body / script id / timeout / etc.
  // The Step 6 plain summary surfaces the verbatim command body via
  // these fields too (brief: "do not strip the actual command body").
  runCommandMode?: "inline" | "attach"
  runCommandRuntime?: "bash" | "python3" | "node"
  runCommandBody?: string                 // inline (≤4000 chars)
  runCommandScriptId?: string             // attach (64-hex sha256)
  runCommandScriptName?: string           // attach (operator-facing label)
  runCommandArgs?: string                 // raw CSV (server splits + trims)
  runCommandTimeoutMs?: string            // stringified int
  runCommandFailClosed?: "true" | "false" // checkbox state
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
  // D57e P1: evidenceRefs naming a verifier that does not fire on
  // the current lifecycle (e.g. `source_allowlist` riding through on
  // a Stop policy URL) are pruned at the state-build seam AND on
  // save. We stash the dropped names here so Step 3 + Step 6 can
  // surface a one-shot banner mirroring `_droppedConditionKind`.
  // Read-only — not part of URL state.
  _droppedEvidenceRefs?: string[]
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
      if (!p) return []
      // D82c fix: thread the picker's chosen field_path into the IR so
      // the runtime's /verify_inline regex scopes its match to the
      // chosen field. Empty / "*" → whole-payload scan (legacy default
      // when the picker is left blank). A real path lands as a typed
      // EvidenceReq.field_path on the cloud side.
      const fp = (s.regexFieldPath ?? "").trim()
      const req: { kind: "regex"; pattern: string; field_path?: string } = {
        kind: "regex",
        pattern: p,
      }
      if (fp && fp !== "*") req.field_path = fp
      return [req]
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

/** D82c fix: per-lifecycle default for the regex Field-to-match picker.
 * The previous hard-coded `"tool_response.output"` default was correct
 * for after_tool_use only — every other lifecycle's payload schema
 * never exposes that path, so the browser silently fell back to
 * whichever option the static <option> list rendered first, while the
 * wizard state thought the default was tool_response.output. The
 * mismatch was harmless until D82c started threading field_path into
 * the IR; the moment it does, the wrong field gets scoped at runtime.
 *
 * This helper returns the canonical "primary check field" per
 * lifecycle. For each lifecycle the chosen default is the field the
 * 90% authoring use case would pick (Bash command on PreToolUse, tool
 * output on PostToolUse, prompt on UserPromptSubmit, final answer on
 * Stop, transcript path on PreCompact, session id on SessionStart).
 */
function defaultRegexFieldFor(s: WizardState): string {
  const life: Lifecycle = s.lifecycle ?? "before_tool_use"
  // Tool-context events look at the tool itself; pick based on the
  // tool family the operator chose. Bash commands and Read/Edit/Write
  // file paths are the dominant cases; default to `tool_input.command`
  // for Bash and `tool_input.file_path` for Read/Edit/Write so the
  // chip picker's default is a path the runtime actually delivers.
  const scope = (s.toolScope ?? "").trim()
  const firstTool = (parseCsv(scope)[0] ?? scope).trim()
  switch (life) {
    case "before_tool_use":
      if (firstTool === "Bash") return "tool_input.command"
      if (firstTool === "WebFetch") return "tool_input.url"
      if (firstTool === "Read" || firstTool === "Edit" || firstTool === "Write")
        return "tool_input.file_path"
      // Wildcard / unknown tool — `tool_input` is the only guaranteed
      // top-level key on PreToolUse.
      return "tool_input"
    case "after_tool_use":
      // The verifier 90% pattern: scan the tool's textual output.
      return "tool_response.output"
    case "user_prompt":
      return "prompt"
    case "pre_final":
    case "subagent_stop":
      return "final_message"
    case "pre_compact":
    case "session_start":
    case "session_end":
      return "session_id"
    default:
      // For the rest of the D58/D70 surface either no payload path
      // is reliably present or the lifecycle is audit-only; the
      // wizard's chip filter will surface what's available and a
      // missing default just lets the browser render the first option.
      return ""
  }
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
  // D57f-1: inject_context collapses to audit for the panel's purposes
  // (the panel never actually renders for that archetype — see Step6
  // Review — but a stale call path bypassing the gate should still
  // produce a valid evidence-shape draft instead of crashing).
  // D57f-2: input_rewrite has the same fallback rationale.
  const action =
    s.action === "strip" || s.action === "inject_context" ||
    s.action === "input_rewrite" || s.action === "run_command"
      ? "audit"
      : (s.action ?? "audit")
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
    // D58-followup (P1 #5): the default branch reads from
    // LIFECYCLE_LABEL_EN so the 22 D58 lifecycles surface a
    // lifecycle-accurate phrase instead of the tool-call default
    // (which used to render "On every matching tool call," even when
    // the user picked Notification / FileChanged / etc.).
    switch (s.lifecycle) {
      case "user_prompt":   return "On every user prompt,"
      case "pre_compact":   return "Right before each context compaction,"
      case "subagent_stop": return "Each time a subagent finishes,"
      case "session_start": return "When the session opens,"
      case "session_end":   return "When the session closes,"
      case "pre_final":     return "When the agent has just finished its answer,"
      case "after_tool_use":return "On every matching tool call,"
      case "before_tool_use":return "On every matching tool call,"
      default: {
        const life = s.lifecycle
        if (life === undefined) return "On every matching tool call,"
        return `${capitalize(LIFECYCLE_LABEL_EN[life])},`
      }
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
    // D58-followup (P1 #5): default branch reads LIFECYCLE_LABEL_KO
    // so a D58 lifecycle without an explicit case still produces a
    // lifecycle-accurate header (not the tool-call default).
    switch (s.lifecycle) {
      case "user_prompt":   return "유저 프롬프트가 도착할 때마다,"
      case "pre_compact":   return "컨텍스트 컴팩션 직전마다,"
      case "subagent_stop": return "서브에이전트가 끝날 때마다,"
      case "session_start": return "세션이 시작될 때,"
      case "session_end":   return "세션이 종료될 때,"
      case "pre_final":     return "에이전트가 최종 응답을 마쳤을 때,"
      case "after_tool_use":return "도구 호출이 끝날 때마다,"
      case "before_tool_use":return "조건에 매칭되는 도구 호출마다,"
      default: {
        const life = s.lifecycle
        if (life === undefined) return "조건에 매칭되는 도구 호출마다,"
        return `${LIFECYCLE_LABEL_KO[life]}마다,`
      }
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
  // D58
  post_tool_use_failure: "도구 실행 실패 시점",
  post_tool_batch:       "도구 배치 실행 직후",
  permission_request:    "권한 요청 직전",
  permission_denied:     "권한 거부 직후",
  user_prompt_expansion: "유저 프롬프트 확장 직전",
  post_compact:          "컨텍스트 컴팩션 직후",
  elicitation:           "유저 응답 요청 직전",
  elicitation_result:    "유저 응답 수신 직후",
  subagent_start:        "서브에이전트 시작 시점",
  stop_failure:          "에이전트 종료 실패 시점",
  setup:                 "최초 셋업 시점",
  notification:          "알림 발송 시점",
  teammate_idle:         "팀메이트 유휴 시점",
  task_created:          "Task 도구 디스패치 시점",
  task_completed:        "Task 도구 완료 시점",
  config_change:         "설정 변경 시점",
  worktree_create:       "워크트리 생성 시점",
  worktree_remove:       "워크트리 제거 시점",
  instructions_loaded:   "지침 로드 시점",
  cwd_changed:           "작업 디렉터리 변경 시점",
  file_changed:          "파일 변경 감지 시점",
  message_display:       "메시지 표시 시점",
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
  // D58
  post_tool_use_failure: "when a tool call fails",
  post_tool_batch:       "after a tool batch returns",
  permission_request:    "before a permission decision",
  permission_denied:     "when a permission was denied",
  user_prompt_expansion: "while a user prompt is being expanded",
  post_compact:          "right after a context compaction",
  elicitation:           "before the runtime asks the user a question",
  elicitation_result:    "when the user has answered an elicitation",
  subagent_start:        "when a subagent is about to start",
  stop_failure:          "when an agent stop fails",
  setup:                 "during one-shot environment setup",
  notification:          "when CC raises a notification",
  teammate_idle:         "when a teammate goes idle",
  task_created:          "when the Task tool dispatches a subagent",
  task_completed:        "when the Task tool returns",
  config_change:         "when a setting changes",
  worktree_create:       "when a git worktree is created",
  worktree_remove:       "when a git worktree is removed",
  instructions_loaded:   "when project instructions are loaded",
  cwd_changed:           "when the working directory changes",
  file_changed:          "when a watched file changes",
  message_display:       "when a message is rendered to the user",
}

function plainSummary(s: WizardState, locale: "ko" | "en"): string {
  const ko = locale === "ko"
  const header = ko ? actionHeaderKO(s) : actionHeaderEN(s)
  const act = s.action ?? "audit"
  const life = s.lifecycle ?? "before_tool_use"
  const lifeLabel = ko ? LIFECYCLE_LABEL_KO[life] : LIFECYCLE_LABEL_EN[life]
  // D57f-1: inject_context surfaces a distinct summary that names the
  // template (truncated to keep the card tidy) instead of a verifier
  // gate. The runtime shim emits the template under additionalContext.
  if (act === "inject_context") {
    const tpl = (s.injectTemplate ?? "").trim()
    const snippet = tpl.length > 80 ? tpl.slice(0, 80) + "…" : tpl
    return ko
      ? `${lifeLabel}, 다음 텍스트가 모델 컨텍스트에 추가됩니다: ${snippet || "(본문 비어있음)"}`
      : `${capitalize(lifeLabel)}: this policy injects the following text into the model's context: ${snippet || "(template empty)"}`
  }
  // D57f-2: input_rewrite surfaces the rewriter operation in plain
  // language. The cloud applies the rewriter spec; the gate's hook
  // emits the new tool_input dict to CC via updatedInput.
  if (act === "input_rewrite") {
    const kind = s.rewriterKind ?? "prefix_strip"
    const field = (s.rewriterField ?? "").trim() || "(field?)"
    let op = ""
    if (kind === "prefix_strip") {
      const prefix = (s.rewriterPrefix ?? "").trim()
      op = ko
        ? `\`${field}\`에서 접두사 \`${prefix || "(?)"}\`를 제거`
        : `strip prefix \`${prefix || "(?)"}\` from \`${field}\``
    } else if (kind === "scheme_force") {
      op = ko
        ? `\`${field}\` 의 스킴을 \`${s.rewriterFrom ?? "?"}\` → \`${s.rewriterTo ?? "?"}\`로 강제`
        : `force \`${field}\` scheme from \`${s.rewriterFrom ?? "?"}\` → \`${s.rewriterTo ?? "?"}\``
    } else {
      op = ko
        ? `\`${field}\` 에 정규식 치환 적용`
        : `apply a regex substitution to \`${field}\``
    }
    return ko
      ? `${lifeLabel}, 도구가 실행되기 전에 입력을 수정합니다: ${op}.`
      : `${capitalize(lifeLabel)}: rewrite the tool's input before it runs — ${op}.`
  }
  // D63: run_command surfaces the inline command (or attached script
  // id) so Step 6 review reads as a plain "Will run: …" line.
  // Brief review (P1): do NOT strip the actual command body — the
  // operator needs to see what will execute before saving.
  if (act === "run_command") {
    const mode = s.runCommandMode ?? "inline"
    const runtime = s.runCommandRuntime ?? "bash"
    const args = (s.runCommandArgs ?? "").trim()
    const timeoutMs = s.runCommandTimeoutMs?.trim() || "5000"
    const failClosed = s.runCommandFailClosed === "true"
    const failTail = failClosed
      ? (ko ? " (실패 시 deny)" : " (deny on failure)")
      : (ko ? " (실패 시 audit + 통과)" : " (audit + continue on failure)")
    if (mode === "attach") {
      const name = (s.runCommandScriptName ?? s.runCommandScriptId ?? "").trim() || (ko ? "(스크립트 미지정)" : "(no script chosen)")
      const argsTail = args ? (ko ? `, 인자 [${args}]` : `, args [${args}]`) : ""
      return ko
        ? `${lifeLabel}, 첨부 스크립트 실행: \`${name}\` (${runtime}${argsTail}, 타임아웃 ${timeoutMs}ms)${failTail}.`
        : `${capitalize(lifeLabel)}: run attached script \`${name}\` (${runtime}${argsTail}, timeout ${timeoutMs}ms)${failTail}.`
    }
    // Inline lane: render the verbatim command body.
    const rawBody = (s.runCommandBody ?? "").trim()
    const body = rawBody.length > 160 ? rawBody.slice(0, 160) + "…" : rawBody
    const cmd = body || (ko ? "(아직 명령 없음)" : "(no command yet)")
    const argsTail = args ? (ko ? `, 인자 [${args}]` : `, args [${args}]`) : ""
    return ko
      ? `${lifeLabel}, 실행 (${runtime}): \`${cmd}\`${argsTail}, 타임아웃 ${timeoutMs}ms${failTail}.`
      : `${capitalize(lifeLabel)}: run (${runtime}): \`${cmd}\`${argsTail}, timeout ${timeoutMs}ms${failTail}.`
  }
  type LegacyAct = "block" | "ask" | "audit" | "strip"
  const legacyAct: LegacyAct = (
    act === "block" || act === "ask" || act === "audit" || act === "strip"
  ) ? act : "audit"
  // D82d follow-up: block summary copy was lifecycle-blind — "이 정책은
  // 차단 합니다" / "this policy will block" reads as "refuse the call"
  // even on the three PostToolUse* lifecycles where Step 4 already
  // told the operator the channel is retry-feedback. Keep Step 4's
  // disambiguation surface in lockstep with Step 6 so the review line
  // does not contradict the action sub-copy.
  const isPostToolBlock = legacyAct === "block" && (
    s.lifecycle === "after_tool_use"
      || s.lifecycle === "post_tool_use_failure"
      || s.lifecycle === "post_tool_batch"
  )
  const actLabel = ko
    ? (isPostToolBlock
        ? "verifier verdict 를 retry-feedback 으로 모델에 돌려보내기"
        : { block: "차단", ask: "사람 승인 요청", audit: "원장에만 기록", strip: "출력에서 제거" }[legacyAct])
    : (isPostToolBlock
        ? "surface the verifier verdict to the model as retry-feedback"
        : { block: "block", ask: "ask a human", audit: "record to the ledger only", strip: "strip from the output" }[legacyAct])
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

/** D57f-1: a minimal ContextInjectionPolicy persisted dict. The cloud
 * dispatches on `type` via policy_from_dict so we just need the right
 * shape. validatePolicyId is shared with the evidence-shape path.
 */
type ContextInjectionDraft = {
  type: "context_injection"
  id: string
  description: string
  version: string
  event: string
  matcher: string
  template: string
}

/** D57f-2: persisted shape for an InputRewritePolicy. Mirrors the
 * cloud's IR — the rewriter spec is a bounded {kind, config} pair the
 * cloud applies server-side at PreToolUse time. */
type InputRewriteDraft = {
  type: "input_rewrite"
  id: string
  description: string
  version: string
  trigger: { host: "claude-code"; event: "PreToolUse"; matcher: string }
  rewriter: {
    kind: "prefix_strip" | "scheme_force" | "regex_substitute"
    config: Record<string, unknown>
  }
}

/** D63: persisted shape for a RunCommandPolicy. The cloud's
 * validate() pin enforces exactly-one-of command / script_path; the
 * dashboard ships the field unconditionally to keep the JSON byte
 * shape predictable, and the cloud handles the rest. */
type RunCommandDraftPersist = {
  type: "run_command"
  id: string
  description: string
  version: string
  trigger: { host: "claude-code"; event: string; matcher: string }
  runtime: "bash" | "python3" | "node"
  command: string
  script_path: string
  args: string[]
  timeout_ms: number
  fail_closed: boolean
}

async function persistDraft(
  draft:
    | PolicyDraft
    | ContextInjectionDraft
    | InputRewriteDraft
    | RunCommandDraftPersist,
  source: string,
  /** P4 (pack-centric authoring): 0..n user-pack ids the saved policy
   *  should join. Threaded from the PackMultiSelect hidden input on each
   *  authoring surface. Empty / undefined = orphan (no pack membership).
   *  The cloud appends the policy id to each named pack in the same
   *  transaction as the policy write. */
  packIds?: string[],
): Promise<void> {
  // D57f-1 / D57f-2: validateDraft only knows the evidence shape; skip
  // it for the sibling archetypes (the cloud's per-type validate() is
  // canonical and the dashboard surfaces the cloud's 4xx via the flash
  // redirect path).
  const draftType = (draft as { type?: string }).type
  if (
    draftType !== "context_injection"
    && draftType !== "input_rewrite"
    && draftType !== "run_command"
  ) {
    const errs = validateDraft(draft as PolicyDraft)
    if (errs.length > 0) { redirect("/policies/new?err=invalid_input"); return }
  }
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
        body: JSON.stringify({
          policy: draft, source, enabled: true,
          // Only include pack_ids when the operator selected at least
          // one pack; omitting the field keeps the orphan (no-pack)
          // path byte-identical to the pre-P4 request shape.
          ...(packIds && packIds.length > 0 ? { pack_ids: packIds } : {}),
        }),
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

/** P4: parse the PackMultiSelect hidden input (`pack_ids` = JSON array
 * of user-pack ids) shared by all three authoring surfaces. Defensive:
 * a missing / malformed value degrades to "no packs" (orphan) so a
 * broken picker never blocks a save. */
function _parsePackIds(formData: FormData): string[] {
  const raw = formData.get("pack_ids")
  if (typeof raw !== "string" || !raw) return []
  try {
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((x): x is string => typeof x === "string" && !!x)
  } catch {
    return []
  }
}

/** Persist a COMPOUND draft (e.g. type=evidence_gate) via
 *  POST /policies/compound. The server expands it into its member IR
 *  policies (audit + precondition + ledger-protection denies) and saves
 *  the owning PolicyRecord + rules atomically. Mirrors persistDraft's
 *  admin-key + error-flash handling, but the endpoint + redirect differ:
 *  a compound has no single-rule detail page, so we land on the policies
 *  list where the grouped policy renders. */
async function persistCompoundDraft(
  draft: { id?: string; type?: string; [k: string]: unknown },
  source: string,
  packIds?: string[],
): Promise<void> {
  const policyId = String(draft.id ?? "").trim()
  try { validatePolicyId(policyId) }
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
  try {
    const r = await fetch(
      `${process.env.MAGI_CP_CLOUD_URL || "http://127.0.0.1:8787"}/policies/compound`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Admin-Api-Key": adminKey },
        cache: "no-store",
        body: JSON.stringify({
          draft, source, enabled: true,
          ...(packIds && packIds.length > 0 ? { pack_ids: packIds } : {}),
        }),
        signal: AbortSignal.timeout(8000),
      },
    )
    if (!r.ok) {
      console.error(`cloud ${r.status} POST /policies/compound: ${await r.text().catch(() => "")}`)
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
  redirect(`/policies?msg=saved`)
}

async function saveCompiled(formData: FormData): Promise<void> {
  "use server"
  let draft: PolicyDraft
  try { draft = JSON.parse(String(formData.get("ir_json") ?? "{}")) }
  catch { redirect("/policies/new?err=invalid_input"); return }
  const source = String(formData.get("source") ?? "org")
  // Compound drafts (e.g. the conversational evidence-gate) save via
  // POST /policies/compound so the server expands them into their member
  // rules; single-policy drafts keep the PUT /policies path.
  const draftType = (draft as { type?: string }).type
  if (draftType === "evidence_gate") {
    await persistCompoundDraft(
      draft as { id?: string; type?: string }, source, _parsePackIds(formData),
    )
    return
  }
  await persistDraft(draft, source, _parsePackIds(formData))
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
  await persistDraft(draft, source, _parsePackIds(formData))
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

  // D62: Step 3 → Step 4 advance now validates the conditionKind's
  // specifics inline. Before D62 the wizard happily passed a
  // half-filled conditionKind state to Step 4 / Step 5; saveWizard
  // only caught it at the final submit and bounced back with a
  // generic "Invalid input" banner with no inline pointer. The
  // operator could not tell what was wrong. Now we refuse the
  // advance and redirect back to Step 3 with a precise err code,
  // and Step 3 renders an inline highlight + helper copy next to
  // the empty input.
  if (stepIn === 3) {
    const kindRaw = params.get("conditionKind")
    const stepThreeErr = validateStep3Specifics(kindRaw, params, evMerged)
    if (stepThreeErr) {
      params.set("step", "3")
      params.set("err", stepThreeErr)
      redirect(`/policies/new?${params.toString()}`); return
    }
  }

  // D68: Step 4 → Step 5 advance now validates the chosen action's
  // sub-form (Step 4b) specifics. Mirror of D62 for Step 3. Before
  // D68 the wizard would silently bounce an inject_context with no
  // template (and run_command with no command + no script_id, and
  // input_rewrite with no rewriter config) back to Step 4 with NO
  // err param at all — saveWizard's per-action branches redirected
  // with a generic `err=invalid_input` or even no err at all,
  // leaving the operator staring at the picker with no inline
  // pointer to the empty field. Refuse the advance here, redirect
  // back to Step 4 with a precise err code, and Step4Action renders
  // an inline banner near the Step 4b sub-form + a red ring on the
  // specific input that's empty. block / ask / audit have no
  // sub-form and pass through.
  if (stepIn === 4) {
    const actionRaw = params.get("action")
    const stepFourErr = validateStep4ActionSpecifics(actionRaw, params)
    if (stepFourErr) {
      params.set("step", "4")
      params.set("err", stepFourErr)
      redirect(`/policies/new?${params.toString()}`); return
    }
  }

  // P9 (D49): cumulative-tip dismissal lives in sessionStorage owned
  // by SteeringAwareField; nothing to scrub off the URL here.

  params.set("step", String(nextStep))
  redirect(`/policies/new?${params.toString()}`)
}

/** D62: Step 3 → Step 4 advance gate. Returns a precise err code
 *  when the operator picked a conditionKind but left its required
 *  specifics empty, or did not pick a kind at all. Returns null
 *  when the advance is safe. Each err code is rendered by
 *  Step3Condition as a localized inline highlight next to the empty
 *  input (red ring + helper copy), replacing the previous generic
 *  "Invalid input" banner that fired at Step 5 with no pointer.
 *
 *  D62 follow-up: the `default` branch is now type-exhaustive via a
 *  `never`-guard so adding a new ConditionKind without a case is a
 *  build-time error instead of silently falling through to a
 *  `pick_condition` verdict (which would strand an operator who
 *  picked the new kind but the gate did not know its required
 *  field). */
function validateStep3Specifics(
  kindRaw: string | null,
  params: URLSearchParams,
  evMerged: string[],
): Step3ErrCode | null {
  if (!kindRaw || !(ALL_CONDITION_KINDS as readonly string[]).includes(kindRaw)) {
    return "pick_condition"
  }
  const kind = kindRaw as ConditionKind
  switch (kind) {
    case "none":
      return null
    case "fetch_domain":
      return (params.get("fetchDomain") ?? "").trim() ? null : "missing_domain"
    case "domain_allowlist": {
      const raw = (params.get("allowlist") ?? "").trim()
      return raw && parseCsv(raw).length > 0 ? null : "missing_allowlist"
    }
    case "regex":
      return (params.get("pattern") ?? "").trim() ? null : "missing_pattern"
    case "llm_critic":
      return (params.get("llmCriterion") ?? "").trim() ? null : "missing_criterion"
    case "evidence_ref":
      return evMerged.length > 0 ? null : "missing_evidence"
    case "shacl":
      return (params.get("shaclTtl") ?? "").trim() ? null : "missing_shacl"
    default: {
      const _exhaustive: never = kind
      return _exhaustive
    }
  }
}

/** D68: Step 4 to Step 5 advance gate. Returns a precise err code
 *  when the operator picked an action that owns a Step 4b sub-form
 *  but left its required fields empty. Returns null when the
 *  advance is safe.
 *
 *  Three actions own a Step 4b sub-form today (the canonical list
 *  lives in ACTIONS_WITH_SUBFORM above this function):
 *   - inject_context  -> empty `injectTemplate` returns "missing_template"
 *   - run_command     -> empty `runCommandBody` AND empty
 *                        `runCommandScriptId` returns
 *                        "missing_command_or_script"
 *                        (inline-mode operators fill body; attach-mode
 *                        operators fill script_id; both empty is the
 *                        silent-pass-through bug D68 closes)
 *   - input_rewrite   -> empty rewriter config (varies by kind:
 *                        prefix_strip needs `rewriterPrefix`,
 *                        scheme_force needs both `rewriterFrom` +
 *                        `rewriterTo`, regex_substitute needs
 *                        `rewriterPattern`). Per-kind codes mirror the
 *                        D62 per-condition split so the inline copy
 *                        names only the relevant field and avoids
 *                        leaking IR kind names to operators:
 *                          prefix_strip      -> "missing_rewriter_prefix"
 *                          scheme_force      -> "missing_rewriter_scheme"
 *                          regex_substitute  -> "missing_rewriter_pattern"
 *
 *  block / ask / audit / strip have no Step 4b sub-form: their cases
 *  return null explicitly so the intent is documented in code, not
 *  implicit in a permissive `default`.
 *
 *  D68 follow-up: the signature now narrows actionRaw to Action
 *  through ALL_ACTIONS (mirror of D62's validateStep3Specifics
 *  narrowing kindRaw via ALL_CONDITION_KINDS). Unknown raw strings
 *  return null (Step 4 also requires the radio; this branch only
 *  fires on a hand-rolled POST that bypassed the form). The default
 *  branch is a `_exhaustive: never` so a future archetype added to
 *  the Action union without a case here becomes a build-time error,
 *  re-introducing the silent-pass-through bug is no longer possible.
 *
 *  Mirror of D62's validateStep3Specifics: the same locale-parity
 *  rule applies (codes intentionally omitted from ERR_CODES so the
 *  inline localized banner is the single source of truth). */
function validateStep4ActionSpecifics(
  actionRaw: Action | string | null,
  params: URLSearchParams,
): Step4ErrCode | null {
  if (!actionRaw || !(ALL_ACTIONS as readonly string[]).includes(actionRaw)) {
    // Unknown raw value (hand-rolled POST, broken Edit-jump). The
    // radio is required so Step 4's first guard already refuses;
    // returning null lets the standard `pick_action`-style fallback
    // path in advanceWizard surface a precise message if needed.
    return null
  }
  const action = actionRaw as Action
  switch (action) {
    case "inject_context": {
      const template = (params.get("injectTemplate") ?? "").trim()
      return template ? null : "missing_template"
    }
    case "run_command": {
      const body = (params.get("runCommandBody") ?? "").trim()
      const scriptId = (params.get("runCommandScriptId") ?? "").trim()
      // The Step 4b mode <select> picks ONE lane (inline vs attach),
      // but both fields ride on the URL state (Edit-jump round-trip).
      // We only refuse the advance when BOTH are empty so the
      // operator who attached a script via the upload widget but
      // never typed a body can still advance.
      return body || scriptId ? null : "missing_command_or_script"
    }
    case "input_rewrite": {
      const kindRaw = (params.get("rewriterKind") ?? "").trim()
      if (!(ALL_REWRITER_KINDS as readonly string[]).includes(kindRaw)) {
        // Operator picked input_rewrite but the kind <select> never
        // landed in FormData (scripted POST, broken Edit-jump). Treat
        // as missing rewriter config so the inline banner points the
        // operator at the rewriter editor. We pick the prefix_strip
        // code because the rewriter editor lands there by default
        // (kindPick = state.rewriterKind ?? "prefix_strip").
        return "missing_rewriter_prefix"
      }
      const kind = kindRaw as RewriterKind
      switch (kind) {
        case "prefix_strip": {
          // D68 follow-up (P2): trim before truthiness so a
          // whitespace-only prefix does not silently pass the gate
          // and then bounce back at saveWizard with an opaque
          // `invalid_input`. Mirrors the trim discipline of the
          // inject_context and run_command branches above.
          const prefix = (params.get("rewriterPrefix") ?? "").trim()
          return prefix ? null : "missing_rewriter_prefix"
        }
        case "scheme_force": {
          const from = (params.get("rewriterFrom") ?? "").trim()
          const to = (params.get("rewriterTo") ?? "").trim()
          return from && to ? null : "missing_rewriter_scheme"
        }
        case "regex_substitute": {
          const pattern = (params.get("rewriterPattern") ?? "").trim()
          return pattern ? null : "missing_rewriter_pattern"
        }
        default: {
          // D68 follow-up: exhaustive over RewriterKind. Adding a
          // new kind to the union without a case here fails at
          // build time, no silent fallthrough.
          const _exhaustive: never = kind
          return _exhaustive
        }
      }
    }
    // Sub-form-less actions: pass the advance through. Listed
    // explicitly so the intent is documented in code rather than
    // implicit in a permissive `default: return null`, and so the
    // exhaustiveness check below catches any future archetype that
    // forgets to wire its own gate.
    case "block":
    case "ask":
    case "audit":
    case "strip":
      return null
    default: {
      const _exhaustive: never = action
      return _exhaustive
    }
  }
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

  // D57f-1: inject_context branches BEFORE the matcher / requires
  // pipeline because ContextInjectionPolicy has its own (event,
  // matcher, template) shape — no requires list, no gate_binary. We
  // still honor lifecycle, but the matcher collapses to wildcard
  // (the wizard surfaces inject_context as a per-lifecycle archetype;
  // a per-tool injection would need a different authoring flow).
  if (action === "inject_context") {
    // P2 follow-up: reuse the already-resolved `lifecycle`; the
    // duplicated `lifecycleRawInj`/`lifecycleInj` block added no
    // safety and risked diverging from the parent block's coercion.
    // Also run the matrix-action guard before the matcher pipeline
    // so a future ACTIONS_BY_LIFECYCLE narrowing of inject_context
    // catches here instead of silently persisting an illegal pair.
    if (!allowedActionsForCombination(lifecycle, undefined).includes("inject_context")) {
      redirect("/policies/new?mode=guided&step=4&err=invalid_input"); return
    }
    // D59: refuse the four lifecycles whose hookSpecificOutput shape
    // ignores additionalContext at runtime (Elicitation,
    // ElicitationResult, WorktreeCreate, MessageDisplay). The cloud's
    // ContextInjectionPolicy.validate() is the canonical refusal; this
    // dashboard-side guard fires the same redirect path so the
    // operator lands back on Step 4 with the disabled-card tooltip
    // visible instead of round-tripping through a generic 4xx flash.
    if (!lifecycleAllowsInjectContext(lifecycle)) {
      redirect("/policies/new?mode=guided&step=4&err=invalid_input"); return
    }
    const eventInj = LIFECYCLE_TO_EVENT[lifecycle]
    const template = String(formData.get("injectTemplate") ?? "").trim()
    if (!template) {
      redirect("/policies/new?mode=guided&step=4&err=invalid_input"); return
    }
    // P2 follow-up: mirror the IR's 16000-char cap so the operator
    // lands on the right step with a precise error code instead of
    // a generic 4xx flash from the cloud. Catches the "scripted POST
    // bypasses the textarea's maxLength" attack surface and the
    // browser-trimmed paste case the textarea silently drops.
    if (template.length > 16_000) {
      redirect("/policies/new?mode=guided&step=4&err=template_too_long"); return
    }
    const idInj = String(formData.get("id") ?? "").trim()
    if (!idInj) {
      redirect("/policies/new?mode=guided&step=5&err=invalid_input"); return
    }
    // P2 follow-up (ux-internal-leak): the previous fallback
    // `Inject context on ${eventInj}` leaked the raw CC event name
    // ("PreToolUse", "UserPromptSubmit", etc.) into the dashboard's
    // policy description. AGENTS.md mandates "never expose internal
    // terms" on NL/conversational surfaces; the LIFECYCLE_LABEL_*
    // tables exist precisely to render lifecycle slugs in human
    // terms. Mirror plainSummary's behavior so the description and
    // the summary stay aligned.
    const { locale: actionLocale } = await getT()
    const fallbackLifecycleLabel =
      actionLocale === "ko"
        ? LIFECYCLE_LABEL_KO[lifecycle]
        : LIFECYCLE_LABEL_EN[lifecycle]
    const descriptionInj = String(formData.get("description") ?? "").trim()
      || (actionLocale === "ko"
            ? `${fallbackLifecycleLabel}, 모델 컨텍스트에 텍스트 주입`
            : `Inject context ${fallbackLifecycleLabel}`)
    // Tool scope: when the lifecycle carries a tool context the
    // operator's pick rides through; everything else collapses to
    // wildcard, matching deriveMatcher's behavior on evidence flows.
    let matcherInj = "*"
    if (lifecycleHasToolScope(lifecycle)) {
      const scope = String(formData.get("toolScope") ?? "").trim()
      if (scope && scope !== "*") {
        const first = scope.split(",").map((s) => s.trim()).filter(Boolean)[0] ?? scope
        matcherInj = first || "*"
      }
    }
    const ctxDraft: ContextInjectionDraft = {
      type: "context_injection",
      id: idInj,
      description: descriptionInj,
      version: "0.1",
      event: eventInj,
      matcher: matcherInj,
      template,
    }
    const sourceInj = String(formData.get("source") ?? "org")
    await persistDraft(ctxDraft, sourceInj, _parsePackIds(formData))
    return
  }

  // D57f-2: input_rewrite branches BEFORE the matcher / requires
  // pipeline because InputRewritePolicy has its own (trigger.matcher,
  // rewriter spec) shape. The cloud's validate() is the canonical
  // gate; we surface its 4xx via the redirect path.
  if (action === "input_rewrite") {
    // P0 follow-up: read the toolScope FIRST so the matrix legality
    // check sees the right matcher class. The previous code passed
    // `undefined` as the scope, which `matcherClassForToolScope`
    // resolves to `"wildcard"` — and the matrix intentionally does
    // NOT legalize input_rewrite on the wildcard column (rewriters
    // target a specific field of a specific tool family). Result:
    // every guided submission of input_rewrite, including the
    // legitimate (before_tool_use, Bash, prefix_strip) happy path,
    // was bounced to Step 4 with `err=invalid_input` and never
    // reached `persistDraft`. The inject_context branch above gets
    // away with the same `undefined` shape only because
    // inject_context IS in the wildcard column.
    //
    // The downstream "wildcard is refused" guard at the
    // `!matcherIr || matcherIr === "*"` line is the right place to
    // refuse a missing scope; the matrix check just needs the real
    // scope so the action is judged against the matcher class the
    // operator actually picked.
    const rawScope = String(formData.get("toolScope") ?? "").trim()
    let matcherIr = rawScope
    if (rawScope.includes(",")) {
      matcherIr = rawScope.split(",").map((s) => s.trim()).filter(Boolean)[0] ?? ""
    }
    if (!allowedActionsForCombination(lifecycle, matcherIr).includes("input_rewrite")) {
      redirect("/policies/new?mode=guided&step=4&err=invalid_input"); return
    }
    if (!matcherIr || matcherIr === "*") {
      redirect("/policies/new?mode=guided&step=2&err=invalid_input"); return
    }
    const idIr = String(formData.get("id") ?? "").trim()
    if (!idIr) {
      redirect("/policies/new?mode=guided&step=5&err=invalid_input"); return
    }
    const rewriterKindRaw = String(formData.get("rewriterKind") ?? "")
    if (
      rewriterKindRaw !== "prefix_strip" &&
      rewriterKindRaw !== "scheme_force" &&
      rewriterKindRaw !== "regex_substitute"
    ) {
      redirect("/policies/new?mode=guided&step=4&err=invalid_input"); return
    }
    const fieldName = String(formData.get("rewriterField") ?? "").trim()
    if (!fieldName || !/^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(fieldName)) {
      redirect("/policies/new?mode=guided&step=4&err=invalid_input"); return
    }
    let cfg: Record<string, unknown>
    if (rewriterKindRaw === "prefix_strip") {
      const prefix = String(formData.get("rewriterPrefix") ?? "")
      if (!prefix) {
        redirect("/policies/new?mode=guided&step=4&err=invalid_input"); return
      }
      if (prefix.length > 2000) {
        redirect("/policies/new?mode=guided&step=4&err=invalid_input"); return
      }
      cfg = {
        field: fieldName,
        prefix,
        strip_repeat: String(formData.get("rewriterStripRepeat") ?? "") === "true",
      }
    } else if (rewriterKindRaw === "scheme_force") {
      const fromVal = String(formData.get("rewriterFrom") ?? "")
      const toVal = String(formData.get("rewriterTo") ?? "")
      if (!fromVal || !toVal || fromVal.length > 2000 || toVal.length > 2000) {
        redirect("/policies/new?mode=guided&step=4&err=invalid_input"); return
      }
      cfg = { field: fieldName, from: fromVal, to: toVal }
    } else {
      const pattern = String(formData.get("rewriterPattern") ?? "")
      const replacement = String(formData.get("rewriterReplacement") ?? "")
      if (!pattern || pattern.length > 2000 || replacement.length > 2000) {
        redirect("/policies/new?mode=guided&step=4&err=invalid_input"); return
      }
      const countRaw = String(formData.get("rewriterCount") ?? "0").trim()
      const count = Number.parseInt(countRaw || "0", 10)
      if (!Number.isFinite(count) || count < 0 || count > 1000) {
        redirect("/policies/new?mode=guided&step=4&err=invalid_input"); return
      }
      cfg = { field: fieldName, pattern, replacement, count }
    }
    const { locale: actionLocaleIr } = await getT()
    const fallbackLifecycleLabelIr =
      actionLocaleIr === "ko"
        ? LIFECYCLE_LABEL_KO[lifecycle]
        : LIFECYCLE_LABEL_EN[lifecycle]
    const descriptionIr = String(formData.get("description") ?? "").trim()
      || (actionLocaleIr === "ko"
            ? `${fallbackLifecycleLabelIr}, 도구 입력 재작성 (${matcherIr})`
            : `Rewrite tool input ${fallbackLifecycleLabelIr} (${matcherIr})`)
    const draftIr: InputRewriteDraft = {
      type: "input_rewrite",
      id: idIr,
      description: descriptionIr,
      version: "0.1",
      trigger: { host: "claude-code", event: "PreToolUse", matcher: matcherIr },
      rewriter: {
        kind: rewriterKindRaw,
        config: cfg,
      },
    }
    const sourceIr = String(formData.get("source") ?? "org")
    await persistDraft(draftIr, sourceIr, _parsePackIds(formData))
    return
  }

  // D63: run_command branches BEFORE the matcher / requires pipeline
  // because RunCommandPolicy has its own (runtime, command/script,
  // args, timeout, fail_closed) shape. The cloud's validate() is the
  // canonical refusal (exactly-one-of, runtime literal, arg caps).
  if (action === "run_command") {
    const eventRc = LIFECYCLE_TO_EVENT[lifecycle]
    if (!eventRc) {
      redirect("/policies/new?mode=guided&step=4&err=invalid_input"); return
    }
    const rcMode = String(formData.get("runCommandMode") ?? "inline")
    const runtimeRaw = String(formData.get("runCommandRuntime") ?? "bash")
    const runtimeRc: "bash" | "python3" | "node" =
      runtimeRaw === "python3" || runtimeRaw === "node"
        ? runtimeRaw
        : "bash"
    let commandRc = ""
    let scriptPathRc = ""
    if (rcMode === "attach") {
      scriptPathRc = String(formData.get("runCommandScriptId") ?? "").trim()
      if (!scriptPathRc) {
        redirect("/policies/new?mode=guided&step=4&err=invalid_input"); return
      }
    } else {
      commandRc = String(formData.get("runCommandBody") ?? "").trim()
      if (!commandRc) {
        redirect("/policies/new?mode=guided&step=4&err=invalid_input"); return
      }
      if (commandRc.length > 4_000) {
        redirect("/policies/new?mode=guided&step=4&err=invalid_input"); return
      }
    }
    const argsRaw = String(formData.get("runCommandArgs") ?? "")
    // D63 review (P2 validation-asymmetry): also enforce the cloud's
    // per-arg 256-char cap on the client so a long inline arg doesn't
    // make it past the dashboard only to 4xx at the cloud's IR
    // validate(). Slice each entry before sending so the operator
    // gets a clean save.
    const argsRc = argsRaw
      .split(",")
      .map((s) => s.trim().slice(0, 256))
      .filter((s) => s.length > 0)
      .slice(0, 16)
    const timeoutRaw = String(formData.get("runCommandTimeoutMs") ?? "5000")
    let timeoutMsRc = Number.parseInt(timeoutRaw, 10)
    if (!Number.isFinite(timeoutMsRc)) timeoutMsRc = 5000
    timeoutMsRc = Math.max(100, Math.min(30_000, timeoutMsRc))
    const failClosedRc =
      String(formData.get("runCommandFailClosed") ?? "") === "true"
    let matcherRc = "*"
    if (lifecycleHasToolScope(lifecycle)) {
      const scope = String(formData.get("toolScope") ?? "").trim()
      if (scope && scope !== "*") {
        const first = scope.split(",").map((s) => s.trim()).filter(Boolean)[0] ?? scope
        matcherRc = first || "*"
      }
    }
    const idRc = String(formData.get("id") ?? "").trim()
    if (!idRc) {
      redirect("/policies/new?mode=guided&step=5&err=invalid_input"); return
    }
    const { locale: actionLocaleRc } = await getT()
    const fallbackLifecycleLabelRc =
      actionLocaleRc === "ko"
        ? LIFECYCLE_LABEL_KO[lifecycle]
        : LIFECYCLE_LABEL_EN[lifecycle]
    const descriptionRc = String(formData.get("description") ?? "").trim()
      || (actionLocaleRc === "ko"
            ? `${fallbackLifecycleLabelRc}, 명령 실행`
            : `Run a command ${fallbackLifecycleLabelRc}`)
    const draftRc: RunCommandDraftPersist = {
      type: "run_command",
      id: idRc,
      description: descriptionRc,
      version: "0.1",
      trigger: { host: "claude-code", event: eventRc, matcher: matcherRc },
      runtime: runtimeRc,
      command: commandRc,
      script_path: scriptPathRc,
      args: argsRc,
      timeout_ms: timeoutMsRc,
      fail_closed: failClosedRc,
    }
    const sourceRc = String(formData.get("source") ?? "org")
    await persistDraft(draftRc, sourceRc, _parsePackIds(formData))
    return
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
  // D57e P1: prune evidenceRefs at the save seam too. The
  // GuidedWizard state-build prune already filters out cross-lifecycle
  // refs before HiddenState re-serializes them, but a server action
  // accepts arbitrary FormData (a hand-rolled POST, a stale tab that
  // never re-rendered, or the per-step `<input type=hidden
  // name=evidence_refs>` riding through from an earlier step where the
  // picker was hidden). Re-running the same `verifierFiresOnLifecycle`
  // filter here is the canonical defense — without it the IR can still
  // persist a `requires:[{kind:'step', step:<dropped>, verdict:'pass'}]`
  // that the runtime never fires.
  const _rawEvidenceRefs = (formData.getAll("evidence_ref") as string[])
    .map((v) => v.trim()).filter(Boolean)
    .concat((String(formData.get("evidence_refs") ?? ""))
      .split(",").map((s) => s.trim()).filter(Boolean))
  const _ccEvent = LIFECYCLE_TO_EVENT[lifecycle]
  const _evidenceRefsKept = _rawEvidenceRefs.filter((s) =>
    verifierFiresOnLifecycle(s, _ccEvent),
  )
  const state: WizardState = {
    lifecycle,
    toolScope,
    conditionKind,
    fetchDomain: String(formData.get("fetchDomain") ?? "").trim() || undefined,
    allowlist: String(formData.get("allowlist") ?? "").trim() || undefined,
    pattern: String(formData.get("pattern") ?? "").trim() || undefined,
    // D82c / D80: regex condition picker, chip-picked or default-typed
    // field path the runtime should run the pattern against. The picker
    // is now FieldPathSelect (a custom listbox + hidden input), not a
    // native <select>; the `initialValue` prop (computed as
    // state.regexFieldPath ?? defaultRegexFieldFor(state) ??
    // payloadFields[0]?.path ?? "") seeds the controlled hidden input
    // so FormData.get("regexFieldPath") carries the default through
    // even when the operator never opens the listbox.
    regexFieldPath: String(formData.get("regexFieldPath") ?? "").trim() || undefined,
    llmCriterion: String(formData.get("llmCriterion") ?? "").trim() || undefined,
    evidenceRefs: _evidenceRefsKept,
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
  // D57f-1 / D57f-2: inject_context + input_rewrite have their own
  // early-return branches above, so here action is narrowed to block /
  // ask / audit / strip — all of which the evidence draft accepts
  // (strip after the audit fallback).
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
  await persistDraft(draft, source, _parsePackIds(formData))
}

/** D62 follow-up: defense-in-depth at the save seam. Operators who
 *  reach saveWizard with empty specifics (e.g., via a deep link, a
 *  browser back-forward, or any future flow that bypasses Step 3's
 *  advance gate) previously hit the generic "invalid_input" banner
 *  with no inline pointer. We now delegate to validateStep3Specifics
 *  so the precise per-kind code is returned and the Step 3 redirect
 *  surfaces the same inline localized banner the advance gate uses.
 *  Single source of truth: both gates share the same per-kind switch.
 */
function validateSpecifics(s: WizardState): Step3ErrCode | null {
  const params = new URLSearchParams()
  if (s.fetchDomain) params.set("fetchDomain", s.fetchDomain)
  if (s.allowlist) params.set("allowlist", s.allowlist)
  if (s.pattern) params.set("pattern", s.pattern)
  if (s.llmCriterion) params.set("llmCriterion", s.llmCriterion)
  if (s.shaclTtl) params.set("shaclTtl", s.shaclTtl)
  return validateStep3Specifics(
    s.conditionKind ?? null,
    params,
    s.evidenceRefs ?? [],
  )
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
  // D57f-1: a context_injection IR (no trigger, no requires; carries
  // `event`+`matcher`+`template` directly) round-trips into a wizard
  // state with action=inject_context and the inline editor fields
  // pre-filled. The detection uses the raw `type` field on the dict
  // (PolicyDraft narrows to evidence shape, so we cast through unknown
  // to read the discriminator).
  const rawType = (ir as unknown as { type?: string }).type
  if (rawType === "context_injection") {
    const ev = (ir as unknown as { event?: string }).event ?? ""
    const tpl = (ir as unknown as { template?: string }).template ?? ""
    const matcherRaw = (ir as unknown as { matcher?: string }).matcher ?? "*"
    const lifecycleCi: Lifecycle | undefined = EVENT_TO_LIFECYCLE[ev]
    // P2 follow-up (wizard-flow): mirror the evidence-shape branch's
    // alternation drop. Without this, a context_injection IR with
    // matcher='Bash|Edit' would round-trip into toolScope='Bash|Edit'
    // and silently collapse to 'Bash' downstream (the GuidedWizard
    // state-build seam splits on '|'), with no banner explaining the
    // loss. Surface `_droppedAlternation` so Step 2 renders the
    // already-existing "we trimmed your alternation" banner.
    let toolScopeCi: string | undefined
    let droppedAlternationCi: string | undefined
    if (lifecycleHasToolScope(lifecycleCi) && matcherRaw && matcherRaw !== "*") {
      if (matcherRaw.includes("|")) {
        const parts = matcherRaw.split("|").map((s) => s.trim()).filter(Boolean)
        toolScopeCi = parts[0] ?? undefined
        if (parts.length > 1) droppedAlternationCi = matcherRaw
      } else {
        toolScopeCi = matcherRaw
      }
    }
    return {
      lifecycle: lifecycleCi,
      toolScope: toolScopeCi,
      conditionKind: "none",
      action: "inject_context",
      injectTemplate: tpl,
      id: (ir.id ?? "").toString() || undefined,
      description: ir.description?.toString() || undefined,
      _droppedAlternation: droppedAlternationCi,
    }
  }
  // D57f-2: input_rewrite IR round-trip. The persisted shape carries
  // trigger.{event,matcher} and a rewriter spec; we map back into the
  // wizard's per-kind fields so the operator can re-author without
  // hand-editing the raw IR.
  if (rawType === "input_rewrite") {
    const trig = (ir as unknown as { trigger?: { matcher?: string } }).trigger
    const matcherRaw = trig?.matcher ?? ""
    const rewriter = (ir as unknown as {
      rewriter?: { kind?: string; config?: Record<string, unknown> }
    }).rewriter
    const kindRaw = rewriter?.kind
    const cfg = rewriter?.config ?? {}
    const kindOk =
      kindRaw === "prefix_strip" || kindRaw === "scheme_force" ||
      kindRaw === "regex_substitute"
    const field = typeof cfg.field === "string" ? cfg.field : ""
    let toolScopeIr: string | undefined
    if (matcherRaw && matcherRaw !== "*") {
      if (matcherRaw.includes("|")) {
        toolScopeIr = matcherRaw.split("|").map((s) => s.trim())
          .filter(Boolean)[0]
      } else {
        toolScopeIr = matcherRaw
      }
    }
    return {
      lifecycle: "before_tool_use",
      toolScope: toolScopeIr,
      conditionKind: "none",
      action: "input_rewrite",
      rewriterKind: kindOk ? (kindRaw as WizardState["rewriterKind"]) : "prefix_strip",
      rewriterField: field || undefined,
      rewriterPrefix: typeof cfg.prefix === "string" ? cfg.prefix : undefined,
      rewriterStripRepeat: cfg.strip_repeat === true ? "true" : "false",
      rewriterFrom: typeof cfg.from === "string" ? cfg.from : undefined,
      rewriterTo: typeof cfg.to === "string" ? cfg.to : undefined,
      rewriterPattern: typeof cfg.pattern === "string" ? cfg.pattern : undefined,
      rewriterReplacement: typeof cfg.replacement === "string"
        ? cfg.replacement : undefined,
      rewriterCount: typeof cfg.count === "number"
        ? String(cfg.count) : undefined,
      id: (ir.id ?? "").toString() || undefined,
      description: ir.description?.toString() || undefined,
    }
  }
  // event -> lifecycle. D56c covered the original 8 hooks; D58
  // extends to the full 30-event CC surface. Anything outside the
  // recognized set degrades to undefined and Step 1's default
  // (`before_tool_use`) takes over. We compute the reverse map at
  // module load (LIFECYCLE_TO_EVENT is the forward direction) so a
  // future event addition only touches one table.
  const lifecycle: Lifecycle | undefined =
    EVENT_TO_LIFECYCLE[ir.trigger?.event ?? ""] ?? undefined

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
  // D82c fix: round-trip regex field_path through Edit. Without this,
  // a saved policy reloaded into the wizard would lose its scoping
  // choice and silently regress to the legacy whole-payload behaviour.
  let regexFieldPath: string | undefined
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
        // D82c fix: thread field_path back into the wizard state.
        // Unknown / legacy rows simply omit the key, which leaves
        // regexFieldPath undefined — Step 3 then shows its
        // lifecycle-aware default.
        if ("field_path" in r && typeof r.field_path === "string" && r.field_path) {
          regexFieldPath = r.field_path
        }
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
      regexFieldPath = undefined
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
    regexFieldPath,
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
          locale={locale === "ko" ? "ko" : "en"}
          // D57g hotfix: AdvancedAuthoring owns the HandoffLink so it
          // can read the live PolicyBuilder draft at click time.
          // Suppress AuthoringShell's own copy by passing the
          // "conversational" sentinel (the shell renders no link).
          handoffOrigin="conversational"
          modeTitle={t("newPolicy.mode.advancedAuthoring")}
          info={{
            tone: "warn",
            title: t("newPolicy.advanced.info.title"),
            body: t("newPolicy.advanced.info.body"),
          }}
        >
          <Card>
            {/* Q90 fix: the dry-run slot used to be a render-prop literal
                built here in the server component, which crashed at render
                with "Functions cannot be passed directly to Client
                Components..." (digest 1331850167) because AdvancedAuthoring
                is a client component. The slot is now defined inside
                AdvancedAuthoring so the function literal lives on the
                client side. */}
            <AdvancedAuthoring
              locale={locale === "ko" ? "ko" : "en"}
              saveAction={saveAdvanced}
              initial={initialDraft}
              wiredSteps={wiredSteps.map((w) => w.step)}
              vendorSteps={vendorSteps}
              packCentric={isPackCentricEnabled()}
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

      {mode === "conversational" && (
        <AuthoringShell
          t={t}
          locale={locale === "ko" ? "ko" : "en"}
          handoffOrigin="conversational"
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
            initialSeed={searchParams.seed ?? ""}
            packCentric={isPackCentricEnabled()}
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
            testId="picker-card-guided"
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
  t, modeTitle, info, children, locale: _locale, handoffOrigin: _handoffOrigin,
}: {
  modeTitle: string
  info: { tone: "info" | "warn"; title: string; body: string }
  children: React.ReactNode
  /** D57g: previously controlled the "Continue in conversation" link
   *  in the header chrome. Now a no-op pin (see comment in the
   *  render block) — the AdvancedAuthoring wrapper owns the
   *  HandoffLink so it can read the live PolicyBuilder draft via a
   *  ref. Prop preserved so call sites do not silently lose the
   *  "we deliberately do NOT render a handoff link here" signal. */
  locale?: "ko" | "en"
  handoffOrigin?: "advanced" | "conversational"
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
          {/* D57g hotfix: the AuthoringShell HandoffLink was rendered
           *  WITHOUT `getDraft`, so clicking it from the advanced (raw
           *  editor) mode silently dropped the operator's entire
           *  authored draft (the draft IR lives in PolicyBuilder
           *  client state, not on the URL). The fix moves the
           *  HandoffLink into AdvancedAuthoring, which holds a live
           *  draft ref and forwards `getDraft` at click time. We
           *  intentionally keep the `handoffOrigin` prop on this
           *  shell as the deprecation pin: every call site now
           *  passes "conversational" so no link renders here, but
           *  surfacing the gap loudly via the prop name makes a
           *  future re-introduction obvious. */}
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
  if (state.regexFieldPath) params.set("regexFieldPath", state.regexFieldPath)
  if (state.llmCriterion) params.set("llmCriterion", state.llmCriterion)
  if (state.evidenceRefs && state.evidenceRefs.length > 0) {
    params.set("evidence_refs", state.evidenceRefs.join(","))
  }
  if (state.shaclTtl) params.set("shaclTtl", state.shaclTtl)
  if (state.action) params.set("action", state.action)
  if (state.injectTemplate) params.set("injectTemplate", state.injectTemplate)
  if (state.injectLabelKo) params.set("injectLabelKo", state.injectLabelKo)
  if (state.injectLabelEn) params.set("injectLabelEn", state.injectLabelEn)
  // D57f-2: rewriter fields ride through URL state so the wizard's
  // Edit-jump round-trip preserves what the operator typed in Step 4b.
  if (state.rewriterKind) params.set("rewriterKind", state.rewriterKind)
  if (state.rewriterField) params.set("rewriterField", state.rewriterField)
  if (state.rewriterPrefix !== undefined) params.set("rewriterPrefix", state.rewriterPrefix)
  if (state.rewriterStripRepeat) params.set("rewriterStripRepeat", state.rewriterStripRepeat)
  if (state.rewriterFrom !== undefined) params.set("rewriterFrom", state.rewriterFrom)
  if (state.rewriterTo !== undefined) params.set("rewriterTo", state.rewriterTo)
  if (state.rewriterPattern !== undefined) params.set("rewriterPattern", state.rewriterPattern)
  if (state.rewriterReplacement !== undefined) params.set("rewriterReplacement", state.rewriterReplacement)
  if (state.rewriterCount !== undefined) params.set("rewriterCount", state.rewriterCount)
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
      {state.regexFieldPath && <input type="hidden" name="regexFieldPath" value={state.regexFieldPath} />}
      {state.llmCriterion && <input type="hidden" name="llmCriterion" value={state.llmCriterion} />}
      {state.evidenceRefs && state.evidenceRefs.length > 0 && (
        <input type="hidden" name="evidence_refs" value={state.evidenceRefs.join(",")} />
      )}
      {state.shaclTtl && <input type="hidden" name="shaclTtl" value={state.shaclTtl} />}
      {state.action && <input type="hidden" name="action" value={state.action} />}
      {state.injectTemplate && <input type="hidden" name="injectTemplate" value={state.injectTemplate} />}
      {state.injectLabelKo && <input type="hidden" name="injectLabelKo" value={state.injectLabelKo} />}
      {state.injectLabelEn && <input type="hidden" name="injectLabelEn" value={state.injectLabelEn} />}
      {state.rewriterKind && <input type="hidden" name="rewriterKind" value={state.rewriterKind} />}
      {state.rewriterField && <input type="hidden" name="rewriterField" value={state.rewriterField} />}
      {state.rewriterPrefix !== undefined && <input type="hidden" name="rewriterPrefix" value={state.rewriterPrefix} />}
      {state.rewriterStripRepeat && <input type="hidden" name="rewriterStripRepeat" value={state.rewriterStripRepeat} />}
      {state.rewriterFrom !== undefined && <input type="hidden" name="rewriterFrom" value={state.rewriterFrom} />}
      {state.rewriterTo !== undefined && <input type="hidden" name="rewriterTo" value={state.rewriterTo} />}
      {state.rewriterPattern !== undefined && <input type="hidden" name="rewriterPattern" value={state.rewriterPattern} />}
      {state.rewriterReplacement !== undefined && <input type="hidden" name="rewriterReplacement" value={state.rewriterReplacement} />}
      {state.rewriterCount !== undefined && <input type="hidden" name="rewriterCount" value={state.rewriterCount} />}
      {/* D63: run_command fields. We keep them as separate hidden
       *  inputs (no JSON blob) so the existing per-field FormData
       *  reader in saveWizard continues to work without parsing. */}
      {state.runCommandMode && <input type="hidden" name="runCommandMode" value={state.runCommandMode} />}
      {state.runCommandRuntime && <input type="hidden" name="runCommandRuntime" value={state.runCommandRuntime} />}
      {state.runCommandBody !== undefined && <input type="hidden" name="runCommandBody" value={state.runCommandBody} />}
      {state.runCommandScriptId && <input type="hidden" name="runCommandScriptId" value={state.runCommandScriptId} />}
      {state.runCommandScriptName && <input type="hidden" name="runCommandScriptName" value={state.runCommandScriptName} />}
      {state.runCommandArgs !== undefined && <input type="hidden" name="runCommandArgs" value={state.runCommandArgs} />}
      {state.runCommandTimeoutMs && <input type="hidden" name="runCommandTimeoutMs" value={state.runCommandTimeoutMs} />}
      {state.runCommandFailClosed && <input type="hidden" name="runCommandFailClosed" value={state.runCommandFailClosed} />}
      {state.id && <input type="hidden" name="id" value={state.id} />}
      {state.description && <input type="hidden" name="description" value={state.description} />}
    </>
  )
}

/** D82a: previous LIVE step, honouring the same skip rules GuidedWizard
 *  uses to advance forward.
 *
 *  The pre-D82a Back link inside StepShell pointed at `step - 1`
 *  unconditionally, which broke when GuidedWizard auto-skipped that
 *  step forward. Concretely: from Step 4 with action=inject_context,
 *  the back arrow targeted Step 3 which auto-skipped right back to
 *  Step 4 — the operator saw nothing happen ("Step 4 Back is broken"
 *  per the install review). Walking BACKWARD through the same skip
 *  table keeps the Back arrow honest.
 *
 *  D82a follow-up: the implementation lives in `./wizard-nav.ts` as a
 *  pure helper so wizard-wiring.test.ts can import it and assert
 *  table-driven (state, current) -> expected tuples directly. The
 *  prior revision only source-text-grepped the function body, which
 *  let math regressions land silently. Re-exported here (the top-of-
 *  file import does the actual binding) so the existing call sites
 *  inside this file keep working untouched. */

function WizardHeader({
  t, step, total, locale, state, searchParams,
}: {
  step: number; total: number
  /** D57g: forwarded to the HandoffLink so the chat label renders in
   *  the operator's preferred language. */
  locale: "ko" | "en"
  /** D82a: needed so the top-left Back arrow can target the previous
   *  LIVE step (honoring the same skip rules GuidedWizard uses to
   *  advance forward). */
  state: WizardState
  /** D82b: pass-through of the wizard's `searchParams` so the Back
   *  link rebuilds the URL from the operator's actual URL state
   *  rather than the `state` projection, which silently dropped
   *  fields that `buildWizardHref` does not serialize (run_command*
   *  in particular). The install review reported "Back from Step 4
   *  stays on Step 4" — the root cause was the projection: rebuilding
   *  from `state` could emit a URL identical to the current one for
   *  the no-op fields. Routing the Back link through
   *  `buildBackHrefFromSearchParams` flips only `step` and preserves
   *  every other param verbatim, so the URL always differs and the
   *  navigation always fires. */
  searchParams: Record<string, string | undefined>
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  // D82a: two top-left affordances side by side — Home (pick different
  // authoring mode) and Back (previous live step). Tab order is Home →
  // Back → wizard body → Next, so a keyboard operator on Step 4 can
  // reach Back with one Tab from Home and never see the broken legacy
  // Step 3 jump.
  // D82b: `backStep` still flows through `state` for the disabled-vs-
  // enabled rendering check, but the Back href is derived from
  // `searchParams` so every URL field — including run_command* which
  // `buildWizardHref` does not emit — rides through the Back nav.
  // D82c: pass the state-resolved action/lifecycle into the helper as
  // navOverrides so the skip math (and condition-side scrub) runs on
  // the same fields the visual `backStep` saw. Otherwise a draft-
  // prefill URL (?draft=<IR>&step=4) with state.action=inject_context
  // would render the Back link as "Step 2" (from `state`) while the
  // emitted URL pointed at "Step 3" (from `searchParams`, where
  // action is undefined), producing an infinite Back-Step-3-auto-skip-
  // to-4 loop on the next render.
  const backStep = previousLiveStep(state, step)
  const backHref = backStep != null
    ? buildBackHrefFromSearchParams(
        { ...searchParams, step: String(step) },
        { action: state.action, lifecycle: state.lifecycle },
      )
    : null
  return (
    <div className="flex items-center justify-between mb-6">
      <div className="flex items-center gap-1">
        {/* Home — returns the operator to the authoring-mode picker.
         *  Same target as the legacy "Pick different" text link; the
         *  icon-only treatment frees the row for the new Back arrow. */}
        <Link
          href="/policies/new"
          aria-label={t("newPolicy.wizard.nav.home.aria")}
          title={t("newPolicy.wizard.nav.home.tip")}
          data-testid="wizard-nav-home"
          className="inline-flex h-8 w-8 items-center justify-center rounded-md text-[var(--color-text-secondary)] hover:bg-black/[0.04] hover:text-[var(--color-text-primary)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]/40"
        >
          <HomeIcon className="h-4 w-4" />
        </Link>
        {/* Back — previous live step (honors skips). Disabled visually
         *  on the first effective step. The legacy bottom-left Back
         *  link in StepShell is gone (D82a). */}
        {backHref ? (
          <Link
            href={backHref}
            aria-label={t("newPolicy.wizard.nav.back.aria")}
            title={t("newPolicy.wizard.nav.back.tip")}
            data-testid="wizard-nav-back"
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-[var(--color-text-secondary)] hover:bg-black/[0.04] hover:text-[var(--color-text-primary)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]/40"
          >
            <ArrowLeftIcon className="h-4 w-4" />
          </Link>
        ) : (
          /* D82a follow-up: render the disabled Back as a real
           *  <button disabled> so keyboard focus order stays stable
           *  across steps (Step 1 included). A bare <span aria-disabled>
           *  is not focusable, so Tab on Step 1 skipped the Back slot
           *  entirely and NVDA/JAWS announced nothing — the operator
           *  silently lost their place in the Tab order. The disabled
           *  button still carries aria-label/title so AT users get the
           *  standard "dimmed, disabled button" affordance. */
          <button
            type="button"
            disabled
            aria-label={t("newPolicy.wizard.nav.back.aria")}
            title={t("newPolicy.wizard.nav.back.tip")}
            data-testid="wizard-nav-back-disabled"
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-[var(--color-text-tertiary)] opacity-40 cursor-not-allowed"
          >
            <ArrowLeftIcon className="h-4 w-4" />
          </button>
        )}
      </div>
      <div className="flex items-center gap-3">
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
        {/* D57g: handoff link sits to the right of the step pips so it
         *  is visible from every wizard step (1-6, including Step 6
         *  review). Reads URL state at click time and forwards to
         *  ?mode=conversational&seed=<...>. */}
        <HandoffLink
          locale={locale}
          origin={step === 6 ? "review" : "guided"}
          testId={`handoff-continue-in-chat-step${step}`}
        />
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
  // D57f-1: inject_context joins block / ask / audit / strip as a
  // legal action archetype. Step 4 surfaces it on every lifecycle;
  // saveWizard branches into the ContextInjectionPolicy compile target.
  const action = (["block", "ask", "audit", "strip", "inject_context", "input_rewrite", "run_command"] as const).includes(actionParam as Action)
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

  // D57e P1: prune evidenceRefs at the state-build seam so a stale
  // ref riding through on the URL (or a prebuilt Edit-jump) that
  // names a verifier the current lifecycle does not fire is dropped
  // BEFORE HiddenState re-serializes it and BEFORE saveWizard reads
  // it back from FormData. Without this prune the IR persists a
  // `requires:[{kind:'step', step:<dropped>, verdict:'pass'}]`
  // pointing at a verifier that will never fire, which the runtime
  // collapses to a vacuous pass. Symmetric to `_droppedConditionKind`
  // / `_droppedAlternation`. Surfaces the dropped names so Step 3 +
  // Step 6 can render a one-shot banner.
  const _resolvedLifecycle: Lifecycle | undefined =
    lifecycle ?? draftState?.lifecycle
  const _rawEvidenceRefs: string[] | undefined =
    evidenceRefs.length > 0 ? evidenceRefs : draftState?.evidenceRefs
  let _prunedEvidenceRefs: string[] | undefined = _rawEvidenceRefs
  let _droppedEvidenceRefs: string[] | undefined
  if (_rawEvidenceRefs && _resolvedLifecycle) {
    const _ccEvent = LIFECYCLE_TO_EVENT[_resolvedLifecycle]
    const _kept: string[] = []
    const _dropped: string[] = []
    for (const s of _rawEvidenceRefs) {
      if (verifierFiresOnLifecycle(s, _ccEvent)) _kept.push(s)
      else _dropped.push(s)
    }
    _prunedEvidenceRefs = _kept.length > 0 ? _kept : undefined
    if (_dropped.length > 0) _droppedEvidenceRefs = _dropped
  }

  const state: WizardState = {
    lifecycle: lifecycle ?? draftState?.lifecycle,
    toolScope: normalizedToolScope,
    conditionKind: conditionKind ?? draftState?.conditionKind,
    fetchDomain: searchParams.fetchDomain || draftState?.fetchDomain,
    allowlist: searchParams.allowlist || draftState?.allowlist,
    pattern: searchParams.pattern || draftState?.pattern,
    // D82c: regex condition's target field path. Migration: legacy URLs
    // and draft states arrive WITHOUT regexFieldPath set; the form's
    // <select> defaultValue lands "tool_response.output" so the round
    // trip is byte-stable for the most common after_tool_use case.
    regexFieldPath: searchParams.regexFieldPath || draftState?.regexFieldPath,
    llmCriterion: searchParams.llmCriterion || draftState?.llmCriterion,
    evidenceRefs: _prunedEvidenceRefs,
    shaclTtl: searchParams.shaclTtl || draftState?.shaclTtl,
    action: action ?? draftState?.action,
    // D57f-1: ContextInjectionPolicy fields ride on the URL state the
    // same way every other per-step field does, so Edit-jumps from
    // Step 6 back to Step 4b preserve the template + labels.
    injectTemplate: searchParams.injectTemplate || draftState?.injectTemplate,
    injectLabelKo: searchParams.injectLabelKo || draftState?.injectLabelKo,
    injectLabelEn: searchParams.injectLabelEn || draftState?.injectLabelEn,
    // D57f-2: rewriter fields. Note: rewriter kind is constrained at
    // save-time; here we just shuttle whatever was in the URL or the
    // prefilled draft. The Step 4b editor narrows to the legal set.
    rewriterKind: (searchParams.rewriterKind as WizardState["rewriterKind"]) || draftState?.rewriterKind,
    rewriterField: searchParams.rewriterField || draftState?.rewriterField,
    rewriterPrefix: searchParams.rewriterPrefix ?? draftState?.rewriterPrefix,
    rewriterStripRepeat: (searchParams.rewriterStripRepeat as WizardState["rewriterStripRepeat"]) || draftState?.rewriterStripRepeat,
    rewriterFrom: searchParams.rewriterFrom ?? draftState?.rewriterFrom,
    rewriterTo: searchParams.rewriterTo ?? draftState?.rewriterTo,
    rewriterPattern: searchParams.rewriterPattern ?? draftState?.rewriterPattern,
    rewriterReplacement: searchParams.rewriterReplacement ?? draftState?.rewriterReplacement,
    rewriterCount: searchParams.rewriterCount ?? draftState?.rewriterCount,
    // D63: run_command URL params. Round-tripped through HiddenState
    // so an Edit jump from Step 6 back to Step 4 preserves the body.
    runCommandMode: (searchParams.runCommandMode as WizardState["runCommandMode"]) || undefined,
    runCommandRuntime: (searchParams.runCommandRuntime as WizardState["runCommandRuntime"]) || undefined,
    runCommandBody: searchParams.runCommandBody ?? undefined,
    runCommandScriptId: searchParams.runCommandScriptId || undefined,
    runCommandScriptName: searchParams.runCommandScriptName || undefined,
    runCommandArgs: searchParams.runCommandArgs ?? undefined,
    runCommandTimeoutMs: searchParams.runCommandTimeoutMs || undefined,
    runCommandFailClosed: (searchParams.runCommandFailClosed as WizardState["runCommandFailClosed"]) || undefined,
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
    _droppedEvidenceRefs,
  }

  // D56c: every no-tool-context lifecycle auto-skips Step 2 (tool
  // scope is irrelevant when matcher is forced to wildcard).
  // D57f-1: when action=inject_context is already on the state (the
  // user picked it on Step 4 and then jumped back via an Edit link),
  // Step 3 has no condition surface to render — ContextInjectionPolicy
  // has no requires list. We skip Step 3 forward to Step 4 so the
  // operator lands on the action card + template editor instead of a
  // ghost condition picker that would be ignored at save time.
  let effectiveStep =
    step === 2 && state.lifecycle && !lifecycleHasToolScope(state.lifecycle)
      ? 3 : step
  if (effectiveStep === 3 && state.action === "inject_context"
      && lifecycleAllowsInjectContext(state.lifecycle)) {
    // D59: only auto-skip Step 3 when the chosen lifecycle still
    // surfaces inject_context as an active card. For the four
    // excluded lifecycles the operator lands on Step 4 with the
    // disabled-card tooltip visible so they can pivot to an audit
    // archetype without losing wizard progress.
    effectiveStep = 4
  }
  // D57f-2: same skip for input_rewrite — InputRewritePolicy has no
  // requires list; Step 3's condition picker would be a dead surface.
  if (effectiveStep === 3 && state.action === "input_rewrite") {
    effectiveStep = 4
  }
  // D63: same skip for run_command. RunCommandPolicy has no
  // requires list either — Step 3's condition picker is meaningless.
  if (effectiveStep === 3 && state.action === "run_command") {
    effectiveStep = 4
  }
  // P2 follow-up (wizard-state): scrub condition-side fields when
  // action=inject_context so a previously-authored
  // pattern/llmCriterion/shaclTtl/evidence_refs does not silently
  // resurrect if the operator changes their mind back to
  // block/audit. The inject_context branch in saveWizard ignores
  // these anyway, but a back-and-forth Edit-jump would otherwise
  // ferry stale condition state through the URL.
  if (state.action === "inject_context") {
    state.conditionKind = "none"
    state.pattern = undefined
    state.llmCriterion = undefined
    state.shaclTtl = undefined
    state.fetchDomain = undefined
    state.allowlist = undefined
    state.evidenceRefs = undefined
  }
  // D57f-2: same scrub for input_rewrite. The saveWizard branch for
  // input_rewrite ignores condition state; carrying it through the URL
  // would re-emerge if the operator switches back to block/audit.
  if (state.action === "input_rewrite") {
    state.conditionKind = "none"
    state.pattern = undefined
    state.llmCriterion = undefined
    state.shaclTtl = undefined
    state.fetchDomain = undefined
    state.allowlist = undefined
    state.evidenceRefs = undefined
  }
  // D63: same scrub for run_command.
  if (state.action === "run_command") {
    state.conditionKind = "none"
    state.pattern = undefined
    state.llmCriterion = undefined
    state.shaclTtl = undefined
    state.fetchDomain = undefined
    state.allowlist = undefined
    state.evidenceRefs = undefined
  }

  return (
    <div className="max-w-2xl mx-auto">
      <WizardHeader t={t} step={effectiveStep} total={WIZARD_TOTAL} locale={locale} state={state} searchParams={searchParams} />

      {effectiveStep === 1 && <Step1Lifecycle t={t} locale={locale} state={state} action={advanceAction} />}
      {effectiveStep === 2 && <Step2ToolScope t={t} locale={locale} state={state} action={advanceAction} />}
      {effectiveStep === 3 && <Step3Condition t={t} locale={locale} state={state} wiredSteps={wiredSteps} action={advanceAction} wizardErr={searchParams.err} />}
      {effectiveStep === 4 && <Step4Action t={t} locale={locale} state={state} action={advanceAction} wizardErr={searchParams.err} />}
      {effectiveStep === 5 && <Step5Naming t={t} state={state} action={advanceAction} />}
      {effectiveStep === 6 && <Step6Review t={t} locale={locale} state={state} action={saveAction} advanceAction={advanceAction} wiredSteps={wiredSteps} />}
    </div>
  )
}

function StepShell({
  heading, helper, children,
}: {
  /** D82a: the bottom-left Back link is gone (the top-left Back arrow
   *  in WizardHeader is now the only Back affordance and honours the
   *  forward skip rules). `prevHref` and `t` are deliberately NOT props
   *  here any more — no call site needs them, the previous revision
   *  kept them as silent dead props that misled readers into thinking
   *  the bottom-left Back was still wired. Reintroduce them only if a
   *  new bottom-left affordance ever reappears. */
  heading: string
  helper?: string
  children: React.ReactNode
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
    </div>
  )
}

/** D52e follow-up: optional action-archetype tone matches the
 * NlAuthoringGuide pill colors so the vocabulary (block=red, ask=amber,
 * audit=blue, strip=purple) is consistent end-to-end. The accent color
 * still wins when the card is selected so the "this is the picked one"
 * affordance reads first. */
type ActionTone = "block" | "ask" | "audit" | "strip" | "inject_context" | "input_rewrite" | "run_command"

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
    case "inject_context":
      // D57f-1: green tone matches "additive / additionalContext"
      // semantics. Distinct from audit's blue so the operator reads
      // the affordance as "we add to the model's view" not "we
      // observe and log."
      return "border-emerald-300 hover:border-emerald-400 peer-checked:border-[var(--color-accent)] peer-checked:bg-[var(--color-accent)]/[0.05]"
    case "input_rewrite":
      // D57f-2: indigo tone reads as "mutating but bounded" — distinct
      // from strip's purple (which means "remove from output").
      return "border-indigo-300 hover:border-indigo-400 peer-checked:border-[var(--color-accent)] peer-checked:bg-[var(--color-accent)]/[0.05]"
    case "run_command":
      // D63: slate tone reads as "executing a script" — neutral; the
      // copy + the dismissible warning callout do the heavy lifting.
      return "border-slate-400 hover:border-slate-500 peer-checked:border-[var(--color-accent)] peer-checked:bg-[var(--color-accent)]/[0.05]"
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

function NextButton({ label, testId }: { label: string; testId?: string }) {
  return (
    <button
      type="submit"
      data-testid={testId}
      className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-[var(--color-accent)] px-5 py-3 text-sm font-semibold text-white shadow-sm hover:bg-[var(--color-accent-hover)] disabled:cursor-not-allowed disabled:opacity-60 cursor-pointer transition-colors"
    >
      {label}
    </button>
  )
}

function FieldLabel({
  children,
  htmlFor,
  id,
}: {
  children: React.ReactNode
  /** Bind the label to a specific input by id (recommended for a11y). */
  htmlFor?: string
  /** id on the label element so callers can wire aria-labelledby. */
  id?: string
}) {
  // D71: emit a real <label> when bound to an input id so the
  // combobox + other wizard fields gain an accessible name. When the
  // caller doesn't pass htmlFor (legacy callsites in this file), we
  // fall back to a non-interactive <span> to preserve the prior
  // visual + layout behaviour byte-equivalently.
  const cls =
    "block text-xs font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)] mb-1.5"
  if (htmlFor) {
    return (
      <label id={id} htmlFor={htmlFor} className={cls}>
        {children}
      </label>
    )
  }
  return (
    <span id={id} className={cls}>
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
      sub: "컨텍스트 컴팩션 직전에 발동. payload 의 trigger (\"manual\" / \"auto\") 와 custom_instructions (`/compact <지시문>` 으로 입력한 운영자 문자열, 자동 컴팩션이면 빈 값) 를 evidence 체인 보존 / 정책 분기에 사용합니다.",
    },
    pre_final: {
      label: "에이전트 턴 종료 (Stop)",
      sub: "메인 에이전트 턴이 끝났을 때. 감사 전용 — “추가 정보 주입” 액션은 지원되지 않습니다.",
    },
    subagent_stop: {
      label: "서브에이전트 종료 (SubagentStop)",
      sub: "서브에이전트(Task) 호출이 끝났을 때. 결과 트랜스크립트 감사 용도. “추가 정보 주입” 은 SubagentStart 에서 하세요.",
    },
    session_start: {
      label: "세션 시작 (SessionStart)",
      sub: "세션이 시작·재개·초기화 될 때 발동. 감사 경계 마커로 사용합니다.",
    },
    session_end: {
      label: "세션 종료 (SessionEnd)",
      sub: "세션이 종료될 때 한 번 발동. 감사 경계 마커로 사용합니다. 세션이 닫히는 중이라 같은 세션의 다음 모델 턴이 없으므로 “추가 정보 주입” 액션은 지원되지 않습니다.",
    },
    // D58 → D79 (verified against CC 2.1.170 binary; payload fields
    // pinned in src/magi_cp/policy/payload_schemas.py).
    post_tool_use_failure: {
      label: "도구 실행 실패 (PostToolUseFailure)",
      sub: "도구 호출이 오류로 끝난 직후 발동. payload 에 tool_name, tool_input, tool_use_id, error, is_interrupt, duration_ms 가 들어옵니다.",
    },
    post_tool_batch: {
      label: "도구 배치 종료 (PostToolBatch)",
      sub: "한 턴의 모든 도구 호출이 끝난 직후 한 번 발동. payload 의 tool_calls 배열에 각 호출의 tool_name/tool_input/tool_response 가 순서대로 들어옵니다.",
    },
    permission_request: {
      label: "권한 요청 (PermissionRequest)",
      sub: "CC 가 사용자에게 권한 확인을 띄우기 직전. payload 에 tool_name, tool_input, permission_suggestions 가 있고, 정책 stdout 의 hookSpecificOutput.permissionRequestResult.behavior (\"allow\" / \"deny\") 로 결정을 덮어쓸 수 있으며 updatedInput / suggestions / message 도 함께 보낼 수 있습니다. (\"ask\" 덮어쓰기는 PreToolUse 전용이라 여기서는 불가합니다.)",
    },
    permission_denied: {
      label: "권한 거부 (PermissionDenied)",
      sub: "사용자가 권한을 거부한 직후. payload 의 tool_name, tool_input, tool_use_id, reason 으로 거부 사유를 감사용으로 기록합니다.",
    },
    user_prompt_expansion: {
      label: "프롬프트 확장 중 (UserPromptExpansion)",
      sub: "슬래시 커맨드 또는 MCP prompt 가 본 프롬프트로 풀리는 동안 발동. payload 에 expansion_type (\"slash_command\" / \"mcp_prompt\"), command_name, command_args, command_source, prompt 가 들어옵니다. 차단 가능, ask 인터럽트는 불가.",
    },
    post_compact: {
      label: "컴팩션 직후 (PostCompact)",
      sub: "컨텍스트 컴팩션이 끝나고 요약이 새 컨텍스트로 들어가기 직전. payload 의 trigger (\"manual\"/\"auto\") 와 compact_summary 를 감사용으로 기록합니다.",
    },
    elicitation: {
      label: "유저 응답 요청 직전 (Elicitation)",
      sub: "MCP 서버가 사용자에게 elicitation 을 요청하기 직전. payload 에 mcp_server_name, message, mode, url, elicitation_id, requested_schema 가 들어옵니다. MCP elicitation 채널이라 “추가 정보 주입” 액션은 지원되지 않습니다.",
    },
    elicitation_result: {
      label: "유저 응답 수신 (ElicitationResult)",
      sub: "유저가 elicitation 에 응답한 직후. payload 에 mcp_server_name, elicitation_id, mode, action (\"accept\"/\"decline\"/\"cancel\"), content 가 들어옵니다. MCP elicitation 채널이라 “추가 정보 주입” 액션은 지원되지 않습니다.",
    },
    subagent_start: {
      label: "서브에이전트 시작 (SubagentStart)",
      sub: "Task 도구가 서브에이전트를 spawn 하기 직전. payload 의 agent_id, agent_type 으로 어떤 child 인지 식별합니다. (CC 가 문서화한 hookSpecificOutput.additionalContext 채널 목록에 SubagentStart 는 포함되지 않습니다. 부모 의도를 child 에 전달하고 싶다면 child 의 SessionStart 훅에서 주입하세요.)",
    },
    stop_failure: {
      label: "에이전트 종료 실패 (StopFailure)",
      sub: "Stop 훅 체인이 오류(비정상 종료 코드, 타임아웃 등) 로 끝났을 때 발동. payload 에 error, error_details, last_assistant_message 가 들어옵니다. 실행 종료 시점이라 “추가 정보 주입” 액션은 지원되지 않습니다.",
    },
    setup: {
      label: "워크스페이스 셋업 (Setup)",
      sub: "CC 가 워크스페이스 셋업을 돌릴 때 한 번 발동. payload 의 trigger (\"init\" / \"maintenance\") 로 어떤 셋업 사이클인지 구분합니다.",
    },
    notification: {
      label: "알림 (Notification)",
      sub: "CC 가 사용자에게 알림(터미널 벨, 데스크톱 푸시 등) 을 표시하기 직전. payload 에 message, title, notification_type 이 들어옵니다. notification_type 은 바이너리에서 추출한 8값 enum 입니다: \"idle_prompt\" / \"worker_permission_prompt\" / \"push_notification\" / \"auth_success\" / \"elicitation_complete\" / \"elicitation_response\" / \"computer_use_enter\" / \"computer_use_exit\".",
    },
    teammate_idle: {
      label: "팀메이트 유휴 (TeammateIdle)",
      sub: "팀 모드에서 다른 에이전트가 유휴(다음 작업 대기) 상태로 들어갔을 때. payload 의 teammate_name, team_name 으로 어떤 팀메이트인지 식별합니다.",
    },
    task_created: {
      label: "Task 디스패치 (TaskCreated)",
      sub: "Task 도구가 서브에이전트에 작업을 디스패치한 직후 발동. payload 에 task_id, task_subject, task_description, teammate_name, team_name 이 들어옵니다.",
    },
    task_completed: {
      label: "Task 완료 (TaskCompleted)",
      sub: "Task 도구가 결과를 돌려준 직후 발동. payload 에 task_id, task_subject, task_description, teammate_name, team_name 이 들어와 TaskCreated 와 task_id 로 짝지을 수 있습니다.",
    },
    config_change: {
      label: "설정 변경 (ConfigChange)",
      sub: "CC 가 settings.json 의 변경을 감지하고 새 값을 적용한 직후. payload 의 source (\"userSettings\" / \"projectSettings\" / \"localSettings\" / \"flagSettings\" / \"policySettings\") 와 file_path 로 어느 레이어가 바뀌었는지 알 수 있습니다.",
    },
    worktree_create: {
      label: "워크트리 생성 (WorktreeCreate)",
      sub: "CC 가 isolation:worktree 정책으로 새 git worktree 를 만든 직후. payload 의 name 으로 슬러그를 받습니다. 이 훅은 hookSpecificOutput.worktreePath 채널로 경로를 반환하기 때문에 “추가 정보 주입” 액션은 지원되지 않습니다.",
    },
    worktree_remove: {
      label: "워크트리 제거 (WorktreeRemove)",
      sub: "isolation 워크트리가 정리된 직후. payload 의 worktree_path 로 제거된 경로를 받습니다.",
    },
    instructions_loaded: {
      label: "지침 로드 (InstructionsLoaded)",
      sub: "CC 가 CLAUDE.md / AGENTS.md / @import 파일을 메모리에 올린 직후 발동. payload 에 file_path, memory_type, load_reason, globs, trigger_file_path, parent_file_path 가 들어옵니다.",
    },
    cwd_changed: {
      label: "작업 디렉터리 변경 (CwdChanged)",
      sub: "에이전트의 cwd 가 다른 폴더로 옮겨간 직후. payload 의 old_cwd, new_cwd 로 전후를 비교할 수 있습니다.",
    },
    file_changed: {
      label: "파일 변경 감지 (FileChanged)",
      sub: "managed FileChanged matcher 가 잡은 파일이 외부에서 변경됐을 때. payload 의 file_path 와 event (chokidar wrapper 가 그대로 전달: \"change\" / \"add\" / \"unlink\") 로 어떤 변경인지 알 수 있습니다.",
    },
    message_display: {
      label: "메시지 표시 (MessageDisplay)",
      sub: "스트리밍 어시스턴트 응답의 각 델타가 터미널로 렌더되기 직전. payload 의 turn_id, message_id, index, final, delta 로 어디까지 그렸는지 추적합니다. hookSpecificOutput.displayContent 로 표시만 덮어쓸 수 있고 저장된 메시지/모델 컨텍스트는 바꾸지 못하므로 “추가 정보 주입” 액션은 지원되지 않습니다.",
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
      sub: "Fires before the runtime compacts the transcript. Payload carries trigger (\"manual\" / \"auto\") and custom_instructions (the operator's string after `/compact <instructions>`; empty when trigger is \"auto\"). Use it to preserve evidence chains or branch on compaction reason.",
    },
    pre_final: {
      label: "When the agent stops (Stop)",
      sub: "Main agent turn ends. Audit-only — Inject extra context is not available here.",
    },
    subagent_stop: {
      label: "When a subagent stops (SubagentStop)",
      sub: "Fires when a subagent task ends. Audit child transcripts. Inject extra context is not available here (use SubagentStart instead).",
    },
    session_start: {
      label: "When the session opens (SessionStart)",
      sub: "Fires on session startup, resume, or clear. Audit boundary marker.",
    },
    session_end: {
      label: "When the session closes (SessionEnd)",
      sub: "Fires once at session end. Audit boundary marker. The session is closing so there is no downstream model turn for additionalContext; “Inject extra context” is not available here.",
    },
    // D58 → D79 (verified against CC 2.1.170 binary; payload fields
    // pinned in src/magi_cp/policy/payload_schemas.py).
    post_tool_use_failure: {
      label: "Tool call failed (PostToolUseFailure)",
      sub: "Fires right after a tool call ends in error. Payload carries tool_name, tool_input, tool_use_id, error, is_interrupt, duration_ms.",
    },
    post_tool_batch: {
      label: "Tool batch finished (PostToolBatch)",
      sub: "Fires once after every tool call in the turn returns. Payload carries the tool_calls array (one entry per call with tool_name/tool_input/tool_response).",
    },
    permission_request: {
      label: "Permission request (PermissionRequest)",
      sub: "Right before CC pops a permission prompt. Payload carries tool_name, tool_input, permission_suggestions. Override the decision via hookSpecificOutput.permissionRequestResult.behavior (\"allow\" / \"deny\") with optional updatedInput, suggestions, and message. (\"ask\" override is PreToolUse-only and not available here.)",
    },
    permission_denied: {
      label: "Permission denied (PermissionDenied)",
      sub: "Right after a permission was denied. Payload carries tool_name, tool_input, tool_use_id, reason — log the rejection cause for audit.",
    },
    user_prompt_expansion: {
      label: "Prompt expansion (UserPromptExpansion)",
      sub: "Fires while a slash command or MCP prompt expands into the final prompt. Payload carries expansion_type (\"slash_command\" / \"mcp_prompt\"), command_name, command_args, command_source, prompt. Block is supported; ask cannot interrupt.",
    },
    post_compact: {
      label: "After compaction (PostCompact)",
      sub: "Fires right after a context compaction, before the new summary lands in the model context. Payload carries trigger (\"manual\"/\"auto\") and compact_summary.",
    },
    elicitation: {
      label: "Before elicitation (Elicitation)",
      sub: "Right before an MCP server asks the user for extra info. Payload carries mcp_server_name, message, mode, url, elicitation_id, requested_schema. MCP elicitation channel — “Inject extra context” is not available here.",
    },
    elicitation_result: {
      label: "Elicitation answered (ElicitationResult)",
      sub: "Right after the user answers an MCP elicitation. Payload carries mcp_server_name, elicitation_id, mode, action (\"accept\"/\"decline\"/\"cancel\"), content. MCP elicitation channel — “Inject extra context” is not available here.",
    },
    subagent_start: {
      label: "Subagent starting (SubagentStart)",
      sub: "Fires just before a Task-tool subagent is spawned. Payload carries agent_id, agent_type for identification. additionalContext injection into the child is not part of CC's documented hook output channels for this event — if you need to inject context, use SessionStart on the child side.",
    },
    stop_failure: {
      label: "Stop failure (StopFailure)",
      sub: "Fires when the Stop hook chain itself errored out (non-zero exit, timeout, etc.). Payload carries error, error_details, last_assistant_message. End-of-execution timing — “Inject extra context” is not available here.",
    },
    setup: {
      label: "Workspace setup (Setup)",
      sub: "Fires once when CC runs workspace setup. Payload carries trigger (\"init\" / \"maintenance\") so you can scope on the cycle.",
    },
    notification: {
      label: "Notification (Notification)",
      sub: "Right before CC surfaces a notification (terminal bell, desktop push, …). Payload carries message, title, notification_type. notification_type is the 8-value enum extracted from the binary: \"idle_prompt\" / \"worker_permission_prompt\" / \"push_notification\" / \"auth_success\" / \"elicitation_complete\" / \"elicitation_response\" / \"computer_use_enter\" / \"computer_use_exit\".",
    },
    teammate_idle: {
      label: "Teammate idle (TeammateIdle)",
      sub: "Fires when a team-mode agent enters idle (waiting for the next task). Payload carries teammate_name, team_name.",
    },
    task_created: {
      label: "Task dispatched (TaskCreated)",
      sub: "Fires right after the Task tool dispatches work to a subagent. Payload carries task_id, task_subject, task_description, teammate_name, team_name.",
    },
    task_completed: {
      label: "Task done (TaskCompleted)",
      sub: "Fires right after the Task tool returns. Payload carries task_id, task_subject, task_description, teammate_name, team_name — correlate with TaskCreated via task_id.",
    },
    config_change: {
      label: "Config change (ConfigChange)",
      sub: "Fires right after CC notices a settings.json change and reloads. Payload carries source (\"userSettings\" / \"projectSettings\" / \"localSettings\" / \"flagSettings\" / \"policySettings\") and file_path so you can scope by layer.",
    },
    worktree_create: {
      label: "Worktree created (WorktreeCreate)",
      sub: "Fires right after isolation:worktree creates a new git worktree. Payload carries name. This hook returns the worktree path via hookSpecificOutput.worktreePath, so “Inject extra context” is not available here.",
    },
    worktree_remove: {
      label: "Worktree removed (WorktreeRemove)",
      sub: "Right after an isolation worktree is cleaned up. Payload carries worktree_path so you can scope by location.",
    },
    instructions_loaded: {
      label: "Instructions loaded (InstructionsLoaded)",
      sub: "Right after CC loads a CLAUDE.md / AGENTS.md / @import file. Payload carries file_path, memory_type, load_reason, globs, trigger_file_path, parent_file_path.",
    },
    cwd_changed: {
      label: "Working directory changed (CwdChanged)",
      sub: "Right after the agent's cwd moves. Payload carries old_cwd and new_cwd so you can diff the move.",
    },
    file_changed: {
      label: "Watched file changed (FileChanged)",
      sub: "Fires when a file matched by a managed FileChanged matcher is modified outside CC. Payload carries file_path and event — the chokidar wrapper forwards \"change\" / \"add\" / \"unlink\" verbatim (no Posix normalization).",
    },
    message_display: {
      label: "Message displayed (MessageDisplay)",
      sub: "Fires for every streaming-assistant delta right before it renders to the terminal. Payload carries turn_id, message_id, index, final, delta. Display-only: hookSpecificOutput.displayContent overrides the rendered text but does not change the stored message or the model context. “Inject extra context” is not available here.",
    },
  }
}

// D61 perf: pre-build the per-locale labels once at module load. The
// `Step1Lifecycle` server function would otherwise call
// `lifecycleCardCopy(locale)` on every render, returning a fresh object
// reference each time and busting the client picker's
// `visibilityByGroup` memo on `[labels, query]`. The two
// constants are stable across the module lifetime.
const LIFECYCLE_LABELS_BY_LOCALE: Record<
  "ko" | "en",
  Record<Lifecycle, { label: string; sub: string }>
> = {
  ko: lifecycleCardCopy("ko"),
  en: lifecycleCardCopy("en"),
}

// D56c: lifecycles grouped by family so the 8-card grid stays scannable.
// Group headers come from the dict (newPolicy.wizard.step1.group.*).
//
// D61 cleanup: the legacy `LIFECYCLE_GROUPS` (8 slugs in 3 groups,
// pre-D58 shape; later 30 slugs in 6 groups but DIFFERENT composition
// from the picker's canonical groups) used to live here as a
// `void`-referenced data shape kept alive by a source-grep gate in
// wizard-wiring.test.ts. Two divergent group declarations for the same
// 30 slugs invited silent drift — the legacy const counted as "pinned"
// even when the rendered surface (driven by `ADVANCED_GROUPS` +
// `COMMON_GROUP` in `step1-lifecycle-groups.ts`) regressed. The gate
// has been re-pointed at `step1-lifecycle-groups.ts` so the 8-slug
// invariant fails on the actual data the picker renders. Coverage is
// not lost: `Step1LifecyclePicker.test.ts` still asserts the full
// 30-slug composition and no-overlap.

function Step1Lifecycle({
  t, locale, state, action,
}: {
  state: WizardState; locale: "ko" | "en"
  action: (fd: FormData) => Promise<void>
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  // D61: layered-disclosure picker. The Step 1 surface used to show
  // every one of the 30 hook events at once; this collapses the screen
  // to a default-expanded "Common" group (PreToolUse, PostToolUse,
  // UserPromptSubmit, Stop, TaskCompleted — D69 added the 5th entry
  // because end-of-task automation is one of the most common operator
  // patterns) with the remaining 25 events tucked into collapsed
  // Advanced groups + a search filter. LIFECYCLE_GROUPS (the legacy
  // data shape) is kept for the i18n drift gate but no longer drives
  // the rendered surface; Step1LifecyclePicker owns the layout.
  const current = state.lifecycle ?? "before_tool_use"
  // Stable per-locale reference: `lifecycleCardCopy` returns a fresh
  // object every call. The picker memoizes `visibilityByGroup` on
  // `[labels, query]`; passing a fresh ref every Step1Lifecycle render
  // moots the memo. `LIFECYCLE_LABELS_BY_LOCALE` is pre-built once at
  // module load (no per-render allocation, no React `useMemo` needed
  // — this function runs in a server component).
  const labels = LIFECYCLE_LABELS_BY_LOCALE[locale]
  return (
    <StepShell
      heading={t("newPolicy.wizard.step1.heading")}
      helper={t("newPolicy.wizard.step1.helper")}
    >
      <form action={action} className="space-y-5">
        <input type="hidden" name="_step" value="1" />
        <Step1LifecyclePicker
          locale={locale}
          currentLifecycle={current}
          labels={labels}
        />
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
  // here.
  // D70: replaced the chip grid + separate MCP input with a single
  // autocomplete combobox that covers every CC built-in + free-typed
  // MCP / custom names. The combobox owns the only Step 2 form input
  // (`toolScope_custom`) and ToolCombobox renders the pre-filled
  // value. advanceWizard's existing fallback `scopeCustom || scopeChip`
  // still works because the chip name is now absent (treated as empty
  // string) and the typed value wins.
  const rawScope = (state.toolScope ?? "").trim()
  const firstPick = rawScope
  const isAny = !rawScope || rawScope === "*"
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
              {t("newPolicy.wizard.step2.toolPicker.hint")}
            </span>
          </span>
          {/* D70: replaced 3-column chip grid + separate MCP input with a
            * single autocomplete combobox covering every CC built-in tool
            * + free-typed MCP / custom names. The combobox owns ONE
            * hidden input named `toolScope_custom` so advanceWizard's
            * existing seam (which already prefers the typed value over
            * any chip pick) works unchanged. The legacy `toolScope_chip`
            * radio row is removed; advanceWizard tolerates a missing
            * chip value because its fallback chain was always
            * `scopeCustom || scopeChip`. */}
          {/* D71: changed <span> -> <div> so the block-level
            * combobox <div>/<ul> isn't nested inside an inline span
            * (browsers break a span at a block child which can
            * rearrange peer-checked reveal styles). */}
          <div className="mt-3 hidden peer-checked:block space-y-3">
            <div>
              <FieldLabel htmlFor="step2-tool-combobox">
                {t("newPolicy.wizard.step2.toolPicker.label")}
              </FieldLabel>
              <ToolCombobox
                initialValue={firstPick && firstPick !== "*" ? firstPick : ""}
                locale={locale}
                inputId="step2-tool-combobox"
              />
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
          </div>
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
  fields, locale, intro, targetTextareaId, variant, targetSelectId,
}: {
  fields: PayloadFieldDescriptor[]
  locale: "ko" | "en"
  intro?: string
  targetTextareaId: string
  // D82c: two new variants. "llm-marker" wraps with curly braces so
  // the runtime marker substitutor recognises the field (and the
  // operator can see where the variable ends). "regex-target" routes
  // the chip click to a separate <select id={targetSelectId}> so the
  // pattern textarea stays clean of curly braces (which would break
  // the regex compile).
  variant: ChipVariant
  targetSelectId?: string
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
      targetSelectId={targetSelectId}
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
  t, locale, state, wiredSteps, action, wizardErr,
}: {
  state: WizardState; locale: "ko" | "en"
  wiredSteps: WiredStep[]
  action: (fd: FormData) => Promise<void>
  /** D62: precise err code from advanceWizard's Step 3 specifics
   *  validation. Drives the inline highlight + helper copy next to
   *  the empty input. */
  wizardErr?: string
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
  // D82a: bottom-left Back is gone; the top-left WizardHeader Back arrow
  // calls previousLiveStep() and owns the prev-step math now.

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

  // D62: precise per-kind inline highlight. `wizardErr` is the err
  // code advanceWizard sets when refusing the Step 3 to Step 4 advance
  // on empty specifics. The map below names WHICH conditionKind's
  // input is empty (so we can target the inline highlight to the
  // right card) and looks up the localized helper copy that replaces
  // the generic "Invalid input" banner. Keep the keys in sync with
  // validateStep3Specifics; the inline banner replaces the page-level
  // ErrorState for these codes (resolveFlash returns null on them so
  // we do not render a duplicate English banner above the localized
  // inline copy).
  //
  // D62 follow-up: tables typed by `Step3ErrCode` so adding a new
  // code to the gate forces both tables to grow at compile time.
  const ERR_TO_KIND: Record<Step3ErrCode, ConditionKind | "any"> = {
    pick_condition: "any",
    missing_criterion: "llm_critic",
    missing_pattern: "regex",
    missing_shacl: "shacl",
    missing_domain: "fetch_domain",
    missing_allowlist: "domain_allowlist",
    missing_evidence: "evidence_ref",
  }
  const ERR_TO_TKEY: Record<Step3ErrCode, import("@/lib/i18n/dict").TKey> = {
    pick_condition: "newPolicy.wizard.step3.err.pickCondition",
    missing_criterion: "newPolicy.wizard.step3.err.missingCriterion",
    missing_pattern: "newPolicy.wizard.step3.err.missingPattern",
    missing_shacl: "newPolicy.wizard.step3.err.missingShacl",
    missing_domain: "newPolicy.wizard.step3.err.missingDomain",
    missing_allowlist: "newPolicy.wizard.step3.err.missingAllowlist",
    missing_evidence: "newPolicy.wizard.step3.err.missingEvidence",
  }
  const isStep3ErrCode = (s: string | undefined): s is Step3ErrCode =>
    s !== undefined && s in ERR_TO_KIND
  const step3ErrCode = isStep3ErrCode(wizardErr) ? wizardErr : undefined
  const step3ErrKind = step3ErrCode ? ERR_TO_KIND[step3ErrCode] : undefined
  const step3ErrHelperKey = step3ErrCode ? ERR_TO_TKEY[step3ErrCode] : undefined
  const step3ErrHelper = step3ErrHelperKey ? t(step3ErrHelperKey) : undefined
  // The red-ring class is applied to the per-kind input only when the
  // err code points at that kind. `pick_condition` does not target a
  // specific input; it surfaces as a top banner instead.
  //
  // D62 follow-up: `errRingFor` returns the class with a leading
  // space already attached when set, so call sites concatenate
  // without leaving a trailing space on the no-error branch.
  const errRingCls = "ring-2 ring-red-400 border-red-400"
  const errRingFor = (k: ConditionKind): string =>
    step3ErrKind === k ? " " + errRingCls : ""

  return (
    <StepShell
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
      {/* D57e P1: evidenceRefs riding through on the URL that named a
          verifier the current lifecycle does not fire are pruned at the
          state-build seam. Surface a one-shot banner mirroring the
          dropped-condition-kind / dropped-alternation pattern so the
          operator knows their cross-lifecycle ref was removed and can
          decide whether to pick a different verifier or change the
          lifecycle. */}
      {state._droppedEvidenceRefs && state._droppedEvidenceRefs.length > 0 && (
        <div
          data-testid="step3-dropped-evidence-refs-banner"
          className="rounded-xl border border-amber-300 bg-amber-50/60 px-3 py-2 text-xs text-amber-900"
        >
          {ko
            ? `${lifecycleLabel} 라이프사이클에서 발동하지 않는 verifier (${state._droppedEvidenceRefs.join(", ")}) 가 제거되었습니다. 다른 verifier 를 선택하거나 라이프사이클을 변경하세요.`
            : `Verifier(s) that do not fire on ${lifecycleLabel} were removed: ${state._droppedEvidenceRefs.join(", ")}. Pick a different verifier or change the lifecycle.`}
        </div>
      )}
      {/* D82b: the "Just inject extra context" shortcut card that
          previously sat above the condition picker has been removed.
          It conflated condition kind (Step 3) with action archetype
          (Step 4): picking the card silently rewrote state.action to
          `inject_context` and jumped past Step 3, which read as a
          condition-picker choice even though it lived a step above the
          radios. Operators who actually want a context-injection
          policy now express it explicitly: pick "No condition" at
          Step 3 -> Next -> pick "Inject extra context" at Step 4.
          The Step 4 surface (with its disabled-card tooltip on
          excluded lifecycles) is the canonical place to discover the
          archetype; Step 3 no longer second-guesses it. */}
      {/* D62: precise inline error banner. Replaces the previous
          generic "Invalid input" page-level flash that fired from
          Step 5 with no pointer. The specific input's red ring +
          helper copy lives inside the per-kind specifics block below
          (rendered next to whichever input the operator left empty);
          this banner names the problem in one line at the top of the
          form so the operator sees both surfaces. `pick_condition`
          surfaces only as the banner because no specific input is
          empty (the operator did not pick a kind at all). */}
      {step3ErrHelper && (
        <div
          data-testid="step3-specifics-err-banner"
          data-step3-err={wizardErr}
          role="alert"
          className="rounded-xl border border-red-300 bg-red-50/60 px-3 py-2 text-xs text-red-900"
        >
          {step3ErrHelper}
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
                      className={inputCls() + " font-mono" + errRingFor("fetch_domain")}
                    />
                    {step3ErrKind === "fetch_domain" && step3ErrHelper && (
                      <p
                        data-testid="step3-fetch-domain-helper"
                        className="mt-1 text-xs text-red-700"
                      >
                        {step3ErrHelper}
                      </p>
                    )}
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
                      className={inputCls() + " font-mono" + errRingFor("domain_allowlist")}
                    />
                    {step3ErrKind === "domain_allowlist" && step3ErrHelper && (
                      <p
                        data-testid="step3-allowlist-helper"
                        className="mt-1 text-xs text-red-700"
                      >
                        {step3ErrHelper}
                      </p>
                    )}
                  </div>
                )}
                {k === "regex" && (
                  <div>
                    <FieldLabel>{ko ? "정규식 패턴 (Python re)" : "Regex pattern (Python re)"}</FieldLabel>
                    {/* D82c: regex condition splits into target field +
                        pattern. Chip clicks set the <select> value; the
                        pattern textarea is left untouched so curly braces
                        can't accidentally land in the regex source.

                        D82c fix: render the <select> ABOVE the chip row
                        so the chip intro text ("click to set the picker
                        above") is directionally correct. The picker is
                        the primary control and the chips are a quick-
                        pick suggestion strip under it. */}
                    <div className="mb-2">
                      <FieldLabel>{ko ? "검사할 필드" : "Field to match"}</FieldLabel>
                      {/* D80: native <select> retired so the OS-styled
                          popup doesn't break visual parity with the rest
                          of the wizard. FieldPathSelect is a custom
                          listbox button + popover that emits the same
                          hidden input contract (name="regexFieldPath")
                          so saveWizard reads FormData unchanged. */}
                      <FieldPathSelect
                        id="w-regex-field-path"
                        name="regexFieldPath"
                        initialValue={
                          state.regexFieldPath
                          ?? defaultRegexFieldFor(state)
                          ?? payloadFields[0]?.path
                          ?? ""
                        }
                        testId="step3-regex-field-path"
                        ariaLabel={ko ? "검사할 필드" : "Field to match"}
                        options={(() => {
                          const opts: { path: string; displayLabel?: string; type?: string }[] =
                            payloadFields.map((pf) => ({
                              path: pf.path,
                              displayLabel: ko
                                ? pf.display_label_ko ?? pf.display_label_en
                                : pf.display_label_en ?? pf.display_label_ko,
                              type: pf.type,
                            }))
                          // Fallback: if the in-state field_path is not
                          // in the static list (legacy migration / MCP
                          // slug), still render it so the value
                          // round-trips.
                          if (
                            state.regexFieldPath
                            && !payloadFields.some((pf) => pf.path === state.regexFieldPath)
                          ) {
                            opts.push({ path: state.regexFieldPath })
                          }
                          return opts
                        })()}
                      />
                    </div>
                    <PayloadFieldChips
                      fields={payloadFields}
                      locale={locale}
                      intro={ko
                        ? "검사할 필드 (클릭하면 위 선택 박스에 설정):"
                        : "Which field to match (click to set the picker above):"}
                      targetTextareaId="w-regex-pattern"
                      variant="regex-target"
                      targetSelectId="w-regex-field-path"
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
                      className={fieldInputCls + errRingFor("regex")}
                      fieldElement="input"
                      name="pattern"
                      placeholder="AKIA[A-Z0-9]{16}"
                      maxLength={2000}
                      monospace
                    />
                    {step3ErrKind === "regex" && step3ErrHelper && (
                      <p
                        data-testid="step3-regex-helper"
                        className="mt-1 text-xs text-red-700"
                      >
                        {step3ErrHelper}
                      </p>
                    )}
                  </div>
                )}
                {k === "llm_critic" && (
                  <div>
                    <FieldLabel>{ko ? "LLM critic 기준" : "LLM critic criterion"}</FieldLabel>
                    {/* D82c: Yes/No guide. Operators tend to write open-
                        ended prompts that produce inconsistent verdicts.
                        Anchor the criterion as a single yes/no question
                        whose Yes answer means the action is SAFE.

                        D82c fix: re-anchor on the SAFE-frame so the
                        natural-language polarity ("Yes = safe") matches
                        the runtime polarity ("verdict=pass → ALLOW").
                        The prior "Does X contain bad thing?" phrasing
                        inverted the polarity for first-time PII / leak
                        gates: literal Yes → "PII present" but Yes mapped
                        to pass which mapped to ALLOW the leaky action.

                        D82c fix: visual promotion. Promote from the
                        plain tertiary 11px caption to a tinted callout
                        (border-l-2) so the guide reads as "rule for
                        writing this field" rather than wallpaper. The
                        chip intro that follows stays tertiary 11px since
                        it's a column header. */}
                    <div
                      data-testid="step3-llm-critic-guide"
                      className="mb-2 rounded-r border-l-2 border-[var(--color-accent)]/50 bg-[var(--color-accent)]/[0.04] px-2.5 py-1.5 text-xs leading-relaxed text-[var(--color-text-secondary)]"
                    >
                      {ko
                        ? "예 = 안전(허용), 아니오 = 차단/감사 으로 답할 수 있는 질문으로 작성하세요. 예: \"출력이 개인정보를 누설하지 않나요?\" → 예이면 통과."
                        : "Write a question whose Yes answer means the action is SAFE to allow. Yes = pass, No = block. e.g. \"Does the output avoid leaking personally identifiable information?\" → Yes means pass."}
                    </div>
                    <PayloadFieldChips
                      fields={payloadFields}
                      locale={locale}
                      intro={ko
                        ? "기준에서 참조 가능한 필드 (클릭하면 {경로} 형태로 삽입):"
                        : "Fields you can reference in your criterion (click to insert as {path}):"}
                      targetTextareaId={`w-llm-${k}`}
                      variant="llm-marker"
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
                      className={fieldInputCls + errRingFor("llm_critic")}
                      fieldElement="textarea"
                      rows={3}
                      name="llmCriterion"
                      // D82c fix: both placeholders model the marker
                      // syntax AND the SAFE-frame Yes polarity. The
                      // prior EN example referenced {transcript_path}
                      // which substitutes to a literal filesystem path
                      // string (degenerate at LLM-time); the KO example
                      // omitted any marker, contradicting the chip
                      // intro that just taught marker syntax. Both now
                      // anchor on {tool_response.output} so the marker
                      // substitutes to the actual tool output text and
                      // the chip tutorial agrees with the example.
                      placeholder={ko
                        ? "예: {tool_response.output}이 개인정보(이름·주민번호·이메일)를 누설하지 않나요?"
                        : "e.g. Does {tool_response.output} avoid leaking personally identifiable information?"}
                      monospace
                    />
                    {step3ErrKind === "llm_critic" && step3ErrHelper && (
                      <p
                        data-testid="step3-llm-critic-helper"
                        className="mt-1 text-xs text-red-700"
                      >
                        {step3ErrHelper}
                      </p>
                    )}
                  </div>
                )}
                {k === "evidence_ref" && (
                  <div className="space-y-2">
                    <FieldLabel>{ko ? "참조할 verifier (1개 이상)" : "Verifier(s) to reference"}</FieldLabel>
                    {step3ErrKind === "evidence_ref" && step3ErrHelper && (
                      <p
                        data-testid="step3-evidence-ref-helper"
                        className="mt-1 text-xs text-red-700"
                      >
                        {step3ErrHelper}
                      </p>
                    )}
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
                      const droppedSteps = wiredSteps
                        .filter((w) =>
                          !verifierFiresOnLifecycle(w.step, ccEvent),
                        )
                        .map((w) => w.step)
                      const droppedCount = droppedSteps.length
                      // D57e P1 follow-up: when the wizard is in EDIT
                      // mode (the user reached this step with an
                      // existing evidenceRefs payload), surface any
                      // ref the policy still references but that the
                      // lifecycle now drops. The picker can't render
                      // a checkbox for it (no descriptor group for
                      // this lifecycle), but the operator needs to
                      // see WHICH ref vanished so they can pick a
                      // remediation. Empty in create mode.
                      const editedDroppedRefs = (state.evidenceRefs ?? [])
                        .filter((s) => !verifierFiresOnLifecycle(s, ccEvent))
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
                              data-dropped-verifier-steps={droppedSteps.join(",")}
                              className="text-[11px] italic text-[var(--color-text-tertiary)]"
                            >
                              {ko
                                ? `${droppedSteps.join(", ")} 가 이 라이프사이클에서 발동하지 않아 숨김 처리되었습니다 (${droppedCount}개).`
                                : `Hidden because they do not fire on this lifecycle: ${droppedSteps.join(", ")} (${droppedCount}).`}
                            </p>
                          )}
                          {editedDroppedRefs.length > 0 && (
                            <p
                              data-testid="step3-verifier-picker-edit-drift-note"
                              data-edit-drift-verifier-steps={editedDroppedRefs.join(",")}
                              className="text-[11px] text-amber-800 bg-amber-50/40 border border-amber-300 rounded-md px-2 py-1"
                            >
                              {ko
                                ? `이 정책이 참조하던 ${editedDroppedRefs.join(", ")} 는 ${lifecycleLabel} 라이프사이클에서 발동하지 않습니다.`
                                : `This policy references ${editedDroppedRefs.join(", ")}, but they do not fire on ${lifecycleLabel}.`}
                            </p>
                          )}
                        </>
                      )
                    })()}
                    <div
                      className={
                        "space-y-2" +
                        (step3ErrKind === "evidence_ref"
                          ? " rounded-md ring-2 ring-red-400 p-1"
                          : "")
                      }
                    >
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
                                locale={locale}
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
                      className={fieldInputCls + errRingFor("shacl")}
                      fieldElement="textarea"
                      rows={6}
                      name="shaclTtl"
                      placeholder={"@prefix sh:   <http://www.w3.org/ns/shacl#> .\n@prefix magi: <https://magi.openmagi.ai/cc/hook#> .\n…"}
                      monospace
                    />
                    {step3ErrKind === "shacl" && step3ErrHelper && (
                      <p
                        data-testid="step3-shacl-helper"
                        className="mt-1 text-xs text-red-700"
                      >
                        {step3ErrHelper}
                      </p>
                    )}
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
  t, locale, state, action, wizardErr,
}: {
  state: WizardState; locale: "ko" | "en"
  action: (fd: FormData) => Promise<void>
  /** D68: precise err code from advanceWizard's Step 4 action-
   *  specifics validation (mirror of Step3Condition.wizardErr). Drives
   *  the inline highlight + helper copy on the empty Step 4b field.
   *  When set, the banner renders inside the corresponding action's
   *  peer-checked sub-form (next to the empty input) so the
   *  operator sees the explanation where their attention already
   *  is. block / ask / audit have no Step 4b and never receive an
   *  err code here. */
  wizardErr?: string
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
  // D82d follow-up: when the (lifecycle × toolScope) combination is
  // empty (operator landed on Step 4 directly with no toolScope, or
  // arrived via a stale URL), narrow the fallback to actions that are
  // ALSO legal on the lifecycle's wildcard surface. ACTIONS_BY_LIFECYCLE
  // alone can leak block onto (after_tool_use × wildcard), which the
  // cloud matrix rejects explicitly (PostToolUse + * + block is not in
  // LEGAL_COMBINATIONS). Intersecting with the wildcard combination
  // entry keeps the per-lifecycle and per-combination tables from
  // drifting in opposite directions.
  const wildcardAllowed = new Set<Action>(
    ACTIONS_BY_COMBINATION[lifecycle]?.wildcard ?? [],
  )
  const fallbackActions: readonly Action[] = wildcardAllowed.size > 0
    ? ACTIONS_BY_LIFECYCLE[lifecycle].filter((a) => wildcardAllowed.has(a))
    // No wildcard surface for this lifecycle either → keep the
    // lifecycle's per-action card visible so the operator can see the
    // shape and Step 2's tool-scope picker can re-narrow it.
    : ACTIONS_BY_LIFECYCLE[lifecycle]
  const allowed = combinationAllowed.length > 0
    ? combinationAllowed
    : fallbackActions
  // D59: inject_context renders as a disabled radio when the chosen
  // lifecycle has a specialized hookSpecificOutput shape (see
  // `CONTEXT_INJECTION_EXCLUDED_LIFECYCLES`). It still appears in
  // `allowed` so the disabled card surfaces a tooltip explaining
  // why the archetype is unavailable, but the operator must NOT land
  // with a stale `?action=inject_context` URL silently pre-selecting
  // a disabled radio. The picker filter below drops inject_context
  // for the auto-default fallback when the lifecycle is excluded.
  const pickableForDefault = lifecycleAllowsInjectContext(lifecycle)
    ? allowed
    : allowed.filter((a) => a !== "inject_context")
  const fallbackPick: Action =
    pickableForDefault.length > 0 ? pickableForDefault[0] : allowed[0]
  const defaultPick: Action =
    state.action && pickableForDefault.includes(state.action)
      ? state.action : fallbackPick
  const ko = locale === "ko"
  const header = ko ? actionHeaderKO(state) : actionHeaderEN(state)
  // D56d (P2 #5): "recommended" badge only renders when block is
  // actually in the legal action set for the current combination.
  const blockLegal = allowed.includes("block")
  // D82d — block sub-copy clarifies the channel by lifecycle:
  //   PostToolUse / PostToolUseFailure / PostToolBatch surface the
  //   reason as a retry-feedback message back to the model
  //   (CC stdout JSON `{"decision":"block","reason":"…"}`); the tool
  //   already ran, so "Refuse the call" wording would mislead.
  //   Every other lifecycle keeps the pre-D82d wording.
  const blockSub: string = (() => {
    switch (lifecycle) {
      case "after_tool_use":
        return t("newPolicy.action.block.subcopy.posttool")
      case "post_tool_use_failure":
        return t("newPolicy.action.block.subcopy.posttoolfailure")
      case "post_tool_batch":
        return t("newPolicy.action.block.subcopy.posttoolbatch")
      default:
        return ko
          ? "호출 자체를 거부합니다. 에이전트가 동작을 못합니다."
          : "Refuse the call. The agent cannot proceed."
    }
  })()
  const labels: Record<Action, { label: string; sub: string }> = ko ? {
    block: { label: "Block",        sub: blockSub },
    ask:   { label: "Ask a human",  sub: "리뷰 큐로 보내고 사람이 승인해야 진행됩니다." },
    audit: { label: "Audit",        sub: "원장에만 기록하고 통과시킵니다 (관찰 모드)." },
    strip: { label: "Strip",        sub: "출력에서 매칭된 부분을 제거합니다 (after_tool_use 전용)." },
    inject_context: {
      label: "추가 정보 주입",
      sub: "이 시점에서 모델 컨텍스트에 정적 텍스트를 끼워 넣습니다. 검증 단계는 필요 없습니다.",
    },
    input_rewrite: {
      label: "도구 입력 재작성",
      sub: "도구가 실행되기 전에 입력을 안전한 형태로 자동 수정합니다 (예: Bash의 `sudo` 접두사 제거, URL을 https로 강제).",
    },
    run_command: {
      label: t("newPolicy.action.runCommand.title"),
      sub: t("newPolicy.action.runCommand.description"),
    },
  } : {
    block: { label: "Block",        sub: blockSub },
    ask:   { label: "Ask a human",  sub: "Send to the review queue; a human must approve to proceed." },
    audit: { label: "Audit",        sub: "Record to the ledger only; pass through (observe mode)." },
    strip: { label: "Strip",        sub: "Remove the matched span from the output (after_tool_use only)." },
    inject_context: {
      label: "Inject extra context",
      sub: "Inject a static block of text into the model's context at this hook. No condition required.",
    },
    input_rewrite: {
      label: "Rewrite tool input",
      sub: "Mutate the tool's input before it runs (e.g. strip `sudo` from Bash commands, force URLs to https://). The agent's request is silently corrected — no human in the loop.",
    },
    run_command: {
      label: t("newPolicy.action.runCommand.title"),
      sub: t("newPolicy.action.runCommand.description"),
    },
  }
  // D68: precise per-action inline highlight. `wizardErr` is the err
  // code advanceWizard sets when refusing the Step 4 → Step 5
  // advance on empty action-specifics. Map each code to the action
  // whose sub-form it points at, and to the localized helper key
  // (rendered both as a red-text inline banner near the Step 4b
  // editor AND as a per-input red-ring helper on the empty field
  // itself).
  //
  // D68 follow-up (P2 ux-clarity): missing_rewriter_config was split
  // into per-kind codes so the inline copy can name only the relevant
  // field; the maps below carry the rewriter kind through so the red
  // ring lands on exactly the empty input (not all three rewriter
  // kinds simultaneously) and the helper paragraph renders adjacent
  // to that input only.
  //
  // Mirror of Step3Condition's ERR_TO_KIND / ERR_TO_TKEY pattern:
  // the maps are typed by `Step4ErrCode` so a future code without a
  // row fails at compile time.
  const ERR_TO_ACTION: Record<Step4ErrCode, Action> = {
    missing_template: "inject_context",
    missing_command_or_script: "run_command",
    missing_rewriter_prefix: "input_rewrite",
    missing_rewriter_scheme: "input_rewrite",
    missing_rewriter_pattern: "input_rewrite",
  }
  // For the input_rewrite codes, also remember which rewriter kind
  // the empty field belongs to so the red ring + helper land on the
  // right input. Non-input_rewrite codes leave this undefined.
  const ERR_TO_REWRITER_KIND: Record<Step4ErrCode, RewriterKind | undefined> = {
    missing_template: undefined,
    missing_command_or_script: undefined,
    missing_rewriter_prefix: "prefix_strip",
    missing_rewriter_scheme: "scheme_force",
    missing_rewriter_pattern: "regex_substitute",
  }
  const ERR_TO_TKEY: Record<Step4ErrCode, import("@/lib/i18n/dict").TKey> = {
    missing_template: "newPolicy.wizard.step4.err.missingTemplate",
    missing_command_or_script: "newPolicy.wizard.step4.err.missingCommandOrScript",
    missing_rewriter_prefix: "newPolicy.wizard.step4.err.missingRewriterPrefix",
    missing_rewriter_scheme: "newPolicy.wizard.step4.err.missingRewriterScheme",
    missing_rewriter_pattern: "newPolicy.wizard.step4.err.missingRewriterPattern",
  }
  const isStep4ErrCode = (s: string | undefined): s is Step4ErrCode =>
    s !== undefined && s in ERR_TO_ACTION
  const step4ErrCode = isStep4ErrCode(wizardErr) ? wizardErr : undefined
  const step4ErrAction = step4ErrCode ? ERR_TO_ACTION[step4ErrCode] : undefined
  const step4ErrRewriterKind = step4ErrCode
    ? ERR_TO_REWRITER_KIND[step4ErrCode]
    : undefined
  const step4ErrHelperKey = step4ErrCode ? ERR_TO_TKEY[step4ErrCode] : undefined
  const step4ErrHelper = step4ErrHelperKey ? t(step4ErrHelperKey) : undefined
  // The red-ring class is applied to the per-action sub-form input
  // only when the err code points at that action. Note the leading
  // space so call sites concatenate without leaving a trailing
  // space on the no-error branch (matches errRingFor in Step3).
  //
  // D68 follow-up (P1 ux-clarity): for input_rewrite, also narrow on
  // the rewriter kind so the red ring lands only on the empty input
  // for the active kind, not on all three rewriter kinds at once.
  // Other actions (inject_context / run_command) ignore the kind
  // argument.
  const errRingCls = "ring-2 ring-red-400 border-red-400"
  const errRingFor = (a: Action, kind?: RewriterKind): string => {
    if (step4ErrAction !== a) return ""
    if (a === "input_rewrite") {
      // input_rewrite has three sub-form kinds rendered side by side.
      // Only highlight the input whose kind matches the err code.
      if (kind === undefined || kind !== step4ErrRewriterKind) return ""
    }
    return " " + errRingCls
  }
  // D68 follow-up (P1 ux-clarity): scheme_force has TWO inputs
  // (rewriterFrom, rewriterTo). When only ONE is empty, highlighting
  // both is a false positive and the helper paragraph (rendered only
  // under rewriterTo today) lands under the filled field if From was
  // the empty one. Compute per-field empty flags from the URL state
  // and use them to scope the ring + helper. We trim to match the
  // gate's whitespace discipline.
  const schemeFromEmpty =
    step4ErrCode === "missing_rewriter_scheme"
    && !((state.rewriterFrom ?? "").trim())
  const schemeToEmpty =
    step4ErrCode === "missing_rewriter_scheme"
    && !((state.rewriterTo ?? "").trim())
  // Inline ring for the scheme_force inputs: highlight only the empty
  // one(s). When BOTH are empty, both light up; when only one is
  // empty, only that one does. Falls back through errRingFor for
  // non-scheme codes so the rest of the API stays uniform.
  const schemeFromRingCls =
    step4ErrCode === "missing_rewriter_scheme"
      ? (schemeFromEmpty ? " " + errRingCls : "")
      : errRingFor("input_rewrite", "scheme_force")
  const schemeToRingCls =
    step4ErrCode === "missing_rewriter_scheme"
      ? (schemeToEmpty ? " " + errRingCls : "")
      : errRingFor("input_rewrite", "scheme_force")
  // D80: layered disclosure for Step 4.
  //
  // The Step 4 action picker used to render all 6 archetype cards in a
  // single vertical list. Operators only need block / ask / audit for
  // the common case; the three derivative archetypes (inject_context /
  // input_rewrite / run_command) carry a Step 4b sub-form and are
  // discoverable but not in the operator's face by default.
  //
  // Partition `allowed` into the two tiers while preserving the order
  // the existing matrix surfaces. `strip` is treated as common (it
  // renders the legacy "coming soon" disabled card and is rare; we
  // keep it on the Common rail so its "wait, ships later" affordance
  // remains visible without an extra click).
  const COMMON_ACTION_TIER: ReadonlySet<Action> = new Set<Action>([
    "block", "ask", "audit", "strip",
  ])
  const ADVANCED_ACTION_TIER: ReadonlySet<Action> = new Set<Action>([
    "inject_context", "input_rewrite", "run_command",
  ])
  // D80 follow-up (partition-exhaustiveness #9): a parallel
  // Record<Action, ...> map drives exhaustiveness off the Action
  // union itself. If a future widening adds an 8th archetype to the
  // Action union (e.g. a new row in actions.py / matrix.ts), tsc fails
  // here on the missing key. Without this, the partition above would
  // silently drop the new value from Step 4's render (allowed.filter
  // → empty) and the matrix-gating contract
  // (`allowedActionsForCombination` returns a value that the wizard
  // renders) would break silently. The Sets above remain the canonical
  // membership tests; the Record is a type-only guard.
  const _ACTION_TIER_EXHAUSTIVE: Record<Action, "common" | "advanced"> = {
    block: "common",
    ask: "common",
    audit: "common",
    strip: "common",
    inject_context: "advanced",
    input_rewrite: "advanced",
    run_command: "advanced",
  }
  // Runtime parity check (DEV-only-ish): the Record must agree with
  // the Sets. A drifted entry (e.g. ACTION_TIER says "common" but the
  // Set says "advanced") fails this check loudly so the two sources of
  // truth cannot quietly diverge. This is a cheap guard; we do it
  // once per Step 4 render which is acceptable.
  for (const a of Object.keys(_ACTION_TIER_EXHAUSTIVE) as Action[]) {
    const tier = _ACTION_TIER_EXHAUSTIVE[a]
    const inCommon = COMMON_ACTION_TIER.has(a)
    const inAdvanced = ADVANCED_ACTION_TIER.has(a)
    if (tier === "common" && !inCommon) {
      throw new Error(`ACTION_TIER drift: ${a} marked common but not in COMMON_ACTION_TIER`)
    }
    if (tier === "advanced" && !inAdvanced) {
      throw new Error(`ACTION_TIER drift: ${a} marked advanced but not in ADVANCED_ACTION_TIER`)
    }
  }
  const commonActions: Action[] = allowed.filter((a) => COMMON_ACTION_TIER.has(a))
  const advancedActions: Action[] = allowed.filter((a) => ADVANCED_ACTION_TIER.has(a))
  // When the operator's currently-picked action lives in the Advanced
  // tier (Edit mode or back-nav from Step 5), force the expander open
  // so the selected card is visible without the operator having to
  // hunt for the disclosure.
  //
  // D80 follow-up (ux-regression #2): also force-open when the Advanced
  // tier carries a disabled-but-relevant inject_context card the
  // operator should see. When the operator picks a
  // CONTEXT_INJECTION_EXCLUDED_LIFECYCLES lifecycle and lands on Step 4
  // with defaultPick=audit (because inject_context is filtered out of
  // pickableForDefault), the disabled inject_context card and its
  // "why is this grayed out?" tooltip would otherwise be buried inside
  // the collapsed-by-default Advanced expander. Forcing the expander
  // open in this case keeps the D59 matrix-driven explanation surface
  // visible by default. That is the whole point of D59.
  const advancedHasDisabledInjectContext: boolean =
    !lifecycleAllowsInjectContext(lifecycle)
    && advancedActions.includes("inject_context")
  const advancedForceOpen: boolean = (
    defaultPick != null && ADVANCED_ACTION_TIER.has(defaultPick)
  ) || advancedHasDisabledInjectContext
  // D80 i18n: prefer translated copy via t() so a future locale add
  // (or copy edit) lifts off the same dict the rest of Step 4 uses.
  //
  // D80 follow-up (i18n #3): use the bare "Advanced" label on the left
  // side of the toggle so the format matches Step 1's "group-label +
  // numeric-count" shape. The numeric count renders on the right via
  // the `advancedCount` prop so it's not duplicated. Before this fix,
  // the toggle rendered "Advanced (3 actions)" on the left AND "3" on
  // the right, which read noisier than the Step 1 parity baseline
  // ("Permissions" + "3") it claims to mirror.
  const advancedHeaderLabel: string = t(
    "newPolicy.wizard.step4.advancedSection",
  )
  const advancedExpandLabel: string = t("newPolicy.wizard.step4.expandAdvanced")
  const advancedCollapseLabel: string = t("newPolicy.wizard.step4.collapseAdvanced")
  // D80 follow-up (#1 + #8): a single per-archetype renderer used by
  // both the Common and Advanced maps so the disabled-card / Step 4b
  // sub-form / matrix-gating logic lives in exactly one place.
  //
  // Before this refactor, both `commonActions.map` AND `advancedActions.map`
  // carried the FULL per-archetype if-ladder (strip-disabled +
  // inject_context disabled + inject_context active + input_rewrite +
  // run_command + fallback RadioCard). Because COMMON_ACTION_TIER and
  // ADVANCED_ACTION_TIER are disjoint, ~600 LOC of branches in each
  // map were dead. `a === "inject_context"` could never fire inside
  // commonActions.map, and `a === "strip"` could never fire inside
  // advancedActions.map. A future operator who tweaked one copy but
  // forgot the twin would silently diverge the matrix-gating contract.
  //
  // The renderer captures every Step 4 closure (defaultPick, labels,
  // ko, state, lifecycle, errRingFor, errRingCls, schemeFrom*, scheme
  // To*, inputCls, actionCardClasses, etc) so the call sites stay
  // one-liners. JSX identity stays per-key via the surrounding
  // `commonActions.map((a) => renderActionCard(a))` (each call
  // produces a fresh element keyed by `a`).
  function renderActionCard(a: Action): React.ReactNode {
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
    // D59: four lifecycles map to hooks whose hookSpecificOutput shape
    // is SPECIALIZED. additionalContext is silently ignored at
    // runtime, so the wizard greys the card out and surfaces a per-
    // event tooltip naming the actual channel that hook uses. The
    // visible state is disabled-but-rendered (not hidden) so the
    // operator understands WHY the archetype they were looking for is
    // unavailable; EvidencePolicy (audit) is still legal on every one
    // of these via the matrix, so the operator can pivot without
    // losing wizard progress. Step 4b (template editor) sits inside
    // the same <label> branch we're skipping. It's unreachable here
    // because the radio input itself is `disabled`, the peer-checked
    // sibling can never match.
    if (a === "inject_context" && !lifecycleAllowsInjectContext(lifecycle)) {
      // D59 follow-up (#14): `lifecycleAllowsInjectContext` returns
      // false iff `lifecycle` is in the excluded set OR undefined;
      // the helper below narrows to the typed union so the
      // disabled-copy switch stays exhaustive.
      const narrowedExcluded = asContextInjectionExcludedLifecycle(lifecycle)
      if (narrowedExcluded !== null) {
        const tip = injectContextDisabledCopy(narrowedExcluded, locale)
        // D59 follow-up (#11 a11y): give the descriptive copy a
        // stable id and wire it via `aria-describedby` on the
        // disabled radio so screen readers announce the channel-
        // mismatch reason at the same moment as the disabled state.
        const tipId = `step4-inject-disabled-${narrowedExcluded}`
        return (
          <label
            key={a}
            className="block cursor-not-allowed opacity-60"
            title={tip}
            data-testid="step4-inject-context-disabled"
            data-disabled-lifecycle={lifecycle}
          >
            <input
              type="radio"
              name="action"
              value={a}
              disabled
              aria-disabled="true"
              aria-describedby={tipId}
              className="peer sr-only"
            />
            <span
              data-action-tone="inject_context"
              className={
                "block rounded-xl border bg-white p-4 transition-colors " +
                "border-black/[0.08]"
              }
            >
              <span className="flex items-center justify-between gap-2 mb-1">
                <span className="text-sm font-semibold text-[var(--color-text-primary)]">{labels[a].label}</span>
                {/* D59 follow-up (#13 ux-consistency): use the
                    neutral `muted` Badge variant so the operator
                    can distinguish "this archetype is fundamentally
                    unavailable on this hook" from the blue `info`
                    "coming soon" badge above. */}
                <Badge variant="muted">
                  {ko ? "이 hook 에서는 비활성" : "not available"}
                </Badge>
              </span>
              <span
                id={tipId}
                role="note"
                className="block text-xs text-[var(--color-text-secondary)] leading-relaxed"
              >
                {tip}
              </span>
            </span>
          </label>
        )
      }
    }
    if (a === "inject_context") {
      return (
        <label key={a} className="block cursor-pointer">
          <input
            type="radio"
            name="action"
            value={a}
            defaultChecked={defaultPick === a}
            required
            className="peer sr-only"
          />
          <span
            data-action-tone="inject_context"
            className={
              "block rounded-xl border bg-white p-4 transition-colors " +
              actionCardClasses("inject_context")
            }
          >
            <span className="flex items-center justify-between gap-2 mb-1">
              <span className="text-sm font-semibold text-[var(--color-text-primary)]">{labels[a].label}</span>
            </span>
            <span className="block text-xs text-[var(--color-text-secondary)] leading-relaxed">{labels[a].sub}</span>
          </span>
          {/* P1 follow-up (html-validation): the editor wraps
              block-level descendants; using a <span> here triggers
              validateDOMNesting warnings. The parent <label> legally
              accepts flow content, so a <div> here is fine and the
              `peer-checked ~` general-sibling selector still matches. */}
          <div
            data-testid="step4b-inject-editor"
            className="hidden peer-checked:block mt-2 rounded-xl border border-[var(--color-accent)]/30 bg-[var(--color-accent)]/[0.03] p-4 space-y-3"
          >
            {/* D68 follow-up (P1 ux-clarity): inject_context's error
                copy renders in ONE place only, directly under the
                empty textarea (co-located with the red ring). */}
            <p className="text-xs text-[var(--color-text-secondary)] leading-relaxed m-0">
              {ko
                ? "이 hook 이 발동하면 위 텍스트가 모델 컨텍스트에 추가 시스템 입력으로 들어갑니다."
                : "When this hook fires, this text becomes part of the model's context. The model sees it as additional system input."}
            </p>
            <div>
              <FieldLabel>
                {ko ? "주입할 본문" : "Text to inject"}
              </FieldLabel>
              <textarea
                name="injectTemplate"
                // D68 hotfix: only require when inject_context is the
                // chosen action. The peer-checked CSS hides the editor
                // when another action card is selected, but the
                // `required` attribute still gates form submit even
                // for hidden inputs.
                required={state.action === "inject_context"}
                maxLength={16000}
                rows={6}
                defaultValue={state.injectTemplate ?? ""}
                placeholder={ko
                  ? "예: 이 프로젝트는 TDD 필수, any 타입 금지. 모든 commit 메시지는 영어로."
                  : "e.g. This project enforces TDD and bans any types. All commit messages must be in English."}
                spellCheck={false}
                className={inputCls() + " font-mono" + errRingFor("inject_context")}
              />
              {step4ErrAction === "inject_context" && step4ErrHelper && (
                <p
                  data-testid="step4-inject-template-helper"
                  data-step4-err={wizardErr}
                  role="alert"
                  className="mt-1 text-xs text-red-700"
                >
                  {step4ErrHelper}
                </p>
              )}
              <p className="mt-1 text-[11px] text-[var(--color-text-tertiary)] m-0">
                {ko
                  ? "최대 16000자. 더 긴 본문은 저장 단계에서 거부됩니다."
                  : "Max 16000 chars. Longer templates are refused at save."}
              </p>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <FieldLabel>
                  {ko ? "라벨 (한국어, 선택)" : "Label (Korean, optional)"}
                </FieldLabel>
                <input
                  name="injectLabelKo"
                  maxLength={128}
                  defaultValue={state.injectLabelKo ?? ""}
                  placeholder={ko ? "팀 코딩 표준 주입" : "팀 코딩 표준 주입"}
                  className={inputCls()}
                />
              </div>
              <div>
                <FieldLabel>
                  {ko ? "라벨 (영어, 선택)" : "Label (English, optional)"}
                </FieldLabel>
                <input
                  name="injectLabelEn"
                  maxLength={128}
                  defaultValue={state.injectLabelEn ?? ""}
                  placeholder="Inject team coding standards"
                  className={inputCls()}
                />
              </div>
            </div>
          </div>
        </label>
      )
    }
    if (a === "input_rewrite") {
      const kindPick = state.rewriterKind ?? "prefix_strip"
      const matcherForHint = (state.toolScope ?? "").trim()
      const fieldHintEn = matcherForHint === "WebFetch" ? "url"
        : matcherForHint === "Read" || matcherForHint === "Write" || matcherForHint === "Edit"
          ? "file_path"
          : "command"
      return (
        <label key={a} className="block cursor-pointer">
          <input
            type="radio"
            name="action"
            value={a}
            defaultChecked={defaultPick === a}
            required
            className="peer sr-only"
          />
          <span
            data-action-tone="input_rewrite"
            className={
              "block rounded-xl border bg-white p-4 transition-colors " +
              actionCardClasses("input_rewrite")
            }
          >
            <span className="flex items-center justify-between gap-2 mb-1">
              <span className="text-sm font-semibold text-[var(--color-text-primary)]">{labels[a].label}</span>
            </span>
            <span className="block text-xs text-[var(--color-text-secondary)] leading-relaxed">{labels[a].sub}</span>
          </span>
          <div
            data-testid="step4b-rewriter-editor"
            className="hidden peer-checked:block mt-2 rounded-xl border border-[var(--color-accent)]/30 bg-[var(--color-accent)]/[0.03] p-4 space-y-3"
          >
            {step4ErrAction === "input_rewrite" && step4ErrHelper && (
              <div
                data-testid="step4b-rewriter-err-banner"
                data-step4-err={wizardErr}
                role="alert"
                className="rounded-xl border border-red-300 bg-red-50/60 px-3 py-2 text-xs text-red-900"
              >
                {step4ErrHelper}
              </div>
            )}
            <p className="text-xs text-[var(--color-text-secondary)] leading-relaxed m-0">
              {ko
                ? "도구가 실행되기 직전, 입력의 한 필드를 안전하게 수정합니다. 도구 자체는 그대로 실행되며 사람 승인은 필요 없습니다."
                : "Right before the tool runs, mutate one field of its input. The tool still executes; no human in the loop."}
            </p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <FieldLabel>
                  {ko ? "재작성 종류" : "Rewriter kind"}
                </FieldLabel>
                <select
                  name="rewriterKind"
                  defaultValue={kindPick}
                  className={inputCls()}
                >
                  <option value="prefix_strip">
                    {ko ? "접두사 제거 (prefix strip)" : "Strip a prefix"}
                  </option>
                  <option value="scheme_force">
                    {ko ? "URL 스킴 강제 (force scheme)" : "Force URL scheme"}
                  </option>
                  <option value="regex_substitute">
                    {ko ? "정규식 치환 (regex substitute)" : "Regex substitute"}
                  </option>
                </select>
              </div>
              <div>
                <FieldLabel>
                  {ko ? "도구 입력 필드명" : "Tool input field name"}
                </FieldLabel>
                <input
                  name="rewriterField"
                  required
                  maxLength={64}
                  pattern="[A-Za-z_][A-Za-z0-9_]{0,63}"
                  defaultValue={state.rewriterField ?? fieldHintEn}
                  placeholder={fieldHintEn}
                  spellCheck={false}
                  className={inputCls() + " font-mono"}
                />
                <p className="mt-1 text-[11px] text-[var(--color-text-tertiary)] m-0">
                  {ko
                    ? "예: Bash → command, WebFetch → url, Read/Write/Edit → file_path."
                    : "Bash → command, WebFetch → url, Read/Write/Edit → file_path."}
                </p>
              </div>
            </div>
            <div className="space-y-3" data-rewriter-kind="prefix_strip">
              <div>
                <FieldLabel>
                  {ko ? "제거할 접두사" : "Prefix to strip"}
                </FieldLabel>
                <input
                  name="rewriterPrefix"
                  maxLength={2000}
                  defaultValue={state.rewriterPrefix ?? ""}
                  placeholder={ko ? "예: sudo " : "e.g. sudo "}
                  spellCheck={false}
                  className={inputCls() + " font-mono" + errRingFor("input_rewrite", "prefix_strip")}
                />
                {step4ErrCode === "missing_rewriter_prefix" && step4ErrHelper && (
                  <p
                    data-testid="step4-rewriter-prefix-helper"
                    className="mt-1 text-xs text-red-700"
                  >
                    {step4ErrHelper}
                  </p>
                )}
              </div>
              <label className="flex items-start gap-2 text-xs text-[var(--color-text-secondary)]">
                <input
                  type="checkbox"
                  name="rewriterStripRepeat"
                  value="true"
                  defaultChecked={state.rewriterStripRepeat === "true"}
                  className="mt-0.5"
                />
                <span>
                  {ko
                    ? "접두사가 연속해서 여러 번 붙어 있어도 모두 제거 (예: `sudo sudo ls` → `ls`)."
                    : "Peel every consecutive occurrence (e.g. `sudo sudo ls` → `ls`)."}
                </span>
              </label>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3" data-rewriter-kind="scheme_force">
              <div>
                <FieldLabel>
                  {ko ? "기존 스킴" : "From scheme"}
                </FieldLabel>
                <input
                  name="rewriterFrom"
                  maxLength={2000}
                  defaultValue={state.rewriterFrom ?? "http://"}
                  placeholder="http://"
                  spellCheck={false}
                  className={inputCls() + " font-mono" + schemeFromRingCls}
                />
                {schemeFromEmpty && step4ErrHelper && (
                  <p
                    data-testid="step4-rewriter-scheme-from-helper"
                    className="mt-1 text-xs text-red-700"
                  >
                    {step4ErrHelper}
                  </p>
                )}
              </div>
              <div>
                <FieldLabel>
                  {ko ? "강제 스킴" : "To scheme"}
                </FieldLabel>
                <input
                  name="rewriterTo"
                  maxLength={2000}
                  defaultValue={state.rewriterTo ?? "https://"}
                  placeholder="https://"
                  spellCheck={false}
                  className={inputCls() + " font-mono" + schemeToRingCls}
                />
                {schemeToEmpty && step4ErrHelper && (
                  <p
                    data-testid="step4-rewriter-scheme-helper"
                    className="mt-1 text-xs text-red-700"
                  >
                    {step4ErrHelper}
                  </p>
                )}
              </div>
            </div>
            <div className="space-y-3" data-rewriter-kind="regex_substitute">
              <div>
                <FieldLabel>
                  {ko ? "정규식 패턴 (Python re)" : "Regex pattern (Python re)"}
                </FieldLabel>
                <input
                  name="rewriterPattern"
                  maxLength={2000}
                  defaultValue={state.rewriterPattern ?? ""}
                  placeholder="^\\s*sudo\\s+"
                  spellCheck={false}
                  className={inputCls() + " font-mono" + errRingFor("input_rewrite", "regex_substitute")}
                />
                {step4ErrCode === "missing_rewriter_pattern" && step4ErrHelper && (
                  <p
                    data-testid="step4-rewriter-pattern-helper"
                    className="mt-1 text-xs text-red-700"
                  >
                    {step4ErrHelper}
                  </p>
                )}
              </div>
              <div>
                <FieldLabel>
                  {ko ? "치환 본문 (backref: \\1 / \\g<name>)" : "Replacement (backrefs: \\1 / \\g<name>)"}
                </FieldLabel>
                <input
                  name="rewriterReplacement"
                  maxLength={2000}
                  defaultValue={state.rewriterReplacement ?? ""}
                  placeholder=""
                  spellCheck={false}
                  className={inputCls() + " font-mono"}
                />
              </div>
              <div>
                <FieldLabel>
                  {ko ? "최대 치환 횟수 (0 = 전부)" : "Max substitutions (0 = all)"}
                </FieldLabel>
                <input
                  name="rewriterCount"
                  type="number"
                  min={0}
                  max={1000}
                  defaultValue={state.rewriterCount ?? "0"}
                  className={inputCls() + " font-mono"}
                />
              </div>
            </div>
            <p className="text-[11px] text-[var(--color-text-tertiary)] m-0">
              {ko
                ? "재작성기는 한정된 동작만 수행합니다 (코드/jinja 불가). 정책 파일이 유출되어도 임의 입력 조작은 불가능합니다."
                : "The rewriter DSL is bounded. No code-eval, no jinja templates. A leaked policy file cannot translate into arbitrary tool-input mutation."}
            </p>
          </div>
        </label>
      )
    }
    if (a === "run_command") {
      return (
        <label key={a} className="block cursor-pointer">
          <input
            type="radio"
            name="action"
            value={a}
            defaultChecked={defaultPick === a}
            required
            className="peer sr-only"
          />
          <span
            data-action-tone="run_command"
            className={
              "block rounded-xl border bg-white p-4 transition-colors " +
              actionCardClasses("run_command")
            }
          >
            <span className="flex items-center justify-between gap-2 mb-1">
              <span className="text-sm font-semibold text-[var(--color-text-primary)]">{labels[a].label}</span>
            </span>
            <span className="block text-xs text-[var(--color-text-secondary)] leading-relaxed">{labels[a].sub}</span>
          </span>
          {/*
           * D63 review (P1): hand off run_command Step 4b rendering to
           * a client island so:
           *   - inline-vs-attach modes are mutually exclusive,
           *   - the attach lane has a real file upload wired to
           *     /api/scripts (no more hand-paste sha256),
           *   - the inline lane shows a dedicated commandHint i18n
           *     string,
           *   - a "Browse uploaded scripts" link to /scripts surfaces
           *     in the attach lane.
           */}
          <div
            data-testid="step4b-run-command-editor"
            className="hidden peer-checked:block mt-2 rounded-xl border border-[var(--color-accent)]/30 bg-[var(--color-accent)]/[0.03] p-4 space-y-3"
          >
            {step4ErrAction === "run_command" && step4ErrHelper && (
              <div
                data-testid="step4b-run-command-err-banner"
                data-step4-err={wizardErr}
                role="alert"
                className="rounded-xl border border-red-300 bg-red-50/60 px-3 py-2 text-xs text-red-900"
              >
                <p
                  data-testid="step4-run-command-helper"
                  className="m-0"
                >
                  {step4ErrHelper}
                </p>
              </div>
            )}
            <Step4bRunCommandFields
              locale={locale}
              defaultMode={state.runCommandMode}
              defaultRuntime={state.runCommandRuntime}
              defaultBody={state.runCommandBody}
              defaultScriptId={state.runCommandScriptId}
              defaultScriptName={state.runCommandScriptName}
              defaultArgs={state.runCommandArgs}
              defaultTimeoutMs={
                state.runCommandTimeoutMs
                  ? Number.parseInt(state.runCommandTimeoutMs, 10) || 5000
                  : 5000
              }
              defaultFailClosed={state.runCommandFailClosed === "true"}
              inputClassName={inputCls()}
              fieldLabelClassName="block text-xs font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)] mb-1.5"
              hasError={step4ErrCode === "missing_command_or_script"}
              errorRingClassName={errRingCls}
            />
          </div>
        </label>
      )
    }
    // Fallback for block / ask / audit (the simple RadioCard path).
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
  }
  return (
    <StepShell
      heading={t("newPolicy.wizard.step4.heading")}
      helper={header + (ko ? " 어떤 동작을 할까요?" : " what should this policy do?")}
    >
      <form action={action} className="space-y-3">
        <input type="hidden" name="_step" value="4" />
        {/* P2 follow-up (wizard-flow round-trip): carry every state
            field that lives beyond Step 4 (id, description, inject_*).
            The radio for `action` is submitted by the visible input
            so we deliberately omit it from HiddenState (avoid double
            submission of the same name). The visible textarea inside
            the inject_context card owns the canonical `injectTemplate`
            value; the hidden carry only matters when the operator
            picks a non-inject action and we still want to remember
            their previously-authored template. */}
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
          id: state.id,
          description: state.description,
          injectLabelKo: state.injectLabelKo,
          injectLabelEn: state.injectLabelEn,
          // injectTemplate intentionally omitted: when the visible
          // editor renders, its textarea (name="injectTemplate") is
          // the source of truth and a duplicate hidden input would
          // confuse advanceWizard's `formData.entries()` write loop
          // (last-wins, but order isn't guaranteed). When the editor
          // is display:none (operator picked a non-inject action),
          // the textarea still defaults to state.injectTemplate so
          // the value survives the round-trip via the visible input.
        }} />
        {/* D80: Common tier renders block / ask / audit (+ legacy
            strip if it surfaces) in the operator's first read. The
            derivative archetypes (inject_context / input_rewrite /
            run_command) drop behind an Advanced expander further down
            with the same layered-disclosure pattern Step 1 uses. */}
        {commonActions.map((a) => renderActionCard(a))}
        {/* D80: Advanced tier (inject_context / input_rewrite /
            run_command). Same layered-disclosure pattern Step 1 uses
            (D61): default-collapsed, per-user persisted via
            localStorage key `magi_cp.step4_advanced_open`. Force-open
            when the operator's pick already lives in this tier so the
            selected card is visible on a back-nav round-trip. */}
        {advancedActions.length > 0 && (
          <Step4ActionAdvanced
            headerLabel={advancedHeaderLabel}
            advancedCount={advancedActions.length}
            expandLabel={advancedExpandLabel}
            collapseLabel={advancedCollapseLabel}
            forceOpen={advancedForceOpen}
          >
            {advancedActions.map((a) => renderActionCard(a))}
          </Step4ActionAdvanced>
        )}
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
  // D82c fix: widen the variant union to the FULL `ChipVariant` so the
  // type checker forces each case to opt into a variant. The prior
  // `'path' | 'shacl-stub'` narrow let the `llm_critic` branch silently
  // fall through to `'path'`, which inserted raw `tool_response.output`
  // (no braces) and broke the runtime marker substitutor. Authors who
  // first composed via Step 3 (correct {marker}) and later tweaked via
  // Step 6 silently corrupted the criterion. Same gap for regex —
  // chips were splicing the path into the pattern textarea, the very
  // thing the Step 3 split was added to prevent.
  let chipVariant: ChipVariant = "path"
  let textareaId = "w-step6-sub-config"

  switch (kind) {
    case "regex":
      label = ko ? "정규식 패턴" : "Regex pattern"
      helper = ko ? "Python `re` 문법. 비우면 condition 이 만족 안 됨." : "Python `re` syntax. Empty pattern means no condition."
      element = "input"
      name = "pattern"
      initial = state.pattern ?? ""
      placeholder = "AKIA[A-Z0-9]{16}"
      // D82c fix: regex authoring in the inline panel mirrors Step 3's
      // split. We hide the chip row entirely for regex here (the panel
      // has no `<select>` companion to route the chip click to), so a
      // path chip click cannot land curly braces in the regex pattern.
      // Authors who want to scope to a specific field jump back to
      // Step 3 where the picker + chips render in concert.
      useChips = false
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
      // D82c fix: lock the inline llm_critic chips to the marker
      // variant so a click inserts `{tool_response.output}` (not the
      // bare path). The runtime `_MARKER_RX` only matches braces; a
      // raw path would leak literal `tool_response.output` text into
      // the prompt.
      chipVariant = "llm-marker"
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
              {state.conditionKind === "evidence_ref" && evidenceList.length > 0 && (() => {
                // D57e P2 (step6 stale-ref display): the Step 3
                // picker filters wiredSteps via
                // `verifierFiresOnLifecycle`. The state-build seam
                // prunes evidenceRefs against the same filter. But if
                // a future call site bypasses the prune (e.g. a
                // hand-crafted server-action body) the review summary
                // would still surface a "source_allowlist=pass" line
                // under a Stop policy, which the runtime never
                // enforces. We mirror the picker filter here as a
                // second defensive sieve and surface a small inline
                // warning when an item is dropped so the operator
                // knows to revisit Step 3.
                const kept = evidenceList.filter((v) =>
                  verifierFiresOnLifecycle(v, event),
                )
                const droppedFromReview = evidenceList.filter((v) =>
                  !verifierFiresOnLifecycle(v, event),
                )
                return (
                  <>
                    <ul className="mt-1 space-y-0.5 list-disc pl-5">
                      {kept.map((v) => {
                        const desc = wiredSteps.find((w) => w.step === v)?.description ?? ""
                        return <li key={v}><code className="font-mono">{v}</code> {desc && <span className="text-[var(--color-text-tertiary)]">· {desc}</span>}</li>
                      })}
                    </ul>
                    {droppedFromReview.length > 0 && (
                      <p
                        data-testid="step6-evidence-list-stale-warning"
                        data-stale-verifier-steps={droppedFromReview.join(",")}
                        className="mt-1 text-[11px] text-amber-800"
                      >
                        {ko
                          ? `참고: ${droppedFromReview.join(", ")} 은 이 라이프사이클에서 발동하지 않습니다. Step 3 에서 다시 확인하세요.`
                          : `Heads up: ${droppedFromReview.join(", ")} do not fire on this lifecycle. Revisit Step 3.`}
                      </p>
                    )}
                  </>
                )
              })()}
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
      <form action={action} data-testid="wizard-save-form">
        <HiddenState state={state} />
        {/* P4: pack-membership picker on the guided wizard's final
            step — writes the hidden `pack_ids` input saveWizard reads
            via `_parsePackIds(formData)`. Same component the raw editor
            and conversational compose reuse.

            Legacy-guard: gated behind the pack-centric runtime flag.
            With the flag off the gate fires enabled policies regardless
            of pack membership, so the picker's "an unpacked policy fires
            in no session" hint would mislead the operator, so hide it
            and let saves flow through the legacy enabled path. */}
        {isPackCentricEnabled() && (
          <div className="mb-3">
            <PackMultiSelect
              locale={locale}
              labels={{
                heading: t("packs.picker.heading"),
                hint: t("packs.picker.hint"),
                search: t("packs.picker.search"),
                alwaysOn: t("packs.alwaysOn"),
                orphan: t("packs.orphan"),
                loading: t("packs.picker.loading"),
                empty: t("packs.picker.empty"),
                suggested: t("packs.picker.suggested"),
              }}
            />
          </div>
        )}
        {/* D74a follow-up: stable testid on the Step 6 save button so
            the e2e harness can target the real save form instead of
            silently picking the InlineSubConfigPanel's inline-edit
            submit (which lives inside the same <main>, fires the
            advance action, and would loop the operator back to Step
            5/6 on regex / llm_critic / shacl archetypes). */}
        <NextButton label={t("newPolicy.wizard.savePolicy")} testId="wizard-save" />
      </form>

      {/* D53b: Dry-run replay against the last 24h of ledger rows.
          The guided wizard's Step 6 surfaces the full draft IR; we
          re-derive it here from `state` (mirrors the persistDraft
          path) and feed it to the panel. The button is disabled
          when the wizard has not produced an id yet (saving would
          fail validation for the same reason). */}
      {/* D57f-1: inject_context has no per-call gate to replay against
          the ledger. The dry-run panel is meaningless for this archetype
          (the runtime shim emits additionalContext unconditionally,
          there's nothing to "would have blocked"). */}
      {state.action !== "inject_context" && (
        <DryRunPanel
          locale={locale}
          ir={state.id
            ? buildGuidedDraftForDryRun(state)
            : null}
          disabled={!state.id}
          action={(state.action === "strip" ? "strip" : (state.action ?? "audit")) as "block" | "ask" | "audit" | "strip"}
        />
      )}
    </StepShell>
  )
}

// Suppress unused warnings (these are reserved for future kind support
// once the backend grows the explicit fetch/allowlist condition kinds).
void FETCH_TOOLS
