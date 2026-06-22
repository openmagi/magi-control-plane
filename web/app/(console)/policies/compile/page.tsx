import Link from "next/link"
import { redirect } from "next/navigation"
import { revalidatePath } from "next/cache"
import { cloud, type CompileResult } from "@/lib/cloud"
import { codeForError } from "@/lib/flash"
import { getT } from "@/lib/i18n/server"
import {
  Badge, Button, Card, CodeBlock, ErrorState, PageHeader,
  SubmitButton, Textarea,
} from "@/components/ui"

export const dynamic = "force-dynamic"

async function runCompile(formData: FormData): Promise<void> {
  "use server"
  const nl = String(formData.get("nl") ?? "").trim()
  if (!nl) redirect("/policies/compile?err=invalid_input&nl=" + encodeURIComponent(nl))
  let result: CompileResult
  try {
    result = await cloud.compilePolicy(nl)
  } catch (e: unknown) {
    redirect(`/policies/compile?err=${codeForError(e)}&nl=${encodeURIComponent(nl)}`)
  }
  const payload = JSON.stringify({ nl, ...result })
  if (payload.length > 1500) {
    // Result too big to round-trip via query string. Persist into a cookie
    // so the page can still render the IR + reviewer output.
    const { cookies } = await import("next/headers")
    cookies().set({
      name: "magi-cp-compile-result",
      value: payload,
      path: "/policies/compile",
      sameSite: "lax",
      maxAge: 60 * 5,   // 5 minutes; never persisted
    })
    revalidatePath("/policies/compile")
    redirect("/policies/compile?msg=large")
  }
  revalidatePath("/policies/compile")
  redirect(`/policies/compile?r=${encodeURIComponent(payload)}`)
}

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

export default async function CompilePage({
  searchParams,
}: { searchParams: { r?: string; err?: string; msg?: string; nl?: string } }) {
  const { t } = await getT()
  const fromQuery = decodeResult(searchParams.r)
  const result = fromQuery ?? (searchParams.msg === "large" ? await readCookieResult() : null)
  const nl = result?.nl ?? searchParams.nl ?? ""

  const errCard = (() => {
    if (searchParams.err === "config_error") {
      return (
        <ErrorState
          status={t("common.serverConfigError")}
          title={t("common.serverConfigError")}
          body={t("common.seeServerLogs")}
        />
      )
    }
    if (searchParams.err === "cloud_unreachable") {
      return (
        <ErrorState
          title={t("common.cloudUnreachable")}
          body={t("compile.llmNotConfigured")}
        />
      )
    }
    if (searchParams.err === "invalid_input") {
      return (
        <ErrorState
          status={t("common.cloudUnreachable")}
          title={t("compile.field.empty")}
          severity="warning"
        />
      )
    }
    return null
  })()

  return (
    <>
      <PageHeader
        title={t("compile.title")}
        description={t("compile.description")}
      />

      {errCard}

      <form action={runCompile} className="mb-6">
        <Textarea
          id="nl"
          name="nl"
          rows={6}
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

      {result && <ResultBlock t={t} data={result} />}
    </>
  )
}

function ResultBlock({
  t, data,
}: {
  data: CompileResult & { nl: string }
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
}) {
  const irJson = JSON.stringify(data.ir, null, 2)
  const hasSchemaIssues = data.schema_issues.length > 0
  return (
    <section aria-labelledby="result-heading" className="space-y-3">
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

      <div className="flex flex-wrap gap-2 mt-2">
        <Link href={`/policies/new?draft=${encodeURIComponent(JSON.stringify(data.ir))}`}>
          <Button variant="primary">{t("compile.handoff")}</Button>
        </Link>
        <Link href="/policies/compile">
          <Button variant="secondary">{t("compile.runAgain")}</Button>
        </Link>
      </div>
    </section>
  )
}
