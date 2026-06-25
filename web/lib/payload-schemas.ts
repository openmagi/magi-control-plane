/**
 * P7: CC hook payload schema menu — client-side cache + helper.
 *
 * Mirrors src/magi_cp/policy/payload_schemas.py. The shape is small and
 * effectively never changes per-tenant, so we ship it inline rather than
 * round-tripping to /payload-schemas from every wizard render. The REST
 * endpoint stays the source of truth — a dashboard build that wants to
 * stay in lockstep can call cloud.listPayloadSchemas() and shadow this
 * static copy.
 *
 * Why both? The wizard renders Server Components; we need synchronous
 * lookup at render time without an awaited fetch on every keystroke.
 * The REST endpoint is for clients that don't run the dashboard build
 * (third-party UIs, future ai-authoring loops, automated linters).
 */

/** Canonical namespace the runtime materializes CC stdin under. Keep
 * in lockstep with `MAGI_HOOK_NS` in src/magi_cp/policy/payload_schemas.py
 * — a chip with path `tool_input.command` resolves to predicate
 * `magi:tool_input.command` ↦ `<MAGI_HOOK_NS>tool_input.command`. */
export const MAGI_HOOK_NS = "https://magi.openmagi.ai/cc/hook#"

export type MatcherClass = "tool" | "no_tool" | "final"

export type FieldType = "str" | "int" | "bool" | "list" | "dict"

export type ShaclDatatype =
  | "xsd:string"
  | "xsd:integer"
  | "xsd:boolean"
  | "xsd:anyURI"
  | "rdf:JSON"

export type ShaclKind = "node" | "property"

export type FieldDescriptor = {
  path: string
  type: FieldType
  description: string
  example?: string
  /** SHACL datatype the lifted triple will carry. Filled by
   * `withShaclHints` so the chip stub-inserter doesn't have to guess.
   * Mirrors `sh_datatype` in the Python registry. */
  sh_datatype?: ShaclDatatype
  /** Idiomatic SHACL frame for this field — `property` for scalar
   * leaves (sh:PropertyShape sh:path …), `node` for nested JSON
   * (sh:NodeShape sh:targetClass magi:Hook + sh:property …). */
  sh_kind?: ShaclKind
  /** D64: friendly display label (KO + EN). UI surfaces render the
   * locale-matched string as the primary chip text and keep the raw
   * `path` in the title= tooltip + aria-label + click-to-insert
   * behaviour. Click-to-insert STAYS the raw path so authors editing
   * regex / shacl get the literal field path the runtime materializes.
   * An UNKNOWN path falls back to the raw path verbatim. */
  display_label_ko?: string
  display_label_en?: string
}

const FT_TO_DATATYPE: Record<FieldType, ShaclDatatype> = {
  str: "xsd:string",
  int: "xsd:integer",
  bool: "xsd:boolean",
  list: "rdf:JSON",
  dict: "rdf:JSON",
}

function withShaclHints(fields: FieldDescriptor[]): FieldDescriptor[] {
  return fields.map((f) => ({
    ...f,
    sh_datatype: f.sh_datatype ?? FT_TO_DATATYPE[f.type],
    sh_kind: f.sh_kind ?? (f.type === "dict" || f.type === "list" ? "node" : "property"),
  }))
}

/* ── D64: friendly display labels ─────────────────────────────────────
 *
 * Mirrors `_DISPLAY_LABELS_*` in src/magi_cp/policy/payload_schemas.py.
 * Keep the two tables in lockstep: a path missing from one side falls
 * back to the raw path verbatim, which is honest but the operator
 * loses the friendly name.
 *
 * The raw path stays the truth source. UI chips, the verifier expander
 * Input Paths panel, the wizard /verifiers/new path picker, and the
 * IR draft pane all render display labels for known paths and the raw
 * path verbatim for unknown ones (operator-typed MCP slugs etc).
 * Click-to-insert behaviour STAYS raw path everywhere: operators
 * authoring regex / shacl need the actual field path, not a friendly
 * label that the runtime doesn't materialize.
 */
