/**
 * D70: Canonical Claude Code built-in tool list.
 *
 * Source: strings extraction from the Claude Code CLI binary at
 *   /opt/homebrew/Caskroom/claude-code/2.1.170/claude
 * The Mach-O binary embeds every tool's canonical name as a top-level
 * literal. Running `strings <claude> | grep -E '^(Bash|Read|Edit|...)$'`
 * surfaces the canonical set (one per line) which we mirror here. Each
 * entry was source-verified against the binary on 2026-06-24.
 *
 * Why an in-repo list (instead of a runtime probe):
 *   - The wizard is a server component that renders into the policies
 *     authoring URL; pinning the list lets us source-grep tests and
 *     guarantees the dropdown stays consistent regardless of which
 *     claude version the operator installed locally.
 *   - The runtime DOES allow arbitrary tool names (MCP tools follow
 *     mcp__server__name; agents may register custom tools). The
 *     ToolCombobox supports free-typing for that — this list is the
 *     suggestion seed for the "built-in" category only.
 *
 * Schema: each entry carries
 *   - name:        canonical tool name as embedded in the binary
 *   - kind:        always "built-in" for entries in this list
 *   - description: one-liner per locale (ko, en) used in the dropdown
 *
 * Adding a new built-in:
 *   1. Re-extract strings against the latest claude binary; confirm the
 *      new name surfaces with `strings | grep -E '^<Name>$'`.
 *   2. Append a new entry below in the canonical order shown by the
 *      binary's symbol layout. Test invariants in cc-tools.test.ts
 *      pin the count + presence of every name.
 *
 * Unverified tools (none for v2.1.170):
 *   - Earlier wizard iterations referenced `MultiEdit`, `BashOutput`,
 *     `KillBash`, `NotebookRead`, `ExitPlanMode`, `AskUserQuestion`,
 *     `Task` as missing chips. All are present in the v2.1.170 binary
 *     strings table; none required guessing. The previous wizard's
 *     name "AskUser" was renamed to "AskUserQuestion" between binary
 *     releases — we use the binary's canonical name.
 */

export type CcToolKind = "built-in" | "mcp" | "custom"

export interface CcToolEntry {
  /** canonical tool name as registered in the CC runtime */
  name: string
  /** kind classification (only "built-in" is enumerated here) */
  kind: CcToolKind
  /** one-line description per locale, used in dropdown suggestion rows */
  description: { ko: string; en: string }
}

/** Canonical list of built-in tools from claude-code v2.1.170.
 * Order mirrors the CLI's documentation grouping:
 *   shell → filesystem → search → web → notebook → agent → planning →
 *   user-interaction.
 * Every entry below was verified against the binary strings table on
 * 2026-06-24 (no name is guessed). */
