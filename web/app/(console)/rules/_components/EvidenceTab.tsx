import Link from "next/link"
import { Badge, Button, Code, EmptyState, ErrorState } from "@/components/ui"
import { ledgerHref } from "@/lib/ledger-url"
import type { CheckEntry, EvidenceRecordType } from "@/lib/cloud"
import { VerifierExpander } from "./VerifierExpander"

/**
 * H1 (audit Q2 / decision 2): the merged "Evidence" tab.
 *
 * Previously TWO tabs read as duplicates: "Checks" listed the verifiers
 * (functions) and "Evidence records" listed the ledger record each verifier
 * emits, both keyed 1:1 on the same built-in verifiers and both showing
 * verdicts. This merges them into ONE tab where the CHECK is the top-level
 * entity and its emitted RECORDS are the drill-down: click a check to see
 * the verdicts it can emit, the payload schema of its ledger record, the
 * recent-24h emission count, and a deep link into the ledger filtered by it.
 *
 * The check list (built-in + custom + inline) is the source of top-level
 * rows; each builtin/custom row joins its EvidenceRecordType by id for the
 * record view. Evidence record types with NO matching check (the generic
 * `inline_<kind>` shapes) are appended as read-only reference rows so no
 * information is lost.
 */

type T = (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string

const KIND_BADGE_TONE: Record<CheckEntry["kind"], string> = {
  "builtin":            "bg-[var(--color-accent)]/10 text-[var(--color-accent-light)]",
  "custom":             "bg-[var(--color-surface-overlay)] text-[var(--color-text-tertiary)]",
  "inline-regex":       "bg-amber-50 text-amber-800",
  "inline-llm-critic":  "bg-sky-50 text-sky-800",
  "inline-shacl":       "bg-emerald-50 text-emerald-800",
}

function kindLabel(kind: CheckEntry["kind"], t: T): string {
  if (kind === "builtin") return t("rules.checks.kind.builtin")
  if (kind === "custom") return t("rules.checks.kind.custom")
  if (kind === "inline-regex") return t("rules.checks.kind.inlineRegex")
  if (kind === "inline-llm-critic") return t("rules.checks.kind.inlineLlmCritic")
  return t("rules.checks.kind.inlineShacl")
}

/** The emitted-record view shared by joined check rows and standalone
 *  record-type rows: verdicts + payload schema + emissions + ledger link. */
function RecordView({
  record, emissionCounts, nfFormat, t,
}: {
  record: EvidenceRecordType
  emissionCounts: Record<string, number>
  nfFormat: (n: number) => string
  t: T
}) {
  const has = Object.prototype.hasOwnProperty.call(emissionCounts, record.id)
  const count = has ? emissionCounts[record.id] : null
  return (
    <div className="flex flex-col gap-3">
      {record.verdict_set.length > 0 && (
        <div className="text-[11px] text-[var(--color-text-tertiary)]">
          <span className="font-semibold uppercase tracking-wider mr-1">
            {t("rules.evidenceRecords.verdicts")}:
          </span>
          <span className="inline-flex flex-wrap gap-1 align-middle">
            {record.verdict_set.map((v) => (
              <Code key={v} className="text-[10px]">{v}</Code>
            ))}
          </span>
        </div>
      )}
      {record.payload_schema.length > 0 && (
        <div>
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)]">
            {t("rules.evidenceRecords.payloadSchema")}
          </div>
          <dl className="text-[12px] leading-relaxed">
            {record.payload_schema.map((f, i) => (
              <div
                key={`${f.path}:${i}`}
                className={`grid grid-cols-[auto_auto_1fr] gap-x-3 py-1 ${i > 0 ? "border-t border-black/[0.04]" : ""}`}
              >
                <dt className="font-mono text-[var(--color-text-primary)]">{f.path}</dt>
                <dd className="font-mono text-[var(--color-text-tertiary)]">{f.type}</dd>
                <dd className="text-[var(--color-text-secondary)]">{f.description}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-[var(--color-text-tertiary)]">
        <div>
          <span className="font-semibold uppercase tracking-wider mr-1">
            {t("rules.evidenceRecords.recentEmissions")}
          </span>
          <span className="font-mono text-[var(--color-text-primary)]">
            {count === null ? "-" : nfFormat(count)}
          </span>
          <span className="ml-1">({t("rules.evidenceRecords.recentEmissionsWindow")})</span>
        </div>
        {/* Inline records aggregate every policy under one generic
            `inline_<kind>` step; a ledger filter on that step cannot narrow
            to a single policy, so the deep link is hidden for inline. */}
        {record.origin !== "inline" && (
          <Link
            href={ledgerHref({ verifiers: [record.id] })}
            className="text-[var(--color-accent-light)] hover:underline"
          >
            {t("rules.evidenceRecords.viewInLedger")}
          </Link>
        )}
      </div>
    </div>
  )
}

export function EvidenceTab({
  checks,
  records,
  err,
  nfFormat,
  t,
  locale,
  emissionCounts,
}: {
  checks: CheckEntry[]
  records: EvidenceRecordType[]
  err: string | null
  nfFormat: (n: number) => string
  t: T
  locale: import("@/lib/i18n/dict").Locale
  /** Recent-24h emission counts keyed by check/record id. Missing key ->
   * render dash (distinguishes "cloud unreachable" from "no emissions"). */
  emissionCounts: Record<string, number>
}) {
  const recordsById = new Map(records.map((r) => [r.id, r]))
  const checkIds = new Set(checks.map((c) => c.id))
  // Record types with no matching check (the generic inline_<kind> shapes)
  // are appended as read-only reference rows.
  const orphanRecords = records.filter((r) => !checkIds.has(r.id))

  const builtinN = checks.filter((r) => r.kind === "builtin").length
  const customN = checks.filter((r) => r.kind === "custom").length
  const inlineN = checks.length - builtinN - customN

  const isEmpty = checks.length === 0 && orphanRecords.length === 0

  return (
    <section>
      <p className="text-xs text-[var(--color-text-tertiary)] mb-3">
        {t("rules.tab.evidence.hint")}
      </p>
      {err && (
        <ErrorState
          title={t("common.cloudUnreachable")}
          body={t("common.seeServerLogs")}
        />
      )}
      {!err && isEmpty && (
        <EmptyState
          title={t("rules.empty.checks.title")}
          body={t("rules.empty.checks.body")}
          action={
            <Link href="/verifiers/new">
              <Button variant="primary">{t("rules.empty.checks.cta")}</Button>
            </Link>
          }
        />
      )}
      {!err && !isEmpty && (
        <>
          <Badge variant="info" className="mb-3">
            {t("rules.summary.checks", {
              total: nfFormat(checks.length),
              builtin: nfFormat(builtinN),
              custom: nfFormat(customN),
              inline: nfFormat(inlineN),
            })}
          </Badge>
          <div className="rounded-2xl border border-black/[0.06] bg-white overflow-hidden">
            {checks.map((row, idx) => {
              const record = recordsById.get(row.id)
              return (
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
                            <span key={`${pid}:${i}`}>
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

                  {/* Drill-down: the RECORDS this check emits. */}
                  {record && (
                    <details className="group mt-2 rounded-lg border border-black/[0.05] bg-[var(--color-surface-1,#f9fafb)]/40"
                             data-testid={`evidence-record-${row.id}`}>
                      <summary className="flex cursor-pointer items-center justify-between gap-2 rounded-lg px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)] hover:bg-black/[0.02]">
                        <span>{t("rules.evidence.emittedRecords")}</span>
                        <span aria-hidden className="inline-block transition-transform duration-150 group-open:rotate-180">▾</span>
                      </summary>
                      <div className="px-3 pb-3 pt-1">
                        <RecordView
                          record={record}
                          emissionCounts={emissionCounts}
                          nfFormat={nfFormat}
                          t={t}
                        />
                      </div>
                    </details>
                  )}

                  {/* The check definition (inputs / descriptor / body). */}
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
                      inputAssemblyOverride={row.kind === "custom" ? row.input_assembly : undefined}
                      callerAssemblyHintOverride={row.kind === "custom" ? row.caller_assembly_hint : undefined}
                    />
                  ) : (
                    <InlineBodyPanel body={row.body} t={t} />
                  )}
                </div>
              )
            })}

            {/* Generic inline record types (no owning check). */}
            {orphanRecords.map((record, idx) => (
              <div
                key={record.id}
                className={`px-4 py-3.5 ${(checks.length > 0 || idx > 0) ? "border-t border-black/[0.05]" : ""}`}
                data-testid={`evidence-orphan-${record.id}`}
              >
                <div className="flex flex-wrap items-baseline gap-2">
                  <Code className="text-sm">{record.id}</Code>
                  <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-amber-50 text-amber-800">
                    {t("rules.evidenceRecords.origin.inline")}
                  </span>
                </div>
                <p className="mt-1 text-xs text-[var(--color-text-secondary)] leading-relaxed">
                  {record.description}
                </p>
                <div className="mt-2">
                  <RecordView
                    record={record}
                    emissionCounts={emissionCounts}
                    nfFormat={nfFormat}
                    t={t}
                  />
                </div>
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
