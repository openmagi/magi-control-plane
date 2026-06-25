/**
 * D70 / D71: Canonical Claude Code built-in tool list, sourced from the
 * v2.1.170 CLI binary.
 *
 * Source: strings extraction from the Claude Code CLI Mach-O binary at
 *   /opt/homebrew/Caskroom/claude-code/2.1.170/claude
 *
 * Methodology (each entry was source-verified on 2026-06-24 against the
 * installed binary, not guessed from prose):
 *   1. The binary minifies tool identifiers to short var bindings
 *      (e.g. `var H9="Bash"`, `var G9="Agent"`). The set of valid var
 *      bindings is found by:
 *        strings <claude> | grep -oE 'var [A-Za-z_$]+="[A-Z][A-Za-z]+"'
 *   2. Each minified var is then matched to a tool registration block
 *      (`a9({name:<var>,searchHint:"..."})`) which proves the var is
 *      attached to a real registered tool, not just a string constant.
 *        strings <claude> | grep -oE 'name:[A-Za-z_$]+,searchHint:"[^"]+"'
 *   3. The post-rename canonical names come from an alias map embedded
 *      directly in the binary at `B78`:
 *        strings <claude> | grep -oE '\{Task:"Agent"[^}]+\}'
 *      surfaces:
 *        Task -> Agent, KillShell -> TaskStop, KillBash -> TaskStop,
 *        AgentOutputTool -> TaskOutput, BashOutputTool -> TaskOutput,
 *        AgentOutput -> TaskOutput, BashOutput -> TaskOutput,
 *        ListPeers -> ListAgents, Brief -> SendUserMessage,
 *        ListMcpResources -> ListMcpResourcesTool,
 *        ReadMcpResource -> ReadMcpResourceTool
 *      We carry the POST-rename canonical name (right-hand side of
 *      each pair) and surface the pre-rename name as a legacy alias.
 *
 * Why an in-repo list (not a runtime probe):
 *   - The wizard is a server component; pinning the list lets us
 *     source-grep tests and guarantees the dropdown stays consistent
 *     regardless of which claude version the operator has installed.
 *   - The runtime DOES allow arbitrary tool names (MCP follows
 *     mcp__server__name; agents may register custom tools).
 *
 * Drift gate:
 *   - cc-tools.test.ts pins this list against the verified manifest.
 *   - When run against a host that has the v2.1.170 binary installed,
 *     the test re-extracts the canonical names from the binary and
 *     fails if the in-repo list has drifted.
 *
 * Adding a new built-in:
 *   1. Re-run the three extraction commands above against the new
 *      binary; confirm the new name surfaces.
 *   2. Append a new entry below, plus its description.
 *   3. Run `vitest run web/lib/cc-tools.test.ts` to confirm.
 *
 * D70 follow-up gap notes:
 *   - `Task`, `BashOutput`, `KillBash`, `MultiEdit`, `NotebookRead` were
 *     previously listed as canonical built-ins; they are NOT. The first
 *     three are pre-rename aliases (see LEGACY_ALIASES below); the latter
 *     two only appear in prose (system prompt, Read tool docstring) and
 *     never register as their own tools in v2.1.170. They are documented
 *     as legacy aliases so the test asserts they are absent from
 *     CC_BUILTIN_TOOLS.
 */

export type CcToolKind = "built-in" | "mcp" | "custom"

export interface CcToolEntry {
  /** canonical tool name as registered in the CC runtime (post-rename) */
  name: string
  /** kind classification (only "built-in" is enumerated here) */
  kind: CcToolKind
  /** one-line description per locale, used in dropdown suggestion rows */
  description: { ko: string; en: string }
}

/** Canonical list of built-in tools from claude-code v2.1.170.
 * Order groups tools by purpose:
 *   shell → filesystem → search → web → notebook → agent → planning →
 *   user-interaction → tasks → mcp → cron → worktree → ops.
 * Each entry below was verified against the binary strings table on
 * 2026-06-24 per the methodology in the module docstring. */
