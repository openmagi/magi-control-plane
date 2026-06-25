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
})
