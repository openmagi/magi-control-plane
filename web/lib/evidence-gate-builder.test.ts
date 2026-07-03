import { describe, it, expect } from "vitest"

import {
  DEFAULT_EVIDENCE_GATE_DRAFT,
  buildEvidenceGateCompoundDraft,
  buildEvidenceGatePolicies,
  describeEvidenceGate,
  looksLikeEvidenceGateIntent,
  parseEvidenceGateIntent,
  validateEvidenceGateDraft,
  type EvidenceGateDraft,
} from "./evidence-gate-builder"

const clone = (): EvidenceGateDraft => JSON.parse(JSON.stringify(DEFAULT_EVIDENCE_GATE_DRAFT))

describe("validateEvidenceGateDraft", () => {
  it("default draft is valid", () => {
    expect(validateEvidenceGateDraft(DEFAULT_EVIDENCE_GATE_DRAFT)).toEqual([])
  })
  it("rejects a non-slug kind", () => {
    const d = clone(); d.kind = "Source Credibility!"
    expect(validateEvidenceGateDraft(d).map(e => e.field)).toContain("kind")
  })
  it("rejects an empty gate matcher", () => {
    const d = clone(); d.gate.matcher = "  "
    expect(validateEvidenceGateDraft(d).map(e => e.field)).toContain("gate.matcher")
  })
  it("rejects an over-long reason", () => {
    const d = clone(); d.gate.reason = "x".repeat(401)
    expect(validateEvidenceGateDraft(d).map(e => e.field)).toContain("gate.reason")
  })
  it("rejects an unknown verdict", () => {
    const d = clone(); (d.gate as { verdict: string }).verdict = "maybe"
    expect(validateEvidenceGateDraft(d).map(e => e.field)).toContain("gate.verdict")
  })
})

describe("buildEvidenceGatePolicies", () => {
  it("produces the audit + precondition pair joined on kind", () => {
    const [audit, gate] = buildEvidenceGatePolicies(DEFAULT_EVIDENCE_GATE_DRAFT)
    expect(audit.type).toBe("evidence_audit")
    expect(gate.type).toBe("evidence_precondition")
    expect(audit.id).toBe("verified-trade-audit")
    expect(gate.id).toBe("verified-trade-gate")
    // joined on kind
    expect(audit.kind).toBe("source_credibility")
    expect(gate.require_kind).toBe("source_credibility")
    expect(gate.require_verdict).toBe("pass")
    expect((audit.trigger as { event: string }).event).toBe("PostToolUse")
    expect((gate.trigger as { matcher: string }).matcher).toBe("mcp__trading__execute_trade")
  })
  it("carries the chosen action + reason onto the gate", () => {
    const d = clone(); d.gate.action = "ask"; d.gate.reason = "verify a source first"
    const [, gate] = buildEvidenceGatePolicies(d)
    expect(gate.action).toBe("ask")
    expect(gate.reason).toBe("verify a source first")
  })
})

describe("describeEvidenceGate", () => {
  it("summarizes block vs ask in plain english", () => {
    expect(describeEvidenceGate(DEFAULT_EVIDENCE_GATE_DRAFT)).toMatch(/^On mcp__trading__execute_trade, block unless/)
    const d = clone(); d.gate.action = "ask"
    expect(describeEvidenceGate(d)).toMatch(/hold for approval unless/)
  })
})

describe("project scope", () => {
  it("threads project_scope onto both policies when set", () => {
    const d = clone(); d.projectScope = "/Users/kevin/trading-mcp"
    const [audit, gate] = buildEvidenceGatePolicies(d)
    expect(audit.project_scope).toBe("/Users/kevin/trading-mcp")
    expect(gate.project_scope).toBe("/Users/kevin/trading-mcp")
  })
  it("empty scope stays empty (global)", () => {
    const [audit] = buildEvidenceGatePolicies(DEFAULT_EVIDENCE_GATE_DRAFT)
    expect(audit.project_scope).toBe("")
  })
  it("summary mentions the scope", () => {
    const d = clone(); d.projectScope = "/x/proj"
    expect(describeEvidenceGate(d)).toMatch(/only in \/x\/proj/)
  })
})

describe("parseEvidenceGateIntent", () => {
  it("detects the intent", () => {
    expect(looksLikeEvidenceGateIntent("verify a credible source before execute_trade")).toBe(true)
    expect(looksLikeEvidenceGateIntent("make me a sandwich")).toBe(false)
  })
  it("pulls the gated mcp tool + fetch tools + scope from a description", () => {
    const d = parseEvidenceGateIntent(
      "In ~/trading-mcp, before mcp__trading__execute_trade runs, require that a WebFetch or Bash verified a credible source; ask for approval if missing",
    )
    expect(d.gate.matcher).toBe("mcp__trading__execute_trade")
    expect(d.audit.matcher).toBe("WebFetch|Bash")
    expect(d.gate.action).toBe("ask")
    expect(d.projectScope).toBe("~/trading-mcp")
  })
  it("falls back to defaults when nothing matches", () => {
    const d = parseEvidenceGateIntent("require verification of the source first")
    expect(d.gate.matcher).toBe(DEFAULT_EVIDENCE_GATE_DRAFT.gate.matcher)
  })
})

describe("buildEvidenceGateCompoundDraft", () => {
  it("produces one compound evidence_gate draft the server can expand", () => {
    const d = clone(); d.projectScope = "/x/proj"
    const c = buildEvidenceGateCompoundDraft(d) as Record<string, any>
    expect(c.type).toBe("evidence_gate")
    expect(c.id).toBe(d.idStem)
    expect(c.kind).toBe(d.kind)
    expect(c.project_scope).toBe("/x/proj")
    expect(c.audit.matcher).toBe(d.audit.matcher)
    expect(c.gate.matcher).toBe(d.gate.matcher)
    expect(c.gate.action).toBe(d.gate.action)
  })
})
