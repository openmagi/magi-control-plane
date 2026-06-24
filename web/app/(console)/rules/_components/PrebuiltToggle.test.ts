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
 *      The callout offers two affordances: "Enable anyway" (proceeds
 *      with the toggle) and "Cancel" (backs out).
 *
 *      D60 follow-up: a previous revision rendered a "Configure" link
 *      that routed to wizard step 6, but the wizard cannot edit the
 *      verifier-side knobs in question (allowlist payload, citation
 *      corpus override). Clicking it landed on a screen that could
 *      not configure the thing. The button was removed; the callout
 *      now states the verifier-side configuration requirement
 *      directly.
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

  it("callout exposes Enable anyway + Cancel and NO Configure link", () => {
    // D60 follow-up: the previous Configure link routed to wizard
    // step 6 but the wizard cannot edit verifier-side knobs
    // (allowlist payload, citation corpus override). Shipping a
    // Configure button that lands on a screen which cannot
    // configure the thing was worse than no button — it made the
    // operator think the policy would work after the click. The
    // callout now offers Enable anyway (proceeds) and Cancel
    // (backs out), and the verifier-side requirement is stated in
    // the body copy.
    expect(src).toContain("copy.enableAnyway")
    expect(src).toContain("copy.cancel")
    expect(src).toContain("submit(true)")
    // Configure link / prop must be gone.
    expect(src).not.toContain("configureHref")
    expect(src).not.toContain("copy.configure")
  })

  it("renders the setup-required header + verifier-side-only setup copy", () => {
    expect(src).toContain("copy.setupRequired")
    expect(src).toContain("setupHint")
    // D60 follow-up: the "this knob lives in the verifier
    // configuration file" disclosure ships alongside the spec hint
    // so the operator knows where to actually set it.
    expect(src).toContain("copy.setupUnconfigurableHere")
  })

  it("surfaces a transport-fault hint when the server action throws non-redirect", () => {
    // D60 follow-up: a NEXT_REDIRECT is the SUCCESS path for a
    // server action that calls `redirect()`; the catch must
    // distinguish that from a real transport / runtime fault and
    // only surface the latter to the operator so the optimistic
    // flip's snap-back has a visible cause.
    expect(src).toContain("NEXT_REDIRECT")
    expect(src).toContain("transportError")
    expect(src).toContain("copy.transportError")
  })

  it("uses optimistic UI (checked=!enabled while pending) so the toggle feels instant", () => {
    expect(src).toMatch(/pending\s*\?\s*!enabled\s*:\s*enabled/)
  })

  it("disables the toggle while the server action is in flight", () => {
    expect(src).toContain("disabled={pending}")
    expect(src).toContain("aria-busy={pending || undefined}")
  })
})
