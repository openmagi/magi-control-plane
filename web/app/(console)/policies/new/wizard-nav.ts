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
 *     `lifecycle`, and `action` for skip-math purposes.
 *   - The `step` query param is updated to the previous live step
 *     (per `previousLiveStep`). EVERY other param the URL already
 *     carried is preserved verbatim (`lifecycle`, `toolScope`,
 *     `conditionKind`, `pattern`, `llmCriterion`, `evidence_refs`,
 *     `action`, ...) so a round-trip Back-then-Forward does not
 *     silently drop operator state — EXCEPT condition-side fields
 *     when the resolved action is inject_context / input_rewrite /
 *     run_command, which GuidedWizard scrubs from `state` (see
 *     page.tsx). Mirroring that scrub here keeps Back URLs and the
 *     wizard's state-side EditLinks byte-consistent for the same
 *     perceived wizard state.
 *   - Returns `null` when there is no previous live step (Step 1 or
 *     a stale URL with no step). The component caller renders a
 *     disabled `<button>` in that case.
 *
 * D82c overrides: when GuidedWizard resolves `state.action` or
 *   `state.lifecycle` from a `draft=` IR (not from the URL), the
 *   raw `searchParams.action`/`searchParams.lifecycle` are
 *   `undefined`, so the skip math here diverges from the visual
 *   `backStep` that the page computes from `state`. Callers may
 *   pass `navOverrides` to force the resolved values into the skip
 *   math (and into the scrub decision). The emitted URL still
 *   serializes the raw `searchParams.lifecycle`/`searchParams.action`
 *   verbatim so the Back URL does not silently rewrite operator-
 *   facing query state — only the skip math and scrub logic see
 *   the overrides.
 *
 * Used both by `WizardHeader` (so the Back link wires the same URL
 * the test asserts on) and by wizard-wiring.test.ts. Pinning the
 * contract here closes the install-review report that "Back from
 * Step 4 stays on Step 4" by making any future regression
 * (e.g. dropping `lifecycle`, returning the current step, etc.)
 * fail a behavioural test instead of riding through silently.
 */
/** D82c: condition-side fields the GuidedWizard state-build scrubs
 *  when action is inject_context / input_rewrite / run_command. The
 *  list MUST stay in sync with the three scrub blocks in
 *  page.tsx's GuidedWizard (search for "state.conditionKind = \"none\"").
 *  Keys are URL param names, NOT WizardState field names — `conditionKind`
 *  matches both because the URL forwards the state name verbatim.
 *  `evidence_refs` is the URL form; `evidenceRefs` is the state form;
 *  both are dropped because the wizard emits the underscore variant
 *  via buildWizardHref. */
const _CONDITION_SCRUB_URL_KEYS: ReadonlySet<string> = new Set<string>([
  "conditionKind",
  "pattern",
  "llmCriterion",
  "shaclTtl",
  "fetchDomain",
  "allowlist",
  "evidence_refs",
  "evidenceRefs",
])

const _CONDITION_SCRUB_ACTIONS: ReadonlySet<string> = new Set<string>([
  "inject_context",
  "input_rewrite",
  "run_command",
])

export interface BuildBackHrefOverrides {
  /** Resolved lifecycle from WizardState — used for skip math when the
   *  URL `lifecycle` param is missing (draft-prefill case). */
  lifecycle?: string
  /** Resolved action from WizardState — used for skip math + scrub
   *  decision when the URL `action` param is missing (draft-prefill). */
  action?: string
}

export function buildBackHrefFromSearchParams(
  searchParams: Record<string, string | undefined>,
  navOverrides?: BuildBackHrefOverrides,
): string | null {
  const rawStep = searchParams.step
  const parsed = rawStep !== undefined ? Number(rawStep) : 1
  const current = Number.isFinite(parsed) && parsed >= 1
    ? Math.floor(parsed)
    : 1
  // D82c: prefer caller-resolved state values when present so the skip
  // math runs on the same fields the visual `backStep` used. This
  // matters for draft-prefill URLs (?draft=<IR>&step=4) where the URL
  // has no `action`/`lifecycle` query but `state` does.
  const navState: PreviousLiveStepState = {
    lifecycle: navOverrides?.lifecycle ?? searchParams.lifecycle,
    action: navOverrides?.action ?? searchParams.action,
  }
  const prev = previousLiveStep(navState, current)
  if (prev == null) return null
  // Preserve every param the URL already carried; only `step` flips.
  // We rebuild via URLSearchParams so the encoding mirrors what
  // buildWizardHref in page.tsx emits (URL-encoded, stable order).
  const out = new URLSearchParams()
  // D82c P2 fix: treat empty-string `mode=` as missing too — a
  // hand-edited or malformed URL with `mode=` would survive the
  // bare `??` check and re-emit `mode=` in the Back href, which
  // resolves to the PickerLanding on the next render and silently
  // kicks the operator back to the authoring-mode picker.
  const rawMode = searchParams.mode
  const mode = rawMode != null && rawMode.length > 0 ? rawMode : "guided"
  // Make sure `mode=guided` is set first to mirror buildWizardHref's
  // canonical layout. If the inbound URL omitted it (a fresh deep
  // link with just step+lifecycle, say), default to guided so the
  // landing surface stays on the wizard rather than bouncing back to
  // the picker.
  out.set("mode", mode)
  out.set("step", String(prev))
  // D82c P2 fix: mirror GuidedWizard's state-build scrub. When the
  // resolved action is inject_context / input_rewrite / run_command,
  // GuidedWizard hard-clears the condition-side fields from `state`
  // (see page.tsx scrub blocks). The wizard's own EditLinks
  // (buildWizardHref(state, ...)) therefore drop them too, but the
  // Back href previously copied them through verbatim — a shared
  // Back URL leaked stale authoring state, and a Back-then-Edit
  // round-trip produced two URLs for the same perceived state.
  // Mirror the scrub here so the canonical URL forms match.
  const resolvedAction = navOverrides?.action ?? searchParams.action
  const scrubConditionFields = resolvedAction != null
    && _CONDITION_SCRUB_ACTIONS.has(resolvedAction)
  for (const [k, v] of Object.entries(searchParams)) {
    if (v === undefined || v === "") continue
    if (k === "step" || k === "mode") continue
    if (scrubConditionFields && _CONDITION_SCRUB_URL_KEYS.has(k)) continue
    out.set(k, v)
  }
  return `/policies/new?${out.toString()}`
}
