import Link from "next/link"
import { cloud, type HitlItem } from "@/lib/cloud"
import { fmtUtc } from "@/lib/format"
import { revalidatePath } from "next/cache"
import { redirect } from "next/navigation"

export const dynamic = "force-dynamic"

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

function _validateApprover(s: unknown): string {
  if (typeof s !== "string") throw new Error("approver missing")
  const t = s.trim()
  if (!t) throw new Error("approver required")
  if (t.length > 256) throw new Error("approver too long")
  if (!EMAIL_RE.test(t)) throw new Error("approver must be an email")
  return t
}

function _validateNote(s: unknown): string | undefined {
  if (s == null || s === "") return undefined
  if (typeof s !== "string") throw new Error("note bad type")
  if (s.length > 2000) throw new Error("note too long")
  return s
}

function _validateId(v: unknown): number {
  const n = Number(v)
  if (!Number.isInteger(n) || n <= 0) throw new Error("bad id")
  return n
}

async function approve(formData: FormData) {
  "use server"
  try {
    const id = _validateId(formData.get("id"))
    const approver = _validateApprover(formData.get("approver"))
    const note = _validateNote(formData.get("note"))
    await cloud.approve(id, approver, note)
    revalidatePath("/hitl")
    redirect(`/hitl?msg=approved_${id}`)
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    redirect(`/hitl?err=${encodeURIComponent(msg)}`)
  }
}

async function reject(formData: FormData) {
  "use server"
  try {
    const id = _validateId(formData.get("id"))
    const approver = _validateApprover(formData.get("approver"))
    const note = _validateNote(formData.get("note"))
    await cloud.reject(id, approver, note)
    revalidatePath("/hitl")
    redirect(`/hitl?msg=rejected_${id}`)
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    redirect(`/hitl?err=${encodeURIComponent(msg)}`)
  }
}

const ALLOWED_STATUS = new Set(["ok", "review", "deny", "missing"])

function StatusTag({ s }: { s: string }) {
  const cls = ALLOWED_STATUS.has(s) ? s : ""
  return <span className={`tag ${cls}`}>{s}</span>
}

function ItemCard({ item }: { item: HitlItem }) {
  const cites = item.payload?.citations ?? []
  return (
    <div className="card">
      <div>
        <Link href={`/hitl/${item.id}`}><strong>#{item.id}</strong></Link>{" "}
        <code>matter={item.matter}</code>{" "}
        <code>doc={item.doc_id}</code>
        <div className="muted" style={{ wordBreak: "break-word" }}>
          reason: {item.reason} · {fmtUtc(item.ts_created)}
        </div>
      </div>
      {cites.length > 0 && (
        <table style={{ marginTop: 12 }}>
          <thead>
            <tr><th>ref</th><th>status</th><th>NLI (advisory)</th><th>reasons</th></tr>
          </thead>
          <tbody>
            {cites.map((c, i) => (
              <tr key={i}>
                <td><code>{c.ref}</code></td>
                <td><StatusTag s={c.status} /></td>
                <td>
                  {c.nli_label
                    ? <span className="muted">{c.nli_label}
                        {typeof c.nli_score === "number" && ` · ${c.nli_score.toFixed(2)}`}</span>
                    : <span className="muted">—</span>}
                </td>
                <td className="muted">{(c.reasons ?? []).join("; ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <form action={approve} style={{ marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input type="hidden" name="id" value={item.id} />
        <label className="muted" htmlFor={`a-${item.id}`}>approver</label>
        <input id={`a-${item.id}`} type="email" name="approver"
               placeholder="partner@firm.example" required maxLength={256} />
        <label className="muted" htmlFor={`n-${item.id}`}>note</label>
        <input id={`n-${item.id}`} type="text" name="note"
               placeholder="optional" maxLength={2000} />
        <button className="primary" type="submit">Approve</button>
      </form>
      <form action={reject} style={{ marginTop: 8, display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input type="hidden" name="id" value={item.id} />
        <input type="email" name="approver" placeholder="reviewer@firm.example" required maxLength={256}
               aria-label="reject as" />
        <input type="text" name="note" placeholder="reason for rejection" maxLength={2000}
               aria-label="reject note" />
        <button className="danger" type="submit">Reject</button>
      </form>
    </div>
  )
}

export default async function HitlPage({
  searchParams,
}: { searchParams: { msg?: string; err?: string } }) {
  let items: HitlItem[]
  let err: string | null = null
  try { items = await cloud.listHitl() }
  catch (e: unknown) { items = []; err = e instanceof Error ? e.message : String(e) }
  return (
    <>
      <h1>Pending review queue ({items.length})</h1>
      {searchParams.msg && (
        <div className="card"><span className="tag ok">recorded: {searchParams.msg}</span></div>
      )}
      {searchParams.err && (
        <div className="card"><span className="tag deny">action error</span>
          <p className="muted">{searchParams.err}</p></div>
      )}
      {err && (
        <div className="card"><span className="tag deny">cloud unreachable</span>
          <p className="muted">{err}</p></div>
      )}
      {items.length === 0 && !err && (
        <div className="card muted">대기 항목이 없습니다.</div>
      )}
      {items.map(item => <ItemCard key={item.id} item={item} />)}
    </>
  )
}
