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

  it("tracks per-field touched state so errors stay silent on mount", () => {
    // WCAG 3.3.1: error messages must be user-triggered, not announced
    // the moment the page mounts. Touched flips to true on first edit
    // or blur; the role='alert' p tag is conditional on it.
    expect(src).toMatch(/touched/)
    expect(src).toMatch(/showNameError/)
    expect(src).toMatch(/showDescriptionError/)
  })

  it("aria-describedby keeps the helper id alongside the error id", () => {
    // The description helper carries the live character counter
    // ({n}/500). Dropping it from aria-describedby when an error
    // appears would mute the counter for SR users.
    expect(src).toMatch(/custom-verifier-description-helper/)
    expect(src).toMatch(/custom-verifier-description-error/)
  })

  it("uses stable row ids (crypto.randomUUID) instead of index keys", () => {
    // React identity must follow row content, not array position;
    // otherwise removing a middle row mis-attributes focus + breaks
    // aria-live announcements that reference idx.
    expect(src).toMatch(/_genRowId/)
    expect(src).toMatch(/key=\{tr\._id\}/)
    // The {idx} key pattern is gone; the only remaining `idx` reference
    // (if any) must not be used as a React key.
    expect(src).not.toMatch(/key=\{idx\}/)
  })

  it("wraps the visible × glyph in aria-hidden so SR keys on aria-label", () => {
    // Otherwise some screen readers announce both the aria-label
    // ("remove this trigger") and the multiplication sign.
    expect(src).toMatch(/<span aria-hidden="true">×<\/span>/)
  })

  it("wraps triggers + verdicts in role='group' with aria-labelledby", () => {
    // Group context for screen-reader users navigating the checkbox /
    // select cluster.
    expect(src).toMatch(/role="group"/)
    expect(src).toMatch(/aria-labelledby="triggers-label"/)
    expect(src).toMatch(/aria-labelledby="verdict-set-label"/)
  })

  it("verdict label carries focus-within ring so keyboard focus is visible", () => {
    // The checkbox is sr-only; without a focus-within ring on the
    // wrapping label, a sighted keyboard user sees zero focus
    // indicator (WCAG 2.4.7).
    expect(src).toMatch(/focus-within:ring-2/)
  })

  it("trigger selects + remove button carry focus styling (not browser-default-only)", () => {
    // The native <select> default focus ring is invisible on most
    // browsers; add focus:ring to match the input treatment.
    expect(src).toMatch(/focus:ring-2 focus:ring-\[var\(--color-border-focus\)\]\/40/)
  })

  it("preview pill uses theme-aware CSS variable tokens", () => {
    // Same dark-mode hardening as the expander chips.
    expect(src).toMatch(/var\(--color-review-bg/)
    expect(src).not.toMatch(/bg-amber-50/)
  })
})
