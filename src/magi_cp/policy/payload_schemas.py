"""Claude Code hook-payload schema menu (P7).

Every CC hook event delivers a JSON payload on the gate's stdin. Until now,
authors of `regex` / `llm_critic` / `shacl` requires entries had to *guess*
which field paths exist on that stdin — and a SHACL shape that targets a
non-existent path is "vacuously satisfied" (zero focus nodes → conforms),
so a mis-specified shape silently fails open at gate time.

This module ships a small, authoritative registry of what fields each
(event, matcher_class) pair carries, so:

  - The wizard can render suggestion chips beside the regex / llm_critic
    input ("here's what's actually in the payload — pick one or write
    your own pattern").
  - The SHACL builder can offer a "Use field path" dropdown that drops a
    `sh:targetNode :tool_input` style shape head into the editor.
  - The runtime `/verify_inline` SHACL path lifts the CC stdin payload
    into RDF triples under a canonical namespace so a chip-picked path
    actually selects a focus node at runtime (no more vacuous conforms).
  - Downstream review code (or a future linter) can cross-reference an
    authored pattern's tokens against the schema and flag references to
    fields that the runtime never delivers.

Source of truth: Claude Code's hook contract (see CC docs §"Hook input
schema") plus what `local/gate.py` actually extracts from the payload
today. PreToolUse + Bash is the canonical case — `tool_input.command`
is the single field the gate's sentinel regex runs against.

# Runtime contract — JSON → RDF lift

The CC stdin is a JSON object; SHACL needs RDF. To make the chip menu
honest, `/verify_inline` lifts the payload into a tiny data graph BEFORE
calling pyshacl:

  PREFIX magi:  <https://magi.openmagi.ai/cc/hook#>

  :hook a magi:Hook ;
        magi:event "<event>" ;
        magi:matcher "<matcher>" ;
        magi:tool_input.command "git push origin main" ;
        magi:tool_input.cwd "/Users/me/project" ;
        magi:session_id "abc123def" ;
        ...

Every leaf field from `available_fields(event, matcher)` that's actually
present in the JSON becomes one triple `<:hook> magi:<path> "<value>"`.
Nested fields use the dotted path verbatim as the predicate local name —
this matches the chip strings authors see in the wizard, so authoring
and runtime stay in lockstep.

A SHACL shape that picks `magi:tool_input.command` (the chip-shown name)
will therefore find at least one focus node when the runtime sees a Bash
call, and a `sh:datatype xsd:string sh:pattern ...` constraint underneath
fires instead of conforming vacuously. A shape that picks
`magi:tool_input.bogus` finds zero focus nodes — `/verify_inline` treats
that as deny (not conform), so silent fail-open is closed.

Authoring a SHACL shape head from a chip therefore looks like:

  @prefix sh:   <http://www.w3.org/ns/shacl#> .
  @prefix magi: <https://magi.openmagi.ai/cc/hook#> .
  @prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

  [] a sh:PropertyShape ;
     sh:path        magi:tool_input.command ;
     sh:datatype    xsd:string ;
     sh:minCount    1 ;
     sh:not         [ sh:pattern "^rm -rf /" ] .

NOT a substitute for the runtime check. The lint helper in this module
(`extract_targets`) and the per-policy validation in `Policy.validate()`
catch the most common authoring slip (picking a path that doesn't exist
under the chosen (event, matcher)). Anything more semantic is on the
author.
"""
from __future__ import annotations
import re
from typing import Literal, TypedDict


# Canonical namespace for the JSON → RDF lift described above. Authors
# who see `magi:tool_input.command` in the chip row are seeing the
# qualified name of the triple this module produces at runtime. Keep
# the URI stable — it is part of the authoring/runtime contract.
MAGI_HOOK_NS = "https://magi.openmagi.ai/cc/hook#"
MAGI_HOOK_SUBJECT = "https://magi.openmagi.ai/cc/hook#__hook__"
"""Stable subject IRI used for the single hook-instance triple set the
runtime materializes from the CC stdin JSON. Picking a chip path with
`sh:targetSubjectsOf` therefore lands exactly one focus node per
hook firing."""


MatcherClassLiteral = Literal["tool", "no_tool", "final"]
"""Coarsened matcher classes used by the schema menu.

The full policy/matrix.py has four (`tool` / `mcp_tool` / `wildcard` /
`tool_alt`), but every tool-matching class shares the same payload
shape (CC fills `tool_name` + `tool_input` regardless of whether the
policy matched on a builtin name, an MCP name, an alternation, or "*").
We collapse all of those into `tool`. `no_tool` covers events with no
tool context (UserPromptSubmit, SessionStart, …). `final` covers Stop /
SubagentStop, which carry the agent's final answer + transcript.
"""


FieldType = Literal["str", "int", "bool", "list", "dict"]

# SHACL hint metadata. `sh_datatype` is the XSD datatype the runtime
# value will carry once lifted into the data graph; `sh_kind` tells the
# wizard whether a NodeShape (anchor on the field itself) or a
# PropertyShape (constrain via `sh:path`) is the idiomatic frame.
ShaclDatatype = Literal[
    "xsd:string", "xsd:integer", "xsd:boolean", "xsd:anyURI", "rdf:JSON",
]
ShaclKind = Literal["node", "property"]


_FT_TO_DATATYPE: dict[FieldType, ShaclDatatype] = {
    "str": "xsd:string",
    "int": "xsd:integer",
    "bool": "xsd:boolean",
    # list / dict are nested JSON; we lift them as rdf:JSON literals so
    # SHACL authors can still write `sh:datatype rdf:JSON` constraints
    # and `sh:pattern` against the serialized form.
    "list": "rdf:JSON",
    "dict": "rdf:JSON",
}


class FieldDescriptor(TypedDict, total=False):
    path: str
    type: FieldType
    description: str
    example: str
    # SHACL idiom hints. Filled by `_with_sh_hints()` below so authors
    # of new field tables don't have to repeat the obvious mapping.
    sh_datatype: ShaclDatatype
    sh_kind: ShaclKind
    # D64: friendly display label (filled by `_with_display_label()` from
    # the `_DISPLAY_LABELS` table at registry-resolution time). UI surfaces
    # render this as the primary chip text; raw `path` stays in the title
    # attribute + aria-label + click-to-insert behaviour so authoring still
    # works with the literal field path. An UNKNOWN path falls back to
    # showing the raw path verbatim (back-compat).
    display_label_ko: str
    display_label_en: str


class PayloadSchema(TypedDict):
    event: str
    matcher_class: MatcherClassLiteral
    fields: list[FieldDescriptor]