export const CC_BUILTIN_TOOLS: readonly CcToolEntry[] = [
  {
    name: "Bash",
    kind: "built-in",
    description: {
      ko: "셸 명령을 실행합니다 (zsh / bash).",
      en: "Run a shell command (zsh / bash).",
    },
  },
  {
    name: "BashOutput",
    kind: "built-in",
    description: {
      ko: "백그라운드로 실행 중인 셸의 출력 스트림을 읽어옵니다.",
      en: "Read the output stream from a background shell.",
    },
  },
  {
    name: "KillBash",
    kind: "built-in",
    description: {
      ko: "백그라운드 셸을 종료합니다.",
      en: "Kill a background shell session.",
    },
  },
  {
    name: "Read",
    kind: "built-in",
    description: {
      ko: "로컬 파일 내용을 읽습니다.",
      en: "Read a local file's contents.",
    },
  },
  {
    name: "Write",
    kind: "built-in",
    description: {
      ko: "새 파일을 쓰거나 기존 파일을 덮어씁니다.",
      en: "Create a new file or overwrite an existing one.",
    },
  },
  {
    name: "Edit",
    kind: "built-in",
    description: {
      ko: "기존 파일에 단일 정확 치환을 수행합니다.",
      en: "Apply an exact string replacement to an existing file.",
    },
  },
  {
    name: "MultiEdit",
    kind: "built-in",
    description: {
      ko: "한 파일에 여러 정확 치환을 한 번에 수행합니다.",
      en: "Apply multiple exact replacements to one file atomically.",
    },
  },
  {
    name: "Glob",
    kind: "built-in",
    description: {
      ko: "glob 패턴으로 파일 경로를 찾습니다.",
      en: "Match file paths against a glob pattern.",
    },
  },
  {
    name: "Grep",
    kind: "built-in",
    description: {
      ko: "정규식으로 파일 내용을 검색합니다.",
      en: "Search file contents with a regular expression.",
    },
  },
  {
    name: "WebFetch",
    kind: "built-in",
    description: {
      ko: "URL의 콘텐츠를 가져와 모델에 전달합니다.",
      en: "Fetch a URL's content and pass it to the model.",
    },
  },
  {
    name: "WebSearch",
    kind: "built-in",
    description: {
      ko: "공개 웹 검색을 수행합니다.",
      en: "Run a public web search query.",
    },
  },
  {
    name: "NotebookEdit",
    kind: "built-in",
    description: {
      ko: "Jupyter 노트북(.ipynb)의 셀을 수정합니다.",
      en: "Edit a cell in a Jupyter notebook (.ipynb).",
    },
  },
  {
    name: "NotebookRead",
    kind: "built-in",
    description: {
      ko: "Jupyter 노트북(.ipynb) 전체를 읽습니다.",
      en: "Read an entire Jupyter notebook (.ipynb).",
    },
  },
  {
    name: "Task",
    kind: "built-in",
    description: {
      ko: "서브에이전트(child agent)를 스폰합니다.",
      en: "Spawn a sub-agent (child agent).",
    },
  },
  {
    name: "TodoWrite",
    kind: "built-in",
    description: {
      ko: "에이전트의 작업 목록(todo)을 갱신합니다.",
      en: "Update the agent's todo list.",
    },
  },
  {
    name: "ExitPlanMode",
    kind: "built-in",
    description: {
      ko: "계획 모드를 종료하고 실행으로 진입합니다.",
      en: "Exit plan mode and enter execution.",
    },
  },
  {
    name: "AskUserQuestion",
    kind: "built-in",
    description: {
      ko: "사용자에게 다지선다형 질문을 합니다 (사람 입력 요청).",
      en: "Ask the user a multiple-choice question (human input request).",
    },
  },
] as const

/** Quick lookup map: lowercase name -> entry. Built once at module load. */
const CC_BUILTIN_BY_LOWER: Map<string, CcToolEntry> = new Map(
  CC_BUILTIN_TOOLS.map((t) => [t.name.toLowerCase(), t]),
)

/** True iff `name` (case-insensitive) matches a canonical built-in. */
export function isCcBuiltinTool(name: string): boolean {
  return CC_BUILTIN_BY_LOWER.has(name.trim().toLowerCase())
}

/** Returns the canonical entry for `name` (case-insensitive), or null. */
export function findCcBuiltinTool(name: string): CcToolEntry | null {
  return CC_BUILTIN_BY_LOWER.get(name.trim().toLowerCase()) ?? null
}

/**
 * Classify any tool name into "built-in" / "mcp" / "custom".
 *   - "built-in" : present in CC_BUILTIN_TOOLS (case-insensitive).
 *   - "mcp"      : starts with `mcp__` (Claude Code MCP naming convention).
 *   - "custom"   : everything else (agent-registered tools, typos, etc.).
 * This DOES NOT validate the matcher class; matcherClassForToolScope
 * in policies/new/page.tsx handles that (mcp__* → mcp_tool, else tool).
 */
export function classifyCcToolName(name: string): CcToolKind {
  const trimmed = name.trim()
  if (!trimmed) return "custom"
  if (isCcBuiltinTool(trimmed)) return "built-in"
  if (trimmed.toLowerCase().startsWith("mcp__")) return "mcp"
  return "custom"
}

/**
 * Top-N suggested built-ins for the empty-input state of the combobox.
 * Heuristic: the 5 tools authors most often gate (Bash, Read, Edit,
 * WebFetch, Task). Kept in declaration order so the dropdown surface
 * is deterministic for source-grep tests.
 */
export const CC_TOP_SUGGESTIONS: readonly string[] = [
  "Bash",
  "Read",
  "Edit",
  "WebFetch",
  "Task",
] as const

/**
 * Substring (case-insensitive) filter over the built-in list. Returns
 * matches in canonical declaration order.
 */
export function filterCcBuiltins(query: string): CcToolEntry[] {
  const q = query.trim().toLowerCase()
  if (!q) return CC_BUILTIN_TOOLS.slice() as CcToolEntry[]
  return CC_BUILTIN_TOOLS.filter((t) => t.name.toLowerCase().includes(q))
}
