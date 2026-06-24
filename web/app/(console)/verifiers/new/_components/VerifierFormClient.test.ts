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
    // D57c follow-up: caller_assembly_hint is emitted as trimmed
    // prose for caller_assembled rows and as the empty string for
    // cc_stdin rows. The conditional preserves the typed prose in
    // component state across radio bounces (no data loss) while
    // keeping the server invariant.
    expect(src).toMatch(/caller_assembly_hint:\s*\n?\s*inputAssembly === "caller_assembled" \? callerAssemblyHint\.trim\(\) : ""/)
  })

  it("D57c: caller_assembled reveals the hint textarea inline", () => {
    expect(src).toMatch(/inputAssembly === "caller_assembled" &&/)
    expect(src).toContain("caller-assembly-hint-row")
    expect(src).toContain("caller-assembly-hint")
  })

  it("D57c follow-up: switching to cc_stdin preserves typed hint in state (no data loss)", () => {
    // D57c follow-up: do NOT wipe the textarea on switch. The wire
    // payload excludes the hint while cc_stdin is selected, which
    // satisfies the server invariant; preserving the state means
    // bouncing the radio doesn't cost the operator their up-to-500
    // chars of authored prose. Lock the absence of setCallerAssemblyHint("")
    // inside the cc_stdin handler so a future regression to wipe
    // gets caught.
    const ccHandlerMatch = src.match(/value="cc_stdin"[\s\S]*?onSelect=\{\(\) => \{[\s\S]*?\}\}/)
    expect(ccHandlerMatch, "cc_stdin onSelect handler missing").not.toBeNull()
    expect(ccHandlerMatch![0]).not.toMatch(/setCallerAssemblyHint\(""\)/)
    expect(ccHandlerMatch![0]).toContain('setInputAssembly("cc_stdin")')
  })

  it("D57c follow-up: radio onSelect handlers do not unconditionally setTouchedHint", () => {
    // WCAG 3.3.1: clicking the OTHER radio card should not preempt
    // the touched gate. Otherwise selecting cc_stdin first then
    // switching to caller_assembled would render the "hint required"
    // error the instant the textarea is revealed, contradicting the
    // documented "silent on initial render" design. The textarea's
    // own onBlur / onChange + submitAttempted already cover the
    // WCAG case.
    const ccHandlerMatch = src.match(/value="cc_stdin"[\s\S]*?onSelect=\{\(\) => \{[\s\S]*?\}\}/)
    const callerHandlerMatch = src.match(/value="caller_assembled"[\s\S]*?onSelect=\{\(\) => \{[\s\S]*?\}\}/)
    expect(ccHandlerMatch, "cc_stdin onSelect handler missing").not.toBeNull()
    expect(callerHandlerMatch, "caller_assembled onSelect handler missing").not.toBeNull()
    expect(ccHandlerMatch![0]).not.toMatch(/setTouchedHint\(true\)/)
    expect(callerHandlerMatch![0]).not.toMatch(/setTouchedHint\(true\)/)
  })

  it("D57c follow-up: radio name is scoped via useId() so two form instances don't merge groups", () => {
    // The hardcoded `name="input-assembly"` literal would merge two
    // simultaneously-mounted forms into one radio group. useId()
    // produces a stable per-instance suffix that scopes the name.
    expect(src).toContain("useId")
    expect(src).toMatch(/inputAssemblyRadioName\s*=\s*`input-assembly-\$\{reactId\}`/)
    // The InputAssemblyOption's <input type="radio"> takes `name`
    // as a prop now (threaded down from the parent), not as a
    // hardcoded literal. Catch any regression that re-introduces
    // the static name on the JSX element.
    expect(src).not.toMatch(/<input[\s\S]{0,160}?name="input-assembly"/)
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
    // The caller_assembled "hint required" label is still wired
    // through the local validator. The `errCallerAssemblyHintOnCcStdin`
    // label is no longer surfaced by the client (the cc_stdin path
    // strips the hint from the wire payload so the server invariant
    // can never fail from this client), but the label still ships in
    // the i18n dict for the server-side error replay surface.
    expect(src).toMatch(/labels\.errCallerAssemblyHint\b/)
  })

  // ── D64 polish: field-check path display label SR parity ─────────
  it("D64 polish: field-check friendly helper is no longer aria-hidden", () => {
    // The friendly resolution must reach SR users to match the chip /
    // tree-row / expander surfaces; the D64 brief surfaces both raw
    // path and friendly label everywhere else, this row was the outlier.
    const helperBlock = src.match(
      /field-check-path-display-label[\s\S]{0,400}/,
    )?.[0] ?? ""
    expect(helperBlock).not.toMatch(/aria-hidden/)
  })

  it("D64 polish: field-check input wires the friendly helper id into aria-describedby", () => {
    // SR users hear the input value + the friendly label after it, the
    // same way the chip + expander surfaces announce the friendly cue.
    expect(src).toMatch(/field-check-path-display-label-\$\{fc\._id\}/)
    // aria-describedby is composed (helper id + row error id) so the
    // friendly cue and the row error coexist for SR users.
    expect(src).toMatch(/aria-describedby=\{describedBy\}/)
  })
})
