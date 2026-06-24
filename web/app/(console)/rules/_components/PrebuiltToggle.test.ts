import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * D60: source-level invariants for the PrebuiltToggle client component.
 *
 * The component drives the new "toggle-first" Prebuilt section on
 * /rules?tab=policies. Three properties matter:
 *
 *   1. Clicking the toggle in the simple case (setupRequired=false)
 *      fires the server action with the flipped enabled value via a
 *      hidden form. NO wizard handoff, NO modal — direct enable /
 *      disable.
 *
 *   2. Clicking the toggle in the setup-required case while the
 *      prebuilt is OFF opens an inline callout instead of submitting.
 *      The callout offers two affordances: "Configure" (Link to the
 *      wizard) and "Enable anyway" (proceeds with the toggle).
 *
 *   3. The simple-disable case (setup-required prebuilt that is
 *      already ON) stays one-click — disabling never trips the gate.
 *
 * Same source-grep pattern as VerifierExpander.test.ts / VerifierSamplesList.test.ts.
 */
describe("PrebuiltToggle source invariants (D60)", () => {
  const src = readFileSync(
    path.join(__dirname, "PrebuiltToggle.tsx"),
    "utf-8",
  )

  it("declares 'use client' (mounts inside the server-rendered rules tree)", () => {
    expect(src.startsWith('"use client"')).toBe(true)
  })

  it("renders a role=switch toggle with aria-checked bound to enabled state", () => {
    expect(src).toContain('role="switch"')
    expect(src).toContain("aria-checked={checked}")
  })

  it("posts via a hidden form to togglePrebuiltAction (no client-side fetch)", () => {
    // Same shape as PolicyToggle: a hidden form carries the (id, enabled)
    // pair, the server action revalidates /rules. Avoids the API-key
    // smuggling problem that a client-side fetch would create.
    expect(src).toContain("togglePrebuiltAction")
    expect(src).toContain('name="id"')
    expect(src).toContain('name="enabled"')
    expect(src).toContain("startTransition")
  })

  it("simple click submits without opening the callout (setupRequired=false path)", () => {
    // The setup-required branch must be guarded so a plain prebuilt
    // toggles directly without an extra confirmation step.
    expect(src).toMatch(/if\s*\(\s*setupRequired\s*&&\s*!enabled\s*\)/)
    expect(src).toContain("submit(!enabled)")
  })

  it("setup-required + OFF opens the inline callout instead of submitting", () => {
    expect(src).toContain("setCalloutOpen(true)")
    expect(src).toContain("calloutOpen")
  })

  it("disable stays one-click even when setup-required", () => {
    // The gate must only fire on OFF -> ON. The guard expression is
    // exactly `setupRequired && !enabled` (not `setupRequired` alone)
    // so the disable path takes the submit branch unconditionally.
    expect(src).not.toMatch(/if\s*\(\s*setupRequired\s*\)\s*\{[^}]*setCalloutOpen/)
  })

  it("callout exposes both Configure (Link) and Enable anyway (button)", () => {
    expect(src).toContain("copy.configure")
    expect(src).toContain("copy.enableAnyway")
    // Configure routes to the wizard via the configureHref prop (passed
    // from page.tsx prebuiltDraftHref); Enable anyway calls submit(true).
    expect(src).toContain("href={configureHref}")
    expect(src).toContain("submit(true)")
  })

  it("renders the setup-required header copy when the callout opens", () => {
    expect(src).toContain("copy.setupRequired")
    expect(src).toContain("setupHint")
  })

  it("uses optimistic UI (checked=!enabled while pending) so the toggle feels instant", () => {
    expect(src).toMatch(/pending\s*\?\s*!enabled\s*:\s*enabled/)
  })

  it("disables the toggle while the server action is in flight", () => {
    expect(src).toContain("disabled={pending}")
    expect(src).toContain("aria-busy={pending || undefined}")
  })
})
