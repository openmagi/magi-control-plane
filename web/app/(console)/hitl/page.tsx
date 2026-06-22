import Link from "next/link"
import { cloud, type HitlItem } from "@/lib/cloud"
import { fmtUtc } from "@/lib/format"
import { revalidatePath, revalidateTag } from "next/cache"
import { redirect } from "next/navigation"
import { getIntl, getT } from "@/lib/i18n/server"
import { WORKSPACE_TAG } from "../_data/workspace"
import {
  Badge, Button, Card, Code, EmptyState, ErrorState, Input, PageHeader,
} from "@/components/ui"

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
  let id: number
  try {
    id = _validateId(formData.get("id"))
    const approver = _validateApprover(formData.get("approver"))
    const note = _validateNote(formData.get("note"))
    await cloud.approve(id, approver, note)
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    redirect(`/hitl?err=${encodeURIComponent(msg)}`)
  }
  // redirect() throws NEXT_REDIRECT — keep out of the try block.
  revalidatePath("/hitl")
  revalidateTag(WORKSPACE_TAG)
  redirect(`/hitl?msg=approved_${id}`)
}

async function reject(formData: FormData) {
  "use server"
  let id: number
  try {
    id = _validateId(formData.get("id"))
    const approver = _validateApprover(formData.get("approver"))
    const note = _validateNote(formData.get("note"))
    await cloud.reject(id, approver, note)
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    redirect(`/hitl?err=${encodeURIComponent(msg)}`)
  }
  revalidatePath("/hitl")
  revalidateTag(WORKSPACE_TAG)
  redirect(`/hitl?msg=rejected_${id}`)
}

const STATUS_VARIANT: Record<string, "ok" | "review" | "deny" | "default"> = {
  ok:      "ok",
  review:  "review",
  deny:    "deny",
  missing: "deny",
}

function StatusTag({ s }: { s: string }) {
  return <Badge variant={STATUS_VARIANT[s] ?? "default"}>{s}</Badge>
}

function ItemCard({
  item, t,
}: {
  item: HitlItem
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const cites = item.payload?.citations ?? []
  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <Link href={`/hitl/${item.id}`} className="font-medium">
          #{item.id}
        </Link>
        <span>matter: <Code>{item.matter}</Code></span>
        <span>doc: <Code>{item.doc_id}</Code></span>
        <span className="text-xs text-[var(--color-text-tertiary)]">
          {item.reason} · {fmtUtc(item.ts_created)}
        </span>
      </div>

      {cites.length > 0 && (
        <div className="overflow-x-auto">
          <table>
            <thead>
              <tr><th>ref</th><th>status</th><th>NLI</th><th>reasons</th></tr>
            </thead>
            <tbody>
              {cites.map((c, i) => (
                <tr key={i}>
                  <td><Code>{c.ref}</Code></td>
                  <td><StatusTag s={c.status} /></td>
                  <td>
                    {c.nli_label
                      ? <span className="text-[var(--color-text-tertiary)]">
                          {c.nli_label}
                          {typeof c.nli_score === "number" && ` · ${c.nli_score.toFixed(2)}`}
                        </span>
                      : <span className="text-[var(--color-text-tertiary)]">—</span>}
                  </td>
                  <td className="text-[var(--color-text-tertiary)]">
                    {(c.reasons ?? []).join("; ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Single combined form: name=action decides approve vs reject */}
      <DecisionForm item={item} t={t} />
    </Card>
  )
}

function DecisionForm({
  item, t,
}: {
  item: HitlItem
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  return (
    <div className="flex flex-wrap items-end gap-2">
      <form action={approve} className="flex flex-wrap items-end gap-2 flex-1 min-w-[260px]">
        <input type="hidden" name="id" value={item.id} />
        <Input
          id={`a-${item.id}`}
          type="email"
          name="approver"
          label={t("hitl.approver")}
          placeholder="partner@firm.example"
          required
          maxLength={256}
          autoComplete="email"
          inputMode="email"
        />
        <Input
          id={`n-${item.id}`}
          type="text"
          name="note"
          label={t("hitl.note")}
          maxLength={2000}
          autoComplete="off"
        />
        <Button type="submit" variant="primary" size="sm">
          {t("hitl.approve")}
        </Button>
      </form>
      <form action={reject} className="flex items-end">
        <input type="hidden" name="id" value={item.id} />
        <input type="hidden" name="approver" value="" />
        <input type="hidden" name="note" value="" />
        {/* This degenerate form is intentional: real reject UX would re-prompt
            for approver+reason in a modal. Keep as a single-shot stub. */}
        <Button type="submit" variant="danger" size="sm" disabled>
          {t("hitl.reject")}
        </Button>
      </form>
    </div>
  )
}

export default async function HitlPage({
  searchParams,
}: { searchParams: { msg?: string; err?: string } }) {
  const { t } = await getT()
  const { nf } = await getIntl()
  let items: HitlItem[]
  let err: string | null = null
  try { items = await cloud.listHitl() }
  catch (e: unknown) { items = []; err = e instanceof Error ? e.message : String(e) }
  return (
    <>
      <PageHeader title={t("hitl.title", { n: nf.format(items.length) })} />

      {searchParams.msg && (
        <Card role="status" className="mb-3">
          <Badge variant="ok">{t("hitl.recorded", { what: searchParams.msg })}</Badge>
        </Card>
      )}
      {searchParams.err && (
        <ErrorState status="action" title={searchParams.err} severity="warning" />
      )}
      {err && (
        <ErrorState
          title={t("common.cloudUnreachable")}
          body={err}
        />
      )}
      {items.length === 0 && !err && (
        <EmptyState title={t("hitl.empty")} />
      )}
      <div className="space-y-3">
        {items.map(item => <ItemCard key={item.id} item={item} t={t} />)}
      </div>
    </>
  )
}
