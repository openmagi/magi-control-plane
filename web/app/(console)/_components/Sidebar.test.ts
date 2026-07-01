import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * Source-level invariants for Sidebar. guards the IA contract:
 * 5 groups, 11 leaf items (1+2+4+3+1). Authoring group has one leaf
 * ("Rules") since policy authoring is reachable from the page's CTA;
 * /rules/new is not surfaced in the sidebar. The audit group gained
 * /endpoints (P10) alongside /overview and /ledger. The new "help"
 * group (D78) surfaces /docs. The setup group gained /settings
 * (Q97b) alongside /setup and /scripts. All keyed to i18n + HITL
 * badge plumbing.
 */
describe("Sidebar IA invariants", () => {
  const src = readFileSync(
    path.join(__dirname, "Sidebar.tsx"),
    "utf-8",
  )

  it("renders all 5 domain groups in the expected order", () => {
    const groups = src.match(/nav\.group\.\w+/g) ?? []
    expect(groups).toEqual([
      "nav.group.authoring",
      "nav.group.runtime",
      "nav.group.audit",
      "nav.group.setup",
      "nav.group.help",
    ])
  })

  it("contains exactly 12 NavItem entries (2+2+4+3+1)", () => {
    // D63: setup group adds /scripts alongside /setup so run_command
    // policies have a management surface.
    // run-share: audit group adds /shared (manage + revoke share links).
    // D78: help group adds /docs.
    // Q97b: setup group adds /settings for self-host LLM-key management.
    // P4: authoring group adds /sessions (pack-centric runtime — see
    // which CC sessions activated which packs) next to /rules.
    const items = src.match(/<NavItem\b/g) ?? []
    expect(items).toHaveLength(12)
  })

  it("authoring group surfaces the P4 /sessions entry next to /rules", () => {
    expect(src).toMatch(/href="\/sessions"/)
    expect(src).toMatch(/label=\{t\("nav\.sessions"\)\}/)
  })

  it("setup group surfaces the Q97b /settings entry", () => {
    expect(src).toMatch(/href="\/settings"/)
    expect(src).toMatch(/icon="settings"/)
  })

  it("help group surfaces the D78 /docs entry", () => {
    expect(src).toMatch(/href="\/docs"/)
    expect(src).toMatch(/icon="docs"/)
  })

  it("audit group surfaces the P10 /endpoints attestation page", () => {
    expect(src).toMatch(/href="\/endpoints"/)
    expect(src).toMatch(/icon="endpoints"/)
  })

  it("audit group surfaces the /shared run-share management page", () => {
    expect(src).toMatch(/href="\/shared"/)
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
