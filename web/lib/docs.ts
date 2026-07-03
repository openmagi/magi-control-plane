import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * Developer docs index. The repo root `docs/` tree is the source of
 * truth; this module reads `<slug>.md` at request time on the server.
 * Pages are statically generated so reads happen at build time in
 * production.
 *
 * Adding a doc: drop a new markdown file into `<repo>/docs/`, append
 * the slug + title + summary to DOCS_INDEX. The renderer at
 * `web/app/(marketing)/docs/[slug]/page.tsx` picks it up automatically
 * because it iterates DOCS_INDEX in generateStaticParams.
 */

export type DocSlug =
  | "getting-started"
  | "install"
  | "architecture"
  | "policy-ir"
  | "verifiers"
  | "operator"
  | "api"
  | "cli"
  | "troubleshooting"
  | "share-runs"

export interface DocEntry {
  slug: DocSlug
  title: string
  summary: string
}

/** Single source of truth for the docs index + the renderer's
 *  generateStaticParams. The order here is the order the index page
 *  renders in. */
export const DOCS_INDEX: ReadonlyArray<DocEntry> = [
  {
    slug: "getting-started",
    title: "Getting started",
    summary: "Install, point Claude Code at the gate, see a deny verdict.",
  },
  {
    slug: "install",
    title: "Install",
    summary: "Full install guide, environment variables, and common failures.",
  },
  {
    slug: "architecture",
    title: "Architecture",
    summary: "Three-layer model: local, cloud, floor. Trust boundaries.",
  },
  {
    slug: "policy-ir",
    title: "Policy IR",
    summary: "The IR schema, sentinel regex, and 5-tier precedence.",
  },
  {
    slug: "verifiers",
    title: "Verifiers",
    summary: "The 5 wired verifiers and how to register a custom one.",
  },
  {
    slug: "operator",
    title: "Operator",
    summary: "Deploy on Docker or K8s. Key rotation, observability, backups.",
  },
  {
    slug: "api",
    title: "API",
    summary: "Cloud REST reference for policies, verify, HITL, ledger, and admin.",
  },
  {
    slug: "cli",
    title: "CLI",
    summary: "magi-cp commands, exit codes, and environment variables.",
  },
  {
    slug: "troubleshooting",
    title: "Troubleshooting",
    summary: "Common errors and resolutions across install, gate, cloud, dashboard.",
  },
  {
    slug: "share-runs",
    title: "Share runs",
    summary: "magi-cp share and the openmagi.runView.v1 contract.",
  },
] as const

const VALID_SLUGS: ReadonlySet<string> = new Set(DOCS_INDEX.map((d) => d.slug))

/**
 * The repo root `docs/` dir. The dashboard ships as
 * `<repo>/web` (Next.js standalone), so the docs tree is one level up.
 */
function docsDir(): string {
  return path.resolve(process.cwd(), "..", "docs")
}

export function isDocSlug(slug: string): slug is DocSlug {
  return VALID_SLUGS.has(slug)
}

export function getDocEntry(slug: DocSlug): DocEntry {
  const entry = DOCS_INDEX.find((d) => d.slug === slug)
  if (!entry) throw new Error(`unknown doc slug: ${slug}`)
  return entry
}

/**
 * Read the raw markdown for a slug. Throws if the file is missing so
 * the build fails loudly during `next build` rather than rendering a
 * blank page in production.
 */
export function readDocMarkdown(slug: DocSlug): string {
  const file = path.join(docsDir(), `${slug}.md`)
  return readFileSync(file, "utf-8")
}
