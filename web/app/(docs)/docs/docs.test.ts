import { describe, it, expect } from "vitest"
import { existsSync, readFileSync } from "node:fs"
import path from "node:path"

/**
 * D78: source-level guarantees for the in-app docs site.
 *
 * Hand-rolled React pages (no MDX) so we assert page existence and a
 * couple of structural invariants from disk. The actual rendering is
 * exercised by `npm run build`.
 */
describe("D78 /docs/* surface", () => {
  const docsDir = path.join(__dirname)

  const PAGES: Array<{ slug: string; rel: string }> = [
    { slug: "index",           rel: "page.tsx" },
    { slug: "concepts",        rel: "concepts/page.tsx" },
    { slug: "first-policy",    rel: "first-policy/page.tsx" },
    { slug: "run-command",     rel: "run-command/page.tsx" },
    { slug: "inject-context",  rel: "inject-context/page.tsx" },
    { slug: "input-rewrite",   rel: "input-rewrite/page.tsx" },
    { slug: "conversational",  rel: "conversational/page.tsx" },
    { slug: "env-reference",   rel: "env-reference/page.tsx" },
    { slug: "troubleshooting", rel: "troubleshooting/page.tsx" },
    { slug: "upgrade",         rel: "upgrade/page.tsx" },
  ]

  it("ships exactly 10 page files (the D78 contract)", () => {
    for (const p of PAGES) {
      const abs = path.join(docsDir, p.rel)
      expect(existsSync(abs), `missing ${p.rel}`).toBe(true)
    }
    expect(PAGES.length).toBe(10)
  })

  it("each page renders inside the shared DocsLayout", () => {
    for (const p of PAGES) {
      const src = readFileSync(path.join(docsDir, p.rel), "utf-8")
      expect(src, `${p.rel} missing DocsLayout`).toMatch(/DocsLayout/)
      expect(src, `${p.rel} missing current= prop`).toMatch(/current=/)
    }
  })

  it("each page is statically rendered (no LLM, no cloud)", () => {
    for (const p of PAGES) {
      const src = readFileSync(path.join(docsDir, p.rel), "utf-8")
      expect(src, `${p.rel} should be force-static`).toMatch(
        /export const dynamic = "force-static"/,
      )
    }
  })

  it("DocsLayout exposes a 10-entry rail matching the page slugs", () => {
    const layoutSrc = readFileSync(
      path.join(docsDir, "_components/DocsLayout.tsx"), "utf-8",
    )
    for (const p of PAGES) {
      expect(layoutSrc, `DocsLayout missing slug ${p.slug}`).toContain(`"${p.slug}"`)
    }
  })

  it("sidebar nav references /docs (D78 entry point)", () => {
    const sidebarSrc = readFileSync(
      path.join(__dirname, "..", "..", "(console)", "_components", "Sidebar.tsx"),
      "utf-8",
    )
    expect(sidebarSrc).toMatch(/href="\/docs"/)
    expect(sidebarSrc).toMatch(/nav\.group\.help/)
  })

  /**
   * Review fix: CLAUDE.md "no em-dashes" is a hard rule and the repo
   * even ships humanize tooling specifically to strip them. A grep
   * gate at the docs surface fails loudly the next time one slips in.
   */
  it("no em-dashes anywhere under /docs/* (CLAUDE.md hard rule)", () => {
    const offenders: string[] = []
    for (const p of PAGES) {
      const src = readFileSync(path.join(docsDir, p.rel), "utf-8")
      if (src.includes("—")) offenders.push(p.rel)
    }
    // Also scan the shared components directory.
    const sharedFiles = ["_components/DocsLayout.tsx", "_components/CalloutAside.tsx"]
    for (const rel of sharedFiles) {
      const abs = path.join(docsDir, rel)
      if (!existsSync(abs)) continue
      const src = readFileSync(abs, "utf-8")
      if (src.includes("—")) offenders.push(rel)
    }
    expect(
      offenders,
      `em-dash (U+2014) found in: ${offenders.join(", ")}; use a comma, period, or colon`,
    ).toEqual([])
  })

  /**
   * Review fix: the dead `docs.nav.*` keys are now load-bearing. The
   * rail in DocsLayout and the card grid on /docs both translate via
   * those keys, so the key set must stay in lockstep with the slug
   * set; the dict file itself must contain a row for each slug. This
   * makes future translator edits visible immediately.
   */
  it("dict has a docs.nav.* key for every slug, and DocsLayout binds it", () => {
    const dictSrc = readFileSync(
      path.join(__dirname, "..", "..", "..", "lib", "i18n", "dict.ts"),
      "utf-8",
    )
    const slugToKey: Record<string, string> = {
      "index":           "docs.nav.index",
      "concepts":        "docs.nav.concepts",
      "first-policy":    "docs.nav.firstPolicy",
      "run-command":     "docs.nav.runCommand",
      "inject-context":  "docs.nav.injectContext",
      "input-rewrite":   "docs.nav.inputRewrite",
      "conversational":  "docs.nav.conversational",
      "env-reference":   "docs.nav.envReference",
      "troubleshooting": "docs.nav.troubleshooting",
      "upgrade":         "docs.nav.upgrade",
    }
    const layoutSrc = readFileSync(
      path.join(docsDir, "_components/DocsLayout.tsx"), "utf-8",
    )
    for (const [, key] of Object.entries(slugToKey)) {
      // Once in KO_RAW, once in EN. At least two occurrences.
      const occurrences = (dictSrc.match(new RegExp(`"${key.replace(/\./g, "\\.")}"`, "g")) ?? []).length
      expect(
        occurrences,
        `dict.ts is missing or under-covers ${key} (need both KO + EN entries; got ${occurrences})`,
      ).toBeGreaterThanOrEqual(2)
      // DocsLayout binds the same key into the rail.
      expect(layoutSrc, `DocsLayout missing labelKey ${key}`).toContain(`"${key}"`)
    }
  })
})
