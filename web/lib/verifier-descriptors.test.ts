import { describe, it, expect } from "vitest"
import {
  allVerifierDescriptors,
  getVerifierDescriptor,
} from "./verifier-descriptors"

describe("verifier descriptors mirror", () => {
  it("exposes the 5 built-in verifiers", () => {
    const steps = allVerifierDescriptors().map((d) => d.step)
    for (const expected of [
      "citation_verify",
      "privilege_scan",
      "source_allowlist",
      "structured_output",
      "prompt_injection_screen",
    ]) {
      expect(steps).toContain(expected)
    }
  })

  it("returns null for unknown step (no throw)", () => {
    expect(getVerifierDescriptor("does_not_exist")).toBeNull()
  })

  it("citation_verify carries Stop + PostToolUse triggers", () => {
    const d = getVerifierDescriptor("citation_verify")
    expect(d).not.toBeNull()
    const events = d!.triggers.map((t) => t.event)
    expect(events).toContain("Stop")
    expect(events).toContain("PostToolUse")
  })

  it("source_allowlist verdict set is pass/deny only (deterministic)", () => {
    const d = getVerifierDescriptor("source_allowlist")
    expect(d).not.toBeNull()
    expect(d!.verdict_set).toEqual(["pass", "deny"])
  })

  it("every descriptor records the common evidence envelope", () => {
    for (const d of allVerifierDescriptors()) {
      const paths = d.output_evidence.map((f) => f.path)
      for (const required of ["step", "subject", "verdict", "reasons"]) {
        expect(paths).toContain(required)
      }
    }
  })

  it("descriptor list is alphabetically stable for diff readability", () => {
    const steps = allVerifierDescriptors().map((d) => d.step)
    const sorted = [...steps].sort()
    expect(steps).toEqual(sorted)
  })
})
