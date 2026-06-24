import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * Source-level invariants for the VerifierSamplesList client component.
 *
 * Same pattern as VerifierExpander.test.ts: grep the rendered TSX for
 * the contract instead of spinning up React Testing Library. Browser
 * behaviour (expand toggle, fetch) is exercised manually in dev; the
 * invariants below are the ones a future refactor is most likely to
 * silently break.
 */
describe("VerifierSamplesList source invariants", () => {
  const src = readFileSync(
    path.join(__dirname, "VerifierSamplesList.tsx"),
    "utf-8",
  )

  it("declares 'use client' (the expander mounts it inside a server tree)", () => {
    expect(src.startsWith('"use client"')).toBe(true)
  })

  it("fetches on expander open, not on mount (no page-load tax)", () => {
    // The component must NOT call fetch from a top-level effect that
    // runs on mount. The `fetched.current` latch + `onToggle` handler
    // is the one allowed entry point.
    expect(src).toContain("fetched")
    expect(src).toContain("onToggle")
    // No `useEffect(... fetch(... )` on mount with an empty dep array.
    // (We DO use one for the relative-time refresh; that is allowed.)
    expect(src).not.toMatch(/useEffect\(\s*\(\)\s*=>\s*\{[^}]*fetch\(/m)
  })

  it("uses the same-origin /api/verifier-samples proxy", () => {
    // Direct cloud calls would leak the API key to the browser. The
    // proxy route reads the key server-side.
    expect(src).toContain("/api/verifier-samples")
    expect(src).toContain("encodeURIComponent(step)")
    expect(src).toContain("limit=5")
  })

  it("renders skeleton placeholders during loading", () => {
    expect(src).toContain("SamplesSkeleton")
    expect(src).toContain("Skeleton")
    expect(src).toContain("verifier-expander-samples-skeleton")
  })

  it("renders the empty state when the cloud returns zero samples", () => {
    expect(src).toContain("verifier-expander-samples-empty")
    expect(src).toContain("rules.verifier.samples.empty")
  })

  it("renders the error state on fetch failure", () => {
    expect(src).toContain("verifier-expander-samples-error")
    expect(src).toContain("rules.verifier.samples.error")
  })

  it("each row deep-links to /ledger?verifier=<step>&record=<id>", () => {
    // The chip selector + this jump-link share `ledgerHref` so the URL
    // encoding stays byte-identical.
    expect(src).toContain('from "@/lib/ledger-url"')
    expect(src).toMatch(/ledgerHref\(\{\s*verifiers:\s*\[step\]\s*\}\)/)
    expect(src).toContain("record=")
  })

  it("renders the verdict via the closed allowlist labels", () => {
    // Verdicts go through verdictLabel which maps the closed set to
    // i18n keys; mirror the verdict labels brief calls for.
    expect(src).toContain("rules.verifier.samples.verdict.pass")
    expect(src).toContain("rules.verifier.samples.verdict.fail")
    expect(src).toContain("rules.verifier.samples.verdict.needs_review")
    expect(src).toContain("rules.verifier.samples.verdict.not_applicable")
  })

  it("renders the redacted preview in a monospace clamped row", () => {
    expect(src).toContain("redacted_payload_preview")
    expect(src).toContain("font-mono")
    expect(src).toContain("truncate")
  })

  it("header surfaces the 24h count using the supplied initialCount", () => {
    expect(src).toContain("rules.verifier.samples.header")
    expect(src).toContain("initialCount")
  })

  it("aria-controls + aria-expanded wire the toggle into a11y tree", () => {
    expect(src).toContain("aria-expanded={open}")
    expect(src).toContain("aria-controls={listId}")
  })

  it("caches the first fetch result; collapsing then re-expanding does not re-fetch", () => {
    // The `fetched` ref is set to true on the first toggle and the
    // fetch call is guarded by `if (next && !fetched.current)`.
    expect(src).toMatch(/fetched\.current\s*=\s*true/)
    expect(src).toMatch(/if\s*\(\s*next\s*&&\s*!fetched\.current\s*\)/)
  })
})
