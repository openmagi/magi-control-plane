import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * D56e: Source-level invariants for the rules page after the
 * three-tab reorganization (Policies / Checks / Evidence records).
 *
 * The Verifiers + Conditions tabs collapsed into a single Checks tab.
 * The new evidence record-types catalog lives under the dedicated
 * `tab=evidence-types` URL parameter (NOT `tab=evidence`, which used
 * to render the Verifiers tab pre-D56e and would silently land on a
 * different page after the rename). Legacy `tab=conditions`,
 * `tab=verifiers`, and `tab=evidence` URLs redirect for bookmark grace.
 */
describe("rules page source invariants (D56e)", () => {
  const src = readFileSync(path.join(__dirname, "page.tsx"), "utf-8")
  const checksSrc = readFileSync(
    path.join(__dirname, "_components/ChecksTab.tsx"), "utf-8",
  )
  const evidenceSrc = readFileSync(
    path.join(__dirname, "_components/EvidenceTab.tsx"), "utf-8",
  )

  // ── Tab structure ─────────────────────────────────────────────
  it("declares the three-tab structure: policies, checks, evidence-types", () => {
    expect(src).toMatch(
      /Tab\s*=\s*"policies"\s*\|\s*"checks"\s*\|\s*"evidence-types"/,
    )
    expect(src).toContain('"policies"')
    expect(src).toContain('"checks"')
    expect(src).toContain('"evidence-types"')
  })

  it("renders Checks + Evidence tab components and threads page state", () => {
    expect(src).toContain("ChecksTab")
    expect(src).toContain("EvidenceTab")
    expect(src).toContain("tab === \"checks\"")
    expect(src).toContain("tab === \"evidence-types\"")
  })

  it("redirects legacy ?tab=conditions and ?tab=verifiers to ?tab=checks", () => {
    expect(src).toContain('searchParams.tab === "conditions"')
    expect(src).toContain('searchParams.tab === "verifiers"')
    expect(src).toContain('redirect(`/rules?')
    expect(src).toContain('tab", "checks"')
  })

  it("redirects legacy ?tab=evidence to a sensible successor", () => {
    // Pre-D56e the `evidence` slug rendered the Verifiers tab; without
    // an explicit redirect it would silently land on the unrelated new
    // evidence-records page. msg=verifier_created (the prior verifier
    // success URL) routes to ?tab=checks; every other evidence URL
    // routes to ?tab=evidence-types.
    expect(src).toContain('searchParams.tab === "evidence"')
    expect(src).toContain('"verifier_created"')
    expect(src).toContain('"evidence-types"')
  })

  // ── Data plumbing ─────────────────────────────────────────────
  it("calls the new /checks and /evidence-types client wrappers", () => {
    expect(src).toContain("cloud.listChecks")
    expect(src).toContain("cloud.listEvidenceRecordTypes")
  })

  it("batches ledger counts in a single round-trip per tab", () => {
    // One ledgerCounts() call per tab so the cloud sees one GROUP BY
    // query regardless of how many rows the catalog returns.
    expect(src).toContain("cloud.ledgerCounts")
  })

  // ── Checks tab ────────────────────────────────────────────────
  it("ChecksTab reuses VerifierExpander for built-in and custom rows", () => {
    expect(checksSrc).toContain("VerifierExpander")
    // Inline rows render a lighter body-preview panel, not the full
    // descriptor expander.
    expect(checksSrc).toContain("InlineBodyPanel")
  })

  it("ChecksTab differentiates inline source via a /policies/ deep link", () => {
    expect(checksSrc).toContain('href={`/policies/${encodeURI(row.source)}`}')
  })

  // ── Evidence tab ──────────────────────────────────────────────
  it("EvidenceTab renders a payload schema table and ledger deep link", () => {
    expect(evidenceSrc).toContain("payload_schema")
    expect(evidenceSrc).toContain("ledgerHref")
    expect(evidenceSrc).toContain("rules.evidenceRecords.viewInLedger")
  })

  it("EvidenceTab surfaces recent emissions count with dash fallback", () => {
    expect(evidenceSrc).toContain("emissionCounts")
    expect(evidenceSrc).toContain("rules.evidenceRecords.recentEmissions")
  })

  // ── Regressions on the still-shipping bits ────────────────────
  it("PoliciesTab still renders the prebuilt section above user policies", () => {
    expect(src).toContain("cloud.listPrebuiltPolicies")
    expect(src).toContain("PrebuiltSection")
    const idxPrebuilt = src.indexOf("<PrebuiltSection")
    const idxPolicyList = src.indexOf("rules.summary.policies")
    expect(idxPrebuilt).toBeGreaterThan(-1)
    expect(idxPolicyList).toBeGreaterThan(-1)
    expect(idxPrebuilt).toBeLessThan(idxPolicyList)
  })

  it("prebuiltDraftHref still double-encodes and lands on wizard step 6", () => {
    expect(src).toContain("prebuiltDraftHref")
    expect(src).toMatch(/encodeURIComponent\(encodeURIComponent\(/)
    expect(src).toContain("mode=guided")
    expect(src).toContain("step=6")
  })

  // ── D60: prebuilt = toggle list ───────────────────────────────
  it("D60: PrebuiltSection renders the PrebuiltToggle on each card", () => {
    expect(src).toContain("PrebuiltToggle")
    expect(src).toContain("togglePrebuiltAction")
    // The toggle is wired with the enabled / setupRequired / setupHint
    // fields the cloud now returns. Without these the toggle would
    // render in the wrong state or skip the inline callout.
    expect(src).toContain("enabled={p.enabled}")
    expect(src).toContain("setupRequired={p.setup_required}")
    expect(src).toContain("setupHint={p.setup_hint}")
  })

  it("D60: the wizard handoff is kept as a SECONDARY 'Edit before enabling' Link", () => {
    // The brief explicitly keeps the Edit-before-enabling shortcut as
    // a secondary affordance so an operator who wants to tweak the IR
    // still has the wizard path. It must not block the primary toggle.
    expect(src).toContain("rules.prebuilt.editBefore")
    expect(src).toContain("prebuiltDraftHref(p)")
  })

  it("D60: cards visually mark the enabled state with an 'Active' pill", () => {
    // Operator scans the section and sees at a glance which prebuilts
    // are on; the pill mirrors the toggle's checked state.
    expect(src).toContain("rules.prebuilt.active")
    expect(src).toMatch(/p\.enabled/)
  })

  it("D60 follow-up: user-policies grid drops prebuilt rows to avoid double render", () => {
    // GET /policies returns every row including the materialized
    // prebuilt rows (POST /policies/prebuilt/{id}/enable saves into
    // the same store under `prebuilt/...` ids). Without filtering
    // the user-policies grid renders TWICE per enabled prebuilt
    // (once with the PrebuiltToggle, once with the PolicyToggle),
    // and the two toggles diverge on enable state.
    expect(src).toMatch(/\.filter\(.*p\.id\.startsWith\(['"]prebuilt\/['"]\)/)
    expect(src).toContain("userPolicies")
  })

  it("D60 follow-up: PrebuiltSection renders a persistent 'Needs setup' chip", () => {
    // The chip lets an operator scanning the grid see the
    // prerequisite without clicking the toggle first. The big
    // callout still appears on click; the chip is the discovery
    // affordance.
    expect(src).toContain("rules.prebuilt.needsSetup")
    expect(src).toMatch(/p\.setup_required/)
  })

  it("D60 follow-up: page no longer references the retired useThis.aria key", () => {
    // The 'Use this' wizard handoff was retired in D60. The
    // secondary link is now the 'Edit before enabling' Link and
    // uses its own aria-label key.
    expect(src).not.toContain("rules.prebuilt.useThis")
    expect(src).toContain("rules.prebuilt.editBeforeAria")
  })

  it("keeps the + New policy CTA on every tab", () => {
    expect(src).toContain("rules.newButton")
    expect(src).toContain('href="/policies/new"')
  })

  it("exposes the + New verifier CTA on the checks tab only", () => {
    expect(src).toContain("rules.newVerifierButton")
    expect(src).toMatch(/tab === "checks"/)
    expect(src).toContain('href="/verifiers/new"')
  })

  // ── D67: lock prebuilt-row dedup with regression tests ────────
  it("D67: items.filter(prebuilt/) precedes the user-policies .map call site", () => {
    // Regression for the D60 follow-up: the filter must run BEFORE
    // the .map that renders the user-policies grid, otherwise the
    // grid silently re-renders every materialized prebuilt row.
    const filterMatch = src.match(
      /\.filter\(\s*\(\s*p\s*\)\s*=>\s*!\s*p\.id\.startsWith\(['"]prebuilt\/['"]\)\s*\)/,
    )
    expect(filterMatch).not.toBeNull()
    const filterIdx = filterMatch ? src.indexOf(filterMatch[0]) : -1
    expect(filterIdx).toBeGreaterThan(-1)
    // The user-policies grid maps over `userPolicies`, the variable
    // the filter assigns to. Any rendered `userPolicies.map` must
    // appear AFTER the filter call site.
    const mapIdx = src.indexOf("userPolicies.map(")
    expect(mapIdx).toBeGreaterThan(-1)
    expect(filterIdx).toBeLessThan(mapIdx)
  })

  it("D67: count badge uses the filtered userPolicies length, not raw items", () => {
    // Without this the summary badge claims a higher policy count
    // than the grid actually renders, and the operator sees a stale
    // number for every enabled prebuilt.
    expect(src).toMatch(
      /rules\.summary\.policies[^}]*nfFormat\(userPolicies\.length\)/,
    )
    // Empty-state branch must also key off userPolicies.length so
    // a tenant with only prebuilt rows still sees the empty state.
    expect(src).toMatch(/userPolicies\.length\s*===\s*0/)
  })

  it("D67: a prebuilt/ id never renders inside the user-policies grid", () => {
    // Concrete-id regression: if someone reintroduces an unfiltered
    // items.map in the PoliciesTab grid the source no longer
    // guarantees the filter — assert that the only place a literal
    // `prebuilt/` id token can appear in the PoliciesTab grid path
    // is via the filter predicate itself.
    //
    // We grep the rendered <Card key={item.id}> body and confirm
    // it iterates `userPolicies`, never the raw `items` collection.
    const cardKeyMatch = src.match(
      /\{userPolicies\.map\(\(item\)\s*=>\s*\(\s*<Card key=\{item\.id\}/,
    )
    expect(cardKeyMatch).not.toBeNull()
    // Negative form: there must NOT be an `items.map((item)` that
    // renders a `<Card key={item.id}>` directly (which would bypass
    // the filter and double-render an enabled prebuilt row).
    expect(src).not.toMatch(
      /\{items\.map\(\(item\)\s*=>\s*\(\s*<Card key=\{item\.id\}/,
    )
  })
})
