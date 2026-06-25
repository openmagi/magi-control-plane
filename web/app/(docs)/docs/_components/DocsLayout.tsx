import Link from "next/link"
import type { ReactNode } from "react"
import {
  HomeIcon, BookOpenIcon, AcademicCapIcon, CommandLineIcon,
  ArrowUpTrayIcon, PencilSquareIcon, ChatBubbleLeftRightIcon,
  WrenchScrewdriverIcon, ArchiveBoxIcon, AdjustmentsHorizontalIcon,
  ArrowLeftIcon,
} from "@heroicons/react/24/outline"
import { getT, getLocale } from "@/lib/i18n/server"
import type { TKey } from "@/lib/i18n/dict"

/**
 * D78: shared layout for every /docs/* page. Renders a left rail of
 * page links (the docs sidebar), breadcrumb header, and a content
 * column. Each page passes its own slug + a small title/subtitle so
 * the rail can highlight the current entry without us needing to read
 * the pathname in a server component.
 *
 * 10 entries match the 10 pages mandated by D78. The breadcrumbs are
 * inline (no heavy schema.org markup) since these pages are inside the
 * console shell and operators aren't sharing the URLs externally.
 *
 * Review fix: rail and breadcrumb labels now resolve through the
 * `docs.nav.*` keys in `web/lib/i18n/dict.ts`, so a translator editing
 * the dict actually changes what the user sees and the existing
 * EN-mirror gate catches drift.
 */

export type DocsSlug =
  | "index"
  | "concepts"
  | "first-policy"
  | "run-command"
  | "inject-context"
  | "input-rewrite"
  | "conversational"
  | "env-reference"
  | "troubleshooting"
  | "upgrade"

interface DocsNavEntry {
  slug: DocsSlug
  href: string
  /** Heroicon component reference. */
  icon: typeof HomeIcon
  /** i18n key for the rail label. Single source of truth for both the
   *  rail and the breadcrumb head. */
  labelKey: TKey
}

/** Single source of truth for the docs left rail order. */
export const DOCS_NAV: ReadonlyArray<DocsNavEntry> = [
  { slug: "index",          href: "/docs",                  icon: HomeIcon,                   labelKey: "docs.nav.index" },
  { slug: "concepts",       href: "/docs/concepts",         icon: AcademicCapIcon,            labelKey: "docs.nav.concepts" },
  { slug: "first-policy",   href: "/docs/first-policy",     icon: BookOpenIcon,               labelKey: "docs.nav.firstPolicy" },
  { slug: "run-command",    href: "/docs/run-command",      icon: CommandLineIcon,            labelKey: "docs.nav.runCommand" },
  { slug: "inject-context", href: "/docs/inject-context",   icon: ArrowUpTrayIcon,            labelKey: "docs.nav.injectContext" },
  { slug: "input-rewrite",  href: "/docs/input-rewrite",    icon: PencilSquareIcon,           labelKey: "docs.nav.inputRewrite" },
  { slug: "conversational", href: "/docs/conversational",   icon: ChatBubbleLeftRightIcon,    labelKey: "docs.nav.conversational" },
  { slug: "env-reference",  href: "/docs/env-reference",    icon: AdjustmentsHorizontalIcon,  labelKey: "docs.nav.envReference" },
  { slug: "troubleshooting",href: "/docs/troubleshooting",  icon: WrenchScrewdriverIcon,      labelKey: "docs.nav.troubleshooting" },
  { slug: "upgrade",        href: "/docs/upgrade",          icon: ArchiveBoxIcon,             labelKey: "docs.nav.upgrade" },
] as const

interface DocsLayoutProps {
  /** Which doc page is rendering this layout. */
  current: DocsSlug
  /** Page title shown as h1. */
  title: string
  /** Optional subtitle / lead paragraph. */
  subtitle?: ReactNode
  /** Body of the page. */
  children: ReactNode
}

/** Lookup helper used by both the rail and the breadcrumb head. */
export function navLabelKey(slug: DocsSlug): TKey {
  const entry = DOCS_NAV.find((e) => e.slug === slug)
  if (!entry) throw new Error(`unknown docs slug: ${slug}`)
  return entry.labelKey
}

export async function DocsLayout({
  current, title, subtitle, children,
}: DocsLayoutProps) {
  const { t } = await getT()
  const locale = getLocale()
  const isKo = locale === "ko"
  const consoleBackLabel = isKo ? "콘솔로 돌아가기" : "Back to console"
  const docsRailLabel = isKo ? "문서 메뉴" : "Docs navigation"
  const onThisPageLabel = isKo ? "이 문서" : "This page"

  return (
    <div className="grid gap-8 lg:grid-cols-[14rem_minmax(0,1fr)]">
      <aside aria-label={docsRailLabel} className="lg:sticky lg:top-20 lg:self-start">
        <nav className="rounded-xl border border-[var(--color-border-subtle)] bg-white/60 p-3">
          <div className="px-2 mb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--color-text-tertiary)]">
            {onThisPageLabel}
          </div>
          <ul role="list" className="flex flex-col gap-0.5">
            {DOCS_NAV.map((entry) => {
              const Icon = entry.icon
              const isCurrent = entry.slug === current
              return (
                <li key={entry.slug}>
                  <Link
                    href={entry.href}
                    prefetch={false}
                    aria-current={isCurrent ? "page" : undefined}
                    className={
                      "group flex min-h-9 items-center gap-2.5 rounded-lg px-2.5 text-[13px] font-medium transition-colors duration-150 " +
                      (isCurrent
                        ? "bg-[var(--color-accent)]/10 text-[var(--color-accent-light)]"
                        : "text-[var(--color-text-secondary)] hover:bg-white hover:text-[var(--color-text-primary)]")
                    }
                  >
                    <Icon aria-hidden="true" className="h-4 w-4 shrink-0" />
                    <span className="truncate">{t(entry.labelKey)}</span>
                  </Link>
                </li>
              )
            })}
          </ul>
          <div className="mt-3 border-t border-[var(--color-border-subtle)] pt-3">
            <Link
              href="/overview"
              prefetch={false}
              className="inline-flex items-center gap-1.5 px-2.5 text-xs font-medium text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)] hover:no-underline"
            >
              <ArrowLeftIcon aria-hidden="true" className="h-3.5 w-3.5" />
              {consoleBackLabel}
            </Link>
          </div>
        </nav>
      </aside>

      <div className="min-w-0">
        <nav aria-label="Breadcrumb" className="mb-4 text-xs text-[var(--color-text-tertiary)]">
          <Link href="/overview" prefetch={false} className="hover:underline">
            {isKo ? "콘솔" : "Console"}
          </Link>
          <span aria-hidden="true" className="mx-1.5">/</span>
          <Link href="/docs" prefetch={false} className="hover:underline">
            {isKo ? "문서" : "Docs"}
          </Link>
          {current !== "index" && (
            <>
              <span aria-hidden="true" className="mx-1.5">/</span>
              <span className="text-[var(--color-text-secondary)]">
                {t(navLabelKey(current))}
              </span>
            </>
          )}
        </nav>
        <header className="mb-6">
          <h1 className="m-0 text-2xl font-semibold text-[var(--color-text-primary)] text-balance">
            {title}
          </h1>
          {subtitle && (
            <p className="mt-2 text-sm text-[var(--color-text-tertiary)] max-w-3xl text-pretty">
              {subtitle}
            </p>
          )}
        </header>
        <div className="docs-prose">{children}</div>
      </div>
    </div>
  )
}
