import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * D80: source-level invariants for the custom Step 3 regex field-path
 * picker. The native <select> was replaced by a custom listbox button
 * + popover so the dropdown's visual parity matches the rest of the
 * wizard. This file pins the contract the page-side wiring depends on.
 *
 *   - Mirrors the original native select's form contract via a hidden
 *     <input name={name} value={value} /> so saveWizard reads
 *     `regexFieldPath` from FormData unchanged.
 *   - Listbox a11y roles (combobox / listbox / option) so SR users can
 *     navigate the popover.
 *   - Default chipEventName is `regex-field-path-set` so the chip row
 *     (PayloadFieldChipsClient variant="regex-target") and the picker
 *     converge on a single event seam.
 *   - "use client" pragma so the React hooks (useState / useEffect)
 *     actually run on the browser instead of being silently inlined
 *     into the server component tree.
 */
describe("FieldPathSelect | D80 custom picker contract", () => {
  const src = readFileSync(
    path.join(__dirname, "FieldPathSelect.tsx"),
    "utf-8",
  )

  it("is a client component", () => {
    expect(src).toMatch(/^"use client"/m)
  })

  it("button carries role=combobox + aria-haspopup=listbox", () => {
    expect(src).toMatch(/role="combobox"/)
    expect(src).toMatch(/aria-haspopup="listbox"/)
  })

  it("aria-expanded reflects open state", () => {
    expect(src).toMatch(/aria-expanded=\{open\}/)
  })

  it("popover is a <ul role=listbox> with role=option rows", () => {
    expect(src).toMatch(/role="listbox"/)
    expect(src).toMatch(/role="option"/)
  })

  it("emits a hidden input matching name + value (saveWizard form contract)", () => {
    expect(src).toMatch(/type="hidden"/)
    expect(src).toMatch(/name=\{name\}/)
    expect(src).toMatch(/value=\{value\}/)
  })

  it("default chipEventName is 'regex-field-path-set'", () => {
    expect(src).toMatch(/chipEventName = "regex-field-path-set"/)
  })

  it("listens for chipEventName on document and applies detail.value", () => {
    expect(src).toMatch(/document\.addEventListener\(chipEventName/)
    expect(src).toMatch(/detail\?\.value/)
  })

  it("closes on outside click via document mousedown listener", () => {
    expect(src).toMatch(/addEventListener\("mousedown"/)
  })

  it("ArrowDown / ArrowUp / Enter / Escape are handled in the listbox", () => {
    expect(src).toMatch(/"ArrowDown"/)
    expect(src).toMatch(/"ArrowUp"/)
    expect(src).toMatch(/"Enter"/)
    expect(src).toMatch(/"Escape"/)
  })

  it("renders the friendly displayLabel as primary text + raw path as mono secondary", () => {
    expect(src).toMatch(/opt\.displayLabel/)
    expect(src).toMatch(/font-mono/)
  })

  it("uses sub-path imports only (no @/components/ui barrel)", () => {
    expect(src).not.toMatch(/from\s+"@\/components\/ui"/)
  })

  it("no em-dash characters in rendered source (project hard rule)", () => {
    expect(src).not.toMatch(/—/)
  })
})