const DISPLAY_LABELS_EN: Record<string, string> = {
  "tool_input.command": "Bash command",
  "tool_input.cwd": "Command working directory",
  "tool_input.timeout": "Command timeout (ms)",
  "tool_input.description": "Command description",
  "tool_input.url": "Fetched URL",
  "tool_input.prompt": "Fetch follow-up prompt",
  "tool_input.file_path": "File path",
  "tool_input.old_string": "Replaced text",
  "tool_input.new_string": "Replacement text",
  "tool_input.content": "File content",
  "tool_input.offset": "Read line offset",
  "tool_input.limit": "Read line limit",
  "tool_input": "Tool input",
  "tool_response.output": "Tool output",
  "tool_response.is_error": "Tool error flag",
  "tool_response.duration_ms": "Tool duration (ms)",
  "session_id": "Session ID",
  "transcript_path": "Conversation transcript path",
  "transcript": "Recent conversation turns",
  "tool_name": "Tool name",
  "tool_use_id": "Tool call ID",
  "cwd": "Session working directory",
  "final_message": "Agent final answer",
  "prompt": "User prompt",
  "citations[].quote": "Cited quote",
  "citations[].ref": "Citation reference id",
}

const DISPLAY_LABELS_KO: Record<string, string> = {
  "tool_input.command": "Bash 명령어",
  "tool_input.cwd": "명령 작업 디렉터리",
  "tool_input.timeout": "명령 타임아웃(ms)",
  "tool_input.description": "명령 설명",
  "tool_input.url": "요청 URL",
  "tool_input.prompt": "Fetch 후속 프롬프트",
  "tool_input.file_path": "파일 경로",
  "tool_input.old_string": "치환 대상 텍스트",
  "tool_input.new_string": "치환할 텍스트",
  "tool_input.content": "파일 내용",
  "tool_input.offset": "읽기 시작 라인",
  "tool_input.limit": "읽기 최대 라인 수",
  "tool_input": "도구 입력",
  "tool_response.output": "도구 출력",
  "tool_response.is_error": "도구 오류 여부",
  "tool_response.duration_ms": "도구 실행 시간(ms)",
  "session_id": "세션 ID",
  "transcript_path": "대화 기록 경로",
  "transcript": "최근 대화 턴",
  "tool_name": "도구 이름",
  "tool_use_id": "도구 호출 ID",
  "cwd": "세션 작업 디렉터리",
  "final_message": "에이전트 최종 답변",
  "prompt": "사용자 입력",
  "citations[].quote": "인용 본문",
  "citations[].ref": "인용 ref id",
}

/**
 * Friendly display label for a raw payload path.
 *
 * UNKNOWN path → raw path verbatim (operator-typed custom paths render
 * the literal field path; the UI never claims a friendly name it does
 * not have).
 *
 * Locale fallback chain: ko → en → raw path. Unsupported locales
 * degrade to English so a future widening (e.g. "ja") doesn't crash
 * the chip renderer.
 */
export function getDisplayLabel(path: string, locale: "ko" | "en" = "en"): string {
  if (!path) return path
  if (locale === "ko") {
    const ko = DISPLAY_LABELS_KO[path]
    if (ko) return ko
  }
  const en = DISPLAY_LABELS_EN[path]
  if (en) return en
  return path
}

function withDisplayLabels(fields: FieldDescriptor[]): FieldDescriptor[] {
  return fields.map((f) => ({
    ...f,
    display_label_ko: f.display_label_ko ?? getDisplayLabel(f.path, "ko"),
    display_label_en: f.display_label_en ?? getDisplayLabel(f.path, "en"),
  }))
}

export type PayloadSchema = {
  event: string
  matcher_class: MatcherClass
  fields: FieldDescriptor[]
}

