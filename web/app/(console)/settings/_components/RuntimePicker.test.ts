import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * P4 (Codex runtime adapter) — source-level invariants for the runtime
 * picker client component. Sibling pattern to LlmKeysForm.test.ts +
 * the rules/_components/*.test.ts grep suites. The two-step confirm +
 * flag-gated radio behaviour is the load-bearing contract; a refactor
 * that drops either must fail loudly.
 */
describe("RuntimePicker source invariants (P4)", () => {
  const src = readFileSync(path.join(__dirname, "RuntimePicker.tsx"), "utf-8")

  it("is a client component using the sub-path translate (no barrel)", () => {
    expect(src).toMatch(/^"use client"/)
    expect(src).toContain('from "@/lib/i18n/dict"')
    expect(src).not.toMatch(/from "@\/components\/ui"/)
  })

  it("two-step confirm: a preview-selected state that is separate from the persisted current runtime", () => {
    // The `selected` state starts on the current runtime (step 1 is a
    // pure preview) and only `confirmSwitch` calls the server action.
    expect(src).toContain("useState<string>(current)")
    expect(src).toContain("setRuntimeAction(selected)")
    expect(src).toContain('data-testid="runtime-confirm"')
  })

  it("the confirm affordance is gated on a real, selectable change", () => {
    // canConfirm requires selected !== current AND codex selectable.
    expect(src).toContain("selected !== current")
    expect(src).toContain("codexSelectable")
    expect(src).toContain("{canConfirm &&")
  })

  it("disables the Codex radio when the build flag is off", () => {
    expect(src).toContain('id === "codex" && !codexSelectable')
    expect(src).toContain("disabled={disabled}")
    expect(src).toContain("settings.runtime.requiresFlag")
  })

  it("renders a coverage preview for the selected alternative (step 1)", () => {
    expect(src).toContain('data-testid="runtime-coverage-preview"')
    expect(src).toContain("settings.runtime.coverage_preview_full")
  })

  it("refreshes the route after a successful switch", () => {
    expect(src).toContain("router.refresh()")
  })
})