# ── D64: friendly display labels ──────────────────────────────────────
# Operator-friendly names for the raw payload paths above. The raw path
# stays the truth source (every authoring affordance keeps inserting the
# raw path verbatim, every SHACL anchor still resolves to
# `magi:<rawpath>`); the display label only changes what the UI SHOWS on
# top of the chip / row, with the raw path moving to the title= tooltip
# and the aria-label so screen reader users still hear it.
#
# Per the D64 brief: an UNKNOWN path falls back to showing the raw path
# verbatim — operator-typed custom paths (e.g. an MCP tool slug) won't
# appear here, and surfacing the raw path is the right "honest" default.
_DISPLAY_LABELS_EN: dict[str, str] = {
    # tool_input.* — what the model is asking the tool to do
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
    # tool_response.* — what the tool returned
    "tool_response.output": "Tool output",
    "tool_response.is_error": "Tool error flag",
    "tool_response.duration_ms": "Tool duration (ms)",
    # envelope fields
    "session_id": "Session ID",
    "transcript_path": "Conversation transcript path",
    "transcript": "Recent conversation turns",
    "tool_name": "Tool name",
    "tool_use_id": "Tool call ID",
    "cwd": "Session working directory",
    # Stop / SubagentStop
    "final_message": "Agent final answer",
    # UserPromptSubmit
    "prompt": "User prompt",
    # citation_verify-style nested fields (operator-typed; surfaced here
    # so a verifier field_checks row picked from a custom verifier still
    # gets a friendly name on the catalog).
    "citations[].quote": "Cited quote",
    "citations[].ref": "Citation reference id",
    # D79 — fields newly promoted to verified payload shapes.
    "error": "Error message",
    "error_details": "Error details",
    "last_assistant_message": "Last assistant message",
    "is_interrupt": "Was an interrupt",
    "duration_ms": "Tool duration (ms)",
    "permission_suggestions": "Suggested permission rules",
    "reason": "Reason",
    "expansion_type": "Expansion type",
    "command_name": "Slash command name",
    "command_args": "Slash command args",
    "command_source": "Command source",
    "trigger": "Trigger",
    "compact_summary": "Compaction summary",
    "mcp_server_name": "MCP server name",
    "message": "Message",
    "mode": "Mode",
    "url": "URL",
    "elicitation_id": "Elicitation ID",
    "requested_schema": "Requested schema",
    "action": "Action",
    "content": "Content",
    "agent_id": "Subagent ID",
    "agent_type": "Subagent type",
    "title": "Notification title",
    "notification_type": "Notification kind",
    "teammate_name": "Teammate name",
    "team_name": "Team name",
    "task_id": "Task ID",
    "task_subject": "Task subject",
    "task_description": "Task description",
    "source": "Settings layer",
    "file_path": "File path",
    "name": "Worktree slug",
    "worktree_path": "Worktree path",
    "memory_type": "Memory layer",
    "load_reason": "Load reason",
    "globs": "Glob patterns",
    "trigger_file_path": "Triggering file path",
    "parent_file_path": "Parent file path",
    "old_cwd": "Previous working directory",
    "new_cwd": "New working directory",
    "event": "Filesystem event kind",
    "tool_calls": "Tool calls in batch",
    "turn_id": "Turn ID",
    "message_id": "Message ID",
    "index": "Delta index",
    "final": "Final delta flag",
    "delta": "Delta text",
}

_DISPLAY_LABELS_KO: dict[str, str] = {
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
    # D79
    "error": "오류 메시지",
    "error_details": "오류 상세",
    "last_assistant_message": "마지막 어시스턴트 메시지",
    "is_interrupt": "인터럽트 여부",
    "duration_ms": "도구 실행 시간(ms)",
    "permission_suggestions": "권장 권한 규칙",
    "reason": "사유",
    "expansion_type": "확장 종류",
    "command_name": "슬래시 커맨드 이름",
    "command_args": "슬래시 커맨드 인자",
    "command_source": "커맨드 출처",
    "trigger": "트리거",
    "compact_summary": "컴팩션 요약",
    "mcp_server_name": "MCP 서버 이름",
    "message": "메시지",
    "mode": "모드",
    "url": "URL",
    "elicitation_id": "Elicitation ID",
    "requested_schema": "요청 스키마",
    "action": "사용자 응답",
    "content": "응답 내용",
    "agent_id": "서브에이전트 ID",
    "agent_type": "서브에이전트 종류",
    "title": "알림 제목",
    "notification_type": "알림 종류",
    "teammate_name": "팀메이트 이름",
    "team_name": "팀 이름",
    "task_id": "태스크 ID",
    "task_subject": "태스크 주제",
    "task_description": "태스크 설명",
    "source": "설정 레이어",
    "file_path": "파일 경로",
    "name": "워크트리 슬러그",
    "worktree_path": "워크트리 경로",
    "memory_type": "메모리 레이어",
    "load_reason": "로드 사유",
    "globs": "글롭 패턴",
    "trigger_file_path": "트리거 파일 경로",
    "parent_file_path": "상위 지침 파일 경로",
    "old_cwd": "이전 작업 디렉터리",
    "new_cwd": "새 작업 디렉터리",
    "event": "파일시스템 이벤트 종류",
    "tool_calls": "배치 내 도구 호출",
    "turn_id": "턴 ID",
    "message_id": "메시지 ID",
    "index": "델타 인덱스",
    "final": "마지막 델타 플래그",
    "delta": "델타 텍스트",
}


def get_display_label(path: str, locale: str = "en") -> str:
    """Friendly display label for a raw payload path.

    Returns the localized label when the path is in the registry. An
    UNKNOWN path falls back to the raw path verbatim — operator-typed
    custom paths (MCP tool slugs, citation_verify nested keys without a
    matching entry) display the literal path so the UI never claims a
    friendly name it doesn't have.

    Locale falls back to English when an unsupported locale is passed,
    so a future widening (e.g. "ja") doesn't crash the chip renderer.
    """
    if not path:
        return path
    if locale == "ko":
        label = _DISPLAY_LABELS_KO.get(path)
        if label:
            return label
    label = _DISPLAY_LABELS_EN.get(path)
    if label:
        return label
    return path


def _with_display_label(fields: list[FieldDescriptor]) -> list[FieldDescriptor]:
    """Auto-fill `display_label_ko` + `display_label_en` for the given
    field descriptors. UNKNOWN paths get the raw path as their label so
    UI rendering can read the key unconditionally."""
    out: list[FieldDescriptor] = []
    for f in fields:
        path = f.get("path")
        if not path:
            out.append(f)
            continue
        new: FieldDescriptor = dict(f)  # type: ignore[assignment]
        if "display_label_ko" not in new:
            new["display_label_ko"] = get_display_label(path, "ko")
        if "display_label_en" not in new:
            new["display_label_en"] = get_display_label(path, "en")
        out.append(new)
    return out


def _with_sh_hints(fields: list[FieldDescriptor]) -> list[FieldDescriptor]:
    """Auto-fill `sh_datatype` + `sh_kind` based on `type`.

    Leaf scalar fields (str/int/bool) default to `property` kind — the
    idiomatic SHACL frame is `sh:PropertyShape sh:path magi:<path>`.
    `dict` / `list` default to `node` (anchor with `sh:targetNode` or
    `sh:targetSubjectsOf`) because constraining nested JSON shape
    structurally usually wants the node-level frame.
    """
    out: list[FieldDescriptor] = []
    for f in fields:
        ft = f.get("type")
        if ft is None:
            out.append(f)
            continue
        new: FieldDescriptor = dict(f)  # type: ignore[assignment]
        if "sh_datatype" not in new:
            new["sh_datatype"] = _FT_TO_DATATYPE[ft]
        if "sh_kind" not in new:
            new["sh_kind"] = "node" if ft in ("dict", "list") else "property"
        out.append(new)
    return out


# Common envelope fields delivered on every PreToolUse / PostToolUse hook.
# Documented separately so per-tool entries can extend without re-listing.
_COMMON_TOOL_ENVELOPE: list[FieldDescriptor] = [
    {
        "path": "session_id",
        "type": "str",
        "description": "Opaque CC session identifier. Stable across the "
                       "session; useful for cross-turn correlation.",
        "example": "abc123def",
    },
    {
        "path": "transcript_path",
        "type": "str",
        "description": "Absolute path to a JSONL file containing the "
                       "conversation transcript so far. A verifier or "
                       "run_command script can OPEN this file and read "
                       "prior turns (user prompts, assistant replies, "
                       "tool calls). The file is owned by the CC session "
                       "and is readable by the gate process; you do not "
                       "need extra permissions to inspect it.",
        "example": "/Users/me/.claude/transcripts/abc.jsonl",
    },
    {
        "path": "tool_name",
        "type": "str",
        "description": "The tool that fired this hook (Bash, Read, "
                       "Edit, WebFetch, mcp__server__name, ...).",
        "example": "Bash",
    },
    {
        "path": "tool_use_id",
        "type": "str",
        "description": "Unique id for THIS tool call. Use to correlate "
                       "PreToolUse with the matching PostToolUse. "
                       "Opaque token — DO NOT constrain with "
                       "xsd:integer.",
        "example": "toolu_01ABcdef0123",
    },
]


