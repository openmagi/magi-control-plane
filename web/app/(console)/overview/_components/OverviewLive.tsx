"use client"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"

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

// Lazy localStorage initialiser. Read once on first render so the
// polling useEffect never sets up an interval just to tear it down on
// the next tick because the operator had previously opted out. SSR is
// guarded with the `typeof window` check.
function readEnabledFromStorage(
  autoRefresh: boolean, storageKey: string,
): boolean {
  if (!autoRefresh) return false
  if (typeof window === "undefined") return autoRefresh
  try {
    const v = window.localStorage.getItem(storageKey)
    if (v === "false") return false
    return autoRefresh
  } catch {
    return autoRefresh
  }
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
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false)
  // Lazy initialiser — see `readEnabledFromStorage`. Prevents the
  // create + clearInterval churn that a useEffect-driven init causes.
  const [enabled, setEnabled] = useState<boolean>(
    () => readEnabledFromStorage(autoRefresh, storageKey),
  )
  const [tabVisible, setTabVisible] = useState<boolean>(true)
  // Holds the currently-in-flight AbortController so a fresh tick can
  // abort a slow predecessor (last-write-wins) and so unmount can
  // cancel any pending response that would otherwise call setState on
  // an unmounted tree.
  const inFlightCtrl = useRef<AbortController | null>(null)

  // Cross-tab opt-out propagation. Subscribe to the storage event so
  // toggling the key in one tab converges the other tabs without a
  // reload.
  useEffect(() => {
    if (typeof window === "undefined") return
    const onStorage = (e: StorageEvent) => {
      if (e.key !== storageKey) return
      setEnabled(e.newValue === "false" ? false : autoRefresh)
    }
    window.addEventListener("storage", onStorage)
    return () => window.removeEventListener("storage", onStorage)
  }, [autoRefresh, storageKey])

  // Page Visibility wiring.
  useEffect(() => {
    if (typeof document === "undefined") return
    const onChange = () => setTabVisible(document.visibilityState === "visible")
    onChange()
    document.addEventListener("visibilitychange", onChange)
    return () => document.removeEventListener("visibilitychange", onChange)
  }, [])

  const sinceSecs = initialAggregate.since_secs
  const bucketSecs = initialAggregate.bucket_secs

  const fetchNow = useCallback(async (externalSignal?: AbortSignal) => {
    // Last-write-wins: a slow in-flight request is aborted by a fresh
    // tick so the dashboard never sits "frozen" while an old request
    // monopolises the inFlight slot. Externally-supplied signals
    // (effect cleanup) are layered with the per-call controller so
    // unmount can cancel everything at once.
    if (inFlightCtrl.current) {
      inFlightCtrl.current.abort()
    }
    const ctrl = new AbortController()
    inFlightCtrl.current = ctrl
    const linkExternal = () => ctrl.abort()
    if (externalSignal) {
      if (externalSignal.aborted) {
        ctrl.abort()
      } else {
        externalSignal.addEventListener("abort", linkExternal, { once: true })
      }
    }
    setIsRefreshing(true)
    try {
      const params = new URLSearchParams()
      params.set("since_secs", String(sinceSecs))
      params.set("bucket_secs", String(bucketSecs))
      const r = await fetch(`/api/overview-refresh?${params.toString()}`, {
        cache: "no-store",
        signal: ctrl.signal,
      })
      if (!r.ok) return
      const body = await r.json() as {
        summary: OverviewSummary
        aggregate: LedgerAggregateResponse
        ts: number
      }
      if (ctrl.signal.aborted) return
      setSummary(body.summary)
      setAggregate(body.aggregate)
      setRefreshedAtSec(body.ts)
    } catch (e) {
      // AbortError is a signal, not a failure: a fresh tick or unmount
      // intentionally cancelled this request. Anything else is a
      // network blip; the next tick will retry.
      if ((e as { name?: string })?.name === "AbortError") return
    } finally {
      if (externalSignal) {
        externalSignal.removeEventListener("abort", linkExternal)
      }
      // Only clear the inFlight slot if it's still ours — a later
      // tick may have already replaced it.
      if (inFlightCtrl.current === ctrl) {
        inFlightCtrl.current = null
        setIsRefreshing(false)
      }
    }
  }, [sinceSecs, bucketSecs])

  // Polling loop. The cleanup aborts any in-flight request via the
  // controller stored in `inFlightCtrl` so a stale response cannot
  // call setState on an unmounted (or hidden-then-resumed) component.
  useEffect(() => {
    if (!enabled || !tabVisible) return
    const ctrl = new AbortController()
    const id = window.setInterval(
      () => { void fetchNow(ctrl.signal) },
      refreshIntervalMs,
    )
    return () => {
      window.clearInterval(id)
      ctrl.abort()
    }
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

  const nf = useMemo(
    () => new Intl.NumberFormat(numberFormatLocale),
    [numberFormatLocale],
  )
  const headlineText = hasActivity
    ? interpolate(t.headlineWithActivity, { n: nf.format(total) })
    : t.headlineNoActivity
  const detailText = interpolate(t.detailWithActivity, {
    blocked: nf.format(blocked),
    pending: nf.format(pending),
    audited: nf.format(audited),
  })

  // Build the SR announcement string once per render and pin it into
  // an aria-live region. The region is gated to a derived sentence
  // (block / pending / audited counts + chain state) so only those
  // operator-meaningful changes re-announce — minor segment-by-segment
  // chart movement does NOT pump the live region.
  const chainOk = summary?.ledger_chain_ok ?? true
  const announcement = useMemo(() => {
    const chainPart = chainOk ? t.kpi.auditChainOk : t.kpi.auditChainBroken
    return `${detailText}; ${t.kpi.auditChain}: ${chainPart}`
  }, [detailText, chainOk, t.kpi.auditChain, t.kpi.auditChainOk, t.kpi.auditChainBroken])

  // Last refreshed footer. Show only after the first poll lands so the
  // server-rendered initial state doesn't carry a "0s ago" label.
  const footer = refreshedAtSec ? (
    <RefreshFooter
      refreshedAtSec={refreshedAtSec}
      label={t.refreshLabel}
      disabledLabel={t.refreshDisabled}
      refreshNowLabel={t.refreshNow}
      enabled={enabled}
      isRefreshing={isRefreshing}
      tabVisible={tabVisible}
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
      {/* Polite SR announcement region. One sentence, re-announces
          only when its content (block/pending/audited counts or chain
          state) actually changes. Sighted users never see it. */}
      <div
        aria-live="polite"
        aria-atomic="true"
        data-testid="overview-live-region"
        style={{
          position: "absolute",
          width: 1,
          height: 1,
          padding: 0,
          margin: -1,
          overflow: "hidden",
          clip: "rect(0,0,0,0)",
          whiteSpace: "nowrap",
          border: 0,
        }}
      >
        {announcement}
      </div>

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
          {chainOk
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
          locale={numberFormatLocale}
        />
      </Card>
    </>
  )
}

function RefreshFooter({
  refreshedAtSec, label, disabledLabel, refreshNowLabel,
  enabled, isRefreshing, tabVisible,
  onToggle, onRefreshNow,
}: {
  refreshedAtSec: number
  label: string
  disabledLabel: string
  refreshNowLabel: string
  enabled: boolean
  isRefreshing: boolean
  tabVisible: boolean
  onToggle: () => void
  onRefreshNow: () => void
}) {
  // Re-render every 5s so the relative-time string ticks. Gate on
  // `tabVisible` so a hidden tab does not keep firing a wakelock-y
  // setInterval — the parent polling loop is already gated this way.
  const [, setTick] = useState(0)
  useEffect(() => {
    if (!tabVisible) return
    const id = window.setInterval(() => setTick(t => t + 1), 5_000)
    return () => window.clearInterval(id)
  }, [tabVisible])

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
        {isRefreshing && (
          <span
            className="ml-2 text-[var(--color-text-tertiary)]"
            data-testid="overview-refreshing-indicator"
          >
            …
          </span>
        )}
      </span>
      <button
        type="button"
        onClick={onRefreshNow}
        disabled={isRefreshing}
        aria-busy={isRefreshing}
        className="text-xs underline text-[var(--color-accent-light)]
                   hover:no-underline
                   disabled:no-underline disabled:opacity-60
                   disabled:cursor-not-allowed"
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
