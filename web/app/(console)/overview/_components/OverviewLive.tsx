"use client"
import { useCallback, useEffect, useRef, useState } from "react"

import type {
  LedgerAggregateResponse, OverviewActionKey, OverviewSummary,
} from "@/lib/cloud"
// D76: client island — subpath imports only so the bundler does not
// drag NavBarShell (which transitively pulls `i18n/server`, server-only)
// into the client chunk.
import { Badge } from "@/components/ui/Badge"
import { Card } from "@/components/ui/Card"
import { KPI } from "@/components/ui/KPI"

import { EmissionChart } from "./EmissionChart"
import { HeadlineCard } from "./HeadlineCard"

/**
 * D76: client island wrapping the headline + KPI grid + chart.
 *
 * Receives the initial snapshot from the server component as
 * `initialSummary` / `initialAggregate`. After mount it polls
 * `/api/overview-refresh` every `refreshIntervalMs` and replaces the
 * rendered state with the fresh data. Polling is opt-in via the
 * Page Visibility API (no requests when the tab is hidden) and via a
 * `localStorage[storageKey]` flag (operator can disable globally).
 *
 * Auto-refresh is disabled when `autoRefresh={false}` (tests +
 * environments that explicitly opt out). The component otherwise
 * defaults to enabled.
 */
type Locale = "ko" | "en"

type Props = {
  locale: Locale
  initialSummary: OverviewSummary | null
  initialAggregate: LedgerAggregateResponse
  /** Disable auto-refresh entirely (tests, feature-flag opt-out). */
  autoRefresh?: boolean
  /** Poll cadence in milliseconds. Defaults to 30s. */
  refreshIntervalMs?: number
  /** localStorage key the operator can flip to "off" to silence polling. */
  storageKey?: string
  /** Localized strings; passed from the server component so the dict
   *  resolution stays server-side. */
  t: {
    headlineWithActivity: string
    headlineNoActivity: string
    detailWithActivity: string
    emptyBody: string
    refreshLabel: string
    refreshDisabled: string
    refreshNow: string
    chartTitle: string
    chartEmptyBody: string
    actionLabel: Record<OverviewActionKey, string>
    kpi: {
      activePolicies: string
      activePoliciesValue: string
      activePacks: string
      activePacksValue: string
      scripts: string
      hitlPending: string
      auditChain: string
      auditChainOk: string
      auditChainBroken: string
    }
  }
  /** Locale-aware Intl.NumberFormat (resolved server-side). */
  numberFormatLocale: string
}

function interpolate(s: string, vars: Record<string, string | number>): string {
  let out = s
  for (const [k, v] of Object.entries(vars)) {
    out = out.replace(new RegExp(`\\{${k}\\}`, "g"), String(v))
  }
  return out
}

