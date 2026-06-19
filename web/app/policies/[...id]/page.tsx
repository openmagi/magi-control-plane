import Link from "next/link"
import { cloud, type PolicyDetail, type CompiledManagedSettings } from "@/lib/cloud"
import { codeForError } from "@/lib/flash"
import { validatePolicyId } from "@/lib/policy-id"

export const dynamic = "force-dynamic"

function Pre({ children }: { children: string }) {
  return (
    <pre style={{
      background: "#0c0d10", border: "1px solid #20232a", borderRadius: 6,
      padding: 12, overflow: "auto", fontSize: 12, lineHeight: 1.4,
      maxHeight: "60vh",
    }}>{children}</pre>
  )
}

export default async function PolicyDetailPage({
  params,
}: { params: { id: string[] } }) {
  const raw = params.id.join("/")
  let id: string
  try { id = validatePolicyId(raw) }
  catch {
    return (
      <>
        <p><Link href="/policies">← Policies</Link></p>
        <div className="card" role="alert">
          <span className="tag deny">invalid policy id</span>
        </div>
      </>
    )
  }

  let detail: PolicyDetail | null = null
  let compiled: CompiledManagedSettings | null = null
  let errCode: string | null = null
  try {
    [detail, compiled] = await Promise.all([
      cloud.getPolicy(id),
      cloud.getCompiled(id),
    ])
  } catch (e: unknown) { errCode = codeForError(e) }

  if (errCode === "not_found") {
    return (
      <>
        <p><Link href="/policies">← Policies</Link></p>
        <div className="card" role="alert">
          <span className="tag deny">policy not found</span>
          <p className="muted"><code>{id}</code></p>
        </div>
      </>
    )
  }
  if (errCode || !detail || !compiled) {
    return (
      <>
        <p><Link href="/policies">← Policies</Link></p>
        <div className="card" role="alert">
          <span className="tag deny">cloud unreachable</span>
          <p className="muted">see server logs</p>
        </div>
      </>
    )
  }

  const irJson = JSON.stringify(detail.policy, null, 2)
  const msJson = JSON.stringify(compiled.managed_settings, null, 2)
  const shaShort = compiled.sha256.slice(0, 16)
  const shaMismatch = detail.compiled_sha256 !== compiled.sha256

  return (
    <>
      <p><Link href="/policies">← Policies</Link></p>
      <h1><code style={{ overflowWrap: "anywhere" }}>{detail.id}</code></h1>
      <div className="card row" style={{ gap: 18, flexWrap: "wrap" }}>
        <div>source: <code>{detail.source}</code></div>
        <div>
          status:{" "}
          {detail.enabled
            ? <span className="tag ok">enabled</span>
            : <span className="tag deny">disabled</span>}
        </div>
        <div>
          enforcement:{" "}
          <span className={
            detail.enforcement === "deterministic-gate" ? "tag ok"
            : detail.enforcement === "observe-only" ? "tag review" : "tag"
          }>{detail.enforcement}</span>
        </div>
        <details>
          <summary className="muted">compiled sha: <code>{shaShort}…</code></summary>
          <code style={{ wordBreak: "break-all" }}>{compiled.sha256}</code>
        </details>
        {shaMismatch && (
          <div role="alert">
            <span className="tag deny">sha mismatch (cloud bug?)</span>
          </div>
        )}
      </div>

      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))",
        gap: 18, marginTop: 18,
      }}>
        <section>
          <h2>Policy IR</h2>
          <p className="muted" style={{ fontSize: 11 }}>
            What an operator authors. The compiler (right) turns it into the
            managed-settings JSON Claude Code consumes.
          </p>
          <Pre>{irJson}</Pre>
        </section>
        <section>
          <h2>Compiled managed-settings.json</h2>
          <p className="muted" style={{ fontSize: 11 }}>
            Deterministic compile. Same IR ⇒ same byte output ⇒ same sha256
            (over the cloud's canonical bytes, not this pretty-printed display).
          </p>
          <Pre>{msJson}</Pre>
        </section>
      </div>
    </>
  )
}
