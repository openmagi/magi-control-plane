import Link from "next/link"
import { revalidatePath } from "next/cache"
import { redirect } from "next/navigation"
import PolicyBuilder from "@/components/PolicyBuilder"
import { codeForError, resolveFlash } from "@/lib/flash"
import { validatePolicyId } from "@/lib/policy-id"
import { validateDraft, type PolicyDraft } from "@/lib/policy-builder"
import { CloudConfigError, cloud } from "@/lib/cloud"
import { getT } from "@/lib/i18n/server"
import { ErrorState, PageHeader } from "@/components/ui"

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
    const r = await fetch(
      `${process.env.MAGI_CP_CLOUD_URL || "http://127.0.0.1:8787"}/policies/${idForUrl}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-Admin-Api-Key": adminKey },
        cache: "no-store",
        body: JSON.stringify({ policy: draft, source, enabled: true }),
        signal: AbortSignal.timeout(8000),
      },
    )
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

function _parseDraftQuery(
  draft: string | undefined,
): import("@/lib/policy-builder").PolicyDraft | null {
  if (!draft) return null
  try {
    const obj = JSON.parse(decodeURIComponent(draft))
    if (typeof obj !== "object" || !obj) return null
    return obj as import("@/lib/policy-builder").PolicyDraft
  } catch { return null }
}

export default async function NewPolicyPage({
  searchParams,
}: { searchParams: { err?: string; draft?: string } }) {
  const { t } = await getT()
  const flash = resolveFlash(undefined, searchParams.err)
  const initial = _parseDraftQuery(searchParams.draft)

  // Pull wired-step list for the requires datalist (best-effort)
  let wiredSteps: string[] = []
  try {
    const presets = await cloud.listPresets()
    wiredSteps = Array.from(new Set(
      presets.filter(p => p.enforcement === "enforcing" && p.step)
             .map(p => p.step as string),
    )).sort()
  } catch { /* best-effort; empty datalist is fine */ }

  return (
    <>
      <p className="mb-3">
        <Link href="/policies" className="text-sm">{t("newPolicy.back")}</Link>
      </p>
      <PageHeader
        title={initial ? t("newPolicy.titlePrefilled") : t("newPolicy.title")}
      />
      {flash?.kind === "error" && (
        <ErrorState
          status={flash.text}
          title={flash.text}
          severity="error"
        />
      )}
      <PolicyBuilder
        submitAction={saveNewPolicy}
        initial={initial}
        wiredSteps={wiredSteps}
        labels={{
          irFields: "IR fields",
          compiledPreview: "Compiled preview",
          compiledPreviewHint:
            "Live mirror of what the cloud compiler will emit. The cloud is authoritative.",
          id: "id",
          description: "description",
          triggerEvent: "trigger.event",
          triggerMatcher: "trigger.matcher",
          onMissing: "on_missing (decision)",
          sentinelRe: "sentinel_re",
          sentinelReHint:
            "Python regex; must contain (?P<matter>…) and (?P<doc_id>…)",
          requires: "requires (evidence)",
          addRequirement: "add requirement",
          removeRequirement: t("policies.disable"),
          source: t("policies.source"),
          save: t("newPolicy.savePolicy"),
          saving: t("newPolicy.saving"),
          fixIssues: (n) => `Fix ${n} validation ${n === 1 ? "issue" : "issues"}`,
          unsavedWarning: t("newPolicy.unsavedWarning"),
          placeholderId: "legal-filing/v1",
          placeholderMatcher: "Bash | mcp__court__file",
        }}
      />
    </>
  )
}