# ── per-tool input fields ────────────────────────────────────────────
# These slot under tool_input.* on PreToolUse / PostToolUse payloads.

_BASH_FIELDS: list[FieldDescriptor] = [
    {
        "path": "tool_input.command",
        "type": "str",
        "description": "The shell command CC is about to run. This is "
                       "the field most policies want — every sentinel "
                       "regex in the gate runs against it.",
        "example": "git push origin main",
    },
    {
        "path": "tool_input.cwd",
        "type": "str",
        "description": "Working directory for the command. Optional; "
                       "absent on calls that don't specify one.",
        "example": "/Users/me/project",
    },
    {
        "path": "tool_input.timeout",
        "type": "int",
        "description": "Per-call timeout in milliseconds, if requested "
                       "by the model.",
    },
    {
        "path": "tool_input.description",
        "type": "str",
        "description": "Short human-readable description of what the "
                       "command does. Model-authored.",
        "example": "push current branch to origin",
    },
]

_WEBFETCH_FIELDS: list[FieldDescriptor] = [
    {
        "path": "tool_input.url",
        "type": "str",
        "description": "Full URL CC is about to fetch. This is what the "
                       "fetch-domain shortcut compiles into a regex on.",
        "example": "https://example.com/api",
    },
    {
        "path": "tool_input.prompt",
        "type": "str",
        "description": "Optional prompt CC will run against the fetched "
                       "content (WebFetch summarises rather than dumping "
                       "the raw page).",
    },
]

# Edit-specific input fields (old_string + new_string + file_path).
# A wizard scoping policy to `Edit` should see exactly these; a wizard
# scoping to `Write` should NOT see old_string/new_string because they
# are not in the Write payload — a SHACL shape targeting them would be
# vacuously satisfied, exactly the silent fail-open mode P7 prevents.
_EDIT_FIELDS: list[FieldDescriptor] = [
    {
        "path": "tool_input.file_path",
        "type": "str",
        "description": "Absolute path of the file being edited.",
        "example": "/Users/me/project/src/app.py",
    },
    {
        "path": "tool_input.old_string",
        "type": "str",
        "description": "Exact text being replaced. Edit-only — absent "
                       "on Write calls.",
        "example": "TODO: fix me",
    },
    {
        "path": "tool_input.new_string",
        "type": "str",
        "description": "Replacement text. Edit-only — Write uses "
                       "`tool_input.content` instead.",
        "example": "done.",
    },
]

# Write-specific input fields (file_path + content, no old/new_string).
_WRITE_FIELDS: list[FieldDescriptor] = [
    {
        "path": "tool_input.file_path",
        "type": "str",
        "description": "Absolute path of the file being written.",
        "example": "/Users/me/project/src/app.py",
    },
    {
        "path": "tool_input.content",
        "type": "str",
        "description": "Full file body being written. Write-only — "
                       "Edit uses `tool_input.old_string` + "
                       "`tool_input.new_string` instead.",
        "example": "print('hello')\n",
    },
]

_READ_FIELDS: list[FieldDescriptor] = [
    {
        "path": "tool_input.file_path",
        "type": "str",
        "description": "Absolute path of the file being read.",
        "example": "/etc/passwd",
    },
    {
        "path": "tool_input.offset",
        "type": "int",
        "description": "Optional line offset to start reading from.",
    },
    {
        "path": "tool_input.limit",
        "type": "int",
        "description": "Optional max number of lines to read.",
    },
]

# Generic catch-all for any other tool — every CC tool call carries
# `tool_input` as a dict; the exact keys vary by tool but `tool_input`
# itself is always present, so a SHACL shape targeting the root dict
# is at least never vacuous.
_GENERIC_TOOL_FIELDS: list[FieldDescriptor] = [
    {
        "path": "tool_input",
        "type": "dict",
        "description": "The full tool input dict. Field shape varies "
                       "by tool — when authoring against an arbitrary "
                       "tool, prefer matching on `tool_name` first to "
                       "narrow.",
    },
]


# ── PostToolUse response envelope ─────────────────────────────────────
_TOOL_RESPONSE_FIELDS: list[FieldDescriptor] = [
    {
        "path": "tool_response.output",
        "type": "str",
        "description": "The tool's textual output. For regex/llm_critic "
                       "after_tool_use checks this is the field you want.",
        "example": "Pushed 3 commits to origin/main",
    },
    {
        "path": "tool_response.is_error",
        "type": "bool",
        "description": "True iff the tool reported a failure.",
    },
    {
        "path": "tool_response.duration_ms",
        "type": "int",
        "description": "Wall time the tool took, in milliseconds.",
    },
]


# ── no-tool-context events ────────────────────────────────────────────
_USER_PROMPT_SUBMIT_FIELDS: list[FieldDescriptor] = [
    {
        "path": "prompt",
        "type": "str",
        "description": "The user message that just landed in the "
                       "session. Use for prompt-injection screens, "
                       "PII filters, etc.",
        "example": "please push to main",
    },
    {
        "path": "session_id",
        "type": "str",
        "description": "Opaque CC session identifier.",
    },
    {
        "path": "transcript_path",
        "type": "str",
        "description": "Path to the session transcript.",
    },
]

_STOP_FIELDS: list[FieldDescriptor] = [
    {
        "path": "final_message",
        "type": "str",
        "description": "The assistant's final answer string CC is "
                       "about to send. This is the field pre_final "
                       "policies usually want.",
        "example": "I cannot verify that claim.",
    },
    {
        "path": "transcript_path",
        "type": "str",
        "description": "Path to the session transcript (full history).",
    },
    {
        "path": "transcript",
        "type": "list",
        "description": "Recent turns (last N), pre-loaded so policies "
                       "don't have to open the transcript file. Shape: "
                       "[{role, content, ...}, ...].",
    },
    {
        "path": "session_id",
        "type": "str",
        "description": "Opaque CC session identifier.",
    },
]

_SESSION_START_FIELDS: list[FieldDescriptor] = [
    {
        "path": "session_id",
        "type": "str",
        "description": "Opaque CC session identifier.",
    },
    {
        "path": "cwd",
        "type": "str",
        "description": "Working directory CC was launched in.",
    },
]

_SESSION_END_FIELDS: list[FieldDescriptor] = list(_SESSION_START_FIELDS)

_PRE_COMPACT_FIELDS: list[FieldDescriptor] = [
    {
        "path": "session_id",
        "type": "str",
        "description": "Opaque CC session identifier.",
    },
    {
        "path": "transcript_path",
        "type": "str",
        "description": "Path to the session transcript about to be "
                       "compacted.",
    },
    {
        "path": "trigger",
        "type": "str",
        "description": "Compaction trigger — `\"manual\"` when the "
                       "operator ran `/compact`, `\"auto\"` when the "
                       "runtime hit its compaction threshold.",
        "example": "auto",
    },
]


# ── D79 promoted events ──────────────────────────────────────────────
# Every field list below was extracted from the CC 2.1.170 binary by
# grepping for the `hook_event_name:"<Event>"` constructor literal and
# reading the sibling object keys. Cross-check with:
#
#   strings /opt/homebrew/Caskroom/claude-code/2.1.170/claude \
#     | grep -oE 'hook_event_name:"<Event>"[^}]{0,250}'
#
# Common envelope (session_id / transcript_path / cwd) is appended via
# `_with_common_envelope` where the runtime is known to also stamp it.

# Tool-context observability variants ---------------------------------

