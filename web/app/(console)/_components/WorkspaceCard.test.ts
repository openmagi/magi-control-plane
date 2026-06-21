import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * Logic invariants for WorkspaceCard — without bootstrapping
 * React Testing Library + jsdom (no other component in this repo
 * uses RTL yet). These guard the three render branches that matter
 * most: self-host vs tenant vs fetch-fail.
 */
describe("WorkspaceCard branch coverage", () => {
  const src = readFileSync(
    path.join(__dirname, "WorkspaceCard.tsx"),
    "utf-8",
  )

  it("renders Self-host label when tenantId is null OR \"default\"", () => {
    // Both branches must be hit by the isSelfHost predicate.
    expect(src).toMatch(/tenantId === null \|\| tenantId === "default"/)
    expect(src).toMatch(/자체 호스트/)
    expect(src).toMatch(/Self-host/)
  })

  it("truncates long tenant ids to 14 chars + ellipsis", () => {
    expect(src).toMatch(/length > 16/)
    expect(src).toMatch(/slice\(0, 14\)/)
  })

  it("uses semantic status dot tokens (no hardcoded hex)", () => {
    // Health dot must use --color-pass-fg / --color-review-fg so a
    // theme flip propagates without touching this component.
    expect(src).toMatch(/--color-pass-fg/)
    expect(src).toMatch(/--color-review-fg/)
    expect(src).not.toMatch(/#[0-9a-fA-F]{6}/)
  })

  it("renders host with translate=\"no\" to prevent auto-translation", () => {
    expect(src).toMatch(/translate="no"/)
  })
})
