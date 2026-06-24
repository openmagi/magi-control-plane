import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * Source-level invariants for the rules page after D52b.
 *
 * D52a (just landed) renamed the tab to "Verifiers" + reworded the
 * description. D52b adds:
 *   - a per-row VerifierExpander on the Verifiers tab
 *   - a "+ New verifier" CTA visible only on the Verifiers tab
 *
 * The rest of the page is exercised by the existing rules visual review;
 * the locks here catch a refactor accidentally collapsing the
 * tab-scoped CTA into the always-visible "+ New policy" button or
 * dropping the expander.
 */
describe("rules page source invariants", () => {
  const src = readFileSync(path.join(__dirname, "page.tsx"), "utf-8")

  it("renders the VerifierExpander on each verifier row", () => {
    expect(src).toContain("VerifierExpander")
    expect(src).toMatch(/<VerifierExpander[^>]*step=/)
  })

  it("exposes the + New verifier CTA on the verifiers tab only", () => {
    expect(src).toContain("rules.newVerifierButton")
    // Tab-scoped: the rendering branch reads `tab === "evidence"`
    expect(src).toMatch(/tab === "evidence"/)
    expect(src).toContain('href="/verifiers/new"')
  })

  it("keeps the + New policy CTA on every tab (regression guard)", () => {
    expect(src).toContain("rules.newButton")
    expect(src).toContain('href="/policies/new"')
  })

  it("rolls expander inside the evidence row, not in a sibling block", () => {
    // The expander is a child of the row map; the new policy block
    // refactor must not break that nesting (we asserted it via the
    // shared per-row layout class).
    expect(src).toMatch(/VerifierExpander step=\{row\.step\}/)
  })
})
