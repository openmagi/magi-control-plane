/**
 * D61: pure data + search helper for the Step 1 lifecycle picker.
 *
 * Kept separate from the .tsx so the test suite can import it without
 * pulling React / i18n / next.js into the test loader. The component
 * itself re-exports these so the public surface is one module from
 * the consumer's POV.
 *
 * No React imports. No `@/` imports. Pure TS only.
 */

/** Lifecycle slug union, kept in lockstep with the server Lifecycle
 * union in page.tsx (LIFECYCLE_TO_EVENT). Adding a slug to either
 * side requires adding it to both. The test suite asserts the union
 * here covers all 30 events. */
export type LifecycleSlug =
  | "before_tool_use" | "after_tool_use" | "pre_final"
  | "subagent_stop"   | "user_prompt"    | "pre_compact"
  | "session_start"   | "session_end"
  | "post_tool_use_failure" | "post_tool_batch"
  | "permission_request" | "permission_denied"
  | "user_prompt_expansion" | "post_compact"
  | "elicitation" | "elicitation_result"
  | "subagent_start" | "stop_failure"
  | "setup" | "notification"
  | "teammate_idle" | "task_created" | "task_completed"
  | "config_change"
  | "worktree_create" | "worktree_remove"
  | "instructions_loaded"
  | "cwd_changed" | "file_changed"
  | "message_display"

export type LifecycleLabels = Record<
  LifecycleSlug,
  { label: string; sub: string }
>

/** Group descriptor. `kind === "common"` means default-expanded and
 * NOT togglable; `kind === "advanced"` means collapsed-by-default and
 * togglable via the header button. `members` is rendered in the listed
 * order. */
export interface LifecycleGroup {
  /** Stable key for persistence (localStorage). Also used as the i18n
   * key for the group header label. */
  key: string
  kind: "common" | "advanced"
  members: readonly LifecycleSlug[]
}

/** Short preview of the 2-3 most recognisable event names inside an
 * Advanced group, shown inline next to the collapsed header. Operators
 * scanning groups for "the one with PostToolUseFailure" do not need to
 * expand every group one by one. Display only — not part of the search
 * surface (which already matches by slug or label).
 *
 * D70 — the workspace preview no longer surfaces "TaskCreated" because
 * both TaskCreated and TaskCompleted live in the Common tier now (the
 * Task-tool lifecycle is paired so end-of-task automation flows have
 * both halves on the recommended row). The preview uses FileChanged +
 * ConfigChange + WorktreeCreate as the workspace-tier exemplars. */
export const ADVANCED_GROUP_PREVIEWS: Record<string, readonly string[]> = {
  "newPolicy.wizard.step1.group.toolActions":
    ["PostToolUseFailure", "PostToolBatch"],
  "newPolicy.wizard.step1.group.contentFlow":
    ["PreCompact", "PostCompact", "Elicitation"],
  "newPolicy.wizard.step1.group.permissions":
    ["PermissionRequest", "PermissionDenied"],
  "newPolicy.wizard.step1.group.subagents":
    ["SubagentStart", "SubagentStop"],
  "newPolicy.wizard.step1.group.boundaries":
    ["StopFailure", "SessionStart", "SessionEnd"],
  "newPolicy.wizard.step1.group.workspace":
    ["WorktreeCreate", "ConfigChange", "FileChanged"],
}

/**
 * D70 — slugs whose canonical CC event lives in
 * `_UNVERIFIED_EVENTS` (see src/magi_cp/policy/matrix.py). These rows
 * render an "unverified candidate" badge so the Common-tier promo for
 * TaskCompleted (D69) does not lend it the same credibility as the
 * 8 _VERIFIED_EVENTS members (PreToolUse / PostToolUse / Stop /
 * SubagentStop / UserPromptSubmit / PreCompact / SessionStart /
 * SessionEnd).
 *
 * Source of truth is matrix.py's `_UNVERIFIED_EVENTS` frozenset; this
 * mirror is the closest the UI can get without crossing the Python /
 * TypeScript boundary at render time. Keep it in lockstep: when a
 * candidate moves to `_VERIFIED_EVENTS` (matched against a binary
 * fixture), remove it here too. The Step1LifecyclePicker test asserts
 * set-equality vs. the Python file so a future Python-side promotion
 * without the TS counterpart fails CI.
 */
export const UNVERIFIED_LIFECYCLE_SLUGS: ReadonlySet<LifecycleSlug> = new Set<LifecycleSlug>([
  // Tool-context observability variants
  "post_tool_use_failure", "post_tool_batch",
  // Permission gate family
  "permission_request", "permission_denied",
  // Content-flow extensions
  "user_prompt_expansion", "post_compact",
  "elicitation", "elicitation_result",
  // Subagent / Stop boundary
  "subagent_start", "stop_failure",
  // Lifecycle / observability surface
  "setup", "notification",
  "teammate_idle", "task_created", "task_completed",
  "config_change",
  "worktree_create", "worktree_remove",
  "instructions_loaded",
  "cwd_changed", "file_changed",
  "message_display",
])

