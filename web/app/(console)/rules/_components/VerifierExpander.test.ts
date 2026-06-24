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

  /* ─── D52d: field_checks tree panel ───────────────────────────── */
  it("D52d: renders the per-field check tree panel", () => {
    expect(src).toContain("verifier-expander-field-checks")
    expect(src).toContain("rules.verifier.expander.fieldChecks")
  })

  it("D52d: shares the VerifierFieldChecks component with the wizard picker", () => {
    expect(src).toContain("VerifierFieldChecks")
    // The component lives in the console-wide _components dir so the
    // catalog expander + wizard picker both consume the same source.
    expect(src).toMatch(/from ".+_components\/VerifierFieldChecks"/)
  })

  /* ─── D53a: dynamic samples list inside the emissions panel ─── */
  it("D53a: imports and renders VerifierSamplesList", () => {
    // The samples list is mounted inside the RecentEmissionsPanel so
    // the operator sees the inline list under the count + ledger link.
    expect(src).toContain("VerifierSamplesList")
    expect(src).toMatch(/from "\.\/VerifierSamplesList"/)
    expect(src).toContain("<VerifierSamplesList")
  })

  it("D53a: skips the samples list for verifiers without runtime binding", () => {
    // custom + policy-derived(missing) rows never emit, so the list
    // would always be empty. The expander gates the render on the
    // same `noRuntimeBinding` predicate it uses for the no-runtime
    // note (single source of truth).
    expect(src).toContain("showSamples")
    expect(src).toContain("!noRuntimeBinding")
  })

  it("D53a: forwards the count into the samples list header", () => {
    // The header shows "N total" using the same server-rendered count
    // the existing count widget already has, so the empty-list case
    // never visually contradicts a stale count.
    expect(src).toMatch(/initialCount=\{count\}/)
  })

  /* ─── D57c: input_assembly panel + caller-assembled notice ──── */
  it("D57c: renders the input-assembly panel for every descriptor", () => {
    // The panel sits above the field_checks tree and reads off the
    // descriptor's input_assembly. Both branches (cc_stdin /
    // caller_assembled) render so the operator sees a positive
    // statement either way.
    expect(src).toContain("verifier-expander-input-assembly")
    expect(src).toContain("rules.verifier.expander.inputAssembly")
  })

  it("D57c: caller-assembled branch renders an amber notice with the hint", () => {
    // The caller_assembled branch uses an aria-marked notice block so
    // a screen-reader user does not miss the contract. The hint prose
    // renders inline; the fallback string surfaces when the descriptor
    // does not carry a hint (defensive — built-ins all carry one).
    expect(src).toContain("verifier-expander-input-assembly-caller-notice")
    expect(src).toMatch(/role=["']note["']/)
    expect(src).toContain("rules.verifier.expander.inputAssembly.callerAssembledBadge")
    expect(src).toContain("rules.verifier.expander.inputAssembly.callerAssembledLabel")
  })

  it("D57c: cc_stdin branch renders a one-line muted affirmation", () => {
    expect(src).toContain("verifier-expander-input-assembly-cc-stdin-note")
    expect(src).toContain("rules.verifier.expander.inputAssembly.ccStdinNote")
  })

  it("D57c: field_checks heading swaps for caller-assembled rows", () => {
    // The catalog distinguishes "CC stdin paths" (cc_stdin) from
    // "Verifier's input dict shape" (caller_assembled). Both i18n
    // keys must be referenced so the parity gate keeps them in sync.
    expect(src).toContain("rules.verifier.expander.fieldChecks")
    expect(src).toContain("rules.verifier.expander.fieldChecks.callerAssembled")
  })

  it("D57c: accepts overrides so custom rows surface authored assembly metadata", () => {
    // ChecksTab forwards the row-level (input_assembly,
    // caller_assembly_hint) pair onto custom catalog rows so the
    // expander renders the same notice it would for a built-in. Lock
    // the prop names so a future refactor does not silently drop them.
    expect(src).toContain("inputAssemblyOverride")
    expect(src).toContain("callerAssemblyHintOverride")
  })

  it("D57c: resolved input_assembly defaults to cc_stdin when neither side supplies a value", () => {
    // Falls back to cc_stdin when nothing is supplied — the pre-D57c
    // implicit assumption — instead of throwing or rendering "??".
    expect(src).toMatch(/inputAssembly:\s*InputAssembly\s*=[\s\S]*?"cc_stdin"/)
  })
})
