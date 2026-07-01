import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * D82a (replaces D56e for the rules-page invariants): the tab nav got a
 * fourth entry — Packs — and PackSection moved out of the Policies tab
 * into a dedicated PacksTab. PoliciesTab + PrebuiltSection moved into
 * their own component file (PoliciesTab.tsx) and prebuilts now render
 * as ROWS (PrebuiltRow.tsx), not a card grid.
 *
 * The Verifiers + Conditions tabs collapsed into a single Checks tab
 * (D56e). The new evidence record-types catalog still lives under
 * `tab=evidence-types`. Legacy `tab=conditions`, `tab=verifiers`, and
 * `tab=evidence` URLs continue to redirect for bookmark grace.
 */
describe("rules page source invariants (D82a)", () => {
  const src = readFileSync(path.join(__dirname, "page.tsx"), "utf-8")
  const checksSrc = readFileSync(
    path.join(__dirname, "_components/ChecksTab.tsx"), "utf-8",
  )
  const evidenceSrc = readFileSync(
    path.join(__dirname, "_components/EvidenceTab.tsx"), "utf-8",
  )
  const policiesTabSrc = readFileSync(
    path.join(__dirname, "_components/PoliciesTab.tsx"), "utf-8",
  )
  const packsTabSrc = readFileSync(
    path.join(__dirname, "_components/PacksTab.tsx"), "utf-8",
  )
  const prebuiltRowSrc = readFileSync(
    path.join(__dirname, "_components/PrebuiltRow.tsx"), "utf-8",
  )

  // ── Tab structure ─────────────────────────────────────────────
  it("declares the four-tab structure: policies, packs, checks, evidence-types", () => {
    expect(src).toMatch(
      /Tab\s*=\s*"policies"\s*\|\s*"packs"\s*\|\s*"checks"\s*\|\s*"evidence-types"/,
    )
    expect(src).toContain('"policies"')
    expect(src).toContain('"packs"')
    expect(src).toContain('"checks"')
    expect(src).toContain('"evidence-types"')
  })

  it("nav lists four entries with packs route param", () => {
    // The TABS array drives the SubTabNav render; pin it includes
    // packs so a future refactor that drops the literal from the
    // array fails loudly.
    expect(src).toMatch(/TABS:\s*readonly Tab\[\]\s*=\s*\[\s*"policies"\s*,\s*"packs"\s*,\s*"checks"\s*,\s*"evidence-types"\s*\]/)
    // SubTabNav label switch: packs maps to the new label key.
    expect(src).toContain('"rules.tab.packs"')
  })

  it("renders Checks + Evidence + Policies + Packs tab components", () => {
    expect(src).toContain("ChecksTab")
    expect(src).toContain("EvidenceTab")
    expect(src).toContain("PoliciesTab")
    expect(src).toContain("PacksTab")
    expect(src).toContain("tab === \"policies\"")
    expect(src).toContain("tab === \"packs\"")
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
    expect(src).toContain('searchParams.tab === "evidence"')
    expect(src).toContain('"verifier_created"')
    expect(src).toContain('"evidence-types"')
  })

  // ── D82a tab content boundaries ───────────────────────────────
  it("PacksTab mounts PackSection", () => {
    expect(packsTabSrc).toContain("PackSection")
    expect(packsTabSrc).toContain('import { PackSection } from "./PackSection"')
    expect(packsTabSrc).toContain('"rules.tab.packs.hint"')
  })

  it("PoliciesTab does NOT mount PackSection", () => {
    // PackSection's home moved entirely to PacksTab (D82a). Any
    // re-introduction of the PackSection import or render on the
    // Policies tab brings back the duplicate-bundle UX confusion.
    expect(policiesTabSrc).not.toContain('from "./PackSection"')
    expect(policiesTabSrc).not.toMatch(/<\s*PackSection\b/)
    // The page-level fetch only runs listPacks on the packs tab.
    expect(src).toMatch(/tab === "packs"[\s\S]{0,400}cloud\.listPacks/)
  })

  it("rules/page.tsx fetches packs only on the packs tab", () => {
    // The Policies tab should not pay the listPacks round-trip
    // anymore. Source-grep pins that listPacks lives behind the
    // tab === "packs" branch.
    const policiesBranch = src.indexOf('if (tab === "policies")')
    const packsBranch = src.indexOf('else if (tab === "packs")')
    expect(policiesBranch).toBeGreaterThan(-1)
    expect(packsBranch).toBeGreaterThan(policiesBranch)
    // Find the chunk between the policies branch and packs branch.
    const policiesChunk = src.slice(policiesBranch, packsBranch)
    expect(policiesChunk).not.toContain("listPacks")
  })

  // ── Data plumbing ─────────────────────────────────────────────
  it("calls the new /checks and /evidence-types client wrappers", () => {
    expect(src).toContain("cloud.listChecks")
    expect(src).toContain("cloud.listEvidenceRecordTypes")
  })

  it("batches ledger counts in a single round-trip per tab", () => {
    expect(src).toContain("cloud.ledgerCounts")
  })

  // ── Checks tab ────────────────────────────────────────────────
  it("ChecksTab reuses VerifierExpander for built-in and custom rows", () => {
    expect(checksSrc).toContain("VerifierExpander")
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
    expect(policiesTabSrc).toContain("PrebuiltSection")
    const idxPrebuilt = policiesTabSrc.indexOf("<PrebuiltSection")
    const idxPolicyList = policiesTabSrc.indexOf("rules.summary.policies")
    expect(idxPrebuilt).toBeGreaterThan(-1)
    expect(idxPolicyList).toBeGreaterThan(-1)
    expect(idxPrebuilt).toBeLessThan(idxPolicyList)
  })

  it("prebuiltDraftHref still double-encodes and lands on wizard step 6", () => {
    expect(policiesTabSrc).toContain("prebuiltDraftHref")
    expect(policiesTabSrc).toMatch(/encodeURIComponent\(encodeURIComponent\(/)
    expect(policiesTabSrc).toContain("mode=guided")
    expect(policiesTabSrc).toContain("step=6")
  })

  // ── D82a: prebuilt = rows, not card grid ──────────────────────
  it("PrebuiltSection renders rows (PrebuiltRow), not a card grid", () => {
    // The pre-D82a section emitted `<Card key=...>` per entry inside
    // a `grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3` wrapper.
    // The new layout is a `<ul>` of `PrebuiltRow` items.
    expect(policiesTabSrc).toContain("PrebuiltRow")
    expect(policiesTabSrc).toMatch(/items\.map\(\s*\(\s*p\s*\)\s*=>\s*\(\s*<li\s/)
    expect(policiesTabSrc).not.toMatch(/grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-3[\s\S]{0,200}items\.map/)
  })

  it("PrebuiltRow is a client component with an expander", () => {
    expect(prebuiltRowSrc.startsWith('"use client"')).toBe(true)
  })

  it("D82d/e: outer row is not a role=button and has no D82a-era expander state", () => {
    // D82a wrapped the row in chevron-expander buttons (identity +
    // caret). D82d dropped that. D82e re-introduces a kebab menu
    // (which legitimately uses aria-expanded on the kebab button) so
    // the pin is scoped to what D82a specifically emitted:
    // outer role=button + setExpanded state + a caret-toggle
    // aria-controls id. The kebab's aria-expanded / aria-haspopup
    // are the CORRECT WAI-ARIA disclosure pattern for a menu button.
    expect(prebuiltRowSrc).not.toMatch(/role="button"/)
    expect(prebuiltRowSrc).not.toContain("setExpanded")
    expect(prebuiltRowSrc).not.toContain("expandLabelKey")
    // D82e sanity: the aria-expanded that IS present belongs to the
    // kebab menu, not a row-level expander.
    expect(prebuiltRowSrc).toMatch(/aria-haspopup="menu"/)
  })

  it("PrebuiltRow no longer needs stopPropagation (toggle/link are siblings, not descendants)", () => {
    // D82a follow-up: since the outer <div> is no longer role=button,
    // the click on PrebuiltToggle / Edit link does not bubble into a
    // row-level click handler. The stopPropagation guards are gone
    // and the layout passes WAI-ARIA's "no interactive descendants in
    // button" rule by construction.
    expect(prebuiltRowSrc).not.toContain("stopPropagation")
  })

  it("D82d: PrebuiltRow renders the summary inline, not behind an expander", () => {
    // D82a animated the description behind a caret expander. D82d
    // dropped the expander entirely (the caret button looked like an
    // empty box on the right of every row, screenshot review). The
    // summary now renders inline as quieter tertiary copy, always
    // visible. Pin the absence of the old animation wrapper so a
    // future refactor that re-introduces it trips loudly.
    expect(prebuiltRowSrc).not.toMatch(/transition-\[grid-template-rows\]/)
    expect(prebuiltRowSrc).not.toMatch(/gridTemplateRows/)
    expect(prebuiltRowSrc).not.toMatch(/\{expanded\s*\?/)
  })

  it("PrebuiltRow renders a status pill right after the name", () => {
    expect(prebuiltRowSrc).toContain("PrebuiltStatusPill")
    expect(prebuiltRowSrc).toContain("rules.prebuilt.row.statusActive")
    expect(prebuiltRowSrc).toContain("rules.prebuilt.row.statusNeedsSetup")
    expect(prebuiltRowSrc).toContain("rules.prebuilt.row.statusOff")
  })

  it("PrebuiltRow still renders the 'Edit before enabling' secondary link", () => {
    // D82e moved the link into the kebab menu; the i18n key still
    // ships. `editBeforeAria` on the outer row is gone because the
    // control now lives inside a role=menuitem inside the kebab.
    expect(prebuiltRowSrc).toContain("rules.prebuilt.editBefore")
  })

  // ── D60 / D67 invariants preserved on the new component file ──
  it("D60: PrebuiltRow renders the PrebuiltToggle wired with cloud action", () => {
    expect(prebuiltRowSrc).toContain("PrebuiltToggle")
    expect(prebuiltRowSrc).toContain("togglePrebuiltAction")
    expect(prebuiltRowSrc).toContain("enabled={entry.enabled}")
  })

  // ── D82d/e invariants ──
  it("D82d/e: PrebuiltRow has no D82a caret expander / collapsible summary grid", () => {
    // The D82a empty-button caret + collapsible grid was the UX
    // complaint. D82d dropped it and D82e keeps it dropped. Pin the
    // absence of the D82a-era hooks; the aria-expanded that CAN
    // appear now belongs to the kebab menu button (WAI-ARIA menu
    // disclosure pattern), which is legitimate.
    expect(prebuiltRowSrc).not.toContain("collapseAria")
    expect(prebuiltRowSrc).not.toContain("grid-template-rows")
    expect(prebuiltRowSrc).not.toContain("setExpanded")
  })

  it("D82d: PrebuiltRow renders a Setup button only when entry.setup_required", () => {
    expect(prebuiltRowSrc).toMatch(/entry\.setup_required\s*\?/)
    expect(prebuiltRowSrc).toContain("rules.prebuilt.setup")
    expect(prebuiltRowSrc).toContain("setupDocsHref")
  })

  it("D67: user-policies grid filters prebuilt rows before mapping", () => {
    expect(policiesTabSrc).toMatch(
      /\.filter\(\s*\(\s*p\s*\)\s*=>\s*!\s*p\.id\.startsWith\(['"]prebuilt\/['"]\)\s*\)/,
    )
    expect(policiesTabSrc).toContain("userPolicies.map(")
    expect(policiesTabSrc).toContain("nfFormat(userPolicies.length)")
    expect(policiesTabSrc).toMatch(/userPolicies\.length\s*===\s*0/)
  })

  it("D67: a literal items.map<Card> is NOT present in PoliciesTab", () => {
    expect(policiesTabSrc).not.toMatch(
      /\bitems\.map\(\s*\(\s*\w+\s*\)\s*=>\s*\(?\s*<Card key=\{/,
    )
  })

  it("D60 follow-up: page no longer references the retired useThis.aria key", () => {
    expect(policiesTabSrc).not.toContain("rules.prebuilt.useThis")
    expect(prebuiltRowSrc).not.toContain("rules.prebuilt.useThis")
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
})