const COMMON_TOOL_ENVELOPE: FieldDescriptor[] = [
  {
    path: "session_id",
    type: "str",
    description:
      "Opaque CC session identifier. Stable across the session; useful for cross-turn correlation.",
    example: "abc123def",
  },
  {
    path: "transcript_path",
    type: "str",
    description:
      "Filesystem path to the full session transcript (read-only from the gate's perspective).",
    example: "/Users/me/.claude/transcripts/abc.jsonl",
  },
  {
    path: "tool_name",
    type: "str",
    description:
      "The tool that fired this hook (Bash, Read, Edit, WebFetch, mcp__server__name, ...).",
    example: "Bash",
  },
  {
    path: "tool_use_id",
    type: "str",
    description:
      "Unique id for THIS tool call. Use to correlate PreToolUse with the matching PostToolUse. Opaque token — DO NOT constrain with xsd:integer.",
    example: "toolu_01ABcdef0123",
  },
]

const BASH_FIELDS: FieldDescriptor[] = [
  {
    path: "tool_input.command",
    type: "str",
    description:
      "The shell command CC is about to run. This is the field most policies want — every sentinel regex in the gate runs against it.",
    example: "git push origin main",
  },
  {
    path: "tool_input.cwd",
    type: "str",
    description: "Working directory for the command. Optional; absent on calls that don't specify one.",
    example: "/Users/me/project",
  },
  {
    path: "tool_input.timeout",
    type: "int",
    description: "Per-call timeout in milliseconds, if requested by the model.",
  },
  {
    path: "tool_input.description",
    type: "str",
    description: "Short human-readable description of what the command does. Model-authored.",
  },
]

const WEBFETCH_FIELDS: FieldDescriptor[] = [
  {
    path: "tool_input.url",
    type: "str",
    description:
      "Full URL CC is about to fetch. This is what the fetch-domain shortcut compiles into a regex on.",
    example: "https://example.com/api",
  },
  {
    path: "tool_input.prompt",
    type: "str",
    description:
      "Optional prompt CC will run against the fetched content (WebFetch summarises rather than dumping the raw page).",
  },
]

// Edit-specific fields. Mirrors `_EDIT_FIELDS` in the Python registry —
// see the P2 review note in payload_schemas.py for why the split.
const EDIT_FIELDS: FieldDescriptor[] = [
  {
    path: "tool_input.file_path",
    type: "str",
    description: "Absolute path of the file being edited.",
    example: "/Users/me/project/src/app.py",
  },
  {
    path: "tool_input.old_string",
    type: "str",
    description:
      "Exact text being replaced. Edit-only — absent on Write calls.",
    example: "TODO: fix me",
  },
  {
    path: "tool_input.new_string",
    type: "str",
    description:
      "Replacement text. Edit-only — Write uses `tool_input.content` instead.",
    example: "done.",
  },
]

// Write-specific fields.
const WRITE_FIELDS: FieldDescriptor[] = [
  {
    path: "tool_input.file_path",
    type: "str",
    description: "Absolute path of the file being written.",
    example: "/Users/me/project/src/app.py",
  },
  {
    path: "tool_input.content",
    type: "str",
    description:
      "Full file body being written. Write-only — Edit uses old_string + new_string instead.",
    example: "print('hello')\n",
  },
]

const READ_FIELDS: FieldDescriptor[] = [
  {
    path: "tool_input.file_path",
    type: "str",
    description: "Absolute path of the file being read.",
    example: "/etc/passwd",
  },
  {
    path: "tool_input.offset",
    type: "int",
    description: "Optional line offset to start reading from.",
  },
  {
    path: "tool_input.limit",
    type: "int",
    description: "Optional max number of lines to read.",
  },
]

const GENERIC_TOOL_FIELDS: FieldDescriptor[] = [
  {
    path: "tool_input",
    type: "dict",
    description:
      "The full tool input dict. Field shape varies by tool — when authoring against an arbitrary tool, prefer matching on `tool_name` first to narrow.",
  },
]

