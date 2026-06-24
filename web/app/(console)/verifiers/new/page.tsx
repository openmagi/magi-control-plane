import Link from "next/link"
import { redirect } from "next/navigation"
import { ArrowLeftIcon } from "@heroicons/react/24/outline"
import { codeForError, resolveFlash } from "@/lib/flash"
import { CloudConfigError } from "@/lib/cloud"
import { getT } from "@/lib/i18n/server"
import { Card, ErrorState, PageHeader } from "@/components/ui"
import VerifierFormClient, {
  type TriggerRow,
  type FieldCheckRow,
} from "./_components/VerifierFormClient"

export const dynamic = "force-dynamic"

/**
 * D52b: /verifiers/new (step-only custom verifier authoring).
 *
 * Design lock: this page accepts ONLY step-kind verifier definitions.
 * regex / llm_critic / shacl checks stay inline in the policy wizard
 * (/policies/new Step 3) because they bind to a specific (event,
 * matcher, sentinel) triple a generic /verifiers row could not capture.
 *
 * v1 body_type is `preview` only. The runtime returns a not_applicable
 * verdict when a policy binds to a custom verifier. Real-code bodies
 * (signed-WASM / Python plug-in) are deferred to v2.
 *
 * Wire: client form serializes the four sections into a JSON `payload`
 * hidden input, the server action below validates locally (cheap reject
 * before the network hop) then POSTs to the cloud's /custom-verifiers.
 * The cloud re-validates against custom_verifier_store.build_from_dict
 * so a hand-rolled client cannot bypass slug/length/verdict checks.
 */

const NAME_RE = /^[a-z][a-z0-9_]*$/
const MAX_NAME_LEN = 64
const MAX_DESCRIPTION_LEN = 500
const ALLOWED_VERDICTS: ReadonlyArray<string> = [
  "pass",
  "fail",
  "needs_review",
  "not_applicable",
]
const ALLOWED_MATCHER_CLASSES: ReadonlyArray<string> = ["tool", "no_tool", "final"]

type CreateVerifierPayload = {
  name: string
  description: string
  triggers: TriggerRow[]
  verdict_set: string[]
  body_type: "preview"
  // D52d: per-field check rows (>=1). Each row is a (path, description)
  // pair documenting what this verifier inspects on each fire.
  field_checks: FieldCheckRow[]
}

const MAX_FIELD_CHECK_PATH_LEN = 128
const MAX_FIELD_CHECK_DESC_LEN = 200

function parseDraftPayload(raw: unknown): CreateVerifierPayload | null {
  if (typeof raw !== "string" || !raw) return null
  try {
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== "object") return null
    const name = String((parsed as Record<string, unknown>).name ?? "")
    const description = String((parsed as Record<string, unknown>).description ?? "")
    const triggersRaw = (parsed as Record<string, unknown>).triggers
    const verdictsRaw = (parsed as Record<string, unknown>).verdict_set
    const body_type = String((parsed as Record<string, unknown>).body_type ?? "preview")
    if (body_type !== "preview") return null
    if (!Array.isArray(triggersRaw)) return null
    const triggers: TriggerRow[] = []
    for (const t of triggersRaw) {
      if (!t || typeof t !== "object") return null
      const event = String((t as Record<string, unknown>).event ?? "")
      const matcher_class = String(
        (t as Record<string, unknown>).matcher_class ?? "",
      )
      if (!event) return null
      if (!ALLOWED_MATCHER_CLASSES.includes(matcher_class)) return null
      triggers.push({ event, matcher_class: matcher_class as TriggerRow["matcher_class"] })
    }
    if (!Array.isArray(verdictsRaw)) return null
    const verdict_set: string[] = []
    for (const v of verdictsRaw) {
      const s = String(v)
      if (!ALLOWED_VERDICTS.includes(s)) return null
      verdict_set.push(s)
    }
    // D52d: parse field_checks rows. >=1 required at validateLocally.
    const fieldChecksRaw = (parsed as Record<string, unknown>).field_checks
    if (!Array.isArray(fieldChecksRaw)) return null
    const field_checks: FieldCheckRow[] = []
    for (const fc of fieldChecksRaw) {
      if (!fc || typeof fc !== "object") return null
      const path = String((fc as Record<string, unknown>).path ?? "").trim()
      const desc = String(
        (fc as Record<string, unknown>).check_description ?? "",
      ).trim()
      if (!path || path.length > MAX_FIELD_CHECK_PATH_LEN) return null
      if (!desc || desc.length > MAX_FIELD_CHECK_DESC_LEN) return null
      field_checks.push({ path, check_description: desc })
    }
    return {
      name, description, triggers, verdict_set,
      body_type: "preview", field_checks,
    }
  } catch {
    return null
  }
}

