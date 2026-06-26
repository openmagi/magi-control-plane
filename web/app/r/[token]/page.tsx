import type { CSSProperties, ReactNode } from "react"

import type { Metadata } from "next"
import { notFound } from "next/navigation"
import Markdown from "react-markdown"
import remarkGfm from "remark-gfm"

import { cloud } from "@/lib/cloud"
import { citeify, shortTool, shortUrl, stripFootnoteTail } from "@/lib/run-view-format"

export const dynamic = "force-dynamic"

export const metadata: Metadata = {
  title: "Shared agent run · Magi",
  description: "A Claude Code run, captured and governed by Magi.",
  robots: { index: false, follow: false },
}

const C = {
  bg: "#0B0E0A",
  termBg: "#0C100B",
  bar: "#11160F",
  border: "#1E261C",
  green: "#22C55E",
  prompt: "#7EE787",
  text: "#C9D1C9",
  muted: "#7A857A",
  red: "#EF4444",
  amber: "#F59E0B",
  coral: "#F97A5A",
}
const DOTS = ["#FF5F56", "#FFBD2E", "#27C93F"]

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

const citeChip: CSSProperties = {
  display: "inline-block", verticalAlign: "super", fontSize: 10, lineHeight: 1.1,
  color: C.bg, background: C.green, borderRadius: 4, padding: "1px 5px",
  margin: "0 1px", textDecoration: "none", fontWeight: 700,
}

type MdAnchorProps = { href?: string; children?: ReactNode }

/** Markdown link renderer: an in-page `#src-N` citation becomes a raised green
 *  chip that jumps to the source; any other link is gated to http(s). */
function MdAnchor({ href, children }: MdAnchorProps) {
  const h = typeof href === "string" ? href : ""
  if (/^#src-\d{1,3}$/.test(h)) {
    return <a href={h} style={citeChip}>{children}</a>
  }
  const safe = safeHref(h)
  return safe ? (
    <a href={safe} style={{ color: C.prompt }} rel="noopener noreferrer nofollow" target="_blank">{children}</a>
  ) : (
    <span>{children}</span>
  )
}

/** Short detail from a tool call's redacted argsSummary (URL / query / command). */
function traceDetail(args: unknown): string {
  if (!args || typeof args !== "object") return ""
  const a = args as Record<string, unknown>
  const pick = a.url ?? a.query ?? a.command ?? a.path ?? a.file_path ?? a.pattern ?? a.prompt
  if (typeof pick !== "string") return ""
  return /^https?:\/\//i.test(pick) ? shortUrl(pick) : pick
}

/** Format a tool call's args as `key: value, …` for the approval prompt. */
function argString(args: unknown, max = 120): string {
  if (!args || typeof args !== "object") return ""
  const parts: string[] = []
  for (const [k, val] of Object.entries(args as Record<string, unknown>)) {
    let rendered: string
    if (typeof val === "string") rendered = /^https?:\/\//i.test(val) ? shortUrl(val, 40) : `"${val}"`
    else rendered = JSON.stringify(val)
    parts.push(`${k}: ${rendered}`)
  }
  const joined = parts.join(", ")
  return joined.length > max ? `${joined.slice(0, max - 1)}…` : joined
}