_POST_TOOL_USE_FAILURE_FIELDS: list[FieldDescriptor] = [
    {
        "path": "tool_name",
        "type": "str",
        "description": "The tool that failed (Bash, Read, Edit, "
                       "WebFetch, mcp__server__name, ...).",
        "example": "Bash",
    },
    {
        "path": "tool_input",
        "type": "dict",
        "description": "The tool input dict CC was about to execute "
                       "when the failure happened.",
    },
    {
        "path": "tool_use_id",
        "type": "str",
        "description": "Unique id for THIS tool call. Correlates with "
                       "the corresponding PreToolUse payload.",
        "example": "toolu_01ABcdef0123",
    },
    {
        "path": "error",
        "type": "str",
        "description": "Failure message the runtime captured. The "
                       "field most failure-recovery scripts want.",
        "example": "git push failed: non-fast-forward",
    },
    {
        "path": "is_interrupt",
        "type": "bool",
        "description": "True iff the failure was triggered by an "
                       "operator interrupt (Ctrl-C / cancel) rather "
                       "than a genuine tool error.",
    },
    {
        "path": "duration_ms",
        "type": "int",
        "description": "Wall time the tool ran before failing, in "
                       "milliseconds.",
    },
]

_POST_TOOL_BATCH_FIELDS: list[FieldDescriptor] = [
    {
        "path": "tool_calls",
        "type": "list",
        "description": "Ordered list of every tool call inside this "
                       "turn's batch. Each element carries "
                       "`tool_name`, `tool_input`, `tool_response`, "
                       "and `tool_use_id`. Authoring per-tool match "
                       "here is what PostToolUseFailure is for; "
                       "PostToolBatch policies operate on the whole "
                       "batch.",
    },
]

# Permission gate family ---------------------------------------------

_PERMISSION_REQUEST_FIELDS: list[FieldDescriptor] = [
    {
        "path": "tool_name",
        "type": "str",
        "description": "The tool CC is about to ask permission for.",
        "example": "Bash",
    },
    {
        "path": "tool_input",
        "type": "dict",
        "description": "The tool input dict CC will execute if "
                       "permission is granted.",
    },
    {
        "path": "permission_suggestions",
        "type": "list",
        "description": "Runtime-suggested permission strings (e.g. "
                       "`Bash(git push:*)`) the user can accept "
                       "verbatim. Useful for audit / policy override.",
    },
]

_PERMISSION_DENIED_FIELDS: list[FieldDescriptor] = [
    {
        "path": "tool_name",
        "type": "str",
        "description": "The tool whose permission was denied.",
        "example": "Bash",
    },
    {
        "path": "tool_input",
        "type": "dict",
        "description": "The tool input dict that was rejected.",
    },
    {
        "path": "tool_use_id",
        "type": "str",
        "description": "Unique id for the denied tool call.",
        "example": "toolu_01ABcdef0123",
    },
    {
        "path": "reason",
        "type": "str",
        "description": "Operator-supplied (or runtime-generated) "
                       "denial reason — the field most audit policies "
                       "want.",
        "example": "denied by user",
    },
]

# Content-flow extensions --------------------------------------------

_USER_PROMPT_EXPANSION_FIELDS: list[FieldDescriptor] = [
    {
        "path": "expansion_type",
        "type": "str",
        "description": "Source of the expansion — one of `\"command\"`, "
                       "`\"alias\"`, `\"file\"`, `\"argument\"`. Each "
                       "carries different sibling fields.",
        "example": "command",
    },
    {
        "path": "command_name",
        "type": "str",
        "description": "Slash-command name when expansion_type is "
                       "`\"command\"` (e.g. `compact`, `model`).",
        "example": "compact",
    },
    {
        "path": "command_args",
        "type": "str",
        "description": "Raw argument string passed to the slash "
                       "command (everything after the command name).",
    },
    {
        "path": "command_source",
        "type": "str",
        "description": "Where the command definition came from — "
                       "`\"builtin\"`, `\"user\"`, `\"project\"`.",
        "example": "builtin",
    },
    {
        "path": "prompt",
        "type": "str",
        "description": "The expanded prompt text the LLM will see "
                       "after expansion. Use for prompt-injection "
                       "screens on the post-expansion form.",
    },
]

_POST_COMPACT_FIELDS: list[FieldDescriptor] = [
    {
        "path": "trigger",
        "type": "str",
        "description": "Compaction trigger — `\"manual\"` when the "
                       "operator ran `/compact`, `\"auto\"` when the "
                       "runtime hit its compaction threshold.",
        "example": "auto",
    },
    {
        "path": "compact_summary",
        "type": "str",
        "description": "The summary the runtime distilled from the "
                       "compacted transcript and is about to splice "
                       "into the new context. The field most audit "
                       "policies want.",
    },
]

_ELICITATION_FIELDS: list[FieldDescriptor] = [
    {
        "path": "mcp_server_name",
        "type": "str",
        "description": "The MCP server that issued the elicitation "
                       "request.",
        "example": "court",
    },
    {
        "path": "message",
        "type": "str",
        "description": "Human-readable text the MCP server is asking "
                       "the user to respond to.",
    },
    {
        "path": "mode",
        "type": "str",
        "description": "Elicitation mode — `\"input\"` (free text), "
                       "`\"select\"`, `\"confirm\"`, etc.",
    },
    {
        "path": "url",
        "type": "str",
        "description": "Optional MCP-supplied URL for context (form "
                       "page, OAuth dialog, etc.).",
    },
    {
        "path": "elicitation_id",
        "type": "str",
        "description": "Opaque id correlating this Elicitation with "
                       "the matching ElicitationResult.",
    },
    {
        "path": "requested_schema",
        "type": "dict",
        "description": "JSON Schema the MCP server expects the user "
                       "response to validate against.",
    },
]

_ELICITATION_RESULT_FIELDS: list[FieldDescriptor] = [
    {
        "path": "mcp_server_name",
        "type": "str",
        "description": "The MCP server that issued the elicitation "
                       "the user just answered.",
        "example": "court",
    },
    {
        "path": "elicitation_id",
        "type": "str",
        "description": "Opaque id correlating with the matching "
                       "Elicitation event.",
    },
    {
        "path": "mode",
        "type": "str",
        "description": "Elicitation mode (mirrors the Elicitation "
                       "field).",
    },
    {
        "path": "action",
        "type": "str",
        "description": "What the user did — `\"accept\"`, "
                       "`\"decline\"`, or `\"cancel\"`.",
        "example": "accept",
    },
    {
        "path": "content",
        "type": "dict",
        "description": "User response payload (shape matches "
                       "Elicitation.requested_schema when action is "
                       "`\"accept\"`).",
    },
]

# Subagent / Stop boundary -------------------------------------------

_SUBAGENT_START_FIELDS: list[FieldDescriptor] = [
    {
        "path": "agent_id",
        "type": "str",
        "description": "Opaque id for the subagent CC is about to "
                       "spawn.",
        "example": "agent_01XYZ",
    },
    {
        "path": "agent_type",
        "type": "str",
        "description": "Subagent kind (e.g. `\"general-purpose\"`, "
                       "or the slug of a custom subagent declared in "
                       "`.claude/agents/`).",
        "example": "general-purpose",
    },
]

_STOP_FAILURE_FIELDS: list[FieldDescriptor] = [
    {
        "path": "error",
        "type": "str",
        "description": "Failure message captured from the Stop hook "
                       "chain (e.g. non-zero exit, timeout).",
        "example": "Stop hook exit code 1",
    },
    {
        "path": "error_details",
        "type": "str",
        "description": "Optional secondary detail string the runtime "
                       "produced.",
    },
    {
        "path": "last_assistant_message",
        "type": "str",
        "description": "The assistant's final message the failing "
                       "Stop hook was guarding. Useful for "
                       "post-mortem audit / replay.",
    },
]

