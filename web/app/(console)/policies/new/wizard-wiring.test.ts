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
})
