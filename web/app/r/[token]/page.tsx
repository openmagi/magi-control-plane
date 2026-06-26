import type { CSSProperties } from "react"

import type { Metadata } from "next"
import { notFound } from "next/navigation"
import Markdown from "react-markdown"
import remarkGfm from "remark-gfm"

import { cloud } from "@/lib/cloud"

export const dynamic = "force-dynamic"

export const metadata: Metadata = {
  title: "Shared agent run · Magi",
  description: "A Claude Code run, captured and governed by Magi.",
  robots: { index: false, follow: false },
}

const C = {
  bg: "#0B0E0A",
  panel: "#11160F",
  border: "#1E261C",
  green: "#22C55E",
  prompt: "#7EE787",
  text: "#C9D1C9",
  muted: "#7A857A",
  red: "#EF4444",
  amber: "#F59E0B",
}

type ModelField = string | { label?: string | null; provider?: string | null } | null | undefined

function modelLabel(model: ModelField): string {
  if (typeof model === "string") return model || "—"
  if (model && typeof model === "object") {
    return [model.provider, model.label].filter(Boolean).join(" / ") || "—"
  }
  return "—"
}

/** Only http(s) hrefs reach the DOM (model-influenced refs; block javascript:). */
function safeHref(url?: string): string | null {
  if (!url) return null
  return /^https?:\/\//i.test(url) ? url : null
}

/** Short detail from a tool call's redacted argsSummary (URL / query / command). */
function traceDetail(args: unknown): string {
  if (!args || typeof args !== "object") return ""
  const a = args as Record<string, unknown>
  const pick = a.url ?? a.query ?? a.command ?? a.path ?? a.file_path ?? a.pattern ?? a.prompt
  return typeof pick === "string" ? pick : ""
}

const SUP: Record<string, string> = {
  "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
  "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
}
/** Turn bracketed footnotes `[1]` into superscript `¹` (citation style), without
 *  touching markdown links `[1](url)`. Pure text transform, XSS-safe. */
