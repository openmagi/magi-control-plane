import Link from "next/link"
import { cloud } from "@/lib/cloud"
import { getIntl, getT } from "@/lib/i18n/server"
import {
  Badge, ErrorState, KPI, PageHeader,
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

  return (
    <>
      <PageHeader title={t("overview.title")} />
      {summary.err ? (
        <ErrorState
          title={t("common.cloudUnreachable")}
          body={t("common.seeServerLogs")}
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
          {/* D72: link to /ledger for first-time visitors so the KPI
              card row always has an actionable next step. */}
          <p className="mt-4 text-xs text-[var(--color-text-tertiary)]">
            <Link
              href="/ledger"
              className="font-medium text-[var(--color-accent-light)] hover:underline"
            >
              {t("overview.empty.cta")}
            </Link>
          </p>
        </>
      )}
    </>
  )
}
