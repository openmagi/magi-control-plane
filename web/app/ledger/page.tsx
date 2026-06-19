import { cloud } from "@/lib/cloud"
import { fmtUtc, clampNonNegInt, LEDGER_PAGE_SIZE } from "@/lib/format"

export const dynamic = "force-dynamic"

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

export default async function LedgerPage({
  searchParams,
}: { searchParams: { since?: string } }) {
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
      <h1>Audit ledger</h1>
      {err && (
        <div className="card">
          <span className="tag deny">cloud unreachable</span>
          <p className="muted" style={{ marginTop: 8 }}>{err}</p>
          <p className="muted"><a href="/ledger">Retry</a></p>
        </div>
      )}
      {result && (
        <>
          <div className="card row">
            <div>
              chain integrity:{" "}
              {result.chain_ok
                ? <span className="tag ok">OK</span>
                : <strong style={{ color: "#e07979" }}>BROKEN — investigate immediately</strong>}
            </div>
            <div className="muted">UTC · cursor: {result.next_since_id}</div>
          </div>
          {result.entries.length === 0 ? (
            <div className="card muted">감사 항목이 없습니다.</div>
          ) : (
            <table className="card">
              <thead>
                <tr><th>id</th><th>ts (UTC)</th><th>matter</th><th>prev</th><th>h</th></tr>
              </thead>
              <tbody>
                {result.entries.map(e => (
                  <tr key={e.id}>
                    <td>{e.id}</td>
                    <td className="muted">{fmtUtc(e.ts)}</td>
                    <td><code>{e.matter}</code></td>
                    <td><code title={e.prev}>{e.prev ? e.prev.slice(0, 12) + "…" : "∅"}</code></td>
                    <td><code title={e.h}>{e.h.slice(0, 12)}…</code></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="muted" style={{ fontSize: 11 }}>
            Note: entry bodies are redacted in this view (P5 v0). Drill-down requires
            an authenticated audit token (deferred to v0.1).
          </p>
          <p className="row">
            {since > 0 && <a href="/ledger">← First</a>}
            {result.entries.length === LEDGER_PAGE_SIZE
              && result.next_since_id !== since && (
              <a href={`/ledger?since=${result.next_since_id}`}>Next →</a>
            )}
          </p>
        </>
      )}
    </>
  )
}
