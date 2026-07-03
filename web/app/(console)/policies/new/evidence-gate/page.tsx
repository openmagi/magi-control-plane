import Link from "next/link"
import { redirect } from "next/navigation"

import { ArrowLeftIcon } from "@heroicons/react/24/outline"

import {
  buildEvidenceGateCompoundDraft,
  validateEvidenceGateDraft,
  type EvidenceGateDraft,
} from "@/lib/evidence-gate-builder"
import { codeForError, resolveFlash } from "@/lib/flash"
import { CloudConfigError } from "@/lib/cloud"
import { getT } from "@/lib/i18n/server"
import { Card, ErrorState, PageHeader } from "@/components/ui"

import EvidenceGateForm from "./EvidenceGateForm"

export const dynamic = "force-dynamic"

const BASE = "/policies/new/evidence-gate"

async function saveEvidenceGate(formData: FormData): Promise<void> {
  "use server"
  let draft: EvidenceGateDraft
  try { draft = JSON.parse(String(formData.get("draft_json") ?? "{}")) }
  catch { redirect(`${BASE}?err=invalid_input`); return }

  if (validateEvidenceGateDraft(draft).length > 0) {
    redirect(`${BASE}?err=invalid_input`); return
  }
  let adminKey: string
  try {
    if (!process.env.MAGI_CP_ADMIN_API_KEY) {
      console.error("dashboard server: MAGI_CP_ADMIN_API_KEY not set")
      throw new CloudConfigError()
    }
    adminKey = process.env.MAGI_CP_ADMIN_API_KEY
  } catch (e) {
    redirect(`${BASE}?err=${codeForError(e)}`); return
  }

  // Author ONE policy (the compound), which the server expands into its member
  // rules and persists atomically as an owning policy.
  const compound = buildEvidenceGateCompoundDraft(draft)
  let r: Response
  try {
    r = await fetch(
      `${process.env.MAGI_CP_CLOUD_URL || "http://127.0.0.1:8787"}/policies/compound`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Admin-Api-Key": adminKey },
        cache: "no-store",
        body: JSON.stringify({ draft: compound, source: "org", enabled: true }),
        signal: AbortSignal.timeout(8000),
      },
    )
  } catch (e) { redirect(`${BASE}?err=${codeForError(e)}`); return }
  if (!r.ok) {
    console.error(`cloud ${r.status} POST /policies/compound: ${await r.text().catch(() => "")}`)
    redirect(`${BASE}?err=${codeForError(new Error(`cloud ${r.status}`))}`); return
  }
  redirect("/rules?flash=policy_saved")
}

export default async function EvidenceGatePage({
  searchParams,
}: { searchParams: Promise<{ err?: string }> }) {
  const { t } = await getT()
  const sp = await searchParams
  const flash = resolveFlash(undefined, sp.err)

  if (!process.env.MAGI_CP_ADMIN_API_KEY) {
    return <ErrorState title="Evidence gate" body="MAGI_CP_ADMIN_API_KEY is not set on the dashboard server." />
  }

  return (
    <div className="space-y-4">
      <Link href="/rules" className="inline-flex items-center gap-1 text-sm text-[var(--color-text-tertiary)]">
        <ArrowLeftIcon className="w-4 h-4" /> Back
      </Link>
      <PageHeader
        title={t("evidenceGate.title") || "New evidence gate"}
        description={t("evidenceGate.description") || "Require that a check passed earlier in the session before a high-risk tool runs. Authors an audit policy that records the evidence plus a gate policy that requires it."}
      />
      {flash ? <Card className="text-sm text-[var(--color-danger)]">{flash.text}</Card> : null}
      <EvidenceGateForm action={saveEvidenceGate} />
    </div>
  )
}
