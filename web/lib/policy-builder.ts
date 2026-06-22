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

const LEGAL = new Set<string>([
  // PreToolUse — gate
  "PreToolUse|tool|deny",     "PreToolUse|tool|ask",
  "PreToolUse|mcp_tool|deny", "PreToolUse|mcp_tool|ask",
  "PreToolUse|tool_alt|deny", "PreToolUse|tool_alt|ask",
  "PreToolUse|wildcard|log",
  // PostToolUse — observe
  "PostToolUse|tool|log",     "PostToolUse|tool|allow",
  "PostToolUse|mcp_tool|log", "PostToolUse|mcp_tool|allow",
  // No-tool-context events (D28: scope expanded to Claude Code's
  // full hook set minus Notification). All use the wildcard matcher
  // class because the hook payload has no tool to match.
  "Stop|wildcard|log",
  "SubagentStop|wildcard|log",
  "UserPromptSubmit|wildcard|deny",
  "UserPromptSubmit|wildcard|ask",
  "UserPromptSubmit|wildcard|log",
  "PreCompact|wildcard|deny",
  "PreCompact|wildcard|log",
  "SessionStart|wildcard|log",
  "SessionEnd|wildcard|log",
])

export function isLegal(event: EventKind, matcher: string, decision: Decision): boolean {
  const kls = classifyMatcher(matcher)
  if (kls === "unknown") return false
  return LEGAL.has(`${event}|${kls}|${decision}`)
}

export type EvidenceReqDraft = { step: string; verdict: string }

export type PolicyDraft = {
  id: string
  description: string
  version: string
  trigger: { host: "claude-code"; event: EventKind; matcher: string }
  sentinel_re: string
  requires: EvidenceReqDraft[]
  on_missing: Decision
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
  on_missing: "deny",
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
  // Note: sentinel_re uses Python regex syntax `(?P<name>)` not JS `(?<name>)`,
  // so we DON'T compile it here. Server-side `Policy.__post_init__` is the
  // source of truth on regex validity; the client only enforces the named-group
  // presence rule above for fast feedback.
  if (d.requires.length === 0)
    errs.push({ field: "requires", message: "at least one evidence requirement is required" })
  for (const [i, r] of d.requires.entries()) {
    if (!r.step) errs.push({ field: `requires[${i}].step`, message: "step required" })
    if (!r.verdict) errs.push({ field: `requires[${i}].verdict`, message: "verdict required" })
  }
  if (!isLegal(d.trigger.event, d.trigger.matcher, d.on_missing)) {
    errs.push({
      field: "matrix",
      message: `Illegal combination: ${d.trigger.event} × ${d.trigger.matcher} × ${d.on_missing}.`,
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
