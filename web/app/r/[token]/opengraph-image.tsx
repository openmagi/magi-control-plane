import { ImageResponse } from "next/og"

import { cloud } from "@/lib/cloud"

export const runtime = "nodejs"
export const size = { width: 1200, height: 630 }
export const contentType = "image/png"
export const alt = "A governed agent run, shared via Magi"

const C = {
  bg: "#0B0E0A",
  panel: "#11160F",
  border: "#1E261C",
  green: "#22C55E",
  prompt: "#7EE787",
  text: "#E6F0E6",
  muted: "#7A857A",
  red: "#EF4444",
  amber: "#F59E0B",
}
const DOTS = ["#FF5F56", "#FFBD2E", "#27C93F"]

function clip(s: string, n: number): string {
  const t = (s || "").replace(/\s+/g, " ").trim()
  return t.length > n ? `${t.slice(0, n - 1)}…` : t
}

export default async function Image({ params }: { params: { token: string } }) {
  const shared = await cloud.getSharedRun(params.token).catch(() => null)
  const v = shared?.view
  const s = v?.summary ?? {}
  const gov = Array.isArray(v?.governance) ? v!.governance : []
  const blocked = gov.filter((g) => g.status === "blocked").length
  const held = gov.filter((g) => g.status === "needs_approval").length
  const rejected = gov.filter((g) => g.status === "rejected").length
  const verified = gov.filter((g) => g.status === "ok" || g.status === "verified" || g.status === "passed").length

  const title = clip(s.title || s.goal || "Agent run", 84)
  const badges: { label: string; color: string }[] = []
  if (blocked) badges.push({ label: `${blocked} BLOCKED`, color: C.red })
  if (rejected) badges.push({ label: `${rejected} REJECTED`, color: C.red })
  if (held) badges.push({ label: `${held} HELD FOR APPROVAL`, color: C.amber })
  if (verified) badges.push({ label: `${verified} VERIFIED`, color: C.green })

  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", background: C.bg, padding: 64, fontFamily: "monospace" }}>
        {/* terminal title bar */}
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {DOTS.map((d) => (
            <div key={d} style={{ width: 18, height: 18, borderRadius: 18, background: d }} />
          ))}
          <div style={{ color: C.muted, fontSize: 26, marginLeft: 12 }}>claude-code · governed by magi</div>
        </div>

        <div style={{ display: "flex", color: C.prompt, fontSize: 30, marginTop: 56 }}>
          <span style={{ color: C.muted }}>magi&nbsp;·&nbsp;</span>governed agent run
        </div>

        <div style={{ display: "flex", color: C.text, fontSize: 60, fontWeight: 700, marginTop: 18, lineHeight: 1.15, maxWidth: 1040 }}>
          {title}
        </div>

        {/* governance badges */}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 16, marginTop: 40 }}>
          {badges.length > 0 ? badges.map((b) => (
            <div key={b.label} style={{ display: "flex", alignItems: "center", color: b.color, border: `2px solid ${b.color}`, borderRadius: 12, padding: "8px 20px", fontSize: 30 }}>
              {b.label}
            </div>
          )) : (
            <div style={{ display: "flex", color: C.muted, fontSize: 30 }}>policy-governed run</div>
          )}
        </div>

        <div style={{ display: "flex", flexGrow: 1 }} />

        {/* footer */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", borderTop: `2px solid ${C.border}`, paddingTop: 28 }}>
          <div style={{ display: "flex", color: C.muted, fontSize: 28 }}>
            <span style={{ color: C.prompt }}>openmagi.ai</span>&nbsp;· a control plane for Claude Code
          </div>
          <div style={{ display: "flex", color: C.muted, fontSize: 26 }}>
            {clip(typeof s.model === "string" ? s.model : "", 28)}
          </div>
        </div>
      </div>
    ),
    { ...size },
  )
}
