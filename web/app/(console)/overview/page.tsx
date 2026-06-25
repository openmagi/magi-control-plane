import Link from "next/link"

import {
  Button, Card, ErrorState, PageHeader,
} from "@/components/ui"
import {
  cloud, type LedgerAggregateResponse, type LedgerEntry,
  type OverviewActionKey, type OverviewSummary,
} from "@/lib/cloud"
import { getIntl, getT } from "@/lib/i18n/server"

import { OverviewLive } from "./_components/OverviewLive"
import {
  RecentActivity, toRecentActivityRows,
} from "./_components/RecentActivity"

export const dynamic = "force-dynamic"

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

type InitialLoad = {
  summary: OverviewSummary | null
  aggregate: LedgerAggregateResponse
  recentRows: LedgerEntry[]
  err?: string
}

function emptyAggregate(): LedgerAggregateResponse {
  // 24 zero-filled buckets at 1h resolution so the chart renders the
  // axis even when the cloud is unreachable. ts_start is anchored to
  // the current hour so the X labels still make sense.
  const now = Math.floor(Date.now() / 1000)
  const bucket = 3_600
  const since = 86_400
  const cutoff = now - since
  const buckets = Array.from({ length: since / bucket }).map((_, i) => ({
    ts_start: cutoff + i * bucket,
    count: 0,
    by_action: {
      block: 0, ask: 0, audit: 0,
      inject_context: 0, run_command: 0, input_rewrite: 0,
    } as Record<OverviewActionKey, number>,
    by_verdict: {
      pass: 0, fail: 0, needs_review: 0, not_applicable: 0,
    } as const,
  }))
  return {
    since_secs: since,
    bucket_secs: bucket,
    now,
    action_buckets: [
      "block", "ask", "audit",
      "inject_context", "run_command", "input_rewrite",
    ],
    verdict_buckets: ["pass", "fail", "needs_review", "not_applicable"],
    buckets,
  }
}

async function loadInitial(): Promise<InitialLoad> {
  try {
    // D76: "Recent activity" panel needs the last 5 rows. The cloud's
    // /ledger endpoint paginates ASC by id and doesn't expose a
    // tenant-scoped DESC reader on its public surface (the dashboard's
    // /ledger page itself fetches a 100-row page and renders ASC).
    // To bound the read here, we read the most recent 100 rows ASC
    // (`since_id` = max(0, count - 100)) and take the last 5; one
    // cheap COUNT(*) preflight bounds the page even on a large chain.
    const [summary, aggregate, count] = await Promise.all([
      cloud.overviewSummary(),
      cloud.ledgerAggregate(86_400, 3_600),
      cloud.ledgerCount(),
    ])
    const PAGE = 100
    const recentSince = Math.max(0, count.count - PAGE)
    let recentRows: LedgerEntry[] = []
    if (count.count > 0) {
      try {
        const ledger = await cloud.ledger(recentSince, PAGE, undefined, true)
        recentRows = ledger.entries
      } catch {
        recentRows = []
      }
    }
    // Cloud returns ASC by id; the "Recent activity" panel renders
    // newest first, so reverse + take the head 5.
    recentRows = recentRows.slice().reverse().slice(0, 5)
    return {
      summary,
      aggregate,
      recentRows,
    }
  } catch (e: unknown) {
    return {
      summary: null,
      aggregate: emptyAggregate(),
      recentRows: [],
      err: errMsg(e),
    }
  }
}

export default async function Home() {
  const { t, locale } = await getT()
  const { dtf } = await getIntl()
  const initial = await loadInitial()

  if (initial.err) {
    return (
      <>
        <PageHeader title={t("overview.title")} />
        <ErrorState
          title={t("common.cloudUnreachable")}
          body={t("common.seeServerLogs")}
        />
      </>
    )
  }

  const actionLabel: Record<OverviewActionKey, string> = {
    block: t("overview.action.block"),
    ask: t("overview.action.ask"),
    audit: t("overview.action.audit"),
    inject_context: t("overview.action.inject_context"),
    run_command: t("overview.action.run_command"),
    input_rewrite: t("overview.action.input_rewrite"),
  }

  // OverviewLive owns the headline + KPI grid + chart so the polling
  // path can replace state in place. The Recent activity panel +
  // ledger CTA stay server-rendered (no need to re-fetch on every tick
  // — the 30s tick still pulls fresh aggregate counts; recent rows
  // update via the operator clicking through to /ledger).
  return (
    <>
      <PageHeader title={t("overview.title")} />

      <OverviewLive
        locale={locale}
        initialSummary={initial.summary}
        initialAggregate={initial.aggregate}
        numberFormatLocale={locale === "ko" ? "ko-KR" : "en-US"}
        t={{
          headlineWithActivity: t("overview.headline.withActivity"),
          headlineNoActivity: t("overview.headline.noActivity"),
          detailWithActivity: t("overview.headline.detail"),
          emptyBody: t("overview.empty.body"),
          refreshLabel: t("overview.refresh.label"),
          refreshDisabled: t("overview.refresh.disable"),
          refreshNow: t("overview.refresh.now"),
          chartTitle: t("overview.chart.title"),
          chartEmptyBody: t("overview.chart.emptyBody"),
          actionLabel,
          kpi: {
            activePolicies: t("overview.kpi.activePolicies"),
            activePoliciesValue: t("overview.kpi.activePoliciesValue"),
            activePacks: t("overview.kpi.activePacks"),
            activePacksValue: t("overview.kpi.activePacksValue"),
            scripts: t("overview.kpi.scripts"),
            hitlPending: t("overview.kpi.hitlPending"),
            auditChain: t("overview.auditChain"),
            auditChainOk: t("overview.auditChainOk"),
            auditChainBroken: t("overview.auditChainBroken"),
          },
        }}
      />

      {/* Row 4: recent activity (server-rendered) */}
      <h2 className="text-sm font-medium text-[var(--color-text-primary)] mt-6 mb-2">
        {t("overview.recent.title")}
      </h2>
      <RecentActivity
        rows={toRecentActivityRows(initial.recentRows, 5)}
        emptyBody={t("overview.recent.empty")}
        ctaLabel={t("overview.recent.viewAll")}
        labels={{
          when: t("overview.recent.when"),
          action: t("overview.recent.action"),
          verdict: t("overview.recent.verdict"),
          policy: t("overview.recent.policy"),
          pass: t("overview.verdict.pass"),
          fail: t("overview.verdict.fail"),
          needsReview: t("overview.verdict.needs_review"),
          notApplicable: t("overview.verdict.not_applicable"),
          unknown: t("overview.verdict.unknown"),
        }}
        dtf={dtf}
      />

      {/* Suppress unused-import noise for the fresh-install CTA card.
          The card stays in the tree so a 0-activity install still has
          a one-click jump to /rules. */}
      {initial.summary
       && initial.summary.policies.total === 0
       && (
        <Card className="mt-4">
          <h3 className="text-sm font-medium text-[var(--color-text-primary)] mb-1">
            {t("overview.empty.title")}
          </h3>
          <p className="text-sm text-[var(--color-text-secondary)] mb-3">
            {t("overview.empty.body")}
          </p>
          <Link href="/rules">
            <Button variant="primary">{t("overview.empty.ctaRules")}</Button>
          </Link>
        </Card>
      )}

      <p className="mt-4 text-xs text-[var(--color-text-tertiary)]">
        <Link
          href="/ledger"
          className="font-medium text-[var(--color-accent-light)] hover:underline"
        >
          {t("overview.kpis.openLedger")}
        </Link>
      </p>
    </>
  )
}
