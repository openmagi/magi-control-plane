import Link from "next/link"
import { QuestionMarkCircleIcon } from "@heroicons/react/24/outline"
import { getLocale, getT } from "@/lib/i18n/server"
import { getWorkspaceData } from "../_data/workspace"

/**
 * Sticky page header inside the console main column. Mirrors
 * magi-agent's LocalRuntimeHeader: small uppercase "ROUTE" label +
 * page title + status pill. The status pill flips between Hosted /
 * Self-host / Offline based on getWorkspaceData().
 */
export async function RuntimeHeader() {
  const locale = getLocale()
  const isKo = locale === "ko"
  const { t } = await getT()
  const { tenant, healthOk } = await getWorkspaceData()
  const isSelfHost = !tenant || tenant.synthetic

  const route = isKo ? "콘솔" : "Console"
  // Persistent brand strip label. This is NOT the page <h1>: each console
  // page renders its own descriptive <h1> via PageHeader, so this stays a
  // plain <div> to avoid two h1s per route (WCAG heading order). Brand
  // string matches the document <title> in app/layout.tsx.
  const title = "Open Magi Control Plane"

  let pillClass: string
  let pillLabel: string
  let pillDotClass: string
  if (!healthOk) {
    pillClass = "border-amber-500/20 bg-amber-500/10 text-amber-700"
    pillDotClass = "bg-amber-500"
    pillLabel = isKo ? "응답 없음" : "Offline"
  } else if (isSelfHost) {
    // Self-host is the console's default posture, so it wears the accent
    // (verdigris) rather than the retired violet.
    pillClass = "border-[var(--color-accent)]/20 bg-[var(--color-accent)]/10 text-[var(--color-accent-light)]"
    pillDotClass = "bg-[var(--color-accent)]"
    pillLabel = isKo ? "자체 호스트" : "Self-host"
  } else {
    pillClass = "border-emerald-500/20 bg-emerald-500/10 text-emerald-700"
    pillDotClass = "bg-emerald-500"
    pillLabel = isKo ? "Pro+ 호스티드" : "Pro+ Hosted"
  }

  return (
    <div className="sticky top-0 z-20 border-b border-black/5 bg-white/85 backdrop-blur-xl">
      <div className="flex min-h-16 flex-col justify-center gap-1 px-4 py-3 sm:px-6 md:px-8">
        <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--color-text-tertiary)]">
          {route}
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <div className="text-base font-semibold text-[var(--color-text-primary)]">
            {title}
          </div>
          <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${pillClass}`}>
            <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${pillDotClass}`} />
            {pillLabel}
          </span>
          <Link
            href="/docs"
            prefetch={false}
            aria-label={t("nav.docs.help")}
            title={t("nav.docs.help")}
            className="ml-auto inline-flex h-7 w-7 items-center justify-center rounded-full text-[var(--color-text-tertiary)] hover:bg-white hover:text-[var(--color-text-primary)] transition-colors"
          >
            <QuestionMarkCircleIcon aria-hidden="true" className="h-4 w-4" />
          </Link>
        </div>
      </div>
    </div>
  )
}
