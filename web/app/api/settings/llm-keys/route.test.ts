import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * Q97b — same-origin proxy for /admin/llm-keys.
 *
 * GET returns {anthropic:{set,last4}, openai:{set,last4}}. PUT writes
 * new keys (missing field preserves, empty string clears, non-empty
 * overwrites). Source-level invariants:
 *   - delegates to cloud.* server-side (admin key stays off the browser)
 *   - validates the body shape + rejects unknown fields
 *   - returns 503 on CloudConfigError
 *   - returns 502 on unknown upstream failures (no body echo)
 *   - never reads MAGI_CP_ADMIN_API_KEY directly (cloud client's job)
 *   - test sub-route stays narrow (just provider narrowing)
 */
describe("/api/settings/llm-keys proxy", () => {
  const src = readFileSync(
    path.join(__dirname, "route.ts"),
    "utf-8",
  )

  it("delegates GET to cloud.getLlmKeys (admin key stays off the browser)", () => {
    expect(src).toContain("cloud.getLlmKeys")
    expect(src).not.toMatch(/process\.env\.MAGI_CP_ADMIN_API_KEY/)
  })

  it("delegates PUT to cloud.putLlmKeys", () => {
    expect(src).toContain("cloud.putLlmKeys")
  })

  it("validates that the PUT body is an object", () => {
    expect(src).toContain('"body must be an object"')
    // Helper j(payload, 400) collapses status-code emission to a
    // single call site so the inline literal is "400" not "status: 400".
    expect(src).toMatch(/, 400\)/)
  })

  it("rejects unknown PUT fields (extra=forbid mirror)", () => {
    expect(src).toMatch(/unknown field/)
    expect(src).toMatch(/KEY_FIELDS/)
  })

  it("trims pasted-key whitespace before forwarding", () => {
    expect(src).toMatch(/\.trim\(\)/)
  })

  it("caps key length so a malformed paste 400s before cloud round-trip", () => {
    expect(src).toMatch(/MAX_KEY_LEN/)
    expect(src).toContain("4_096")
  })

  it("returns 503 on CloudConfigError (missing env)", () => {
    expect(src).toContain("CloudConfigError")
    expect(src).toMatch(/return 503/)
  })

  it("collapses unknown upstream failures to 502 — no body echo", () => {
    expect(src).toMatch(/return 502/)
    expect(src).toMatch(/"upstream"/)
  })

  it("forwards cloud 401/403 + 422 with stable codes", () => {
    // 401/403 collapse to 401 (auth failure on the proxy boundary);
    // 422 surfaces verbatim so a malformed key length hits the form.
    expect(src).toMatch(/return 401/)
    expect(src).toMatch(/return 422/)
  })

  it("is force-dynamic + no-store (status reflects every restart)", () => {
    expect(src).toMatch(/dynamic = "force-dynamic"/)
    expect(src).toMatch(/"cache-control": "no-store"/)
  })

  it("only exports GET + PUT (POST handled by the test sub-route)", () => {
    expect(src).toMatch(/export async function GET\(/)
    expect(src).toMatch(/export async function PUT\(/)
    expect(src).not.toMatch(/export async function POST\(/)
  })

  it("does not echo the client's auth headers upstream", () => {
    // The route MUST NOT lift X-Api-Key / X-Admin-Api-Key off the
    // incoming request and shove it into the cloud call. The cloud
    // client injects the server-side admin key on every call.
    expect(src).not.toMatch(/req\.headers\.get\(["']x-/i)
    expect(src).not.toMatch(/Headers\(req\.headers\)/)
  })
})

describe("/api/settings/llm-keys/test sub-route", () => {
  const src = readFileSync(
    path.join(__dirname, "test", "route.ts"),
    "utf-8",
  )

  it("delegates to cloud.testLlmKeys", () => {
    expect(src).toContain("cloud.testLlmKeys")
  })

  it("accepts an optional provider narrow with a closed enum", () => {
    expect(src).toContain('"anthropic"')
    expect(src).toContain('"openai"')
    expect(src).toMatch(/provider must be anthropic or openai/)
  })

  it("returns 503 on CloudConfigError + 502 on unknown upstream", () => {
    expect(src).toContain("CloudConfigError")
    expect(src).toMatch(/return 502/)
    expect(src).toMatch(/return 503/)
  })

  it("is force-dynamic + no-store", () => {
    expect(src).toMatch(/dynamic = "force-dynamic"/)
    expect(src).toMatch(/"cache-control": "no-store"/)
  })

  it("only exports POST", () => {
    expect(src).toMatch(/export async function POST\(/)
    expect(src).not.toMatch(/export async function GET\(/)
  })
})
