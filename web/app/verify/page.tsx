import Link from "next/link"
import { redirect } from "next/navigation"
import { cloud, type PresetEntry } from "@/lib/cloud"
import { codeForError } from "@/lib/flash"
import { SubmitButton } from "@/components/SubmitButton"

export const dynamic = "force-dynamic"

type VerifyResult = {
  step: string
  payload: string
  matter: string
  docId: string
  verdict: "pass" | "review" | "deny" | "error"
  token: string | null
  reasons: string[]
  exp?: number | null
  kid?: string | null
  hitlId?: number | null
}

// Per-step starter payload — shows the operator what shape each verifier expects.
const SAMPLE_PAYLOAD: Record<string, string> = {
  privilege_scan: JSON.stringify(
    { text: "Motion to compel discovery filed on 2026-06-20." },
    null,
    2,
  ),
  source_allowlist: JSON.stringify(
    { sources: ["https://law.go.kr/case/123"], allowlist: ["law.go.kr"] },
    null,
    2,
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
    },
    null,
    2,
  ),
  prompt_injection_screen: JSON.stringify(
    { text: "대법원 2018도13694 판결문 전문…" },
    null,
    2,
  ),
}

async function runVerify(formData: FormData): Promise<void> {
  "use server"
  const step = String(formData.get("step") ?? "").trim()
  const payloadRaw = String(formData.get("payload") ?? "").trim()
  const matter = String(formData.get("matter") ?? "dashboard").trim() || "dashboard"
  const docId = String(formData.get("doc_id") ?? "dashboard").trim() || "dashboard"

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
    result = await cloud.verifyDispatch(step, parsed!, matter, docId)
  } catch (e: unknown) {
    redirect(`/verify?err=${codeForError(e)}&step=${encodeURIComponent(step)}`)
  }

  const display: VerifyResult = {
    step,
    payload: payloadRaw,
    matter,
    docId,
    verdict: result!.verdict,
    token: result!.token,
    reasons: result!.reasons ?? [],
    exp: result!.exp ?? null,
    kid: result!.kid ?? null,
    hitlId: result!.hitl_id ?? null,
  }
  const encoded = encodeURIComponent(JSON.stringify(display))
  if (encoded.length > 6000) {
    redirect(`/verify?msg=ran&step=${encodeURIComponent(step)}`)
  }
  redirect(`/verify?r=${encoded}`)
}

function VerdictBadge({ v }: { v: VerifyResult["verdict"] }) {
  const cls =
    v === "pass" ? "tag ok"
    : v === "review" ? "tag review"
    : v === "deny" ? "tag deny"
    : "tag"
  return <span className={cls} aria-label={`verdict: ${v}`}>{v}</span>
}

function VerifyForm({
  wiredSteps,
  defaultStep,
  defaultPayload,
}: {
  wiredSteps: { step: string; id: string }[]
  defaultStep?: string
  defaultPayload?: string
}) {
  const initialStep = defaultStep ?? wiredSteps[0]?.step ?? ""
  return (
    <form action={runVerify}>
      <div className="row" style={{ gap: 12, alignItems: "flex-start" }}>
        <label style={{ flex: 1, minWidth: 220 }}>
          <div className="muted">verifier step</div>
          <select
            name="step"
            defaultValue={initialStep}
            style={{
              width: "100%", background: "#0c0d10",
              border: "1px solid #2f3845", color: "#d7d7db",
              padding: "8px 10px", borderRadius: 6, fontSize: 13, marginTop: 4,
            }}
          >
            {wiredSteps.map(s => (
              <option key={s.step} value={s.step}>
                {s.step} ({s.id})
              </option>
            ))}
          </select>
        </label>
        <label style={{ minWidth: 160 }}>
          <div className="muted">matter (audit label)</div>
          <input type="text" name="matter" defaultValue="dashboard" />
        </label>
        <label style={{ minWidth: 160 }}>
          <div className="muted">doc_id (audit label)</div>
          <input type="text" name="doc_id" defaultValue="dashboard" />
        </label>
      </div>

      <label style={{ display: "block", marginTop: 14 }}>
        <div className="muted">payload (JSON object passed to verifier.run)</div>
        <textarea
          name="payload"
          rows={10}
          defaultValue={defaultPayload ?? SAMPLE_PAYLOAD[initialStep] ?? "{\n  \n}"}
          style={{
            width: "100%", marginTop: 4, background: "#0c0d10",
            border: "1px solid #2f3845", color: "#d7d7db",
            padding: 10, borderRadius: 6, fontSize: 13,
            fontFamily: "ui-monospace, monospace",
          }}
        />
      </label>

      <div style={{ marginTop: 12 }}>
        <SubmitButton
          label="Run verifier"
          pendingLabel="Running"
          progressHint="Dispatching to the verifier; signing token + appending to ledger."
        />
      </div>

      <p className="muted" style={{ marginTop: 10, fontSize: 11 }}>
        Tip: the per-step sample shows the shape each verifier expects.
        Switch the step to load a fresh sample.
      </p>
    </form>
  )
}

