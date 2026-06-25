/**
 * D82a follow-up: pure helpers for the wizard's top-left Back arrow.
 *
 * Lives in its own sibling module so wizard-wiring.test.ts can import
 * `previousLiveStep` directly and assert behavioural invariants over
 * concrete (state, current) -> expected tuples. The prior revision
 * grepped the function body for substrings, which gave false confidence:
 * a future regression that mangled the math (e.g. returned 1 from
 * Step 4 when only Step 3 should be skipped, or returned 3 from Step 3
 * when the lifecycle skipped Step 2) would keep every grepped substring
 * and pass silently.
 *
 * The lifecycle sets here MUST stay in sync with page.tsx
 * (TOOL_CONTEXT_LIFECYCLES + CONTEXT_INJECTION_EXCLUDED_LIFECYCLES).
 * They are duplicated here, not re-exported from page.tsx, because
 * page.tsx pulls in heavy Next/React server-component dependencies that
 * a unit test cannot import. The full Lifecycle string union is loosely
 * typed via `string` so the test does not need to import the type
 * either; runtime correctness is preserved by the page-level use sites
 * which still pass the Lifecycle union through.
 */

/** D56c: lifecycles whose tool context is meaningful at Step 2. */
const _TOOL_CONTEXT_LIFECYCLES: ReadonlySet<string> = new Set<string>([
  "before_tool_use",
  "after_tool_use",
])

/** D59 + D70: eight lifecycles where inject_context is silent-fail-open
 *  (specialized hookSpecificOutput shape OR end-of-life with no
 *  downstream same-session model turn). */
const _CONTEXT_INJECTION_EXCLUDED_LIFECYCLES: ReadonlySet<string> =
  new Set<string>([
    // D59 — specialized hookSpecificOutput shape
    "elicitation",
    "elicitation_result",
    "worktree_create",
    "message_display",
    // D70 — end-of-life events with no downstream same-session turn
    "pre_final",
    "stop_failure",
    "session_end",
    "subagent_stop",
  ])

export function lifecycleHasToolScopeForNav(
  life: string | undefined,
): boolean {
  return life !== undefined && _TOOL_CONTEXT_LIFECYCLES.has(life)
}

export function lifecycleAllowsInjectContextForNav(
  life: string | undefined,
): boolean {
  return life !== undefined && !_CONTEXT_INJECTION_EXCLUDED_LIFECYCLES.has(life)
}

/** Minimal state shape `previousLiveStep` needs. Kept as a structural
 *  type so callers (page.tsx with the full WizardState; the test with
 *  hand-built fixtures) both fit without conversion. */
export interface PreviousLiveStepState {
  lifecycle?: string
  action?: string
}

/** D82a: previous LIVE step from `current`, honouring the same skip
 *  rules GuidedWizard uses to advance forward.
 *
 *  Skip rules mirror (in order):
 *    Step 2 -> Step 3 when lifecycle has no tool scope.
 *    Step 3 -> Step 4 when action=inject_context AND lifecycle is
 *                     NOT in CONTEXT_INJECTION_EXCLUDED_LIFECYCLES.
 *    Step 3 -> Step 4 when action=input_rewrite.
 *    Step 3 -> Step 4 when action=run_command.
 *
 *  Returns null when there is no previous step (Step 1). */
export function previousLiveStep(
  state: PreviousLiveStepState,
  current: number,
): number | null {
  if (current <= 1) return null
  if (current === 6) return 5
  if (current === 5) return 4
  if (current === 4) {
    const a = state.action
    const skipsStep3 =
      (a === "inject_context"
        && lifecycleAllowsInjectContextForNav(state.lifecycle))
      || a === "input_rewrite"
      || a === "run_command"
    if (skipsStep3) {
      return lifecycleHasToolScopeForNav(state.lifecycle) ? 2 : 1
    }
    return 3
  }
  if (current === 3) {
    return state.lifecycle && !lifecycleHasToolScopeForNav(state.lifecycle)
      ? 1
      : 2
  }
  if (current === 2) return 1
  return null
}
