import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * Source-level invariants for the /verifiers/new client form.
 *
 * Matches the SteeringAwareField.test.ts pattern. The full event-level
 * UX (add row, remove row, disabled-submit when invalid) is exercised
 * by hand in dev; the invariants here are the regression risks.
 */
describe("VerifierFormClient source invariants", () => {
  const src = readFileSync(
    path.join(__dirname, "VerifierFormClient.tsx"),
    "utf-8",
  )

  it('is marked "use client"', () => {
    expect(src.startsWith('"use client"')).toBe(true)
  })

  it("locks slug regex to /^[a-z][a-z0-9_]*$/", () => {
    expect(src).toMatch(/\/\^\[a-z\]\[a-z0-9_\]\*\$\//)
  })

  it("ships max-length constants matching backend (64 name, 500 description)", () => {
    expect(src).toMatch(/maxLength=\{64\}/)
    expect(src).toMatch(/maxLength=\{500\}/)
  })

  it("body_type is locked to preview (v1)", () => {
    // Locked enum sent in the JSON payload + helper label
    expect(src).toContain('body_type: "preview"')
    // Selector not present (no select / radio for body_type)
    expect(src).not.toMatch(/name=["']body_type["']/)
  })

  it("serializes name, description, triggers, verdict_set, body_type into a single hidden payload input", () => {
    expect(src).toMatch(/name="payload"/)
    expect(src).toContain("JSON.stringify")
  })

  it("seeds at least one trigger row by default", () => {
    expect(src).toMatch(/PreToolUse[\s\S]*matcher_class:\s*"tool"/)
  })

  it("renders + Add trigger button", () => {
    expect(src).toContain("labels.triggerAdd")
  })

  it("guards submit on the four required-field errors", () => {
    // canSubmit depends on each error null-check
    expect(src).toMatch(/nameError/)
    expect(src).toMatch(/descriptionError/)
    expect(src).toMatch(/triggersError/)
    expect(src).toMatch(/verdictError/)
  })

  it("disables submit when canSubmit is false", () => {
    expect(src).toMatch(/disabled=\{!canSubmit\}/)
  })

  it("verdict allowlist matches backend (pass / fail / needs_review / not_applicable)", () => {
    const verdicts = ["pass", "fail", "needs_review", "not_applicable"]
    for (const v of verdicts) expect(src).toContain(`"${v}"`)
  })

  it("error messages route through props (no hardcoded English strings)", () => {
    // All errors come from labels.* so i18n stays the source
    expect(src).toMatch(/labels\.errName/)
    expect(src).toMatch(/labels\.errNameSlug/)
    expect(src).toMatch(/labels\.errDescription/)
    expect(src).toMatch(/labels\.errTriggers/)
    expect(src).toMatch(/labels\.errVerdicts/)
  })

  it("uses native <select> for trigger event + matcher (no custom dropdown lib)", () => {
    expect(src).toMatch(/<select/)
    expect(src).not.toMatch(/react-select/)
  })
})