const TOOL_RESPONSE_FIELDS: FieldDescriptor[] = [
  {
    path: "tool_response.output",
    type: "str",
    description:
      "The tool's textual output. For regex/llm_critic after_tool_use checks this is the field you want.",
    example: "Pushed 3 commits to origin/main",
  },
  {
    path: "tool_response.is_error",
    type: "bool",
    description: "True iff the tool reported a failure.",
  },
  {
    path: "tool_response.duration_ms",
    type: "int",
    description: "Wall time the tool took, in milliseconds.",
  },
]

const USER_PROMPT_SUBMIT_FIELDS: FieldDescriptor[] = [
  {
    path: "prompt",
    type: "str",
    description:
      "The user message that just landed in the session. Use for prompt-injection screens, PII filters, etc.",
    example: "please push to main",
  },
  { path: "session_id", type: "str", description: "Opaque CC session identifier." },
  {
    path: "transcript_path",
    type: "str",
    description:
      "Absolute path to a JSONL file containing the conversation transcript so far. A verifier or run_command script can OPEN this file and read prior turns (user prompts, assistant replies, tool calls). The file is owned by the CC session and is readable by the gate process; you do not need extra permissions to inspect it.",
    example: "/tmp/cc-session-abc123/transcript.jsonl",
  },
]

const STOP_FIELDS: FieldDescriptor[] = [
  {
    path: "final_message",
    type: "str",
    description:
      "The assistant's final answer string CC is about to send. This is the field pre_final policies usually want.",
    example: "I cannot verify that claim.",
  },
  {
    path: "transcript_path",
    type: "str",
    description: "Path to the session transcript (full history).",
  },
  {
    path: "transcript",
    type: "list",
    description:
      "Recent turns (last N), pre-loaded so policies don't have to open the transcript file.",
  },
  { path: "session_id", type: "str", description: "Opaque CC session identifier." },
]

const SESSION_START_FIELDS: FieldDescriptor[] = [
  { path: "session_id", type: "str", description: "Opaque CC session identifier." },
  { path: "cwd", type: "str", description: "Working directory CC was launched in." },
]

const PRE_COMPACT_FIELDS: FieldDescriptor[] = [
  { path: "session_id", type: "str", description: "Opaque CC session identifier." },
  {
    path: "transcript_path",
    type: "str",
    description: "Path to the session transcript about to be compacted.",
  },
]

const TOOL_SPECIFIC_BY_NAME: Record<string, FieldDescriptor[]> = {
  Bash: BASH_FIELDS,
  WebFetch: WEBFETCH_FIELDS,
  // Edit and Write carry disjoint fields — see P2 fix in
  // src/magi_cp/policy/payload_schemas.py for the rationale.
  Edit: EDIT_FIELDS,
  Write: WRITE_FIELDS,
  Read: READ_FIELDS,
}

const REGISTRY: Record<string, Partial<Record<MatcherClass, PayloadSchema>>> = {
  PreToolUse: {
    tool: {
      event: "PreToolUse",
      matcher_class: "tool",
      fields: [...COMMON_TOOL_ENVELOPE, ...GENERIC_TOOL_FIELDS],
    },
  },
  PostToolUse: {
    tool: {
      event: "PostToolUse",
      matcher_class: "tool",
      fields: [
        ...COMMON_TOOL_ENVELOPE,
        ...GENERIC_TOOL_FIELDS,
        ...TOOL_RESPONSE_FIELDS,
      ],
    },
  },
  UserPromptSubmit: {
    no_tool: {
      event: "UserPromptSubmit",
      matcher_class: "no_tool",
      fields: USER_PROMPT_SUBMIT_FIELDS,
    },
  },
  Stop: {
    final: { event: "Stop", matcher_class: "final", fields: STOP_FIELDS },
  },
  SubagentStop: {
    final: { event: "SubagentStop", matcher_class: "final", fields: STOP_FIELDS },
  },
  SessionStart: {
    no_tool: {
      event: "SessionStart",
      matcher_class: "no_tool",
      fields: SESSION_START_FIELDS,
    },
  },
  SessionEnd: {
    no_tool: {
      event: "SessionEnd",
      matcher_class: "no_tool",
      fields: SESSION_START_FIELDS,
    },
  },
  PreCompact: {
    no_tool: {
      event: "PreCompact",
      matcher_class: "no_tool",
      fields: PRE_COMPACT_FIELDS,
    },
  },
}