export const CC_BUILTIN_TOOLS: readonly CcToolEntry[] = [
  // ----- shell -----
  {
    name: "Bash",
    kind: "built-in",
    description: {
      ko: "셸 명령을 실행합니다 (zsh / bash).",
      en: "Run a shell command (zsh / bash).",
    },
  },
  {
    name: "PowerShell",
    kind: "built-in",
    description: {
      ko: "Windows PowerShell 명령을 실행합니다.",
      en: "Execute Windows PowerShell commands.",
    },
  },
  // ----- filesystem -----
  {
    name: "Read",
    kind: "built-in",
    description: {
      ko: "로컬 파일, 이미지, PDF, Jupyter 노트북(.ipynb)을 읽습니다.",
      en: "Read a local file, image, PDF, or Jupyter notebook (.ipynb).",
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
      ko: "기존 파일의 내용을 정확 치환으로 수정합니다.",
      en: "Modify file contents with an exact string replacement.",
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
  // ----- search -----
  {
    name: "Glob",
    kind: "built-in",
    description: {
      ko: "glob 패턴으로 파일 경로를 찾습니다.",
      en: "Find files by name pattern or wildcard.",
    },
  },
  {
    name: "Grep",
    kind: "built-in",
    description: {
      ko: "정규식으로 파일 내용을 검색합니다 (ripgrep).",
      en: "Search file contents with regex (ripgrep).",
    },
  },
  // ----- web -----
  {
    name: "WebFetch",
    kind: "built-in",
    description: {
      ko: "URL의 콘텐츠를 가져와 모델에 전달합니다.",
      en: "Fetch and extract content from a URL.",
    },
  },
  {
    name: "WebSearch",
    kind: "built-in",
    description: {
      ko: "공개 웹 검색을 수행합니다.",
      en: "Search the web for current information.",
    },
  },
  // ----- subagent / team -----
  {
    name: "Agent",
    kind: "built-in",
    description: {
      ko: "서브에이전트(child agent)에 작업을 위임합니다. (구버전 'Task'의 새 이름)",
      en: "Delegate work to a subagent. (formerly named 'Task')",
    },
  },
  {
    name: "TeamCreate",
    kind: "built-in",
    description: {
      ko: "멀티 에이전트 팀을 생성합니다.",
      en: "Create a multi-agent team.",
    },
  },
  {
    name: "TeamDelete",
    kind: "built-in",
    description: {
      ko: "팀을 해체하고 정리합니다.",
      en: "Disband a team and clean up.",
    },
  },
  {
    name: "ListAgents",
    kind: "built-in",
    description: {
      ko: "활성 서브에이전트를 나열합니다. (구버전 'ListPeers'의 새 이름)",
      en: "List active subagents. (formerly named 'ListPeers')",
    },
  },
  {
    name: "SendMessage",
    kind: "built-in",
    description: {
      ko: "팀 동료 에이전트에게 메시지를 보냅니다.",
      en: "Send messages to agent teammates.",
    },
  },
  // ----- planning -----
  {
    name: "EnterPlanMode",
    kind: "built-in",
    description: {
      ko: "계획 모드로 진입해 접근법을 설계합니다.",
      en: "Switch to plan mode to design an approach before coding.",
    },
  },
  {
    name: "ExitPlanMode",
    kind: "built-in",
    description: {
      ko: "계획 모드를 종료하고 실행으로 진입합니다.",
      en: "Present plan for approval and start coding (plan mode only).",
    },
  },
  {
    name: "TodoWrite",
    kind: "built-in",
    description: {
      ko: "세션 작업 체크리스트(todo)를 관리합니다.",
      en: "Manage the session task checklist.",
    },
  },
  // ----- user-interaction -----
  {
    name: "AskUserQuestion",
    kind: "built-in",
    description: {
      ko: "사용자에게 다지선다형 질문을 합니다.",
      en: "Prompt the user with a multiple-choice question.",
    },
  },
  {
    name: "SendUserMessage",
    kind: "built-in",
    description: {
      ko: "사용자에게 메시지를 보냅니다 (주 출력 채널). (구버전 'Brief'의 새 이름)",
      en: "Send a message to the user (primary visible output channel). (formerly 'Brief')",
    },
  },
  {
    name: "SendUserFile",
    kind: "built-in",
    description: {
      ko: "사용자에게 파일(스크린샷, 리포트, 아티팩트)을 전달합니다.",
      en: "Deliver files (screenshots, reports, artifacts) to the user.",
    },
  },
  {
    name: "PushNotification",
    kind: "built-in",
    description: {
      ko: "터미널 및 선택적으로 모바일을 통한 알림을 보냅니다.",
      en: "Send a notification via terminal and optionally mobile.",
    },
  },
  {
    name: "StructuredOutput",
    kind: "built-in",
    description: {
      ko: "최종 응답을 요청된 구조화 JSON 형식으로 반환합니다.",
      en: "Return the final response as structured JSON.",
    },
  },
  // ----- tasks (background) -----
  {
    name: "TaskCreate",
    kind: "built-in",
    description: {
      ko: "작업 목록에 태스크를 생성합니다.",
      en: "Create a task in the task list.",
    },
  },
  {
    name: "TaskGet",
    kind: "built-in",
    description: {
      ko: "ID로 태스크를 조회합니다.",
      en: "Retrieve a task by ID.",
    },
  },
  {
    name: "TaskUpdate",
    kind: "built-in",
    description: {
      ko: "태스크를 수정합니다.",
      en: "Update a task.",
    },
  },
  {
    name: "TaskList",
    kind: "built-in",
    description: {
      ko: "모든 태스크를 나열합니다.",
      en: "List all tasks.",
    },
  },
  {
    name: "TaskStop",
    kind: "built-in",
    description: {
      ko: "백그라운드 태스크를 종료합니다. (구버전 'KillBash' / 'KillShell'의 새 이름)",
      en: "Kill a running background task. (formerly 'KillBash' / 'KillShell')",
    },
  },
  {
    name: "TaskOutput",
    kind: "built-in",
    description: {
      ko: "백그라운드 태스크의 출력/로그를 읽습니다. (구버전 'BashOutput'의 새 이름)",
      en: "Read output/logs from a background task. (formerly 'BashOutput')",
    },
  },
  // ----- skills / discovery / loop -----
  {
    name: "Skill",
    kind: "built-in",
    description: {
      ko: "슬래시 명령(스킬)을 호출합니다.",
      en: "Invoke a slash-command skill.",
    },
  },
  {
    name: "ToolSearch",
    kind: "built-in",
    description: {
      ko: "지연 로드된 도구 정의를 키워드로 검색해 로드합니다.",
      en: "Search deferred tool definitions and load their schemas.",
    },
  },
  {
    name: "Workflow",
    kind: "built-in",
    description: {
      ko: "결정론적 JavaScript 워크플로로 서브에이전트를 오케스트레이션합니다.",
      en: "Orchestrate subagents with deterministic JavaScript workflow.",
    },
  },
  {
    name: "REPL",
    kind: "built-in",
    description: {
      ko: "프로그램적 도구 접근이 가능한 JavaScript REPL을 실행합니다.",
      en: "Execute JavaScript with programmatic tool access.",
    },
  },
  {
    name: "LSP",
    kind: "built-in",
    description: {
      ko: "코드 인텔리전스 (정의, 참조, 심볼, hover).",
      en: "Code intelligence (definitions, references, symbols, hover).",
    },
  },
  {
    name: "Monitor",
    kind: "built-in",
    description: {
      ko: "프로세스/로그/명령을 모니터링하며 stdout 라인마다 알림을 받습니다.",
      en: "Watch a process/log/command and stream each stdout line as a notification.",
    },
  },
  {
    name: "ScheduleWakeup",
    kind: "built-in",
    description: {
      ko: "다음 반복을 self-pace: 지연 시간을 골라 다음 /loop tick을 예약합니다.",
      en: "Self-pace next iteration: pick a delay before the next /loop tick.",
    },
  },
  // ----- mcp resource tools -----
  {
    name: "ListMcpResourcesTool",
    kind: "built-in",
    description: {
      ko: "연결된 MCP 서버의 리소스를 나열합니다.",
      en: "List resources from connected MCP servers.",
    },
  },
  {
    name: "ReadMcpResourceTool",
    kind: "built-in",
    description: {
      ko: "특정 MCP 리소스를 URI로 읽습니다.",
      en: "Read a specific MCP resource by URI.",
    },
  },
  // ----- cron -----
  {
    name: "CronCreate",
    kind: "built-in",
    description: {
      ko: "반복 또는 일회성 프롬프트 cron 잡을 예약합니다.",
      en: "Schedule a recurring or one-shot prompt.",
    },
  },
  {
    name: "CronDelete",
    kind: "built-in",
    description: {
      ko: "예약된 cron 잡을 취소합니다.",
      en: "Cancel a scheduled cron job.",
    },
  },
  {
    name: "CronList",
    kind: "built-in",
    description: {
      ko: "활성 cron 잡을 나열합니다.",
      en: "List active cron jobs.",
    },
  },
  {
    name: "RemoteTrigger",
    kind: "built-in",
    description: {
      ko: "예약된 클라우드 에이전트 루틴을 관리합니다.",
      en: "Manage scheduled cloud agent routines.",
    },
  },
  // ----- worktree -----
  {
    name: "EnterWorktree",
    kind: "built-in",
    description: {
      ko: "격리된 git worktree를 만들고 그곳으로 전환합니다.",
      en: "Create an isolated git worktree and switch into it.",
    },
  },
  {
    name: "ExitWorktree",
    kind: "built-in",
    description: {
      ko: "worktree 세션을 종료하고 원래 디렉토리로 복귀합니다.",
      en: "Exit a worktree session and return to the original directory.",
    },
  },
  // ----- design / ops -----
  {
    name: "DesignSync",
    kind: "built-in",
    description: {
      ko: "로컬 디자인 시스템 컴포넌트를 claude.ai/design 프로젝트로 동기화합니다.",
      en: "Sync local design system components to a claude.ai/design project.",
    },
  },
  {
    name: "ShareOnboardingGuide",
    kind: "built-in",
    description: {
      ko: "ONBOARDING.md 를 업로드하고 팀 공유 링크를 받습니다.",
      en: "Upload ONBOARDING.md and get a team share link.",
    },
  },
  {
    name: "Cd",
    kind: "built-in",
    description: {
      ko: "에이전트의 현재 작업 디렉토리를 변경합니다.",
      en: "Change the agent's current working directory.",
    },
  },
] as const

/**
 * Legacy / pre-rename aliases mapped to their POST-rename canonical
 * names per the v2.1.170 binary's `B78` alias map. When a user types one
 * of the LHS names, the combobox surfaces a hint pointing to the RHS
 * canonical so authors who carry over old presets are not silently left
 * with a dead matcher. The runtime itself still translates LHS -> RHS,
 * but our hidden form input is what eventually persists to the IR, so
 * we prefer writing the canonical name there.
 */
export const LEGACY_ALIASES: Readonly<Record<string, string>> = Object.freeze({
  Task: "Agent",
  KillShell: "TaskStop",
  KillBash: "TaskStop",
  AgentOutputTool: "TaskOutput",
  BashOutputTool: "TaskOutput",
  AgentOutput: "TaskOutput",
  BashOutput: "TaskOutput",
  ListPeers: "ListAgents",
  Brief: "SendUserMessage",
  ListMcpResources: "ListMcpResourcesTool",
  ReadMcpResource: "ReadMcpResourceTool",
})

/**
 * Returns the post-rename canonical name for a known legacy alias, or
 * null if the typed name is not a known alias. Case-sensitive (matches
 * the binary's literal map keys); callers wanting case-insensitive
 * resolution should `.toLowerCase()` first if they have a need.
 */
export function legacyAliasCanonical(name: string): string | null {
  const trimmed = name.trim()
  if (!trimmed) return null
  // Case-sensitive lookup against the binary's literal LHS keys.
  if (Object.prototype.hasOwnProperty.call(LEGACY_ALIASES, trimmed)) {
    return LEGACY_ALIASES[trimmed]
  }
  return null
}

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
 *   - "mcp"      : starts with `mcp__` (Claude Code MCP naming convention,
 *                  matched case-insensitively to stay in sync with
 *                  matcherClassForToolScope in policies/new/page.tsx).
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
 * WebFetch, Agent). Kept in declaration order so the dropdown surface
 * is deterministic for source-grep tests.
 *
 * Note: `Agent` is the post-rename canonical for what used to be `Task`.
 * If a wizard test pins `Task`, update it to `Agent` per LEGACY_ALIASES.
 */
export const CC_TOP_SUGGESTIONS: readonly string[] = [
  "Bash",
  "Read",
  "Edit",
  "WebFetch",
  "Agent",
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
