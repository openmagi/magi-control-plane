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
  // noindex (don't list in search), but allow link unfurls (og/twitter image).
  robots: { index: false, follow: false },
  openGraph: {
    title: "A governed agent run · Magi",
    description: "A Claude Code run, captured and governed by Magi.",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "A governed agent run · Magi",
    description: "A Claude Code run, captured and governed by Magi.",
  },
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
  blue: "#5B9BFF",
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

const MD_COMPONENTS = { img: () => null, a: MdAnchor }

/** Short detail from a tool call's redacted argsSummary (URL / query / command). */
function traceDetail(args: unknown): string {
  if (!args || typeof args !== "object") return ""
  const a = args as Record<string, unknown>
  const pick = a.url ?? a.query ?? a.command ?? a.path ?? a.file_path ?? a.pattern ?? a.prompt
  if (typeof pick !== "string") return ""
  if (/^https?:\/\//i.test(pick)) return shortUrl(pick)
  const oneLine = pick.replace(/\s+/g, " ").trim()
  return oneLine.length > 160 ? `${oneLine.slice(0, 159)}…` : oneLine
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
  return status === "blocked" || status === "needs_approval" || status === "rejected"
}
function markColor(status?: string | null): string {
  if (status === "blocked" || status === "rejected") return C.red
  if (status === "needs_approval") return C.amber
  if (status === "error" || status === "aborted") return C.red
  return C.green
}
function govVerb(status?: string | null): string {
  if (status === "blocked") return "BLOCKED"
  if (status === "rejected") return "REJECTED BY REVIEWER"
  if (status === "needs_approval") return "HELD FOR APPROVAL"
  if (status === "ok" || status === "verified" || status === "passed") return "VERIFIED"
  if (status === "error") return "ERROR"
  return (status ?? "").toUpperCase()
}
function govIcon(status?: string | null): string {
  if (status === "blocked") return "⛔"
  if (status === "rejected") return "🛑"
  if (status === "needs_approval") return "⏸"
  if (status === "error") return "✕"
  return "✓"
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
  // The final assistant wrap-up (prose after the last tool) is "what happened";
  // it moves to the right panel, derived from policy, not shown inline.
  let lastToolIdx = -1
  for (let i = items.length - 1; i >= 0; i--) {
    if (items[i].kind === "tool") { lastToolIdx = i; break }
  }
  const wrapup = items
    .filter((it, i) => it.kind === "text" && i > lastToolIdx && typeof it.text === "string" && it.text.trim())
    .map((it) => stripFootnoteTail(it.text as string))
    .join("\n\n")

  const mono = "ui-monospace, SFMono-Regular, Menlo, monospace"
  const card: CSSProperties = { background: C.bar, border: `1px solid ${C.border}`, borderRadius: 10, padding: "16px 18px", marginBottom: 14 }
  const label: CSSProperties = { color: C.muted, fontSize: 12, textTransform: "uppercase", letterSpacing: 0.6 }

  return (
    <main style={{ background: C.bg, color: C.text, minHeight: "100vh", fontFamily: mono }}>
      <style>{`
        .md p { margin: 4px 0; } .md strong { color: #E6F0E6; }
        .md.inline p { display: inline; margin: 0; }
        .run-grid { display: grid; grid-template-columns: 1fr; gap: 0 20px; align-items: start; }
        @media (min-width: 900px) { .run-grid { grid-template-columns: minmax(0,1.55fr) minmax(0,1fr); } }
        .run-aside { position: sticky; top: 16px; }
        @media (max-width: 899px) { .run-aside { position: static; } }
      `}</style>
      <div style={{ maxWidth: 1140, margin: "0 auto", padding: "40px 20px 56px" }}>
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

        <div className="run-grid">
          {/* LEFT: the run, replayed as the Claude Code TUI */}
          <div style={{ border: `1px solid ${C.border}`, borderRadius: 12, overflow: "hidden", background: C.termBg, boxShadow: "0 8px 40px rgba(0,0,0,0.4)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "11px 14px", background: C.bar, borderBottom: `1px solid ${C.border}` }}>
              {DOTS.map((d) => (
                <span key={d} style={{ width: 11, height: 11, borderRadius: "50%", background: d, display: "inline-block" }} />
              ))}
              <span style={{ color: C.muted, fontSize: 12.5, marginLeft: 8 }}>
                claude-code <span style={{ opacity: 0.6 }}>· governed by magi</span>
              </span>
            </div>

            <div style={{ padding: "16px 18px 6px", fontSize: 14, lineHeight: 1.55 }}>
              {/* user prompt */}
              {s.goal ? (
                <div style={{ background: "rgba(255,255,255,0.05)", borderRadius: 6, padding: "8px 10px", marginBottom: 16 }}>
                  <span style={{ color: C.muted, fontWeight: 700 }}>❯ </span>
                  <span style={{ color: "#E6F0E6" }}>{s.goal}</span>
                </div>
              ) : null}

              {/* paired narration (● bullet) + tool action subline */}
              {items.map((it, i) => {
                if (it.kind === "text") {
                  if (i > lastToolIdx) return null // wrap-up -> right panel
                  const body = typeof it.text === "string" ? it.text : ""
                  if (!body.trim()) return null
                  return (
                    <div key={i} style={{ display: "flex", gap: 8, marginTop: 12 }}>
                      <span style={{ color: C.green, fontWeight: 700, flexShrink: 0 }}>●</span>
                      <div className="md inline" style={{ color: C.text }}>
                        <Markdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>{citeify(body)}</Markdown>
                      </div>
                    </div>
                  )
                }
                const name = shortTool(it.name ?? "")
                const fullName = it.name ?? name
                const g = govByName.get(name)
                const detail = traceDetail(it.argsSummary)
                const held1 = it.status === "needs_approval"
                const blocked1 = it.status === "blocked"
                const stopped = isStopped(it.status)
                return (
                  <div key={i} style={{ paddingLeft: 16, marginTop: 4 }}>
                    {/* muted tool action line. Routine (non-policy) calls are
                        clamped to one line; a held/blocked step keeps its label. */}
                    <div
                      style={{
                        color: stopped ? markColor(it.status) : C.muted,
                        fontSize: 13,
                        ...(stopped ? {} : { whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }),
                      }}
                      title={detail || undefined}
                    >
                      {stopped ? `✗ ${name}` : `↳ ${name}`}{detail ? <span style={{ color: C.muted }}>({detail})</span> : null}
                      {stopped ? <span style={{ marginLeft: 8 }}>· {govVerb(it.status)}</span> : null}
                    </div>
                    {/* verification verdict (passed step) — detail collapsed */}
                    {g && !stopped && g.kind === "verification" && g.reason ? (
                      (() => {
                        const ok = g.status === "ok" || g.status === "verified" || g.status === "passed"
                        return (
                          <details style={{ paddingLeft: 16, marginTop: 2 }}>
                            <summary style={{ color: ok ? C.green : C.amber, fontSize: 13, cursor: "pointer", listStyle: "revert" }}>
                              {ok ? "✓ source verified credible" : "⚠ source not credible"}
                            </summary>
                            <div style={{ color: C.muted, fontSize: 13, marginTop: 4 }}>{g.reason}</div>
                          </details>
                        )
                      })()
                    ) : null}
                    {/* blocked (deny) gate note */}
                    {blocked1 && g ? (
                      <div style={{ color: C.muted, fontSize: 13, paddingLeft: 16, marginTop: 2 }}>
                        {(g.reason ?? "").replace(/^blocked by policy:\s*/i, "blocked: ")}
                      </div>
                    ) : null}
                    {/* held (pending) -> the real Claude Code approval prompt.
                        A rejected step shows only its one-line REJECTED label above. */}
                    {held1 ? (
                      <div style={{ marginTop: 10, borderTop: `1px solid ${C.blue}`, paddingTop: 12 }}>
                        <div style={{ color: C.blue, fontWeight: 700, marginBottom: 8 }}>Tool use</div>
                        <div style={{ paddingLeft: 12, marginBottom: 10 }}>
                          <div style={{ color: C.text }}>
                            {name}(<span style={{ color: C.muted }}>{argString(it.argsSummary)}</span>)
                            {fullName.startsWith("mcp__") ? <span style={{ color: C.muted }}> (MCP)</span> : null}
                          </div>
                          {g?.reason ? (
                            <div style={{ color: C.muted, fontSize: 13, marginTop: 2 }}>
                              held by magi policy · {(g.reason ?? "").replace(/^held:\s*/i, "")}
                            </div>
                          ) : null}
                        </div>
                        <div style={{ color: C.muted, fontSize: 13 }}>
                          Permission rule <span style={{ color: C.text, fontWeight: 700 }}>{fullName}</span> requires confirmation for this tool.
                        </div>
                        <div style={{ color: C.muted, fontSize: 13, marginBottom: 10 }}>/permissions to update rules</div>
                        <div style={{ color: C.text, marginBottom: 4 }}>Do you want to proceed?</div>
                        <div style={{ color: C.prompt }}>❯ 1. Yes</div>
                        <div style={{ color: C.muted }}>{"  "}2. Yes, and don&apos;t ask again for {name}</div>
                        <div style={{ color: C.muted }}>{"  "}3. No</div>
                        <div style={{ color: C.muted, fontSize: 12.5, marginTop: 8 }}>Esc to cancel · Tab to amend</div>
                      </div>
                    ) : null}
                  </div>
                )
              })}
            </div>

            {/* status bar */}
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", padding: "9px 16px", background: C.bar, borderTop: `1px solid ${C.border}`, color: C.muted, fontSize: 12.5, marginTop: 14 }}>
              <span style={{ color: C.coral, fontWeight: 700 }}>▶▶ governed by magi</span>
              <span>· {v.counts?.stepCount ?? trace.length} steps</span>
              {held > 0 ? <span style={{ color: C.amber }}>· {held} held</span> : null}
              {blocked > 0 ? <span style={{ color: C.red }}>· {blocked} blocked</span> : null}
              <span style={{ marginLeft: "auto" }}>{modelLabel(s.model)} · {(usage.inputTokens ?? 0).toLocaleString()} in / {(usage.outputTokens ?? 0).toLocaleString()} out</span>
            </div>
          </div>

          {/* RIGHT: what happened, in terms of the policy that was applied */}
          <aside className="run-aside">
            <section style={card}>
              <div style={label}>What happened</div>
              {governance.length > 0 ? (
                <>
                  <div style={{ color: C.text, fontSize: 13.5, margin: "8px 0 12px" }}>
                    Magi applied {governance.length} {governance.length === 1 ? "policy" : "policies"} to this run
                    {held > 0 || blocked > 0 ? <> — {[held ? `${held} held` : null, blocked ? `${blocked} blocked` : null].filter(Boolean).join(", ")} before running.</> : "."}
                  </div>
                  <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                    {governance.map((g, i) => {
                      const c = markColor(g.status)
                      return (
                        <li key={i} style={{ borderLeft: `2px solid ${c}`, padding: "2px 0 2px 12px", marginBottom: 12 }}>
                          <div style={{ fontSize: 12.5 }}>
                            <span style={{ color: c, fontWeight: 700 }}>{govIcon(g.status)} {govVerb(g.status)}</span>
                            <span style={{ color: C.muted }}> · {g.name}</span>
                          </div>
                          {g.reason ? (
                            <details style={{ marginTop: 3 }}>
                              <summary style={{ color: C.muted, fontSize: 12, cursor: "pointer" }}>details</summary>
                              <div style={{ color: C.text, fontSize: 13, marginTop: 3 }}>{g.reason}</div>
                            </details>
                          ) : null}
                        </li>
                      )
                    })}
                  </ul>
                </>
              ) : wrapup ? (
                <div className="md" style={{ color: C.text, fontSize: 13.5, marginTop: 8 }}>
                  <Markdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>{citeify(wrapup)}</Markdown>
                </div>
              ) : (
                <div style={{ color: C.muted, fontSize: 13, marginTop: 8 }}>No policy actions were triggered on this run.</div>
              )}
            </section>

            {/* sources / evidence */}
            {sources.length > 0 ? (
              <section style={card}>
                <div style={label}>Sources ({sources.length})</div>
                <ol style={{ margin: "8px 0 0", paddingLeft: 22, fontSize: 13 }}>
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
                        {cred ? <div style={{ color: credColor, marginTop: 2, fontSize: 12 }}>{credOk ? "✓ verified credible" : `⚠ ${cred.toLowerCase()}`}</div> : null}
                      </li>
                    )
                  })}
                </ol>
              </section>
            ) : null}
          </aside>
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
