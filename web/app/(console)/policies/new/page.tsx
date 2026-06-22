import Link from "next/link"
import { revalidatePath } from "next/cache"
import { redirect } from "next/navigation"
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

type Mode = "nl" | "advanced"

// ── server actions ──────────────────────────────────────────────────

async function compileNL(formData: FormData): Promise<void> {
  "use server"
  const nl = String(formData.get("nl") ?? "").trim()
  if (!nl) {
    redirect("/policies/new?mode=nl&err=invalid_input&nl=" + encodeURIComponent(nl))
  }
  let result: CompileResult
  try {
    result = await cloud.compilePolicy(nl)
  } catch (e: unknown) {
    redirect(`/policies/new?mode=nl&err=${codeForError(e)}&nl=${encodeURIComponent(nl)}`)
  }
  const payload = JSON.stringify({ nl, ...result })
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
    redirect("/policies/new?mode=nl&msg=large")
  }
  revalidatePath("/policies/new")
  redirect(`/policies/new?mode=nl&r=${encodeURIComponent(payload)}`)
}

async function persistDraft(draft: PolicyDraft, source: string): Promise<void> {
  const errs = validateDraft(draft)
  if (errs.length > 0) { redirect("/policies/new?err=invalid_input"); return }
  try { validatePolicyId(draft.id) }
  catch { redirect("/policies/new?err=invalid_id"); return }
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
  try {
    const { cookies } = await import("next/headers")
    cookies().delete("magi-cp-compile-result")
  } catch { /* no-op */ }
  revalidatePath("/policies")
  redirect(`/policies/${encodeURI(draft.id)}?msg=saved`)
}

async function saveCompiled(formData: FormData): Promise<void> {
  "use server"
  let draft: PolicyDraft
  try { draft = JSON.parse(String(formData.get("ir_json") ?? "{}")) }
  catch { redirect("/policies/new?err=invalid_input"); return }
  const source = String(formData.get("source") ?? "org")
  await persistDraft(draft, source)
}

async function saveAdvanced(formData: FormData): Promise<void> {
  "use server"
  let draft: PolicyDraft
  try { draft = JSON.parse(String(formData.get("draft_json") ?? "{}")) }
  catch { redirect("/policies/new?err=invalid_input"); return }
  const source = String(formData.get("source") ?? "org")
  await persistDraft(draft, source)
}

// ── decoders ────────────────────────────────────────────────────────

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
}: { searchParams: { err?: string; draft?: string; r?: string; msg?: string; nl?: string; mode?: string } }) {
  const { t } = await getT()
  const flash = resolveFlash(undefined, searchParams.err)

  // Determine mode. Legacy /policies/compile?draft=... hand-off opens advanced.
  const mode: Mode =
    searchParams.mode === "advanced" ||
    (searchParams.mode === undefined && searchParams.draft != null)
      ? "advanced"
      : "nl"

  const fromQuery = decodeResult(searchParams.r)
  const compileResult =
    fromQuery ?? (searchParams.msg === "large" ? await readCookieResult() : null)
  const nl = compileResult?.nl ?? searchParams.nl ?? ""

  const initialDraft =
    (compileResult?.ir as PolicyDraft | undefined) ??
    _parseDraftQuery(searchParams.draft) ??
    null

  // Wired steps for the requires datalist — only fetched when advanced mode
  // is active (the NL mode doesn't show that input).
  let wiredSteps: string[] = []
  if (mode === "advanced") {
    try {
      const presets = await cloud.listPresets()
      wiredSteps = Array.from(new Set(
        presets.filter(p => p.enforcement === "enforcing" && p.step)
               .map(p => p.step as string),
      )).sort()
    } catch { /* best-effort; empty datalist is fine */ }
  }

  return (
    <>
      <p className="mb-3">
        <Link href="/policies" className="text-sm">{t("newPolicy.back")}</Link>
      </p>
      <PageHeader
        title={t("newPolicy.title")}
        description={t("newPolicy.description")}
      />
      {flash?.kind === "error" && (
        <ErrorState title={flash.text} severity="error" />
      )}

      <ModeTabs t={t} mode={mode} />

      {mode === "nl" ? (
        <>
          <Card>
            <h2 className="text-md font-semibold m-0 mb-3">
              {t("newPolicy.composeNL.title")}
            </h2>
            <form action={compileNL}>
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
              <div className="mt-3 flex items-center gap-2">
                <SubmitButton
                  label={t("compile.submit")}
                  pendingLabel={t("compile.submit.pending")}
                  progressHint={t("compile.progressHint")}
                />
                {compileResult && (
                  <Link href="/policies/new?mode=nl" className="text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]">
                    {t("newPolicy.composeNL.clear")}
                  </Link>
                )}
              </div>
            </form>
          </Card>

          {compileResult && (
            <CompileResultBlock t={t} data={compileResult} saveAction={saveCompiled} />
          )}
        </>
      ) : (
        <Card>
          <h2 className="text-md font-semibold m-0 mb-3">
            {t("newPolicy.advanced.title")}
          </h2>
          <p className="text-xs text-[var(--color-text-tertiary)] mb-4">
            {t("newPolicy.advanced.hint")}
          </p>
          <PolicyBuilder
            submitAction={saveAdvanced}
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
        </Card>
      )}
    </>
  )
}

