import { describe, it, expect } from "vitest"
import { isSameOrigin } from "./same-origin"

function req(headers: Record<string, string>): import("next/server").NextRequest {
  return { headers: new Headers(headers) } as unknown as import("next/server").NextRequest
}

describe("isSameOrigin (WEB-2 CSRF guard)", () => {
  it("allows same-origin / same-site / none via Sec-Fetch-Site", () => {
    expect(isSameOrigin(req({ "sec-fetch-site": "same-origin" }))).toBe(true)
    expect(isSameOrigin(req({ "sec-fetch-site": "same-site" }))).toBe(true)
    expect(isSameOrigin(req({ "sec-fetch-site": "none" }))).toBe(true)
  })

  it("rejects cross-site via Sec-Fetch-Site", () => {
    expect(isSameOrigin(req({ "sec-fetch-site": "cross-site" }))).toBe(false)
  })

  it("falls back to Origin vs Host when Sec-Fetch-Site is absent", () => {
    expect(
      isSameOrigin(req({ origin: "https://cp.local", host: "cp.local" })),
    ).toBe(true)
    expect(
      isSameOrigin(req({ origin: "https://evil.example", host: "cp.local" })),
    ).toBe(false)
  })

  it("allows a caller with no Origin (server / curl / tests)", () => {
    expect(isSameOrigin(req({}))).toBe(true)
  })

  it("rejects a malformed Origin", () => {
    expect(isSameOrigin(req({ origin: "://not a url", host: "cp.local" }))).toBe(false)
  })
})
