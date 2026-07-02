import { describe, it, expect, beforeEach, afterEach } from "vitest"
import { signSession, verifySession, isLoopbackHost } from "./dashboard-auth"

/**
 * WEB-1: the self-host console auth backstop. Loopback stays open (single
 * operator default); non-loopback requires a signed session; no secret means
 * fail-closed (an exposed dashboard denies).
 */
describe("isLoopbackHost", () => {
  it("recognizes loopback hosts (with or without port)", () => {
    expect(isLoopbackHost("localhost")).toBe(true)
    expect(isLoopbackHost("localhost:8787")).toBe(true)
    expect(isLoopbackHost("127.0.0.1:3000")).toBe(true)
    expect(isLoopbackHost("[::1]:3000")).toBe(true)
  })
  it("rejects public hosts and null", () => {
    expect(isLoopbackHost("cp.example.com")).toBe(false)
    expect(isLoopbackHost("10.0.0.5")).toBe(false)
    expect(isLoopbackHost(null)).toBe(false)
  })
})

describe("signSession / verifySession", () => {
  const OLD_SESSION = process.env.MAGI_CP_DASHBOARD_SESSION_SECRET
  const OLD_ADMIN = process.env.MAGI_CP_ADMIN_HMAC_SECRET

  beforeEach(() => {
    process.env.MAGI_CP_DASHBOARD_SESSION_SECRET = "test-secret-xyz"
    delete process.env.MAGI_CP_ADMIN_HMAC_SECRET
  })
  afterEach(() => {
    if (OLD_SESSION === undefined) delete process.env.MAGI_CP_DASHBOARD_SESSION_SECRET
    else process.env.MAGI_CP_DASHBOARD_SESSION_SECRET = OLD_SESSION
    if (OLD_ADMIN === undefined) delete process.env.MAGI_CP_ADMIN_HMAC_SECRET
    else process.env.MAGI_CP_ADMIN_HMAC_SECRET = OLD_ADMIN
  })

  it("round-trips a valid session", async () => {
    const tok = await signSession("t-abc")
    expect(tok).toBeTruthy()
    expect(await verifySession(tok!)).toBe(true)
  })

  it("rejects a tampered signature", async () => {
    const tok = await signSession("t-abc")
    const last = tok!.slice(-1)
    const bad = tok!.slice(0, -1) + (last === "a" ? "b" : "a")
    expect(await verifySession(bad)).toBe(false)
  })

  it("rejects a malformed token", async () => {
    expect(await verifySession("not-a-token")).toBe(false)
    expect(await verifySession("only.two")).toBe(false)
    expect(await verifySession(undefined)).toBe(false)
  })

  it("rejects an expired token", async () => {
    // exp = 1 (1970) is far in the past -> rejected before HMAC check.
    expect(await verifySession("t-abc.1.deadbeef")).toBe(false)
  })

  it("fails closed when no signing secret is configured", async () => {
    delete process.env.MAGI_CP_DASHBOARD_SESSION_SECRET
    delete process.env.MAGI_CP_ADMIN_HMAC_SECRET
    expect(await signSession("t-abc")).toBeNull()
    expect(await verifySession("t-abc.9999999999.abc")).toBe(false)
  })

  it("falls back to ADMIN_HMAC_SECRET when the session secret is unset", async () => {
    delete process.env.MAGI_CP_DASHBOARD_SESSION_SECRET
    process.env.MAGI_CP_ADMIN_HMAC_SECRET = "admin-secret"
    const tok = await signSession("t-abc")
    expect(tok).toBeTruthy()
    expect(await verifySession(tok!)).toBe(true)
  })

  it("does not verify a token signed under a different secret", async () => {
    const tok = await signSession("t-abc")
    process.env.MAGI_CP_DASHBOARD_SESSION_SECRET = "rotated-secret"
    expect(await verifySession(tok!)).toBe(false)
  })
})
