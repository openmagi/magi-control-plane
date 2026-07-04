import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/** D2 (audit CV-03): the policy detail page offers an Edit affordance that
 *  re-opens the rule's IR in the Advanced editor. */
const src = readFileSync(
  path.join(__dirname, "[...id]/page.tsx"), "utf-8",
)

describe("policy detail page edit link", () => {
  it("renders an Edit link seeding the Advanced editor with the IR", () => {
    expect(src).toContain('data-testid="policy-edit-link"')
    expect(src).toContain("mode=advanced&draft=")
    expect(src).toContain("encodeURIComponent(JSON.stringify(detail.policy))")
  })
})

describe("CV-10: policy detail delete", () => {
  it("renders a Delete form wired to deletePolicyAction", () => {
    expect(src).toContain('data-testid="policy-delete-form"')
    expect(src).toContain("deletePolicyAction")
  })
})
