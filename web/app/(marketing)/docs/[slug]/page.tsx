import Link from "next/link"
import { notFound } from "next/navigation"
import {
  DOCS_INDEX,
  getDocEntry,
  isDocSlug,
  readDocMarkdown,
} from "@/lib/docs"
import { getLocale } from "@/lib/i18n/server"
import { DocsMarkdown } from "../_components/DocsMarkdown"

/**
 * Q96: render docs/{slug}.md. Slugs come from `DOCS_INDEX`; unknown
 * slugs 404. The file read happens at build time because the page
 * is statically generated.
 */
export const dynamic = "force-static"

export async function generateStaticParams() {
  return DOCS_INDEX.map((doc) => ({ slug: doc.slug }))
}

export async function generateMetadata({
  params,
}: {
  params: { slug: string }
}) {
  if (!isDocSlug(params.slug)) return { title: "Not found" }
  const entry = getDocEntry(params.slug)
  return {
    title: `${entry.title} - magi-control-plane docs`,
    description: entry.summary,
  }
}

export default async function DocsSlugPage({
  params,
}: {
  params: { slug: string }
}) {
  if (!isDocSlug(params.slug)) notFound()
  const entry = getDocEntry(params.slug)
  const source = readDocMarkdown(params.slug)
  const isKo = (await getLocale()) === "ko"

  return (
    <div className="mx-auto max-w-6xl px-4 py-10 sm:px-6 md:py-12">
      <div className="grid gap-10 lg:grid-cols-[14rem_minmax(0,1fr)]">
        <aside
          aria-label={isKo ? "문서 메뉴" : "Docs navigation"}
          className="lg:sticky lg:top-20 lg:self-start"
        >
          <nav className="rounded-xl border border-[var(--color-border-subtle)] bg-white p-3">
            <div className="px-2 mb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--color-text-tertiary)]">
              {isKo ? "이 문서" : "On this site"}
            </div>
            <ul role="list" className="flex flex-col gap-0.5">
              <li>
                <Link
                  href="/docs"
                  prefetch={false}
                  className="block min-h-9 rounded-lg px-2.5 py-1.5 text-[13px] font-medium text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-overlay)] hover:text-[var(--color-text-primary)] hover:no-underline"
                >
                  {isKo ? "문서 홈" : "Docs home"}
                </Link>
              </li>
              {DOCS_INDEX.map((doc) => {
                const isCurrent = doc.slug === entry.slug
                return (
                  <li key={doc.slug}>
                    <Link
                      href={`/docs/${doc.slug}`}
                      prefetch={false}
                      aria-current={isCurrent ? "page" : undefined}
                      className={
                        "block min-h-9 rounded-lg px-2.5 py-1.5 text-[13px] font-medium transition-colors duration-150 hover:no-underline " +
                        (isCurrent
                          ? "bg-[var(--color-surface-overlay)] text-[var(--color-accent-light)]"
                          : "text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-overlay)] hover:text-[var(--color-text-primary)]")
                      }
                    >
                      {doc.title}
                    </Link>
                  </li>
                )
              })}
            </ul>
          </nav>
        </aside>

        <article className="min-w-0">
          <nav
            aria-label="Breadcrumb"
            className="mb-4 text-xs text-[var(--color-text-tertiary)]"
          >
            <Link href="/docs" prefetch={false} className="hover:underline">
              {isKo ? "문서" : "Docs"}
            </Link>
            <span aria-hidden="true" className="mx-1.5">
              /
            </span>
            <span className="text-[var(--color-text-secondary)]">
              {entry.title}
            </span>
          </nav>
          <DocsMarkdown source={source} />
        </article>
      </div>
    </div>
  )
}
