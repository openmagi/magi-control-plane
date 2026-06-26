import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * D82d: PrebuiltToggle simplification.
 *
 * The D60 setup-required gating popover repeatedly surfaced in
 * screenshot review as confusing UI. The toggle now only owns the
 * toggle visual + optimistic submission lifecycle. The setup-required
 * affordance lives on PrebuiltRow (a Setup → button that links to the
 * docs anchor), not here.
 *
 * Source-level invariants:
 *   1. role=switch toggle with aria-checked bound to the state.
 *   2. Posts via hidden form to togglePrebuiltAction.
 *   3. Click always submits direct — NO callout, NO setupRequired
 *      branch, NO Enable-anyway / Cancel affordances.
 *   4. Transport-fault hint surfaces non-NEXT_REDIRECT errors.
 *   5. Optimistic UI keeps the toggle feeling instant.
 */
describe("PrebuiltToggle source invariants (D82d)", () => {
  const src = readFileSync(
    path.join(__dirname, "PrebuiltToggle.tsx"),
    "utf-8",
  )

  it("declares 'use client'", () => {
    expect(src.startsWith('"use client"')).toBe(true)
  })

  it("renders a role=switch toggle with aria-checked bound to enabled state", () => {
    expect(src).toContain('role="switch"')
    expect(src).toContain("aria-checked={checked}")
  })

  it("posts via a hidden form to togglePrebuiltAction (no client-side fetch)", () => {
    expect(src).toContain("togglePrebuiltAction")
    expect(src).toContain('name="id"')
    expect(src).toContain('name="enabled"')
    expect(src).toContain("startTransition")
  })

  it("click always submits direct — no callout, no setupRequired branch", () => {
    // D82d removed the setup-required gate. The toggle submits on
    // every click; the Setup-required affordance lives on PrebuiltRow.
    expect(src).not.toMatch(/setupRequired/)
    expect(src).not.toContain("calloutOpen")
    expect(src).not.toContain("Enable anyway")
    expect(src).not.toContain("copy.enableAnyway")
    expect(src).not.toContain("copy.cancel")
    expect(src).toContain("submit(!enabled)")
  })

  it("surfaces a transport-fault hint when the server action throws non-redirect", () => {
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