export function isUnverifiedLifecycle(slug: LifecycleSlug): boolean {
  return UNVERIFIED_LIFECYCLE_SLUGS.has(slug)
}

/** Default-expanded "Common" group.
 *
 * D69: TaskCompleted joined the Common tier. End-of-task automation
 * ("when a /workflows background task finishes, inject the summary
 * back into the next turn" or "run a recovery script") is one of the
 * most common hook patterns operators ask for; the prior layout
 * tucked it deep inside the workspace Advanced group where the search
 * filter was the only way to find it.
 *
 * D70: TaskCreated joins the Common tier alongside TaskCompleted. End-
 * of-task automation almost always pairs the two halves (audit at
 * dispatch, react at completion); splitting them across Common +
 * Advanced surfaces the pair across two tiers without a cross-
 * reference and surprises operators who try to author "audit when
 * the Task tool fires" by searching the Advanced workspace group.
 * PreToolUse stays the only "recommended" badge — the Task pair is
 * present in Common as a discoverability promo, not as a fleet-wide
 * recommendation.
 *
 * Order on screen:
 *   - PreToolUse  (verified, recommended)
 *   - PostToolUse (verified)
 *   - UserPromptSubmit (verified)
 *   - Stop / pre_final (verified)
 *   - TaskCreated  (unverified candidate, badged)
 *   - TaskCompleted (unverified candidate, badged) */
export const COMMON_GROUP: LifecycleGroup = {
  key: "newPolicy.wizard.step1.group.common",
  kind: "common",
  members: [
    "before_tool_use",
    "after_tool_use",
    "user_prompt",
    "pre_final",
    "task_created",
    "task_completed",
  ],
}

/** Advanced groups. Order on screen follows this array. Each group
 * starts collapsed; localStorage persists which were opened. */
export const ADVANCED_GROUPS: ReadonlyArray<LifecycleGroup> = [
  {
    key: "newPolicy.wizard.step1.group.toolActions",
    kind: "advanced",
    members: ["post_tool_use_failure", "post_tool_batch"],
  },
  {
    key: "newPolicy.wizard.step1.group.contentFlow",
    kind: "advanced",
    members: [
      "pre_compact", "user_prompt_expansion", "post_compact",
      "elicitation", "elicitation_result",
    ],
  },
  {
    key: "newPolicy.wizard.step1.group.permissions",
    kind: "advanced",
    members: ["permission_request", "permission_denied"],
  },
  {
    key: "newPolicy.wizard.step1.group.subagents",
    kind: "advanced",
    members: ["subagent_start", "subagent_stop"],
  },
  {
    key: "newPolicy.wizard.step1.group.boundaries",
    kind: "advanced",
    members: ["stop_failure", "session_start", "session_end"],
  },
  // D70: task_created promoted to Common alongside task_completed;
  // the Task-tool lifecycle pair stays together.
  {
    key: "newPolicy.wizard.step1.group.workspace",
    kind: "advanced",
    members: [
      "setup", "notification",
      "teammate_idle",
      "config_change",
      "worktree_create", "worktree_remove",
      "instructions_loaded",
      "cwd_changed", "file_changed",
      "message_display",
    ],
  },
]

/** localStorage key for which Advanced groups stay open between
 * sessions. Stored as a JSON string array of group keys. */
export const ADVANCED_OPEN_STORAGE_KEY = "magi_cp.step1_advanced_open"

/** Normalise a search query: lowercase + trim. Empty string means
 * "no filter active". */
export function normalizeQuery(q: string): string {
  return q.trim().toLowerCase()
}

/** A lifecycle row matches when the query is a substring of EITHER
 * the slug OR the plain-language label (case-insensitive). Returns
 * true for every row when the query is empty. */
export function matchesQuery(
  slug: LifecycleSlug,
  label: string,
  rawQuery: string,
): boolean {
  const q = normalizeQuery(rawQuery)
  if (q === "") return true
  if (slug.toLowerCase().includes(q)) return true
  if (label.toLowerCase().includes(q)) return true
  return false
}

/** Return the Advanced group whose `members` contains `slug`, or
 * `null` if the slug lives in the Common group (or is unrecognised).
 * Used by the picker to auto-expand the owning Advanced group on mount
 * when the wizard returns to Step 1 with `currentLifecycle` already
 * pointing inside an Advanced group (regression fix vs. the prior
 * screen-full layout where the selected card was always visible). */
export function findOwningAdvancedGroup(
  slug: LifecycleSlug,
): LifecycleGroup | null {
  for (const group of ADVANCED_GROUPS) {
    if ((group.members as readonly LifecycleSlug[]).includes(slug)) {
      return group
    }
  }
  return null
}
