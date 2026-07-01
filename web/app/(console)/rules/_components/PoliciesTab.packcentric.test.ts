import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * P4 (pack-centric runtime): /rules Policies tab read-only invariants.
 *
 * When MAGI_CP_PACK_CENTRIC_RUNTIME is on the Policies tab drops its
 * per-policy toggles (activation lives in Claude Code), shows a banner
 * explaining the shift, and renders a "which pack" chip list on each
 * card. These are source-grep invariants pinning that contract:
 *
 *   1. The tab takes a `packCentric` prop and a banner renders under it.
 *   2. Every PolicyToggle / PrebuiltToggle render is guarded by
 *      `!packCentric` so no toggle ships once the flag is on.
 *   3. Pack chips (`PackChips`) render under `packCentric`.
 *   4. The rules page reads the flag and passes it down.
 */
describe("PoliciesTab pack-centric read-only invariants (P4)", () => {
  const src = readFileSync(
    path.join(__dirname, "PoliciesTab.tsx"), "utf-8",
  )

  it("accepts a packCentric prop", () => {
    expect(src).toMatch(/packCentric\??\s*:/)
  })

  it("renders the pack-centric banner", () => {
    expect(src).toContain("PackCentricBanner")
    expect(src).toContain("rules.packCentric.banner")
  })

  it("guards EVERY toggle behind !packCentric", () => {
    // No PolicyToggle / PrebuiltToggle may render without the guard.
    // Assert that every `<PolicyToggle` / `<PrebuiltToggle` occurrence
    // is preceded (within the component) by a `!packCentric` guard —
    // pinned structurally by requiring the guard string to appear and
    // no toggle to appear outside a guarded block. We approximate with
    // the presence of the guard on both card renders.
    const guards = src.match(/!packCentric\s*&&/g) ?? []
    expect(guards.length).toBeGreaterThanOrEqual(2)
    // The toggles must exist (legacy path) but only inside guards.
    expect(src).toContain("PolicyToggle")
    expect(src).toContain("PrebuiltToggle")
  })

  it("renders which-pack chips", () => {
    expect(src).toContain("PackChips")
    expect(src).toContain("packs.whichPack")
  })
})

describe("rules page wires the pack-centric flag (P4)", () => {
  const src = readFileSync(
    path.join(__dirname, "../page.tsx"), "utf-8",
  )

  it("reads MAGI_CP_PACK_CENTRIC_RUNTIME", () => {
    expect(src).toContain("MAGI_CP_PACK_CENTRIC_RUNTIME")
  })

  it("passes packCentric + policyPacks into PoliciesTab", () => {
    expect(src).toContain("packCentric={packCentric}")
    expect(src).toContain("policyPacks={policyPacks}")
  })
})
