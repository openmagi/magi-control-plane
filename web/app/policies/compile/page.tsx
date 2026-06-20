import Link from "next/link"
import { redirect } from "next/navigation"
import { revalidatePath } from "next/cache"
import { cloud, type CompileResult } from "@/lib/cloud"
import { codeForError } from "@/lib/flash"
import { SubmitButton } from "@/components/SubmitButton"

export const dynamic = "force-dynamic"

async function runCompile(formData: FormData): Promise<void> {
  "use server"
  const nl = String(formData.get("nl") ?? "").trim()
  if (!nl) redirect("/policies/compile?err=invalid_input")
  let result: CompileResult
  try {
    result = await cloud.compilePolicy(nl)
  } catch (e: unknown) {
    redirect(`/policies/compile?err=${codeForError(e)}`)
  }
  // Stash via in-memory query — Next.js server actions can return data but
  // we redirect to render-only (no client JS). Encode the result into a URL
  // payload that the same page reads back. Bounded at ~2 KB for safety;
  // larger payloads fall back to the no-result view.
  const payload = JSON.stringify({ nl, ...result })
  if (payload.length > 1500) {
    // Too large for URL — render a "compile succeeded, copy from server logs" hint.
    redirect("/policies/compile?msg=saved")
  }
  revalidatePath("/policies/compile")
  redirect(`/policies/compile?r=${encodeURIComponent(payload)}`)
}

function CompileForm({ nl }: { nl?: string }) {
  return (
    <form action={runCompile}>
      <label htmlFor="nl" className="muted" style={{ display: "block", marginBottom: 6 }}>
        Describe the policy in plain language. The compiler turns it into a Policy IR.
      </label>
      <textarea id="nl" name="nl" rows={6}
                defaultValue={nl ?? ""}
                style={{
                  width: "100%", background: "#0c0d10",
                  border: "1px solid #2f3845", color: "#d7d7db",
                  padding: 10, borderRadius: 6, fontSize: 13,
                  fontFamily: "ui-monospace, monospace",
                }}
                placeholder="e.g. 법원 filing 시 인용을 결정론으로 검증하고 미통과는 차단하라" />
      <div style={{ marginTop: 10 }}>
        <SubmitButton
          label="Compile"
          pendingLabel="Compiling"
          progressHint="LLM compiler + critic LLM running — typically 5–20s. Don't refresh."
        />
      </div>
    </form>
  )
}

function ReviewBadge({ ok }: { ok: boolean }) {
  return ok
    ? <span className="tag ok">reviewer ok</span>
    : <span className="tag review">reviewer flagged</span>
}

function CompileResultView({ data }: { data: CompileResult & { nl: string } }) {
  const irJson = JSON.stringify(data.ir, null, 2)
  const hasSchemaIssues = data.schema_issues.length > 0
  return (
    <>
      <h2>Result</h2>
      <div className="row" style={{ gap: 6, marginBottom: 10 }}>
        <ReviewBadge ok={data.review.ok} />
        {hasSchemaIssues
          ? <span className="tag deny">{data.schema_issues.length} schema issue(s)</span>
          : <span className="tag ok">schema clean</span>}
      </div>

      <div className="card">
        <div className="muted" style={{ marginBottom: 6 }}>Compiled Policy IR</div>
        <pre style={{ overflow: "auto", margin: 0, fontSize: 12,
                       background: "#0c0d10", padding: 10, borderRadius: 6,
                       maxHeight: "40vh" }}>{irJson}</pre>
      </div>

      {data.review.issues.length > 0 && (
        <div className="card">
          <div className="muted" style={{ marginBottom: 6 }}>Reviewer issues</div>
          <ul style={{ margin: 0, paddingLeft: 20, fontSize: 12 }}>
            {data.review.issues.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </div>
      )}

      {hasSchemaIssues && (
        <div className="card" role="alert">
          <div className="muted" style={{ marginBottom: 6 }}>Schema issues (deterministic)</div>
          <ul style={{ margin: 0, paddingLeft: 20, fontSize: 12 }}>
            {data.schema_issues.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </div>
      )}

      <div className="row" style={{ marginTop: 14, gap: 10 }}>
        <Link href={`/policies/new?draft=${encodeURIComponent(JSON.stringify(data.ir))}`}>
          <button className="primary">Edit & save (hand off to /policies/new)</button>
        </Link>
        <Link href="/policies/compile">
          <button>Compile another</button>
        </Link>
      </div>
    </>
  )
}

function decodeResult(r: string | undefined): (CompileResult & { nl: string }) | null {
  if (!r) return null
  try {
    const obj = JSON.parse(decodeURIComponent(r))
    if (typeof obj !== "object" || !obj || !obj.ir || !obj.review) return null
    return obj as CompileResult & { nl: string }
  } catch { return null }
}

export default function CompilePage({
  searchParams,
}: { searchParams: { r?: string; err?: string; msg?: string } }) {
  const result = decodeResult(searchParams.r)
  return (
    <>
      <h1>Compile a policy (NL → IR)</h1>
      <p className="muted" style={{ marginTop: -4, marginBottom: 14 }}>
        LLM compiles your description into a Policy IR. A critic LLM reviews it. Server-side
        schema check runs deterministically. <strong>Nothing is saved</strong> — review and
        click "Edit & save" to PUT via the policies form.
      </p>

      {searchParams.err === "config_error" && (
        <div className="card" role="alert">
          <span className="tag deny">server config error</span>
          <p className="muted">see server logs (admin key likely missing)</p>
        </div>
      )}
      {searchParams.err === "cloud_unreachable" && (
        <div className="card" role="alert">
          <span className="tag deny">cloud unreachable</span>
          <p className="muted">503: LLM providers not configured? see README "Environment variables"</p>
        </div>
      )}
      {searchParams.err === "invalid_input" && (
        <div className="card" role="alert">
          <span className="tag deny">empty input</span>
          <p className="muted">describe the policy in a full sentence (≥8 chars)</p>
        </div>
      )}
      {searchParams.msg === "saved" && (
        <div className="card" role="status">
          <span className="tag ok">compiled (large payload — re-run with shorter NL to display)</span>
        </div>
      )}

      <CompileForm nl={result?.nl} />
      {result && <CompileResultView data={result} />}
    </>
  )
}
