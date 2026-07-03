/** Authoring model for the session-evidence pair (audit + precondition).
 *
 * A user authors ONE "evidence gate": "before <tool> runs, require that a
 * <kind> check passed earlier this session." That compiles to two coupled IR
 * policies joined on the evidence `kind`:
 *   - evidence_audit        records `kind` about the calls it matches
 *   - evidence_precondition denies the gated event unless `kind` is on record
 *
 * Pure + framework-free so it is unit-testable; the page just PUTs the two dicts
 * this returns. Mirrors the server IR constraints so the UI catches errors early.
 */

export type EvidenceVerdict = "pass" | "fail" | "review"
export type GateAction = "block" | "ask"

export type EvidenceGateDraft = {
  /** Join key + evidence label, e.g. "source_credibility". [a-z0-9_]+. */
  kind: string
  /** Policy-id stem; produces `<idStem>-audit` and `<idStem>-gate`. */
  idStem: string
  description: string
  audit: {
    event: string // e.g. "PostToolUse"
    matcher: string // e.g. "WebFetch|Bash"
    extract: string // "url"
    judge: string // "domain-credibility"
  }
  gate: {
    event: string // e.g. "PreToolUse"
    matcher: string // e.g. "mcp__trading__execute_trade"
    action: GateAction
    verdict: EvidenceVerdict
    reason: string
  }
}

export const EVIDENCE_JUDGES = ["domain-credibility"] as const
export const EVIDENCE_EXTRACTS = ["url"] as const
export const EVIDENCE_VERDICTS: EvidenceVerdict[] = ["pass", "fail", "review"]
export const GATE_ACTIONS: GateAction[] = ["block", "ask"]

const KIND_RE = /^[a-z0-9_]+$/
const ID_STEM_RE = /^[a-z0-9][a-z0-9._-]*$/i

export const DEFAULT_EVIDENCE_GATE_DRAFT: EvidenceGateDraft = {
  kind: "source_credibility",
  idStem: "verified-trade",
  description: "Require a credible source before placing a trade",
  audit: { event: "PostToolUse", matcher: "WebFetch|Bash", extract: "url", judge: "domain-credibility" },
  gate: {
    event: "PreToolUse",
    matcher: "mcp__trading__execute_trade",
    action: "block",
    verdict: "pass",
    reason: "This run has no verified credible source yet. Retrieve the figure from an official primary source first, then retry.",
  },
}

export type DraftError = { field: string; message: string }

export function validateEvidenceGateDraft(d: EvidenceGateDraft): DraftError[] {
  const e: DraftError[] = []
  if (!KIND_RE.test(d.kind)) e.push({ field: "kind", message: "kind must be lowercase letters, digits, or underscore" })
  if (d.kind.length > 128) e.push({ field: "kind", message: "kind too long" })
  if (!ID_STEM_RE.test(d.idStem)) e.push({ field: "idStem", message: "id must start alphanumeric (letters, digits, . _ -)" })
  if (!d.audit.matcher.trim()) e.push({ field: "audit.matcher", message: "choose which tools to credibility-check" })
  if (!d.gate.matcher.trim()) e.push({ field: "gate.matcher", message: "choose the tool to gate" })
  if (!EVIDENCE_EXTRACTS.includes(d.audit.extract as (typeof EVIDENCE_EXTRACTS)[number]))
    e.push({ field: "audit.extract", message: "unknown extract" })
  if (!EVIDENCE_JUDGES.includes(d.audit.judge as (typeof EVIDENCE_JUDGES)[number]))
    e.push({ field: "audit.judge", message: "unknown judge" })
  if (!EVIDENCE_VERDICTS.includes(d.gate.verdict)) e.push({ field: "gate.verdict", message: "unknown verdict" })
  if (!GATE_ACTIONS.includes(d.gate.action)) e.push({ field: "gate.action", message: "action must be block or ask" })
  if (d.gate.reason.length > 400) e.push({ field: "gate.reason", message: "reason too long (max 400)" })
  return e
}

/** Build the two IR policy dicts the pair compiles from. */
export function buildEvidenceGatePolicies(d: EvidenceGateDraft): [Record<string, unknown>, Record<string, unknown>] {
  const audit = {
    type: "evidence_audit",
    id: `${d.idStem}-audit`,
    description: d.description ? `${d.description} (audit)` : "Record source-credibility evidence",
    trigger: { host: "claude-code", event: d.audit.event, matcher: d.audit.matcher },
    kind: d.kind,
    extract: d.audit.extract,
    judge: d.audit.judge,
  }
  const gate = {
    type: "evidence_precondition",
    id: `${d.idStem}-gate`,
    description: d.description || "Require verified evidence before the gated tool",
    trigger: { host: "claude-code", event: d.gate.event, matcher: d.gate.matcher },
    require_kind: d.kind,
    require_verdict: d.gate.verdict,
    reason: d.gate.reason,
    action: d.gate.action,
  }
  return [audit, gate]
}

/** One-line plain-English summary of what the pair enforces. */
export function describeEvidenceGate(d: EvidenceGateDraft): string {
  const verb = d.gate.action === "ask" ? "hold for approval" : "block"
  return `On ${d.gate.matcher}, ${verb} unless an earlier ${d.audit.matcher} call recorded ${d.kind}=${d.gate.verdict} this session.`
}
