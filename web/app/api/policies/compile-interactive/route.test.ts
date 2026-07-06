import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * D55b: source-level invariants for the same-origin
 * /api/policies/compile-interactive proxy. Same pattern as the other
 * proxy routes in this repo: assert the contract via grep so the file
 * stays narrow enough that a future refactor can't silently drift the
 * gate.
 */
describe("/api/policies/compile-interactive proxy", () => {
  const src = readFileSync(
    path.join(__dirname, "route.ts"),
    "utf-8",
  )

  it("reads MAGI_CP_ADMIN_API_KEY server-side only (key stays off the browser)", () => {
    expect(src).toContain("MAGI_CP_ADMIN_API_KEY")
    expect(src).toContain("X-Admin-Api-Key")
    // No client-side key reads: the proxy is a server route.
    expect(src).toContain("process.env.MAGI_CP_ADMIN_API_KEY")
  })

  it("forwards to the cloud's POST /policies/compile-interactive endpoint", () => {
    expect(src).toContain("/policies/compile-interactive")
    expect(src).toContain('method: "POST"')
  })

  it("validates history length + per-turn shape", () => {
    expect(src).toContain("MAX_HISTORY_TURNS")
    expect(src).toContain("16")
    expect(src).toContain("history must be an array")
    expect(src).toContain('"user"')
    expect(src).toContain('"assistant"')
  })

  it("validates answers shape (per-key + per-value caps)", () => {
    expect(src).toContain("MAX_ANSWERS")
    expect(src).toContain("MAX_ANSWER_KEY_CHARS")
    expect(src).toContain("MAX_ANSWER_VALUE_CHARS")
  })

  it("rejects malformed body with 400 (no cloud round-trip)", () => {
    expect(src).toMatch(/400/)
    expect(src).toContain("invalid body")
  })

  it("classifies 503 'providers not configured' as a stable error code", () => {
    // The brief: provider_unconfigured maps to an actionable assistant
    // bubble. The classification MUST happen server-side (the upstream
    // body never reaches the browser).
    expect(src).toContain("provider_unconfigured")
    expect(src).toMatch(/provider.*not configured/i)
  })

  it("forwards cloud 422 to the client (invalid_input)", () => {
    expect(src).toContain("status === 422")
    expect(src).toContain("invalid_input")
  })

  it("collapses unknown upstream failures to 502 - no body echo", () => {
    expect(src).toMatch(/upstream/)
    expect(src).toMatch(/502/)
  })

  it("returns 503 on missing admin key (CloudConfigError-equivalent)", () => {
    expect(src).toMatch(/503/)
    expect(src).toContain("server config")
  })

  it("is force-dynamic (no caching of conversational turns)", () => {
    expect(src).toContain('dynamic = "force-dynamic"')
    expect(src).toContain('"cache-control": "no-store"')
  })

  it("uses a long fetch timeout (matches /policies/compile's 90s budget)", () => {
    expect(src).toContain("FETCH_TIMEOUT_MS")
    // 90s budget so an interactive LLM call has the same window as
    // the one-shot compile.
    expect(src).toContain("90_000")
  })

  it("does NOT echo upstream response body to the client (security)", () => {
    // The upstream body lives only in `upstreamBody` then ends up in
    // a stderr console.error. The Response body is one of our stable
    // error codes, never the raw text.
    expect(src).not.toMatch(/return\s+.*upstreamBody/)
  })

  it("PR-6: validates runtime_id against the KNOWN_RUNTIMES set (rejects unknowns with 400)", () => {
    // The proxy must enforce the runtime_id allowlist so unknown values
    // never reach the cloud. KNOWN_RUNTIMES pins the two known values.
    expect(src).toContain("KNOWN_RUNTIMES")
    expect(src).toContain('"claude-code"')
    expect(src).toContain('"codex"')
    // Unknown runtime_id returns 400 before the cloud round-trip.
    expect(src).toMatch(/runtime_id/)
    expect(src).toContain("invalid body")
  })

  it("PR-6: forwards runtime_id to the cloud endpoint", () => {
    // The forwarded body includes runtime_id so the cloud can compute the
    // correct feasibility class for the operator's chosen runtime.
    expect(src).toContain("runtime_id: runtimeId")
  })

  it("PR-6: absent runtime_id is accepted (optional field)", () => {
    // The proxy must NOT 400 when runtime_id is absent from the body.
    // The guard is: only reject when rawRuntimeId is non-null/non-undefined
    // AND not in KNOWN_RUNTIMES.
    expect(src).toMatch(/rawRuntimeId !== undefined && rawRuntimeId !== null/)
  })
})
