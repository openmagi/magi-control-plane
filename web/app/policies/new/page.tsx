import Link from "next/link"
import { revalidatePath } from "next/cache"
import { redirect } from "next/navigation"
import PolicyBuilder from "@/components/PolicyBuilder"
import { codeForError, resolveFlash } from "@/lib/flash"
import { validatePolicyId } from "@/lib/policy-id"
import { validateDraft, type PolicyDraft } from "@/lib/policy-builder"
import { CloudConfigError } from "@/lib/cloud"

export const dynamic = "force-dynamic"

async function saveNewPolicy(formData: FormData) {
  "use server"
  let draft: PolicyDraft
  try { draft = JSON.parse(String(formData.get("draft_json") ?? "{}")) }
  catch { redirect("/policies/new?err=invalid_input"); return }
  const errs = validateDraft(draft)
  if (errs.length > 0) { redirect("/policies/new?err=invalid_input"); return }
  try { validatePolicyId(draft.id) }
  catch { redirect("/policies/new?err=invalid_id"); return }
  const source = String(formData.get("source") ?? "org")
  // Read admin key via the same sentinel pattern lib/cloud.ts uses, so a
  // missing env surfaces as config_error (not as a misleading 401→forbidden).
  let adminKey: string
  try {
    if (!process.env.MAGI_CP_ADMIN_API_KEY) {
      console.error("dashboard server: MAGI_CP_ADMIN_API_KEY not set")
      throw new CloudConfigError()
    }
    adminKey = process.env.MAGI_CP_ADMIN_API_KEY
  } catch (e) {
    redirect(`/policies/new?err=${codeForError(e)}`); return
  }
  const idForUrl = draft.id.split("/").map(encodeURIComponent).join("/")
  try {
    const r = await fetch(`${process.env.MAGI_CP_CLOUD_URL || "http://127.0.0.1:8787"}/policies/${idForUrl}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-Admin-Api-Key": adminKey },
      cache: "no-store",
      body: JSON.stringify({ policy: draft, source, enabled: true }),
    })
    if (!r.ok) {
      console.error(`cloud ${r.status} PUT /policies: ${await r.text().catch(() => "")}`)
      redirect(`/policies/new?err=${codeForError(new Error(`cloud ${r.status}`))}`); return
    }
  } catch (e) {
    redirect(`/policies/new?err=${codeForError(e)}`); return
  }
  revalidatePath("/policies")
  redirect(`/policies/${encodeURI(draft.id)}?msg=saved`)
}

function _parseDraftQuery(draft: string | undefined): import("@/lib/policy-builder").PolicyDraft | null {
  if (!draft) return null
  try {
    const obj = JSON.parse(decodeURIComponent(draft))
    if (typeof obj !== "object" || !obj) return null
    return obj as import("@/lib/policy-builder").PolicyDraft
  } catch { return null }
}

export default function NewPolicyPage({
  searchParams,
}: { searchParams: { err?: string; draft?: string } }) {
  // Use the allowlist; unknown ?err= codes silently drop (no reflected text).
  const flash = resolveFlash(undefined, searchParams.err)
  // v1.2-W1: /policies/compile hands off via ?draft=<encoded IR>. Prefill if present.
  const initial = _parseDraftQuery(searchParams.draft)
  return (
    <>
      <p><Link href="/policies">← Policies</Link></p>
      <h1>New policy {initial && <span className="muted" style={{ fontSize: 12 }}>(prefilled from /compile)</span>}</h1>
      {flash?.kind === "error" && (
        <div className="card" role="alert" aria-live="assertive">
          <span className="tag deny">{flash.text}</span>
        </div>
      )}
      <PolicyBuilder submitAction={saveNewPolicy} initial={initial} />
    </>
  )
}
