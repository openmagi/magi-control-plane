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
}

type ModelField = string | { label?: string | null; provider?: string | null } | null | undefined

function modelLabel(model: ModelField): string {
  if (typeof model === "string") return model || "—"
  if (model && typeof model === "object") {
    return [model.provider, model.label].filter(Boolean).join(" / ") || "—"
  }
  return "—"
}

/** Only http(s) hrefs reach the DOM. prUrl is model-influenceable; a
 *  `javascript:`/`data:` scheme would be an XSS vector on this public page
 *  (React does not sanitize href schemes). */
function safeHref(url?: string): string | null {
  if (!url) return null
  return /^https?:\/\//i.test(url) ? url : null
}

/** A short, human-readable detail from a tool call's redacted argsSummary:
 *  the URL fetched, the query searched, the command run, etc. */
function traceDetail(args: unknown): string {
  if (!args || typeof args !== "object") return ""
  const a = args as Record<string, unknown>
  const pick = a.url ?? a.query ?? a.command ?? a.path ?? a.file_path ?? a.pattern ?? a.prompt
  if (typeof pick === "string") return pick
  return ""
}

function statusColor(status?: string | null): string {
  if (status === "ok" || status === "completed") return C.green
  if (status === "blocked" || status === "needs_approval") return "#F59E0B"
  if (status === "error" || status === "aborted") return "#EF4444"
  return C.muted
}

export default async function SharedRunPage({
  params,
}: { params: { token: string } }) {
  const shared = await cloud.getSharedRun(params.token).catch(() => null)
  if (!shared) notFound()

  const v = shared.view
  const s = v.summary ?? {}
  const usage = s.usage ?? {}
  // Coerce to arrays: a malformed/model-influenced view must render partial,
  // not throw and 500 this public page.
  const trace = Array.isArray(v.trace) ? v.trace : []
  const governance = Array.isArray(v.governance) ? v.governance : []
  const results = Array.isArray(v.results) ? v.results : []
  const sources = Array.isArray(v.sources) ? v.sources : []

  const card: CSSProperties = {
    background: C.panel,
    border: `1px solid ${C.border}`,
    borderRadius: 10,
    padding: "16px 18px",
    marginBottom: 14,
  }
  const label: CSSProperties = { color: C.muted, fontSize: 12, textTransform: "uppercase", letterSpacing: 0.6 }
  const mono = "ui-monospace, SFMono-Regular, Menlo, monospace"

  return (
    <main style={{ background: C.bg, color: C.text, minHeight: "100vh", fontFamily: mono }}>
      <div style={{ maxWidth: 860, margin: "0 auto", padding: "40px 20px 64px" }}>
        <div style={{ color: C.prompt, fontSize: 13, marginBottom: 6 }}>
          <span style={{ color: C.muted }}>magi ·</span> shared agent run
        </div>
        <h1 style={{ fontSize: 22, fontWeight: 600, margin: "0 0 4px" }}>
          {s.title || "Agent run"}
        </h1>
        <div style={{ color: statusColor(s.status), fontSize: 13, marginBottom: 22 }}>
          ● {s.status ?? "unknown"}
        </div>

        {/* Summary */}
        <section style={card}>
          {s.goal ? (
            <div style={{ marginBottom: 12 }}>
              <div style={label}>Goal</div>
              <div style={{ marginTop: 4 }}>{s.goal}</div>
            </div>
          ) : null}
          {s.result ? (
            <div style={{ marginBottom: 12 }}>
              <div style={label}>Result</div>
              <div className="md" style={{ marginTop: 4 }}>
                {/* drop images: no third-party fetch from a public page */}
                <Markdown remarkPlugins={[remarkGfm]} components={{ img: () => null }}>
                  {s.result}
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

        {/* Sources (research evidence: where the agent looked) */}
        {sources.length > 0 ? (
          <section style={card}>
            <div style={label}>Sources ({sources.length})</div>
            <ul style={{ listStyle: "none", padding: 0, margin: "8px 0 0", fontSize: 13 }}>
              {sources.slice(0, 50).map((src, i) => {
                const href = src.isUrl ? safeHref(src.ref) : null
                return (
                  <li key={i} style={{ display: "flex", gap: 10, padding: "3px 0", borderBottom: `1px solid ${C.border}` }}>
                    <span style={{ color: C.muted, minWidth: 78 }}>{src.tool}</span>
                    {href ? (
                      <a href={href} style={{ color: C.prompt, wordBreak: "break-all" }} rel="noopener noreferrer nofollow" target="_blank">
                        {src.ref}
                      </a>
                    ) : (
                      <span style={{ color: C.text, wordBreak: "break-all" }}>{src.ref}</span>
                    )}
                  </li>
                )
              })}
            </ul>
            {sources.length > 50 ? (
              <div style={{ color: C.muted, marginTop: 6, fontSize: 12 }}>+{sources.length - 50} more</div>
            ) : null}
          </section>
        ) : null}

        {/* Results (PR links) */}
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
                      <a href={href} style={{ color: C.prompt }} rel="noopener noreferrer nofollow" target="_blank">
                        {text}
                      </a>
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

        {/* Governance */}
        {governance.length > 0 ? (
          <section style={card}>
            <div style={label}>Governance ({governance.length})</div>
            <ul style={{ listStyle: "none", padding: 0, margin: "8px 0 0", fontSize: 13 }}>
              {governance.map((g, i) => (
                <li key={i} style={{ marginBottom: 4 }}>
                  <span style={{ color: statusColor(g.status) }}>{g.status}</span>{" "}
                  <span style={{ color: C.text }}>{g.name}</span>
                  {g.reason ? <span style={{ color: C.muted }}> — {g.reason}</span> : null}
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {/* Trace */}
        {trace.length > 0 ? (
          <section style={card}>
            <div style={label}>Trace ({trace.length})</div>
            <ul style={{ listStyle: "none", padding: 0, margin: "8px 0 0", fontSize: 13 }}>
              {trace.slice(0, 200).map((t, i) => {
                const detail = traceDetail(t.argsSummary)
                return (
                  <li key={i} style={{ display: "flex", gap: 10, padding: "3px 0", borderBottom: `1px solid ${C.border}`, alignItems: "baseline" }}>
                    <span style={{ color: statusColor(t.status), width: 8, flexShrink: 0 }}>●</span>
                    <span style={{ color: C.text, minWidth: 96, flexShrink: 0 }}>{t.name}</span>
                    {detail ? (
                      <span style={{ color: C.muted, wordBreak: "break-all", flex: 1 }}>{detail}</span>
                    ) : (
                      <span style={{ color: C.muted, flex: 1 }}>{t.activityType}</span>
                    )}
                    {t.durationMs ? <span style={{ color: C.muted, flexShrink: 0 }}>{t.durationMs}ms</span> : null}
                  </li>
                )
              })}
            </ul>
            {trace.length > 200 ? (
              <div style={{ color: C.muted, marginTop: 6, fontSize: 12 }}>+{trace.length - 200} more steps</div>
            ) : null}
          </section>
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
