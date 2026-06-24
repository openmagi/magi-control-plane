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
    // shared per-row layout class). D52c reformatted the call onto
    // multiple lines (props grew when we added recentEmissions24h);
    // assert via the `step={row.step}` substring instead of the
    // prior single-line shape.
    expect(src).toMatch(/<VerifierExpander[\s\S]*?step=\{row\.step\}/)
  })

  it("D52c: passes recent emissions count + nf formatter to the expander", () => {
    // Server component calls the batched /ledger/counts endpoint
    // (D52c follow-up) and threads the result through
    // `recentEmissions24h`. nfFormat localizes the rendered number on
    // the dashboard.
    expect(src).toMatch(/recentEmissions24h=/)
    expect(src).toMatch(/nfFormat=\{nfFormat\}/)
    expect(src).toContain("cloud.ledgerCounts")
  })

  it("D52c follow-up: batched, fall through to dash on unreachable count", () => {
    // The single batched call replaces K-per-row fan-out. A failure
    // of the batched call must leave `emissionCounts` empty so each
    // row falls through to the unavailable dash via the
    // hasOwnProperty branch (was: `return null` from inner
    // Promise.all swallow; now a single try/catch around the batch).
    // Assert the batched call landing + the dash branch staying.
    expect(src).toContain("cloud.ledgerCounts")
    expect(src).toMatch(/\? emissionCounts\[row\.step\]\s*:\s*null/)
  })
})
