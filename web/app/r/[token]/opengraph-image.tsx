import { ImageResponse } from "next/og"

import { cloud } from "@/lib/cloud"

export const runtime = "nodejs"
export const size = { width: 1200, height: 630 }
export const contentType = "image/png"
export const alt = "A policy-enforced agent run, governed by Magi"

const C = {
  bg: "#0B0E0A",
  green: "#22C55E",
  prompt: "#7EE787",
  text: "#E6F0E6",
  muted: "#7A857A",
  red: "#EF4444",
  amber: "#F59E0B",
  coral: "#D97757", // Claude Code brand
  border: "#1E261C",
}

function clip(s: string, n: number): string {
  const t = (s || "").replace(/\s+/g, " ").trim()
  return t.length > n ? `${t.slice(0, n - 1)}…` : t
}

function shortTool(name: string): string {
  return name && name.includes("__") ? name.split("__").pop() || name : name || "action"
}

// Claude's sparkle mark, drawn (no emoji font needed).
function Sparkle({ size: sz = 34, color = C.coral }: { size?: number; color?: string }) {
  return (
    <svg width={sz} height={sz} viewBox="0 0 24 24">
      <g stroke={color} strokeWidth="2.4" strokeLinecap="round">
        <line x1="12" y1="2.5" x2="12" y2="21.5" />
        <line x1="2.5" y1="12" x2="21.5" y2="12" />
        <line x1="5.3" y1="5.3" x2="18.7" y2="18.7" />
        <line x1="18.7" y1="5.3" x2="5.3" y2="18.7" />
      </g>
    </svg>
  )
}

export default async function Image({ params }: { params: { token: string } }) {
  const shared = await cloud.getSharedRun(params.token).catch(() => null)
  const v = shared?.view
  const s = v?.summary ?? {}
  const gov = Array.isArray(v?.governance) ? v!.governance : []
  const pick = (st: string) => gov.find((g) => g.status === st)
  const blocked = gov.filter((g) => g.status === "blocked").length
  const rejected = gov.filter((g) => g.status === "rejected").length
  const held = gov.filter((g) => g.status === "needs_approval").length
  const verified = gov.filter((g) => g.status === "ok" || g.status === "verified" || g.status === "passed").length

  let accent = C.green
  let headline = "POLICY-GOVERNED RUN"
  let action = "every step checked against policy"
  if (blocked) { accent = C.red; headline = "BLOCKED BY POLICY"; action = `${shortTool(pick("blocked")?.name ?? "")} was not allowed to run` }
  else if (rejected) { accent = C.red; headline = "STOPPED AT THE GATE"; action = `${shortTool(pick("rejected")?.name ?? "")} declined by the reviewer` }
  else if (held) { accent = C.amber; headline = "HELD FOR HUMAN APPROVAL"; action = `${shortTool(pick("needs_approval")?.name ?? "")} requires sign-off before it runs` }
  else if (verified) { accent = C.green; headline = "VERIFIED & GOVERNED"; action = `${verified} source${verified > 1 ? "s" : ""} credibility-checked by policy` }

  const chips: { label: string; color: string }[] = []
  if (verified) chips.push({ label: `${verified} verified`, color: C.green })
  if (held) chips.push({ label: `${held} held`, color: C.amber })
  if (rejected) chips.push({ label: `${rejected} rejected`, color: C.red })
  if (blocked) chips.push({ label: `${blocked} blocked`, color: C.red })

  const query = clip(s.goal || s.title || "Agent run", 78)

  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", background: C.bg }}>
        <div style={{ width: 18, height: "100%", background: accent, display: "flex" }} />

        <div style={{ display: "flex", flexDirection: "column", flexGrow: 1, padding: "48px 60px" }}>
          {/* brand lockup: Magi control plane + Claude Code (coral, with sparkle) */}
          <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
            <div style={{ display: "flex", alignItems: "center", color: C.green, border: `2px solid ${C.green}`, borderRadius: 999, padding: "6px 18px", fontSize: 23, fontWeight: 700, letterSpacing: 2 }}>
              MAGI CONTROL PLANE
            </div>
            <div style={{ display: "flex", color: C.muted, fontSize: 24 }}>for</div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Sparkle size={30} />
              <div style={{ display: "flex", color: C.coral, fontSize: 28, fontWeight: 800 }}>Claude Code</div>
            </div>
          </div>

          {/* HERO: shield + the policy outcome */}
          <div style={{ display: "flex", alignItems: "center", gap: 24, marginTop: 40 }}>
            <svg width="86" height="86" viewBox="0 0 24 24" fill="none">
              <path d="M12 2 L20 5 V11 C20 16.2 16.4 20.1 12 22 C7.6 20.1 4 16.2 4 11 V5 Z" fill={accent} fillOpacity="0.16" stroke={accent} strokeWidth="1.6" />
              <path d="M8.4 12.2 L11 14.8 L15.8 9.4" stroke={accent} strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <div style={{ display: "flex", flexDirection: "column" }}>
              <div style={{ display: "flex", color: accent, fontSize: 58, fontWeight: 800, lineHeight: 1.05 }}>{headline}</div>
              <div style={{ display: "flex", color: C.text, fontSize: 29, marginTop: 8 }}>{clip(action, 62)}</div>
            </div>
          </div>

          {/* the user's prompt, styled like the Claude Code composer */}
          <div style={{ display: "flex", flexDirection: "column", marginTop: 36, border: `1px solid ${C.border}`, borderRadius: 12, padding: "16px 20px", background: "#0E120C" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              {/* chevron (drawn) */}
              <svg width="16" height="22" viewBox="0 0 16 22"><path d="M3 4 L11 11 L3 18" stroke={C.coral} strokeWidth="2.4" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
              <div style={{ display: "flex", color: C.text, fontSize: 28 }}>{query}</div>
              {/* cursor block */}
              <div style={{ display: "flex", width: 13, height: 26, background: C.text, opacity: 0.85 }} />
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 12, color: C.coral, fontSize: 19 }}>
              <svg width="20" height="14" viewBox="0 0 20 14"><path d="M2 2 L8 7 L2 12 Z M10 2 L16 7 L10 12 Z" fill={C.coral} /></svg>
              <div style={{ display: "flex", color: C.muted }}>bypass permissions on · governed by magi</div>
            </div>
          </div>

          <div style={{ display: "flex", flexGrow: 1 }} />

          {chips.length > 0 ? (
            <div style={{ display: "flex", gap: 14, marginBottom: 18 }}>
              {chips.map((c) => (
                <div key={c.label} style={{ display: "flex", color: c.color, border: `2px solid ${c.color}`, borderRadius: 10, padding: "5px 15px", fontSize: 23 }}>{c.label}</div>
              ))}
            </div>
          ) : null}

          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", borderTop: `2px solid ${C.border}`, paddingTop: 22 }}>
            <div style={{ display: "flex", color: C.muted, fontSize: 25 }}>
              <span style={{ color: C.prompt }}>openmagi.ai</span>&nbsp;· deterministic policy + audit for AI agents
            </div>
            <div style={{ display: "flex", color: C.muted, fontSize: 23 }}>{clip(typeof s.model === "string" ? s.model : "", 22)}</div>
          </div>
        </div>
      </div>
    ),
    { ...size },
  )
}
