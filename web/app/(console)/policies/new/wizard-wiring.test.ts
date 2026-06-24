import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

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
    const body = src.slice(start, start + 1500)
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
      // Step1Lifecycle iterates LIFECYCLE_GROUPS (3 groups, 8 total
      // members). Pin the group declaration so a future refactor that
      // drops one of the lifecycles from the UI is obvious in the diff.
      const m = src.match(/const LIFECYCLE_GROUPS[\s\S]*?=\s*\[([\s\S]+?)\n\]/)
      expect(m).not.toBeNull()
      const block = m![1]
      const members = [
        "before_tool_use", "after_tool_use",
        "user_prompt", "pre_compact", "pre_final",
        "subagent_stop", "session_start", "session_end",
      ]
      for (const slug of members) {
        expect(block).toContain(`"${slug}"`)
      }
    })

    it("ACTIONS_BY_LIFECYCLE narrows audit-only events per the matrix", () => {
      // pre_final + subagent_stop + session_start + session_end are
      // audit-only per matrix.LEGAL_COMBINATIONS. Saving with block
      // on one of those must be refused before the round-trip.
      const m = src.match(/ACTIONS_BY_LIFECYCLE[\s\S]*?=\s*\{([\s\S]+?)\n\}/)
      expect(m).not.toBeNull()
      const body = m![1]
      expect(body).toMatch(/pre_final:\s*\[\s*"audit"\s*\]/)
      expect(body).toMatch(/subagent_stop:\s*\[\s*"audit"\s*\]/)
      expect(body).toMatch(/session_start:\s*\[\s*"audit"\s*\]/)
      expect(body).toMatch(/session_end:\s*\[\s*"audit"\s*\]/)
      // user_prompt has the full pre-event action set.
      expect(body).toMatch(/user_prompt:\s*\[\s*"block",\s*"ask",\s*"audit"\s*\]/)
      // pre_compact has block + audit (matrix admits both).
      expect(body).toMatch(/pre_compact:\s*\[\s*"block",\s*"audit"\s*\]/)
    })

    it("saveWizard refuses matrix-illegal action choices", () => {
      // Pinning the validation block keeps the client-side guard from
      // silently disappearing on a future refactor (the cloud's
      // canonical guard is matrix.validate_combination).
      // D56d: widened to a per-(lifecycle, matcher_class) check so
      // (PreToolUse, wildcard, block) — lifecycle-legal but matrix-
      // illegal — gets caught here. Pin the combination helper instead
      // of the now-superseded lifecycle-only lookup.
      const start = src.indexOf("async function saveWizard")
      expect(start).toBeGreaterThan(-1)
      const body = src.slice(start, start + 4000)
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

    it("Step 2 chip row is radio-single-select (not checkbox-multi)", () => {
      // The toolScope_chip control must be `type="radio"` so the
      // browser enforces single-select per radio-group name.
      const start = src.indexOf("function Step2ToolScope")
      const end = src.indexOf("\n}\n", start)
      expect(start).toBeGreaterThan(-1)
      expect(end).toBeGreaterThan(start)
      const body = src.slice(start, end)
      // Exactly one toolScope_chip input on Step 2, and it's a radio.
      expect(body).toMatch(/name="toolScope_chip"[\s\S]*?type="radio"|type="radio"[\s\S]*?name="toolScope_chip"/)
      // No checkbox flavour anywhere on Step 2.
      expect(body).not.toMatch(/name="toolScope_chip"[\s\S]*?type="checkbox"/)
      // The MCP free-text input is a single name field (maxLength tight).
      expect(body).toMatch(/name="toolScope_custom"[\s\S]*?maxLength=\{256\}/)
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
      const start = src.indexOf("function _irToWizardState")
      expect(start).toBeGreaterThan(-1)
      const body = src.slice(start, start + 2500)
      expect(body).toMatch(/droppedAlternation/)
      expect(body).toMatch(/matcher\.includes\("\|"\)/)
    })

    it("saveWizard refuses a multi-tool toolScope before classification", () => {
      // The hard refusal must fire BEFORE matcherClassForToolScope
      // collapses to first-token, otherwise a stale `Bash,Edit` would
      // silently persist a Bash matcher under the multi-tool URL.
      // Pin the early-refusal block and the redirect target.
      const start = src.indexOf("async function saveWizard")
      expect(start).toBeGreaterThan(-1)
      const body = src.slice(start, start + 5000)
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
      const start = src.indexOf("async function saveWizard")
      const body = src.slice(start, start + 5000)
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
})
