import Link from "next/link"
import { Badge, Card, Code, EmptyState, ErrorState } from "@/components/ui"
import { ledgerHref } from "@/lib/ledger-url"
import type { EvidenceRecordType } from "@/lib/cloud"

/**
 * D56e: Evidence record-types catalog. One row per kind of ledger
 * record the system can emit (built-in verifier payload shapes,
 * inline-kind generic shapes, custom verifier preview rows).
 *
 * Each card surfaces:
 *   - id + origin badge,
 *   - payload schema as a (path, type, description) table,
 *   - possible verdicts as chips,
 *   - "Recent emissions (last 24h)" count with a "View in ledger →"
 *     deep-link filtering /ledger by the record's step id.
 *
 * /ledger is unchanged and already filters by step name, so the
 * deep-link is built off the public ledgerHref helper.
 */

type T = (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string

const ORIGIN_BADGE_TONE: Record<EvidenceRecordType["origin"], string> = {
  "builtin": "bg-[var(--color-accent)]/10 text-[var(--color-accent-light)]",
  "custom":  "bg-[var(--color-muted-bg,#f3f4f6)] text-[var(--color-muted-fg,#374151)]",
  "inline":  "bg-amber-50 text-amber-800",
}

function originLabel(origin: EvidenceRecordType["origin"], t: T): string {
  if (origin === "builtin") return t("rules.evidenceRecords.origin.builtin")
  if (origin === "custom") return t("rules.evidenceRecords.origin.custom")
  return t("rules.evidenceRecords.origin.inline")
}

export function EvidenceTab({
  items,
  err,
  nfFormat,
  t,
  emissionCounts,
}: {
  items: EvidenceRecordType[]
  err: string | null
  nfFormat: (n: number) => string
  t: T
  /** Recent-24h emission counts keyed by record id (step name in the
   * ledger). Missing key → render dash, distinguishing "cloud
   * unreachable" from "cloud answered, no emissions". */
  emissionCounts: Record<string, number>
}) {
  const builtinN = items.filter((r) => r.origin === "builtin").length
  const customN = items.filter((r) => r.origin === "custom").length
  const inlineN = items.length - builtinN - customN

  return (
    <section>
      <p className="text-xs text-[var(--color-text-tertiary)] mb-3">
        {t("rules.tab.evidenceRecords.hint")}
      </p>
      {err && (
        <ErrorState
          title={t("common.cloudUnreachable")}
          body={t("common.seeServerLogs")}
        />
      )}
      {!err && items.length === 0 && (
        <EmptyState title={t("rules.empty.evidenceRecords")} />
      )}
      {!err && items.length > 0 && (
        <>
          <Badge variant="info" className="mb-3">
            {t("rules.summary.evidenceRecords", {
              total: nfFormat(items.length),
              builtin: nfFormat(builtinN),
              custom: nfFormat(customN),
              inline: nfFormat(inlineN),
            })}
          </Badge>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {items.map((row) => {
              const has = Object.prototype.hasOwnProperty.call(emissionCounts, row.id)
              const count = has ? emissionCounts[row.id] : null
              return (
                <Card key={row.id} className="flex flex-col gap-3">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-baseline gap-2">
                        <Code className="text-sm">{row.id}</Code>
                        <span
                          className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${ORIGIN_BADGE_TONE[row.origin]}`}
                        >
                          {originLabel(row.origin, t)}
                        </span>
                        {row.preview && (
                          <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-yellow-50 text-yellow-800">
                            {t("rules.evidenceRecords.previewBadge")}
                          </span>
                        )}
                      </div>
                      <p className="mt-1 text-xs text-[var(--color-text-secondary)] leading-relaxed">
                        {row.description}
                      </p>
                    </div>
                  </div>

                  {row.verdict_set.length > 0 && (
                    <div className="text-[11px] text-[var(--color-text-tertiary)]">
                      <span className="font-semibold uppercase tracking-wider mr-1">
                        {t("rules.evidenceRecords.verdicts")}:
                      </span>
                      <span className="inline-flex flex-wrap gap-1 align-middle">
                        {row.verdict_set.map((v) => (
                          <Code key={v} className="text-[10px]">{v}</Code>
                        ))}
                      </span>
                    </div>
                  )}

                  <details className="rounded-lg border border-black/[0.05] bg-[var(--color-surface-1,#f9fafb)]/40">
                    <summary className="flex cursor-pointer items-center justify-between gap-2 rounded-lg px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)] hover:bg-black/[0.02]">
                      <span>{t("rules.evidenceRecords.payloadSchema")}</span>
                      <span aria-hidden>▾</span>
                    </summary>
                    <div className="px-3 pb-3 pt-1">
                      <dl className="text-[12px] leading-relaxed">
                        {row.payload_schema.map((f, i) => (
                          <div
                            key={`${f.path}:${i}`}
                            className={`grid grid-cols-[auto_auto_1fr] gap-x-3 py-1 ${i > 0 ? "border-t border-black/[0.04]" : ""}`}
                          >
                            <dt className="font-mono text-[var(--color-text-primary)]">
                              {f.path}
                            </dt>
                            <dd className="font-mono text-[var(--color-text-tertiary)]">
                              {f.type}
                            </dd>
                            <dd className="text-[var(--color-text-secondary)]">
                              {f.description}
                            </dd>
                          </div>
                        ))}
                      </dl>
                    </div>
                  </details>

                  <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-[var(--color-text-tertiary)]">
                    <div>
                      <span className="font-semibold uppercase tracking-wider mr-1">
                        {t("rules.evidenceRecords.recentEmissions")}
                      </span>
                      <span className="font-mono text-[var(--color-text-primary)]">
                        {count === null ? "-" : nfFormat(count)}
                      </span>
                      <span className="ml-1">
                        ({t("rules.evidenceRecords.recentEmissionsWindow")})
                      </span>
                    </div>
                    <Link
                      href={ledgerHref({ verifiers: [row.id] })}
                      className="text-[var(--color-accent-light)] hover:underline"
                    >
                      {t("rules.evidenceRecords.viewInLedger")}
                    </Link>
                  </div>

                  {row.used_by_policies.length > 0 && (
                    <div className="text-[11px] text-[var(--color-text-tertiary)]">
                      <span className="font-semibold uppercase tracking-wider mr-1">
                        {t("rules.evidenceRecords.usedBy")}:
                      </span>
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
                </Card>
              )
            })}
          </div>
        </>
      )}
    </section>
  )
}
