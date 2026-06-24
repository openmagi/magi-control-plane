import Link from "next/link"
import { Badge, Code, EmptyState, ErrorState } from "@/components/ui"
import type { CheckEntry } from "@/lib/cloud"
import { VerifierExpander } from "./VerifierExpander"

/**
 * D56e: merged Checks list — built-in verifiers, custom verifiers,
 * and policy-extracted inline checks. The Rules page reorganized into
 * three tabs (Policies / Checks / Evidence); this is the middle one.
 *
 * Row layout follows the magi-agent customize row convention: list
 * with a per-row "kind" badge, source attribution, and a per-row
 * expander for the built-in / custom kinds (which carry a descriptor
 * tree). Inline rows render a compact code-block body since their
 * "checks" are the body itself, no descriptor exists.
 */

type T = (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string

const KIND_BADGE_TONE: Record<CheckEntry["kind"], string> = {
  "builtin":            "bg-[var(--color-accent)]/10 text-[var(--color-accent-light)]",
  "custom":             "bg-[var(--color-muted-bg,#f3f4f6)] text-[var(--color-muted-fg,#374151)]",
  "inline-regex":       "bg-amber-50 text-amber-800",
  "inline-llm-critic":  "bg-violet-50 text-violet-800",
  "inline-shacl":       "bg-emerald-50 text-emerald-800",
}

function kindLabel(kind: CheckEntry["kind"], t: T): string {
  if (kind === "builtin") return t("rules.checks.kind.builtin")
  if (kind === "custom") return t("rules.checks.kind.custom")
  if (kind === "inline-regex") return t("rules.checks.kind.inlineRegex")
  if (kind === "inline-llm-critic") return t("rules.checks.kind.inlineLlmCritic")
  return t("rules.checks.kind.inlineShacl")
}

export function ChecksTab({
  items,
  err,
  nfFormat,
  t,
  locale,
  emissionCounts,
}: {
  items: CheckEntry[]
  err: string | null
  nfFormat: (n: number) => string
  t: T
  locale: import("@/lib/i18n/dict").Locale
  /** Recent emissions in the last 24h, keyed by check id. Present only
   * for built-in / custom kinds — inline kinds emit under a generic
   * step name (`inline_<kind>`) handled on the Evidence tab. */
  emissionCounts: Record<string, number>
}) {
  const builtinN = items.filter((r) => r.kind === "builtin").length
  const customN = items.filter((r) => r.kind === "custom").length
  const inlineN = items.length - builtinN - customN

  return (
    <section>
      <p className="text-xs text-[var(--color-text-tertiary)] mb-3">
        {t("rules.tab.checks.hint")}
      </p>
      {err && (
        <ErrorState
          title={t("common.cloudUnreachable")}
          body={t("common.seeServerLogs")}
        />
      )}
      {!err && items.length === 0 && (
        <EmptyState title={t("rules.empty.checks")} />
      )}
      {!err && items.length > 0 && (
        <>
          <Badge variant="info" className="mb-3">
            {t("rules.summary.checks", {
              total: nfFormat(items.length),
              builtin: nfFormat(builtinN),
              custom: nfFormat(customN),
              inline: nfFormat(inlineN),
            })}
          </Badge>
          <div className="rounded-2xl border border-black/[0.06] bg-white overflow-hidden">
            {items.map((row, idx) => (
              <div
                key={row.id}
                className={`px-4 py-3.5 ${idx > 0 ? "border-t border-black/[0.05]" : ""}`}
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline gap-2">
                      <Code className="text-sm truncate max-w-full">{row.name}</Code>
                      <span
                        className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${KIND_BADGE_TONE[row.kind]}`}
                      >
                        {kindLabel(row.kind, t)}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-[var(--color-text-secondary)] leading-relaxed">
                      {row.description}
                    </p>
                    <div className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">
                      {t("rules.checks.source")}:{" "}
                      {row.kind === "builtin" || row.kind === "custom" ? (
                        <Code>{row.source}</Code>
                      ) : (
                        <Link
                          href={`/policies/${encodeURI(row.source)}`}
                          className="font-mono text-[var(--color-accent-light)] hover:underline"
                        >
                          {row.source}
                        </Link>
                      )}
                    </div>
                    {row.used_by_policies.length > 0 && (row.kind === "builtin" || row.kind === "custom") && (
                      <div className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">
                        {t("rules.checks.usedBy")}:{" "}
                        {row.used_by_policies.map((pid, i) => (
                          <span key={pid}>
                            {i > 0 && ", "}
                            <Link
                              href={`/policies/${encodeURI(pid)}`}
                              className="font-mono text-[var(--color-accent-light)] hover:underline"
                            >
                              {pid}
                            </Link>
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>

                {row.kind === "builtin" || row.kind === "custom" ? (
                  <VerifierExpander
                    step={row.id}
                    t={t}
                    locale={locale}
                    recentEmissions24h={
                      Object.prototype.hasOwnProperty.call(emissionCounts, row.id)
                        ? emissionCounts[row.id]
                        : null
                    }
                    nfFormat={nfFormat}
                    source={row.kind === "builtin" ? "builtin" : "custom"}
                    enforcement={row.kind === "builtin" ? "enforcing" : "preview"}
                    fieldChecksOverride={row.kind === "custom" ? row.field_checks : undefined}
                  />
                ) : (
                  <InlineBodyPanel body={row.body} t={t} />
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  )
}

function InlineBodyPanel({ body, t }: { body: string | null; t: T }) {
  if (!body) return null
  return (
    <details className="group mt-2 rounded-lg border border-black/[0.05] bg-[var(--color-surface-1,#f9fafb)]/40">
      <summary className="flex cursor-pointer items-center justify-between gap-2 rounded-lg px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)] hover:bg-black/[0.02]">
        <span>{t("rules.checks.inline.body")}</span>
        <span aria-hidden className="inline-block transition-transform duration-150 group-open:rotate-180">▾</span>
      </summary>
      <div className="px-3 pb-3 pt-1">
        <pre className="overflow-x-auto rounded bg-black/[0.03] p-2 text-[11px] leading-relaxed">
          <code className="font-mono">{body}</code>
        </pre>
      </div>
    </details>
  )
}