function VerifyResultCard({ r }: { r: VerifyResult }) {
  return (
    <>
      <h2 style={{ marginTop: 24 }}>Result</h2>
      <div className="row" style={{ gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
        <VerdictBadge v={r.verdict} />
        {r.token
          ? <span className="tag ok">token issued</span>
          : <span className="tag">no token</span>}
        {r.hitlId != null && <span className="tag review">hitl #{r.hitlId}</span>}
        {r.kid && <span className="tag" style={{ fontFamily: "monospace" }}>kid {r.kid.slice(0, 8)}…</span>}
        {r.exp && (
          <span className="muted" style={{ fontSize: 11 }}>
            token exp = {new Date(r.exp * 1000).toLocaleString()}
          </span>
        )}
      </div>

      {r.reasons.length > 0 && (
        <div className="card">
          <div className="muted" style={{ marginBottom: 4 }}>reasons</div>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
            {r.reasons.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </div>
      )}

      {r.token && (
        <details className="card">
          <summary className="muted">signed token (ed25519)</summary>
          <pre style={{
            margin: 0, marginTop: 6, fontSize: 11, overflowX: "auto",
            whiteSpace: "pre-wrap", wordBreak: "break-all",
          }}>{r.token}</pre>
        </details>
      )}

      <div className="row" style={{ marginTop: 16, gap: 8, flexWrap: "wrap" }}>
        <Link href="/verify"><button>Run another</button></Link>
        <Link href="/ledger"><button>See ledger entry</button></Link>
        {r.hitlId != null && (
          <Link href={`/hitl/${r.hitlId}`}><button>Open in review queue</button></Link>
        )}
      </div>
    </>
  )
}

function decodeResult(r: string | undefined): VerifyResult | null {
  if (!r) return null
  try {
    const obj = JSON.parse(decodeURIComponent(r))
    if (!obj || typeof obj.verdict !== "string") return null
    return obj as VerifyResult
  } catch {
    return null
  }
}

export default async function VerifyPage({
  searchParams,
}: {
  searchParams: { r?: string; err?: string; step?: string; msg?: string; parse?: string; missing?: string }
}) {
  let presets: PresetEntry[] = []
  let listErr: string | null = null
  try { presets = await cloud.listPresets() } catch (e: unknown) { listErr = String(e) }

  const wired = presets
    .filter(p => p.enforcement === "enforcing" && p.step && p.step !== "citation_verify")
    .map(p => ({ step: p.step!, id: p.id }))

  const prior = decodeResult(searchParams.r)
  const stepHint = searchParams.step ?? prior?.step

  return (
    <>
      <h1>Run a verifier</h1>
      <p className="muted" style={{ marginTop: -4, marginBottom: 14 }}>
        Dispatches against <code>/verify/&#123;step&#125;</code>. On <strong>pass</strong> a signed
        Ed25519 token is issued + appended to the audit ledger. On{" "}
        <strong>deny</strong> the reasons explain why. <code>citation_verify</code> has its own
        specialised path (with corpus_override and NLI advisory) — use{" "}
        <Link href="/policies/compile">/policies/compile</Link> or the cloud API directly for it.
      </p>

      {listErr && (
        <div className="card" role="alert">
          <span className="tag deny">cloud unreachable</span>
          <p className="muted">cannot list verifiers; see server logs</p>
        </div>
      )}

      {searchParams.err === "invalid_input" && (
        <div className="card" role="alert">
          <span className="tag deny">invalid input</span>
          <p className="muted">
            {searchParams.missing
              ? `missing field: ${searchParams.missing}`
              : searchParams.parse
                ? "payload is not a valid JSON object"
                : "see form below"}
          </p>
        </div>
      )}
      {searchParams.err === "cloud_unreachable" && (
        <div className="card" role="alert">
          <span className="tag deny">cloud unreachable</span>
          <p className="muted">see server logs</p>
        </div>
      )}
      {searchParams.err === "forbidden" && (
        <div className="card" role="alert">
          <span className="tag deny">forbidden</span>
          <p className="muted">tenant suspended or revoked key</p>
        </div>
      )}
      {searchParams.msg === "ran" && (
        <div className="card" role="status">
          <span className="tag ok">verifier ran</span>
          <p className="muted">result payload too large to display — see /ledger</p>
        </div>
      )}

      <VerifyForm
        wiredSteps={wired}
        defaultStep={stepHint}
        defaultPayload={prior?.payload}
      />

      {prior && <VerifyResultCard r={prior} />}
    </>
  )
}