# Lifecycle / observability surface ----------------------------------

_SETUP_FIELDS: list[FieldDescriptor] = [
    {
        "path": "trigger",
        "type": "str",
        "description": "What initiated the workspace setup — typically "
                       "`\"first_run\"`, `\"reset\"`, or a CLI flag "
                       "string.",
        "example": "first_run",
    },
]

_NOTIFICATION_FIELDS: list[FieldDescriptor] = [
    {
        "path": "message",
        "type": "str",
        "description": "Notification body text the runtime is about "
                       "to surface to the user.",
        "example": "Claude needs your input",
    },
    {
        "path": "title",
        "type": "str",
        "description": "Notification title (terminal bell label / "
                       "OS notification title).",
    },
    {
        "path": "notification_type",
        "type": "str",
        "description": "Notification kind — `\"idle\"`, "
                       "`\"permission\"`, `\"completed\"`, etc.",
        "example": "idle",
    },
]

_TEAMMATE_IDLE_FIELDS: list[FieldDescriptor] = [
    {
        "path": "teammate_name",
        "type": "str",
        "description": "Display name of the teammate agent that just "
                       "went idle.",
        "example": "Reviewer",
    },
    {
        "path": "team_name",
        "type": "str",
        "description": "Team this teammate belongs to.",
        "example": "review-loop",
    },
]

_TASK_CREATED_FIELDS: list[FieldDescriptor] = [
    {
        "path": "task_id",
        "type": "str",
        "description": "Opaque id for the Task-tool invocation.",
        "example": "task_01ABCDEF",
    },
    {
        "path": "task_subject",
        "type": "str",
        "description": "Short subject the parent agent gave the "
                       "subtask (one-line summary).",
        "example": "Audit the migration script for SQL injection",
    },
    {
        "path": "task_description",
        "type": "str",
        "description": "Full description of the subtask the parent "
                       "agent asked the teammate to perform.",
    },
    {
        "path": "teammate_name",
        "type": "str",
        "description": "Display name of the teammate the task was "
                       "dispatched to.",
    },
    {
        "path": "team_name",
        "type": "str",
        "description": "Team the dispatched teammate belongs to.",
    },
]

_TASK_COMPLETED_FIELDS: list[FieldDescriptor] = [
    {
        "path": "task_id",
        "type": "str",
        "description": "Opaque id for the completed Task-tool "
                       "invocation (correlates with TaskCreated).",
        "example": "task_01ABCDEF",
    },
    {
        "path": "task_subject",
        "type": "str",
        "description": "Short subject of the completed subtask.",
    },
    {
        "path": "task_description",
        "type": "str",
        "description": "Full description of the completed subtask.",
    },
    {
        "path": "teammate_name",
        "type": "str",
        "description": "Display name of the teammate that ran the "
                       "task.",
    },
    {
        "path": "team_name",
        "type": "str",
        "description": "Team the teammate belongs to.",
    },
]

_CONFIG_CHANGE_FIELDS: list[FieldDescriptor] = [
    {
        "path": "source",
        "type": "str",
        "description": "Which settings layer changed — "
                       "`\"userSettings\"`, `\"projectSettings\"`, "
                       "`\"localSettings\"`, `\"flagSettings\"`.",
        "example": "projectSettings",
    },
    {
        "path": "file_path",
        "type": "str",
        "description": "Absolute path to the settings.json the "
                       "runtime just reloaded.",
        "example": "/Users/me/project/.claude/settings.json",
    },
]

_WORKTREE_CREATE_FIELDS: list[FieldDescriptor] = [
    {
        "path": "name",
        "type": "str",
        "description": "Worktree slug the runtime created (e.g. "
                       "`fix/login-bug`). The hook stdout channel uses "
                       "`hookSpecificOutput.worktreePath` to return "
                       "the created path; `additionalContext` is NOT "
                       "honored here.",
        "example": "fix/login-bug",
    },
]

_WORKTREE_REMOVE_FIELDS: list[FieldDescriptor] = [
    {
        "path": "worktree_path",
        "type": "str",
        "description": "Absolute path of the worktree the runtime "
                       "just removed.",
        "example": "/Users/me/project-worktrees/fix-login-bug",
    },
]

_INSTRUCTIONS_LOADED_FIELDS: list[FieldDescriptor] = [
    {
        "path": "file_path",
        "type": "str",
        "description": "Absolute path of the instruction file the "
                       "runtime loaded (CLAUDE.md, AGENTS.md, an "
                       "imported `@…` file).",
        "example": "/Users/me/project/CLAUDE.md",
    },
    {
        "path": "memory_type",
        "type": "str",
        "description": "Memory layer — `\"user\"`, `\"project\"`, "
                       "`\"project, gitignored\"` (localSettings), "
                       "or `\"cli flag\"`.",
        "example": "project",
    },
    {
        "path": "load_reason",
        "type": "str",
        "description": "Why this file loaded — `\"startup\"`, "
                       "`\"reload\"`, `\"import\"`, `\"glob_match\"`.",
        "example": "startup",
    },
    {
        "path": "globs",
        "type": "list",
        "description": "Glob patterns that matched, when load_reason "
                       "is `\"glob_match\"`.",
    },
    {
        "path": "trigger_file_path",
        "type": "str",
        "description": "Path of the file that triggered an import "
                       "load (e.g. the parent CLAUDE.md that wrote "
                       "`@some/file.md`).",
    },
    {
        "path": "parent_file_path",
        "type": "str",
        "description": "Path of the parent instruction file when "
                       "load_reason is `\"import\"`.",
    },
]

_CWD_CHANGED_FIELDS: list[FieldDescriptor] = [
    {
        "path": "old_cwd",
        "type": "str",
        "description": "The previous working directory.",
        "example": "/Users/me/project",
    },
    {
        "path": "new_cwd",
        "type": "str",
        "description": "The new working directory CC just moved to.",
        "example": "/Users/me/project/subdir",
    },
]

_FILE_CHANGED_FIELDS: list[FieldDescriptor] = [
    {
        "path": "file_path",
        "type": "str",
        "description": "Absolute path of the file the watcher saw "
                       "change.",
        "example": "/Users/me/project/src/app.py",
    },
    {
        "path": "event",
        "type": "str",
        "description": "Filesystem event kind — `\"created\"`, "
                       "`\"modified\"`, `\"deleted\"`, `\"renamed\"`.",
        "example": "modified",
    },
]

_MESSAGE_DISPLAY_FIELDS: list[FieldDescriptor] = [
    {
        "path": "turn_id",
        "type": "str",
        "description": "Opaque id of the turn this delta belongs to.",
    },
    {
        "path": "message_id",
        "type": "str",
        "description": "Opaque id of the streaming message the delta "
                       "appends to.",
    },
    {
        "path": "index",
        "type": "int",
        "description": "Zero-based index of this delta within the "
                       "message.",
    },
    {
        "path": "final",
        "type": "bool",
        "description": "True iff this is the terminal delta of the "
                       "message (CC will not stream another delta for "
                       "this message after this one).",
    },
    {
        "path": "delta",
        "type": "str",
        "description": "The text chunk about to render to the user's "
                       "terminal. Display-only — overriding it via "
                       "`hookSpecificOutput.displayContent` only "
                       "changes the rendered string, not the stored "
                       "message or the model context.",
    },
]


def _tool_schema(event: str, *, tool_specific: list[FieldDescriptor]) -> PayloadSchema:
    return {
        "event": event,
        "matcher_class": "tool",
        "fields": [*_COMMON_TOOL_ENVELOPE, *tool_specific],
    }


