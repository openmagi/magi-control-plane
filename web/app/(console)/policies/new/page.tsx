import Link from "next/link"
import { revalidatePath } from "next/cache"
import { redirect } from "next/navigation"
import { ChevronDownIcon } from "@heroicons/react/24/outline"
import PolicyBuilder from "@/components/PolicyBuilder"
import { codeForError, resolveFlash } from "@/lib/flash"
import { validatePolicyId } from "@/lib/policy-id"
import { validateDraft, type PolicyDraft } from "@/lib/policy-builder"
import { CloudConfigError, cloud, type CompileResult } from "@/lib/cloud"
import { getT } from "@/lib/i18n/server"
import {
  Badge, Card, CodeBlock, ErrorState, PageHeader,
  SubmitButton, Textarea,
} from "@/components/ui"

export const dynamic = "force-dynamic"

// ── server actions ──────────────────────────────────────────────────

async function compileNL(formData: FormData): Promise<void> {
  "use server"
  const nl = String(formData.get("nl") ?? "").trim()
  if (!nl) {
    redirect("/policies/new?err=invalid_input&nl=" + encodeURIComponent(nl))
  }
  let result: CompileResult
  try {
    result = await cloud.compilePolicy(nl)
  } catch (e: unknown) {
    redirect(`/policies/new?err=${codeForError(e)}&nl=${encodeURIComponent(nl)}`)
  }
  const payload = JSON.stringify({ nl, ...result })
  // Result includes the full IR JSON + reviewer issues; can blow past the
  // URL length budget. Use a short-lived cookie for the overflow case.
  if (payload.length > 1500) {
    const { cookies } = await import("next/headers")
    cookies().set({
      name: "magi-cp-compile-result",
      value: payload,
      path: "/policies/new",
      sameSite: "lax",
      maxAge: 60 * 5,
    })
    revalidatePath("/policies/new")
    redirect("/policies/new?msg=large")
  }
  revalidatePath("/policies/new")
  redirect(`/policies/new?r=${encodeURIComponent(payload)}`)
}

async function saveNewPolicy(formData: FormData): Promise<void> {
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

// ── result decoders ────────────────────────────────────────────────

function decodeResult(r: string | undefined): (CompileResult & { nl: string }) | null {
  if (!r) return null
  try {
    const obj = JSON.parse(decodeURIComponent(r))
    if (typeof obj !== "object" || !obj || !obj.ir || !obj.review) return null
    return obj as CompileResult & { nl: string }
  } catch { return null }
}

async function readCookieResult(): Promise<(CompileResult & { nl: string }) | null> {
  const { cookies } = await import("next/headers")
  const raw = cookies().get("magi-cp-compile-result")?.value
  if (!raw) return null
  try {
    const obj = JSON.parse(raw)
    if (!obj?.ir || !obj?.review) return null
    return obj as CompileResult & { nl: string }
  } catch { return null }
}

function _parseDraftQuery(draft: string | undefined): PolicyDraft | null {
  if (!draft) return null
  try {
    const obj = JSON.parse(decodeURIComponent(draft))
    if (typeof obj !== "object" || !obj) return null
    return obj as PolicyDraft
  } catch { return null }
}

// ── page ────────────────────────────────────────────────────────────

export default async function NewPolicyPage({
  searchParams,
}: { searchParams: { err?: string; draft?: string; r?: string; msg?: string; nl?: string } }) {
  const { t } = await getT()
  const flash = resolveFlash(undefined, searchParams.err)

  const fromQuery = decodeResult(searchParams.r)
  const compileResult =
    fromQuery ?? (searchParams.msg === "large" ? await readCookieResult() : null)
  const nl = compileResult?.nl ?? searchParams.nl ?? ""

  const initialDraft =
    (compileResult?.ir as PolicyDraft | undefined) ??
    _parseDraftQuery(searchParams.draft) ??
    null

  // Wired steps for the requires datalist (best-effort)
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
        title={initialDraft ? t("newPolicy.titlePrefilled") : t("newPolicy.title")}
        description={t("newPolicy.description")}
      />
      {flash?.kind === "error" && (
        <ErrorState title={flash.text} severity="error" />
      )}

      {/* ── Compose via natural language ─────────────────────────── */}
      <details
        open={!initialDraft}
        className="group rounded-2xl border border-black/[0.06] bg-white overflow-hidden mb-4"
      >
        <summary className="flex items-center gap-3 px-5 py-4 cursor-pointer list-none select-none hover:bg-gray-50/60 transition-colors duration-150">
          <ChevronDownIcon
            aria-hidden="true"
            className="w-4 h-4 text-[var(--color-text-tertiary)] transition-transform duration-200 group-open:rotate-0 -rotate-90"
          />
          <h2 className="text-md font-semibold text-[var(--color-text-primary)] m-0">
            {t("newPolicy.composeNL.title")}
          </h2>
          <span className="text-xs text-[var(--color-text-tertiary)] ml-auto">
            {t("newPolicy.composeNL.hint")}
          </span>
        </summary>
        <form action={compileNL} className="px-5 pb-5 pt-1 border-t border-black/[0.04]">
          <Textarea
            id="nl"
            name="nl"
            rows={4}
            defaultValue={nl}
            label={t("compile.field.label")}
            placeholder={t("compile.field.placeholder")}
            required
            spellCheck={false}
            autoComplete="off"
            monospace
          />
          <div className="mt-3">
            <SubmitButton
              label={t("compile.submit")}
              pendingLabel={t("compile.submit.pending")}
              progressHint={t("compile.progressHint")}
            />
          </div>
        </form>
      </details>

      {compileResult && <CompileResultCards t={t} data={compileResult} />}

      {/* ── Manual IR fields + Save ──────────────────────────────── */}
      <PolicyBuilder
        submitAction={saveNewPolicy}
        initial={initialDraft}
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
          fixIssueOne: "Fix 1 validation issue",
          fixIssueMany: "Fix {n} validation issues",
          unsavedWarning: t("newPolicy.unsavedWarning"),
          placeholderId: "legal-filing/v1",
          placeholderMatcher: "Bash | mcp__court__file",
        }}
      />
    </>
  )
}