function isStopped(status?: string | null): boolean {
  return status === "blocked" || status === "needs_approval"
}
function markColor(status?: string | null): string {
  if (status === "blocked") return C.red
  if (status === "needs_approval") return C.amber
  if (status === "error" || status === "aborted") return C.red
  return C.green
}
function govVerb(status?: string | null): string {
  if (status === "blocked") return "BLOCKED"
  if (status === "needs_approval") return "HELD FOR APPROVAL"
  if (status === "ok" || status === "verified" || status === "passed") return "VERIFIED"
  if (status === "error") return "ERROR"
  return (status ?? "").toUpperCase()
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

  // Index governance by tool name so each transcript step can show its verdict
  // / hold reason inline (the gate block, the differentiator vs a chat log).
  const govByName = new Map<string, { name?: string; status?: string; reason?: string; kind?: string }>()
  for (const g of governance) {
    const key = g.name ? shortTool(g.name) : ""
    if (key && !govByName.has(key)) govByName.set(key, g)
  }

  // Replay the run in order. Prefer the producer's interleaved transcript;
  // fall back to (tool steps -> final answer) for older stored views.
  type TItem = { kind: string; text?: string | null; name?: string | null; status?: string | null; argsSummary?: unknown }
  const rawTranscript = Array.isArray(v.transcript) ? v.transcript : []
  const items: TItem[] =
    rawTranscript.length > 0
      ? rawTranscript.map((t) => ({ kind: t.kind ?? "tool", text: t.text, name: t.name, status: t.status, argsSummary: t.argsSummary }))
      : [
          ...trace.map((t) => ({ kind: "tool", name: t.name, status: t.status, argsSummary: t.argsSummary })),
          ...(s.result ? [{ kind: "text", text: s.result }] : []),
        ]
  const mono = "ui-monospace, SFMono-Regular, Menlo, monospace"

  const lbl: CSSProperties = { color: C.muted, display: "inline-block", minWidth: 78 }
  const subRow: CSSProperties = { marginTop: 3, paddingLeft: 22, color: C.text, fontSize: 13.5 }

  return (
    <main style={{ background: C.bg, color: C.text, minHeight: "100vh", fontFamily: mono }}>
      <style>{`.md p { margin: 6px 0; } .md strong { color: #E6F0E6; }`}</style>
      <div style={{ maxWidth: 820, margin: "0 auto", padding: "40px 20px 56px" }}>
        <div style={{ color: C.prompt, fontSize: 13, marginBottom: 6 }}>
          <span style={{ color: C.muted }}>magi ·</span> governed agent run
        </div>
        <h1 style={{ fontSize: 21, fontWeight: 600, margin: "0 0 8px" }}>{s.title || "Agent run"}</h1>
        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", marginBottom: 18, fontSize: 13 }}>
          <span style={{ color: markColor(s.status === "completed" ? "ok" : s.status) }}>● {s.status ?? "unknown"}</span>
          {blocked > 0 ? (
            <span style={{ color: C.red, border: `1px solid ${C.red}`, borderRadius: 6, padding: "2px 8px" }}>🛡 {blocked} blocked</span>
          ) : null}
          {held > 0 ? (
            <span style={{ color: C.amber, border: `1px solid ${C.amber}`, borderRadius: 6, padding: "2px 8px" }}>⏸ {held} held for approval</span>
          ) : null}
        </div>

        {/* Terminal window: the run, styled as the Claude Code TUI */}
        <div style={{ border: `1px solid ${C.border}`, borderRadius: 12, overflow: "hidden", background: C.termBg, boxShadow: "0 8px 40px rgba(0,0,0,0.4)" }}>
          {/* title bar */}
          <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "11px 14px", background: C.bar, borderBottom: `1px solid ${C.border}` }}>
            {DOTS.map((d) => (
              <span key={d} style={{ width: 11, height: 11, borderRadius: "50%", background: d, display: "inline-block" }} />
            ))}
            <span style={{ color: C.muted, fontSize: 12.5, marginLeft: 8 }}>
              claude-code <span style={{ opacity: 0.6 }}>· governed by magi</span>
            </span>
          </div>

          {/* transcript body */}
          <div style={{ padding: "18px 18px 6px", fontSize: 14, lineHeight: 1.55 }}>
            {/* user prompt */}
            {s.goal ? (
              <div style={{ marginBottom: 16 }}>
                <span style={{ color: C.green, fontWeight: 700 }}>{"> "}</span>
                <span style={{ color: "#E6F0E6" }}>{s.goal}</span>
              </div>
            ) : null}

            {/* the run, replayed in order: prose and tool calls interleaved */}
            {items.slice(0, 240).map((it, i) => {
              if (it.kind === "text") {
                const body = stripFootnoteTail(typeof it.text === "string" ? it.text : "")
                if (!body.trim()) return null
                return (
                  <div key={i} className="md" style={{ color: C.text, margin: "14px 0" }}>
                    <Markdown remarkPlugins={[remarkGfm]} components={{ img: () => null, a: MdAnchor }}>
                      {citeify(body)}
                    </Markdown>
                  </div>
                )
              }
              const name = shortTool(it.name ?? "")
              const g = govByName.get(name)
              const detail = traceDetail(it.argsSummary)
              const stopped = isStopped(it.status)
              const held = it.status === "needs_approval"
              const mc = markColor(it.status)
              return (
                <div key={i} style={{ marginBottom: 12 }}>
                  <div>
                    <span style={{ color: mc, fontWeight: 700 }}>{stopped ? "✗" : "●"} </span>
                    <span style={{ color: C.text }}>{name}</span>
                    {detail ? <span style={{ color: C.muted }}>({detail})</span> : null}
                    {stopped ? <span style={{ color: mc, marginLeft: 8 }}>· {govVerb(it.status)}</span> : null}
                  </div>
                  {/* verification verdict (passed step) */}
                  {g && !stopped && g.kind === "verification" && g.reason ? (
                    <div style={subRow}><span style={{ color: C.green }}>✓ </span><span>{g.reason}</span></div>
                  ) : null}
                  {/* blocked (deny) gate block */}
                  {g && stopped && !held ? (
                    <>
                      <div style={subRow}><span style={lbl}>rule</span><span style={{ color: C.text }}>{(g.reason ?? "").replace(/^blocked by policy:\s*/i, "") || "governed by policy"}</span></div>
                      <div style={{ ...subRow, color: C.muted, marginTop: 5 }}>↳ revise and retry, or route to a human</div>
                    </>
                  ) : null}
                  {/* held -> the actual Claude Code approval prompt */}
                  {held ? (
                    <div style={{ marginTop: 8, marginLeft: 22, border: `1px solid ${C.amber}`, borderRadius: 8, overflow: "hidden", maxWidth: 560 }}>
                      <div style={{ background: "rgba(245,158,11,0.10)", padding: "8px 12px", borderBottom: `1px solid ${C.amber}`, color: C.amber, fontSize: 12.5, fontWeight: 700 }}>
                        Tool use · approval required
                      </div>
                      <div style={{ padding: "10px 12px", fontSize: 13.5 }}>
                        <div style={{ color: C.text, marginBottom: 8 }}>
                          <span style={{ color: C.prompt }}>{name}</span>
                          <span style={{ color: C.muted }}>({argString(it.argsSummary)})</span>
                        </div>
                        <div style={{ color: C.text, marginBottom: 6 }}>Do you want to proceed?</div>
                        <div style={{ color: C.green }}>❯ 1. Yes</div>
                        <div style={{ color: C.muted }}>{"  "}2. Yes, and don&apos;t ask again for {name}</div>
                        <div style={{ color: C.muted }}>{"  "}3. No, and tell Claude what to do differently <span style={{ opacity: 0.7 }}>(esc)</span></div>
                        {g?.reason ? (
                          <div style={{ marginTop: 8, paddingTop: 8, borderTop: `1px solid ${C.border}`, color: C.muted, fontSize: 12.5 }}>
                            held by magi policy · {(g.reason ?? "").replace(/^held:\s*/i, "")}
                          </div>
                        ) : null}
                      </div>
                    </div>
                  ) : null}
                </div>
              )
            })}

            {/* deliverables */}
            {results.length > 0 ? (
              <div style={{ marginTop: 8, marginBottom: 12 }}>
                <span style={{ color: C.muted }}>deliverables  </span>
                {results.slice(0, 50).map((r, i) => {
                  const href = safeHref(r.prUrl)
                  const text = r.prNumber ? `PR #${r.prNumber}` : (r.prUrl ?? "—")
                  return (
                    <span key={i} style={{ marginRight: 12 }}>
                      {href ? <a href={href} style={{ color: C.prompt }} rel="noopener noreferrer nofollow" target="_blank">{text}</a> : <span>{text}</span>}
                    </span>
                  )
                })}
              </div>
            ) : null}

            {/* sources footer (numbered to match the inline ¹ chips) */}
            {sources.length > 0 ? (
              <div style={{ marginTop: 10, marginBottom: 6, borderTop: `1px solid ${C.border}`, paddingTop: 12 }}>
                <div style={{ color: C.muted, fontSize: 12, marginBottom: 6 }}>sources</div>
                <ol style={{ margin: 0, paddingLeft: 22, fontSize: 13.5 }}>
                  {sources.slice(0, 50).map((src, i) => {
                    const href = src.isUrl ? safeHref(src.ref) : null
                    const cred = (src.credibility ?? "").toUpperCase()
                    const credOk = /\b(CREDIBLE|VERIFIED|TRUSTED|RELIABLE|OFFICIAL)\b/.test(cred) && !/NOT_?CREDIBLE|NOT CREDIBLE|UNVERIFIED/.test(cred)
                    const credColor = !cred ? C.muted : credOk ? C.green : C.amber
                    return (
                      <li id={`src-${i + 1}`} key={i} style={{ padding: "3px 0", scrollMarginTop: 16 }}>
                        {href ? (
                          <a href={href} title={src.ref} style={{ color: C.prompt }} rel="noopener noreferrer nofollow" target="_blank">{shortUrl(src.ref ?? "")}</a>
                        ) : (
                          <span style={{ color: C.text, wordBreak: "break-word" }}>{src.ref}</span>
                        )}
                        {cred ? <span style={{ color: credColor, marginLeft: 8 }}>{credOk ? "✓ verified credible" : `⚠ ${cred.toLowerCase()}`}</span> : null}
                      </li>
                    )
                  })}
                </ol>
              </div>
            ) : null}
          </div>

          {/* status bar */}
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", padding: "9px 16px", background: C.bar, borderTop: `1px solid ${C.border}`, color: C.muted, fontSize: 12.5 }}>
            <span style={{ color: C.coral, fontWeight: 700 }}>▶▶ governed by magi</span>
            <span>· {v.counts?.stepCount ?? trace.length} steps</span>
            {held > 0 ? <span style={{ color: C.amber }}>· {held} held</span> : null}
            {blocked > 0 ? <span style={{ color: C.red }}>· {blocked} blocked</span> : null}
            <span style={{ marginLeft: "auto" }}>{modelLabel(s.model)} · {(usage.inputTokens ?? 0).toLocaleString()} in / {(usage.outputTokens ?? 0).toLocaleString()} out</span>
          </div>
        </div>

        <footer style={{ color: C.muted, fontSize: 12, marginTop: 22, textAlign: "center" }}>
          Powered by{" "}
          <a href="https://openmagi.ai" style={{ color: C.prompt }} rel="noopener noreferrer">Magi</a>
          {" "}· a control plane for Claude Code
        </footer>
      </div>
    </main>
  )
}