# Sub-key inside the registry: per-tool override under (event, matcher_class).
# The top-level matcher_class key resolves to the generic-tool schema; tool-
# specific entries are keyed by the tool name as the third dimension. The
# helper `available_fields(event, matcher)` resolves both.
_TOOL_SPECIFIC_FIELDS_BY_NAME: dict[str, list[FieldDescriptor]] = {
    "Bash": _BASH_FIELDS,
    "WebFetch": _WEBFETCH_FIELDS,
    # Edit and Write carry disjoint input fields — see P2 in issue #1.
    # A wizard scoping to one must not advertise the other's fields.
    "Edit": _EDIT_FIELDS,
    "Write": _WRITE_FIELDS,
    "Read": _READ_FIELDS,
}


# Top-level registry. `available_fields()` is the right way to query —
# it resolves matcher strings (Bash, mcp__server__x, *, ...) to a
# matcher_class and then enriches with per-tool fields when the matcher
# names a specific known tool.
PAYLOAD_SCHEMAS_BY_EVENT: dict[str, dict[str, PayloadSchema]] = {
    "PreToolUse": {
        "tool": _tool_schema("PreToolUse", tool_specific=_GENERIC_TOOL_FIELDS),
    },
    "PostToolUse": {
        "tool": {
            "event": "PostToolUse",
            "matcher_class": "tool",
            "fields": [
                *_COMMON_TOOL_ENVELOPE,
                *_GENERIC_TOOL_FIELDS,
                *_TOOL_RESPONSE_FIELDS,
            ],
        },
    },
    "UserPromptSubmit": {
        "no_tool": {
            "event": "UserPromptSubmit",
            "matcher_class": "no_tool",
            "fields": _USER_PROMPT_SUBMIT_FIELDS,
        },
    },
    "Stop": {
        "final": {
            "event": "Stop",
            "matcher_class": "final",
            "fields": _STOP_FIELDS,
        },
    },
    "SubagentStop": {
        "final": {
            "event": "SubagentStop",
            "matcher_class": "final",
            "fields": _STOP_FIELDS,
        },
    },
    "SessionStart": {
        "no_tool": {
            "event": "SessionStart",
            "matcher_class": "no_tool",
            "fields": _SESSION_START_FIELDS,
        },
    },
    "SessionEnd": {
        "no_tool": {
            "event": "SessionEnd",
            "matcher_class": "no_tool",
            "fields": _SESSION_END_FIELDS,
        },
    },
    "PreCompact": {
        "no_tool": {
            "event": "PreCompact",
            "matcher_class": "no_tool",
            "fields": _PRE_COMPACT_FIELDS,
        },
    },
    # ── D79 — promoted from `_UNVERIFIED_EVENTS` ─────────────────────
    "PostToolUseFailure": {
        "tool": {
            "event": "PostToolUseFailure",
            "matcher_class": "tool",
            "fields": [
                *_COMMON_TOOL_ENVELOPE,
                *_POST_TOOL_USE_FAILURE_FIELDS,
            ],
        },
    },
    "PostToolBatch": {
        "tool": {
            "event": "PostToolBatch",
            "matcher_class": "tool",
            "fields": [
                {
                    "path": "session_id",
                    "type": "str",
                    "description": "Opaque CC session identifier.",
                },
                *_POST_TOOL_BATCH_FIELDS,
            ],
        },
    },
    "PermissionRequest": {
        "tool": {
            "event": "PermissionRequest",
            "matcher_class": "tool",
            "fields": [
                *_COMMON_TOOL_ENVELOPE,
                *_PERMISSION_REQUEST_FIELDS,
            ],
        },
    },
    "PermissionDenied": {
        "tool": {
            "event": "PermissionDenied",
            "matcher_class": "tool",
            "fields": [
                *_COMMON_TOOL_ENVELOPE,
                *_PERMISSION_DENIED_FIELDS,
            ],
        },
    },
    "UserPromptExpansion": {
        "no_tool": {
            "event": "UserPromptExpansion",
            "matcher_class": "no_tool",
            "fields": _USER_PROMPT_EXPANSION_FIELDS,
        },
    },
    "PostCompact": {
        "no_tool": {
            "event": "PostCompact",
            "matcher_class": "no_tool",
            "fields": _POST_COMPACT_FIELDS,
        },
    },
    "Elicitation": {
        "no_tool": {
            "event": "Elicitation",
            "matcher_class": "no_tool",
            "fields": _ELICITATION_FIELDS,
        },
    },
    "ElicitationResult": {
        "no_tool": {
            "event": "ElicitationResult",
            "matcher_class": "no_tool",
            "fields": _ELICITATION_RESULT_FIELDS,
        },
    },
    "SubagentStart": {
        "no_tool": {
            "event": "SubagentStart",
            "matcher_class": "no_tool",
            "fields": _SUBAGENT_START_FIELDS,
        },
    },
    "StopFailure": {
        "final": {
            "event": "StopFailure",
            "matcher_class": "final",
            "fields": _STOP_FAILURE_FIELDS,
        },
    },
    "Setup": {
        "no_tool": {
            "event": "Setup",
            "matcher_class": "no_tool",
            "fields": _SETUP_FIELDS,
        },
    },
    "Notification": {
        "no_tool": {
            "event": "Notification",
            "matcher_class": "no_tool",
            "fields": _NOTIFICATION_FIELDS,
        },
    },
    "TeammateIdle": {
        "no_tool": {
            "event": "TeammateIdle",
            "matcher_class": "no_tool",
            "fields": _TEAMMATE_IDLE_FIELDS,
        },
    },
    "TaskCreated": {
        "no_tool": {
            "event": "TaskCreated",
            "matcher_class": "no_tool",
            "fields": _TASK_CREATED_FIELDS,
        },
    },
    "TaskCompleted": {
        "no_tool": {
            "event": "TaskCompleted",
            "matcher_class": "no_tool",
            "fields": _TASK_COMPLETED_FIELDS,
        },
    },
    "ConfigChange": {
        "no_tool": {
            "event": "ConfigChange",
            "matcher_class": "no_tool",
            "fields": _CONFIG_CHANGE_FIELDS,
        },
    },
    "WorktreeCreate": {
        "no_tool": {
            "event": "WorktreeCreate",
            "matcher_class": "no_tool",
            "fields": _WORKTREE_CREATE_FIELDS,
        },
    },
    "WorktreeRemove": {
        "no_tool": {
            "event": "WorktreeRemove",
            "matcher_class": "no_tool",
            "fields": _WORKTREE_REMOVE_FIELDS,
        },
    },
    "InstructionsLoaded": {
        "no_tool": {
            "event": "InstructionsLoaded",
            "matcher_class": "no_tool",
            "fields": _INSTRUCTIONS_LOADED_FIELDS,
        },
    },
    "CwdChanged": {
        "no_tool": {
            "event": "CwdChanged",
            "matcher_class": "no_tool",
            "fields": _CWD_CHANGED_FIELDS,
        },
    },
    "FileChanged": {
        "no_tool": {
            "event": "FileChanged",
            "matcher_class": "no_tool",
            "fields": _FILE_CHANGED_FIELDS,
        },
    },
    "MessageDisplay": {
        "no_tool": {
            "event": "MessageDisplay",
            "matcher_class": "no_tool",
            "fields": _MESSAGE_DISPLAY_FIELDS,
        },
    },
}


def _resolve_matcher_class(matcher: str | None) -> MatcherClassLiteral:
    """Coarse the policy/matrix.py matcher classes to the schema menu's
    three classes.

    Empty / None matcher (no tool context implied) → `no_tool`. Any
    tool-shaped matcher (builtin name, mcp__…, alternation, "*") →
    `tool`. The caller's event ultimately decides which schema slot
    we look at; this helper is only used to keep the rendering layer
    honest.
    """
    if not matcher:
        return "no_tool"
    m = matcher.strip()
    if not m:
        return "no_tool"
    # Star, alternation, mcp, builtin name → tool-context.
    return "tool"