export function OverviewLive({
  locale, initialSummary, initialAggregate,
  autoRefresh = true,
  refreshIntervalMs = 30_000,
  storageKey = "magi-cp:overview:autoRefresh",
  t,
  numberFormatLocale,
}: Props) {
  const [summary, setSummary] = useState<OverviewSummary | null>(initialSummary)
  const [aggregate, setAggregate] = useState<LedgerAggregateResponse>(initialAggregate)
  const [refreshedAtSec, setRefreshedAtSec] = useState<number | null>(null)
  // Track the operator's opt-out from localStorage. We resolve it lazily
  // on mount so SSR + first paint stay deterministic.
  const [enabled, setEnabled] = useState<boolean>(autoRefresh)
  const [tabVisible, setTabVisible] = useState<boolean>(true)
  const inFlight = useRef(false)

  // Initialize localStorage-driven enabled flag once on mount. Default
  // is "respect prop"; if the operator has explicitly set the key to
  // "false" we honour that.
  useEffect(() => {
    if (!autoRefresh) return
    try {
      const v = window.localStorage.getItem(storageKey)
      if (v === "false") setEnabled(false)
    } catch {
      // localStorage may throw on private-mode browsers; treat as
      // "no opt-out" and continue.
    }
  }, [autoRefresh, storageKey])

  // Page Visibility wiring.
  useEffect(() => {
    if (typeof document === "undefined") return
    const onChange = () => setTabVisible(document.visibilityState === "visible")
    onChange()
    document.addEventListener("visibilitychange", onChange)
    return () => document.removeEventListener("visibilitychange", onChange)
  }, [])

  const fetchNow = useCallback(async () => {
    if (inFlight.current) return
    inFlight.current = true
    try {
      const params = new URLSearchParams()
      params.set("since_secs", String(initialAggregate.since_secs))
      params.set("bucket_secs", String(initialAggregate.bucket_secs))
      const r = await fetch(`/api/overview-refresh?${params.toString()}`, {
        cache: "no-store",
      })
      if (!r.ok) return
      const body = await r.json() as {
        summary: OverviewSummary
        aggregate: LedgerAggregateResponse
        ts: number
      }
      setSummary(body.summary)
      setAggregate(body.aggregate)
      setRefreshedAtSec(body.ts)
    } catch {
      // Network blip: skip this tick; the next tick will retry.
    } finally {
      inFlight.current = false
    }
  }, [initialAggregate.since_secs, initialAggregate.bucket_secs])

  // Polling loop.
  useEffect(() => {
    if (!enabled || !tabVisible) return
    const id = window.setInterval(() => { void fetchNow() }, refreshIntervalMs)
    return () => window.clearInterval(id)
  }, [enabled, tabVisible, refreshIntervalMs, fetchNow])

  // Headline numbers. Derive from the action / verdict breakdown.
  const total = summary?.ledger_24h_total ?? 0
  const blocked = aggregate.buckets.reduce(
    (a, b) => a + (b.by_action.block ?? 0), 0,
  )
  const audited = aggregate.buckets.reduce(
    (a, b) => a + (b.by_action.audit ?? 0), 0,
  )
  const pending = summary?.hitl_pending ?? 0
  const hasActivity = total > 0 || pending > 0

  const nf = new Intl.NumberFormat(numberFormatLocale)
  const headlineText = hasActivity
    ? interpolate(t.headlineWithActivity, { n: nf.format(total) })
    : t.headlineNoActivity
  const detailText = interpolate(t.detailWithActivity, {
    blocked: nf.format(blocked),
    pending: nf.format(pending),
    audited: nf.format(audited),
  })

  // Last refreshed footer. Show only after the first poll lands so the
  // server-rendered initial state doesn't carry a "0s ago" label.
  const footer = refreshedAtSec ? (
    <RefreshFooter
      refreshedAtSec={refreshedAtSec}
      label={t.refreshLabel}
      disabledLabel={t.refreshDisabled}
      refreshNowLabel={t.refreshNow}
      enabled={enabled}
      onToggle={() => {
        const next = !enabled
        setEnabled(next)
        try {
          window.localStorage.setItem(storageKey, String(next))
        } catch {
          // ignore
        }
      }}
      onRefreshNow={() => { void fetchNow() }}
    />
  ) : null

  return (
    <>
      <HeadlineCard
        total={total}
        blocked={blocked}
        pending={pending}
        audited={audited}
        hasActivity={hasActivity}
        headline={headlineText}
        detail={detailText}
        emptyBody={t.emptyBody}
        footer={footer}
      />

      {/* Row 2: KPI grid */}
      <div
        className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mt-4"
        data-testid="overview-kpi-grid"
      >
        <KPI
          label={t.kpi.activePolicies}
          value={summary
            ? interpolate(t.kpi.activePoliciesValue, {
                enabled: nf.format(summary.policies.enabled),
                total: nf.format(summary.policies.total),
              })
            : "—"}
        />
        <KPI
          label={t.kpi.activePacks}
          value={summary
            ? interpolate(t.kpi.activePacksValue, {
                active: nf.format(summary.packs.total_active),
                partial: nf.format(summary.packs.partial),
              })
            : "—"}
        />
        <KPI
          label={t.kpi.scripts}
          value={summary ? nf.format(summary.scripts.total) : "—"}
        />
        <KPI
          label={t.kpi.hitlPending}
          value={summary ? nf.format(summary.hitl_pending) : "—"}
          trailing={summary && summary.hitl_pending > 0
            ? <Badge variant="review">{nf.format(summary.hitl_pending)}</Badge>
            : undefined}
        />
      </div>

      {/* Audit chain badge — a one-bit indicator so it never gets lost
          in the noise of the 4-up grid above. */}
      <div className="mt-4">
        <Card className="flex items-center justify-between gap-2">
          <span className="text-sm text-[var(--color-text-secondary)]">
            {t.kpi.auditChain}
          </span>
          {summary?.ledger_chain_ok ?? true
            ? <Badge variant="ok">{t.kpi.auditChainOk}</Badge>
            : <Badge variant="deny">{t.kpi.auditChainBroken}</Badge>}
        </Card>
      </div>

      {/* Row 3: 24h chart */}
      <Card className="mt-4">
        <h2 className="text-sm font-medium text-[var(--color-text-primary)] mb-3">
          {t.chartTitle}
        </h2>
        <EmissionChart
          data={aggregate}
          actionLabel={t.actionLabel}
          emptyBody={t.chartEmptyBody}
          nf={nf}
        />
      </Card>
    </>
  )
}

function RefreshFooter({
  refreshedAtSec, label, disabledLabel, refreshNowLabel,
  enabled, onToggle, onRefreshNow,
}: {
  refreshedAtSec: number
  label: string
  disabledLabel: string
  refreshNowLabel: string
  enabled: boolean
  onToggle: () => void
  onRefreshNow: () => void
}) {
  // Re-render every 5s so the relative-time string ticks.
  const [, setTick] = useState(0)
  useEffect(() => {
    const id = window.setInterval(() => setTick(t => t + 1), 5_000)
    return () => window.clearInterval(id)
  }, [])

  const now = Math.floor(Date.now() / 1000)
  const ago = Math.max(0, now - refreshedAtSec)
  let agoLabel: string
  if (ago < 5) agoLabel = "just now"
  else if (ago < 60) agoLabel = `${ago}s ago`
  else if (ago < 3600) agoLabel = `${Math.floor(ago / 60)}m ago`
  else agoLabel = `${Math.floor(ago / 3600)}h ago`

  return (
    <div className="flex items-center gap-3">
      <span>
        {label}: <span className="font-medium">{agoLabel}</span>
      </span>
      <button
        type="button"
        onClick={onRefreshNow}
        className="text-xs underline text-[var(--color-accent-light)] hover:no-underline"
      >
        {refreshNowLabel}
      </button>
      <button
        type="button"
        onClick={onToggle}
        className="text-xs text-[var(--color-text-tertiary)] hover:underline"
      >
        {enabled ? disabledLabel : label}
      </button>
    </div>
  )
}

export default OverviewLive
