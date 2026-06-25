import Link from "next/link"
import { cloud } from "@/lib/cloud"
import { getIntl, getT } from "@/lib/i18n/server"
import {
  Badge, Button, EmptyState, ErrorState, KPI, PageHeader,
} from "@/components/ui"

export const dynamic = "force-dynamic"

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

type Summary = {
  pending: number
  chainOk: boolean
  ledgerEntries: number
  err?: string
}

async function loadSummary(): Promise<Summary> {
  try {
    // Fetch a large page of the ledger so we can show the actual entry count.
    // The cloud's /ledger endpoint returns at most `limit` rows; we walk
    // forward only as far as needed for the KPI display (cap at 1000).
    const [hitl, ledger] = await Promise.all([
      cloud.listHitl(),
      cloud.ledger(0, 1000),
    ])
    return {
      pending: hitl.length,
      chainOk: ledger.chain_ok,
      ledgerEntries: ledger.entries.length,
    }
  } catch (e: unknown) {
    return { pending: 0, chainOk: false, ledgerEntries: 0, err: errMsg(e) }
  }
}

export default async function Home() {
  const { t } = await getT()
  const { nf } = await getIntl()
  const summary = await loadSummary()

  // D72 follow-up: a true fresh install renders three KPI cards all
  // reading 0/OK with no context for what the surface is for. Detect
  // that case (no HITL items AND no ledger entries) and surface an
  // EmptyState framing instead, pointing the operator at /rules so
  // they can enable a policy. The KPI grid still renders below for
  // continuity once entries start landing.
  const isFreshInstall =
    !summary.err && summary.pending === 0 && summary.ledgerEntries === 0

  return (
    <>
      <PageHeader title={t("overview.title")} />
      {summary.err ? (
        <ErrorState
          title={t("common.cloudUnreachable")}
          body={t("common.seeServerLogs")}
        />
      ) : isFreshInstall ? (
        <EmptyState
          title={t("overview.empty.title")}
          body={t("overview.empty.body")}
          action={
            <Link href="/rules">
              <Button variant="primary">{t("overview.empty.ctaRules")}</Button>
            </Link>
          }
        />
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <KPI
              label={t("overview.pendingReview")}
              value={nf.format(summary.pending)}
            />
            <KPI
              label={t("overview.auditChain")}
              value={
                summary.chainOk
                  ? <Badge variant="ok">{t("overview.auditChainOk")}</Badge>
                  : <Badge variant="deny">{t("overview.auditChainBroken")}</Badge>
              }
            />
            <KPI
              label={t("overview.ledgerEntries")}
              value={nf.format(summary.ledgerEntries)}
            />
          </div>
          {/* D72: link to /ledger so the KPI card row always has an
              actionable next step once data lands. */}
          <p className="mt-4 text-xs text-[var(--color-text-tertiary)]">
            <Link
              href="/ledger"
              className="font-medium text-[var(--color-accent-light)] hover:underline"
            >
              {t("overview.kpis.openLedger")}
            </Link>
          </p>
        </>
      )}
    </>
  )
}