def _enrich_with_tool_specific(
    base: PayloadSchema, matcher: str
) -> PayloadSchema:
    """If the matcher names a SPECIFIC known tool, splice that tool's
    fields in *replacing* the generic `tool_input` entry.

    A wildcard / alternation / mcp matcher stays on the generic entry —
    we don't know which specific tool the runtime will see and shouldn't
    advertise fields the gate might not get.
    """
    name = matcher.strip()
    specific = _TOOL_SPECIFIC_FIELDS_BY_NAME.get(name)
    if specific is None:
        return base
    # Drop the catch-all `tool_input` row, replace with tool-specific
    # paths. Common envelope rows are preserved verbatim.
    new_fields: list[FieldDescriptor] = []
    for f in base["fields"]:
        if f["path"] == "tool_input":
            continue
        new_fields.append(f)
    return {
        "event": base["event"],
        "matcher_class": base["matcher_class"],
        "fields": [*new_fields, *specific],
    }


def available_fields(event: str, matcher: str | None = None) -> list[FieldDescriptor]:
    """Resolve (event, matcher) to a flat list of field descriptors.

    Lookup rules:
      1. Find the schema bucket for `event`.
      2. Coarse the matcher to one of `tool` / `no_tool` / `final`.
         Events that have only one bucket (Stop → `final`) ignore the
         matcher's class.
      3. For a tool-context event with a SPECIFIC tool matcher (`Bash`,
         `WebFetch`, etc.), splice the tool's input fields in.

    Returns an empty list when no schema is known for the event — the
    caller (REST endpoint / wizard chip renderer) decides whether to
    show "no suggestions" or skip the chip row entirely.
    """
    bucket = PAYLOAD_SCHEMAS_BY_EVENT.get(event)
    if bucket is None:
        return []
    cls = _resolve_matcher_class(matcher)
    schema = bucket.get(cls)
    if schema is None:
        # Some events have only one slot (Stop = final). Fall back to
        # whatever the event has.
        schema = next(iter(bucket.values()))
    if cls == "tool" and matcher:
        schema = _enrich_with_tool_specific(schema, matcher)
    return _with_display_label(_with_sh_hints(list(schema["fields"])))


def all_schemas() -> list[PayloadSchema]:
    """Flatten the registry into a list. Used by the GET endpoint to
    return everything in one response so the wizard can client-cache."""
    out: list[PayloadSchema] = []
    for bucket in PAYLOAD_SCHEMAS_BY_EVENT.values():
        for schema in bucket.values():
            out.append({
                "event": schema["event"],
                "matcher_class": schema["matcher_class"],
                "fields": _with_display_label(
                    _with_sh_hints(list(schema["fields"]))
                ),
            })
    return out


# ── JSON → RDF lift (P0 #1) + SHACL lint helpers (P0 #3, P1 #4) ────

def _flatten_payload(
    payload: dict, prefix: str = "",
) -> dict[str, object]:
    """Flatten a CC stdin JSON payload into the canonical dotted keys
    the chip row advertises.

    Example: `{"tool_input": {"command": "ls"}}` →
             `{"tool_input.command": "ls", "tool_input": {"command": "ls"}}`.
    Both the leaf scalar and the nested dict are kept so authors can
    target either form (a SHACL `sh:datatype rdf:JSON` shape against the
    parent dict still works).
    """
    flat: dict[str, object] = {}
    if not isinstance(payload, dict):
        return flat
    for k, v in payload.items():
        key = f"{prefix}{k}" if prefix else str(k)
        flat[key] = v
        if isinstance(v, dict):
            flat.update(_flatten_payload(v, prefix=f"{key}."))
    return flat


# ── D82c: {marker} substitution for inline llm_critic criteria ─────
#
# Authors compose llm_critic criteria with a curly-brace marker syntax
# borrowed from f-strings: `{tool_response.output}` inside the criterion
# is replaced with the actual value of that path inside the CC stdin
# payload BEFORE the prompt reaches the LLM. The marker rules:
#
#   - Path syntax: dotted lookups (`a.b.c`), same as the SHACL chip row.
#   - Value typing: scalars stringify, dict / list values serialize to
#     compact JSON so the LLM sees a readable form (Python repr would
#     leak `True/False/None` rather than `true/false/null` and surface
#     `'` instead of `"` for strings).
#   - Missing path: substitutes `(no <field_path> available)` so the
#     surrounding prose stays grammatical. The runtime never leaves
#     literal `{...}` braces in the prompt because the LLM would treat
#     them as Python-style format placeholders ("forgot to render") and
#     hallucinate the missing value.
#   - Unbalanced / malformed markers: left untouched. We only substitute
#     a span when there is a matching `}` AND the captured key resolves
#     to a valid identifier-style path.
#
# Reused by /verify_inline (cloud llm_critic dispatch) and by any future
# in-process critic runner (e.g. a `magi-cp gate` local llm_critic mode).

# D82c fix: tighten the regex to a strict dotted-identifier chain so
# `{foo.}` / `{a..b}` / `{.x}` no longer match (the previous loose form
# admitted trailing-dot and double-dot paths whose `split('.')` yields
# empty segments that miss the dict walk and render the noisy
# `(no foo. available)` placeholder into the operator-facing prompt).
_MARKER_RX = re.compile(
    r"\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\}"
)

# D82c fix: `_MISSING` is the sentinel `_resolve_dotted_path` returns
# when any dotted segment is absent. Declared BEFORE the resolver so the
# read order matches the call order (the prior code put `_MISSING` after
# the function — runtime tolerated it because name resolution is late,
# but a future default-argument move or early-eval import cycle would
# break it).
_MISSING = object()

# D82c fix: per-marker substitution cap. An author who writes
# `Does {tool_input} look hostile?` on a PostToolUse Write/Edit policy
# would otherwise inline the entire (potentially megabytes) file body
# into the CRITERION block — the existing 4000-char PAYLOAD cap was
# designed to protect against this, but `interpolate_payload_markers`
# wrote into CRITERION which bypassed it. Cap each substituted value
# at 1000 chars with an ellipsis suffix so the prompt stays bounded.
_MAX_MARKER_VALUE_CHARS = 1000


def _resolve_dotted_path(payload: dict | None, path: str) -> object | None:
    """Walk a dotted path through a nested dict. Returns the resolved
    value, or ``None`` when any segment is missing (callers distinguish
    "missing" from "stored value is None" via the dedicated `_MISSING`
    sentinel above)."""
    if not isinstance(payload, dict):
        return _MISSING
    cur: object = payload
    for seg in path.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return _MISSING
        cur = cur[seg]
    return cur


