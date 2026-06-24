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

  // ── D52d: field_checks editor ────────────────────────────────────
  it("D52d: serializes field_checks into the payload", () => {
    expect(src).toContain("field_checks: fieldChecks.map")
    // path + check_description are the only persisted keys; _id is
    // client-only and must be stripped (same pattern as triggers).
    expect(src).toMatch(/path: path\.trim\(\)/)
    expect(src).toMatch(/check_description: check_description\.trim\(\)/)
  })

  it("D52d: seeds at least one (empty) field_check row", () => {
    expect(src).toMatch(/path: ""[,\s]+check_description: ""/)
  })

  it("D52d: blocks submit when field_checks are invalid", () => {
    expect(src).toMatch(/fieldChecksError/)
    expect(src).toMatch(/canSubmit[\s\S]*!fieldChecksError/)
  })

  it("D52d: enforces description max-length 200 + path max-length 128", () => {
    expect(src).toMatch(/MAX_FIELD_CHECK_PATH_LEN\s*=\s*128/)
    expect(src).toMatch(/MAX_FIELD_CHECK_DESC_LEN\s*=\s*200/)
  })

  it("D52d: each field_check row has add + remove + per-row aria labels", () => {
    expect(src).toContain("labels.fieldCheckAdd")
    expect(src).toContain("labels.fieldCheckRemove")
    expect(src).toContain("data-testid=\"field-check-row\"")
  })

  // ── D57c: input_assembly select + caller_assembly_hint ──────────
  it("D57c: ships the input_assembly select as two distinguished radio cards", () => {
    // Two options: cc_stdin (default) + caller_assembled. Rendered as
    // radio-cards so the contract is visually distinguished from a
    // generic dropdown — picking the wrong side is a load-bearing
    // decision the wizard surfaces inline.
    expect(src).toMatch(/input-assembly-option-\$\{value\}/)
    expect(src).toMatch(/value="cc_stdin"/)
    expect(src).toMatch(/value="caller_assembled"/)
    expect(src).toMatch(/role="radiogroup"/)
  })

  it("D57c: serializes input_assembly + caller_assembly_hint into the payload", () => {
    expect(src).toContain("input_assembly: inputAssembly")
    expect(src).toContain("caller_assembly_hint: callerAssemblyHint.trim()")
  })

  it("D57c: caller_assembled reveals the hint textarea inline", () => {
    expect(src).toMatch(/inputAssembly === "caller_assembled" &&/)
    expect(src).toContain("caller-assembly-hint-row")
    expect(src).toContain("caller-assembly-hint")
  })

  it("D57c: switching to cc_stdin clears the hint state (server invariant)", () => {
    // cc_stdin rows must leave the hint blank; clearing on switch
    // matches the server validate_input_assembly() invariant and
    // keeps the operator from having to wipe the textarea by hand
    // before submitting.
    expect(src).toMatch(/setInputAssembly\("cc_stdin"\)\s*\n\s*\/\/[\s\S]*?setCallerAssemblyHint\(""\)/)
  })

  it("D57c: blocks submit when (assembly, hint) pair is invalid", () => {
    expect(src).toMatch(/callerAssemblyHintError/)
    expect(src).toMatch(/canSubmit[\s\S]*!callerAssemblyHintError/)
  })

  it("D57c: caps the caller_assembly_hint at 500 chars", () => {
    expect(src).toMatch(/MAX_CALLER_ASSEMBLY_HINT_LEN\s*=\s*500/)
    expect(src).toMatch(/maxLength=\{MAX_CALLER_ASSEMBLY_HINT_LEN\}/)
  })

  it("D57c: error messages route through labels (no hardcoded English)", () => {
    expect(src).toMatch(/labels\.errCallerAssemblyHint\b/)
    expect(src).toMatch(/labels\.errCallerAssemblyHintOnCcStdin\b/)
  })
})
