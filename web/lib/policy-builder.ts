/**
 * Policy IR builder — shared utilities for the builder UI.
 *
 * The legal-combinations matrix lives in the Python backend; we mirror its
 * SHAPE here so the client can validate input locally before POSTing (good
 * UX) and so the server enforces the source-of-truth (good security). The
 * server's response is authoritative on disagreement.
 */
export type EventKind =
  | "PreToolUse" | "PostToolUse"
  | "Stop" | "SubagentStop"
  | "UserPromptSubmit"
  | "PreCompact"
  | "SessionStart" | "SessionEnd"

// D31: action archetypes replace the old decision vocabulary. block /
// ask map to the previous deny / ask 1:1; audit replaces both log and
// allow (which were operationally interchangeable). strip is reserved
// for the verifier-protocol-mutation cycle.
export type Action = "block" | "ask" | "audit"

// Legacy alias kept so old request bodies / draft fixtures from the
// pre-D31 vocabulary keep round-tripping through validateDraft until
// they're migrated.
export type Decision = "deny" | "ask" | "log" | "allow"

export type MatcherClass = "tool" | "mcp_tool" | "wildcard" | "tool_alt"

const BUILTIN_TOOLS = new Set([
  "Bash", "Read", "Edit", "Write", "Glob", "Grep",
  "NotebookEdit", "TodoWrite", "WebFetch", "WebSearch",
])

const MCP_RE = /^mcp__[A-Za-z0-9_]+__[A-Za-z0-9_]+$/

export function classifyMatcher(matcher: string): MatcherClass | "unknown" {
  if (matcher === "*") return "wildcard"
  if (matcher.includes("|")) {
    const parts = matcher.split("|").map(s => s.trim()).filter(Boolean)
    return parts.every(p => BUILTIN_TOOLS.has(p)) ? "tool_alt" : "unknown"
  }
  if (BUILTIN_TOOLS.has(matcher)) return "tool"
  if (MCP_RE.test(matcher)) return "mcp_tool"
  return "unknown"
}

// D31: triples now use action archetype vocabulary (block / ask /
// audit). Mirrors backend policy/matrix.LEGAL_COMBINATIONS exactly.
const LEGAL = new Set<string>([
  // PreToolUse — every action class is legal on every concrete matcher;
  // wildcard narrows to audit only.
  "PreToolUse|tool|block",     "PreToolUse|tool|ask",     "PreToolUse|tool|audit",
  "PreToolUse|mcp_tool|block", "PreToolUse|mcp_tool|ask", "PreToolUse|mcp_tool|audit",
  "PreToolUse|tool_alt|block", "PreToolUse|tool_alt|ask", "PreToolUse|tool_alt|audit",
  "PreToolUse|wildcard|audit",
  // PostToolUse — tool already ran, only audit makes sense.
  "PostToolUse|tool|audit",
  "PostToolUse|mcp_tool|audit",
  // No-tool-context events all use wildcard.
  "UserPromptSubmit|wildcard|block",
  "UserPromptSubmit|wildcard|ask",
  "UserPromptSubmit|wildcard|audit",
  "PreCompact|wildcard|block",
  "PreCompact|wildcard|audit",
  "Stop|wildcard|audit",
  "SubagentStop|wildcard|audit",
  "SessionStart|wildcard|audit",
  "SessionEnd|wildcard|audit",
])

// Migration shim: callers still using the old (deny / ask / log /
// allow) wording get folded into the new archetype set so the legacy
// tests keep working without churn.
const LEGACY_DECISION_TO_ACTION: Record<Decision, Action> = {
  deny:  "block",
  ask:   "ask",
  log:   "audit",
  allow: "audit",
}

export function isLegal(
  event: EventKind,
  matcher: string,
  actionOrDecision: Action | Decision,
): boolean {
  const kls = classifyMatcher(matcher)
  if (kls === "unknown") return false
  const action: Action =
    actionOrDecision === "block" || actionOrDecision === "ask" || actionOrDecision === "audit"
      ? actionOrDecision
      : LEGACY_DECISION_TO_ACTION[actionOrDecision]
  return LEGAL.has(`${event}|${kls}|${action}`)
}

// D35: EvidenceReq is a discriminated union by kind.
//   step       — existing: reference a wired verifier.
//   regex      — inline pattern (Python re syntax) matched against
//                payload text at gate time.
//   llm_critic — natural-language rule judged by LLM provider.
//   shacl      — Turtle SHACL shape validated against payload dict.
export type EvidenceKind = "step" | "regex" | "llm_critic" | "shacl"