def _format_value_for_prompt(val: object) -> str:
    """Render a payload value as a human-readable string for the LLM
    prompt. Dicts / lists JSON-serialize so the model sees `"command"`
    not `'command'`; bools render `True/False` (English, not lowercase
    json) because the surrounding prose is English.

    D82c fix: each substituted value is capped at
    ``_MAX_MARKER_VALUE_CHARS`` characters with a `…<truncated>` suffix
    so a `{tool_input.content}` marker over a megabyte file body cannot
    blow past the LLM provider's token limits.
    """
    import json as _json

    if isinstance(val, (dict, list)):
        try:
            rendered = _json.dumps(val, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            rendered = str(val)
    elif isinstance(val, str):
        rendered = val
    else:
        rendered = str(val)
    if len(rendered) > _MAX_MARKER_VALUE_CHARS:
        return rendered[:_MAX_MARKER_VALUE_CHARS] + "…<truncated>"
    return rendered


def interpolate_payload_markers(
    text: str, payload: dict | None,
) -> str:
    """Replace `{field.path}` markers in `text` with values lifted from
    the CC stdin `payload`.

    Used by the inline `llm_critic` evaluator so an author-written
    criterion like

        "Does {tool_response.output} contain PII?"

    becomes

        "Does <the actual tool output text> contain PII?"

    BEFORE the prompt reaches the LLM. Missing paths render as
    `(no <field_path> available)` so the prose stays grammatical and
    the model isn't left holding literal curly braces (which it would
    interpret as a forgotten format placeholder and try to "render").

    Unmatched / malformed markers (no closing brace, non-identifier
    inside) are left exactly as authored; the regex only consumes
    spans that look like real dotted-path markers."""
    if not text:
        return text

    def _sub(m: "re.Match[str]") -> str:
        path = m.group(1)
        val = _resolve_dotted_path(payload, path)
        if val is _MISSING:
            return f"(no {path} available)"
        return _format_value_for_prompt(val)

    return _MARKER_RX.sub(_sub, text)


def lift_payload_to_data_graph(
    payload: dict, event: str, matcher: str | None = None,
):
    """Materialize a tiny RDF graph from the CC stdin JSON so SHACL
    shapes targeting chip-picked paths actually select a focus node.

    Each leaf field from `available_fields(event, matcher)` that is
    present in the JSON becomes a triple
    `<MAGI_HOOK_SUBJECT> magi:<path> "<value>"^^<datatype>`. Dicts /
    lists land as `rdf:JSON` literals (JSON-encoded string). Missing
    fields are silently omitted — the runtime contract is "what the gate
    actually delivers."

    Returns an `rdflib.Graph` ready to hand to pyshacl. Raises
    `ImportError` when rdflib is not installed; callers should fall back
    to the import-error message they already produce for that case.
    """
    try:
        import rdflib  # type: ignore[import-not-found]
    except ImportError as e:
        raise ImportError("rdflib is required for SHACL data lift") from e
    import json as _json

    g = rdflib.Graph()
    g.bind("magi", MAGI_HOOK_NS)
    g.bind("xsd", "http://www.w3.org/2001/XMLSchema#")

    subj = rdflib.URIRef(MAGI_HOOK_SUBJECT)
    # Always mark the hook instance with rdf:type so `sh:targetClass`
    # shapes anchored on `magi:Hook` get one focus node per firing.
    g.add((subj, rdflib.RDF.type, rdflib.URIRef(MAGI_HOOK_NS + "Hook")))
    g.add((subj, rdflib.URIRef(MAGI_HOOK_NS + "event"),
           rdflib.Literal(event)))
    if matcher:
        g.add((subj, rdflib.URIRef(MAGI_HOOK_NS + "matcher"),
               rdflib.Literal(matcher)))

    flat = _flatten_payload(payload)
    # We pick from `available_fields(event, matcher)` so policies stay
    # on the documented contract — a shape targeting a JSON key the menu
    # doesn't advertise still finds zero focus nodes, which the runtime
    # treats as deny (closing the silent fail-open).
    descriptors = available_fields(event, matcher)
    for f in descriptors:
        path = f.get("path")
        if not path or path not in flat:
            continue
        val = flat[path]
        pred = rdflib.URIRef(MAGI_HOOK_NS + path)
        if isinstance(val, bool):
            g.add((subj, pred, rdflib.Literal(val)))
        elif isinstance(val, int):
            g.add((subj, pred, rdflib.Literal(val)))
        elif isinstance(val, str):
            g.add((subj, pred, rdflib.Literal(val)))
        else:
            # dict / list → JSON literal so SHACL pattern constraints
            # still work on the serialized form.
            g.add((
                subj, pred,
                rdflib.Literal(_json.dumps(val, ensure_ascii=False, sort_keys=True)),
            ))
    return g


# ── SHACL target lint (P0 #3, P1 #4) ──────────────────────────────

def extract_targets(shape_ttl: str) -> dict[str, list[str]]:
    """Pull every SHACL anchoring literal out of a Turtle shape graph.

    Returns a dict with three sections:
      - "targetNode":  list of subject IRI local names referenced
      - "targetClass": list of class IRI local names referenced
      - "path":        list of property IRI local names referenced

    Only local names under the canonical `MAGI_HOOK_NS` are returned —
    a shape that anchors on some other namespace is out-of-contract and
    falls through (callers may surface a separate warning if they care).
    """
    try:
        import rdflib  # type: ignore[import-not-found]
    except ImportError:
        # Without rdflib we can't parse Turtle; degrade to "no targets"
        # so callers fall through gracefully instead of blowing up the
        # whole policy save path.
        return {"targetNode": [], "targetClass": [], "path": []}
    SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    g = rdflib.Graph()
    g.parse(data=shape_ttl, format="turtle")

    out: dict[str, list[str]] = {
        "targetNode": [], "targetClass": [], "path": [],
    }

    def _local(node) -> str | None:
        try:
            iri = str(node)
        except Exception:
            return None
        if iri.startswith(MAGI_HOOK_NS):
            return iri[len(MAGI_HOOK_NS):]
        return None

    for _, _, o in g.triples((None, SH.targetNode, None)):
        ln = _local(o)
        if ln is not None and ln not in out["targetNode"]:
            out["targetNode"].append(ln)
    for _, _, o in g.triples((None, SH.targetClass, None)):
        ln = _local(o)
        if ln is not None and ln not in out["targetClass"]:
            out["targetClass"].append(ln)
    for _, _, o in g.triples((None, SH.path, None)):
        ln = _local(o)
        if ln is not None and ln not in out["path"]:
            out["path"].append(ln)
    return out


def lint_shacl_targets(
    shape_ttl: str, event: str, matcher: str | None = None,
) -> list[str]:
    """Cross-reference a SHACL shape's anchoring literals against the
    payload schema menu for the given (event, matcher).

    Returns a list of human-readable issue strings — empty list means
    every anchor is on a path the runtime actually delivers. Used by:
      - `Policy.validate()` (when MAGI_CP_STRICT_SHACL_TARGETS=1, raises)
      - the wizard's saveAdvanced (renders as a banner warning)
      - the REST PUT /policies handler (always warns, never blocks
        unless strict mode is set — see app.py)

    Note: SHACL paths under `sh:path` that resolve to the canonical
    namespace ARE the predicates `lift_payload_to_data_graph` produces,
    so a mismatched path here means runtime would produce zero focus
    nodes and the shape would be vacuously satisfied — exactly the
    P7-prevented failure mode.
    """
    issues: list[str] = []
    try:
        targets = extract_targets(shape_ttl)
    except Exception as e:
        return [f"shape parse error: {type(e).__name__}: {str(e)[:120]}"]

    known_paths = {f["path"] for f in available_fields(event, matcher) if "path" in f}
    known_classes = {"Hook"}  # the only class the runtime materializes

    def _suggest(bad: str, pool: set[str]) -> str:
        import difflib
        m = difflib.get_close_matches(bad, list(pool), n=1, cutoff=0.5)
        return f"; did you mean '{m[0]}'?" if m else ""

    for kind in ("targetNode", "path"):
        for ln in targets[kind]:
            # `targetNode "<path>"` shapes anchor on the literal path-
            # local-name (used in `sh:targetSubjectsOf`-style framing
            # too); both treat the local name as a field path.
            if ln in known_paths:
                continue
            # The canonical hook subject IRI itself is allowed; it's how
            # `sh:targetNode` selects the single hook node every time.
            if kind == "targetNode" and ln == "__hook__":
                continue
            hint = _suggest(ln, known_paths)
            issues.append(
                f"sh:{kind} magi:{ln} is not a field the runtime delivers "
                f"for ({event}, {matcher or '*'}){hint}"
            )
    for ln in targets["targetClass"]:
        if ln in known_classes:
            continue
        issues.append(
            f"sh:targetClass magi:{ln} — only 'Hook' is materialized at "
            f"runtime; this shape will be vacuously satisfied"
        )
    return issues
