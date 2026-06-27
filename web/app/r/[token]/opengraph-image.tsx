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
}

function clip(s: string, n: number): string {
  const t = (s || "").replace(/\s+/g, " ").trim()
  return t.length > n ? `${t.slice(0, n - 1)}…` : t
}

function shortTool(name: string): string {
  return name && name.includes("__") ? name.split("__").pop() || name : name || "action"
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

  // The hero is the single most consequential thing the policy did.
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

  const title = clip(s.title || s.goal || "Agent run", 76)

  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", background: C.bg }}>
        {/* severity rail — frames the run as gated by a control plane */}
        <div style={{ width: 18, height: "100%", background: accent, display: "flex" }} />

        <div style={{ display: "flex", flexDirection: "column", flexGrow: 1, padding: "56px 64px" }}>
          {/* brand line */}
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <div style={{ display: "flex", alignItems: "center", color: C.green, border: `2px solid ${C.green}`, borderRadius: 999, padding: "6px 18px", fontSize: 24, fontWeight: 700, letterSpacing: 2 }}>
              MAGI CONTROL PLANE
            </div>
            <div style={{ display: "flex", color: C.muted, fontSize: 24 }}>for Claude Code</div>
          </div>

          {/* HERO: shield + the policy outcome */}
          <div style={{ display: "flex", alignItems: "center", gap: 26, marginTop: 54 }}>
            <svg width="92" height="92" viewBox="0 0 24 24" fill="none">
              <path d="M12 2 L20 5 V11 C20 16.2 16.4 20.1 12 22 C7.6 20.1 4 16.2 4 11 V5 Z" fill={accent} fillOpacity="0.16" stroke={accent} strokeWidth="1.6" />
              <path d="M8.4 12.2 L11 14.8 L15.8 9.4" stroke={accent} strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <div style={{ display: "flex", flexDirection: "column" }}>
              <div style={{ display: "flex", color: accent, fontSize: 62, fontWeight: 800, lineHeight: 1.05 }}>{headline}</div>
              <div style={{ display: "flex", color: C.text, fontSize: 30, marginTop: 8 }}>{clip(action, 64)}</div>
            </div>
          </div>

          {/* the run this happened on */}
          <div style={{ display: "flex", color: C.muted, fontSize: 27, marginTop: 30 }}>
            <span style={{ color: C.prompt }}>❯&nbsp;</span>{title}
          </div>

          <div style={{ display: "flex", flexGrow: 1 }} />

          {/* pipeline chips */}
          {chips.length > 0 ? (
            <div style={{ display: "flex", gap: 14, marginBottom: 20 }}>
              {chips.map((c) => (
                <div key={c.label} style={{ display: "flex", color: c.color, border: `2px solid ${c.color}`, borderRadius: 10, padding: "6px 16px", fontSize: 24 }}>{c.label}</div>
              ))}
            </div>
          ) : null}

          {/* footer */}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", borderTop: "2px solid #1E261C", paddingTop: 24 }}>
            <div style={{ display: "flex", color: C.muted, fontSize: 26 }}>
              <span style={{ color: C.prompt }}>openmagi.ai</span>&nbsp;· deterministic policy + audit for AI agents
            </div>
            <div style={{ display: "flex", color: C.muted, fontSize: 24 }}>{clip(typeof s.model === "string" ? s.model : "", 22)}</div>
          </div>
        </div>
      </div>
    ),
    { ...size },
  )
}
