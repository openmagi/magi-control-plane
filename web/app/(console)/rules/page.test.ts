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

  it("D54: verifier card no longer renders an EnforcementBadge inside the evidence row", () => {
    // The pill conflated verifier (function) with policy
    // (composition). The component itself is still imported for the
    // Policies tab card, but the EvidenceTab (`row.enforcement` is
    // the row variable name there) must not mount it.
    expect(src).not.toMatch(/<EnforcementBadge[^>]*kind=\{row\.enforcement\}/)
  })

  it("D54: PoliciesTab accepts and renders a Prebuilt section above the user's policies", () => {
    // The dashboard fetches `listPrebuiltPolicies()` on the Policies
    // tab and passes the array down to PoliciesTab as `prebuilt`.
    // The PrebuiltSection component renders the catalog of 5 (cloud
    // returns them) above the operator's own policies.
    expect(src).toContain("cloud.listPrebuiltPolicies")
    expect(src).toContain("PrebuiltSection")
    expect(src).toMatch(/prebuilt=\{prebuilt\}/)
    // The "verifier = function, policy = composition" framing leans
    // on PrebuiltSection landing ABOVE the operator's policies. A
    // refactor that flipped the order would silently subvert that
    // framing without breaking other assertions, so pin the offset.
    const idxPrebuilt = src.indexOf("<PrebuiltSection")
    const idxPolicyList = src.indexOf("rules.summary.policies")
    expect(idxPrebuilt).toBeGreaterThan(-1)
    expect(idxPolicyList).toBeGreaterThan(-1)
    expect(idxPrebuilt).toBeLessThan(idxPolicyList)
  })

  it("D56a: prebuiltDraftHref encodes draft twice and lands on guided wizard step 6", () => {
    // D56a rerouted prebuilt "Use this" off the raw IR editor (was
    // mode=advanced) onto the guided wizard's Step 6 (review). The
    // wizard's WizardState parser reads `draft` the same way the
    // PolicyBuilder did, so the double-encode contract still holds:
    // Next.js searchParams decodes once on the way in, the wizard
    // decodes again before JSON.parse, both layers must survive.
    expect(src).toContain("prebuiltDraftHref")
    expect(src).toMatch(/encodeURIComponent\(encodeURIComponent\(/)
    expect(src).toContain("mode=guided")
    expect(src).toContain("step=6")
    expect(src).toContain("&draft=")
  })

  it("D54: PrebuiltSection renders a Use this Link to /policies/new", () => {
    // The "Use this" button is a Link that hands the operator the
    // raw editor prefilled with the prebuilt IR. We pin the link
    // target shape so a refactor of the new-policy URL surface
    // surfaces in CI instead of silently breaking the handoff.
    expect(src).toContain("rules.prebuilt.useThis")
    // Anchor on the function name rather than the rendered URL
    // string, since the URL is built by `prebuiltDraftHref`.
    expect(src).toMatch(/href=\{prebuiltDraftHref\(/)
  })
})
