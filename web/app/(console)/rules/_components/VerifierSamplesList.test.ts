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

  it("controlled <ul> is rendered unconditionally so aria-controls always resolves", () => {
    // Earlier shape used `{open && <ul ...>}` which left aria-controls
    // pointing at a non-existent ID when collapsed (axe-core flags
    // 'aria-controls references an element that does not exist'). The
    // <ul> now uses the `hidden` attribute to flip visibility while
    // staying mounted.
    expect(src).not.toMatch(/\{open && <ul/)
    expect(src).toMatch(/hidden=\{!open\}/)
  })

  it("loading state is announced via aria-busy + aria-live + sr-only text", () => {
    // Skeleton placeholders are aria-hidden, so the perceivable
    // loading signal comes from aria-busy + aria-live polite + an
    // sr-only 'Loading samples...' line (WCAG 4.1.3 status messages).
    expect(src).toContain("aria-busy={open && loading}")
    expect(src).toContain('aria-live="polite"')
    expect(src).toContain("rules.verifier.samples.loading")
  })

  it("preview row drops the mouse-only title= attribute (a11y parity with VerifierExpander)", () => {
    // VerifierExpander.test.ts pinned a no-title contract for the
    // sibling inline-example surface; the sample row mirrors it so a
    // keyboard / SR user is not the only person who cannot read the
    // full preview.
    expect(src).not.toMatch(/title=\{sample\.redacted_payload_preview\}/)
  })

  it("renders a localized placeholder when the redacted preview is empty", () => {
    // Empty preview previously rendered the literal string "." which
    // SR announced as 'period' and sighted users saw as a stray dot.
    expect(src).toContain("rules.verifier.samples.previewUnavailable")
    expect(src).not.toMatch(/\|\|\s*"\."/m)
  })

  it("deep link surfaces the destination via title=href + visible label (no surprise navigation)", () => {
    // The brief calls for 'deep-link previews href on hover (no
    // surprise navigation)'. A bare arrow with only an aria-label
    // does not satisfy this for sighted / keyboard users.
    expect(src).toContain("title={href}")
    expect(src).toContain("deepLinkLabel")
  })

  it("relative-time tick is a single shared interval at the list level, not per-row", () => {
    // Per-row setInterval was the previous shape and grew the timer
    // count linearly with the sample count. The list hoists a single
    // 'now' state and passes it down as a prop.
    expect(src).toMatch(/setInterval\(\(\) => setNow\(/)
    // RelativeTime no longer owns its own interval.
    expect(src).not.toMatch(/function RelativeTime[\s\S]*setInterval/)
  })

  it("skeleton renders 5 rows so loaded state never expands the panel", () => {
    // Real responses return up to 5 (the client default + server cap
    // for this endpoint). Three placeholder rows caused the panel to
    // grow when more samples arrived.
    expect(src).toMatch(/\[0,\s*1,\s*2,\s*3,\s*4\]\.map/)
    expect(src).not.toMatch(/\[0,\s*1,\s*2\]\.map/)
  })

  it("caches the first fetch result; collapsing then re-expanding does not re-fetch", () => {
    // The `fetched` ref is set to true on the first toggle and the
    // fetch call is guarded by `if (next && !fetched.current)`.
    expect(src).toMatch(/fetched\.current\s*=\s*true/)
    expect(src).toMatch(/if\s*\(\s*next\s*&&\s*!fetched\.current\s*\)/)
  })
})
