import { describe, it, expect } from "vitest"

import {
  DEFAULT_EVIDENCE_GATE_DRAFT,
  buildEvidenceGatePolicies,
  describeEvidenceGate,
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
