import Link from "next/link"
import { redirect } from "next/navigation"
import { cloud, type PresetEntry } from "@/lib/cloud"
import { codeForError } from "@/lib/flash"
import { getIntl, getT } from "@/lib/i18n/server"
import {
  Badge, Button, Card, CardHeader, Code, CodeBlock, CopyButton,
  EmptyState, ErrorState, Input, PageHeader, Select, SubmitButton, Textarea,
} from "@/components/ui"

export const dynamic = "force-dynamic"

type VerifyResult = {
  step: string
  payload: string
  // PR4: canonical fields. Legacy `matter` / `docId` removed.
  subject: string
  payloadHash: string
  verdict: "pass" | "review" | "deny" | "error"
  token: string | null
  reasons: string[]
  exp?: number | null
  kid?: string | null
  hitlId?: number | null
}

const SAMPLE_PAYLOAD: Record<string, string> = {
  privilege_scan: JSON.stringify(
    { text: "Motion to compel discovery filed on 2026-06-20." },
    null, 2,
  ),
  source_allowlist: JSON.stringify(
    { sources: ["https://law.go.kr/case/123"], allowlist: ["law.go.kr"] },
    null, 2,
  ),
  structured_output: JSON.stringify(
    {
      data: { case_no: "2024가합1234", filing_type: "motion" },
      schema: {
        type: "object",
        required: ["case_no", "filing_type"],
        properties: {
          case_no: { type: "string" },
          filing_type: { type: "string", enum: ["motion", "brief", "response"] },
        },
      },
    }, null, 2,
  ),
  prompt_injection_screen: JSON.stringify(
    { text: "대법원 2018도13694 판결문 전문…" },
    null, 2,
  ),
}

async function runVerify(formData: FormData): Promise<void> {
  "use server"
  const step = String(formData.get("step") ?? "").trim()
  const payloadRaw = String(formData.get("payload") ?? "").trim()
  // PR4: canonical fields only. Legacy form names dropped.
  const subject = String(formData.get("subject") ?? "dashboard").trim() || "dashboard"
  const payloadHash = String(formData.get("payload_hash") ?? "dashboard").trim() || "dashboard"

  if (!step) redirect("/verify?err=invalid_input&missing=step")
  if (!payloadRaw) redirect("/verify?err=invalid_input&missing=payload")

  let parsed: Record<string, unknown>
  try {
    parsed = JSON.parse(payloadRaw)
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      throw new Error("payload must be a JSON object")
    }
  } catch (e: unknown) {
    redirect(`/verify?err=invalid_input&parse=1&step=${encodeURIComponent(step)}`)
  }

  let result: Awaited<ReturnType<typeof cloud.verifyDispatch>>
  try {
    result = await cloud.verifyDispatch(step, parsed!, subject, payloadHash)
  } catch (e: unknown) {
    redirect(`/verify?err=${codeForError(e)}&step=${encodeURIComponent(step)}`)
  }

  const display: VerifyResult = {
    step, payload: payloadRaw, subject, payloadHash,
    verdict: result!.verdict,
    token: result!.token,
    reasons: result!.reasons ?? [],
    exp: result!.exp ?? null,
    kid: result!.kid ?? null,
    hitlId: result!.hitl_id ?? null,
  }
  const encoded = encodeURIComponent(JSON.stringify(display))
  if (encoded.length > 6000) {
    // round-trip via cookie like /policies/compile
    const { cookies } = await import("next/headers")
    cookies().set({
      name: "magi-cp-verify-result",
      value: JSON.stringify(display),
      path: "/verify",
      sameSite: "lax",
      maxAge: 60 * 5,
    })
    redirect(`/verify?msg=ran&step=${encodeURIComponent(step)}`)
  }
  redirect(`/verify?r=${encoded}`)
}

function decodeResult(r: string | undefined): VerifyResult | null {
  if (!r) return null
  try {
    const obj = JSON.parse(decodeURIComponent(r))
    if (!obj || typeof obj.verdict !== "string") return null
    return obj as VerifyResult
  } catch { return null }
}

async function readCookieResult(): Promise<VerifyResult | null> {
  const { cookies } = await import("next/headers")
  const raw = cookies().get("magi-cp-verify-result")?.value
  if (!raw) return null
  try { return JSON.parse(raw) as VerifyResult } catch { return null }
}

