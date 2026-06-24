import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"
import {
  getVerifierDescriptor,
  allVerifierDescriptors,
} from "../../../lib/verifier-descriptors"

/**
 * D52d: VerifierFieldChecks is rendered in two surfaces (catalog
 * expander + wizard verifier picker). These source-level invariants
 * guard the contract that both surfaces depend on:
 *
 *   - the component is a shared React component (no per-surface fork)
 *   - it pulls the `field_checks` list straight off the descriptor
 *     mirror (no second translation layer to drift against)
 *   - the preview branch fires when there is no descriptor OR when
 *     the descriptor's field_checks is empty (custom verifier with no
 *     authored field_checks)
 *   - the tree uses semantic <dl> markup, not <div>+text
 */
describe("VerifierFieldChecks source invariants", () => {
  const src = readFileSync(
    path.join(__dirname, "VerifierFieldChecks.tsx"),
    "utf-8",
  )

  it("imports the descriptor mirror directly (single source of truth)", () => {
    expect(src).toContain("getVerifierDescriptor")
    expect(src).toContain("@/lib/verifier-descriptors")
  })

  it("renders the preview branch when descriptor is null or field_checks is empty", () => {
    expect(src).toMatch(/descriptor === null \|\| fieldChecks\.length === 0/)
    expect(src).toContain("verifier-field-checks-preview")
  })

  it("uses a semantic <dl> for the path -> description mapping", () => {
    expect(src).toMatch(/<dl/)
    expect(src).toMatch(/<dt/)
    expect(src).toMatch(/<dd/)
  })

  it("marks the visual tree glyphs as aria-hidden so SR reads data, not ASCII", () => {
    expect(src).toMatch(/aria-hidden/)
    expect(src).toContain("├─")
    expect(src).toContain("└─")
  })

  it("exposes showFooter so the wizard picker gets verdicts + emits inline", () => {
    expect(src).toMatch(/showFooter\?:\s*boolean/)
    expect(src).toContain("verifier-field-checks-footer")
  })

  it("the preview note is i18n-routed (not a hardcoded English string)", () => {
    expect(src).toMatch(/rules\.verifier\.fieldChecks\.preview/)
  })
})

/**
 * Data-shape parity check vs the mirror. Renders correctness depends
 * on the descriptor row being well-formed; both the catalog expander
 * and the wizard picker would mis-render if the mirror drifted.
 */
describe("VerifierFieldChecks data parity vs the descriptor mirror", () => {
  it("every built-in descriptor exposes a non-empty field_checks list", () => {
    for (const d of allVerifierDescriptors()) {
      const fcs = d.field_checks ?? []
      expect(fcs.length, `field_checks empty on ${d.step}`).toBeGreaterThan(0)
      for (const fc of fcs) {
        expect(fc.path, `empty path on ${d.step}`).toBeTruthy()
        expect(fc.check_description, `empty desc on ${d.step}`).toBeTruthy()
        expect(fc.check_description.length).toBeLessThanOrEqual(200)
      }
    }
  })

  it("citation_verify carries the three rows the brief specifies", () => {
    const d = getVerifierDescriptor("citation_verify")
    expect(d).not.toBeNull()
    const paths = (d!.field_checks ?? []).map((f) => f.path)
    expect(paths).toContain("tool_input.url")
    expect(paths).toContain("tool_response.output")
    expect(paths).toContain("transcript_path")
  })

  it("source_allowlist field_check description names allowlist semantics", () => {
    const d = getVerifierDescriptor("source_allowlist")
    expect(d).not.toBeNull()
    const fcs = d!.field_checks ?? []
    expect(fcs.length).toBeGreaterThan(0)
    const blob = fcs.map((f) => `${f.path} ${f.check_description}`).join("\n")
    expect(blob).toMatch(/allowlist/i)
  })

  it("getVerifierDescriptor returns null for unknown step (preview branch fallback)", () => {
    expect(getVerifierDescriptor("does_not_exist")).toBeNull()
  })
})
