import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * D53b: source-level invariants for the DryRunPanel client component.
 *
 * Same pattern as VerifierSamplesList.test.ts - grep the rendered TSX
 * for the contract instead of mounting React Testing Library. The
 * browser behaviour (button enable/disable, fetch, toggle samples)
 * is exercised manually in dev; these invariants catch the refactors
 * a future maintainer is most likely to silently break.
 */
describe("DryRunPanel source invariants", () => {
  const src = readFileSync(
    path.join(__dirname, "DryRunPanel.tsx"),
    "utf-8",
  )

  it("declares 'use client' (the parent server tree mounts it)", () => {
    expect(src.startsWith('"use client"')).toBe(true)
  })

  it("hits the same-origin /api/policies/dry-run proxy, not the cloud directly", () => {
    // Direct cloud calls would leak the admin key to the browser.
    // The Next.js API route reads the key server-side.
    expect(src).toContain("/api/policies/dry-run")
    expect(src).not.toMatch(/process\.env\.MAGI_CP_ADMIN_API_KEY/)
  })

  it("button is gated on the parent-supplied `disabled` / `ir==null` flags", () => {
    // The brief: "Only enabled when the IR draft is valid (compile
    // passed)." The parent decides validity; the panel respects it
    // plus its own loading state.
    expect(src).toContain("disabled={disabled || loading || ir == null}")
  })

  it("renders the result headline using the i18n template with action/matched/total placeholders", () => {
    expect(src).toContain("newPolicy.dryRun.result")
    expect(src).toContain("matched: result.matched")
    expect(src).toContain("total: result.total_records")
    expect(src).toContain("action: t(actionLabelKey(action))")
  })

  it("renders the loading state", () => {
    // aria-busy is wired so SR users get the busy signal.
    expect(src).toContain("aria-busy={loading}")
    expect(src).toContain("dry-run-loading")
    expect(src).toContain("newPolicy.dryRun.loading")
  })

  it("renders the error state without blocking save (role=alert only on err)", () => {
    expect(src).toContain("dry-run-error")
    expect(src).toContain("newPolicy.dryRun.failed")
    expect(src).toMatch(/role="alert"/)
  })

  it("renders the empty / archetype-skipped states with friendly copy", () => {
    expect(src).toContain("dry-run-empty")
    expect(src).toContain("newPolicy.dryRun.empty")
    expect(src).toContain("dry-run-skipped-archetype")
    expect(src).toContain("newPolicy.dryRun.empty.archetype")
    expect(src).toContain("archetype-not-dry-runnable")
    expect(src).toContain("no-records-in-trigger-frame")
  })

  it("show-samples toggle is wired via aria-expanded + aria-controls", () => {
    expect(src).toContain("dry-run-samples-toggle")
    expect(src).toContain("aria-expanded={samplesOpen}")
    expect(src).toContain("aria-controls={samplesId}")
    expect(src).toContain("newPolicy.dryRun.showSamples")
    expect(src).toContain("newPolicy.dryRun.hideSamples")
  })

  it("controlled sample list stays mounted (hidden attribute, not unmount)", () => {
    // Mirror the VerifierSamplesList contract: keeping aria-controls
    // resolvable to a real DOM element even when collapsed avoids
    // axe-core "references an element that does not exist".
    expect(src).toMatch(/hidden=\{!samplesOpen\}/)
  })

  it("renders the by_verdict pill row, skipping zero-count buckets", () => {
    expect(src).toContain("dry-run-by-verdict")
    expect(src).toContain("dry-run-pill-")
    expect(src).toContain("if (n === 0) return null")
  })

  it("each sample row prints the redacted preview, not a raw body field", () => {
    // The cloud server-side redactor (D50) is the single source of
    // truth for what reaches the browser. The panel must render
    // `redacted_payload_preview` as-is - never a raw body field.
    expect(src).toContain("dry-run-sample-row")
    expect(src).toContain("redacted_payload_preview")
    expect(src).toContain("font-mono")
    // No accidental raw-body field references that could bypass the
    // redactor by reading the (non-existent) raw payload.
    expect(src).not.toMatch(/sample\.body[^a-z_]/)
    expect(src).not.toMatch(/sample\.payload[^a-z_]/)
  })

  it("action archetype drives the headline pill color via actionTone", () => {
    expect(src).toContain("actionTone(resolvedAction)")
    expect(src).toContain("case \"block\":")
    expect(src).toContain("case \"ask\":")
    expect(src).toContain("case \"audit\":")
  })

  it("does not block the save action - dry-run failure is best-effort", () => {
    // The brief: "Do not block save on dry-run failure." We assert
    // the panel never throws past its own try/catch boundary and
    // never short-circuits the parent's form via window.history etc.
    expect(src).toContain("try {")
    expect(src).toContain("} finally {")
    expect(src).not.toContain("window.history")
    // No event.preventDefault on a parent form path; the panel
    // owns its own button and a fetch failure stays inside the panel.
    expect(src).not.toMatch(/preventDefault\(\)/)
  })

  it("payload sent to the proxy carries ir + since (default 24h)", () => {
    expect(src).toContain('JSON.stringify({ ir, since: "24h" })')
  })
})
