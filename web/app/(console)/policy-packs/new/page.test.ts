import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * E1 (audit CV-11): the new-pack picker is POLICY-first - it sources
 * options from listPolicyGroups (the complete policy view), so an operator
 * can put a POLICY in a pack. Source-level invariants only.
 */
const src = readFileSync(path.join(__dirname, "page.tsx"), "utf-8")

describe("new-pack picker is policy-first", () => {
  it("sources options from listPolicyGroups, not raw listPolicies", () => {
    expect(src).toContain("cloud.listPolicyGroups()")
    expect(src).not.toContain("cloud.listPolicies()")
  })

  it("labels a compound policy with its member-rule count", () => {
    expect(src).toContain("g.rule_ids.length")
    expect(src).toMatch(/kind === "compound"/)
  })

  it("still lists prebuilts for the not-yet-materialized case", () => {
    expect(src).toContain("cloud.listPrebuiltPolicies()")
  })
})
