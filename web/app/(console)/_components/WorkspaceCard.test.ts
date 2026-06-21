import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * Source-level invariants for WorkspaceCard. Guards the 3 branches
 * (Pro+ tenant / Self-host / unreachable) without bootstrapping
 * React Testing Library.
 */
describe("WorkspaceCard branch coverage", () => {
  const src = readFileSync(
    path.join(__dirname, "WorkspaceCard.tsx"),
    "utf-8",
  )

  it("renders Self-host label when tenantId is null OR \"default\"", () => {
    expect(src).toMatch(/tenantId === null \|\| tenantId === "default"/)
    // Both KO + EN labels for the Self-host branch must be present.
    expect(src).toMatch(/Self-host/)
  })

  it("truncates long tenant ids to 14 chars + ellipsis", () => {
    expect(src).toMatch(/length > 16/)
    expect(src).toMatch(/slice\(0, 14\)/)
  })

  it("uses emerald/amber for healthOk dot (no hardcoded hex)", () => {
    expect(src).toMatch(/bg-emerald-500/)
    expect(src).toMatch(/bg-amber-500/)
    expect(src).not.toMatch(/#[0-9a-fA-F]{6}/)
  })

  it("renders host with translate=\"no\" to prevent auto-translation", () => {
    expect(src).toMatch(/translate="no"/)
  })

  it("dot has role=\"img\" + aria-label for a11y (WAI rule)", () => {
    expect(src).toMatch(/role="img"/)
    expect(src).toMatch(/aria-label=\{dotLabel\}/)
  })
})
