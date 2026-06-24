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

/** Default-expanded "Common" group with the 4 most-used events
 * (PreToolUse / PostToolUse / UserPromptSubmit / Stop). PreToolUse
 * carries a "recommended" badge to match prior behaviour. */
export const COMMON_GROUP: LifecycleGroup = {
  key: "newPolicy.wizard.step1.group.common",
  kind: "common",
  members: [
    "before_tool_use",
    "after_tool_use",
    "user_prompt",
    "pre_final",
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
  {
    key: "newPolicy.wizard.step1.group.workspace",
    kind: "advanced",
    members: [
      "setup", "notification",
      "teammate_idle", "task_created", "task_completed",
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
