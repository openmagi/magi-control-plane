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
    // The component is rendered inside the wiredSteps.map iteration so
    // every author-visible verifier card gets its own tree.
    expect(src).toMatch(
      /wiredSteps\.map\([\s\S]*?VerifierFieldChecks[\s\S]*?showFooter/,
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
    expect(src).toMatch(/toolScope:\s*searchParams\.toolScope\s*\|\|\s*draftState\?\.toolScope/)
    expect(src).toMatch(/conditionKind:\s*conditionKind\s*\?\?\s*draftState\?\.conditionKind/)
    expect(src).toMatch(/action:\s*action\s*\?\?\s*draftState\?\.action/)
  })

  it("D56a: IR-to-WizardState mapping covers the prebuilt event surface", () => {
    // The 5 prebuilts emit PreToolUse / PostToolUse / Stop. The
    // mapper must cover all three; anything else degrades to
    // undefined (Step 1's default kicks in). Source-level pin so
    // a future widening of the event surface lands consciously.
    expect(src).toMatch(/case "PreToolUse":\s*lifecycle = "before_tool_use"/)
    expect(src).toMatch(/case "PostToolUse":\s*lifecycle = "after_tool_use"/)
    expect(src).toMatch(/case "Stop":\s*lifecycle = "pre_final"/)
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
      const advanceBody = src.slice(advanceStart, advanceStart + 3000)
      expect(advanceBody).toMatch(/!lifecycleHasToolScope\(lifecycle\)/)
    })

    it("_irToWizardState round-trips every CC event the wizard understands", () => {
      // 8 case statements: three pinned by the D56a tests above, five
      // added in D56c. Pin all eight here so a future trim to the IR
      // -> wizard mapper is intentional rather than a silent regression.
      const cases = [
        ["PreToolUse", "before_tool_use"],
        ["PostToolUse", "after_tool_use"],
        ["Stop", "pre_final"],
        ["SubagentStop", "subagent_stop"],
        ["UserPromptSubmit", "user_prompt"],
        ["PreCompact", "pre_compact"],
        ["SessionStart", "session_start"],
        ["SessionEnd", "session_end"],
      ]
      for (const [ev, life] of cases) {
        const re = new RegExp(`case "${ev}":\\s*[^\\n]*lifecycle = "${life}"`)
        expect(src).toMatch(re)
      }
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
      const body = src.slice(start, start + 3000)
      expect(body).not.toMatch(/scopeChipsRaw/)
      // No CSV merge: single pick wins.
      expect(body).not.toMatch(/merged\.join\(","\)/)
      // The chip + custom values collapse to a single string.
      expect(body).toMatch(/scopeChip\s*\|\|\s*scopeCustom/)
    })
  })
})