export default async function VerifyPage({
  searchParams,
}: {
  searchParams: {
    r?: string; err?: string; step?: string; msg?: string;
    parse?: string; missing?: string;
  }
}) {
  const { t } = await getT()
  const { dtf } = await getIntl()

  let presets: PresetEntry[] = []
  let listErr: string | null = null
  try { presets = await cloud.listPresets() }
  catch (e: unknown) { listErr = String(e) }

  const wired = presets
    .filter(p =>
      p.enforcement === "enforcing" && p.step && p.step !== "citation_verify")
    .map(p => ({ step: p.step!, id: p.id }))

  const fromQuery = decodeResult(searchParams.r)
  const prior =
    fromQuery ?? (searchParams.msg === "ran" ? await readCookieResult() : null)
  const stepHint = searchParams.step ?? prior?.step
  const initialStep = stepHint ?? wired[0]?.step ?? ""

  return (
    <>
      <PageHeader
        title={t("verify.title")}
        description={
          <span>
            {t("verify.description", {
              pass: "pass",
              deny: "deny",
            })}
          </span>
        }
      />

      {listErr && (
        <ErrorState
          title={t("common.cloudUnreachable")}
          body={t("common.seeServerLogs")}
        />
      )}

      {searchParams.err === "invalid_input" && (
        <ErrorState
          status={searchParams.parse ? t("verify.error.invalidPayload") : "invalid"}
          title={
            searchParams.parse
              ? t("verify.error.invalidPayload")
              : (searchParams.missing
                  ? `missing field: ${searchParams.missing}`
                  : t("verify.error.invalidPayload"))
          }
          severity="warning"
        />
      )}
      {searchParams.err === "cloud_unreachable" && (
        <ErrorState
          title={t("common.cloudUnreachable")}
          body={t("common.seeServerLogs")}
        />
      )}
      {searchParams.err === "forbidden" && (
        <ErrorState
          status="forbidden"
          title={t("verify.error.tenantSuspended")}
        />
      )}
      {searchParams.msg === "ran" && !prior && (
        <Card role="status">
          <Badge variant="ok">{t("verify.result.title")}</Badge>
          <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">
            {t("verify.error.payloadTooLarge")}
          </p>
        </Card>
      )}

      {/* D72: when no verifier is wired the form Select would render an
          empty dropdown. Surface a first-time-visitor empty state that
          tells the operator where the built-in checks live. */}
      {!listErr && wired.length === 0 && (
        <EmptyState
          title={t("verify.empty.title")}
          body={t("verify.empty.body")}
          action={
            <Link href="/rules?tab=checks">
              <Button variant="primary">{t("verify.empty.cta")}</Button>
            </Link>
          }
        />
      )}

      <form action={runVerify} className="space-y-4 mb-6">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <Select
            name="step"
            label={t("verify.field.step")}
            defaultValue={initialStep}
            options={wired.map(s => ({ value: s.step, label: `${s.step}` }))}
          />
          <Input
            type="text"
            name="subject"
            label={t("verify.field.subject")}
            defaultValue={prior?.subject ?? "dashboard"}
            autoComplete="off"
            spellCheck={false}
          />
          <Input
            type="text"
            name="payload_hash"
            label={t("verify.field.payloadHash")}
            defaultValue={prior?.payloadHash ?? "dashboard"}
            autoComplete="off"
            spellCheck={false}
          />
        </div>

        <Textarea
          name="payload"
          rows={10}
          label={t("verify.field.payload")}
          helper={t("verify.tip")}
          defaultValue={prior?.payload ?? SAMPLE_PAYLOAD[initialStep] ?? "{\n  \n}"}
          spellCheck={false}
          autoComplete="off"
          monospace
        />

        <SubmitButton
          label={t("verify.submit")}
          pendingLabel={t("verify.submit.pending")}
          progressHint={t("verify.progressHint")}
        />
      </form>

      {prior && <ResultBlock t={t} dtf={dtf} r={prior} />}
    </>
  )
}

function ResultBlock({
  t, dtf, r,
}: {
  t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
  dtf: Intl.DateTimeFormat
  r: VerifyResult
}) {
  const verdictTone =
    r.verdict === "pass"   ? "ok"
    : r.verdict === "review" ? "review"
    : r.verdict === "deny"   ? "deny"
    : "default"
  return (
    <section aria-labelledby="verify-result" className="space-y-3">
      <h2 id="verify-result" className="text-md font-semibold mt-4">
        {t("verify.result.title")}
      </h2>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={verdictTone as never}>{r.verdict}</Badge>
        {r.token
          ? <Badge variant="ok">{t("verify.result.tokenIssued")}</Badge>
          : <Badge variant="muted">{t("verify.result.noToken")}</Badge>}
        {r.hitlId != null && (
          <Badge variant="review">HITL #{r.hitlId}</Badge>
        )}
        {r.kid && (
          <Badge variant="muted">
            kid <Code className="ml-1 bg-transparent border-0 px-0">{r.kid.slice(0, 8)}…</Code>
          </Badge>
        )}
        {r.exp && (
          <span className="text-xs text-[var(--color-text-tertiary)]">
            {t("verify.result.expires")}: {dtf.format(new Date(r.exp * 1000))}
          </span>
        )}
      </div>

      {r.reasons.length > 0 && (
        <Card>
          <div className="text-xs text-[var(--color-text-tertiary)] mb-2">
            {t("verify.result.reasons")}
          </div>
          <ul className="m-0 pl-5 text-sm list-disc text-[var(--color-text-secondary)] space-y-1">
            {r.reasons.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </Card>
      )}

      {r.token && (
        <Card>
          <CardHeader
            title={t("verify.result.signedToken")}
            action={<CopyButton value={r.token} size="sm" variant="ghost" />}
          />
          <CodeBlock maxHeight="40vh">{r.token}</CodeBlock>
        </Card>
      )}

      <div className="flex flex-wrap gap-2 mt-2">
        <Link href="/verify"><Button variant="secondary">{t("verify.action.runAnother")}</Button></Link>
        <Link href="/ledger"><Button variant="ghost">{t("verify.action.seeLedger")}</Button></Link>
        {r.hitlId != null && (
          <Link href={`/hitl/${r.hitlId}`}>
            <Button variant="ghost">{t("verify.action.openHitl")}</Button>
          </Link>
        )}
      </div>
    </section>
  )
}
