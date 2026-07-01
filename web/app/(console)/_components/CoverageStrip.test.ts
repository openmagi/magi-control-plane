import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * P4 (Codex runtime adapter) — source-level invariants for the coverage
 * strip + per-pack rollup, and the cloud lib methods that feed them.
 * Sibling grep pattern to the rest of the (console) test suite.
 */
describe("CoverageStrip source invariants (P4)", () => {
  const src = readFileSync(
    path.join(__dirname, "CoverageStrip.tsx"), "utf-8",
  )

  it("maps the four coverage cells onto DS badge variants (green/amber/red/gray)", () => {
    expect(src).toContain("enforced: \"ok\"")
    expect(src).toContain("downgraded: \"review\"")
    expect(src).toContain("unsupported: \"deny\"")
    expect(src).toContain("not_applicable: \"muted\"")
  })

  it("renders CC always (reference runtime) and Codex only when enabled", () => {
    // CC's chip is unconditional; the Codex chip is guarded by
    // codexEnabled so a CC-only tenant sees exactly one runtime.
    expect(src).toContain('data-testid="coverage-strip"')
    expect(src).toContain("{codexEnabled &&")
    expect(src).toContain("policy.coverage.enforced")
  })

  it("the per-pack rollup renders the enforced/downgraded/unsupported counts", () => {
    expect(src).toContain('data-testid="pack-coverage-rollup"')
    expect(src).toContain("pack.coverage.rollup")
    expect(src).toContain("coverage.enforced")
    expect(src).toContain("coverage.downgraded")
    expect(src).toContain("coverage.unsupported")
  })

  it("the rollup renders nothing when coverage is null (CC-only tenant)", () => {
    expect(src).toContain("if (!coverage) return null")
  })
})

describe("cloud lib coverage helpers (P4)", () => {
  const cloudSrc = readFileSync(
    path.join(__dirname, "../../../lib/cloud.ts"), "utf-8",
  )

  it("getPolicyCoverage targets /policies/{id}/coverage/{runtime}", () => {
    expect(cloudSrc).toContain("getPolicyCoverage")
    expect(cloudSrc).toContain("/coverage/")
  })

  it("getPackCoverage targets /packs/{id}/coverage/{runtime}", () => {
    expect(cloudSrc).toContain("getPackCoverage")
    expect(cloudSrc).toContain("/packs/")
  })

  it("getTenantRuntime + setTenantRuntime target /tenants/{id}/runtime", () => {
    expect(cloudSrc).toContain("getTenantRuntime")
    expect(cloudSrc).toContain("setTenantRuntime")
    expect(cloudSrc).toContain("/runtime")
  })

  it("exports the coverage cell + rollup types", () => {
    expect(cloudSrc).toContain("export type CoverageCell")
    expect(cloudSrc).toContain("export type PackCoverage")
    expect(cloudSrc).toContain("export type TenantRuntimeState")
  })
})
