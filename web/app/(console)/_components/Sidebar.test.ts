import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * Source-level invariants for Sidebar — guards the IA contract:
 * 4 groups, 6 leaf items (1+2+2+1). Authoring group has one leaf
 * ("Rules") since policy authoring is reachable from the page's CTA;
 * /rules/new is not surfaced in the sidebar. All keyed to i18n +
 * HITL badge plumbing.
 */
describe("Sidebar IA invariants", () => {
  const src = readFileSync(
    path.join(__dirname, "Sidebar.tsx"),
    "utf-8",
  )

  it("renders all 4 domain groups in the expected order", () => {
    const groups = src.match(/nav\.group\.\w+/g) ?? []
    expect(groups).toEqual([
      "nav.group.authoring",
      "nav.group.runtime",
      "nav.group.audit",
      "nav.group.setup",
    ])
  })

  it("contains exactly 6 NavItem entries (1+2+2+1)", () => {
    const items = src.match(/<NavItem\b/g) ?? []
    expect(items).toHaveLength(6)
  })

  it("authoring group points only at /rules (New policy lives in-page)", () => {
    expect(src).toMatch(/href="\/rules"/)
    expect(src).not.toMatch(/href="\/rules\/new"/)
    expect(src).not.toMatch(/href="\/policies"\s/)
    expect(src).not.toMatch(/href="\/presets"/)
  })

  it("wires the HITL pending-count badge", () => {
    expect(src).toMatch(/icon="hitl"/)
    expect(src).toMatch(/badge=\{hitlPending\}/)
  })

  it("uses cached getWorkspaceData (not an inline fetch)", () => {
    expect(src).toMatch(/getWorkspaceData/)
    expect(src).not.toMatch(/loadSidebarData/)   // D4 retired this name
  })

  it("respects tenant.synthetic when deciding self-host branch", () => {
    expect(src).toMatch(/tenant\?\.synthetic \? null : \(tenant\?\.id \?\? null\)/)
  })
})
