import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * C1 (audit Q1 / decision 1: rule -> policy -> pack): the Policies tab is
 * policy-first. Source-level invariants for the new PolicyList primary
 * component + its page wiring.
 */
const HERE = __dirname
const read = (rel: string) => readFileSync(path.join(HERE, rel), "utf-8")

describe("PolicyList is the policy-first primary list", () => {
  const src = read("PolicyList.tsx")

  it("renders ALL policies from the group view (no multi-rule filter)", () => {
    // The old PolicyGroupSection filtered to rule_ids.length > 1; the new
    // list must show every policy incl. one-rule ones.
    expect(src).not.toMatch(/rule_ids\.length\s*>\s*1/)
    expect(src).toContain("groups.map(")
  })

  it("counts POLICIES, not rules", () => {
    expect(src).toContain('t("rules.summary.policies"')
    expect(src).toMatch(/n:\s*nfFormat\(groups\.length\)/)
  })

  it("shows member RULES as an expandable drill-down (implementation detail)", () => {
    expect(src).toContain('data-testid={`policy-rules-')
    expect(src).toContain("g.rule_ids.map(")
    expect(src).toContain("rulesById.get(rid)")
  })

  it("acts at the policy level (group toggle + delete cascade)", () => {
    expect(src).toContain("togglePolicyGroupAction")
    expect(src).toContain("deletePolicyGroupAction")
  })

  it("hides per-policy toggle under the pack-centric runtime", () => {
    expect(src).toMatch(/!packCentric\s*&&[\s\S]{0,120}togglePolicyGroupAction/)
  })
})

describe("rules page wires PolicyList as the primary policies surface", () => {
  const page = read("../page.tsx")

  it("renders PolicyList (not the raw-rule PoliciesTab grid) on the policies tab", () => {
    expect(page).toContain("<PolicyList")
    expect(page).toContain("rulesById={new Map(policies.map(")
    // The raw-rule grid component is no longer rendered as the primary list.
    expect(page).not.toMatch(/<PoliciesTab\b/)
  })

  it("keeps the prebuilt template catalog behind ?templates=1", () => {
    expect(page).toMatch(/showTemplates\s*&&\s*prebuilt\.length\s*>\s*0/)
    expect(page).toContain("PrebuiltCard")
  })
})