// ── tabs ────────────────────────────────────────────────────────────

function ModeTabs({
  t, mode,
}: {
  mode: Mode
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  return (
    <div
      role="tablist"
      aria-label={t("newPolicy.modeLabel")}
      className="inline-flex items-center gap-1 rounded-full border border-black/[0.06] bg-white p-1 mb-4"
    >
      <ModeTab
        href="/policies/new?mode=nl"
        active={mode === "nl"}
        label={t("newPolicy.mode.nl")}
        sub={t("newPolicy.mode.nlSub")}
      />
      <ModeTab
        href="/policies/new?mode=advanced"
        active={mode === "advanced"}
        label={t("newPolicy.mode.advanced")}
        sub={t("newPolicy.mode.advancedSub")}
      />
    </div>
  )
}

function ModeTab({
  href, active, label, sub,
}: { href: string; active: boolean; label: string; sub: string }) {
  return (
    <Link
      role="tab"
      aria-selected={active}
      href={href}
      className={
        active
          ? "inline-flex items-center gap-2 rounded-full px-4 py-1.5 text-sm font-semibold bg-[var(--color-accent)] text-white shadow-sm"
          : "inline-flex items-center gap-2 rounded-full px-4 py-1.5 text-sm font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] hover:bg-gray-50"
      }
    >
      {label}
      <span
        className={
          active
            ? "text-[10px] font-medium uppercase tracking-wider opacity-80"
            : "text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]"
        }
      >
        {sub}
      </span>
    </Link>
  )
}

// ── compile result + direct save ────────────────────────────────

function CompileResultBlock({
  t, data, saveAction,
}: {
  data: CompileResult & { nl: string }
  saveAction: (fd: FormData) => Promise<void>
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const irJson = JSON.stringify(data.ir, null, 2)
  const hasSchemaIssues = data.schema_issues.length > 0
  const canSave = data.review.ok && !hasSchemaIssues
  const draft = data.ir as unknown as PolicyDraft

  return (
    <Card className="mt-3 border-[var(--color-accent)]/20 bg-gradient-to-br from-[var(--color-accent)]/[0.02] to-white">
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <h2 className="text-md font-semibold m-0">
          {t("compile.result.title")}
        </h2>
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

      <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1.5 text-sm mb-3">
        <dt className="text-[var(--color-text-tertiary)] text-xs uppercase tracking-wider font-semibold pt-0.5">id</dt>
        <dd className="font-mono text-[13px]" translate="no">{draft.id}</dd>
        <dt className="text-[var(--color-text-tertiary)] text-xs uppercase tracking-wider font-semibold pt-0.5">trigger</dt>
        <dd><code className="font-mono">{draft.trigger.event}</code> · <code className="font-mono">{draft.trigger.matcher}</code></dd>
        {draft.requires && draft.requires.length > 0 && (
          <>
            <dt className="text-[var(--color-text-tertiary)] text-xs uppercase tracking-wider font-semibold pt-0.5">requires</dt>
            <dd className="text-[var(--color-text-secondary)] text-xs">
              {draft.requires.map(r => `${r.step}=${r.verdict}`).join(", ")}
            </dd>
          </>
        )}
        <dt className="text-[var(--color-text-tertiary)] text-xs uppercase tracking-wider font-semibold pt-0.5">on_missing</dt>
        <dd className="text-[var(--color-text-secondary)]">{draft.on_missing}</dd>
      </dl>

      <details className="mb-3 rounded-lg bg-gray-50/70 p-2">
        <summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)]">
          {t("compile.result.irLabel")}
        </summary>
        <CodeBlock maxHeight="44vh" className="mt-2">{irJson}</CodeBlock>
      </details>

      {data.review.issues.length > 0 && (
        <div className="mb-3">
          <p className="text-xs font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)] mb-1.5">
            {t("compile.result.reviewerIssuesLabel")}
          </p>
          <ul className="m-0 pl-5 text-xs list-disc text-[var(--color-text-secondary)] space-y-1 leading-relaxed">
            {data.review.issues.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </div>
      )}

      {hasSchemaIssues && (
        <div className="mb-3 rounded-lg border border-[var(--color-deny-fg)]/20 bg-[var(--color-deny-bg)]/60 p-3" role="alert">
          <p className="text-xs font-semibold uppercase tracking-wider text-[var(--color-deny-fg)] mb-1.5">
            {t("compile.result.schemaIssuesLabel")}
          </p>
          <ul className="m-0 pl-5 text-xs list-disc text-[var(--color-text-secondary)] space-y-1 leading-relaxed">
            {data.schema_issues.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </div>
      )}

      <form action={saveAction} className="mt-2 flex items-center gap-2 flex-wrap">
        <input type="hidden" name="ir_json" value={irJson} />
        <input type="hidden" name="source" value="org" />
        <SubmitButton
          label={t("compile.activate")}
          pendingLabel={t("newPolicy.saving")}
        />
        {!canSave && (
          <span className="text-xs text-[var(--color-text-tertiary)] leading-tight">
            {t("compile.cantActivate")}
          </span>
        )}
      </form>
    </Card>
  )
}
