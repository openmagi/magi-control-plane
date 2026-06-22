/**
 * Hardcoded usage hints for the 5 wired verifiers — these answer the
 * "when / how is it invoked" question that the backend metadata alone
 * can't (verifiers are payload-shaped; hook event + tool matcher is
 * decided by the policy IR that binds them).
 *
 * Keys are PresetEntry.id (step with underscores replaced by hyphens).
 * Values describe the *recommended* policy binding for the preset.
 */
export interface UsageHint {
  /** Recommended hook event in Claude Code's policy IR. */
  when: string[]
  /** Recommended tool matchers. "*" = wildcard. */
  matchers: string[]
  /** What the verifier returns (status + how it's typically used). */
  verdict: string
  /** Two-sentence summary of the operational pattern. */
  howItWorks: string
}

export const PRESET_USAGE_HINTS: Record<string, UsageHint> = {
  "privilege-scan": {
    when: ["PreToolUse", "Stop"],
    matchers: ["Bash", "Edit|Write"],
    verdict:
      "deny on attorney-client / RRN match · review on soft confidentiality marker · pass otherwise",
    howItWorks:
      "Runs deterministic regex (no LLM) over the tool payload text. Blocks the operation before Bash filing or file write touches client-sensitive material.",
  },
  "source-allowlist": {
    when: ["PreToolUse"],
    matchers: ["WebFetch|WebSearch"],
    verdict:
      "pass when every source URL hostname matches the allowlist · deny on malformed / off-allowlist URLs",
    howItWorks:
      "Parses each URL with urllib, compares against the allowlist (subdomain match). Wraps web research so the model cannot exfiltrate or cite untrusted hosts.",
  },
  "structured-output": {
    when: ["PostToolUse", "Stop"],
    matchers: ["*"],
    verdict:
      "pass when payload validates against the bound JSON Schema · deny / review on schema violation",
    howItWorks:
      "Validates the model's final output against a tenant-supplied JSON Schema before the gate accepts it. Used for filings that must conform to a court template.",
  },
  "prompt-injection-screen": {
    when: ["PreToolUse"],
    matchers: ["WebFetch", "Read", "ReadMcp*"],
    verdict:
      "deny when fetched content contains directive overrides / role-shift attempts · pass otherwise",
    howItWorks:
      "Scans content that re-enters the model's prompt window for known prompt-injection markers. Blocks the tool result before it lands in the next turn's context.",
  },
  "citation-verify": {
    when: ["PostToolUse", "Stop"],
    matchers: ["Bash"],
    verdict:
      "deny on citation that doesn't exist in the source corpus · review on existing-but-not-verbatim · pass on verbatim match",
    howItWorks:
      "Each citation is checked against the source corpus (case-text, court archive). The verbatim quote match is advisory — exists-but-misquoted downgrades to HITL review.",
  },
}
