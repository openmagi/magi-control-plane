import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * P10 — endpoints page source-level invariants.
 *
 * Light-touch: the page is server-rendered and reaches cloud.listEndpoints().
 * We assert it surfaces the columns operators care about (endpoint_id,
 * last_seen, digest, version, stale) and renders the cloud-unreachable
 * fallback. No JSX render (the i18n + DS shim costs more to set up than
 * the test is worth at this layer; cloud.test.ts covers the data path).
 */
describe("Endpoints page", () => {
  const src = readFileSync(path.join(__dirname, "page.tsx"), "utf-8")

  it("calls cloud.listEndpointsListing() for the classification meta", () => {
    expect(src).toMatch(/cloud\.listEndpointsListing/)
  })

  it("renders the columns operators read at a glance", () => {
    expect(src).toMatch(/Endpoint/)
    expect(src).toMatch(/Last seen|마지막 응답/)
    expect(src).toMatch(/Claimed digest|Claimed Digest/)
    expect(src).toMatch(/Version|버전/)
  })

  it("classifies digests against the cloud-active compile (Issue #1 P0 #2)", () => {
    expect(src).toMatch(/policy_status/)
    expect(src).toMatch(/confirmed/)
    expect(src).toMatch(/stale-policy/)
    expect(src).toMatch(/unknown/)
    expect(src).toMatch(/not-loaded/)
  })

  it("shows the cloud-active digest header so the operator can compare", () => {
    expect(src).toMatch(/cloud_active_digest/)
  })

  it("uses ErrorState when the cloud is unreachable", () => {
    expect(src).toMatch(/ErrorState/)
    expect(src).toMatch(/cloudUnreachable/)
  })

  it("flags stale endpoints distinctly from healthy", () => {
    expect(src).toMatch(/ep\.stale/)
    expect(src).toMatch(/variant="deny"/)
    expect(src).toMatch(/variant="ok"/)
  })

  it("shows EmptyState when no endpoint has attested yet", () => {
    expect(src).toMatch(/EmptyState/)
    // D72: the empty-state body content moved into the i18n dict
    // (endpoints.empty.title / endpoints.empty.body / endpoints.empty.cta).
    // The page references the keys; the literal MAGI_CP_ENDPOINT_ID
    // string now lives in dict.ts. We assert the page references the
    // empty-state keys.
    expect(src).toMatch(/endpoints\.empty\.title/)
    expect(src).toMatch(/endpoints\.empty\.body/)
    expect(src).toMatch(/endpoints\.empty\.cta/)
  })

  it("links to /setup for gate-side onboarding", () => {
    expect(src).toMatch(/href="\/setup"/)
  })
})
