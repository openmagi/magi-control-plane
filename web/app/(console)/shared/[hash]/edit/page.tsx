import { revalidatePath } from "next/cache"
import { redirect } from "next/navigation"

import { cloud, type ShareEdits } from "@/lib/cloud"
import { shortTool, shortUrl } from "@/lib/run-view-format"
import { Button, Card, ErrorState, Input, PageHeader, Textarea } from "@/components/ui"

export const dynamic = "force-dynamic"

function itemLabel(it: { kind?: string; text?: string | null; name?: string | null; argsSummary?: unknown }): string {
  if (it.kind === "text") {
    const t = (it.text ?? "").replace(/\s+/g, " ").trim()
    return t.length > 90 ? `${t.slice(0, 89)}…` : t || "(empty)"
  }
  const name = shortTool(it.name ?? "")
  const a = it.argsSummary
  let detail = ""
  if (a && typeof a === "object") {
    const o = a as Record<string, unknown>
    const pick = o.url ?? o.command ?? o.query ?? o.path ?? o.file_path ?? o.symbol
    if (typeof pick === "string") detail = /^https?:\/\//i.test(pick) ? shortUrl(pick, 36) : pick
  }
  return detail ? `${name}(${detail})` : name
}

export default async function ShareEditPage({ params }: { params: { hash: string } }) {
  const hash = params.hash

  let data: { view: Awaited<ReturnType<typeof cloud.getSharedRunForEdit>>["view"]; edits: ShareEdits }
  try {
    const r = await cloud.getSharedRunForEdit(hash)
    data = { view: r.view, edits: r.edits ?? {} }
  } catch {
    return <ErrorState title="Edit shared run" body="Could not load this run (not found, revoked, or cloud unreachable)." />
  }

  const transcript = Array.isArray(data.view.transcript) ? data.view.transcript : []
  const n = transcript.length
  const e = data.edits ?? {}
  const rng = Array.isArray(e.range) ? e.range : [0, Math.max(0, n - 1)]
  const hiddenSet = new Set(Array.isArray(e.hidden) ? e.hidden : [])
  const redactionsText = Array.isArray(e.redactions) ? e.redactions.join("\n") : ""

  async function save(formData: FormData) {
    "use server"
    const num = (k: string, d: number) => {
      const v = Number(formData.get(k))
      return Number.isFinite(v) ? Math.trunc(v) : d
    }
    const start = Math.max(0, num("start", 0))
    const end = Math.min(n - 1, num("end", n - 1))
    const hidden: number[] = []
    for (let i = 0; i < n; i++) if (formData.get(`hide_${i}`) === "on") hidden.push(i)
    const redactions = String(formData.get("redactions") ?? "")
      .split("\n").map((s) => s.trim()).filter(Boolean)

    const edits: ShareEdits = {}
    if (start > 0 || end < n - 1) edits.range = [start, end]
    if (hidden.length) edits.hidden = hidden
    if (redactions.length) edits.redactions = redactions

    try {
      await cloud.setSharedRunEdits(hash, edits)
    } catch {
      // stays on page; reload shows unchanged state
    }
    revalidatePath(`/shared/${hash}/edit`)
    redirect("/shared")
  }

  async function clearEdits() {
    "use server"
    try { await cloud.setSharedRunEdits(hash, {}) } catch { /* ignore */ }
    revalidatePath(`/shared/${hash}/edit`)
    redirect("/shared")
  }

  return (
    <div className="space-y-4">
      <PageHeader title="Trim shared run" description="The link shows the whole session. Keep a range, hide steps, or redact text. Changes apply to the public page; the full export is kept." />

      {n === 0 ? (
        <Card className="text-sm text-[var(--color-text-tertiary)]">
          This run has no step-by-step transcript to trim (older export). You can still add redactions below.
        </Card>
      ) : null}

      <form action={save} className="space-y-4">
        <Card className="space-y-3">
          <div className="text-sm font-semibold">Keep range</div>
          <div className="flex items-center gap-3 text-sm">
            <label className="flex items-center gap-2">from step
              <Input type="number" name="start" min={0} max={Math.max(0, n - 1)} defaultValue={rng[0]} className="w-20" />
            </label>
            <label className="flex items-center gap-2">to step
              <Input type="number" name="end" min={0} max={Math.max(0, n - 1)} defaultValue={rng[1]} className="w-20" />
            </label>
            <span className="text-[var(--color-text-tertiary)]">({n} steps total, 0–{Math.max(0, n - 1)})</span>
          </div>
        </Card>

        {n > 0 ? (
          <Card className="space-y-2">
            <div className="text-sm font-semibold">Hide individual steps</div>
            <ul className="space-y-1 text-sm">
              {transcript.map((it, i) => (
                <li key={i} className="flex items-start gap-2">
                  <input type="checkbox" name={`hide_${i}`} defaultChecked={hiddenSet.has(i)} className="mt-1" />
                  <span className="text-[var(--color-text-tertiary)] w-8 shrink-0">{i}</span>
                  <span className={it.kind === "tool" ? "font-medium" : ""}>
                    {it.kind === "tool" ? "● " : ""}{itemLabel(it)}
                  </span>
                </li>
              ))}
            </ul>
          </Card>
        ) : null}

        <Card className="space-y-2">
          <div className="text-sm font-semibold">Extra redactions</div>
          <div className="text-xs text-[var(--color-text-tertiary)]">One literal phrase per line. Each occurrence becomes [redacted] across the page.</div>
          <Textarea name="redactions" rows={4} defaultValue={redactionsText} placeholder={"account number 1234\ninternal-codename"} />
        </Card>

        <Button type="submit">Save edits</Button>
      </form>

      <form action={clearEdits}>
        <Button type="submit" variant="ghost">Reset to full export</Button>
      </form>
    </div>
  )
}