function citeify(md: string): string {
  return md.replace(/\[(\d{1,3})\](?!\()/g, (_, n: string) =>
    [...n].map((d) => SUP[d] ?? d).join(""),
  )
}

function statusColor(status?: string | null): string {
  if (status === "ok" || status === "completed") return C.green
  if (status === "blocked") return C.red
  if (status === "needs_approval") return C.amber
  if (status === "error" || status === "aborted") return C.red
  return C.muted
}

function govVerb(status?: string | null): string {
  if (status === "blocked") return "BLOCKED"
  if (status === "needs_approval") return "HELD FOR APPROVAL"
  if (status === "ok" || status === "verified" || status === "passed") return "VERIFIED"
  if (status === "error") return "ERROR"
  return status ?? ""
}

export default async function SharedRunPage({
  params,
}: { params: { token: string } }) {
  const shared = await cloud.getSharedRun(params.token).catch(() => null)
  if (!shared) notFound()

  const v = shared.view
  const s = v.summary ?? {}
  const usage = s.usage ?? {}
  const trace = Array.isArray(v.trace) ? v.trace : []
  const governance = Array.isArray(v.governance) ? v.governance : []
  const results = Array.isArray(v.results) ? v.results : []
  const sources = Array.isArray(v.sources) ? v.sources : []

  const blocked = governance.filter((g) => g.status === "blocked").length
  const held = governance.filter((g) => g.status === "needs_approval").length

  const card: CSSProperties = {
    background: C.panel, border: `1px solid ${C.border}`,
    borderRadius: 10, padding: "16px 18px", marginBottom: 14,
  }
  const label: CSSProperties = { color: C.muted, fontSize: 12, textTransform: "uppercase", letterSpacing: 0.6 }
  const mono = "ui-monospace, SFMono-Regular, Menlo, monospace"

  return (
    <main style={{ background: C.bg, color: C.text, minHeight: "100vh", fontFamily: mono }}>
      <div style={{ maxWidth: 860, margin: "0 auto", padding: "40px 20px 64px" }}>
        <div style={{ color: C.prompt, fontSize: 13, marginBottom: 6 }}>
          <span style={{ color: C.muted }}>magi ·</span> governed agent run
        </div>
        <h1 style={{ fontSize: 22, fontWeight: 600, margin: "0 0 8px" }}>{s.title || "Agent run"}</h1>

        {/* Governance headline: the differentiator. What magi enforced. */}
        <div style={{ display: "flex", gap: 14, alignItems: "center", flexWrap: "wrap", marginBottom: 22, fontSize: 13 }}>
          <span style={{ color: statusColor(s.status) }}>● {s.status ?? "unknown"}</span>
          {blocked > 0 ? (
            <span style={{ color: C.red, border: `1px solid ${C.red}`, borderRadius: 6, padding: "2px 8px" }}>
              🛡 {blocked} blocked
            </span>
          ) : null}
          {held > 0 ? (
            <span style={{ color: C.amber, border: `1px solid ${C.amber}`, borderRadius: 6, padding: "2px 8px" }}>
              ⏸ {held} held for approval
            </span>
          ) : null}
        </div>

        {/* Goal + Answer (with inline citations) */}
        <section style={card}>
          {s.goal ? (
            <div style={{ marginBottom: 14 }}>
              <div style={label}>Goal</div>
              <div style={{ marginTop: 4 }}>{s.goal}</div>
            </div>
          ) : null}
          {s.result ? (
            <div style={{ marginBottom: 12 }}>
              <div style={label}>Answer</div>
              <div className="md" style={{ marginTop: 4 }}>
                <Markdown remarkPlugins={[remarkGfm]} components={{ img: () => null }}>
                  {citeify(s.result)}
                </Markdown>
              </div>
            </div>
          ) : null}
          <div style={{ display: "flex", gap: 28, flexWrap: "wrap", color: C.muted, fontSize: 13 }}>
            <span>model <span style={{ color: C.text }}>{modelLabel(s.model)}</span></span>
            <span>tokens <span style={{ color: C.text }}>
              {(usage.inputTokens ?? 0).toLocaleString()} in / {(usage.outputTokens ?? 0).toLocaleString()} out
            </span></span>
            <span>steps <span style={{ color: C.text }}>{v.counts?.stepCount ?? trace.length}</span></span>
          </div>
        </section>

        {/* Governance: what magi blocked / held. The thing ChatGPT logs do not have. */}
        {governance.length > 0 ? (
          <section style={card}>
            <div style={label}>Policy enforcement ({governance.length})</div>
            <ul style={{ listStyle: "none", padding: 0, margin: "10px 0 0" }}>
              {governance.map((g, i) => {
                const c = statusColor(g.status)
                return (
                  <li key={i} style={{ borderLeft: `2px solid ${c}`, padding: "4px 0 4px 12px", marginBottom: 10 }}>
                    <div style={{ fontSize: 12 }}>
                      <span style={{ color: c, fontWeight: 600 }}>{govVerb(g.status)}</span>
                      <span style={{ color: C.muted }}> · {g.name}</span>
                    </div>
                    {g.reason ? <div style={{ color: C.text, fontSize: 13, marginTop: 2 }}>{g.reason}</div> : null}
                  </li>
                )
              })}
            </ul>
          </section>
        ) : null}

        {/* Sources: numbered to match the inline ¹²³ footnotes above */}
        {sources.length > 0 ? (
          <section style={card}>
            <div style={label}>Sources ({sources.length})</div>
            <ol style={{ margin: "8px 0 0", paddingLeft: 22, fontSize: 13 }}>
              {sources.slice(0, 50).map((src, i) => {
                const href = src.isUrl ? safeHref(src.ref) : null
                const cred = (src.credibility ?? "").toUpperCase()
                const credColor = cred.startsWith("CREDIBLE") ? C.green : cred ? C.amber : C.muted
                return (
                  <li id={`src-${i + 1}`} key={i} style={{ padding: "3px 0" }}>
                    <span style={{ color: C.muted, marginRight: 8 }}>{src.tool}</span>
                    {href ? (
                      <a href={href} style={{ color: C.prompt, wordBreak: "break-all" }} rel="noopener noreferrer nofollow" target="_blank">
                        {src.ref}
                      </a>
                    ) : (
                      <span style={{ color: C.text, wordBreak: "break-all" }}>{src.ref}</span>
                    )}
                    {cred ? (
                      <span style={{ color: credColor, marginLeft: 8 }}>
                        {cred.startsWith("CREDIBLE") ? "✓ verified credible" : "⚠ " + cred.toLowerCase()}
                      </span>
                    ) : null}
                  </li>
                )
              })}
            </ol>
            {sources.length > 50 ? (
              <div style={{ color: C.muted, marginTop: 6, fontSize: 12 }}>+{sources.length - 50} more</div>
            ) : null}
          </section>
        ) : null}

        {/* Deliverables (PR links) */}
        {results.length > 0 ? (
          <section style={card}>
            <div style={label}>Deliverables ({results.length})</div>
            <ul style={{ listStyle: "none", padding: 0, margin: "8px 0 0" }}>
              {results.slice(0, 50).map((r, i) => {
                const href = safeHref(r.prUrl)
                const text = r.prNumber ? `PR #${r.prNumber}` : (r.prUrl ?? "—")
                return (
                  <li key={i} style={{ marginBottom: 4 }}>
                    {href ? (
                      <a href={href} style={{ color: C.prompt }} rel="noopener noreferrer nofollow" target="_blank">{text}</a>
                    ) : (
                      <span>{text}</span>
                    )}
                  </li>
                )
              })}
            </ul>
            {results.length > 50 ? (
              <div style={{ color: C.muted, marginTop: 6, fontSize: 12 }}>+{results.length - 50} more</div>
            ) : null}
          </section>
        ) : null}

        {/* Trace (full step list, secondary) */}
        {trace.length > 0 ? (
          <details style={card}>
            <summary style={{ ...label, cursor: "pointer", listStyle: "revert" }}>Trace ({trace.length})</summary>
            <ul style={{ listStyle: "none", padding: 0, margin: "10px 0 0", fontSize: 13 }}>
              {trace.slice(0, 200).map((t, i) => {
                const detail = traceDetail(t.argsSummary)
                const isBlocked = t.status === "blocked" || t.status === "needs_approval"
                return (
                  <li key={i} style={{ display: "flex", gap: 10, padding: "3px 0", borderBottom: `1px solid ${C.border}`, alignItems: "baseline" }}>
                    <span style={{ color: statusColor(t.status), flexShrink: 0 }}>{isBlocked ? "⛔" : "●"}</span>
                    <span style={{ color: C.text, minWidth: 90, flexShrink: 0 }}>{t.name}</span>
                    <span style={{ color: isBlocked ? statusColor(t.status) : C.muted, wordBreak: "break-all", flex: 1 }}>
                      {detail || t.activityType}
                    </span>
                    {t.durationMs ? <span style={{ color: C.muted, flexShrink: 0 }}>{t.durationMs}ms</span> : null}
                  </li>
                )
              })}
            </ul>
            {trace.length > 200 ? (
              <div style={{ color: C.muted, marginTop: 6, fontSize: 12 }}>+{trace.length - 200} more steps</div>
            ) : null}
          </details>
        ) : null}

        <footer style={{ color: C.muted, fontSize: 12, marginTop: 28, textAlign: "center" }}>
          Powered by{" "}
          <a href="https://openmagi.ai" style={{ color: C.prompt }} rel="noopener noreferrer">Magi</a>
          {" "}· a control plane for Claude Code
        </footer>
      </div>
    </main>
  )
}
