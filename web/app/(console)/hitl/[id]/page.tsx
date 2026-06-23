import Link from "next/link"
import { cloud, type HitlDetail } from "@/lib/cloud"
import { codeForError } from "@/lib/flash"
import { fmtUtc } from "@/lib/format"
import { getT } from "@/lib/i18n/server"
import {
  Badge, Card, Code, ErrorState, PageHeader,
} from "@/components/ui"

export const dynamic = "force-dynamic"

function StatusTag({ s }: { s: string }) {
  const variant = s === "approved" ? "ok"
                : s === "rejected" ? "deny"
                : "review"
  return <Badge variant={variant}>{s}</Badge>
}

const CITATION_VARIANTS: Record<string, "ok" | "review" | "deny" | "default"> = {
  ok:      "ok",
  review:  "review",
  missing: "deny",
}

function CitationStatusTag({ s }: { s: string }) {
  return <Badge variant={CITATION_VARIANTS[s] ?? "default"}>{s}</Badge>
}

function NliTag({ label, score }: { label?: string; score?: number }) {
  if (!label) return <span className="text-[var(--color-text-tertiary)]">, </span>
  const variant = label === "entailment" ? "ok"
                : label === "contradiction" ? "deny"
                : "review"
  return (
    <span>
      <Badge variant={variant}>{label}</Badge>
      {typeof score === "number" && (
        <span className="ml-2 text-xs text-[var(--color-text-tertiary)]">
          {score.toFixed(2)}
        </span>
      )}
    </span>
  )
}

export default async function HitlDetailPage({
  params,
}: { params: { id: string } }) {
  const { t } = await getT()
  const id = Number(params.id)
  if (!Number.isInteger(id) || id <= 0) {
    return (
      <>
        <p className="mb-3">
          <Link href="/hitl" className="text-sm">{t("hitl.detail.back")}</Link>
        </p>
        <ErrorState
          status={t("hitl.invalidId")}
          title={t("hitl.invalidId")}
        />
      </>
    )
  }

  let detail: HitlDetail | null = null
  let errCode: string | null = null
  try { detail = await cloud.getHitlDetail(id) }
  catch (e: unknown) { errCode = codeForError(e) }

  if (errCode === "not_found") {
    return (
      <>
        <p className="mb-3">
          <Link href="/hitl" className="text-sm">{t("hitl.detail.back")}</Link>
        </p>
        <ErrorState
          status={t("hitl.notFound")}
          title={t("hitl.notFound")}
          body={<>#{id}</>}
        />
      </>
    )
  }
  if (errCode || !detail) {
    return (
      <>
        <p className="mb-3">
          <Link href="/hitl" className="text-sm">{t("hitl.detail.back")}</Link>
        </p>
        <ErrorState
          title={t("common.cloudUnreachable")}
          body={t("common.seeServerLogs")}
        />
      </>
    )
  }

  const cites = detail.payload?.citations ?? []
  return (
    <>
      <p className="mb-3">
        <Link href="/hitl" className="text-sm">{t("hitl.detail.back")}</Link>
      </p>
      <PageHeader title={t("hitl.detail.title", { id: detail.id })} />

      <Card className="mb-6">
        <dl className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-2 text-sm m-0">
          <div>
            <dt className="inline text-[var(--color-text-tertiary)]">matter: </dt>
            <dd className="inline"><Code>{detail.matter}</Code></dd>
          </div>
          <div>
            <dt className="inline text-[var(--color-text-tertiary)]">doc: </dt>
            <dd className="inline"><Code>{detail.doc_id}</Code></dd>
          </div>
          <div>
            <dt className="inline text-[var(--color-text-tertiary)]">reason: </dt>
            <dd className="inline">{detail.reason}</dd>
          </div>
          <div>
            <dt className="inline text-[var(--color-text-tertiary)]">status: </dt>
            <dd className="inline"><StatusTag s={detail.status} /></dd>
          </div>
          <div>
            <dt className="inline text-[var(--color-text-tertiary)]">created: </dt>
            <dd className="inline">{fmtUtc(detail.ts_created)}</dd>
          </div>
          {detail.ts_decided != null && (
            <div>
              <dt className="inline text-[var(--color-text-tertiary)]">decided: </dt>
              <dd className="inline">{fmtUtc(detail.ts_decided)}</dd>
            </div>
          )}
          {detail.approver && (
            <div className="sm:col-span-2 md:col-span-3">
              <dt className="inline text-[var(--color-text-tertiary)]">by: </dt>
              <dd className="inline"><Code>{detail.approver}</Code></dd>
            </div>
          )}
        </dl>
      </Card>

      <h2 className="text-md font-semibold m-0 mb-2">{t("hitl.detail.why")}</h2>
      {cites.length === 0 && (
        <Card className="text-[var(--color-text-tertiary)] text-sm">No citations.</Card>
      )}
      {cites.length > 0 && (
        <Card noPadding className="overflow-x-auto mb-6">
          <table>
            <thead>
              <tr>
                <th>ref</th>
                <th>status</th>
                <th>NLI</th>
                <th>reasons</th>
              </tr>
            </thead>
            <tbody>
              {cites.map((c, i) => (
                <tr key={i}>
                  <td><Code>{c.ref}</Code></td>
                  <td><CitationStatusTag s={c.status} /></td>
                  <td><NliTag label={c.nli_label} score={c.nli_score} /></td>
                  <td className="text-[var(--color-text-tertiary)]">
                    {(c.reasons ?? []).length === 0 ? ", " : (c.reasons ?? []).join("; ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <h2 className="text-md font-semibold m-0 mb-2">
        {t("hitl.detail.ledgerContext", { matter: detail.matter })}
      </h2>
      <p className="text-xs text-[var(--color-text-tertiary)] mb-3">
        {t("hitl.detail.ledgerHint")}
      </p>
      <Card noPadding className="overflow-x-auto">
        <table>
          <thead>
            <tr><th>id</th><th>ts</th><th>verdict</th><th>step</th><th>h</th></tr>
          </thead>
          <tbody>
            {detail.ledger_context.map(e => {
              const isReview = e.body?.verdict === "review" && e.body?.hitl_id === detail!.id
              return (
                <tr
                  key={e.id}
                  aria-current={isReview ? "true" : undefined}
                  className={isReview
                    ? "bg-[var(--color-surface-overlay)] outline-2 outline outline-[var(--color-info-fg)]/40"
                    : undefined}
                >
                  <td>{e.id}</td>
                  <td className="text-[var(--color-text-tertiary)]">{fmtUtc(e.ts)}</td>
                  <td>
                    {String(e.body?.verdict ?? "") &&
                      <CitationStatusTag s={String(e.body?.verdict ?? ", ")} />}
                  </td>
                  <td className="text-[var(--color-text-tertiary)]">
                    {String(e.body?.step ?? ", ")}
                  </td>
                  <td><Code title={e.h}>{e.h.slice(0, 12)}…</Code></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </Card>
    </>
  )
}