function resolveMatcherClass(matcher: string | undefined): MatcherClass {
  if (!matcher) return "no_tool"
  const m = matcher.trim()
  if (!m) return "no_tool"
  return "tool"
}

function enrichWithToolSpecific(base: PayloadSchema, matcher: string): PayloadSchema {
  const specific = TOOL_SPECIFIC_BY_NAME[matcher.trim()]
  if (!specific) return base
  const filtered = base.fields.filter((f) => f.path !== "tool_input")
  return {
    event: base.event,
    matcher_class: base.matcher_class,
    fields: [...filtered, ...specific],
  }
}

/**
 * Resolve (event, matcher) to a flat list of FieldDescriptor.
 *
 *   availableFields("PreToolUse", "Bash")  → 4 envelope + 4 Bash fields
 *   availableFields("PreToolUse", "*")     → 4 envelope + 1 generic tool_input
 *   availableFields("Stop")                → 4 final-answer fields
 *
 * Unknown event → []. Honest empty so the chip row hides itself rather
 * than rendering fake suggestions.
 */
export function availableFields(event: string, matcher?: string): FieldDescriptor[] {
  const bucket = REGISTRY[event]
  if (!bucket) return []
  const cls = resolveMatcherClass(matcher)
  let schema = bucket[cls]
  if (!schema) {
    const first = Object.values(bucket)[0]
    if (!first) return []
    schema = first
  }
  if (schema.matcher_class === "tool" && matcher && matcher !== "*") {
    return withDisplayLabels(
      withShaclHints(enrichWithToolSpecific(schema, matcher).fields),
    )
  }
  return withDisplayLabels(withShaclHints(schema.fields.slice()))
}

/** Full registry dump — for debugging / linting / a future "show me
 * everything CC can deliver" view. D64: each schema's fields carry the
 * resolved sh_datatype / sh_kind / display_label_* hints (matches the
 * `availableFields` contract so callers can render either with the
 * same FieldDescriptor shape). */
export function allSchemas(): PayloadSchema[] {
  const out: PayloadSchema[] = []
  for (const bucket of Object.values(REGISTRY)) {
    for (const schema of Object.values(bucket)) {
      if (schema) {
        out.push({
          event: schema.event,
          matcher_class: schema.matcher_class,
          fields: withDisplayLabels(withShaclHints(schema.fields.slice())),
        })
      }
    }
  }
  return out
}

/** Convenience: only the path strings. Useful when rendering a
 * dropdown of "Use field path" choices in the SHACL builder. */
export function availableFieldPaths(event: string, matcher?: string): string[] {
  return availableFields(event, matcher).map((f) => f.path)
}

/** Convenience: lifecycle (the wizard's coarsened event grouping) →
 * the underlying CC event name. Mirrors the LIFECYCLE_TO_EVENT map
 * in policies/new/page.tsx so the chip row stays consistent with
 * whichever lifecycle the wizard is on. D56d: widened from the
 * legacy 3-value union to all 8 CC hooks the wizard now covers. */
export type Lifecycle =
  | "before_tool_use" | "after_tool_use" | "pre_final"
  | "subagent_stop"   | "user_prompt"    | "pre_compact"
  | "session_start"   | "session_end"

export function lifecycleToEvent(lifecycle: Lifecycle): string {
  switch (lifecycle) {
    case "before_tool_use":  return "PreToolUse"
    case "after_tool_use":   return "PostToolUse"
    case "pre_final":        return "Stop"
    case "subagent_stop":    return "SubagentStop"
    case "user_prompt":      return "UserPromptSubmit"
    case "pre_compact":      return "PreCompact"
    case "session_start":    return "SessionStart"
    case "session_end":      return "SessionEnd"
  }
}

