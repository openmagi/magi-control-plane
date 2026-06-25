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

/**
 * D82b: pure helper that derives the Back-link URL from the wizard's
 * current `searchParams`. Lives next to `previousLiveStep` so the
 * Back behaviour is testable end-to-end (URL in -> URL out) without
 * pulling page.tsx's full server-component dependency tree into the
 * test bundle.
 *
 * Contract:
 *
 *   - Input is the wizard's `searchParams` as a plain
 *     `Record<string, string | undefined>` (Next App-Router shape).
 *     The function must NOT depend on any field beyond `step`,
 *     `lifecycle`, and `action` because those are the only fields
 *     `previousLiveStep` consults.
 *   - The `step` query param is updated to the previous live step
 *     (per `previousLiveStep`). EVERY other param the URL already
 *     carried is preserved verbatim (`lifecycle`, `toolScope`,
 *     `conditionKind`, `pattern`, `llmCriterion`, `evidence_refs`,
 *     `action`, ...) so a round-trip Back-then-Forward does not
 *     silently drop operator state.
 *   - Returns `null` when there is no previous live step (Step 1 or
 *     a stale URL with no step). The component caller renders a
 *     disabled `<button>` in that case.
 *
 * Used both by `WizardHeader` (so the Back link wires the same URL
 * the test asserts on) and by wizard-wiring.test.ts. Pinning the
 * contract here closes the install-review report that "Back from
 * Step 4 stays on Step 4" by making any future regression
 * (e.g. dropping `lifecycle`, returning the current step, etc.)
 * fail a behavioural test instead of riding through silently.
 */
export function buildBackHrefFromSearchParams(
  searchParams: Record<string, string | undefined>,
): string | null {
  const rawStep = searchParams.step
  const parsed = rawStep !== undefined ? Number(rawStep) : 1
  const current = Number.isFinite(parsed) && parsed >= 1
    ? Math.floor(parsed)
    : 1
  const navState: PreviousLiveStepState = {
    lifecycle: searchParams.lifecycle,
    action: searchParams.action,
  }
  const prev = previousLiveStep(navState, current)
  if (prev == null) return null
  // Preserve every param the URL already carried; only `step` flips.
  // We rebuild via URLSearchParams so the encoding mirrors what
  // buildWizardHref in page.tsx emits (URL-encoded, stable order).
  const out = new URLSearchParams()
  // Make sure `mode=guided` is set first to mirror buildWizardHref's
  // canonical layout. If the inbound URL omitted it (a fresh deep
  // link with just step+lifecycle, say), default to guided so the
  // landing surface stays on the wizard rather than bouncing back to
  // the picker.
  out.set("mode", searchParams.mode ?? "guided")
  out.set("step", String(prev))
  for (const [k, v] of Object.entries(searchParams)) {
    if (v === undefined || v === "") continue
    if (k === "step" || k === "mode") continue
    out.set(k, v)
  }
  return `/policies/new?${out.toString()}`
}
