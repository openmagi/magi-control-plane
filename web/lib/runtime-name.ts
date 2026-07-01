import type { TKey } from "@/lib/i18n/dict"

/**
 * P4 (Codex runtime adapter): map a raw `runtime_id` onto the i18n key
 * that renders its human-readable name. Unknown / legacy values fall
 * back to Claude Code (the pre-adapter default). Keeping this in one
 * place means the sessions table, the runtime picker, and any future
 * surface all render the same label without a dynamic `t()` key (which
 * would break the TKey union type).
 */
export function runtimeNameKey(runtimeId: string): TKey {
  return runtimeId === "codex"
    ? "runtime.name.codex"
    : "runtime.name.claude-code"
}
