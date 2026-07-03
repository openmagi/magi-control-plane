import { describe, it, expect, beforeEach, afterEach } from "vitest"
import { signSession, verifySession, isLoopbackHost } from "./dashboard-auth"

/**
 * WEB-1: the self-host console auth backstop. Fail-closed by default: every
 * console route requires a signed session (the host header is spoofable, so
 * the loopback exception is opt-in via MAGI_CP_TRUST_LOOPBACK_HEADER=1). No
 * secret also means fail-closed (an exposed dashboard denies).
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

describe("trustLoopbackHeader (localhost trusted by default)", () => {
  const orig = process.env.MAGI_CP_TRUST_LOOPBACK_HEADER
  afterEach(() => {
    if (orig === undefined) delete process.env.MAGI_CP_TRUST_LOOPBACK_HEADER
    else process.env.MAGI_CP_TRUST_LOOPBACK_HEADER = orig
  })
  it("is TRUE by default (self-host single-operator localhost: no login)", async () => {
    delete process.env.MAGI_CP_TRUST_LOOPBACK_HEADER
    const { trustLoopbackHeader } = await import("./dashboard-auth")
    expect(trustLoopbackHeader()).toBe(true)
  })
  it("only an explicit '0' opts out (belt-and-suspenders session-always)", async () => {
    process.env.MAGI_CP_TRUST_LOOPBACK_HEADER = "0"
    const { trustLoopbackHeader } = await import("./dashboard-auth")
    expect(trustLoopbackHeader()).toBe(false)
    process.env.MAGI_CP_TRUST_LOOPBACK_HEADER = "1"
    expect(trustLoopbackHeader()).toBe(true)
  })
})

describe("requestCameThroughProxy (WEB-1 P0: proxy hop suppresses loopback trust)", () => {
  it("false for a direct request (no forwarding headers)", async () => {
    const { requestCameThroughProxy } = await import("./dashboard-auth")
    expect(requestCameThroughProxy(new Headers({ host: "localhost:3000" }))).toBe(false)
  })
  it("true when x-forwarded-for / x-forwarded-host / forwarded is present", async () => {
    const { requestCameThroughProxy } = await import("./dashboard-auth")
    expect(requestCameThroughProxy(new Headers({ "x-forwarded-for": "1.2.3.4" }))).toBe(true)
    expect(requestCameThroughProxy(new Headers({ "x-forwarded-host": "evil.com" }))).toBe(true)
    expect(requestCameThroughProxy(new Headers({ forwarded: "for=1.2.3.4" }))).toBe(true)
  })
})
