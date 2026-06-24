import Link from "next/link"
import { cloud, type EvidenceTypeEntry } from "@/lib/cloud"
import { fmtUtc, clampNonNegInt, LEDGER_PAGE_SIZE } from "@/lib/format"
import { getIntl, getT } from "@/lib/i18n/server"
import {
  Badge, Card, Code, EmptyState, ErrorState, PageHeader,
} from "@/components/ui"

export const dynamic = "force-dynamic"

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

type TFunc = (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string

/** D52c: normalize `?verifier=...` into a clean string[].
 *
 * Next.js delivers a repeated query param as `string[]` (e.g.
 * `?verifier=a&verifier=b`) and a single occurrence as `string`. We
 * also strip empty values so `?verifier=` (no value) is treated as
 * "no filter" (matches the backend's `if v` filter).
 */
function parseVerifierParam(raw: string | string[] | undefined): string[] {
  if (raw == null) return []
  const arr = Array.isArray(raw) ? raw : [raw]
  // Dedupe with Set to keep the URL stable when a chip is clicked twice
  // in a row (browsers often submit duplicate params on hash collisions).
  return Array.from(new Set(arr.filter(Boolean)))
}

/** Build a `/ledger?...` href with the given verifier filter applied. */
function ledgerHref(opts: { since?: number; verifiers?: string[] }): string {
  const params = new URLSearchParams()
  if (opts.since && opts.since > 0) params.set("since", String(opts.since))
  for (const v of opts.verifiers ?? []) params.append("verifier", v)
  const qs = params.toString()
  return qs ? `/ledger?${qs}` : "/ledger"
}

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
          policy authoring view. */}
      {catalog.length > 0 && (
        <VerifierFilterChips
          catalog={catalog}
          selected={verifierFilter}
          t={t}
        />
      )}

      {err && (
        <ErrorState
          title={t("common.cloudUnreachable")}
          body={err}
        />
      )}

      {result && (
        <>
          <Card className="mb-4 flex flex-wrap items-center gap-3">
            <span className="text-sm">
              {t("ledger.chainIntegrity")}:{" "}
              {result.chain_ok
                ? <Badge variant="ok">{t("ledger.chainOk")}</Badge>
                : <Badge variant="deny">{t("ledger.chainBroken")}</Badge>}
            </span>
            <span className="text-xs text-[var(--color-text-tertiary)]">
              {t("ledger.cursor", { n: nf.format(result.next_since_id) })}
            </span>
          </Card>

          {result.entries.length === 0 ? (
            <EmptyState
              title={verifierFilter.length > 0
                ? t("ledger.filter.empty")
                : t("ledger.empty")}
            />
          ) : (
            <Card noPadding className="overflow-x-auto">
              <table>
                <caption className="sr-only">
                  {t("ledger.title")}
                </caption>
                <thead>
                  <tr>
                    <th>{t("ledger.col.id")}</th>
                    <th>{t("ledger.col.ts")}</th>
                    <th>{t("ledger.col.subject")}</th>
                    <th>{t("ledger.col.prev")}</th>
                    <th>{t("ledger.col.h")}</th>
                  </tr>
                </thead>
                <tbody>
                  {result.entries.map(e => (
                    <tr key={e.id}>
                      <td>{e.id}</td>
                      <td className="text-[var(--color-text-tertiary)]">
                        {fmtUtc(e.ts)}
                      </td>
                      <td><Code>{e.subject}</Code></td>
                      <td>
                        <Code title={e.prev}>
                          {e.prev ? e.prev.slice(0, 12) + "…" : "∅"}
                        </Code>
                      </td>
                      <td><Code title={e.h}>{e.h.slice(0, 12)}…</Code></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          )}

          <p className="text-xs text-[var(--color-text-tertiary)] mt-3">
            {t("ledger.redactionNote")}
          </p>

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

/** D52c: chip selector rendered at the top of the /ledger page.
 *
 * Each chip is a server-rendered <Link> that toggles its own step
 * in/out of the `?verifier=` URL state. Clicking a chip rebuilds the
 * URL with the new set and Next.js navigates to it; URL is the only
 * state (no client JS, no useState). The chip set comes from the
 * catalog (built-in + custom + policy-derived) so a verifier that
 * cannot fire (`enforcement: "missing"`) is still pickable. Useful
 * for diagnosing why a policy isn't producing emissions.
 */
function VerifierFilterChips({
  catalog, selected, t,
}: {
  catalog: EvidenceTypeEntry[]
  selected: string[]
  t: TFunc
}) {
  const selectedSet = new Set(selected)
  // Stable order: builtin first (alpha), then custom, then derived.
  // Within each bucket, alpha. Matches the Rules → Verifiers tab order.
  const sourceRank: Record<EvidenceTypeEntry["source"], number> = {
    builtin: 0, custom: 1, "policy-derived": 2,
  }
  const sorted = [...catalog].sort((a, b) => {
    const ra = sourceRank[a.source] ?? 99
    const rb = sourceRank[b.source] ?? 99
    if (ra !== rb) return ra - rb
    return a.step.localeCompare(b.step)
  })
  // Resetting filters always returns to page 1: since the chain
  // changes shape when the filter changes, a cursor from one filter
  // view is meaningless against another.
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
          // Page cursor `since` is dropped on chip toggle because it
          // points into a different filter view (see clearHref note).
          const nextVerifiers = isOn
            ? selected.filter((s) => s !== row.step)
            : [...selected, row.step]
          const href = ledgerHref({ verifiers: nextVerifiers })
          return (
            <Link
              key={row.step}
              href={href}
              role="listitem"
              aria-pressed={isOn}
              data-step={row.step}
              data-on={isOn ? "true" : "false"}
              className={`inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-mono transition-colors ${
                isOn
                  ? "bg-[var(--color-accent)] text-white"
                  : "bg-[var(--color-muted-bg,#f3f4f6)] text-[var(--color-muted-fg,#374151)] hover:bg-black/[0.06]"
              }`}
            >
              {row.step}
            </Link>
          )
        })}
      </div>
    </Card>
  )
}
