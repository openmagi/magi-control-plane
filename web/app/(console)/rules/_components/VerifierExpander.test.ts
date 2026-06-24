import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * Source-level invariants for the VerifierExpander server component.
 *
 * Matches the SteeringAwareField.test.ts pattern (grep the rendered TSX
 * for the contract rather than spinning up React Testing Library).
 * Browser-side toggle behavior is exercised manually in dev; the
 * invariants below are the ones a future refactor is most likely to
 * silently break.
 */
describe("VerifierExpander source invariants", () => {
  const src = readFileSync(
    path.join(__dirname, "VerifierExpander.tsx"),
    "utf-8",
  )

  it("does NOT carry a 'use client' pragma (server component, no JS)", () => {
    expect(src.startsWith('"use client"')).toBe(false)
  })

  it("uses native <details>/<summary> for keyboard a11y + CSS animation", () => {
    expect(src).toMatch(/<details/)
    expect(src).toMatch(/<summary/)
  })

  it("renders the four required panels", () => {
    // Triggers / Input / Verdicts / Evidence
    expect(src).toContain("verifier-expander-triggers")
    expect(src).toContain("verifier-expander-input")
    expect(src).toContain("verifier-expander-verdicts")
    expect(src).toContain("verifier-expander-evidence")
  })

  it("renders the no-descriptor fallback for unknown steps", () => {
    expect(src).toContain("rules.verifier.expander.noDescriptor")
  })

  it("looks up payload-schema field descriptions for chip hover", () => {
    expect(src).toContain("availableFields(")
  })

  it("animates the chevron via CSS group-open transform (no JS lib)", () => {
    expect(src).toMatch(/group-open:rotate/)
    // No external animation lib
    expect(src).not.toMatch(/framer-motion|react-spring/)
  })

  it("verdict chips render via a closed allowlist tone helper", () => {
    expect(src).toMatch(/verdictTone\(/)
  })

  it("evidence shape table renders path / type / description columns", () => {
    expect(src).toContain("rules.verifier.expander.evidence.path")
    expect(src).toContain("rules.verifier.expander.evidence.type")
    expect(src).toContain("rules.verifier.expander.evidence.description")
  })
})
