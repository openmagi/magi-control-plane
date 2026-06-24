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
