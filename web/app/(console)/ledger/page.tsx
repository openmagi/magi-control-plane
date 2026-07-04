import Link from "next/link"
import { cloud, type EvidenceTypeEntry } from "@/lib/cloud"
import { fmtUtc, clampNonNegInt, LEDGER_PAGE_SIZE } from "@/lib/format"
import { getIntl, getT } from "@/lib/i18n/server"
import { ledgerHref, parseVerifierParam } from "@/lib/ledger-url"
import {
  Badge, Button, Card, Code, CopyButton, EmptyState, ErrorState, PageHeader,
} from "@/components/ui"

export const dynamic = "force-dynamic"

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

type TFunc = (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string

export default async function LedgerPage({
  searchParams,
}: { searchParams: { since?: string; verifier?: string | string[] } }) {
  const { t } = await getT()
  const { nf } = await getIntl()
  const since = clampNonNegInt(searchParams.since, 0)
  const verifierFilter = parseVerifierParam(searchParams.verifier)

  let result: Awaited<ReturnType<typeof cloud.ledger>> | null = null
  let err: string | null = null
  // D52c: catalog drives the chip selector. We swallow the catalog
  // error so a flaky `/catalog/evidence-types` only mutes the chip
  // row; the ledger view itself stays available.
  let catalog: EvidenceTypeEntry[] = []
  try {
    catalog = await cloud.listEvidenceTypes()
  } catch {
    catalog = []
  }
  try {
    result = await cloud.ledger(
      since, LEDGER_PAGE_SIZE,
      verifierFilter.length > 0 ? verifierFilter : undefined,
      // Self-host single-operator: the operator owns this ledger and
      // authenticates with their own tenant key, so there is no reason to
      // hide their own entry bodies. Fetch the full body for drill-down.
      true,
    )
  } catch (e: unknown) {
    err = errMsg(e)
  }

  return (
    <>
      <PageHeader title={t("ledger.title")} />

      {/* D52c: verifier chip selector. Rendered above the chain-integrity
          card so the operator sees the filter context before the data.
          Catalog comes from /catalog/evidence-types (same source as the
          Rules → Verifiers tab) so the chip set stays in sync with the
          policy authoring view.

          D52c follow-up: when the catalog fetch fails AND a filter is
          active in the URL, we render a degraded card with only the
          active-badge + Clear-filter link so a deep-linked URL always
          has a one-click escape (was: filter applied silently with no
          chips to toggle off and no Clear affordance). */}
      {catalog.length > 0 ? (
        <VerifierFilterChips
          catalog={catalog}
          selected={verifierFilter}
          since={since}
          t={t}
        />
      ) : verifierFilter.length > 0 ? (
        <VerifierFilterDegradedCard selected={verifierFilter} t={t} />
      ) : null}

      {err && (
        <ErrorState
          title={t("common.cloudUnreachable")}
          body={err}
        />
      )}

      {result && (
        <>
          {result.entries.length === 0 ? (
            <>
              <LedgerMasthead chainOk={result.chain_ok} cursor={nf.format(result.next_since_id)} attached={false} t={t} />
              {verifierFilter.length > 0 ? (
                <EmptyState title={t("ledger.filter.empty")} />
              ) : (
                <EmptyState
                  title={t("ledger.empty.title")}
                  body={t("ledger.empty.body")}
                  action={
                    <Link href="/rules">
                      <Button variant="primary">{t("ledger.empty.cta")}</Button>
                    </Link>
                  }
                />
              )}
            </>
          ) : (
            // The audit ledger is a tamper-evident hash chain. Render it as
            // one bordered record: an integrity masthead over a monospace
            // table with a sticky header, hairline rows, and per-row raw-JSON
            // copy. Server-rendered; the body drill-down stays no-JS
            // (<details>), copy is progressive enhancement.
            <div className="overflow-hidden rounded-lg border border-[var(--color-border-subtle)] bg-[var(--color-surface-raised)]">
              <LedgerMasthead chainOk={result.chain_ok} cursor={nf.format(result.next_since_id)} attached t={t} />
              <div className="max-h-[calc(100dvh-16rem)] overflow-auto">
                <table className="w-full">
                  <caption className="sr-only">{t("ledger.title")}</caption>
                  <thead className="sticky top-0 z-10 bg-[var(--color-surface-raised)]">
                    <tr>
                      <th className="text-right">{t("ledger.col.id")}</th>
                      <th>{t("ledger.col.ts")}</th>
                      <th>{t("ledger.col.subject")}</th>
                      <th>{t("ledger.col.prev")}</th>
                      <th>{t("ledger.col.h")}</th>
                      <th>{t("ledger.col.detail")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.entries.map(e => (
                      <tr
                        key={e.id}
                        className="align-top transition-colors hover:bg-[var(--color-surface-overlay)]/60"
                      >
                        <td className="text-right font-mono tabular-nums text-[var(--color-text-tertiary)]">
                          {e.id}
                        </td>
                        <td className="whitespace-nowrap font-mono text-xs text-[var(--color-text-tertiary)]">
                          {fmtUtc(e.ts)}
                        </td>
                        <td><Code>{e.subject}</Code></td>
                        <td>
                          <Code title={e.prev}>
                            {e.prev ? e.prev.slice(0, 12) + "…" : "∅"}
                          </Code>
                        </td>
                        <td><Code title={e.h}>{e.h.slice(0, 12)}…</Code></td>
                        <td>
                          {e.body ? (
                            // No-JS drill-down: the operator owns this ledger,
                            // so the full entry body is served (include_body)
                            // and expanded inline via a native <details>.
                            <details>
                              <summary className="cursor-pointer text-xs font-medium text-[var(--color-accent-light)] hover:underline">
                                {t("ledger.detail.view")}
                              </summary>
                              <div className="mt-2 flex items-start gap-2">
                                <pre className="max-w-2xl flex-1 overflow-x-auto whitespace-pre-wrap break-words rounded-md bg-[var(--color-surface-overlay)] p-2 text-[11px] font-mono text-[var(--color-text-secondary)]">
                                  {JSON.stringify(e.body, null, 2)}
                                </pre>
                                <CopyButton
                                  value={JSON.stringify(e.body, null, 2)}
                                  size="sm"
                                  variant="ghost"
                                  label={t("common.copy")}
                                  copiedLabel={t("common.copied")}
                                  className="shrink-0"
                                />
                              </div>
                            </details>
                          ) : (
                            <span className="text-xs text-[var(--color-text-tertiary)]">
                              {t("ledger.detail.none")}
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <nav
            aria-label="Ledger pagination"
            className="mt-4 flex items-center gap-4 text-sm"
          >
            {since > 0 && (
              <Link
                href={ledgerHref({ verifiers: verifierFilter })}
                aria-label="First page"
              >
                {t("ledger.first")}
              </Link>
            )}
            {result.entries.length === LEDGER_PAGE_SIZE
              && result.next_since_id !== since && (
              <Link
                href={ledgerHref({
                  since: result.next_since_id,
                  verifiers: verifierFilter,
                })}
                aria-label="Next page"
              >
                {t("ledger.next")}
              </Link>
            )}
          </nav>
        </>
      )}
    </>
  )
}

/** Ledger integrity masthead: the chain-integrity verdict + cursor as the
 * record's status line. `attached` sits it flush on top of the table
 * (shared bottom border); standalone (empty state) it is its own card. */
function LedgerMasthead({
  chainOk, cursor, attached, t,
}: {
  chainOk: boolean
  cursor: string
  attached: boolean
  t: TFunc
}) {
  return (
    <div
      className={
        "flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 "
        + (attached
          ? "border-b border-[var(--color-border-subtle)]"
          : "mb-4 rounded-lg border border-[var(--color-border-subtle)] bg-[var(--color-surface-raised)]")
      }
    >
      <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--color-text-tertiary)]">
        {t("ledger.chainIntegrity")}
      </span>
      {chainOk
        ? <Badge variant="ok">{t("ledger.chainOk")}</Badge>
        : <Badge variant="deny">{t("ledger.chainBroken")}</Badge>}
      <span className="ml-auto font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
        {t("ledger.cursor", { n: cursor })}
      </span>
    </div>
  )
}

/** D52c: chip selector rendered at the top of the /ledger page.
 *
 * Each chip is a server-rendered <Link> that toggles its own step
 * in/out of the `?verifier=` URL state. Clicking a chip rebuilds the
 * URL with the new set and Next.js navigates to it; URL is the only
 * state (no client JS, no useState). The chip set comes from the
 * catalog (built-in + custom + policy-derived) so a verifier that
 * cannot fire (`enforcement: "missing"`) is still pickable. Useful
 * for diagnosing why a policy isn't producing emissions.
 *
 * D52c follow-up:
 *   - empty-step catalog rows are dropped (was: rendered as
 *     visually-empty clickable chips that navigated to `?verifier=`,
 *     i.e. unfiltered (defeating the operator's intent),
 *   - `since` cursor is preserved through chip toggles (was:
 *     silently reset to page 1 on every click. The backend
 *     tolerates a stale cursor under the new filter; `e.id > since`
 *     simply continues from there),
 *   - per-source visual treatment so a `missing` chip is
 *     distinguishable at a glance from a built-in enforcing chip
 *     (aligns with the EnforcementBadge mapping on the Rules tab).
 */
function VerifierFilterChips({
  catalog, selected, since, t,
}: {
  catalog: EvidenceTypeEntry[]
  selected: string[]
  since: number
  t: TFunc
}) {
  const selectedSet = new Set(selected)
  // Stable order: builtin first (alpha), then custom, then derived.
  // Within each bucket, alpha. Matches the Rules → Verifiers tab order.
  const sourceRank: Record<EvidenceTypeEntry["source"], number> = {
    builtin: 0, custom: 1, "policy-derived": 2,
  }
  // D52c follow-up: drop empty-step rows. They produced visually-blank
  // clickable chips and a /ledger?verifier= dead link (the backend
  // treats empty values as "no filter"); React would also key-collide
  // if more than one such row leaked through. The catalog producer at
  // app.py now skips empty-step requires too, but the dashboard guard
  // stays as defence-in-depth.
  const sorted = catalog
    .filter((row) => row.step && row.step.length > 0)
    .sort((a, b) => {
      const ra = sourceRank[a.source] ?? 99
      const rb = sourceRank[b.source] ?? 99
      if (ra !== rb) return ra - rb
      return a.step.localeCompare(b.step)
    })
  // Clearing filters always returns to page 1: with no filter on the
  // URL the operator wants the freshest view, not a cursor inherited
  // from a narrower one.
  const clearHref = `/ledger`
  return (
    <Card className="mb-4" data-testid="verifier-filter-chips">
      <div className="flex flex-wrap items-baseline justify-between gap-2 mb-2">
        <div className="flex flex-col gap-0.5">
          <span className="text-xs font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)]">
            {t("ledger.filter.title")}
          </span>
          <span className="text-[11px] text-[var(--color-text-tertiary)]">
            {t("ledger.filter.hint")}
          </span>
        </div>
        {selected.length > 0 && (
          <div className="flex items-center gap-2">
            <Badge variant="info">
              {t("ledger.filter.activeBadge", { n: selected.length })}
            </Badge>
            <Link
              href={clearHref}
              data-testid="verifier-filter-clear"
              className="text-xs font-medium text-[var(--color-accent-light)] hover:underline"
            >
              {t("ledger.filter.clear")}
            </Link>
          </div>
        )}
      </div>
      <div className="flex flex-wrap gap-1.5" role="list">
        {sorted.map((row) => {
          const isOn = selectedSet.has(row.step)
          // Toggle: if on → drop it from the set; if off → add it.
          // D52c follow-up: preserve `since` across the toggle so a
          // user widening / narrowing the filter on page 3 isn't
          // yanked back to page 1. The backend tolerates a stale
          // cursor (entries continue from `e.id > since` under the
          // new filter view).
          const nextVerifiers = isOn
            ? selected.filter((s) => s !== row.step)
            : [...selected, row.step]
          const href = ledgerHref({
            since: since > 0 ? since : undefined,
            verifiers: nextVerifiers,
          })
          return (
            <Link
              key={row.step}
              href={href}
              role="listitem"
              aria-pressed={isOn}
              data-step={row.step}
              data-on={isOn ? "true" : "false"}
              data-source={row.source}
              data-enforcement={row.enforcement}
              className={chipClasses(row, isOn)}
              title={chipTitle(row, t)}
            >
              {row.step}
            </Link>
          )
        })}
      </div>
    </Card>
  )
}

/** Per-source visual treatment for the chip. Reuses the same accent
 * tones as the EnforcementBadge palette on the Rules tab so an
 * operator's eye can correlate the two views at a glance.
 *
 *   - builtin / enforcing → solid accent when ON, neutral when OFF
 *   - custom              → dashed outline when OFF (preview source)
 *   - policy-derived      → dashed outline + muted tone when OFF
 *     (inline-kind rows live here too; the kind is encoded in the
 *      step prefix `inline_*`)
 */
function chipClasses(row: EvidenceTypeEntry, isOn: boolean): string {
  const base =
    "inline-flex items-center rounded-full px-2.5 py-1 text-[11px] " +
    "font-mono transition-colors"
  if (isOn) {
    return `${base} bg-[var(--color-accent)] text-white`
  }
  if (row.source === "builtin") {
    return (
      `${base} bg-[var(--color-surface-overlay)] ` +
      "text-[var(--color-text-tertiary)] hover:bg-black/[0.06]"
    )
  }
  // custom + policy-derived share the dashed-outline treatment so the
  // operator can see at a glance "this is not an authored built-in".
  return (
    `${base} border border-dashed border-black/[0.18] ` +
    "bg-white text-[var(--color-text-tertiary)] hover:bg-black/[0.04]"
  )
}

/** Hover tooltip: explains the source bucket so the visual treatment
 * is self-documenting. Reuses existing dictionary keys from the
 * Rules → Verifiers tab. */
function chipTitle(row: EvidenceTypeEntry, t: TFunc): string {
  if (row.source === "builtin") return t("rules.evidence.source.builtin")
  if (row.source === "custom") return t("rules.evidence.source.custom")
  return t("rules.evidence.source.derived")
}

/** D52c follow-up: degraded chip card.
 *
 * Rendered when `/catalog/evidence-types` is unreachable but the user
 * deep-linked into a filtered view. We can't show the chips (no
 * catalog) but we can still surface the active-badge + Clear-filter
 * link so the URL has a one-click escape regardless of catalog
 * availability. Was: filter applied silently with no UI signal.
 */
function VerifierFilterDegradedCard({
  selected, t,
}: {
  selected: string[]
  t: TFunc
}) {
  return (
    <Card className="mb-4" data-testid="verifier-filter-chips-degraded">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          <span className="text-xs font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)]">
            {t("ledger.filter.title")}
          </span>
          <span className="text-[11px] text-[var(--color-text-tertiary)]">
            {t("ledger.filter.catalogUnavailable")}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="info">
            {t("ledger.filter.activeBadge", { n: selected.length })}
          </Badge>
          <Link
            href={`/ledger`}
            data-testid="verifier-filter-clear"
            className="text-xs font-medium text-[var(--color-accent-light)] hover:underline"
          >
            {t("ledger.filter.clear")}
          </Link>
        </div>
      </div>
    </Card>
  )
}
