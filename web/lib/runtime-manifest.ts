/**
 * D78 review fix: a single TypeScript-side mirror of the canonical
 * runtime constants the docs pages cite. Each constant is paired with
 * a vitest grep gate in `runtime-manifest.test.ts` that re-derives the
 * same set from the Python source on every test run, so a future
 * mutation in `src/magi_cp/policy/*.py` fails the test loudly before
 * the docs drift.
 *
 * Why mirror instead of import: the docs pages are server components
 * inside a Next.js build; they cannot synchronously `import` Python.
 * We could emit a JSON manifest at `prebuild` time but that adds an
 * install-time Python dependency to the dashboard image. A typed TS
 * mirror + grep-gate keeps the build pure-JS and still bombs on drift.
 *
 * Whenever you edit a value here, also update the matching constant
 * in the Python module the comment names. The runtime-manifest test
 * pins both directions.
 */

/** `src/magi_cp/policy/verdicts.py` — `LEDGER_VERDICTS_ORDERED`. */
export const LEDGER_VERDICTS_ORDERED = [
  "pass",
  "fail",
  "deny",
  "review",
  "needs_review",
  "not_applicable",
] as const

export type LedgerVerdict = (typeof LEDGER_VERDICTS_ORDERED)[number]

/** `src/magi_cp/policy/ir.py` — `EventLiteral` (all 30 CC hook events). */
export const HOOK_EVENTS_ALL = [
  "PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch",
  "PermissionRequest", "PermissionDenied",
  "UserPromptSubmit", "UserPromptExpansion",
  "PreCompact", "PostCompact",
  "Elicitation", "ElicitationResult",
  "SubagentStart", "SubagentStop",
  "Stop", "StopFailure",
  "Setup", "Notification",
  "SessionStart", "SessionEnd",
  "TeammateIdle", "TaskCreated", "TaskCompleted",
  "ConfigChange",
  "WorktreeCreate", "WorktreeRemove",
  "InstructionsLoaded",
  "CwdChanged", "FileChanged",
  "MessageDisplay",
] as const

/**
 * `src/magi_cp/policy/ir.py` — `_CONTEXT_INJECTION_EXCLUDED_EVENTS`.
 *
 * Eight hook events that do NOT accept `additionalContext`. Four of
 * them (Elicitation / ElicitationResult / WorktreeCreate /
 * MessageDisplay) have a specialized `hookSpecificOutput` shape; four
 * (Stop / StopFailure / SessionEnd / SubagentStop) fire after the last
 * model turn in the session so CC silently drops the field.
 *
 * `SessionStart` is NOT in this set — CC accepts `additionalContext`
 * on SessionStart and uses it to prime the first model turn of the
 * session (`ContextEventLiteral` in `ir.py` lists it explicitly).
 */
export const CONTEXT_INJECTION_EXCLUDED_EVENTS = [
  "Elicitation",
  "ElicitationResult",
  "WorktreeCreate",
  "MessageDisplay",
  "Stop",
  "StopFailure",
  "SessionEnd",
  "SubagentStop",
] as const

/**
 * Per-event alternate-channel description used by the runtime when an
 * operator authors a `ContextInjectionPolicy` on an excluded event.
 * Mirrors `_CONTEXT_INJECTION_ALTERNATE_CHANNEL` in `ir.py`. Shortened
 * to UI-friendly phrasing; full sentences live in the Python module.
 */
export const CONTEXT_INJECTION_ALTERNATE_CHANNEL: Record<
  (typeof CONTEXT_INJECTION_EXCLUDED_EVENTS)[number],
  string
> = {
  Elicitation: "hookSpecificOutput.elicitationDecision (accept / decline an MCP elicitation request)",
  ElicitationResult: "hookSpecificOutput action / content override (applied before the response is sent to the MCP server)",
  WorktreeCreate: "hookSpecificOutput.worktreePath (the gate returns a worktree path)",
  MessageDisplay: "no model-context channel (display-only hook)",
  Stop: "no downstream same-session model turn (end-of-execution; CC drops additionalContext)",
  StopFailure: "no downstream same-session model turn (mirrors Stop)",
  SessionEnd: "no downstream same-session model turn (session teardown)",
  SubagentStop: "no downstream same-session model turn (the child has returned; author on SubagentStart instead)",
}

/** `src/magi_cp/policy/rewriters.py` — `REWRITER_KINDS`. */
export const REWRITER_KINDS = [
  "prefix_strip",
  "scheme_force",
  "regex_substitute",
] as const

/** `src/magi_cp/policy/ir.py` — `_RUN_COMMAND_RUNTIMES`. */
export const RUN_COMMAND_RUNTIMES = ["bash", "python3", "node"] as const

/**
 * `src/magi_cp/policy/ir.py` —
 * `_DEFAULT_RUN_COMMAND_TIMEOUT_MS` / `_MAX_RUN_COMMAND_TIMEOUT_MS`.
 */
export const RUN_COMMAND_TIMEOUT_DEFAULT_MS = 5_000
export const RUN_COMMAND_TIMEOUT_MAX_MS = 30_000

/**
 * `src/magi_cp/policy/ir.py` — `_SCRIPT_ID_RE`. The script id is a
 * 64-hex sha256 of the script body. Doc placeholder kept short with a
 * deterministic-looking prefix so operators don't accidentally copy a
 * real hash from elsewhere.
 */
export const SCRIPT_ID_EXAMPLE = "0a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f9"

/** Per-policy IR-version field name as it appears on saved JSON. */
export const POLICY_IR_VERSION_FIELD = "version"

/** Number of events that accept `additionalContext`. Always derived. */
export const CONTEXT_ACCEPTING_EVENT_COUNT =
  HOOK_EVENTS_ALL.length - CONTEXT_INJECTION_EXCLUDED_EVENTS.length
