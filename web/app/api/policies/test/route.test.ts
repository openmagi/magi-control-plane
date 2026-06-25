import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * D77: source-level invariants for the same-origin /api/policies/test
 * proxy. Mirrors the /dry-run proxy's test pattern: assert the
 * contract via grep so a future refactor cannot silently drift the
 * gate.
 */
describe("/api/policies/test proxy", () => {
  const src = readFileSync(path.join(__dirname, "route.ts"), "utf-8")

  it("delegates to cloud.testPolicy and cloud.testPack (key stays server-side)", () => {
    expect(src).toContain("cloud.testPolicy")
    expect(src).toContain("cloud.testPack")
    expect(src).not.toMatch(/process\.env\.MAGI_CP_ADMIN_API_KEY/)
  })

  it("requires kind in {policy, pack}", () => {
    expect(src).toContain('"policy"')
    expect(src).toContain('"pack"')
    expect(src).toContain("kind must be 'policy' or 'pack'")
  })

  it("rejects missing id with 400", () => {
    expect(src).toContain("id is required")
  })

  it("rejects non-object payload with 400", () => {
    expect(src).toContain("payload must be an object")
  })

  it("forwards cloud 404 / 422 to the client", () => {
    expect(src).toContain("status: 404")
    expect(src).toContain("status: 422")
  })

  it("collapses unknown upstream failures to 502", () => {
    expect(src).toContain("status: 502")
    expect(src).toContain('"upstream"')
  })

  it("returns 503 on CloudConfigError", () => {
    expect(src).toContain("CloudConfigError")
    expect(src).toContain("status: 503")
  })

  it("is force-dynamic + no-store", () => {
    expect(src).toContain('dynamic = "force-dynamic"')
    expect(src).toContain('"cache-control": "no-store"')
  })

  it("only accepts POST", () => {
    expect(src).toContain("export async function POST(")
    expect(src).not.toContain("export async function GET(")
  })
})