// ── compile result cards ──────────────────────────────────────────

function CompileResultCards({
  t, data,
}: {
  data: CompileResult & { nl: string }
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const irJson = JSON.stringify(data.ir, null, 2)
  const hasSchemaIssues = data.schema_issues.length > 0
  return (
    <section aria-labelledby="result-heading" className="space-y-3 mb-5">
      <h2 id="result-heading" className="text-md font-semibold mt-2">
        {t("compile.result.title")}
      </h2>
      <div className="flex items-center gap-2 flex-wrap">
        <Badge variant={data.review.ok ? "ok" : "review"}>
          {data.review.ok
            ? t("compile.result.reviewerOk")
            : t("compile.result.reviewerFlagged")}
        </Badge>
        <Badge variant={hasSchemaIssues ? "deny" : "ok"}>
          {hasSchemaIssues
            ? t("compile.result.schemaIssues", { n: data.schema_issues.length })
            : t("compile.result.schemaClean")}
        </Badge>
      </div>

      <Card>
        <div className="text-xs text-[var(--color-text-tertiary)] mb-2">
          {t("compile.result.irLabel")}
        </div>
        <CodeBlock maxHeight="44vh">{irJson}</CodeBlock>
      </Card>

      {data.review.issues.length > 0 && (
        <Card>
          <div className="text-xs text-[var(--color-text-tertiary)] mb-2">
            {t("compile.result.reviewerIssuesLabel")}
          </div>
          <ul className="m-0 pl-5 text-sm list-disc text-[var(--color-text-secondary)] space-y-1">
            {data.review.issues.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </Card>
      )}

      {hasSchemaIssues && (
        <Card tone="alert" role="alert">
          <div className="text-xs text-[var(--color-deny-fg)] mb-2 font-medium">
            {t("compile.result.schemaIssuesLabel")}
          </div>
          <ul className="m-0 pl-5 text-sm list-disc text-[var(--color-text-secondary)] space-y-1">
            {data.schema_issues.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </Card>
      )}

      <p className="text-xs text-[var(--color-text-tertiary)] italic">
        {t("newPolicy.composeNL.handoffNote")}
      </p>
    </section>
  )
}
