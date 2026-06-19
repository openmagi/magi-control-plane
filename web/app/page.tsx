import { cloud } from "@/lib/cloud"

export const dynamic = "force-dynamic"

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

export default async function Home() {
  let summary: { pending: number; chainOk: boolean; ledgerHeight: number; err?: string }
  try {
    const [hitl, ledger] = await Promise.all([
      cloud.listHitl(),
      cloud.ledger(0, 1),
    ])
    summary = {
      pending: hitl.length,
      chainOk: ledger.chain_ok,
      ledgerHeight: ledger.next_since_id,
    }
  } catch (e: unknown) {
    summary = { pending: 0, chainOk: false, ledgerHeight: 0, err: errMsg(e) }
  }
  return (
    <>
      <h1>Overview</h1>
      {summary.err ? (
        <div className="card">
          <span className="tag deny">cloud unreachable</span>
          <p className="muted" style={{ marginTop: 8 }}>see server logs</p>
        </div>
      ) : (
        <div className="row">
          <div className="card" style={{ flex: 1 }}>
            <div className="muted">Pending review</div>
            <div style={{ fontSize: 28 }}>{summary.pending}</div>
          </div>
          <div className="card" style={{ flex: 1 }}>
            <div className="muted">Audit chain</div>
            <div style={{ fontSize: 28 }}>
              {summary.chainOk
                ? <span className="tag ok">OK</span>
                : <strong style={{ color: "#e07979" }}>BROKEN</strong>}
            </div>
          </div>
          <div className="card" style={{ flex: 1 }}>
            <div className="muted">Ledger height</div>
            <div style={{ fontSize: 28 }}>{summary.ledgerHeight}</div>
          </div>
        </div>
      )}
    </>
  )
}
