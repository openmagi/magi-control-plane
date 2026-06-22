import Link from "next/link"
import { cloud } from "@/lib/cloud"
import { fmtUtc, clampNonNegInt, LEDGER_PAGE_SIZE } from "@/lib/format"
import { getIntl, getT } from "@/lib/i18n/server"
import {
  Badge, Card, Code, EmptyState, ErrorState, PageHeader,
} from "@/components/ui"

export const dynamic = "force-dynamic"

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

export default async function LedgerPage({
  searchParams,
}: { searchParams: { since?: string } }) {
  const { t } = await getT()
  const { nf } = await getIntl()
  const since = clampNonNegInt(searchParams.since, 0)
  let result: Awaited<ReturnType<typeof cloud.ledger>> | null = null
  let err: string | null = null
  try {
    result = await cloud.ledger(since, LEDGER_PAGE_SIZE)
  } catch (e: unknown) {
    err = errMsg(e)
  }

  return (
    <>
      <PageHeader title={t("ledger.title")} />

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
            <EmptyState title={t("ledger.empty")} />
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
                    <th>{t("ledger.col.matter")}</th>
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
                      <td><Code>{e.matter}</Code></td>
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
              <Link href="/ledger" aria-label="First page">
                {t("ledger.first")}
              </Link>
            )}
            {result.entries.length === LEDGER_PAGE_SIZE
              && result.next_since_id !== since && (
              <Link
                href={`/ledger?since=${result.next_since_id}`}
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
