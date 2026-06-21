import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * Source-level invariants for the workspace cache layer. The actual
 * cloud calls run through `cloud` (path-aliased to @/lib/cloud) which
 * vitest's default resolver doesn't follow, so we assert structure
 * instead of executing the wrapped fetch.
 */
describe("workspace data layer", () => {
  const src = readFileSync(
    path.join(__dirname, "workspace.ts"),
    "utf-8",
  )

  it("exports the WORKSPACE_TAG constant as \"workspace\"", () => {
    expect(src).toMatch(/export const WORKSPACE_TAG = "workspace"/)
  })

  it("wraps the loader in unstable_cache with the WORKSPACE_TAG tag", () => {
    expect(src).toMatch(/unstable_cache\(/)
    expect(src).toMatch(/tags: \[WORKSPACE_TAG\]/)
  })

  it("revalidate window is 30 seconds (matches operator runbook)", () => {
    expect(src).toMatch(/revalidate: 30/)
  })

  it("uses a stable single-element cache key (no per-page variation)", () => {
    expect(src).toMatch(/\["workspace-sidebar-v1"\]/)
  })

  it("loader degrades gracefully on every cloud call", () => {
    // tenant fetch → .catch returns null
    expect(src).toMatch(/getMyTenant.*\.catch\(\(\) => null\)/s)
    // healthz fetch → .catch returns false
    expect(src).toMatch(/healthz.*\.catch\(\(\) => false\)/s)
    // hitl count → .catch returns 0
    expect(src).toMatch(/listHitl[\s\S]*?\.catch[\s\S]*?return 0/)
  })

  it("respects AbortSignal.timeout(2000) on the healthz probe", () => {
    expect(src).toMatch(/AbortSignal\.timeout\(2000\)/)
  })
})
