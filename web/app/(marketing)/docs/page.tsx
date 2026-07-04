import Link from "next/link"
import { DOCS_INDEX } from "@/lib/docs"
import { getLocale } from "@/lib/i18n/server"

/**
 * Q96: /docs index. Lists the developer docs that live under
 * `<repo>/docs/*.md`. Statically generated; no LLM, no cloud.
 *
 * The index is bilingual on copy but the doc bodies are English-only
 * (developer audience). Korean operator-facing copy lives at /install
 * and /welcome on the marketing surface.
 */
export const dynamic = "force-static"

export const metadata = {
  title: "Docs - magi-control-plane",
  description:
    "Developer docs for the magi-control-plane governance gate. Install, architecture, Policy IR, verifiers, operator runbook, REST API, and CLI reference.",
}

export default async function DocsIndexPage() {
  const isKo = (await getLocale()) === "ko"

  return (
    <div className="mx-auto max-w-5xl px-4 py-12 sm:px-6 md:py-16">
      <header className="mb-10 max-w-3xl">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[var(--color-accent)]">
          {isKo ? "개발자 문서" : "Developer docs"}
        </p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight text-[var(--color-text-primary)] sm:text-4xl">
          {isKo ? "magi-control-plane" : "magi-control-plane"}
        </h1>
        <p className="mt-3 text-[15px] leading-7 text-[var(--color-text-secondary)]">
          {isKo
            ? "Claude Code 위에서 도는 오픈소스 거버넌스 게이트. 정책 IR, verifier 레지스트리, Ed25519 서명 evidence ledger. 설치부터 운영까지 전 과정."
            : "Open-source governance gate over Claude Code. Policy IR, verifier registry, Ed25519-signed evidence ledger. Everything from install to operate."}
        </p>
        <div className="mt-6 flex flex-wrap gap-3 text-sm">
          <Link
            href="/docs/getting-started"
            className="inline-flex items-center rounded-md bg-[var(--color-accent)] px-4 py-2 font-medium text-white hover:bg-[var(--color-accent-hover)] hover:no-underline"
          >
            {isKo ? "시작하기" : "Get started"}
          </Link>
          <a
            href="https://github.com/openmagi/magi-control-plane"
            className="inline-flex items-center rounded-md border border-[var(--color-border-strong)] px-4 py-2 font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-surface-overlay)] hover:no-underline"
          >
            GitHub
          </a>
        </div>
      </header>

      <ul role="list" className="grid gap-4 sm:grid-cols-2">
        {DOCS_INDEX.map((doc) => (
          <li key={doc.slug}>
            <Link
              href={`/docs/${doc.slug}`}
              prefetch={false}
              className="block h-full rounded-xl border border-[var(--color-border-subtle)] bg-white p-5 transition-colors hover:border-[var(--color-accent)]/40 hover:no-underline"
            >
              <div className="text-base font-semibold text-[var(--color-text-primary)]">
                {doc.title}
              </div>
              <p className="mt-1.5 text-sm leading-6 text-[var(--color-text-tertiary)]">
                {doc.summary}
              </p>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}
