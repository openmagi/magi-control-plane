import { describe, it, expect } from "vitest"
import { existsSync, readFileSync } from "node:fs"
import path from "node:path"
import { DOCS_INDEX, isDocSlug, getDocEntry } from "./docs"

/**
 * Q96 docs surface.
 *
 * The repo root `docs/*.md` files are the source of truth for the
 * /docs renderer. DOCS_INDEX must stay in lockstep with the on-disk
 * tree so generateStaticParams in the renderer matches what next build
 * reads.
 */
describe("Q96 developer docs", () => {
  const docsDir = path.resolve(__dirname, "..", "..", "docs")

  it("DOCS_INDEX matches the on-disk docs set", () => {
    expect(DOCS_INDEX.length).toBe(12)
    const expected = [
      "getting-started",
      "install",
      "architecture",
      "runtimes",
      "policy-ir",
      "verifiers",
      "session-evidence",
      "operator",
      "api",
      "cli",
      "troubleshooting",
      "share-runs",
    ]
    expect(DOCS_INDEX.map((d) => d.slug)).toEqual(expected)
  })

  it("every DOCS_INDEX slug has a matching markdown file on disk", () => {
    for (const doc of DOCS_INDEX) {
      const file = path.join(docsDir, `${doc.slug}.md`)
      expect(existsSync(file), `missing ${doc.slug}.md`).toBe(true)
    }
  })

  it("every doc file starts with an h1", () => {
    for (const doc of DOCS_INDEX) {
      const src = readFileSync(path.join(docsDir, `${doc.slug}.md`), "utf-8")
      expect(src.trimStart().startsWith("# "), `${doc.slug} missing h1`).toBe(true)
    }
  })

  it("no em-dashes anywhere in the docs tree (CLAUDE.md hard rule)", () => {
    const offenders: string[] = []
    for (const doc of DOCS_INDEX) {
      const src = readFileSync(path.join(docsDir, `${doc.slug}.md`), "utf-8")
      if (src.includes("—")) offenders.push(`${doc.slug}.md`)
    }
    expect(
      offenders,
      `em-dash (U+2014) found in: ${offenders.join(", ")}; use a comma, period, or colon`,
    ).toEqual([])
  })

  it("isDocSlug accepts known slugs and rejects unknown ones", () => {
    expect(isDocSlug("install")).toBe(true)
    expect(isDocSlug("verifiers")).toBe(true)
    expect(isDocSlug("not-a-real-doc")).toBe(false)
    expect(isDocSlug("../etc/passwd")).toBe(false)
  })

  it("getDocEntry returns the matching entry", () => {
    const e = getDocEntry("install")
    expect(e.title).toBe("Install")
    expect(e.summary.length).toBeGreaterThan(0)
  })

  it("no internal-only planning files were retained under docs/", () => {
    // Q96 dropped the legacy internal-only files that used to sit
    // alongside the developer docs. The list below stays banned.
    //
    // `docs/plans/` is now banned too (see the dedicated assertion
    // below): planning / design artifacts live outside this public
    // repo, in the private clawy monorepo under docs/plans/. Only the
    // rendered developer docs (DOCS_INDEX) belong under docs/.
    const banned = [
      "plans",
      "workflows",
      "clawy-integration.md",
      "design-partner-onepager.md",
      "run-share-deploy-checklist.md",
      "2026-06-19-magi-agent-customize-delta.md",
      "2026-06-19-v1-build-plan.md",
      "quickstart.md",
    ]
    for (const name of banned) {
      expect(existsSync(path.join(docsDir, name)), `internal-only path remains: ${name}`).toBe(false)
    }
  })

  it("docs/plans/ never returns: plan docs live outside this public repo", () => {
    // House rule: no plan / design docs in the public repo. They live
    // in the private clawy monorepo (docs/plans/). This guard fails
    // loudly if a plans directory is ever reintroduced here.
    expect(
      existsSync(path.join(docsDir, "plans")),
      "docs/plans/ must not exist; keep planning docs in the private clawy repo",
    ).toBe(false)
  })

  it("the renderer is allowlist-based: no DOCS_INDEX slug escapes into a subdirectory", () => {
    // The protective intent behind the ban above is "internal plans
    // must never render on the public /docs page." That intent is
    // enforced here structurally: every rendered slug must be a
    // flat top-level docs/<slug>.md. A slug containing a path
    // separator (which is how a plans/ file would sneak in) fails.
    for (const doc of DOCS_INDEX) {
      expect(doc.slug.includes("/"), `slug escapes to a subdir: ${doc.slug}`).toBe(false)
      expect(doc.slug.includes(".."), `slug traverses: ${doc.slug}`).toBe(false)
    }
  })
})
