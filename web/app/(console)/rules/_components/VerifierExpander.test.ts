import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * Source-level invariants for the VerifierExpander server component.
 *
 * Matches the SteeringAwareField.test.ts pattern (grep the rendered TSX
 * for the contract rather than spinning up React Testing Library).
 * Browser-side toggle behavior is exercised manually in dev; the
 * invariants below are the ones a future refactor is most likely to
 * silently break.
 */
describe("VerifierExpander source invariants", () => {
  const src = readFileSync(
    path.join(__dirname, "VerifierExpander.tsx"),
    "utf-8",
  )

  it("does NOT carry a 'use client' pragma (server component, no JS)", () => {
    expect(src.startsWith('"use client"')).toBe(false)
  })

  it("uses native <details>/<summary> for keyboard a11y + CSS animation", () => {
    expect(src).toMatch(/<details/)
    expect(src).toMatch(/<summary/)
  })

  it("renders the four required panels", () => {
    // Triggers / Input / Verdicts / Evidence
    expect(src).toContain("verifier-expander-triggers")
    expect(src).toContain("verifier-expander-input")
    expect(src).toContain("verifier-expander-verdicts")
    expect(src).toContain("verifier-expander-evidence")
  })

  it("renders the no-descriptor fallback for unknown steps", () => {
    expect(src).toContain("rules.verifier.expander.noDescriptor")
  })

  it("looks up payload-schema field descriptions as secondary fallback", () => {
    // Primary lookup is the descriptor's own input_fields (verifier
    // input_schema). availableFields() stays available as a secondary
    // fallback for paths that happen to overlap CC stdin fields.
    expect(src).toContain("availableFields(")
  })

  it("renders example payload inline (not as mouse-only title attr)", () => {
    // P1 a11y: the prior `title={titleParts.join("\\n\\n")}` was mouse-
    // only and invisible to keyboard / SR users. Examples now render
    // in a sibling <p> reachable by focus order.
    expect(src).toContain("rules.verifier.expander.inputExample")
    expect(src).not.toMatch(/\btitle=\{titleParts/)
  })

  it("summary surfaces the verifier step name (not just generic 'Details')", () => {
    // Distinct accessible name per row so a SR user scanning the list
    // hears "citation_verify details" instead of five "details"s.
    expect(src).toContain("rules.verifier.expander.toggleWithStep")
  })

  it("verdict + matcher chips use theme-aware CSS variable tokens", () => {
    // P2 a11y / dark-mode hardening: bg-emerald-50 / bg-rose-50 etc
    // burn in for the light theme. The new tokens degrade through a
    // var(--color-*) fallback.
    expect(src).toMatch(/var\(--color-pass-bg/)
    expect(src).toMatch(/var\(--color-deny-bg/)
    expect(src).not.toMatch(/bg-emerald-50/)
    expect(src).not.toMatch(/bg-rose-50/)
  })

  it("animates the chevron via CSS group-open transform (no JS lib)", () => {
    expect(src).toMatch(/group-open:rotate/)
    // No external animation lib
    expect(src).not.toMatch(/framer-motion|react-spring/)
  })

  it("verdict chips render via a closed allowlist tone helper", () => {
    expect(src).toMatch(/verdictTone\(/)
  })

  it("evidence shape table renders path / type / description columns", () => {
    expect(src).toContain("rules.verifier.expander.evidence.path")
    expect(src).toContain("rules.verifier.expander.evidence.type")
    expect(src).toContain("rules.verifier.expander.evidence.description")
  })

  /* ─── D52c: recent emissions widget ──────────────────────────── */
  it("D52c: renders the recent emissions panel for every verifier", () => {
    // Same data-testid pattern as the other panels for parity.
    expect(src).toContain("verifier-expander-recent-emissions")
    expect(src).toContain("rules.verifier.expander.recentEmissions")
    expect(src).toContain("rules.verifier.expander.recentEmissionsWindow")
  })

  it("D52c: recent-emissions panel renders for unknown-descriptor steps too", () => {
    // Operators of a derived / policy-only step still need the jump-
    // to-ledger affordance. The panel must live OUTSIDE the
    // `descriptor === null` early-return branch.
    // We assert this structurally: the closing JSX paren of the
    // ternary (`)}`) is followed by the recent-emissions panel
    // call (not by `</div>`, which would put it inside).
    expect(src).toContain("verifier-expander-recent-emissions")
    // Find the ternary closing and confirm <RecentEmissionsPanel comes
    // before the wrapping div closes. A precise structural check
    // (without React DOM) is brittle; pattern below requires only
    // that the panel appears between the `)}` end-of-ternary and the
    // outer `</details>` close.
    const ternaryEnd = src.indexOf(")}")
    const ternaryNext = src.indexOf(")}", ternaryEnd + 1)
    const panelIdx = src.indexOf("<RecentEmissionsPanel")
    const detailsClose = src.indexOf("</details>")
    expect(panelIdx).toBeGreaterThan(0)
    expect(detailsClose).toBeGreaterThan(panelIdx)
    // panel must come AFTER one of the ternary-end markers, not before.
    expect(panelIdx).toBeGreaterThan(Math.min(ternaryEnd, ternaryNext))
  })

  it("D52c: View-in-ledger link routed through shared ledgerHref", () => {
    // The hosted ledger filter contract is `?verifier=<step>`; the
    // chip selector reads the same key. D52c follow-up routes both
    // sides through `web/lib/ledger-url.ts::ledgerHref` so the URL
    // encoding is byte-identical (was: hand-rolled
    // encodeURIComponent here, URLSearchParams there → `%20` vs `+`
    // divergence on space).
    expect(src).toMatch(/ledgerHref\(\{\s*verifiers:\s*\[step\]\s*\}\)/)
    expect(src).toContain('from "@/lib/ledger-url"')
    expect(src).toContain("rules.verifier.expander.viewInLedger")
    expect(src).toContain("verifier-expander-ledger-link")
  })

  it("D52c: null count renders dash, number renders nf-formatted", () => {
    // `recentEmissions24h === null` → render the "unavailable" string
    // (a transient cloud outage must not look like "no emissions").
    // Otherwise the number is formatted through the optional nfFormat
    // so locale-aware separators apply.
    expect(src).toContain("rules.verifier.expander.recentEmissionsUnavailable")
    expect(src).toMatch(/nfFormat\s*\?\s*nfFormat\(count\)/)
  })
})