function validateLocally(p: CreateVerifierPayload): string | null {
  if (!p.name) return "invalid_input"
  if (p.name.length > MAX_NAME_LEN) return "invalid_input"
  if (!NAME_RE.test(p.name)) return "invalid_input"
  if (!p.description.trim()) return "invalid_input"
  if (p.description.length > MAX_DESCRIPTION_LEN) return "invalid_input"
  if (p.triggers.length === 0) return "invalid_input"
  if (p.verdict_set.length === 0) return "invalid_input"
  if (p.field_checks.length === 0) return "invalid_input"
  for (const fc of p.field_checks) {
    if (!fc.path || fc.path.length > MAX_FIELD_CHECK_PATH_LEN) return "invalid_input"
    if (!fc.check_description) return "invalid_input"
    if (fc.check_description.length > MAX_FIELD_CHECK_DESC_LEN) return "invalid_input"
  }
  return null
}

async function createVerifierAction(formData: FormData): Promise<void> {
  "use server"
  const raw = formData.get("payload")
  const parsed = parseDraftPayload(raw)
  if (parsed === null) {
    redirect("/verifiers/new?err=invalid_input")
    return
  }
  const localErr = validateLocally(parsed)
  if (localErr) {
    redirect(`/verifiers/new?err=${localErr}`)
    return
  }
  let apiKey: string
  try {
    if (!process.env.MAGI_CP_API_KEY) {
      console.error("dashboard server: MAGI_CP_API_KEY not set")
      throw new CloudConfigError()
    }
    apiKey = process.env.MAGI_CP_API_KEY
  } catch (e) {
    redirect(`/verifiers/new?err=${codeForError(e)}`)
    return
  }
  try {
    const r = await fetch(
      `${process.env.MAGI_CP_CLOUD_URL || "http://127.0.0.1:8787"}/custom-verifiers`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Api-Key": apiKey },
        cache: "no-store",
        body: JSON.stringify(parsed),
        signal: AbortSignal.timeout(8000),
      },
    )
    if (!r.ok) {
      console.error(
        `cloud ${r.status} POST /custom-verifiers: ${await r.text().catch(() => "")}`,
      )
      redirect(`/verifiers/new?err=${codeForError(new Error(`cloud ${r.status}`))}`)
      return
    }
  } catch (e) {
    redirect(`/verifiers/new?err=${codeForError(e)}`)
    return
  }
  redirect("/rules?tab=evidence&msg=verifier_created")
}

export default async function NewCustomVerifierPage({
  searchParams,
}: {
  searchParams: { err?: string; msg?: string }
}) {
  const { t } = await getT()
  const flash = resolveFlash(searchParams.msg, searchParams.err)

  const labels = {
    name: t("verifiers.new.name"),
    nameHelper: t("verifiers.new.name.helper"),
    description: t("verifiers.new.description"),
    descriptionHelper: t("verifiers.new.description.helper"),
    triggers: t("verifiers.new.triggers"),
    triggersHelper: t("verifiers.new.triggers.helper"),
    triggerEvent: t("verifiers.new.trigger.event"),
    triggerMatcher: t("verifiers.new.trigger.matcher"),
    triggerAdd: t("verifiers.new.trigger.add"),
    triggerRemove: t("verifiers.new.trigger.remove"),
    verdictSet: t("verifiers.new.verdictSet"),
    verdictSetHelper: t("verifiers.new.verdictSet.helper"),
    bodyType: t("verifiers.new.bodyType"),
    bodyTypePreview: t("verifiers.new.bodyType.preview"),
    submit: t("verifiers.new.submit"),
    submitPending: t("verifiers.new.submit.pending"),
    errName: t("verifiers.new.err.name"),
    errNameSlug: t("verifiers.new.err.nameSlug"),
    errDescription: t("verifiers.new.err.description"),
    errTriggers: t("verifiers.new.err.triggers"),
    errVerdicts: t("verifiers.new.err.verdicts"),
    fieldChecks: t("verifiers.new.fieldChecks"),
    fieldChecksHelper: t("verifiers.new.fieldChecks.helper"),
    fieldCheckPath: t("verifiers.new.fieldChecks.path"),
    fieldCheckDescription: t("verifiers.new.fieldChecks.description"),
    fieldCheckAdd: t("verifiers.new.fieldChecks.add"),
    fieldCheckRemove: t("verifiers.new.fieldChecks.remove"),
    errFieldChecks: t("verifiers.new.err.fieldChecks"),
  }

  return (
    <>
      <PageHeader
        title={t("verifiers.new.title")}
        description={t("verifiers.new.description.page")}
        actions={
          <Link
            href="/rules?tab=evidence"
            className="inline-flex items-center gap-1.5 text-sm font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] hover:no-underline"
          >
            <ArrowLeftIcon className="h-4 w-4" aria-hidden />
            {t("verifiers.new.back")}
          </Link>
        }
      />

      {flash?.kind === "error" && (
        <ErrorState title={flash.text} severity="error" />
      )}

      <Card className="max-w-2xl">
        <form action={createVerifierAction} className="space-y-4" noValidate>
          <VerifierFormClient labels={labels} />
        </form>
      </Card>
    </>
  )
}