export type EvidenceReqDraft =
  | { kind: "step"; step: string; verdict: string }
  | { kind: "regex"; pattern: string }
  | { kind: "llm_critic"; criterion: string }
  | { kind: "shacl"; shape_ttl: string }
  // Legacy: rows without an explicit kind are treated as step.
  | { step: string; verdict: string }

export type PolicyDraft = {
  id: string
  description: string
  version: string
  trigger: { host: "claude-code"; event: EventKind; matcher: string }
  sentinel_re: string
  requires: EvidenceReqDraft[]
  action: Action
  on_signature_invalid: "deny"
  gate_binary: string
}

export const DEFAULT_DRAFT: PolicyDraft = {
  id: "",
  description: "",
  version: "0.1",
  trigger: { host: "claude-code", event: "PreToolUse", matcher: "Bash" },
  sentinel_re: "FILE_COURT_(?P<matter>[A-Za-z0-9]+)_(?P<doc_id>[A-Za-z0-9]+)",
  requires: [{ step: "citation_verify", verdict: "pass" }],
  action: "block",
  on_signature_invalid: "deny",
  gate_binary: "/usr/local/bin/magi-gate.sh",
}

export type DraftError = { field: string; message: string }

const POLICY_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._\-/]{0,127}$/

export function validateDraft(d: PolicyDraft): DraftError[] {
  const errs: DraftError[] = []
  if (!POLICY_ID_RE.test(d.id)) errs.push({ field: "id", message: "Invalid id (alphanumeric + . _ - /; max 128)" })
  if (d.id.includes("..")) errs.push({ field: "id", message: "id must not contain '..'" })
  if (d.id.endsWith("/compiled") || d.id.endsWith("/enabled"))
    errs.push({ field: "id", message: "id must not end with /compiled or /enabled" })
  if (!d.sentinel_re || !d.sentinel_re.includes("?P<matter>") || !d.sentinel_re.includes("?P<doc_id>"))
    errs.push({ field: "sentinel_re", message: "must contain named groups (?P<matter>...) and (?P<doc_id>...)" })
  // D31: requires can be empty for the emit-signal archetype. We no
  // longer hard-fail on length 0; we DO surface a soft warning when
  // a non-audit action is paired with an empty list (almost always
  // an authoring mistake).
  for (const [i, r] of d.requires.entries()) {
    const kind = ("kind" in r ? r.kind : "step")
    if (kind === "step") {
      const step = ("step" in r ? r.step : "")
      const verdict = ("verdict" in r ? r.verdict : "")
      if (!step) errs.push({ field: `requires[${i}].step`, message: "step required" })
      if (!verdict) errs.push({ field: `requires[${i}].verdict`, message: "verdict required" })
    } else if (kind === "regex") {
      const pattern = ("pattern" in r ? r.pattern : "")
      if (!pattern) errs.push({ field: `requires[${i}].pattern`, message: "regex pattern required" })
      else {
        try { new RegExp(pattern) }
        catch { errs.push({ field: `requires[${i}].pattern`, message: "regex fails to compile" }) }
      }
    } else if (kind === "llm_critic") {
      const criterion = ("criterion" in r ? r.criterion : "")
      if (!criterion) errs.push({ field: `requires[${i}].criterion`, message: "criterion required" })
    } else if (kind === "shacl") {
      const shape_ttl = ("shape_ttl" in r ? r.shape_ttl : "")
      if (!shape_ttl) errs.push({ field: `requires[${i}].shape_ttl`, message: "SHACL shape required" })
    }
  }
  if (d.requires.length === 0 && d.action !== "audit") {
    errs.push({
      field: "requires",
      message: `empty requires is only meaningful with action="audit" (emit-signal); current action is "${d.action}"`,
    })
  }
  if (!isLegal(d.trigger.event, d.trigger.matcher, d.action)) {
    errs.push({
      field: "matrix",
      message: `Illegal combination: ${d.trigger.event} × ${d.trigger.matcher} × ${d.action}.`,
    })
  }
  return errs
}

/** Build a *preview* of the managed-settings JSON the cloud compiler will emit.
 * This MUST stay in sync with src/magi_cp/policy/compiler.py shape. The cloud
 * is authoritative; this is purely UX. */
export function previewManagedSettings(d: PolicyDraft): Record<string, unknown> {
  return {
    _magi_policies: [{
      id: d.id,
      version: d.version,
      description: d.description,
    }],
    allowManagedHooksOnly: true,
    hooks: {
      [d.trigger.event]: [{
        matcher: d.trigger.matcher,
        hooks: [{ command: d.gate_binary, type: "command" }],
      }],
    },
    permissions: { defaultMode: "default" },
  }
}
