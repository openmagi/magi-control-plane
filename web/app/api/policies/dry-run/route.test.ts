import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * D53b: source-level invariants for the same-origin /api/policies/
 * dry-run proxy. Same pattern as the other proxy routes in this
 * repo: assert the contract via grep so the file stays narrow
 * enough that a future refactor can't silently drift the gate.
 */
describe("/api/policies/dry-run proxy", () => {
  const src = readFileSync(
    path.join(__dirname, "route.ts"),
    "utf-8",
  )

  it("delegates to cloud.dryRunPolicy server-side (key stays off the browser)", () => {
    expect(src).toContain("cloud.dryRunPolicy")
    // The route must NOT read MAGI_CP_ADMIN_API_KEY directly; that
    // is the cloud client's job, server-only.
    expect(src).not.toMatch(/process\.env\.MAGI_CP_ADMIN_API_KEY/)
  })

  it("validates `since` against the closed enum", () => {
    expect(src).toContain('"24h"')
    expect(src).toContain('"7d"')
    expect(src).toContain("SINCE_VALUES")
  })

  it("clamps `limit` to a server-side cap", () => {
    expect(src).toContain("LIMIT_MAX")
    expect(src).toContain("10_000")
  })

  it("rejects malformed body with 400", () => {
    expect(src).toContain("status: 400")
    expect(src).toContain("invalid body")
  })

  it("forwards cloud 422 to the client (so the inline validation message can render)", () => {
    expect(src).toContain("status: 422")
    expect(src).toContain("invalid policy")
  })

  it("collapses unknown upstream failures to 502 - no body echo", () => {
    expect(src).toContain("status: 502")
    expect(src).toContain('"upstream"')
  })

  it("returns 503 on missing config (CloudConfigError)", () => {
    expect(src).toContain("CloudConfigError")
    expect(src).toContain("status: 503")
  })

  it("is force-dynamic (no caching of dry-run results)", () => {
    expect(src).toContain('dynamic = "force-dynamic"')
    expect(src).toContain('"cache-control": "no-store"')
  })

  it("only accepts POST (read-only by intent; body is non-trivial)", () => {
    expect(src).toContain("export async function POST(")
    expect(src).not.toContain("export async function GET(")
  })
})
