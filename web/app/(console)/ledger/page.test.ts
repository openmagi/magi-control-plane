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
    // accept both string and string[] shapes. D52c follow-up moved
    // the parser + URL builder to `@/lib/ledger-url` so the
    // VerifierExpander's View-in-ledger link reuses the same
    // contract (was: hand-rolled `encodeURIComponent` divergence).
    expect(src).toMatch(/verifier\?:\s*string\s*\|\s*string\[\]/)
    expect(src).toContain("parseVerifierParam")
    expect(src).toContain('from "@/lib/ledger-url"')
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

  it("D52c follow-up: chip toggle preserves the `since` cursor", () => {
    // Was: chip toggle silently dropped `since`, bouncing the user
    // back to page 1 every time they added or removed a filter.
    // Now: the toggle href threads `since` through unchanged so the
    // user stays on the page they were reading.
    expect(src).toMatch(
      /ledgerHref\(\{\s*since:\s*since\s*>\s*0\s*\?\s*since\s*:\s*undefined/,
    )
  })

  it("D52c follow-up: chip selector drops empty-step rows", () => {
    // A catalog row with step="" used to render a visually-empty
    // clickable chip + `?verifier=` dead link + a React key
    // collision risk. The selector now filters them out.
    expect(src).toMatch(/row\.step\s*&&\s*row\.step\.length\s*>\s*0/)
  })

  it("D52c follow-up: per-source visual treatment", () => {
    // builtin / custom / policy-derived must look distinct at a
    // glance so an operator can tell whether a 0-count chip is "no
    // emissions" (builtin) or "no runtime binding" (custom /
    // missing). Reuses the EnforcementBadge palette.
    expect(src).toContain("chipClasses")
    expect(src).toContain("data-source={row.source}")
    expect(src).toContain("data-enforcement={row.enforcement}")
  })

  it("D52c follow-up: deep-linked filter has an escape even when catalog fails", () => {
    // Was: catalog fetch failure + active `?verifier=` filter left
    // the user with no chips, no Clear-link, no visible cue. Now we
    // render a degraded card with the active-badge + Clear-link.
    expect(src).toContain("VerifierFilterDegradedCard")
    expect(src).toContain("verifier-filter-chips-degraded")
    expect(src).toContain("ledger.filter.catalogUnavailable")
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
    //
    // D72 split the branch: the filter-empty branch keeps the
    // existing `ledger.filter.empty` key, but the no-entries branch
    // now renders a richer EmptyState (title + body + CTA) using the
    // `ledger.empty.title` / `ledger.empty.body` / `ledger.empty.cta`
    // namespace. The bare `ledger.empty` key was retired from this
    // page; the test now asserts the new namespace + the
    // filter-vs-no-data split.
    expect(src).toContain("ledger.filter.empty")
    expect(src).toContain("ledger.empty.title")
    expect(src).toContain("ledger.empty.body")
    expect(src).toContain("ledger.empty.cta")
    expect(src).toMatch(/verifierFilter\.length\s*>\s*0\s*\?/)
    expect(src).toMatch(/t\("ledger\.filter\.empty"\)/)
  })

  it("catalog fetch failure mutes chips but does not break the ledger view", () => {
    // listEvidenceTypes is wrapped in try/catch; an outage falls back
    // to an empty catalog (no chips), the ledger table still renders.
    expect(src).toMatch(/catalog\s*=\s*await\s+cloud\.listEvidenceTypes/)
    expect(src).toMatch(/catch[\s\S]{0,80}catalog\s*=\s*\[\]/)
  })
})
