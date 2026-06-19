import Link from "next/link"
import { cloud, type HitlDetail } from "@/lib/cloud"
import { codeForError } from "@/lib/flash"
import { fmtUtc } from "@/lib/format"

export const dynamic = "force-dynamic"

function StatusTag({ s }: { s: string }) {
  const cls = s === "approved" ? "tag ok"
            : s === "rejected" ? "tag deny"
            : "tag review"
  return <span className={cls}>{s}</span>
}

function CitationStatusTag({ s }: { s: string }) {
  const cls = s === "ok" ? "tag ok"
            : s === "review" ? "tag review"
            : s === "missing" ? "tag deny"
            : "tag"
  return <span className={cls}>{s}</span>
}

function NliTag({ label, score }: { label?: string; score?: number }) {
  if (!label) return <span className="muted">—</span>
  const cls = label === "entailment" ? "tag ok"
            : label === "contradiction" ? "tag deny"
            : "tag review"
  return (
    <span>
      <span className={cls}>{label}</span>
      {typeof score === "number" && (
        <span className="muted" style={{ marginLeft: 6, fontSize: 11 }}>
          {score.toFixed(2)}
        </span>
      )}
    </span>
  )
}

export default async function HitlDetailPage({
  params,
}: { params: { id: string } }) {
  const id = Number(params.id)
  if (!Number.isInteger(id) || id <= 0) {
    return (
      <>
        <p><Link href="/hitl">← Review queue</Link></p>
        <div className="card" role="alert">
          <span className="tag deny">invalid id</span>
        </div>
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
        <p><Link href="/hitl">← Review queue</Link></p>
        <div className="card" role="alert">
          <span className="tag deny">review item not found</span>
          <p className="muted">#{id}</p>
        </div>
      </>
    )
  }
  if (errCode || !detail) {
    return (
      <>
        <p><Link href="/hitl">← Review queue</Link></p>
        <div className="card" role="alert">
          <span className="tag deny">cloud unreachable</span>
          <p className="muted">see server logs</p>
        </div>
      </>
    )
  }

  const cites = detail.payload?.citations ?? []
  return (
    <>
      <p><Link href="/hitl">← Review queue</Link></p>
      <h1>HITL #{detail.id}</h1>
      <div className="card row" style={{ gap: 18, flexWrap: "wrap" }}>
        <div>matter: <code>{detail.matter}</code></div>
        <div>doc: <code>{detail.doc_id}</code></div>
        <div>reason: {detail.reason}</div>
        <div>status: <StatusTag s={detail.status} /></div>
        <div className="muted">created: {fmtUtc(detail.ts_created)}</div>
        {detail.ts_decided != null && (
          <div className="muted">decided: {fmtUtc(detail.ts_decided)}</div>
        )}
        {detail.approver && <div>by: <code>{detail.approver}</code></div>}
      </div>

      <h2>Why this is in review</h2>
      {cites.length === 0 && (
        <div className="card muted">No citations in payload.</div>
      )}
      {cites.length > 0 && (
        <table className="card">
          <thead>
            <tr>
              <th>ref</th>
              <th>status</th>
              <th>NLI (advisory)</th>
              <th>reasons (why this status)</th>
            </tr>
          </thead>
          <tbody>
            {cites.map((c, i) => (
              <tr key={i}>
                <td><code style={{ overflowWrap: "anywhere" }}>{c.ref}</code></td>
                <td><CitationStatusTag s={c.status} /></td>
                <td><NliTag label={c.nli_label} score={c.nli_score} /></td>
                <td className="muted">
                  {(c.reasons ?? []).length === 0 ? "—" : (c.reasons ?? []).join("; ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2>Ledger context for matter {detail.matter}</h2>
      <p className="muted" style={{ fontSize: 11 }}>
        All ledger entries for this matter, oldest → newest. The review entry
        that produced this HITL item is highlighted.
      </p>
      <table className="card">
        <thead>
          <tr><th>id</th><th>ts</th><th>verdict</th><th>step</th><th>h</th></tr>
        </thead>
        <tbody>
          {detail.ledger_context.map(e => {
            const isReview = e.body?.verdict === "review" && e.body?.hitl_id === detail!.id
            return (
              <tr key={e.id} style={isReview ? { background: "#1c2530" } : undefined}>
                <td>{e.id}</td>
                <td className="muted">{fmtUtc(e.ts)}</td>
                <td>
                  {String(e.body?.verdict ?? "")
                    && <CitationStatusTag s={String(e.body?.verdict ?? "—")} />}
                </td>
                <td className="muted">{String(e.body?.step ?? "—")}</td>
                <td><code title={e.h}>{e.h.slice(0, 12)}…</code></td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </>
  )
}
