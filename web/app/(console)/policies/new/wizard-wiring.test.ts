import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"
import { previousLiveStep } from "./wizard-nav"

/**
 * P9 (D49) wizard-wiring invariants.
 *
 * These guard the URL+sessionStorage contract that the cumulative-
 * judgment steering tip lives inside. They are easy-to-break in any
 * future refactor of the wizard's URL state, so we lock them as
 * source-level assertions (cheap, no full React Testing Library setup
 * needed — the runtime behaviour of the client island is covered by
 * the SteeringAwareField unit tests).
 *
 * Specifically:
 *   - The wizard URL must NOT carry a `keepKind=1` query param. The
 *     dismissal lives in sessionStorage now (per-tab); a Cmd-R or a
 *     pasted link must not survive a dismissal.
 *   - Step 3 must mount the client SteeringAwareField for each of
 *     regex / llm_critic / shacl (i.e. the heuristic actually has a
 *     chance to fire on each kind's text).
 *   - SteeringAwareField must not be mounted for evidence_ref / none /
 *     fetch_domain / domain_allowlist (the heuristic does not apply
 *     and a stray mount would mean a stale URL).
 *   - advanceWizard server action must NOT scrub or write a `keepKind`
 *     param (proves the URL contract is clean).
 */
describe("policies/new wizard — P9 steering wiring", () => {
  const src = readFileSync(
    path.join(__dirname, "page.tsx"),
    "utf-8",
  )

  it("URL state never carries a keepKind param", () => {
    expect(src).not.toMatch(/keepKind/)
  })

  it("SteeringAwareField is mounted for regex/llm_critic/shacl", () => {
    // Each kind must mount exactly one client island.
    const regexMounts = src.match(/kind="regex"/g) ?? []
    const llmMounts   = src.match(/kind="llm_critic"/g) ?? []
    const shaclMounts = src.match(/kind="shacl"/g) ?? []
    expect(regexMounts.length).toBe(1)
    expect(llmMounts.length).toBe(1)
    expect(shaclMounts.length).toBe(1)
  })

  it("SteeringAwareField is NOT mounted for non-payload kinds", () => {
    // Sanity: evidence_ref / none / fetch_domain / domain_allowlist
    // must never appear as a `kind=` prop on the steering island.
    for (const k of ["evidence_ref", "none", "fetch_domain", "domain_allowlist"]) {
      const re = new RegExp(`<SteeringAwareField[\\s\\S]*?kind="${k}"`)
      expect(src.match(re)).toBeNull()
    }
  })

  it("each SteeringAwareField forwards its native form-field name", () => {
    expect(src).toMatch(/name="pattern"[\s\S]*?fieldElement="input"|fieldElement="input"[\s\S]*?name="pattern"/)
    expect(src).toMatch(/name="llmCriterion"/)
    expect(src).toMatch(/name="shaclTtl"/)
  })

  it("advanceWizard does not touch keepKind", () => {
    // Server action must not write or delete a keepKind URL param.
    const action = src.slice(src.indexOf("async function advanceWizard"))
    expect(action).not.toMatch(/keepKind/)
  })

  it("imports SteeringAwareField from the colocated client island", () => {
    expect(src).toMatch(
      /import SteeringAwareField from "\.\/_components\/SteeringAwareField"/,
    )
  })

  // ── D52d ──────────────────────────────────────────────────────
  it("D52d: imports VerifierFieldChecks from the shared console component", () => {
    expect(src).toMatch(
      /import \{\s*VerifierFieldChecks\s*\} from ".+VerifierFieldChecks"/,
    )
  })

  it("D52d: surfaces the field_checks tree inline for each verifier in the evidence_ref picker", () => {
    // The component is rendered inside the wiredSteps.{filter}.map
    // iteration so every author-visible verifier card gets its own
    // tree. D57e widened this from a bare `.map` to `.filter(...).map`
    // so a verifier with no lifecycle-matching field_checks group
    // is hidden; the surface contract still holds.
    expect(src).toMatch(
      /wiredSteps[\s\S]*?\.map\([\s\S]*?VerifierFieldChecks[\s\S]*?showFooter/,
    )
  })

  // D52d follow-up: pin the boundary between author-flow (/verifiers/new
  // creates a custom verifier with field_checks) and consumer-flow
  // (wizard evidence_ref picker). Today the wizard hides
  // enforcement=preview entries on purpose: a custom verifier has no
  // runtime binding yet, so binding a policy to one would compile a
  // step the runtime can't satisfy. The catalog expander still
  // surfaces the authored field_checks tree (per the
  // VerifierFieldChecks fieldChecksOverride seam) so the operator can
  // confirm their authoring landed; promoting the entry into the
  // wizard waits on a runtime binding hook. This test pins that
  // boundary so a future widen-the-filter change is intentional, not
  // incidental.
  it("D52d follow-up: wizard wiredSteps filter remains enforcing-only", () => {
    expect(src).toMatch(
      /p\.enforcement === "enforcing"[\s\S]*?wiredSteps\.push/,
    )
    // Negative: no custom-source short-circuit smuggled into the same
    // loop. Catching this drift early because including preview
    // entries would compile policies the runtime can't honor.
    expect(src).not.toMatch(
      /p\.enforcement === "preview"[\s\S]*?wiredSteps\.push/,
    )
  })

  // D53b: dry-run wiring on every authoring mode.
  // D56b: NL CompileResultBlock retired; the conversational compose
  // surface owns its own dry-run pane (covered by
  // ConversationalCompose.test.ts).
  it("D53b: Guided Step6Review renders DryRunPanel with the derived draft", () => {
    expect(src).toContain("buildGuidedDraftForDryRun")
    // The panel only enables when the wizard has an id (would not
    // pass save validation otherwise).
    expect(src).toMatch(/disabled=\{!state\.id\}/)
  })

  it("D53b: Raw/Advanced mode passes a dryRunSlot to PolicyBuilder", () => {
    // The slot receives the current draft + `isValid` and renders
    // the DryRunPanel; the parent disables the button when the
    // PolicyBuilder reports validation errors.
    expect(src).toContain("dryRunSlot={")
    expect(src).toMatch(/DryRunPanel[\s\S]*?ir=\{isValid \?/)
  })

  // ── D56a ─────────────────────────────────────────────────────
  // Prebuilt "Use this" now lands on Step 6 review with a prefilled
  // IR; Step 6 surfaces per-field Edit jumps + inline editors for
  // sub-config that lives inside the IR but isn't its own wizard
  // step. These guard the URL contract.

  it("D56a: GuidedWizard reads searchParams.draft and merges into the WizardState", () => {
    // The merge must use draft as a FALLBACK (URL params win) so an
    // Edit jump from Step 6 to an earlier step and back doesn't
    // re-override the operator's edit. We pin the function name
    // (_irToWizardState) and the call site (`searchParams.draft`).
    expect(src).toContain("_irToWizardState")
    expect(src).toContain("_parseDraftQuery(searchParams.draft)")
    // The state record uses ?? / || fallbacks against draftState.
    expect(src).toMatch(/lifecycle:\s*lifecycle\s*\?\?\s*draftState\?\.lifecycle/)
    // D56d follow-up: toolScope is canonicalized at the state-build
    // seam (multi-tool URL collapses to first entry); the seam still
    // sources raw value from searchParams.toolScope || draftState.
    expect(src).toMatch(/searchParams\.toolScope\s*\|\|\s*draftState\?\.toolScope/)
    expect(src).toMatch(/toolScope:\s*normalizedToolScope/)
    expect(src).toMatch(/conditionKind:\s*conditionKind\s*\?\?\s*draftState\?\.conditionKind/)
    expect(src).toMatch(/action:\s*action\s*\?\?\s*draftState\?\.action/)
  })

  it("D56a / D58: IR-to-WizardState mapping covers the prebuilt event surface", () => {
    // The 5 prebuilts emit PreToolUse / PostToolUse / Stop. D58
    // collapsed the per-event switch into a single LIFECYCLE_TO_EVENT
    // / EVENT_TO_LIFECYCLE pair so a future event addition only
    // touches one table. Pin the canonical forward-map entries so
    // a refactor cannot silently lose a prebuilt event mapping.
    expect(src).toMatch(/before_tool_use:\s*"PreToolUse"/)
    expect(src).toMatch(/after_tool_use:\s*"PostToolUse"/)
    expect(src).toMatch(/pre_final:\s*"Stop"/)
    // The reverse map IS the IR -> wizard projection.
    expect(src).toContain("EVENT_TO_LIFECYCLE[ir.trigger?.event ?? \"\"]")
    // step requires -> evidence_ref conditionKind (the prebuilt
    // catalog's whole shape) must round-trip cleanly.
    expect(src).toMatch(/conditionKind = "evidence_ref"/)
    // Suggested id strips the `prebuilt/` slug so the operator
    // picks a fresh one at Step 5.
    expect(src).toMatch(/rawId\.startsWith\("prebuilt\/"\)/)
  })

  it("D56a: Step 6 renders an EditLink for each editable field row", () => {
    // 5 rows: name (5), lifecycle (1), tool scope (2), condition
    // (3), action (4). Tool scope is conditionally rendered when
    // lifecycle !== "pre_final", so source-level we expect 5
    // EditLink calls in Step6Review.
    const stepStart = src.indexOf("function Step6Review")
    expect(stepStart).toBeGreaterThan(-1)
    // Step6Review grew with the per-row EditLink wiring + sub-config
    // inline editors (D56a). Slice covers the whole body but stops
    // before the next top-level helper so we don't pick up siblings.
    const stepBody = src.slice(stepStart, stepStart + 12_000)
    expect(stepBody.match(/<EditLink /g)?.length).toBeGreaterThanOrEqual(5)
    // Each Edit jump pins a step number 1..5.
    for (const n of [1, 2, 3, 4, 5]) {
      const re = new RegExp(`step=\\{${n}\\}`)
      expect(stepBody).toMatch(re)
    }
  })

  it("D56a: Step 6 surfaces an inline editor for sub-config (regex/llm/shacl/fetch/allowlist)", () => {
    // Inline sub-config editor mounts inside the condition row.
    // We pin the panel name + the 5 sub-config field names the
    // panel can edit.
    expect(src).toContain("InlineSubConfigPanel")
    expect(src).toMatch(/case "regex":[\s\S]*?name = "pattern"/)
    expect(src).toMatch(/case "llm_critic":[\s\S]*?name = "llmCriterion"/)
    expect(src).toMatch(/case "shacl":[\s\S]*?name = "shaclTtl"/)
    expect(src).toMatch(/case "fetch_domain":[\s\S]*?name = "fetchDomain"/)
    expect(src).toMatch(/case "domain_allowlist":[\s\S]*?name = "allowlist"/)
    // The inline form posts to advanceAction with _step=5 (so
    // advanceWizard's stepIn+1 lands the operator back on Step 6).
    expect(src).toMatch(/InlineSubConfigPanel[\s\S]*?advanceAction/)
    expect(src).toMatch(/<input type="hidden" name="_step" value="5" \/>/)
  })

  it("D56a: buildWizardHref persists every WizardState field — Edit jumps round-trip", () => {
    // Step 6 -> Edit jump -> Step N -> Back -> Step 6 must
    // preserve every field. We pin every field name appears in
    // buildWizardHref's param list (the canonical URL serializer)
    // so a refactor adding a new field can't silently break the
    // round-trip.
    const start = src.indexOf("function buildWizardHref")
    expect(start).toBeGreaterThan(-1)
    // D57f-2 widened buildWizardHref with rewriter fields; bump the
    // slice window so the trailing `description` write stays in scope.
    const body = src.slice(start, start + 3000)
    for (const field of [
      "lifecycle", "conditionKind", "toolScope",
      "fetchDomain", "allowlist", "pattern", "llmCriterion",
      "shaclTtl", "action", "id", "description",
    ]) {
      expect(body).toContain(field)
    }
    // evidence_refs joins into the CSV form the wizard reads back.
    expect(body).toMatch(/evidenceRefs\.join\(","\)/)
  })

  // ── D56c ─────────────────────────────────────────────────────
  // Wizard Step 1 now exposes all 8 CC hook events. The Lifecycle
  // union, LIFECYCLES array, and LIFECYCLE_TO_EVENT map must stay
  // in lockstep with the cloud's matrix.LEGAL_COMBINATIONS table.
  describe("D56c: lifecycle expansion covers all 8 CC hooks", () => {
    it("Lifecycle type union has all 8 slugs", () => {
      // type Lifecycle = "..." | ... matches the literal union block.
      const m = src.match(/type Lifecycle\s*=\s*([\s\S]+?)const LIFECYCLES/)
      expect(m).not.toBeNull()
      const block = m![1]
      for (const slug of [
        "before_tool_use", "after_tool_use", "pre_final",
        "subagent_stop", "user_prompt", "pre_compact",
        "session_start", "session_end",
      ]) {
        expect(block).toContain(`"${slug}"`)
      }
    })

    it("LIFECYCLES array enumerates all 8 slugs", () => {
      const m = src.match(/const LIFECYCLES:[\s\S]*?=\s*\[([\s\S]+?)\]/)
      expect(m).not.toBeNull()
      const arr = m![1]
      for (const slug of [
        "before_tool_use", "after_tool_use", "pre_final",
        "subagent_stop", "user_prompt", "pre_compact",
        "session_start", "session_end",
      ]) {
        expect(arr).toContain(`"${slug}"`)
      }
    })

    it("LIFECYCLE_TO_EVENT maps each slug to its CC event name", () => {
      const m = src.match(/LIFECYCLE_TO_EVENT[\s\S]*?=\s*\{([\s\S]+?)\}/)
      expect(m).not.toBeNull()
      const body = m![1]
      for (const [slug, event] of [
        ["before_tool_use", "PreToolUse"],
        ["after_tool_use", "PostToolUse"],
        ["pre_final", "Stop"],
        ["subagent_stop", "SubagentStop"],
        ["user_prompt", "UserPromptSubmit"],
        ["pre_compact", "PreCompact"],
        ["session_start", "SessionStart"],
        ["session_end", "SessionEnd"],
      ]) {
        const re = new RegExp(`${slug}\\s*:\\s*"${event}"`)
        expect(body).toMatch(re)
      }
    })

    it("Step 1 renders the 8 lifecycle cards", () => {
      // D61: Step 1 surface is owned by `_components/Step1LifecyclePicker.tsx`
      // + the canonical group composition in
      // `_components/step1-lifecycle-groups.ts`. The pre-D58 8-slug
      // shape lives across `COMMON_GROUP.members` (4 slugs) +
      // selected `ADVANCED_GROUPS` members (the other 4). Pin the
      // 8-slug invariant against the file that the picker actually
      // imports so a future refactor that drops one of the legacy 8
      // from the rendered surface fails the gate. The full 30-slug
      // composition + no-overlap is asserted in the picker's own
      // test; this gate guards the legacy 8-slug minimum.
      const groupsSrc = readFileSync(
        path.join(__dirname, "_components", "step1-lifecycle-groups.ts"),
        "utf-8",
      )
      const members = [
        "before_tool_use", "after_tool_use",
        "user_prompt", "pre_compact", "pre_final",
        "subagent_stop", "session_start", "session_end",
      ]
      for (const slug of members) {
        expect(groupsSrc).toContain(`"${slug}"`)
      }
    })

    it("ACTIONS_BY_LIFECYCLE narrows audit-only events per the matrix", () => {
      // pre_final + subagent_stop + session_start + session_end are
      // audit-only per matrix.LEGAL_COMBINATIONS. Saving with block
      // on one of those must be refused before the round-trip.
      // D57f-1: inject_context is a 5th archetype legal on every
      // lifecycle whose CC hookSpecificOutput accepts
      // additionalContext. The pin shifts from "audit-only" to "block
      // refused, inject_context allowed where channel applies."
      // D63: run_command joins inject_context as a 6th archetype legal
      // on every lifecycle (uniform CC stdout JSON contract).
      // D70: pre_final / subagent_stop / session_end / stop_failure
      // are now in `CONTEXT_INJECTION_EXCLUDED_LIFECYCLES` because
      // they fire at end-of-life with no downstream same-session
      // model turn for additionalContext to land in. Their
      // `inject_context` entry is dropped by `_withInjectContextIf`.
      // session_start stays legal (the session is still alive). The
      // base ACTIONS_BY_LIFECYCLE input lists are pinned below; the
      // derivation through `_withInjectContextIf` then strips
      // inject_context for the excluded rows.
      const m = src.match(/ACTIONS_BY_LIFECYCLE[\s\S]*?=\s*\{([\s\S]+?)\n\}/)
      expect(m).not.toBeNull()
      const body = m![1]
      // The wizard now keys each row through `_withInjectContextIf`
      // so the same exclusion set governs both `ACTIONS_BY_LIFECYCLE`
      // and `ACTIONS_BY_COMBINATION`. The base array per row is still
      // present inside the helper call; the call is the on-disk
      // shape; the helper filters at module load.
      expect(body).toMatch(/_withInjectContextIf\("pre_final",\s*\[\s*"audit",\s*"inject_context",\s*"run_command"\s*\]\)/)
      expect(body).toMatch(/_withInjectContextIf\("subagent_stop",\s*\[\s*"audit",\s*"inject_context",\s*"run_command"\s*\]\)/)
      expect(body).toMatch(/_withInjectContextIf\("session_start",\s*\[\s*"audit",\s*"inject_context",\s*"run_command"\s*\]\)/)
      expect(body).toMatch(/_withInjectContextIf\("session_end",\s*\[\s*"audit",\s*"inject_context",\s*"run_command"\s*\]\)/)
      // user_prompt has the full pre-event action set + inject_context + run_command.
      expect(body).toMatch(/_withInjectContextIf\("user_prompt",\s*\[\s*"block",\s*"ask",\s*"audit",\s*"inject_context",\s*"run_command"\s*\]\)/)
      // pre_compact has block + audit + inject_context + run_command.
      expect(body).toMatch(/_withInjectContextIf\("pre_compact",\s*\[\s*"block",\s*"audit",\s*"inject_context",\s*"run_command"\s*\]\)/)
      // D57f-1: block / ask are still NOT legal on the audit-only
      // lifecycles — the new archetype rides alongside audit, it
      // doesn't loosen the block/ask gates. Pin against the lifecycle
      // entry to its closing paren so a future widening lands here.
      expect(body).not.toMatch(/_withInjectContextIf\("pre_final",\s*\[\s*"block"/)
      expect(body).not.toMatch(/_withInjectContextIf\("session_start",\s*\[\s*"block"/)
    })

    it("saveWizard refuses matrix-illegal action choices", () => {
      // Pinning the validation block keeps the client-side guard from
      // silently disappearing on a future refactor (the cloud's
      // canonical guard is matrix.validate_combination).
      // D56d: widened to a per-(lifecycle, matcher_class) check so
      // (PreToolUse, wildcard, block) — lifecycle-legal but matrix-
      // illegal — gets caught here. Pin the combination helper instead
      // of the now-superseded lifecycle-only lookup.
      // D57f-1: slice widened (originally 6500) because the
      // inject_context early-return branch sits above the
      // matrix-action gate. The 06-24 follow-up grew the branch with
      // a template-length guard, a matrix-action guard, locale-aware
      // description fallback, and the prose comments documenting the
      // ux-internal-leak fix — so the slice widened again to keep the
      // tail (matrix-action gate) inside the window.
      // D57f-2: input_rewrite added another early-return branch
      // to saveWizard so the matrix-action gate sits further down;
      // widen the slice to 16000 to keep the gate inside the window.
      // D63: run_command added a 3rd early-return branch (script
      // upload + runtime + args + timeout + fail_closed), so widen
      // the slice again to keep the matrix-action gate visible.
      const start = src.indexOf("async function saveWizard")
      expect(start).toBeGreaterThan(-1)
      const body = src.slice(start, start + 22000)
      expect(body).toMatch(/allowedActionsForCombination\(lifecycle,\s*toolScope\)/)
      expect(body).toMatch(/!allowedActions\.includes\(action\)/)
      // D56d (P1 #2): also rejects matrix-illegal matcher classes
      // (e.g. after_tool_use + wildcard / tool_alt).
      expect(body).toMatch(/allowedMatcherClassesForLifecycle\(lifecycle\)/)
    })

    it("Step 2 auto-skips for every no-tool-context lifecycle", () => {
      // The advance / GuidedWizard step-routing widened from a
      // hardcoded `=== "pre_final"` to lifecycleHasToolScope; pin both
      // call sites so the broadened skip can't silently regress to
      // pre_final-only.
      expect(src).toContain("lifecycleHasToolScope")
      const advanceStart = src.indexOf("async function advanceWizard")
      const advanceBody = src.slice(advanceStart, advanceStart + 6000)
      expect(advanceBody).toMatch(/!lifecycleHasToolScope\(lifecycle\)/)
    })

    it("_irToWizardState round-trips every CC event the wizard understands", () => {
      // Pre-D58 there were 8 explicit `case "<Event>": lifecycle =
      // "<slug>"` statements. D58 collapsed them into the
      // LIFECYCLE_TO_EVENT map (and its computed reverse
      // EVENT_TO_LIFECYCLE) so a 30-event surface stays maintainable
      // in one place. We now pin each forward-map entry instead —
      // the pre-D58 8 events plus the D58 additions land in the
      // same table, and `_irToWizardState` reads the reverse map.
      const cases = [
        // pre-D58
        ["PreToolUse", "before_tool_use"],
        ["PostToolUse", "after_tool_use"],
        ["Stop", "pre_final"],
        ["SubagentStop", "subagent_stop"],
        ["UserPromptSubmit", "user_prompt"],
        ["PreCompact", "pre_compact"],
        ["SessionStart", "session_start"],
        ["SessionEnd", "session_end"],
        // D58 additions
        ["PostToolUseFailure", "post_tool_use_failure"],
        ["PostToolBatch", "post_tool_batch"],
        ["PermissionRequest", "permission_request"],
        ["PermissionDenied", "permission_denied"],
        ["UserPromptExpansion", "user_prompt_expansion"],
        ["PostCompact", "post_compact"],
        ["Elicitation", "elicitation"],
        ["ElicitationResult", "elicitation_result"],
        ["SubagentStart", "subagent_start"],
        ["StopFailure", "stop_failure"],
        ["Setup", "setup"],
        ["Notification", "notification"],
        ["TeammateIdle", "teammate_idle"],
        ["TaskCreated", "task_created"],
        ["TaskCompleted", "task_completed"],
        ["ConfigChange", "config_change"],
        ["WorktreeCreate", "worktree_create"],
        ["WorktreeRemove", "worktree_remove"],
        ["InstructionsLoaded", "instructions_loaded"],
        ["CwdChanged", "cwd_changed"],
        ["FileChanged", "file_changed"],
        ["MessageDisplay", "message_display"],
      ]
      for (const [ev, life] of cases) {
        const re = new RegExp(`${life}:\\s*"${ev}"`)
        expect(src).toMatch(re)
      }
      // The reverse map (event-name keyed) is what
      // `_irToWizardState` actually reads. Pin the construction so
      // a future refactor flipping the source of truth has to
      // intentionally update this assertion.
      expect(src).toContain("const EVENT_TO_LIFECYCLE")
    })
  })

  // ── D56d (single-tool wizard) ───────────────────────────────────
  // Step 2 authors one tool per policy. The matcher-class set shrinks
  // to {tool, mcp_tool, wildcard}; the chip row is a radio group, the
  // MCP free-text input takes one name, and Step 3's payload-field
  // suggestions are guaranteed to map to a specific tool's schema.
  // Multi-tool coverage = separate policies.
  describe("D56d: single-tool matcher in Step 2", () => {
    it("WizardState.toolScope is a single string (not an array)", () => {
      // Pin the type declaration so a future refactor flipping toolScope
      // to string[] would have to update this assertion intentionally.
      const m = src.match(/interface WizardState\s*\{([\s\S]+?)\n\}/)
      expect(m).not.toBeNull()
      const body = m![1]
      expect(body).toMatch(/toolScope\?:\s*string\b/)
      // Negative: must not be string[] / Array<string> / ReadonlyArray.
      expect(body).not.toMatch(/toolScope\?:\s*(?:string\[\]|Array<string>|ReadonlyArray<string>)/)
    })

    it("deriveMatcher returns single tool or wildcard (no alternation)", () => {
      // The previous wizard joined multi-pick with `|`. Pin the
      // single-tool collapse so a future regression to alternation is
      // intentional in the diff.
      const start = src.indexOf("function deriveMatcher")
      expect(start).toBeGreaterThan(-1)
      const body = src.slice(start, start + 1200)
      expect(body).not.toMatch(/tools\.join\("\|"\)/)
      // Picks parseCsv[0] as the single matcher name.
      expect(body).toMatch(/parseCsv\([\s\S]*?\)\[0\]/)
    })

    it("MatcherClassKey drops tool_alt", () => {
      // tool_alt (alternation matcher A|B|C) is retired with the
      // single-tool wizard. The matcher-class union must be exactly
      // {tool, mcp_tool, wildcard}.
      const m = src.match(/type MatcherClassKey\s*=\s*([^\n]+)/)
      expect(m).not.toBeNull()
      const union = m![1]
      expect(union).toContain('"tool"')
      expect(union).toContain('"mcp_tool"')
      expect(union).toContain('"wildcard"')
      expect(union).not.toContain('"tool_alt"')
    })

    it("ACTIONS_BY_COMBINATION has no tool_alt rows", () => {
      // Strict source pin so the matrix table cannot silently grow
      // a tool_alt row again (would re-introduce multi-tool save
      // surface that the wizard no longer authors).
      const m = src.match(/const ACTIONS_BY_COMBINATION[\s\S]*?=\s*\{([\s\S]+?)\n\}/)
      expect(m).not.toBeNull()
      const body = m![1]
      expect(body).not.toContain("tool_alt")
    })

    it("Step 2 mounts the D70 ToolCombobox (single autocomplete surface)", () => {
      // D70: the legacy `toolScope_chip` radio grid + separate MCP
      // free-text input were retired in favour of a single autocomplete
      // combobox that covers every CC built-in + free-typed MCP /
      // custom names. The combobox component owns one hidden form
      // input named `toolScope_custom` so advanceWizard's existing
      // seam still picks the typed value up. The Step 2 body therefore
      // imports + mounts ToolCombobox and no longer renders a chip
      // radio surface or a separate MCP text input.
      const start = src.indexOf("function Step2ToolScope")
      const end = src.indexOf("\n}\n", start)
      expect(start).toBeGreaterThan(-1)
      expect(end).toBeGreaterThan(start)
      const body = src.slice(start, end)
      // Mounts the client island.
      expect(body).toMatch(/<ToolCombobox[\s>]/)
      // No legacy chip radio in Step 2.
      expect(body).not.toMatch(/name="toolScope_chip"/)
      // No separate raw MCP text input either; the combobox owns
      // the only `toolScope_custom` write surface (which lives inside
      // the imported component, NOT inline here).
      expect(body).not.toMatch(/name="toolScope_custom"[\s\S]*?maxLength=\{256\}/)
      // File-level import is present (top of page.tsx).
      expect(src).toMatch(/import\s+ToolCombobox\s+from\s+"\.\/_components\/ToolCombobox"/)
    })

    it("Step 2 surfaces the picked-tool helper hint", () => {
      // The brief mandates a helper line when a specific tool is
      // picked, so the operator understands that Step 3 will tailor
      // its check suggestions per-tool.
      const start = src.indexOf("function Step2ToolScope")
      const end = src.indexOf("\n}\n", start)
      const body = src.slice(start, end)
      expect(body).toContain("step2-tool-helper")
      // Mentions both the picked-tool variable and the multi-policy
      // hint copy.
      expect(body).toContain("Step 3 will suggest checks specific to")
      expect(body).toContain("separate policies")
    })

    it("advanceWizard collapses Step 2 submission to a single tool", () => {
      // Step 2's form submits `toolScope_chip` (radio pick) and
      // `toolScope_custom` (single MCP name). advanceWizard picks one,
      // not the merge of many.
      const start = src.indexOf("async function advanceWizard")
      const body = src.slice(start, start + 4000)
      expect(body).not.toMatch(/scopeChipsRaw/)
      // No CSV merge: single pick wins.
      expect(body).not.toMatch(/merged\.join\(","\)/)
      // D56d follow-up (P1): the typed MCP value wins so the runtime
      // matches the user-facing helper copy ("If both are set, the MCP
      // name wins"). The chip stays as a fallback.
      expect(body).toMatch(/scopeCustom\s*\|\|\s*scopeChip/)
    })
  })

  // ── D56d follow-up: tool-scope state-shape consistency ──────────
  // The lens-asks: a stale CSV URL `?toolScope=Bash,Edit` must not
  // result in Step 6 display ≠ saved matcher. The fixes hinge on
  // normalizing toolScope at the GuidedWizard state-build seam plus a
  // hard refusal in saveWizard so a server-action body that bypasses
  // normalization cannot persist silent data loss.
  describe("D56d follow-up: tool-scope state-shape consistency", () => {
    it("GuidedWizard state-build seam normalizes multi-tool toolScope to first entry", () => {
      // The seam must split on either `,` or `|`, take the first
      // entry, and stash the original raw value on
      // _droppedAlternation so Step 2 can surface a banner. Pin
      // every step of the normalization so a future refactor that
      // drops the seam is intentional.
      //
      // D57e P1: the seam grew an evidenceRefs lifecycle prune block
      // between the toolScope normalization and the WizardState
      // literal, so the 2500-char slice the original test used no
      // longer reached `toolScope: normalizedToolScope`. Bumped to
      // 5000 chars so both invariants stay in the window without
      // restructuring the seam.
      const buildStart = src.indexOf("const draftState = _irToWizardState")
      expect(buildStart).toBeGreaterThan(-1)
      const body = src.slice(buildStart, buildStart + 5000)
      // splits on `,` OR `|`
      expect(body).toMatch(/split\(\s*\/\[,\|\]\/\s*\)/)
      // canonical state.toolScope is the normalized form
      expect(body).toMatch(/toolScope:\s*normalizedToolScope/)
      // dropped value lands on _droppedAlternation for the Step 2 banner
      expect(body).toMatch(/_droppedAlternation/)
    })

    it("WizardState carries an optional _droppedAlternation for the banner", () => {
      const m = src.match(/interface WizardState\s*\{([\s\S]+?)\n\}/)
      expect(m).not.toBeNull()
      expect(m![1]).toMatch(/_droppedAlternation\?:\s*string\b/)
    })

    it("_irToWizardState surfaces _droppedAlternation when matcher is an alternation", () => {
      // The IR mapper must detect `|` in the inbound matcher, split,
      // collapse to first, AND stash the original on droppedAlternation
      // so Step 2 can render the banner.
      // D57f-1: slice widened to 4500 chars because the
      // context_injection discriminator branch sits above the
      // evidence-shape mapper.
      // D57f-2: slice widened (4500 → 7000) because the input_rewrite
      // round-trip branch sits between the context_injection discriminator
      // and the evidence-shape mapper.
      const start = src.indexOf("function _irToWizardState")
      expect(start).toBeGreaterThan(-1)
      const body = src.slice(start, start + 7000)
      expect(body).toMatch(/droppedAlternation/)
      expect(body).toMatch(/matcher\.includes\("\|"\)/)
    })

    it("saveWizard refuses a multi-tool toolScope before classification", () => {
      // The hard refusal must fire BEFORE matcherClassForToolScope
      // collapses to first-token, otherwise a stale `Bash,Edit` would
      // silently persist a Bash matcher under the multi-tool URL.
      // Pin the early-refusal block and the redirect target.
      // Slice widened to accommodate the 06-24 inject_context branch
      // growth above the multi-tool refusal.
      // D57f-2: slice widened (8000 → 16000) because the input_rewrite
      // early-return branch grew saveWizard above the multi-tool refusal.
      const start = src.indexOf("async function saveWizard")
      expect(start).toBeGreaterThan(-1)
      const body = src.slice(start, start + 16000)
      // Multi detection (CSV or alternation)
      expect(body).toMatch(/parseCsv\(toolScope\)\.length\s*>\s*1/)
      expect(body).toMatch(/toolScope\.includes\("\|"\)/)
      // Redirect target = Step 2 with invalid_input flash
      expect(body).toMatch(/carry\.set\("step",\s*"2"\)/)
      expect(body).toMatch(/carry\.set\("err",\s*"invalid_input"\)/)
    })

    it("saveWizard normalizes toolScope under a wildcard-only lifecycle", () => {
      // A stale toolScope rides along on a bookmark; the
      // wildcard-only lifecycle (pre_final, etc.) does not surface
      // Step 2. Normalize toolScope to undefined before the matcher-
      // class check so the save can proceed without bouncing the user
      // back to Step 2 and dropping their wizard progress.
      // Slice widened (5000→8000) per the 06-24 inject_context branch
      // growth (template-length guard + matrix-action guard + locale-
      // aware fallback push the wildcard-only normalize step further
      // down the function body).
      // D57f-2: slice widened (8000 → 16000) per input_rewrite branch
      // growth above the wildcard-only normalize step.
      const start = src.indexOf("async function saveWizard")
      const body = src.slice(start, start + 16000)
      expect(body).toMatch(/!lifecycleHasToolScope\(lifecycle\)/)
      expect(body).toMatch(/toolScope = undefined/)
    })

    it("advanceWizard refuses Step 2 'specific' with no chip and no custom", () => {
      // P2 follow-up (matrix gate, empty specific): submitting Step 2
      // with mode=specific but both inputs blank must bounce back to
      // Step 2 with invalid_input, NOT silently advance with no
      // toolScope (which would lose all later progress when saveWizard
      // bounces from Step 6).
      const start = src.indexOf("async function advanceWizard")
      const body = src.slice(start, start + 4000)
      // No-pick branch redirects to step=2 with err carry; pin the
      // exact redirect target so a refactor that drops the early
      // refusal is obvious.
      expect(body).toMatch(/params\.set\("step",\s*"2"\)/)
      expect(body).toMatch(/params\.set\("err",\s*"invalid_input"\)/)
    })

    it("suggestPolicyId slugifies the canonical matcher, not raw toolScope", () => {
      // The P2 follow-up demands the policy id reflects the
      // single-tool matcher that will actually save, not a stale CSV
      // string. deriveMatcher(state) is the canonical seam.
      const start = src.indexOf("function suggestPolicyId")
      expect(start).toBeGreaterThan(-1)
      const body = src.slice(start, start + 1400)
      expect(body).toMatch(/deriveMatcher\(state\)/)
      // No more direct slugify of state.toolScope
      expect(body).not.toMatch(/state\.toolScope\.toLowerCase/)
    })

    it("suggestPolicyId appends the action archetype as a third segment (D57d)", () => {
      // D57d: the auto-suggested id surfaces WHAT the policy does
      // alongside WHEN and WHICH TOOL. Format is
      // `{lifecycle-kebab}-{tool-kebab-or-skipped}-{action}/v1`.
      // We pin the source-level shape because the function isn't
      // exported and the test file is source-inspection-only (the
      // wizard-wiring tests above all follow the same pattern).
      const start = src.indexOf("function suggestPolicyId")
      // Slice generously past the function body. The helper grew
      // additional D57d follow-up comments (lifeSlug source pin,
      // slice-before-strip note, conditionKind kebab note) so a 2200
      // window now stops short of `segments.join("-")`.
      const body = src.slice(start, start + 3000)
      // The action archetype must be read off state.action and
      // composed into the id segments. A bare regex pinning the
      // identifier is enough — refactoring the join can keep the
      // identifier but rename the local variable.
      expect(body).toMatch(/state\.action/)
      // Segments join with "-" and the suffix is "/v1".
      expect(body).toMatch(/segments\.join\("-"\)[\s\S]*\/v1/)
      // Pin the lifeSlug source rule. The lifecycle slug is the raw
      // lifecycle key with `_`→`-` (NOT the LIFECYCLE_TO_EVENT kebab),
      // so e.g. `before_tool_use`→`before-tool-use`, not
      // `pre-tool-use`. A future refactor that swaps the source must
      // update both this assertion and the contract examples below.
      expect(body).toMatch(/life\.replace\(\/_\/g, "-"\)/)
      // The behavioural contract from the brief (mirrored as comment
      // markers so a refactor that drops a case is loud):
      //   suggestPolicyId({lifecycle: "before_tool_use",
      //                    toolScope:"Bash", action:"block"})
      //     -> "before-tool-use-bash-block/v1"
      //   suggestPolicyId({lifecycle:"user_prompt",
      //                    action:"block"})
      //     -> wildcard skips the tool segment -> "user-prompt-block/v1"
      //     (pre_final + block is NOT a legal pairing — Step 4 only
      //      surfaces `audit` for pre_final, so picking `pre_final` +
      //      `audit` is what the wizard can actually emit:
      //      "pre-final-audit/v1".)
      //   suggestPolicyId({lifecycle:"after_tool_use",
      //                    toolScope:"Grep"})
      //     -> action undefined -> back-compat
      //        "after-tool-use-grep/v1"
      // Pin the D57d marker so future edits to the helper land here
      // with an explicit intent.
      expect(body).toContain("D57d")
    })

    it("Step 2 surfaces the _droppedAlternation banner", () => {
      const start = src.indexOf("function Step2ToolScope")
      const end = src.indexOf("\n}\n", start)
      const body = src.slice(start, end)
      expect(body).toContain("step2-dropped-alternation-banner")
      expect(body).toMatch(/droppedAlternation\s*&&/)
    })

    it("Step 2 helper hint is past-tense (URL-persisted state, refresh on submit)", () => {
      // The hint is server-rendered from URL state and does NOT mirror
      // live form selection. Use past-tense copy so the operator
      // understands a re-pick takes effect on submit. The brief
      // explicitly mandates honest copy here.
      const start = src.indexOf("function Step2ToolScope")
      const end = src.indexOf("\n}\n", start)
      const body = src.slice(start, end)
      expect(body).toContain("step2-tool-helper")
      // English copy: "Currently saved as ... pick a different one above and submit to refresh"
      expect(body).toContain("Currently saved as")
      expect(body).toContain("submit to refresh")
    })

    it("availableFields surfaces tool-specific paths for Bash and WebFetch", async () => {
      // Wiring-level test: Step 2 picks a tool → Step 3 reads tool-
      // specific payload fields. Pin the contract end-to-end so a
      // future refactor that drops the matcher arg lands intentionally.
      const mod = await import("../../../../lib/payload-schemas")
      const bashPaths = mod.availableFields("PreToolUse", "Bash").map((f) => f.path)
      expect(bashPaths).toContain("tool_input.command")
      expect(bashPaths).not.toContain("tool_input.url")
      const webFetchPaths = mod.availableFields("PreToolUse", "WebFetch").map((f) => f.path)
      expect(webFetchPaths).toContain("tool_input.url")
      expect(webFetchPaths).not.toContain("tool_input.command")
      // Unknown MCP tool degrades to generic tool_input
      const mcpPaths = mod.availableFields("PreToolUse", "mcp__court__file").map((f) => f.path)
      expect(mcpPaths).toContain("tool_input")
      expect(mcpPaths).not.toContain("tool_input.command")
      expect(mcpPaths).not.toContain("tool_input.url")
    })

    it("Step 3 + Step 6 InlineSubConfigPanel pass canonical toolScope into payloadAvailableFields", () => {
      // The brief: pin `ccMatcher = lifecycleHasToolScope(lifecycle) ?
      // state.toolScope : undefined` so a future refactor that drops
      // the matcher arg lands intentionally. state.toolScope is
      // already canonical because GuidedWizard normalized it at the
      // state-build seam.
      const occurrences = src.match(
        /const ccMatcher = lifecycleHasToolScope\(lifecycle\) \? state\.toolScope : undefined/g,
      ) ?? []
      // Step 3 + Step 6 InlineSubConfigPanel
      expect(occurrences.length).toBeGreaterThanOrEqual(2)
    })

    it("matcherClassForToolScope comment is honest about its inputs", () => {
      // The previous comment promised "saveWizard's matcher-class
      // guard refuses anything that survives parsing as multi" but
      // the guard did no such check. The fix replaces the comment
      // with the truthful narrative.
      const start = src.indexOf("function matcherClassForToolScope")
      const body = src.slice(start, start + 1200)
      expect(body).not.toContain("refuses anything that survives parsing as multi")
      // The new comment names the state-build seam as the
      // normalization point and the early-refusal in saveWizard
      // as defense-in-depth. Allow comment line-wrapping between
      // "state-build" and "seam" since comment-formatter or future
      // editor may re-wrap.
      expect(body).toMatch(/state-build[\s\n/]*seam/)
    })
  })

  describe("D57e: Step 3 verifier picker filters by lifecycle", () => {
    // D57e: the verifier picker (kind=evidence_ref) must only show
    // verifiers whose descriptor declares a field_checks group for
    // the wizard's current lifecycle. A Stop-lifecycle wizard
    // should hide source_allowlist (PreToolUse-only); a PreToolUse-
    // lifecycle wizard should hide citation_verify (Stop-only).
    it("imports verifierFiresOnLifecycle from the descriptor mirror", () => {
      // Single source of truth for "does this verifier fire on this
      // lifecycle": the descriptor mirror in @/lib/verifier-
      // descriptors. Local re-derivation would drift.
      expect(src).toContain("verifierFiresOnLifecycle")
      expect(src).toMatch(/from\s+["']@\/lib\/verifier-descriptors["']/)
    })

    it("filters wiredSteps through verifierFiresOnLifecycle inside the evidence_ref branch", () => {
      // The picker map starts with `wiredSteps.filter((w) =>
      // verifierFiresOnLifecycle(w.step, ccEvent)).map(...)` so the
      // dropped verifiers do not render. The filter uses the same
      // `ccEvent` the per-lifecycle conditional uses (LIFECYCLE_TO_
      // EVENT lookup), so the picker tracks Step 1's choice.
      expect(src).toMatch(
        /wiredSteps[\s\n]*\.filter\(\(w\)\s*=>\s*verifierFiresOnLifecycle\(w\.step,\s*ccEvent\)\)/,
      )
    })

    it("surfaces a 'no verifier matches this lifecycle' note when the filter drops everything", () => {
      expect(src).toContain("step3-verifier-picker-no-lifecycle-match")
    })

    it("surfaces a dropped-count note when the filter hides some but not all verifiers", () => {
      expect(src).toContain("step3-verifier-picker-dropped-note")
    })

    it("threads the lifecycle into the inline VerifierFieldChecks render", () => {
      // The matching group expands by default and other groups dim
      // out, so the operator sees the context relevant to their
      // policy without losing the cross-lifecycle picture.
      expect(src).toMatch(/VerifierFieldChecks[\s\S]{0,400}lifecycle=\{ccEvent\}/)
    })
  })

  /* D57f-1 — `inject_context` action archetype.
   *
   * The wizard's 5th action archetype maps to a ContextInjectionPolicy
   * instead of an EvidencePolicy. The CC hookSpecificOutput JSON schema
   * accepts `additionalContext` on every hook event, so the archetype
   * is universally legal at the wizard's authoring surface; the
   * runtime gate emits the additionalContext JSON keyed on the chosen
   * event so CC applies it whichever way that event's downstream
   * consumer reads it.
   */
  describe("D57f-1 — inject_context archetype", () => {
    it("Action type includes inject_context as a 5th archetype", () => {
      expect(src).toMatch(
        /type Action = "block" \| "ask" \| "audit" \| "strip" \| "inject_context"/,
      )
    })

    it("ACTIONS_BY_LIFECYCLE legalizes inject_context on every non-excluded lifecycle", () => {
      const m = src.match(/ACTIONS_BY_LIFECYCLE[\s\S]*?=\s*\{([\s\S]+?)\n\}/)
      expect(m).not.toBeNull()
      const body = m![1]
      // Every non-excluded lifecycle row must include inject_context in
      // its base array. We pin a sample across the 5 family bands the
      // matrix recognizes so a future narrowing landing on only a
      // subset is loud. The `_withInjectContextIf` wrapper filters
      // out the excluded set at module load.
      expect(body).toMatch(/_withInjectContextIf\("before_tool_use",[^\]]*"inject_context"/)
      expect(body).toMatch(/_withInjectContextIf\("after_tool_use",[^\]]*"inject_context"/)
      expect(body).toMatch(/_withInjectContextIf\("user_prompt",[^\]]*"inject_context"/)
      expect(body).toMatch(/_withInjectContextIf\("session_start",[^\]]*"inject_context"/)
      expect(body).toMatch(/_withInjectContextIf\("notification",[^\]]*"inject_context"/)
      expect(body).toMatch(/_withInjectContextIf\("file_changed",[^\]]*"inject_context"/)
    })

    it("CONTEXT_INJECTION_EXCLUDED_LIFECYCLES carries the 8 silent-fail-open lifecycles (D59 + D70)", () => {
      // The exclusion set drives both ACTIONS_BY_LIFECYCLE and
      // ACTIONS_BY_COMBINATION so a future re-add of any of these
      // cannot silently re-introduce the D69 wizard-matrix
      // divergence. Pin the literal members so a deletion is loud.
      const m = src.match(/CONTEXT_INJECTION_EXCLUDED_LIFECYCLES[\s\S]*?new Set<Lifecycle>\(\[([\s\S]+?)\]\)/)
      expect(m).not.toBeNull()
      const body = m![1]
      // D59 specialized-channel set.
      expect(body).toContain('"elicitation"')
      expect(body).toContain('"elicitation_result"')
      expect(body).toContain('"worktree_create"')
      expect(body).toContain('"message_display"')
      // D70 end-of-life set.
      expect(body).toContain('"pre_final"')
      expect(body).toContain('"stop_failure"')
      expect(body).toContain('"session_end"')
      expect(body).toContain('"subagent_stop"')
    })

    it("WizardState carries inject template + KO/EN label fields", () => {
      const m = src.match(/interface WizardState\s*\{([\s\S]+?)\n\}/)
      expect(m).not.toBeNull()
      const body = m![1]
      expect(body).toMatch(/injectTemplate\?:\s*string/)
      expect(body).toMatch(/injectLabelKo\?:\s*string/)
      expect(body).toMatch(/injectLabelEn\?:\s*string/)
    })

    it("Step 4 renders the inline template editor for inject_context", () => {
      // The CSS-only peer-checked reveal sits in a <span> tagged with
      // data-testid="step4b-inject-editor" so the operator can assert
      // the editor in browser-driven tests.
      expect(src).toContain("step4b-inject-editor")
      // The editor must include the template textarea + two label
      // inputs (ko + en).
      expect(src).toMatch(/name="injectTemplate"/)
      expect(src).toMatch(/name="injectLabelKo"/)
      expect(src).toMatch(/name="injectLabelEn"/)
    })

    it("Step 4 helper copy matches the brief's UX text", () => {
      // "When this hook fires, this text becomes part of the model's
      // context. The model sees it as additional system input."
      expect(src).toContain("becomes part of the model's context")
      expect(src).toContain("Inject extra context")
      // Korean copy: "추가 정보 주입"
      expect(src).toContain("추가 정보 주입")
    })

    it("GuidedWizard skips Step 3 forward to Step 4 when action=inject_context", () => {
      // Skipping condition picker is the brief: "Verifier picker on
      // Step 3: context_injection has no verifier requirement; Step 3
      // is skipped when action = inject_context." We jump effectiveStep
      // forward.
      const start = src.indexOf("function GuidedWizard")
      const end = src.indexOf("\n}\n", start)
      const body = src.slice(start, end)
      expect(body).toMatch(
        /effectiveStep === 3 && state\.action === "inject_context"/,
      )
    })

    it("saveWizard branches into ContextInjectionDraft for inject_context", () => {
      // The branch must early-return BEFORE the matrix-action guard +
      // the requires-derivation pipeline, so its draft uses the
      // type=context_injection discriminator and POSTs to the same
      // /policies/{id} surface evidence policies use.
      const start = src.indexOf("async function saveWizard")
      expect(start).toBeGreaterThan(-1)
      const body = src.slice(start, start + 6500)
      expect(body).toMatch(/action === "inject_context"/)
      expect(body).toMatch(/type: "context_injection"/)
      expect(body).toMatch(/template/)
      expect(body).toMatch(/injectTemplate/)
    })

    it("_irToWizardState recognizes a context_injection IR discriminator", () => {
      // A prebuilt or saved context_injection round-trips into a
      // WizardState with action=inject_context and the template
      // pre-filled. The mapper detects the `type` field on the raw
      // dict (PolicyDraft only narrows the evidence shape).
      const start = src.indexOf("function _irToWizardState")
      expect(start).toBeGreaterThan(-1)
      const body = src.slice(start, start + 4500)
      expect(body).toMatch(/rawType === "context_injection"/)
      expect(body).toMatch(/action:\s*"inject_context"/)
    })

    it("Step 6 review summary names the injected template snippet", () => {
      // plainSummary surfaces "this policy injects the following text
      // into the model's context: ..." for inject_context. The
      // truncation cap (80 chars) keeps the card tidy.
      const start = src.indexOf("function plainSummary")
      const end = src.indexOf("\n}\n", start)
      const body = src.slice(start, end)
      expect(body).toMatch(/act === "inject_context"/)
      expect(body).toMatch(/injectTemplate/)
      expect(body).toMatch(/injects the following text/)
    })

    it("Step 6 DryRunPanel is hidden when action=inject_context", () => {
      // The dry-run panel replays the last 24h of ledger rows through
      // the gate. inject_context has no gate to replay against, so the
      // panel is meaningless for the archetype.
      const start = src.indexOf("function Step6Review")
      const end = src.indexOf("\n}\n", start)
      const body = src.slice(start, end)
      expect(body).toMatch(/state\.action !== "inject_context"/)
    })
  })

  /* D59 — `inject_context` archetype narrowed for specialized hooks.
   *
   * Four hooks carry a SPECIALIZED hookSpecificOutput shape where
   * `additionalContext` is silently ignored at runtime:
   *   - Elicitation       → hookSpecificOutput.elicitationDecision
   *   - ElicitationResult → action / content override before MCP reply
   *   - WorktreeCreate    → hookSpecificOutput.worktreePath
   *   - MessageDisplay    → display-only
   *
   * The wizard renders the inject_context card with a disabled state
   * + tooltip on these four lifecycles instead of hiding it, so the
   * operator understands why the archetype is unavailable. The
   * matching `ContextInjectionPolicy.validate()` raise on the cloud
   * is the canonical refusal; this dashboard surface short-circuits
   * the round-trip.
   */
  describe("D59 — inject_context disabled on specialized hooks", () => {
    it("CONTEXT_INJECTION_EXCLUDED_LIFECYCLES names the 4 lifecycle slugs", () => {
      const m = src.match(
        /CONTEXT_INJECTION_EXCLUDED_LIFECYCLES[\s\S]*?=\s*new Set<Lifecycle>\(\[([\s\S]+?)\]\)/,
      )
      expect(m).not.toBeNull()
      const body = m![1]
      for (const slug of [
        "elicitation", "elicitation_result",
        "worktree_create", "message_display",
      ]) {
        expect(body).toContain(`"${slug}"`)
      }
    })

    it("lifecycleAllowsInjectContext returns false for each excluded lifecycle", () => {
      // Source-level pin (the test harness is source-inspection-only,
      // matching the existing wizard-wiring patterns above). The
      // helper must be defined and named so the rest of the wizard
      // can call it; we pin the predicate body to ensure a future
      // refactor flipping the polarity is intentional.
      const start = src.indexOf("function lifecycleAllowsInjectContext")
      expect(start).toBeGreaterThan(-1)
      const body = src.slice(start, start + 400)
      expect(body).toMatch(/CONTEXT_INJECTION_EXCLUDED_LIFECYCLES\.has\(life\)/)
      // Negated has() = "allows" semantic
      expect(body).toMatch(/!CONTEXT_INJECTION_EXCLUDED_LIFECYCLES/)
    })

    it("Step 4 renders the inject_context card disabled when lifecycle excludes it", () => {
      // The disabled branch must (a) check
      // `!lifecycleAllowsInjectContext(lifecycle)`, (b) render the
      // radio input with `disabled`, (c) carry a per-event tooltip via
      // `title=` AND the data-testid hook so a browser-driven test
      // can assert the disabled card.
      const start = src.indexOf("function Step4Action")
      const end = src.indexOf("/* ─── Step 5", start)
      expect(start).toBeGreaterThan(-1)
      expect(end).toBeGreaterThan(start)
      const body = src.slice(start, end)
      expect(body).toMatch(
        /a === "inject_context"[\s\S]*?!lifecycleAllowsInjectContext\(lifecycle\)/,
      )
      expect(body).toContain("step4-inject-context-disabled")
      expect(body).toMatch(/disabled\s*\n?\s*aria-disabled="true"/)
      // D59 follow-up (#14): the disabled branch funnels the lifecycle
      // through `asContextInjectionExcludedLifecycle` so the call to
      // `injectContextDisabledCopy` receives a narrowed
      // `ContextInjectionExcludedLifecycle` union (TS exhaustiveness).
      // Pin both the narrowing helper and the copy call.
      expect(body).toContain("asContextInjectionExcludedLifecycle(lifecycle)")
      expect(body).toMatch(
        /injectContextDisabledCopy\(\s*narrowedExcluded\s*,\s*locale\s*\)/,
      )
      // D59 follow-up (#11): screen-reader path uses aria-describedby
      // wired to a stable id alongside the visual title attribute.
      expect(body).toMatch(/aria-describedby=\{tipId\}/)
    })

    it("Step 4 unreachable template editor when picker disabled (no peer-checked sibling render)", () => {
      // The disabled branch must NOT render the Step 4b inline editor.
      // The editor's `data-testid="step4b-inject-editor"` only appears
      // in the *active* branch. Pin source-level distance so a future
      // merge that hoists the editor into the disabled branch is loud.
      const start = src.indexOf("function Step4Action")
      const end = src.indexOf("/* ─── Step 5", start)
      const body = src.slice(start, end)
      // The disabled-branch slice ends BEFORE the active branch
      // begins; capture both and assert the editor sits in the
      // active branch only.
      const disabledStart = body.indexOf("step4-inject-context-disabled")
      const activeStart = body.indexOf("step4b-inject-editor")
      expect(disabledStart).toBeGreaterThan(-1)
      expect(activeStart).toBeGreaterThan(disabledStart)
      // D59 follow-up (#7 test rigor): the order check above is
      // necessary but not sufficient. A future refactor that hoists
      // the editor (`step4b-inject-editor`) into the disabled
      // <label> body (e.g. for a "preview what the operator typed"
      // affordance) would still pass the positional test if the
      // closing </label> sits *before* the active branch. Slice the
      // disabled <label> body explicitly and assert the editor token
      // is NOT inside it. The label opens at
      // `data-testid="step4-inject-context-disabled"` and closes at
      // the first `</label>` after that marker.
      const labelOpen = body.indexOf(
        "data-testid=\"step4-inject-context-disabled\"",
      )
      expect(labelOpen).toBeGreaterThan(-1)
      const labelClose = body.indexOf("</label>", labelOpen)
      expect(labelClose).toBeGreaterThan(labelOpen)
      const disabledLabelBody = body.slice(labelOpen, labelClose)
      expect(disabledLabelBody).not.toContain("step4b-inject-editor")
    })

    it("saveWizard refuses inject_context on excluded lifecycle", () => {
      // Defense in depth: a stale URL pasted with
      // `?action=inject_context&lifecycle=elicitation` must NOT reach
      // persistDraft. The redirect target lands the operator back on
      // Step 4 so the disabled-card tooltip is visible.
      const start = src.indexOf("if (action === \"inject_context\")")
      expect(start).toBeGreaterThan(-1)
      const body = src.slice(start, start + 2000)
      expect(body).toMatch(/!lifecycleAllowsInjectContext\(lifecycle\)/)
      expect(body).toMatch(/policies\/new\?mode=guided&step=4&err=invalid_input/)
    })

    it("GuidedWizard does NOT auto-skip Step 3 for excluded lifecycles", () => {
      // Standard inject_context flow skips Step 3 → 4. For the four
      // excluded lifecycles we keep Step 3 reachable so the operator
      // can re-author with an alternate archetype after seeing the
      // disabled Step 4 card.
      const start = src.indexOf("function GuidedWizard")
      const end = src.indexOf("\n}\n", start)
      const body = src.slice(start, end)
      expect(body).toMatch(
        /effectiveStep === 3 && state\.action === "inject_context"\s*\n?\s*&&\s*lifecycleAllowsInjectContext\(state\.lifecycle\)/,
      )
    })

    it("lifecycleCardCopy carries the channel caveat for each excluded lifecycle (KO + EN)", () => {
      // The 4 lifecycle helper-text rows must mention the alternate
      // output channel so the Step 1 picker hints at the constraint
      // before the operator reaches Step 4.
      const start = src.indexOf("function lifecycleCardCopy")
      const end = src.indexOf("// D56c: lifecycles grouped", start)
      expect(start).toBeGreaterThan(-1)
      expect(end).toBeGreaterThan(start)
      const body = src.slice(start, end)
      // EN caveats — exact phrase pinned from the brief.
      expect(body).toMatch(/MCP elicitation channel/)
      expect(body).toMatch(/hookSpecificOutput\.worktreePath/)
      // KO caveats
      expect(body).toMatch(/MCP elicitation 채널/)
      expect(body).toMatch(/hookSpecificOutput\.worktreePath/)
      // MessageDisplay already had a display-only caveat pre-D59; we
      // pin the "Inject extra context" mention so the picker copy
      // surfaces the archetype gate in the same line.
      expect(body).toMatch(/Display-only[\s\S]*?Inject extra context/)
    })
  })

  /* D57f-2 follow-up — input_rewrite saveWizard branch.
   *
   * The original D57f-2 commit's input_rewrite branch passed
   * `undefined` as the scope to `allowedActionsForCombination`, which
   * `matcherClassForToolScope` resolves to `"wildcard"`. The matrix
   * intentionally does NOT legalize input_rewrite on the wildcard
   * column, so EVERY guided-wizard submission of action=input_rewrite
   * was bounced to Step 4 with err=invalid_input and never reached
   * `persistDraft`. The fix reads the toolScope first and passes it
   * to the legality check so the matrix sees the real matcher class.
   */
  describe("D57f-2 follow-up — input_rewrite save path uses real toolScope", () => {
    it("matrix-action gate reads rawScope before the input_rewrite legality check", () => {
      // The input_rewrite branch must (a) read `formData.get("toolScope")`
      // BEFORE the `allowedActionsForCombination(...)` call, (b) pass the
      // parsed scope to that call. The previous `(lifecycle, undefined)`
      // shape is the bug we're closing — pin against its re-introduction.
      const start = src.indexOf("if (action === \"input_rewrite\")")
      expect(start).toBeGreaterThan(-1)
      // Reuse the same 1500-char slice the inject_context tests use.
      const body = src.slice(start, start + 1500)
      // rawScope read happens BEFORE the matrix check.
      const rawScopeIdx = body.indexOf("formData.get(\"toolScope\")")
      const matrixIdx = body.indexOf(
        "allowedActionsForCombination(lifecycle, matcherIr)",
      )
      expect(rawScopeIdx).toBeGreaterThan(-1)
      expect(matrixIdx).toBeGreaterThan(-1)
      expect(rawScopeIdx).toBeLessThan(matrixIdx)
      // Negative pin: the legality check must NOT pass `undefined`.
      expect(body).not.toMatch(
        /allowedActionsForCombination\(lifecycle,\s*undefined\)\.includes\("input_rewrite"\)/,
      )
    })

    it("ACTIONS_BY_COMBINATION legalizes input_rewrite on (before_tool_use, tool)", () => {
      // The matrix legality table must list input_rewrite on the tool
      // and mcp_tool columns under before_tool_use; the wildcard column
      // intentionally omits it. We pin both the positive and negative
      // rows so a future widening of input_rewrite onto wildcard is
      // loud (the cloud's IR validator would refuse, but the wizard
      // must not preview an option the cloud cannot honor).
      //
      // D70: each per-class entry is now wrapped in
      // `_filterByCombination` so the inject_context exclusion stays
      // in lockstep with ACTIONS_BY_LIFECYCLE. The pinned slice
      // narrows on the wrapper call before reading the inner array.
      const m = src.match(/const ACTIONS_BY_COMBINATION[\s\S]*?=\s*\{([\s\S]+?)\n\}/)
      expect(m).not.toBeNull()
      const body = m![1]
      // Sub-block under before_tool_use.
      const btuStart = body.indexOf("before_tool_use:")
      const btuEnd = body.indexOf("after_tool_use:", btuStart)
      const btu = body.slice(btuStart, btuEnd)
      expect(btu).toMatch(/tool:[^\]]*"input_rewrite"/)
      expect(btu).toMatch(/mcp_tool:[^\]]*"input_rewrite"/)
      // wildcard line must NOT carry input_rewrite. Read the inner
      // array passed to `_filterByCombination` (the wrapper does not
      // add archetypes; it only filters inject_context per excluded
      // set), so a wildcard list without input_rewrite stays the
      // canonical surface.
      const wildcardLine = btu.match(
        /wildcard:\s*_filterByCombination\("before_tool_use",\s*\[([^\]]*)\]\)/,
      )
      expect(wildcardLine).not.toBeNull()
      expect(wildcardLine![1]).not.toContain("input_rewrite")
    })
  })

  /* D62: Step 3 to Step 4 advance validates conditionKind specifics.
   *
   * Recurring live-verification problem: the operator picks a
   * conditionKind on Step 3 (e.g. llm_critic) but leaves the
   * criterion blank, hits Next, and the wizard happily lands them on
   * Step 4 (action picker). Step 5 then bounces them with a generic
   * "Invalid input" banner with NO inline pointer. They cannot tell
   * what was wrong.
   *
   * Fix: advanceWizard now refuses the Step 3 to Step 4 advance when
   * the chosen conditionKind's specifics are empty, redirecting back
   * to step=3 with a precise err code. Step 3 renders an inline
   * banner plus per-input red ring plus helper copy that names
   * exactly what's missing, replacing the generic "Invalid input"
   * page-level flash.
   */
  describe("D62: Step 3 advance refuses empty conditionKind specifics", () => {
    it("validateStep3Specifics gates each conditionKind on its required field", () => {
      // Pin the per-kind specifics gate. The function is private (no
      // export) but the wizard-wiring tests are source-inspection-
      // only, mirroring the pattern the rest of this file uses.
      const start = src.indexOf("function validateStep3Specifics")
      expect(start).toBeGreaterThan(-1)
      const end = src.indexOf("\n}\n", start)
      expect(end).toBeGreaterThan(start)
      const body = src.slice(start, end)
      // Each err code maps to exactly ONE empty-field check; pin the
      // (code, field) pair so a future refactor cannot silently swap
      // codes or omit a kind.
      expect(body).toMatch(/return "pick_condition"/)
      expect(body).toMatch(/case "fetch_domain":\s*\n[\s\S]*?"missing_domain"/)
      expect(body).toMatch(/case "domain_allowlist":[\s\S]*?"missing_allowlist"/)
      expect(body).toMatch(/case "regex":\s*\n[\s\S]*?"missing_pattern"/)
      expect(body).toMatch(/case "llm_critic":\s*\n[\s\S]*?"missing_criterion"/)
      expect(body).toMatch(/case "evidence_ref":\s*\n[\s\S]*?"missing_evidence"/)
      expect(body).toMatch(/case "shacl":\s*\n[\s\S]*?"missing_shacl"/)
      // "none" passes through (no specifics to check); pin the
      // explicit `case "none": return null` so a refactor that drops
      // the early-return is loud.
      expect(body).toMatch(/case "none":\s*\n\s*return null/)
    })

    it("advanceWizard wires validateStep3Specifics into the Step 3 → 4 advance", () => {
      // The gate must fire when stepIn === 3, BEFORE the
      // `params.set("step", String(nextStep))` line that lands the
      // operator on Step 4. Pin both the conditional and the
      // redirect target so a refactor cannot move the call site
      // past the redirect.
      const start = src.indexOf("async function advanceWizard")
      expect(start).toBeGreaterThan(-1)
      const body = src.slice(start, start + 8000)
      expect(body).toMatch(/if \(stepIn === 3\)/)
      expect(body).toMatch(/validateStep3Specifics\(/)
      expect(body).toMatch(/params\.set\("step",\s*"3"\)/)
      expect(body).toMatch(/params\.set\("err",\s*stepThreeErr\)/)
    })

    it("STEP3_ERR_CODES enumerates every D62 per-kind code in lib/flash.ts", () => {
      // D62 follow-up: the seven codes intentionally do NOT appear in
      // ERR_CODES (resolveFlash would render an English top-of-page
      // banner stacked above the localized inline banner, regressing
      // locale parity). Pin the canonical STEP3_ERR_CODES export
      // instead so the wizard, dict, and per-kind helpers stay in
      // lockstep without duplicating copy across page-level + inline.
      const flashSrc = readFileSync(
        path.join(__dirname, "..", "..", "..", "..", "lib", "flash.ts"),
        "utf-8",
      )
      const codes = [
        "pick_condition",
        "missing_criterion",
        "missing_pattern",
        "missing_shacl",
        "missing_domain",
        "missing_allowlist",
        "missing_evidence",
      ]
      // STEP3_ERR_CODES literal exists.
      expect(flashSrc).toMatch(/export const STEP3_ERR_CODES\s*=\s*\[/)
      for (const code of codes) {
        expect(flashSrc).toMatch(new RegExp(`"${code}"`))
      }
      // Negative pin: each code must NOT appear inside ERR_CODES so
      // resolveFlash returns null for it (the inline localized banner
      // is the single source of truth for the operator-facing copy).
      const errCodesMatch = flashSrc.match(
        /const ERR_CODES[\s\S]*?=\s*\{([\s\S]+?)\n\}/,
      )
      expect(errCodesMatch).not.toBeNull()
      const errCodesBody = errCodesMatch![1]
      for (const code of codes) {
        expect(errCodesBody).not.toMatch(new RegExp(`\\b${code}:`))
      }
    })

    it("Step 3 component surfaces inline err banner + per-input helper", () => {
      // The page-level flash banner already renders when err=... is
      // on the URL (via resolveFlash); D62 adds an INLINE banner +
      // per-input red-ring helper that names exactly which field is
      // empty so the operator can fix it in one trip. Pin both
      // surfaces.
      const start = src.indexOf("function Step3Condition")
      expect(start).toBeGreaterThan(-1)
      // Slice runs to the next top-level function so the entire
      // Step3Condition body is in scope (the function is large
      // because it carries six per-kind specifics blocks).
      const end = src.indexOf("\nfunction Step4Action", start)
      expect(end).toBeGreaterThan(start)
      const body = src.slice(start, end)
      // wizardErr prop wired through from GuidedWizard.
      expect(body).toMatch(/wizardErr\?:\s*string/)
      // Inline top-of-form banner with precise helper text.
      expect(body).toContain("step3-specifics-err-banner")
      // Per-kind inline helpers (red ring + text) for each empty
      // specifics case; one data-testid per kind so the live-verify
      // operator and tests can target them directly.
      expect(body).toContain("step3-fetch-domain-helper")
      expect(body).toContain("step3-allowlist-helper")
      expect(body).toContain("step3-regex-helper")
      expect(body).toContain("step3-llm-critic-helper")
      expect(body).toContain("step3-shacl-helper")
      expect(body).toContain("step3-evidence-ref-helper")
    })

    it("Step 3 renders localized helper copy per empty-specifics case (KO+EN)", () => {
      // Pin every helper copy key in lib/i18n/dict.ts so a refactor
      // that drops a code's localized copy fails loud. The keys
      // mirror the err codes 1:1.
      const dictSrc = readFileSync(
        path.join(__dirname, "..", "..", "..", "..", "lib", "i18n", "dict.ts"),
        "utf-8",
      )
      for (const key of [
        "newPolicy.wizard.step3.err.pickCondition",
        "newPolicy.wizard.step3.err.missingCriterion",
        "newPolicy.wizard.step3.err.missingPattern",
        "newPolicy.wizard.step3.err.missingShacl",
        "newPolicy.wizard.step3.err.missingDomain",
        "newPolicy.wizard.step3.err.missingAllowlist",
        "newPolicy.wizard.step3.err.missingEvidence",
      ]) {
        // Each key must appear at least twice (KO block + EN block).
        const occurrences = dictSrc.split(`"${key}"`).length - 1
        expect(occurrences).toBeGreaterThanOrEqual(2)
      }
    })

    /* ── Per-case redirect assertions ─────────────────────────────
     *
     * For each empty-specifics case the gate must (a) refuse the
     * advance and (b) redirect to step=3 with the right err code.
     * The function is source-inspection-only; we pin the (case,
     * code) pair on the function body so a refactor that swaps a
     * code, or that introduces a `nextStep` early-return shortcut
     * past the gate, is loud.
     */
    it("redirect target carries step=3 with the precise err code per kind", () => {
      // The advanceWizard gate writes `step=3` and `err=<code>`
      // together. Pin both writes plus the conditional that fires
      // them; a refactor that lands one of these out of sync is the
      // exact silent-pass-through bug D62 closes.
      const start = src.indexOf("async function advanceWizard")
      const body = src.slice(start, start + 8000)
      expect(body).toMatch(/if \(stepThreeErr\)/)
      expect(body).toMatch(/params\.set\("step",\s*"3"\)/)
      expect(body).toMatch(/params\.set\("err",\s*stepThreeErr\)/)
      // The validateStep3Specifics call must precede the standard
      // `params.set("step", String(nextStep))` so the empty-
      // specifics case never reaches the Step 4 redirect path.
      const gateIdx = body.indexOf("validateStep3Specifics")
      const nextStepIdx = body.indexOf("params.set(\"step\", String(nextStep))")
      expect(gateIdx).toBeGreaterThan(-1)
      expect(nextStepIdx).toBeGreaterThan(-1)
      expect(gateIdx).toBeLessThan(nextStepIdx)
    })

    it("each empty-specifics conditionKind maps to its own err code (no shared 'invalid_input')", () => {
      // Negative pin: the gate must NOT fall back to the generic
      // "invalid_input" code (the old Step 5 round-trip behavior).
      // We compute the exact set of err codes the gate emits by
      // grepping the function body for return-string literals and
      // assert each precise code appears AND `invalid_input` does
      // NOT appear inside the gate.
      const start = src.indexOf("function validateStep3Specifics")
      const end = src.indexOf("\n}\n", start)
      const body = src.slice(start, end)
      const expected = [
        "pick_condition",
        "missing_domain",
        "missing_allowlist",
        "missing_pattern",
        "missing_criterion",
        "missing_evidence",
        "missing_shacl",
      ]
      for (const code of expected) expect(body).toContain(`"${code}"`)
      // The gate must NOT short-circuit to "invalid_input"; that
      // was the original silent-pass-through-to-Step-5 bug.
      expect(body).not.toContain("\"invalid_input\"")
    })

    /* D62 follow-up: exhaustiveness over ConditionKind.
     *
     * Review found the previous gate ended in `default: return
     * "pick_condition"` over the ConditionKind switch. Adding a new
     * member to `ALL_CONDITION_KINDS` without a `case` row would
     * silently fall through to "pick_condition" and strand the
     * operator (they DID pick the new kind, the gate just does not
     * know its required-field name). We now (a) iterate every kind in
     * `ALL_CONDITION_KINDS` and assert the gate body has a `case
     * "<kind>":` row, and (b) assert the `default` branch carries the
     * type-exhaustive `never`-guard so tsc --noEmit fails on a
     * missing case.
     */
    it("validateStep3Specifics has a `case` row for every ConditionKind", () => {
      const start = src.indexOf("function validateStep3Specifics")
      expect(start).toBeGreaterThan(-1)
      const end = src.indexOf("\n}\n", start)
      const body = src.slice(start, end)
      // Read ALL_CONDITION_KINDS literal from the file. Hardcoding
      // the slugs here would let a future widening of the union skip
      // this assertion silently.
      const allMatch = src.match(
        /const ALL_CONDITION_KINDS:\s*readonly ConditionKind\[\]\s*=\s*\[([^\]]+)\]/,
      )
      expect(allMatch).not.toBeNull()
      const kinds = (allMatch![1].match(/"([^"]+)"/g) ?? [])
        .map((s) => s.replace(/"/g, ""))
      expect(kinds.length).toBeGreaterThan(0)
      for (const k of kinds) {
        const re = new RegExp(`case "${k}":`)
        expect(body).toMatch(re)
      }
    })

    it("validateStep3Specifics default branch is type-exhaustive (never-guard)", () => {
      const start = src.indexOf("function validateStep3Specifics")
      const end = src.indexOf("\n}\n", start)
      const body = src.slice(start, end)
      // The `never`-guard pattern: `const _exhaustive: never = kind`
      // (where `kind` is the narrowed switch discriminator). Pin the
      // shape so tsc --noEmit fails on a future widening of
      // ConditionKind without a new case.
      expect(body).toMatch(/const _exhaustive:\s*never\s*=/)
    })

    /* D62 follow-up: per-kind UI surface keyed by ConditionKind.
     *
     * Review caught that `ERR_TO_KIND`, `ERR_TO_TKEY`, and the
     * per-kind render blocks were parallel hand-maintained tables. A
     * new conditionKind without a `step3-<kind>-helper` block would
     * silently render nothing (the inline guard `step3ErrHelper && …`
     * is quiet). Pin one testid per non-"none" kind so adding a new
     * kind without a per-kind helper card fails the wire test.
     */
    it("Step3Condition renders a step3-*-helper testid for every non-'none' kind", () => {
      const allMatch = src.match(
        /const ALL_CONDITION_KINDS:\s*readonly ConditionKind\[\]\s*=\s*\[([^\]]+)\]/,
      )
      const kinds = (allMatch![1].match(/"([^"]+)"/g) ?? [])
        .map((s) => s.replace(/"/g, ""))
        .filter((k) => k !== "none")
      const start = src.indexOf("function Step3Condition")
      const end = src.indexOf("\nfunction Step4Action", start)
      const body = src.slice(start, end)
      // The testid slug is not always a kebab-cased ConditionKind: a
      // few inputs use a tighter slug (`step3-allowlist-helper`
      // instead of `step3-domain-allowlist-helper`). Pin one testid
      // per kind explicitly so a new kind without a per-kind helper
      // card has to add a row here AND in Step3Condition.
      const TESTID_FOR_KIND: Record<string, string> = {
        fetch_domain: "step3-fetch-domain-helper",
        domain_allowlist: "step3-allowlist-helper",
        regex: "step3-regex-helper",
        llm_critic: "step3-llm-critic-helper",
        evidence_ref: "step3-evidence-ref-helper",
        shacl: "step3-shacl-helper",
      }
      for (const k of kinds) {
        const id = TESTID_FOR_KIND[k]
        expect(id, `missing testid mapping for kind '${k}'`).toBeDefined()
        expect(body).toContain(id)
      }
    })

    /* D62 follow-up: i18n drift gate.
     *
     * The previous helper-copy test hardcoded the seven dict keys and
     * only checked they appeared at least twice (KO + EN). A future
     * code that lacks a dict entry would render `t(undefined as
     * TKey)` and the inline banner would vanish. Drive the test from
     * the err codes the gate actually emits so adding a code without
     * dict copy fails loud.
     */
    it("every Step3 err code has a localized dict entry (KO + EN)", () => {
      const dictSrc = readFileSync(
        path.join(__dirname, "..", "..", "..", "..", "lib", "i18n", "dict.ts"),
        "utf-8",
      )
      // Grep ALL string-literal returns out of the gate body (the
      // bare `return "pick_condition"` early-return as well as the
      // ternary `X ? null : "missing_*"` returns). A future code that
      // forgets a localized dict entry would otherwise render an
      // empty inline helper and a silent banner regression.
      const start = src.indexOf("function validateStep3Specifics")
      const end = src.indexOf("\n}\n", start)
      const body = src.slice(start, end)
      const codes = Array.from(
        new Set(
          (body.match(/"((?:pick|missing)_[a-z_]+)"/g) ?? [])
            .map((s) => s.replace(/"/g, "")),
        ),
      )
      // Sanity: gate emits at least the seven D62 codes.
      expect(codes).toContain("pick_condition")
      expect(codes).toContain("missing_pattern")
      expect(codes).toContain("missing_criterion")
      expect(codes).toContain("missing_evidence")
      for (const code of codes) {
        // camelCase the snake_case code: pick_condition -> pickCondition.
        const camel = code.replace(/_([a-z])/g, (_, c) => c.toUpperCase())
        const key = `newPolicy.wizard.step3.err.${camel}`
        const occurrences = dictSrc.split(`"${key}"`).length - 1
        // KO + EN block: each key MUST appear in both.
        expect(
          occurrences,
          `dict missing key for code '${code}' (expected '${key}')`,
        ).toBeGreaterThanOrEqual(2)
      }
    })

    /* D62 follow-up: behavioural sanity on the gate's param-key
     * contract.
     *
     * Source-pin tests only assert the (case, code) pair; a refactor
     * that read `params.get("fetch_domain")` (snake) instead of
     * `params.get("fetchDomain")` (camelCase, matching the wizard
     * inputs) would still pass every regex assertion but always
     * return `missing_domain` even when the operator filled the
     * field. Pin the param-key the gate reads against the input
     * `name` attribute the form renders, so the two cannot drift.
     */
    it("validateStep3Specifics reads the same param key the Step 3 input emits", () => {
      const start = src.indexOf("function validateStep3Specifics")
      const end = src.indexOf("\n}\n", start)
      const gateBody = src.slice(start, end)
      const s3Start = src.indexOf("function Step3Condition")
      const s3End = src.indexOf("\nfunction Step4Action", s3Start)
      const s3Body = src.slice(s3Start, s3End)
      // (gate param key, input name attribute) pairs.
      const pairs: Array<[string, string]> = [
        ["fetchDomain", "fetchDomain"],
        ["allowlist", "allowlist"],
        ["pattern", "pattern"],
        ["llmCriterion", "llmCriterion"],
        ["shaclTtl", "shaclTtl"],
      ]
      for (const [gateKey, inputName] of pairs) {
        expect(gateBody).toMatch(
          new RegExp(`params\\.get\\(\\s*"${gateKey}"\\s*\\)`),
        )
        expect(s3Body).toMatch(new RegExp(`name="${inputName}"`))
      }
      // evidence_ref reads the merged list, not a URL key, so pin
      // that instead.
      expect(gateBody).toMatch(/evMerged\.length/)
      expect(s3Body).toMatch(/name="evidence_ref"/)
    })

    /* D62 follow-up: save-seam defense-in-depth.
     *
     * Review caught that `validateSpecifics` (saveWizard's gate at
     * Step 5 / Step 6) still returned the generic "invalid_input"
     * code for every empty-specifics case. An operator who reached
     * saveWizard with empty specifics (deep link, browser back-
     * forward, a future flow that bypasses Step 3) would hit the
     * exact silent-pass-through bug D62 was built to close. The fix
     * delegates to `validateStep3Specifics` so the save seam returns
     * the same per-kind codes as the advance gate.
     */
    it("validateSpecifics delegates to validateStep3Specifics for precise codes", () => {
      const start = src.indexOf("function validateSpecifics")
      expect(start).toBeGreaterThan(-1)
      const end = src.indexOf("\n}\n", start)
      const body = src.slice(start, end)
      // Delegates to the shared gate so the save seam returns the
      // same precise code (no more generic "invalid_input" at Step 5).
      expect(body).toMatch(/validateStep3Specifics\(/)
      // Negative pin: the save-seam gate must NOT emit
      // "invalid_input" anymore.
      expect(body).not.toContain("\"invalid_input\"")
    })
  })

  /* D68: Step 4 → Step 5 advance refuses empty action-specifics.
   *
   * Mirror of D62 (Step 3 → Step 4). Recurring live-verification
   * problem: operator picks action=inject_context at Step 4, clicks
   * Next, advanceWizard silently rejects because injectTemplate is
   * empty, redirects to step=4 with NO err param, operator stares
   * at the picker unable to tell what is wrong. Same pattern for
   * action=run_command (empty command AND no script_id) and
   * action=input_rewrite (empty rewriter config).
   *
   * Fix: advanceWizard now refuses the Step 4 → Step 5 advance
   * when the chosen action's sub-form fields are empty, redirects
   * to step=4 with a precise err code (missing_template /
   * missing_command_or_script / missing_rewriter_config), and
   * Step4Action renders an inline banner near the Step 4b sub-form
   * (NOT at the top of the page) plus a per-input red ring with
   * helper copy.
   */
  describe("D68: Step 4 advance refuses empty action-specifics", () => {
    it("validateStep4ActionSpecifics gates each action on its required field", () => {
      // Pin the per-action specifics gate. Source-inspection only,
      // matching the D62 validateStep3Specifics pattern.
      const start = src.indexOf("function validateStep4ActionSpecifics")
      expect(start).toBeGreaterThan(-1)
      const end = src.indexOf("\n}\n", start)
      expect(end).toBeGreaterThan(start)
      const body = src.slice(start, end)
      // Each action maps to its per-action err code(s). input_rewrite
      // is now split into three per-kind codes so the inline copy can
      // name only the relevant UI field (D68 follow-up P2 ux-clarity).
      expect(body).toMatch(/case "inject_context":[\s\S]*?"missing_template"/)
      expect(body).toMatch(/case "run_command":[\s\S]*?"missing_command_or_script"/)
      expect(body).toMatch(/case "input_rewrite":[\s\S]*?"missing_rewriter_prefix"/)
      expect(body).toMatch(/case "input_rewrite":[\s\S]*?"missing_rewriter_scheme"/)
      expect(body).toMatch(/case "input_rewrite":[\s\S]*?"missing_rewriter_pattern"/)
      // The run_command branch must read BOTH runCommandBody AND
      // runCommandScriptId so the attach-mode happy path (script
      // uploaded, body empty) advances without a false positive.
      expect(body).toMatch(/runCommandBody/)
      expect(body).toMatch(/runCommandScriptId/)
      // inject_context branch must read injectTemplate.
      expect(body).toMatch(/injectTemplate/)
      // input_rewrite must dispatch on rewriterKind so each kind's
      // required config field is checked.
      expect(body).toMatch(/rewriterKind/)
      expect(body).toMatch(/rewriterPrefix/)
      expect(body).toMatch(/rewriterFrom/)
      expect(body).toMatch(/rewriterTo/)
      expect(body).toMatch(/rewriterPattern/)
      // D68 follow-up: the four sub-form-less actions
      // (block / ask / audit / strip) now have EXPLICIT cases that
      // return null so the intent is documented in code rather than
      // implicit in a permissive default. The default branch is a
      // `_exhaustive: never` so a future archetype added to the
      // Action union without a case here becomes a build-time error.
      expect(body).toMatch(/case "block":/)
      expect(body).toMatch(/case "ask":/)
      expect(body).toMatch(/case "audit":/)
      expect(body).toMatch(/case "strip":/)
      expect(body).toMatch(/_exhaustive:\s*never\s*=\s*action/)
    })

    /* D68 follow-up (P2 completeness/testing): negative-completeness
     * pin. The lens requires "A future archetype without an early-
     * validation rule should fail a test." The positive source-grep
     * tests above match the three known sub-form-owning archetypes,
     * but adding `case "replace_output":` to the Action union and a
     * new Step 4b sub-form would leave them green while reintroducing
     * the silent-pass bug for that archetype.
     *
     * This pin reads ALL_ACTIONS + ACTIONS_WITH_SUBFORM from the same
     * page.tsx source-of-truth and asserts every sub-form-owning
     * archetype has a case in validateStep4ActionSpecifics. If a
     * future archetype is added to ACTIONS_WITH_SUBFORM without a
     * case in the gate body, this test fails. */
    it("ACTIONS_WITH_SUBFORM exhaustiveness: every sub-form-owning archetype has a gate case", () => {
      // Pull the canonical lists out of page.tsx so the test tracks
      // them rather than a hard-coded local copy.
      const allActionsMatch = src.match(
        /const ALL_ACTIONS:\s*readonly Action\[\]\s*=\s*\[([\s\S]*?)\]/,
      )
      expect(allActionsMatch).not.toBeNull()
      const allActions = (allActionsMatch![1].match(/"([a-z_]+)"/g) ?? [])
        .map((s) => s.replace(/"/g, ""))
      expect(allActions).toContain("inject_context")
      expect(allActions).toContain("input_rewrite")
      expect(allActions).toContain("run_command")
      expect(allActions).toContain("block")
      expect(allActions).toContain("ask")
      expect(allActions).toContain("audit")
      expect(allActions).toContain("strip")

      const subformMatch = src.match(
        /const ACTIONS_WITH_SUBFORM:\s*readonly Action\[\]\s*=\s*\[([\s\S]*?)\]/,
      )
      expect(subformMatch).not.toBeNull()
      const subformActions = (subformMatch![1].match(/"([a-z_]+)"/g) ?? [])
        .map((s) => s.replace(/"/g, ""))
      // Today's known sub-form-owning archetypes. Adding a new one
      // here without also adding a `case` in the gate fails the next
      // assertion.
      expect(subformActions.length).toBeGreaterThan(0)

      const gateStart = src.indexOf("function validateStep4ActionSpecifics")
      const gateEnd = src.indexOf("\n}\n", gateStart)
      const gateBody = src.slice(gateStart, gateEnd)

      // Every archetype in ACTIONS_WITH_SUBFORM must have a `case "<a>":`
      // discriminator in the gate body. A future sub-form-owning
      // archetype added without a case here fails this assertion
      // (negative-completeness invariant).
      for (const a of subformActions) {
        expect(
          gateBody,
          `validateStep4ActionSpecifics is missing a case for "${a}"`,
        ).toMatch(new RegExp(`case "${a}":`))
      }

      // Pin that the gate signature narrows actionRaw to Action via a
      // membership check up front (mirror of D62's
      // validateStep3Specifics, which guards with ALL_CONDITION_KINDS
      // before narrowing kindRaw). Without this guard, a future
      // archetype added without a `case` would silently fall through
      // to the permissive `default` again.
      expect(gateBody).toMatch(/ALL_ACTIONS as readonly string\[\]\)\.includes/)
      expect(gateBody).toMatch(/const action = actionRaw as Action/)

      // The default branch must be the exhaustiveness `never` cast,
      // not `return null`. A permissive default re-introduces the
      // silent-pass bug for unknown union members.
      expect(gateBody).toMatch(/default:[\s\S]*?_exhaustive:\s*never\s*=\s*action/)
    })

    /* D68 follow-up (P2 completeness): inner rewriter-kind switch is
     * also exhaustive. A future RewriterKind added without a case in
     * the gate fails this assertion at runtime AND at build time
     * (via the `_exhaustive: never = kind` cast). */
    it("input_rewrite branch exhausts the RewriterKind union", () => {
      const kindsMatch = src.match(
        /const ALL_REWRITER_KINDS:\s*readonly RewriterKind\[\]\s*=\s*\[([\s\S]*?)\]/,
      )
      expect(kindsMatch).not.toBeNull()
      const kinds = (kindsMatch![1].match(/"([a-z_]+)"/g) ?? [])
        .map((s) => s.replace(/"/g, ""))
      expect(kinds).toEqual([
        "prefix_strip",
        "scheme_force",
        "regex_substitute",
      ])
      const gateStart = src.indexOf("function validateStep4ActionSpecifics")
      const gateEnd = src.indexOf("\n}\n", gateStart)
      const gateBody = src.slice(gateStart, gateEnd)
      for (const k of kinds) {
        expect(gateBody).toMatch(new RegExp(`case "${k}":`))
      }
      // The inner default must also be the `_exhaustive: never` cast.
      // A permissive inner default would re-introduce the
      // missing_rewriter_config catch-all that hid which field was
      // actually empty.
      expect(gateBody).toMatch(/_exhaustive:\s*never\s*=\s*kind/)
    })

    it("advanceWizard wires validateStep4ActionSpecifics into the Step 4 → 5 advance", () => {
      // The gate must fire when stepIn === 4, BEFORE the
      // `params.set("step", String(nextStep))` line. Pin both the
      // conditional and the redirect target so a refactor cannot
      // move the call site past the redirect.
      const start = src.indexOf("async function advanceWizard")
      expect(start).toBeGreaterThan(-1)
      const body = src.slice(start, start + 10_000)
      expect(body).toMatch(/if \(stepIn === 4\)/)
      expect(body).toMatch(/validateStep4ActionSpecifics\(/)
      // Redirect target carries step=4 and err=<code>.
      expect(body).toMatch(/params\.set\("step",\s*"4"\)/)
      expect(body).toMatch(/params\.set\("err",\s*stepFourErr\)/)
      // Ordering: the gate call must precede the standard
      // `params.set("step", String(nextStep))` so the empty-
      // specifics case never reaches the Step 5 redirect path.
      const gateIdx = body.indexOf("validateStep4ActionSpecifics")
      const nextStepIdx = body.indexOf("params.set(\"step\", String(nextStep))")
      expect(gateIdx).toBeGreaterThan(-1)
      expect(nextStepIdx).toBeGreaterThan(-1)
      expect(gateIdx).toBeLessThan(nextStepIdx)
    })

    it("STEP4_ERR_CODES enumerates every D68 per-action code in lib/flash.ts", () => {
      // Mirror of the D62 STEP3_ERR_CODES pin. The codes are
      // deliberately omitted from ERR_CODES so resolveFlash returns
      // null for them (the inline localized banner is the single
      // source of truth; a duplicate English page-level banner
      // above the localized inline copy would regress locale
      // parity exactly as the D62 review documented).
      const flashSrc = readFileSync(
        path.join(__dirname, "..", "..", "..", "..", "lib", "flash.ts"),
        "utf-8",
      )
      // D68 follow-up (P2 ux-clarity): missing_rewriter_config was
      // split into per-kind codes so each banner names only the
      // relevant UI field (mirror of D62 Step 3's per-condition
      // codes).
      const codes = [
        "missing_template",
        "missing_command_or_script",
        "missing_rewriter_prefix",
        "missing_rewriter_scheme",
        "missing_rewriter_pattern",
      ]
      expect(flashSrc).toMatch(/export const STEP4_ERR_CODES\s*=\s*\[/)
      for (const code of codes) {
        expect(flashSrc).toMatch(new RegExp(`"${code}"`))
      }
      // Negative pin: each code must NOT appear inside ERR_CODES.
      const errCodesMatch = flashSrc.match(
        /const ERR_CODES[\s\S]*?=\s*\{([\s\S]+?)\n\}/,
      )
      expect(errCodesMatch).not.toBeNull()
      const errCodesBody = errCodesMatch![1]
      for (const code of codes) {
        expect(errCodesBody).not.toMatch(new RegExp(`\\b${code}:`))
      }
      // Negative pin: the old catch-all rewriter code is gone.
      expect(flashSrc).not.toMatch(/"missing_rewriter_config"/)
    })

    it("Step 4 component surfaces inline error affordance per archetype", () => {
      // Each archetype that owns a Step 4b sub-form renders an
      // explanation inside the peer-checked editor div (NOT at the
      // top of the page). D68 follow-up (P1 ux-clarity) rebalanced
      // the patterns so each archetype has EXACTLY one error
      // surface:
      //   inject_context  -> per-input helper under the textarea
      //                      (banner removed; the duplicate copy
      //                      created visual noise and ambiguity).
      //   input_rewrite   -> banner near sub-form + per-input helper
      //                      under the empty rewriter input(s).
      //   run_command     -> banner near sub-form + per-input red
      //                      ring on the empty body / script_id
      //                      (the affordance was missing before).
      const start = src.indexOf("function Step4Action")
      expect(start).toBeGreaterThan(-1)
      // Slice to the next top-level function so the entire
      // Step4Action body is in scope.
      const end = src.indexOf("\nfunction Step5Naming", start)
      expect(end).toBeGreaterThan(start)
      const body = src.slice(start, end)
      // wizardErr prop wired through from GuidedWizard.
      expect(body).toMatch(/wizardErr\?:\s*string/)
      // Banners survive for input_rewrite + run_command. The
      // inject_context banner was dropped in favor of the single
      // helper paragraph under the empty textarea (which now carries
      // role="alert"). Negative pin guards regression.
      expect(body).toContain("step4b-rewriter-err-banner")
      expect(body).toContain("step4b-run-command-err-banner")
      expect(body).not.toContain("step4b-inject-err-banner")
      // Per-input red-ring helpers, one per empty field. scheme_force
      // now renders TWO helpers (one per side) so the operator who
      // emptied only one side sees the explanation co-located with
      // that side's red ring.
      expect(body).toContain("step4-inject-template-helper")
      expect(body).toContain("step4-rewriter-prefix-helper")
      expect(body).toContain("step4-rewriter-scheme-helper")
      expect(body).toContain("step4-rewriter-scheme-from-helper")
      expect(body).toContain("step4-rewriter-pattern-helper")
      expect(body).toContain("step4-run-command-helper")
      // P2 follow-up: the Step4bRunCommandFields island must receive
      // hasError + errorRingClassName so the empty body / script_id
      // input lights up red (matching the inject / rewrite ring).
      // Without this prop wiring a future refactor would silently
      // drop the affordance.
      expect(body).toMatch(/hasError=\{step4ErrCode === "missing_command_or_script"\}/)
      expect(body).toMatch(/errorRingClassName=\{errRingCls\}/)
    })

    it("Step 4 renders localized helper copy per empty-specifics case (KO+EN)", () => {
      const dictSrc = readFileSync(
        path.join(__dirname, "..", "..", "..", "..", "lib", "i18n", "dict.ts"),
        "utf-8",
      )
      // D68 follow-up: the three rewriter sub-keys replace the old
      // catch-all missingRewriterConfig so each banner names only
      // the relevant UI field.
      for (const key of [
        "newPolicy.wizard.step4.err.missingTemplate",
        "newPolicy.wizard.step4.err.missingCommandOrScript",
        "newPolicy.wizard.step4.err.missingRewriterPrefix",
        "newPolicy.wizard.step4.err.missingRewriterScheme",
        "newPolicy.wizard.step4.err.missingRewriterPattern",
      ]) {
        // Each key must appear at least twice (KO block + EN block).
        const occurrences = dictSrc.split(`"${key}"`).length - 1
        expect(occurrences).toBeGreaterThanOrEqual(2)
      }
      // Negative pin: the old catch-all key is gone from both blocks.
      expect(dictSrc).not.toMatch(/missingRewriterConfig/)
      // P2 follow-up (ux-clarity): the user-visible copy must NOT
      // leak IR kind names. The previous catch-all banner read
      // "Fill in the rewriter config: prefix (prefix_strip) /
      // from + to (scheme_force) / pattern (regex_substitute)."
      // which exposed the discriminators to operators. None of the
      // per-kind copy in either locale block names them.
      const ko = dictSrc.match(/missingRewriterPrefix":\s*"([^"]+)"/)
      const en = dictSrc.match(/missingRewriterPattern":\s*"([^"]+)"/)
      expect(ko).not.toBeNull()
      expect(en).not.toBeNull()
      expect(ko![1]).not.toMatch(/prefix_strip|scheme_force|regex_substitute/)
      expect(en![1]).not.toMatch(/prefix_strip|scheme_force|regex_substitute/)
    })

    it("Step 4 component's GuidedWizard call forwards searchParams.err as wizardErr", () => {
      // Pin the prop flow so a refactor that drops the wizardErr
      // prop on Step4Action's invocation leaves the inline banner
      // permanently dark. Mirror of the Step3Condition wiring at
      // the same call site.
      const callSite = src.match(
        /Step4Action[\s\S]{0,400}?wizardErr=\{searchParams\.err\}/,
      )
      expect(callSite).not.toBeNull()
    })

    it("each empty-specifics action maps to its own err code (no shared 'invalid_input')", () => {
      // Negative pin: the gate must NOT fall back to the generic
      // "invalid_input" code (the old silent-pass-through-to-Step-5
      // behavior).
      const start = src.indexOf("function validateStep4ActionSpecifics")
      const end = src.indexOf("\n}\n", start)
      const body = src.slice(start, end)
      const expected = [
        "missing_template",
        "missing_command_or_script",
        "missing_rewriter_prefix",
        "missing_rewriter_scheme",
        "missing_rewriter_pattern",
      ]
      for (const code of expected) expect(body).toContain(`"${code}"`)
      // The gate must NOT short-circuit to "invalid_input" or the
      // old catch-all rewriter code.
      expect(body).not.toContain("\"invalid_input\"")
      expect(body).not.toContain("\"missing_rewriter_config\"")
    })

    it("block / ask / audit / strip pass through with explicit cases", () => {
      // D68 follow-up: the sub-form-less actions now have EXPLICIT
      // cases returning null so the intent is documented in code
      // rather than implicit in a permissive default. Pinning the
      // explicit cases catches a refactor that re-introduces a
      // permissive default by accident.
      const start = src.indexOf("function validateStep4ActionSpecifics")
      const end = src.indexOf("\n}\n", start)
      const body = src.slice(start, end)
      // Explicit fall-through cases for block / ask / audit / strip.
      expect(body).toMatch(/case "block":[\s\S]*?case "ask":[\s\S]*?case "audit":[\s\S]*?case "strip":[\s\S]*?return null/)
      // The default branch is now the exhaustiveness `never` cast,
      // NOT a permissive `return null` (which would let an unknown
      // union member fall through silently).
      expect(body).toMatch(/default:\s*\{[\s\S]*?_exhaustive:\s*never\s*=\s*action/)
    })

    it("run_command attach-mode (script_id only, body empty) advances OK", () => {
      // Behavioural pin: the run_command branch must use a logical
      // OR over runCommandBody and runCommandScriptId, so attach-
      // mode operators (who fill ONLY the script_id via the upload
      // widget) are not falsely refused. A naive AND or a body-
      // only check would re-introduce the silent-reject loop for
      // attach-mode.
      const start = src.indexOf("function validateStep4ActionSpecifics")
      const end = src.indexOf("\n}\n", start)
      const body = src.slice(start, end)
      // The run_command case body must return null when EITHER
      // field is present. The shape we ship is
      //   return body || scriptId ? null : "missing_command_or_script"
      expect(body).toMatch(/body \|\| scriptId\s*\?\s*null\s*:/)
    })
  })
})

/**
 * D82a: wizard chrome — top-left Home + Back replace the legacy
 * top-left "Pick different" text link AND the bottom-left "Back" link
 * inside StepShell.
 *
 *   - Home    -> /policies/new (same target the legacy pickDifferent
 *                link pointed at) using the home icon.
 *   - Back    -> previousLiveStep(state, current) — honors the same
 *                skip rules GuidedWizard uses to advance forward, so
 *                Back from Step 4 with action=inject_context lands on
 *                Step 2 (or Step 1 if the lifecycle skipped Step 2)
 *                rather than bouncing through the now-skipped Step 3.
 *   - StepShell no longer renders the bottom-left back affordance.
 */
describe("policies/new wizard — D82a top-left Home + Back", () => {
  const src = readFileSync(
    path.join(__dirname, "page.tsx"),
    "utf-8",
  )

  it("imports HomeIcon for the wizard nav", () => {
    expect(src).toMatch(/HomeIcon[\s,]/)
    expect(src).toMatch(/from "@heroicons\/react\/24\/outline"/)
  })

  it("WizardHeader renders Home then Back as the top-left affordances", () => {
    const start = src.indexOf("function WizardHeader(")
    expect(start).toBeGreaterThan(-1)
    const end = src.indexOf("\n}\n", start)
    const body = src.slice(start, end)
    // Home: targets /policies/new (the picker landing), uses HomeIcon,
    // carries the new aria/title keys.
    expect(body).toContain('href="/policies/new"')
    expect(body).toContain('"newPolicy.wizard.nav.home.aria"')
    expect(body).toContain('"newPolicy.wizard.nav.home.tip"')
    expect(body).toContain("<HomeIcon")
    expect(body).toContain('data-testid="wizard-nav-home"')
    // Back: targets the previous live step, carries the new aria/title
    // keys, uses ArrowLeftIcon, exposes a data-testid for e2e harness.
    expect(body).toContain('"newPolicy.wizard.nav.back.aria"')
    expect(body).toContain('"newPolicy.wizard.nav.back.tip"')
    expect(body).toContain("<ArrowLeftIcon")
    expect(body).toContain('data-testid="wizard-nav-back"')
    // Home comes before Back in the rendered order so Tab order is
    // Home -> Back -> wizard body -> Next.
    const homeIdx = body.indexOf("wizard-nav-home")
    const backIdx = body.indexOf("wizard-nav-back")
    expect(homeIdx).toBeGreaterThan(-1)
    expect(backIdx).toBeGreaterThan(-1)
    expect(homeIdx).toBeLessThan(backIdx)
  })

  it("WizardHeader threads state so Back can compute the live previous step", () => {
    // D82a follow-up: the WizardHeader JSX call site must thread `state`
    // (so previousLiveStep can run). The previous revision pinned an
    // exact single-line regex with strict prop order, which is brittle
    // under Prettier rewrap or any future prop addition. Slice the
    // `<WizardHeader ... />` block by index scan and assert each prop
    // appears anywhere within — that survives wrap + reorder.
    const open = src.indexOf("<WizardHeader")
    expect(open).toBeGreaterThan(-1)
    const close = src.indexOf("/>", open)
    expect(close).toBeGreaterThan(open)
    const slice = src.slice(open, close + 2)
    expect(slice).toContain("t={t}")
    expect(slice).toContain("step={effectiveStep}")
    expect(slice).toContain("total={WIZARD_TOTAL}")
    expect(slice).toContain("locale={locale}")
    expect(slice).toContain("state={state}")
  })

  it("StepShell no longer renders the bottom-left Back link", () => {
    const start = src.indexOf("function StepShell(")
    expect(start).toBeGreaterThan(-1)
    // Brace-balance scan from the function signature so we slice
    // exactly the StepShell function body.
    const bodyAnchor = src.indexOf("}) {", start)
    expect(bodyAnchor).toBeGreaterThan(start)
    let i = bodyAnchor + 3
    let depth = 0
    let end = -1
    for (; i < src.length; i++) {
      const ch = src[i]
      if (ch === "{") depth++
      else if (ch === "}") {
        depth--
        if (depth === 0) { end = i + 1; break }
      }
    }
    expect(end).toBeGreaterThan(start)
    const body = src.slice(start, end)
    // The old form was `{prevHref && (<div><Link href={prevHref}>...
    // <ArrowLeftIcon/>{t("newPolicy.wizard.back")}</Link></div>)}`.
    // None of those tokens may appear inside StepShell now.
    expect(body).not.toContain("prevHref &&")
    expect(body).not.toContain('"newPolicy.wizard.back"')
    expect(body).not.toContain("ArrowLeftIcon")
  })

  it("StepShell no longer accepts the dead prevHref / t props on its signature", () => {
    // D82a follow-up: the prior revision kept `prevHref?` and `t?` as
    // type-shape backwards-compat props that the body ignored. There
    // are no external call sites — every consumer is in the same file.
    // The retention misled readers into thinking the bottom-left Back
    // was still wired and made every call site emit a dead
    // `buildWizardHref(state, n)` URL. Pin the cleanup at the source
    // level so a future regression cannot silently re-add the noise.
    const start = src.indexOf("function StepShell(")
    expect(start).toBeGreaterThan(-1)
    // Slice up to the first opening brace of the function body so we
    // only inspect the props typedef, not the body.
    const sig = src.slice(start, src.indexOf("}) {", start) + 4)
    expect(sig).not.toMatch(/prevHref\?:/)
    expect(sig).not.toMatch(/t\?:/)
  })

  it("no StepShell call site passes prevHref= or t= any more", () => {
    // Pin the cleanup at every consumer — the JSX surface must be
    // free of dead `prevHref={...}` and `t={t}` props after D82a's
    // bottom-left Back removal. (The wizard already threads `t` via
    // closure inside each Step component; StepShell never used it.)
    const callRegex = /<StepShell\b[\s\S]*?>/g
    const calls = src.match(callRegex) ?? []
    expect(calls.length).toBeGreaterThan(0)
    for (const call of calls) {
      expect(call).not.toMatch(/\bprevHref=/)
      expect(call).not.toMatch(/\bt=\{t\}/)
    }
  })

  it("the legacy top-left 'Pick different' text link is gone from the wizard", () => {
    // The link lived inside WizardHeader before D82a. After D82a the
    // Home icon (with the same /policies/new target) replaces it; the
    // bare "newPolicy.pickDifferent" text key must NOT appear inside
    // WizardHeader.
    const start = src.indexOf("function WizardHeader(")
    expect(start).toBeGreaterThan(-1)
    const end = src.indexOf("\n}\n", start)
    const body = src.slice(start, end)
    expect(body).not.toContain('"newPolicy.pickDifferent"')
  })
})

/**
 * D82a follow-up: table-driven behaviour pin for previousLiveStep.
 *
 * The prior revision substring-grepped page.tsx for the function body
 * (`inject_context`, `input_rewrite`, `run_command`, `lifecycleHasToolScope`,
 * `current === 3`, `current <= 1`). None of those grepped the return
 * value, so a future refactor that mangled the math (e.g. returned 1
 * from Step 4 when only Step 3 should be skipped, or returned 3 from
 * Step 3 when the lifecycle skipped Step 2) would keep every substring
 * and pass silently. Behavioural assertions over (state, current) ->
 * expected tuples catch a real regression.
 *
 * The Step 4 + action=inject_context + excluded-lifecycle case is the
 * one the install review surfaced: forward auto-skip is gated on
 * `lifecycleAllowsInjectContext(state.lifecycle)`, but the prior
 * previousLiveStep treated inject_context as unconditionally skipping
 * Step 3 backward. For the eight excluded lifecycles Step 3 is a LIVE
 * step and Back must return 3, not 2/1.
 */
describe("policies/new wizard — previousLiveStep behavioural cases", () => {
  // tool-context lifecycles (Step 2 is live).
  const TOOL_LIFECYCLES = ["before_tool_use", "after_tool_use"] as const
  // non-tool-context lifecycles where inject_context IS allowed (so the
  // forward Step 3 -> Step 4 skip fires).
  const NON_TOOL_INJECT_OK_LIFECYCLE = "user_prompt" as const
  // inject_context excluded lifecycles (CONTEXT_INJECTION_EXCLUDED_LIFECYCLES)
  const INJECT_EXCLUDED_LIFECYCLES = [
    "elicitation", "elicitation_result",
    "worktree_create", "message_display",
    "pre_final", "stop_failure", "session_end", "subagent_stop",
  ] as const

  it("returns null on Step 1 (no live previous step)", () => {
    expect(previousLiveStep({ lifecycle: "before_tool_use", action: "block" }, 1)).toBeNull()
    expect(previousLiveStep({}, 1)).toBeNull()
    expect(previousLiveStep({}, 0)).toBeNull()
  })

  it("Step 2 -> Step 1 unconditionally", () => {
    expect(previousLiveStep({ lifecycle: "before_tool_use" }, 2)).toBe(1)
    expect(previousLiveStep({ lifecycle: "user_prompt" }, 2)).toBe(1)
  })

  it("Step 3 -> Step 2 when lifecycle has tool scope", () => {
    for (const lc of TOOL_LIFECYCLES) {
      expect(previousLiveStep({ lifecycle: lc }, 3)).toBe(2)
    }
  })

  it("Step 3 -> Step 1 when lifecycle skips Step 2 (no tool scope)", () => {
    expect(previousLiveStep({ lifecycle: NON_TOOL_INJECT_OK_LIFECYCLE }, 3)).toBe(1)
    expect(previousLiveStep({ lifecycle: "session_start" }, 3)).toBe(1)
  })

  it("Step 4 + action=block -> Step 3 (no condition-side skip)", () => {
    expect(previousLiveStep({ lifecycle: "before_tool_use", action: "block" }, 4)).toBe(3)
    expect(previousLiveStep({ lifecycle: "after_tool_use", action: "audit" }, 4)).toBe(3)
  })

  it("Step 4 + action=inject_context + tool-context lifecycle -> Step 2", () => {
    for (const lc of TOOL_LIFECYCLES) {
      expect(previousLiveStep({ lifecycle: lc, action: "inject_context" }, 4)).toBe(2)
    }
  })

  it("Step 4 + action=inject_context + non-tool-context lifecycle (inject allowed) -> Step 1", () => {
    expect(
      previousLiveStep(
        { lifecycle: NON_TOOL_INJECT_OK_LIFECYCLE, action: "inject_context" },
        4,
      ),
    ).toBe(1)
  })

  // The bug the install review surfaced. Pin every excluded lifecycle.
  for (const lc of INJECT_EXCLUDED_LIFECYCLES) {
    it(`Step 4 + action=inject_context + excluded lifecycle ${lc} -> Step 3 (NOT 1/2)`, () => {
      expect(previousLiveStep({ lifecycle: lc, action: "inject_context" }, 4)).toBe(3)
    })
  }

  it("Step 4 + action=input_rewrite skips Step 3 regardless of lifecycle", () => {
    expect(previousLiveStep({ lifecycle: "before_tool_use", action: "input_rewrite" }, 4)).toBe(2)
    expect(previousLiveStep({ lifecycle: NON_TOOL_INJECT_OK_LIFECYCLE, action: "input_rewrite" }, 4)).toBe(1)
  })

  it("Step 4 + action=run_command skips Step 3 regardless of lifecycle", () => {
    expect(previousLiveStep({ lifecycle: "before_tool_use", action: "run_command" }, 4)).toBe(2)
    expect(previousLiveStep({ lifecycle: NON_TOOL_INJECT_OK_LIFECYCLE, action: "run_command" }, 4)).toBe(1)
  })

  it("Step 5 -> Step 4 and Step 6 -> Step 5 (no condition-side skips after Step 4)", () => {
    expect(previousLiveStep({ lifecycle: "before_tool_use", action: "block" }, 5)).toBe(4)
    expect(previousLiveStep({ lifecycle: "before_tool_use", action: "inject_context" }, 6)).toBe(5)
  })
})
