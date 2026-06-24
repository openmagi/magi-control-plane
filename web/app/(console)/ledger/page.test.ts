import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * D52c source-level invariants for the /ledger page.
 *
 * The page is a server component. The chip selector ships pure URL
 * state (no client JS, no useState) and the filter param round-trips
 * to the cloud's `/ledger?verifier=<step>` query. The guards below
 * catch the most likely refactor regressions:
 *   - chip Link forgetting to round-trip the filter
 *   - pagination Link dropping the filter (next-page would silently
 *     widen the view back to "all entries")
 *   - empty-state message regressing to "no entries" when filter is
 *     active (operators would think the ledger is broken)
 */
describe("ledger page source invariants (D52c)", () => {
  const src = readFileSync(path.join(__dirname, "page.tsx"), "utf-8")

  it("does NOT carry a 'use client' pragma (server component)", () => {
    expect(src.startsWith('"use client"')).toBe(false)
  })

  it("parses `?verifier=` as a multi-value URL state", () => {
    // Repeated query param → string[] from Next.js. The page must
    // accept both string and string[] shapes.
    expect(src).toMatch(/verifier\?:\s*string\s*\|\s*string\[\]/)
    expect(src).toContain("parseVerifierParam")
  })

  it("threads the verifier filter into cloud.ledger()", () => {
    // The third argument to cloud.ledger(sinceId, limit, verifier?:
    // string[]) MUST be wired or the cloud sees an unfiltered query
    // and the chip UI lies about its effect.
    expect(src).toMatch(/cloud\.ledger\(\s*since\s*,\s*LEDGER_PAGE_SIZE/)
    expect(src).toMatch(/verifierFilter\.length\s*>\s*0\s*\?\s*verifierFilter/)
  })

  it("renders a VerifierFilterChips card above the chain-integrity card", () => {
    expect(src).toContain("VerifierFilterChips")
    expect(src).toContain("verifier-filter-chips")
    expect(src).toContain("ledger.filter.title")
    expect(src).toContain("ledger.filter.hint")
  })

  it("chips drive aria-pressed (toggle state) for SR users", () => {
    // The chip is a Link, but it functions as a toggle. aria-pressed
    // is the only way an AT user can hear the on/off state without
    // re-reading the URL.
    expect(src).toMatch(/aria-pressed=\{isOn\}/)
  })

  it("toggle path: clicking an ON chip drops it from the URL", () => {
    expect(src).toMatch(/isOn\s*\?\s*selected\.filter\(\(s\)\s*=>\s*s\s*!==\s*row\.step\)/)
  })

  it("toggle path: clicking an OFF chip appends it to the URL", () => {
    expect(src).toMatch(/\[\.\.\.selected,\s*row\.step\]/)
  })

  it("clear-filter affordance only renders when something is selected", () => {
    expect(src).toMatch(/selected\.length\s*>\s*0/)
    expect(src).toContain("verifier-filter-clear")
    expect(src).toContain("ledger.filter.clear")
  })

  it("pagination Links preserve the verifier filter (next page)", () => {
    // The Next-page link MUST include `verifiers: verifierFilter` or
    // a navigation away from page 1 silently drops the filter.
    expect(src).toMatch(/ledgerHref\(\{\s*since:\s*result\.next_since_id,\s*verifiers:\s*verifierFilter,\s*\}\)/)
  })

  it("pagination Links preserve the verifier filter (first page)", () => {
    expect(src).toMatch(/ledgerHref\(\{\s*verifiers:\s*verifierFilter\s*\}\)/)
  })

  it("empty state distinguishes 'no entries' vs 'filter empty'", () => {
    // Two distinct i18n keys so the operator can tell whether the
    // chain is genuinely empty or whether their filter happens to
    // match nothing.
    expect(src).toContain("ledger.filter.empty")
    expect(src).toContain("ledger.empty")
    expect(src).toMatch(/verifierFilter\.length\s*>\s*0\s*\?\s*t\("ledger\.filter\.empty"\)/)
  })

  it("catalog fetch failure mutes chips but does not break the ledger view", () => {
    // listEvidenceTypes is wrapped in try/catch; an outage falls back
    // to an empty catalog (no chips), the ledger table still renders.
    expect(src).toMatch(/catalog\s*=\s*await\s+cloud\.listEvidenceTypes/)
    expect(src).toMatch(/catch[\s\S]{0,80}catalog\s*=\s*\[\]/)
  })
})