/**
 * P7 (issue #1, P0 #3 / P1 #4): cross-reference a raw SHACL shape (Turtle)
 * against the payload schema for the given (event, matcher). Returns
 * a list of human-readable issue strings; empty list means every
 * anchor is on a path the runtime actually delivers.
 *
 * Mirrors `lint_shacl_targets` in src/magi_cp/policy/payload_schemas.py
 * but is regex-based (we don't ship rdflib in the dashboard bundle).
 * Trade-off: a Turtle shape using exotic IRI forms or multi-line
 * collection syntax may slip past — the Python check in
 * Policy.validate() is the canonical line of defence; this TS check
 * is a fast author-time hint that catches the typical chip-style stub.
 *
 * The matched literals are local names under the canonical
 * `magi:` prefix (a.k.a. MAGI_HOOK_NS). Anchors on other namespaces
 * are out-of-contract and skipped — the caller may surface a separate
 * banner if it cares.
 */
export function lintShaclTargets(
  shapeTtl: string,
  event: string,
  matcher?: string,
): string[] {
  // Strip prefixed-name local parts after `magi:` for each anchor.
  // We accept both `magi:foo.bar` and `<MAGI_HOOK_NS>foo.bar`.
  const anchors: { kind: "targetNode" | "targetClass" | "path"; name: string }[] = []
  const PREFIXED = /(sh:targetNode|sh:targetClass|sh:path)\s+(?:magi:([A-Za-z_][A-Za-z0-9_.]*)|<https:\/\/magi\.openmagi\.ai\/cc\/hook#([^>]+)>)/g
  let m: RegExpExecArray | null
  while ((m = PREFIXED.exec(shapeTtl)) !== null) {
    const kindStr = m[1].slice("sh:".length) as "targetNode" | "targetClass" | "path"
    const name = m[2] ?? m[3]
    if (!name) continue
    anchors.push({ kind: kindStr, name })
  }
  if (anchors.length === 0) return []

  const known = new Set<string>(
    availableFields(event, matcher).map((f) => f.path),
  )
  const issues: string[] = []
  for (const a of anchors) {
    if (a.kind === "targetClass") {
      if (a.name !== "Hook") {
        issues.push(
          `sh:targetClass magi:${a.name} — only 'Hook' is materialized at runtime; this shape will be vacuously satisfied`,
        )
      }
      continue
    }
    if (a.name === "__hook__") continue
    if (known.has(a.name)) continue
    // Cheap nearest-match suggestion.
    let hint = ""
    let best = ""
    let bestScore = -1
    for (const p of known) {
      const score = lcsLen(a.name, p)
      if (score > bestScore) {
        bestScore = score
        best = p
      }
    }
    if (best && bestScore >= Math.max(3, Math.floor(a.name.length / 2))) {
      hint = `; did you mean '${best}'?`
    }
    issues.push(
      `sh:${a.kind} magi:${a.name} is not a field the runtime delivers for (${event}, ${matcher ?? "*"})${hint}`,
    )
  }
  return issues
}

function lcsLen(a: string, b: string): number {
  // Longest common subsequence length — cheap typo proximity. Fine for
  // the ~40-character path strings we work with.
  const m = a.length, n = b.length
  if (m === 0 || n === 0) return 0
  const dp = new Array<number>(n + 1).fill(0)
  for (let i = 1; i <= m; i++) {
    let prev = 0
    for (let j = 1; j <= n; j++) {
      const tmp = dp[j]
      if (a.charCodeAt(i - 1) === b.charCodeAt(j - 1)) {
        dp[j] = prev + 1
      } else if (dp[j - 1] > dp[j]) {
        dp[j] = dp[j - 1]
      }
      prev = tmp
    }
  }
  return dp[n]
}
