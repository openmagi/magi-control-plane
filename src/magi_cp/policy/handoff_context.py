"""D57g — handoff to conversational compose from any authoring screen.

When the operator clicks "Continue in conversation" from the guided
wizard (any step), the raw IR editor, or even Step 6 review, the
dashboard serialises the in-progress state and seeds the conversational
chat with a single assistant turn that summarises "so far you've picked
…" in plain language, plus the canonical follow-up question set for
whatever is still missing.

`build_handoff_turn` is the pure entry point. It accepts:

  wizard_state  dict of the wizard's URL-state shape (lifecycle slug,
                toolScope, conditionKind, action, id, description, plus
                the per-condition / per-action body fields the wizard
                ferries on the URL). Unknown / malformed values are
                silently dropped — the merged draft only inherits what
                survived per-field validation.
  draft_ir      already-shaped policy IR dict (the raw editor's view).
                Sanitised through `_sanitize_draft_so_far` so a client
                cannot smuggle `gate_binary` or other RCE-shaped keys.

It returns the same wire shape `step_compile` does so the conversational
client can mount the response verbatim as the first assistant turn:

  {
    "assistant_message": str,
    "draft":             dict | None,
    "missing_fields":    list[str],
    "questions":         list[Question],
    "needs_more":        bool,
    "ready_to_save":     bool,
  }

Security boundary:
  - The wizard URL state is OPERATOR-supplied (came from the same
    server-rendered dashboard) but a malicious link can still smuggle
    arbitrary values via the seed. We reuse the same per-field
    validators the answer-merge path uses (`_apply_answer_to_draft`)
    so any value that survives the merge already passed the canonical
    allowlist.
  - `draft_ir` rides through `_sanitize_draft_so_far` so a hand-crafted
    POST body cannot pre-seed `gate_binary` / `on_signature_invalid`
    / `sentinel_re` / `type`.

The serialiser is intentionally OFFLINE — no LLM call is made. The
conversational compose's first real turn (the operator's first reply)
runs through `step_compile` as usual, which then drives the chat.
"""
from __future__ import annotations

import copy
import inspect
import re as _re_check
from typing import Any

from .nl_compiler_interactive import (
    _LIFECYCLE_TO_EVENT,
    _ON_MISSING_VALUES,
    _REQUIRES_KINDS,
    _RC_SCRIPT_ID_RE,
    _RUN_COMMAND_RUNTIMES,
    _MAX_RUN_COMMAND_ARGS,
    _MAX_RUN_COMMAND_ARG_LEN,
    _MAX_RUN_COMMAND_INLINE_LEN,
    _MAX_RUN_COMMAND_TIMEOUT_MS,
    _MIN_RUN_COMMAND_TIMEOUT_MS,
    _apply_answer_to_draft,
    _detect_korean,
    _draft_passes_ir_validator,
    _missing_fields_for_draft,
    _question_for_field,
    _question_for_requires_body,
    _sanitize_draft_so_far,
    _to_plain_language,
    MAX_QUESTIONS_PER_TURN,
    Question,
)


# D66 follow-up: the FULL wider lifecycle → event table that page.tsx
# LIFECYCLE_TO_EVENT exposes via RUN_COMMAND_LEGAL_BY_LIFECYCLE. The
# run_command archetype is uniformly legal across the D58 surface,
# so the handoff serializer must round-trip every slug that page.tsx
# legalises for run_command. Mirrors web/app/(console)/policies/new/
# page.tsx LIFECYCLE_TO_EVENT 1:1 — adding a new event there MUST add
# a row here.
_RUN_COMMAND_LIFECYCLE_TO_EVENT: dict[str, str] = {
    "before_tool_use":      "PreToolUse",
    "after_tool_use":       "PostToolUse",
    "pre_final":            "Stop",
    "subagent_stop":        "SubagentStop",
    "user_prompt":          "UserPromptSubmit",
    "pre_compact":          "PreCompact",
    "session_start":        "SessionStart",
    "session_end":          "SessionEnd",
    # D58
    "post_tool_use_failure": "PostToolUseFailure",
    "post_tool_batch":       "PostToolBatch",
    "permission_request":    "PermissionRequest",
    "permission_denied":     "PermissionDenied",
    "user_prompt_expansion": "UserPromptExpansion",
    "post_compact":          "PostCompact",
    "elicitation":           "Elicitation",
    "elicitation_result":    "ElicitationResult",
    "subagent_start":        "SubagentStart",
    "stop_failure":          "StopFailure",
    "setup":                 "Setup",
    "notification":          "Notification",
    "teammate_idle":         "TeammateIdle",
    "task_created":          "TaskCreated",
    "task_completed":        "TaskCompleted",
    "config_change":         "ConfigChange",
    "worktree_create":       "WorktreeCreate",
    "worktree_remove":       "WorktreeRemove",
    "instructions_loaded":   "InstructionsLoaded",
    "cwd_changed":           "CwdChanged",
    "file_changed":          "FileChanged",
    "message_display":       "MessageDisplay",
}


# Lifecycle slug → human label for the assistant summary line. Mirrors
# the dashboard's LIFECYCLE_LABEL_* tables in page.tsx but only for the
# three lifecycle buckets the conversational compiler authors over
# (PreToolUse / PostToolUse / Stop). The runtime fires hooks the wizard
# allows authoring on a broader 30-event surface; the conversational
# wire shape collapses these onto the 3-bucket vocabulary the LLM
# already speaks.
_LIFECYCLE_LABEL_KO: dict[str, str] = {
    "before_tool_use": "도구 실행 전",
    "after_tool_use":  "도구 실행 후",
    "pre_final":       "최종 응답 직전",
}
_LIFECYCLE_LABEL_EN: dict[str, str] = {
    "before_tool_use": "before a tool runs",
    "after_tool_use":  "after a tool runs",
    "pre_final":       "just before the final answer",
}

_ACTION_LABEL_KO: dict[str, str] = {
    "block": "차단",
    "ask":   "사용자 승인 요청",
    "audit": "원장에 기록",
}
_ACTION_LABEL_EN: dict[str, str] = {
    "block": "block",
    "ask":   "ask a human",
    "audit": "record to the ledger",
}

_REQUIRES_KIND_LABEL_KO: dict[str, str] = {
    "regex":      "응답에서 패턴 확인",
    "llm_critic": "AI 판단",
    "shacl":      "구조 규칙 확인",
    "step":       "기존 검증기 사용",
}
_REQUIRES_KIND_LABEL_EN: dict[str, str] = {
    "regex":      "a pattern in the response",
    "llm_critic": "an AI judge",
    "shacl":      "a structured rule",
    "step":       "an existing verifier",
}


# Plain-language labels for the wizard-only action archetypes that the
# conversational vocabulary cannot model. These are rendered into the
# assistant message INSTEAD of the raw enum slug so the
# project-wide "NEVER expose internal terms" rule survives the
# wizard → chat handoff. See `_summarize_so_far`.
_ACTION_DROPPED_LABEL_KO: dict[str, str] = {
    "inject_context": "맥락 주입",
    "input_rewrite":  "입력 다시 쓰기",
    "strip":          "응답에서 제거",
    # D66: `run_command` USED to live here as a "wizard-only" archetype
    # that the conversational vocabulary could not store. D66 widened
    # the serializer so a wizard-built RunCommandPolicy now ROUND-TRIPS
    # through the seed (lifecycle + matcher + id + command + runtime +
    # args + timeout_ms + fail_closed + script_id). The conversational
    # compiler in turn already understands `type: "run_command"` (D65)
    # and the IrDraftPane already renders the run_command body slot
    # (D63 review). So `run_command` is intentionally absent from this
    # table — it is no longer a dropped archetype.
}
_ACTION_DROPPED_LABEL_EN: dict[str, str] = {
    "inject_context": "context injection",
    "input_rewrite":  "input rewriting",
    "strip":          "stripping the output",
}

# Plain-language labels for wizard-only ConditionKind values that the
# conversational vocabulary cannot model. Anything not in the table
# falls back to a generic "that check" / "이 조건" phrasing so a
# future ConditionKind or a hand-crafted unknown value cannot leak its
# raw enum to the rendered chat.
_COND_DROPPED_LABEL_KO: dict[str, str] = {
    "fetch_domain":     "도메인 규칙",
    "domain_allowlist": "허용 도메인 목록",
    "evidence_ref":     "기존 검증기 참조",
}
_COND_DROPPED_LABEL_EN: dict[str, str] = {
    "fetch_domain":     "a domain rule",
    "domain_allowlist": "an allowed-domain list",
    "evidence_ref":     "an existing-checker reference",
}
_COND_DROPPED_FALLBACK_KO: str = "이 조건"
_COND_DROPPED_FALLBACK_EN: str = "that check"

# Plain-language labels for the wider D58 lifecycle vocabulary that the
# conversational vocabulary cannot model (anything outside the
# 3-bucket `_LIFECYCLE_TO_EVENT`). Anything not in the table falls
# back to a generic phrasing so a future lifecycle slug cannot leak
# its raw token. See `_summarize_so_far` and `_draft_from_wizard_state`.
_LIFECYCLE_DROPPED_LABEL_KO: dict[str, str] = {
    "permission_request": "권한 요청 시점",
    "session_start":      "세션 시작 시점",
    "session_end":        "세션 종료 시점",
    "subagent_start":     "서브에이전트 시작 시점",
    "subagent_stop":      "서브에이전트 종료 시점",
    "worktree_create":    "워크트리 생성 시점",
    "worktree_remove":    "워크트리 제거 시점",
    # The wizard lifecycle slug for the prompt-submit hook is
    # `user_prompt` (see page.tsx LIFECYCLE_TO_EVENT). The old
    # `user_prompt_submit` key was unreachable — it was the synthetic
    # reverse-map slug, not a real wizard lifecycle id. Match the
    # page.tsx slug set 1:1 here so this table can be reached.
    "user_prompt":        "사용자 입력 제출 시점",
    "pre_compact":        "메모리 정리 직전",
    "stop":               "에이전트 종료 시점",
    "notification":       "알림 시점",
    "hook_call":          "후크 호출 시점",
}
_LIFECYCLE_DROPPED_LABEL_EN: dict[str, str] = {
    "permission_request": "the permission-request moment",
    "session_start":      "the session-start moment",
    "session_end":        "the session-end moment",
    "subagent_start":     "the subagent-start moment",
    "subagent_stop":      "the subagent-stop moment",
    "worktree_create":    "the worktree-create moment",
    "worktree_remove":    "the worktree-remove moment",
    # See KO note above: `user_prompt` is the real wizard slug;
    # `user_prompt_submit` was synthetic and unreachable here.
    "user_prompt":        "the prompt-submit moment",
    "pre_compact":        "the pre-compact moment",
    "stop":               "the agent-stop moment",
    "notification":       "the notification moment",
    "hook_call":          "the hook-call moment",
}
_LIFECYCLE_DROPPED_FALLBACK_KO: str = "그 발동 시점"
_LIFECYCLE_DROPPED_FALLBACK_EN: str = "that timing"


# Maximum bytes we accept on the inbound dicts. Both `wizard_state` and
# `draft_ir` are user-controlled at the route boundary; the route's
# pydantic model enforces a similar bound but the library guard keeps
# direct callers honest.
_MAX_STATE_BYTES = 16_000


class HandoffContextError(ValueError):
    """Caller-facing validation failure. Maps to HTTP 422 at the route."""


# Tool-context lifecycles where a tool name (matcher) makes sense.
# Everything else collapses to wildcard "*".
_TOOL_CONTEXT_LIFECYCLES: frozenset[str] = frozenset({
    "before_tool_use", "after_tool_use",
})


# D66: wizard URL field names that ferry the in-progress
# RunCommandPolicy state (see Step4bRunCommandFields). These are the
# only run_command-shaped keys the serializer reads from `wizard_state`;
# anything outside this set is silently ignored so a malicious client
# cannot smuggle a fictional `runCommandFoo` field through.
_RUN_COMMAND_WIZARD_KEYS: frozenset[str] = frozenset({
    "runCommandMode", "runCommandRuntime", "runCommandBody",
    "runCommandScriptId", "runCommandScriptName",
    "runCommandArgs", "runCommandTimeoutMs", "runCommandFailClosed",
})


def _project_run_command_state(
    state: dict[str, Any], draft: dict[str, Any],
) -> None:
    """Write the run_command archetype fields from `state` into `draft`.

    Mirrors the per-field allowlists `_sanitize_draft_so_far` applies to
    the raw editor's run_command passthrough (same caps, same regex). A
    value that survives this projection is canonical and safe to feed
    back through `policy_from_dict` for the ready_to_save decision.

    Notes on what we DO and DO NOT seed:

    * `type: "run_command"` is always written — this is the archetype
      discriminator the rest of the conversational compiler dispatches
      on (`_is_run_command_draft`). Without this the draft would look
      like an evidence-archetype draft to `_missing_fields_for_draft`
      and ask for `requires` / `on_missing` instead of asking for the
      missing command body.
    * Inline `command` and attached `script_path` (alias `script_id` on
      the wire) are mutually exclusive per `RunCommandPolicy.validate`.
      The wizard's `runCommandMode` URL key picks which lane is live;
      when the mode is "inline" we drop any half-typed script id, and
      vice versa, so the merged draft never carries both lanes filled
      (which would 422 in `policy_from_dict`).
    * `script_path` is gated to the 64-hex sha256 shape that
      `RunCommandPolicy.validate` accepts. A half-typed id (e.g.
      "abc123") would be dropped here; the conversational follow-up
      then re-asks via the `requires_body` question slot.
    * `args` parses the wizard's raw CSV (`runCommandArgs`) the same
      way the wizard server action does. Per-arg length and total-arg
      count caps mirror the IR.
    * `timeout_ms` clamps to `[_MIN_RUN_COMMAND_TIMEOUT_MS,
      _MAX_RUN_COMMAND_TIMEOUT_MS]`; anything outside the range is
      dropped (NOT clamped) so a malicious client cannot smuggle a
      sub-tick value past the IR's range check.
    * `fail_closed` is the checkbox state — "true"/"false" strings on
      the URL, coerced to bool here.

    The operator-facing `runCommandScriptName` label is stashed on a
    dedicated `_script_name` key. The IR does NOT persist it (only the
    hash id matters at gate time) but the IrDraftPane can render it as
    a friendly affordance next to the bare hash. `_script_name` is not
    part of `_sanitize_draft_so_far`'s run_command allowlist
    (`_DRAFT_TOP_KEYS | _RUN_COMMAND_TOP_KEYS`), so the save round-trip
    drops it — it never crosses the persistence boundary. The leading
    underscore is a HUMAN-READABLE marker for "internal-only"; Python
    has no language-level passthrough semantics tied to the prefix, so
    do NOT rely on it to drop other internal-only keys without adding
    them to the matching allowlist too.
    """
    # Discriminator. Always set so the conversational dispatcher takes
    # the run_command path.
    draft["type"] = "run_command"

    mode_raw = state.get("runCommandMode")
    mode = mode_raw if mode_raw in ("inline", "attach") else None

    runtime = state.get("runCommandRuntime")
    if isinstance(runtime, str) and runtime in _RUN_COMMAND_RUNTIMES:
        draft["runtime"] = runtime

    # Inline lane.
    if mode != "attach":
        body = state.get("runCommandBody")
        if (
            isinstance(body, str)
            and body.strip()
            and len(body) <= _MAX_RUN_COMMAND_INLINE_LEN
        ):
            # Strip surrounding whitespace at the SOURCE so the rendered
            # summary does not show backticks around leading / trailing
            # spaces. Mirrors the script_id branch below which already
            # writes `.strip()`. Without this the IR validator accepts
            # the value (`command` is just a non-empty string) but the
            # summary line reads as noise.
            draft["command"] = body.strip()

    # Attached-script lane. Only land the path when the id matches the
    # canonical 64-hex shape; a half-typed id stays unwritten and the
    # follow-up turn re-asks. Always preserve the operator-typed name
    # alongside (see docstring).
    if mode != "inline":
        script_id = state.get("runCommandScriptId")
        if (
            isinstance(script_id, str)
            and script_id.strip()
            and _RC_SCRIPT_ID_RE.match(script_id.strip())
        ):
            draft["script_path"] = script_id.strip()
        script_name = state.get("runCommandScriptName")
        if (
            isinstance(script_name, str)
            and script_name.strip()
            and len(script_name) <= 200
        ):
            draft["_script_name"] = script_name.strip()

    # Args: parse CSV. Drop anything past the IR's cap so a malicious
    # client cannot pre-seed a 1000-element list and rely on the
    # validator catching it later (we want a clean ready_to_save here).
    args_raw = state.get("runCommandArgs")
    if isinstance(args_raw, str) and args_raw.strip():
        parts = [p.strip() for p in args_raw.split(",")]
        kept: list[str] = []
        for p in parts:
            if not p:
                continue
            if len(p) > _MAX_RUN_COMMAND_ARG_LEN:
                continue
            kept.append(p)
            if len(kept) >= _MAX_RUN_COMMAND_ARGS:
                break
        if kept:
            draft["args"] = kept

    # Timeout: stringified int on the URL; drop a non-integer or
    # out-of-range value.
    timeout_raw = state.get("runCommandTimeoutMs")
    if isinstance(timeout_raw, str) and timeout_raw.strip():
        try:
            tm = int(timeout_raw.strip())
        except ValueError:
            tm = None
        if (
            isinstance(tm, int)
            and not isinstance(tm, bool)
            and _MIN_RUN_COMMAND_TIMEOUT_MS <= tm <= _MAX_RUN_COMMAND_TIMEOUT_MS
        ):
            draft["timeout_ms"] = tm
    elif isinstance(timeout_raw, int) and not isinstance(timeout_raw, bool):
        if _MIN_RUN_COMMAND_TIMEOUT_MS <= timeout_raw <= _MAX_RUN_COMMAND_TIMEOUT_MS:
            draft["timeout_ms"] = timeout_raw

    # Checkbox: the wizard URL key is "true"/"false". Map to bool.
    fc_raw = state.get("runCommandFailClosed")
    if fc_raw == "true":
        draft["fail_closed"] = True
    elif fc_raw == "false":
        draft["fail_closed"] = False
    elif isinstance(fc_raw, bool):
        draft["fail_closed"] = fc_raw


# Lifecycle slug → human label for the run_command summary "at <event>"
# clause. We render even D58-only lifecycle buckets here so a
# run_command policy authored on, e.g., `permission_request` can still
# round-trip its "at the permission-request moment" framing — the
# run_command archetype is uniformly legal across the matrix per
# RUN_COMMAND_LEGAL_BY_LIFECYCLE in page.tsx, so the summary must
# describe ALL of them in plain language.
#
# COVERAGE GUARANTEE: every key in `_RUN_COMMAND_LIFECYCLE_TO_EVENT` is
# also a key in BOTH tables below. A module-load assertion at the
# bottom of the file enforces this so a future contributor who adds a
# row to the canonical event map without widening the label tables
# trips an import-time failure rather than silently dropping the
# "at <event>" clause from the summary.
_RUN_COMMAND_LIFECYCLE_LABEL_KO: dict[str, str] = {
    "before_tool_use":       "도구 실행 전",
    "after_tool_use":        "도구 실행 후",
    "pre_final":             "최종 응답 직전",
    "subagent_stop":         "서브에이전트 종료 시점",
    "user_prompt":           "사용자 입력 제출 시점",
    "pre_compact":           "메모리 정리 직전",
    "session_start":         "세션 시작 시점",
    "session_end":           "세션 종료 시점",
    # D58
    "post_tool_use_failure": "도구 실행 실패 직후",
    "post_tool_batch":       "도구 배치 실행 직후",
    "permission_request":    "권한 요청 시점",
    "permission_denied":     "권한 거부 시점",
    "user_prompt_expansion": "사용자 입력 확장 시점",
    "post_compact":          "메모리 정리 직후",
    "elicitation":           "추가 입력 요청 시점",
    "elicitation_result":    "추가 입력 결과 시점",
    "subagent_start":        "서브에이전트 시작 시점",
    "stop_failure":          "에이전트 종료 실패 시점",
    "setup":                 "초기화 시점",
    "notification":          "알림 시점",
    "teammate_idle":         "팀메이트 대기 시점",
    "task_created":          "작업 생성 시점",
    "task_completed":        "작업 완료 시점",
    "config_change":         "설정 변경 시점",
    "worktree_create":       "워크트리 생성 시점",
    "worktree_remove":       "워크트리 제거 시점",
    "instructions_loaded":   "지시문 로드 시점",
    "cwd_changed":           "작업 디렉터리 변경 시점",
    "file_changed":          "파일 변경 시점",
    "message_display":       "메시지 표시 시점",
}
_RUN_COMMAND_LIFECYCLE_LABEL_EN: dict[str, str] = {
    "before_tool_use":       "before a tool runs",
    "after_tool_use":        "after a tool runs",
    "pre_final":             "just before the final answer",
    "subagent_stop":         "when a subagent stops",
    # Plain-language at source: the string does not depend on the
    # `_to_plain_language` scrubber to translate `LLM` into `AI`.
    "user_prompt":           "before a user prompt reaches the AI",
    "pre_compact":           "before context compaction",
    "session_start":         "when the session opens",
    "session_end":           "when the session closes",
    # D58
    "post_tool_use_failure": "right after a tool failed",
    "post_tool_batch":       "right after a tool batch ran",
    "permission_request":    "at the permission-request moment",
    "permission_denied":     "right after a permission was denied",
    "user_prompt_expansion": "while the user prompt is being expanded",
    "post_compact":          "right after context compaction",
    "elicitation":           "at the follow-up-prompt moment",
    "elicitation_result":    "right after the follow-up prompt resolves",
    "subagent_start":        "when a subagent starts",
    "stop_failure":          "when the agent failed to stop",
    "setup":                 "at the setup moment",
    "notification":          "at the notification moment",
    "teammate_idle":         "when a teammate goes idle",
    "task_created":          "when a task is created",
    "task_completed":        "when a task is completed",
    "config_change":         "when configuration changes",
    "worktree_create":       "when a worktree is created",
    "worktree_remove":       "when a worktree is removed",
    "instructions_loaded":   "when instructions load",
    "cwd_changed":           "when the working directory changes",
    "file_changed":          "when a file changes",
    "message_display":       "at the message-display moment",
}

# Generic fallback for a slug the table does not cover (defensive — the
# module-load assertion should make this unreachable in practice, but
# a future page.tsx row added before the constant is widened lands
# here instead of leaking the raw slug).
_RUN_COMMAND_LIFECYCLE_FALLBACK_KO: str = "그 발동 시점"
_RUN_COMMAND_LIFECYCLE_FALLBACK_EN: str = "at that moment"


def _first_requires_body(draft: dict[str, Any]) -> str | None:
    """Read the body slot of the first requires entry, regardless of
    kind. Returns None when the requires list is empty / shaped weird.

    Used to detect whether `_apply_answer_to_draft("requires_body", ...)`
    actually wrote a value (it silently drops bodies that fail
    per-kind validation, e.g. an uncompilable regex). The conversational
    follow-up turn re-asks for a body, but the operator's bytes are
    preserved on the draft pane via `_force_requires_pattern`.
    """
    reqs = draft.get("requires")
    if not (isinstance(reqs, list) and reqs and isinstance(reqs[0], dict)):
        return None
    item = reqs[0]
    for k in ("pattern", "criterion", "shape_ttl", "step"):
        v = item.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def _requires_overlay_has_body(reqs: list[Any]) -> bool:
    """Return True iff the first overlay requires entry carries a
    non-empty body field. Used at the merge layer so an overlay slot
    that only carries a kind (e.g. wizard picked regex but the body is
    in-progress) does not clobber a fully-shaped base value.
    """
    if not reqs:
        return False
    first = reqs[0]
    if not isinstance(first, dict):
        return False
    for k in ("pattern", "criterion", "shape_ttl", "step"):
        v = first.get(k)
        if isinstance(v, str) and v.strip():
            return True
    return False


def _force_requires_pattern(draft: dict[str, Any], pattern: str) -> None:
    """Write a raw regex pattern onto the first requires slot even when
    `_apply_answer_to_draft` would refuse it (uncompilable). The
    follow-up turn validates the pattern on submit so a bad value
    cannot leak past ready_to_save; preserving the operator's bytes on
    the IR draft pane is more important than refusing the seed at the
    handoff seam.
    """
    if len(pattern) > 2_000:
        return
    reqs = draft.get("requires")
    if not (isinstance(reqs, list) and reqs and isinstance(reqs[0], dict)):
        draft["requires"] = [{"kind": "regex", "pattern": pattern}]
        return
    item = reqs[0]
    item["kind"] = "regex"
    item["pattern"] = pattern


def _draft_from_wizard_state(state: dict[str, Any]) -> dict[str, Any]:
    """Project the wizard's URL state into a draft IR shape.

    Reuses `_apply_answer_to_draft`'s per-field allowlists so a value
    that survives the projection has already passed canonical validation.
    Anything the wizard can hold but the conversational vocabulary
    cannot model (rewriter spec, context_injection template, the long
    tail of D58 lifecycles) is silently dropped — the operator will see
    the collapse reflected in the assistant summary so they know what
    landed.

    Empty / uncompilable regex bodies do NOT seed an empty `requires`
    slot — see the regex branch below. The merge layer (`_merge_drafts`)
    treats the absence of `requires` as "fall back to the raw editor's
    value", which preserves a good `requires=[{kind:"regex",
    pattern:"^foo$"}]` from the raw editor when the wizard's regex body
    is in-progress / half-typed.
    """
    draft: dict[str, Any] = {}

    # Lifecycle. The conversational vocab supports the 3-bucket
    # mapping in `_LIFECYCLE_TO_EVENT`. Anything else degrades to
    # "still missing" so the assistant re-asks — UNLESS the wizard is
    # mid-flight on a run_command policy, in which case we project the
    # wider D58 lifecycle directly onto the trigger (see the
    # `action == "run_command"` branch further down). The state-coverage
    # gap that motivated this widening is that page.tsx legalises
    # run_command across ALL ~30 lifecycles via
    # RUN_COMMAND_LEGAL_BY_LIFECYCLE, so the handoff seam must honor
    # those picks; the 3-bucket `_apply_answer_to_draft("lifecycle", ...)`
    # only handles verifier-vocabulary lifecycles.
    lifecycle = state.get("lifecycle")
    action_value = state.get("action")
    is_run_command_action = (
        isinstance(action_value, str) and action_value == "run_command"
    )
    if isinstance(lifecycle, str) and lifecycle in _LIFECYCLE_TO_EVENT:
        _apply_answer_to_draft(draft, "lifecycle", lifecycle)
    elif (
        is_run_command_action
        and isinstance(lifecycle, str)
        and lifecycle in _RUN_COMMAND_LIFECYCLE_TO_EVENT
    ):
        # Wider lifecycle for a run_command policy. Write the trigger
        # directly so the round-trip survives the 3-bucket bottleneck.
        wider_event = _RUN_COMMAND_LIFECYCLE_TO_EVENT[lifecycle]
        trig = draft.get("trigger") if isinstance(draft.get("trigger"), dict) else {}
        trig = dict(trig) if isinstance(trig, dict) else {}
        trig["host"] = "claude-code"
        trig["event"] = wider_event
        draft["trigger"] = trig

    # Tool scope. Only meaningful for tool-context lifecycles; the
    # wizard's URL already collapsed multi-tool to first-token, so
    # we accept the value as-is here. The matcher-legality check
    # inside `_apply_answer_to_draft` will silently drop unrecognised
    # matcher classes.
    if isinstance(lifecycle, str) and lifecycle in _TOOL_CONTEXT_LIFECYCLES:
        tool_scope = state.get("toolScope")
        if isinstance(tool_scope, str):
            v = tool_scope.strip()
            # Wildcard / empty / multi-token strings are skipped here so
            # the merged draft does not pre-seed a matcher the wizard
            # would treat as "still missing" anyway. The single-tool
            # case lands cleanly.
            if v and v != "*" and "," not in v and "|" not in v:
                _apply_answer_to_draft(draft, "matcher", v)

    # Condition kind + body. Only the four EvidenceReq archetypes the
    # conversational compiler models (regex / llm_critic / shacl /
    # evidence_ref→step) are accepted. The wizard's wider surface
    # (fetch_domain / domain_allowlist) is compiled-down to regex on
    # save in the guided flow; we mirror that here so the assistant
    # summary can still describe the intent.
    #
    # IMPORTANT: only seed `requires` when there is also a body to
    # write. An empty / uncompilable body would leave `requires=[{...,
    # pattern:""}]` on the wizard overlay which `_merge_drafts` would
    # then prefer over a well-formed `requires` from the raw editor.
    # The "kind-but-no-body" case is reported via missing_fields
    # (`requires_body`) so the next conversational turn asks for it.
    cond_kind = state.get("conditionKind")
    if isinstance(cond_kind, str):
        if cond_kind in _REQUIRES_KINDS:
            if cond_kind == "regex":
                pat = state.get("pattern")
                if isinstance(pat, str) and pat.strip():
                    body = pat.strip()
                    _apply_answer_to_draft(draft, "requires", cond_kind)
                    # `_apply_answer_to_draft("requires_body", ...)`
                    # silently drops uncompilable regex bodies (it gates
                    # on `re.compile`). To preserve the operator's
                    # half-typed pattern, fall back to a direct write
                    # on the requires slot when the canonical path
                    # refuses the body. The conversational follow-up
                    # turn will validate again on submit, so a bad
                    # body cannot leak past `ready_to_save` here.
                    before = _first_requires_body(draft)
                    _apply_answer_to_draft(draft, "requires_body", body)
                    after = _first_requires_body(draft)
                    if before == after:
                        _force_requires_pattern(draft, body)
            elif cond_kind == "llm_critic":
                crit = state.get("llmCriterion")
                if isinstance(crit, str) and crit.strip():
                    _apply_answer_to_draft(draft, "requires", cond_kind)
                    _apply_answer_to_draft(draft, "requires_body", crit.strip())
            elif cond_kind == "shacl":
                ttl = state.get("shaclTtl")
                if isinstance(ttl, str) and ttl.strip():
                    _apply_answer_to_draft(draft, "requires", cond_kind)
                    _apply_answer_to_draft(draft, "requires_body", ttl.strip())
            elif cond_kind == "step":
                # Bare wizard `step` kind is rare (the wizard usually
                # passes via `evidence_ref`) but we accept it here for
                # parity. No body field on the wizard URL, so leave
                # the body slot for the follow-up turn.
                _apply_answer_to_draft(draft, "requires", cond_kind)
        elif cond_kind == "evidence_ref":
            refs = state.get("evidenceRefs")
            if isinstance(refs, list) and refs:
                first = next((r for r in refs if isinstance(r, str) and r.strip()), None)
                if first:
                    _apply_answer_to_draft(draft, "requires", "step")
                    _apply_answer_to_draft(draft, "requires_body", first.strip())
        elif cond_kind == "fetch_domain":
            domain = state.get("fetchDomain")
            if isinstance(domain, str) and domain.strip():
                import re as _re
                pattern = (
                    f"https?://([^/]+\\.)?{_re.escape(domain.strip())}(/|$)"
                )
                _apply_answer_to_draft(draft, "requires", "regex")
                _apply_answer_to_draft(draft, "requires_body", pattern)
        elif cond_kind == "domain_allowlist":
            raw_list = state.get("allowlist")
            if isinstance(raw_list, str) and raw_list.strip():
                import re as _re
                entries = [
                    s.strip() for s in raw_list.split(",") if s.strip()
                ]
                if entries:
                    alts = "|".join(_re.escape(e) for e in entries)
                    pattern = (
                        f"^(?!https?://([^/]+\\.)?({alts})(/|$)).*$"
                    )
                    _apply_answer_to_draft(draft, "requires", "regex")
                    _apply_answer_to_draft(draft, "requires_body", pattern)
        # "none" / unknown → no requires written.

    # Action. The conversational vocab speaks block / ask / audit; the
    # wizard's extended archetypes (strip / inject_context /
    # input_rewrite) cannot round-trip through this serializer. We
    # collapse them to a neutral "audit" so the missing-fields list
    # does not report on_missing as still-missing; the assistant
    # summary calls out the collapse in plain language so the operator
    # knows it happened.
    #
    # D66 — `action == "run_command"` is special: the conversational
    # compiler DOES model the run_command archetype (via the `type:
    # "run_command"` discriminator, see D65). When the wizard handed
    # off mid-flight on a run_command policy, project its per-field
    # body (command / runtime / args / timeout_ms / fail_closed /
    # script_id / scriptName) straight onto the draft. The verifier
    # fields `requires` / `on_missing` are NOT meaningful on
    # run_command — `_missing_fields_for_draft` already dispatches on
    # the `type` discriminator so we do NOT need to seed `on_missing`.
    action = state.get("action")
    if isinstance(action, str):
        if action == "run_command":
            _project_run_command_state(state, draft)
            # If `_project_run_command_state` did not seed a command or
            # script_path, the conversational follow-up will re-ask via
            # the `requires_body` question slot (see
            # `_run_command_missing_fields`). The `requires` slot stays
            # absent — it is verifier vocabulary.
            #
            # `conditionKind` could have written a `requires` slot
            # earlier in this function (e.g. wizard state carries
            # conditionKind=regex + pattern=^foo$ AND action=run_command
            # — the dashboard surfaces both legally). The run_command
            # archetype's discriminator is mutually exclusive with the
            # verifier `requires` slot; leaving both on the overlay
            # makes `_merge_drafts` write `requires` straight onto the
            # merged draft, and IrDraftPane.conditionLabel reads it
            # back as "Pattern in the response" next to the runs-shell
            # banner. Drop the verifier-only keys here so the overlay
            # is a pure run_command archetype at the SOURCE rather
            # than relying on `_merge_drafts` to scrub the base only.
            draft.pop("requires", None)
            draft.pop("action", None)
            draft.pop("on_missing", None)
        elif action in _ON_MISSING_VALUES:
            _apply_answer_to_draft(draft, "on_missing", action)

    # Id + description.
    pid = state.get("id")
    if isinstance(pid, str) and pid.strip():
        _apply_answer_to_draft(draft, "id", pid.strip())
    desc = state.get("description")
    if isinstance(desc, str) and 0 < len(desc) <= 2_000:
        draft["description"] = desc.strip()

    return draft


def _merge_drafts(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Shallow-merge: overlay wins on present-and-non-empty keys.

    Used to layer the wizard-state-derived draft on top of the
    sanitized `draft_ir`. The raw editor is the more explicit author;
    if both are present we take the wizard state's values when they
    exist (the user just clicked Continue from the wizard surface, so
    the wizard state IS the most recent intent), and fall back to the
    raw editor's values for anything the wizard surface did not author.

    D66 — when the overlay commits to `type: "run_command"` we DROP the
    base's verifier-only keys (`requires`, `action`) so the merged
    draft is a pure run_command archetype. Otherwise a raw editor
    draft that had a half-typed evidence rule would collide with the
    wizard's run_command intent and `_draft_passes_ir_validator` would
    reject both archetypes' field union as an unknown shape.
    Conversely when the BASE committed to run_command but the overlay
    did not (e.g. the wizard handed off before the operator picked the
    run_command action) the base wins on the discriminator.
    """
    out: dict[str, Any] = copy.deepcopy(base) if base else {}
    if not isinstance(overlay, dict):
        return out
    # D66 — drop verifier-only keys when the overlay commits to
    # run_command. The two archetypes are mutually exclusive and
    # mixing them would produce a draft no IR loader accepts.
    if overlay.get("type") == "run_command":
        out.pop("requires", None)
        out.pop("action", None)
        out.pop("on_missing", None)
    for k, v in overlay.items():
        if k == "trigger" and isinstance(v, dict):
            cur = out.get("trigger") if isinstance(out.get("trigger"), dict) else {}
            merged = dict(cur)
            for tk, tv in v.items():
                if tv:
                    merged[tk] = tv
            merged["host"] = "claude-code"
            out["trigger"] = merged
            continue
        if k == "requires" and isinstance(v, list):
            if v and _requires_overlay_has_body(v):
                out["requires"] = v
            # If the overlay has a `requires` slot but its body is empty
            # (kind selected, body still missing), fall back to the
            # base's value so a well-formed `requires` from the raw
            # editor is not clobbered by the wizard's empty slot.
            continue
        # D66 — `fail_closed=False` is a meaningful run_command value
        # (the operator explicitly opted INTO non-blocking failure).
        # The blanket "drop empty / falsy" rule below would otherwise
        # erase it on the merge.
        if k == "fail_closed" and isinstance(v, bool):
            out["fail_closed"] = v
            continue
        if v in (None, "", []):
            continue
        out[k] = v
    return out


def _dropped_action_label(slug: str, ko: bool) -> str:
    """Plain-language label for a dropped action archetype. Falls back
    to a generic phrasing for an unknown / future value so the raw
    slug never reaches the rendered chat."""
    table = _ACTION_DROPPED_LABEL_KO if ko else _ACTION_DROPPED_LABEL_EN
    if slug in table:
        return table[slug]
    return "그 동작" if ko else "that action"


def _dropped_kind_label(slug: str, ko: bool) -> str:
    """Plain-language label for a dropped condition kind. Falls back to
    a generic phrasing for unknown values (`banana`, a future kind that
    ships without a label update) so the raw slug never reaches chat."""
    table = _COND_DROPPED_LABEL_KO if ko else _COND_DROPPED_LABEL_EN
    if slug in table:
        return table[slug]
    return _COND_DROPPED_FALLBACK_KO if ko else _COND_DROPPED_FALLBACK_EN


def _dropped_lifecycle_label(slug: str, ko: bool) -> str:
    """Plain-language label for a dropped lifecycle slug outside the
    3-bucket conversational vocabulary."""
    table = _LIFECYCLE_DROPPED_LABEL_KO if ko else _LIFECYCLE_DROPPED_LABEL_EN
    if slug in table:
        return table[slug]
    return _LIFECYCLE_DROPPED_FALLBACK_KO if ko else _LIFECYCLE_DROPPED_FALLBACK_EN


def _summarize_run_command_line(
    draft: dict[str, Any], *, ko: bool,
) -> str | None:
    """Build the brief's run_command summary line.

    Per the D66 brief:
        "Continuing where you left off. So far: run <command> at <event>
        with <timeout>s timeout, <fail_closed | non-blocking on failure>."

    We render the localised KO/EN variant deterministically (no LLM
    call). Anything the draft has NOT yet filled is silently elided so
    the line still reads naturally for a half-typed draft:

        "run <X> at <event>"
        "run <X> at <event> with 3.0s timeout"
        "<event>"  (event-only handoff, no command yet)
        None       (no run_command material at all)
    """
    if draft.get("type") != "run_command":
        return None
    trig = draft.get("trigger") if isinstance(draft.get("trigger"), dict) else {}
    event = trig.get("event") if isinstance(trig, dict) else None
    # Map back through both LIFECYCLE_TO_EVENT (conversational vocab)
    # and the wider D58 table so the line still names the timing for a
    # wizard-built run_command policy on permission_request etc. We use
    # the canonical `_RUN_COMMAND_LIFECYCLE_TO_EVENT` table (derived
    # 1:1 from page.tsx LIFECYCLE_TO_EVENT) so every D58 slug renders
    # a real label rather than falling through to an empty clause.
    lifecycle_slug: str | None = None
    if isinstance(event, str):
        for slug, ev in _LIFECYCLE_TO_EVENT.items():
            if ev == event:
                lifecycle_slug = slug
                break
        if lifecycle_slug is None:
            for slug, ev in _RUN_COMMAND_LIFECYCLE_TO_EVENT.items():
                if ev == event:
                    lifecycle_slug = slug
                    break
    lifecycle_label: str | None = None
    if lifecycle_slug:
        table = (
            _RUN_COMMAND_LIFECYCLE_LABEL_KO if ko
            else _RUN_COMMAND_LIFECYCLE_LABEL_EN
        )
        lifecycle_label = table.get(lifecycle_slug)
        if lifecycle_label is None:
            # Defensive fallback — should be unreachable because the
            # module-load assertion at the bottom of the file guarantees
            # the label tables cover every key in the canonical map.
            lifecycle_label = (
                _RUN_COMMAND_LIFECYCLE_FALLBACK_KO if ko
                else _RUN_COMMAND_LIFECYCLE_FALLBACK_EN
            )

    command = draft.get("command")
    script_path = draft.get("script_path")
    script_name = draft.get("_script_name")
    timeout_ms = draft.get("timeout_ms")
    fail_closed = draft.get("fail_closed")

    # Build the "what runs" phrase: prefer the inline command verbatim
    # (truncated for the assistant line so a 4000-char inline body does
    # not blow the summary). For an attached script, show the
    # operator-typed name when present, else the bare id (operators
    # need SOMETHING to recognise their policy by).
    #
    # When truncation kicks in we append a localised "(truncated, N more
    # chars)" cue so the operator knows the rest of the body survived
    # in the draft and is not silently dropped. The bare "..." suffix
    # without a length signal looked indistinguishable from a verbatim
    # ellipsis the operator might have typed themselves.
    what: str | None = None
    if isinstance(command, str) and command.strip():
        body = command.strip()
        if len(body) <= 80:
            what = f"`{body}`"
        else:
            head_chars = body[:80]
            remaining = len(body) - 80
            if ko:
                what = f"`{head_chars}` (일부 생략, {remaining}자 더 있음)"
            else:
                what = f"`{head_chars}` (truncated, {remaining} more chars)"
    elif isinstance(script_path, str) and script_path.strip():
        if isinstance(script_name, str) and script_name.strip():
            what = script_name.strip()
        else:
            # Show only the first 8 chars of the hash so the assistant
            # line stays readable.
            sid = script_path.strip()
            short = sid[:8] + "..." if len(sid) > 8 else sid
            what = (
                f"스크립트 {short}" if ko else f"script {short}"
            )

    # Compose the assemble-as-you-go line. We avoid string-format
    # acrobatics so the KO branch reads idiomatic Korean (postpositions
    # depend on whether `what` ends in a consonant or vowel; we just
    # use a connector that works in both cases).
    if ko:
        if what and lifecycle_label:
            base = f"{lifecycle_label}에 {what} 실행"
        elif what:
            base = f"{what} 실행"
        elif lifecycle_label:
            base = f"{lifecycle_label}에 명령 실행"
        else:
            base = "명령 실행"
    else:
        if what and lifecycle_label:
            base = f"run {what} {lifecycle_label}"
        elif what:
            base = f"run {what}"
        elif lifecycle_label:
            base = f"run a command {lifecycle_label}"
        else:
            base = "run a command"

    # Timeout suffix.
    if isinstance(timeout_ms, int) and not isinstance(timeout_ms, bool):
        seconds = timeout_ms / 1_000
        # Trim trailing zero on whole-second timeouts so "5s timeout"
        # reads cleaner than "5.0s timeout".
        if seconds == int(seconds):
            secs_str = f"{int(seconds)}s"
        else:
            secs_str = f"{seconds:g}s"
        if ko:
            base += f", 타임아웃 {secs_str}"
        else:
            base += f" with {secs_str} timeout"

    # Fail-closed suffix.
    if isinstance(fail_closed, bool):
        if fail_closed:
            base += (
                ", 실패 시 차단" if ko else ", deny on failure"
            )
        else:
            base += (
                ", 실패 시 통과 + 기록"
                if ko else ", non-blocking on failure"
            )

    return base


def _build_collapse_note(
    *,
    ko: bool,
    dropped_action: str | None,
    dropped_kind: str | None,
    dropped_lifecycle: str | None,
    dropped_tool_scope: str | None,
    dropped_payload: dict[str, str] | None,
) -> str:
    """Render the "Note:" block surfacing any axis that degraded on the
    handoff. Pulled out of `_summarize_so_far` so the run_command branch
    can reuse the same renderer — without this both branches would have
    to repeat the dropped_lifecycle / dropped_tool_scope / dropped_payload
    wiring and a slip in one branch would silently swallow the operator's
    pick on the other.
    """
    note_lines: list[str] = []
    if dropped_lifecycle:
        label = _dropped_lifecycle_label(dropped_lifecycle, ko)
        if ko:
            note_lines.append(
                f"{label} 은(는) 대화형에서 다루지 못해서 가까운 발동 시점으로 정리해 주세요."
            )
        else:
            note_lines.append(
                f"{label} does not have a chat-mode equivalent yet, so please re-pick when in tool-runs / after tool-runs / final answer."
            )
    if dropped_tool_scope:
        if ko:
            note_lines.append(
                f"'{dropped_tool_scope}' 적용 대상은 그 발동 시점에는 의미가 없어서 함께 비웠어요. 새 발동 시점을 고른 뒤 다시 골라주세요."
            )
        else:
            note_lines.append(
                f"the '{dropped_tool_scope}' target was cleared because that timing has no tool scope; please re-pick after choosing a new timing."
            )
    if dropped_action:
        label = _dropped_action_label(dropped_action, ko)
        if ko:
            note_lines.append(
                f"{label} 동작은 대화형에서 다루지 못해서 가까운 기본값으로 정리했어요."
            )
        else:
            note_lines.append(
                f"{label} does not map cleanly to the chat surface, so it was collapsed to the closest default."
            )
    if dropped_kind:
        label = _dropped_kind_label(dropped_kind, ko)
        if ko:
            note_lines.append(
                f"{label} 은(는) 대화형에서 다루지 못해서 가까운 기본값으로 정리했어요."
            )
        else:
            note_lines.append(
                f"{label} does not map cleanly to the chat surface, so it was collapsed to the closest default."
            )

    note: str = ""
    if note_lines:
        if ko:
            note = "\n\n참고:\n" + "\n".join(f"  - {ln}" for ln in note_lines)
        else:
            note = "\n\nNote:\n" + "\n".join(f"  - {ln}" for ln in note_lines)

    if dropped_payload:
        if ko:
            note += "\n\n작성하셨던 내용은 그대로 적어 둘게요:\n"
        else:
            note += "\n\nYou had written this; keep it handy in case you want to reuse it:\n"
        for k, v in dropped_payload.items():
            snippet = v if len(v) <= 500 else (v[:500] + ("..." if len(v) > 500 else ""))
            label_ko = {
                "injectTemplate":      "맥락 주입 텍스트",
                "rewriterPrefix":      "앞에 붙일 텍스트",
                "rewriterFrom":        "찾을 텍스트",
                "rewriterTo":          "바꿀 텍스트",
                "rewriterPattern":     "찾을 패턴",
                "rewriterReplacement": "치환 텍스트",
                "rewriterField":       "대상 필드",
                "rewriterStripRepeat": "반복 제거",
                "rewriterKind":        "다시 쓰기 방식",
                "rewriterCount":       "치환 횟수",
                "injectLabelKo":       "한국어 라벨",
                "injectLabelEn":       "영어 라벨",
            }
            label_en = {
                "injectTemplate":      "the reminder text",
                "rewriterPrefix":      "the prefix text",
                "rewriterFrom":        "the find text",
                "rewriterTo":          "the replace text",
                "rewriterPattern":     "the pattern",
                "rewriterReplacement": "the replacement text",
                "rewriterField":       "the target field",
                "rewriterStripRepeat": "the strip-repeat setting",
                "rewriterKind":        "the rewrite kind",
                "rewriterCount":       "the replacement count",
                "injectLabelKo":       "the Korean label",
                "injectLabelEn":       "the English label",
            }
            label = label_ko.get(k, k) if ko else label_en.get(k, k)
            note += f"  - {label}: {snippet}\n"
    return note


def _summarize_so_far(
    draft: dict[str, Any],
    *,
    ko: bool,
    dropped_action: str | None,
    dropped_kind: str | None,
    dropped_lifecycle: str | None = None,
    dropped_tool_scope: str | None = None,
    dropped_payload: dict[str, str] | None = None,
    origin: str | None = None,
) -> str:
    """Plain-language single-paragraph summary of the merged draft.

    Used as the first assistant turn on the conversational shell after a
    handoff. Mirrors the brief's "so far: …" framing. Never names an
    internal field, never emits an internal vocabulary token (the
    `_to_plain_language` scrubber runs at the end as defense in depth).

    D66 — when the merged draft carries `type: "run_command"` the
    summary uses the brief's run-command framing instead of the
    verifier-shaped (when / applies-to / check / on-failure) bullets.
    """
    # D66 — run_command archetype short-circuit. We render the brief's
    # framing ("Continuing where you left off. So far: run <X> at
    # <when> with <T>s timeout, <fail mode>.") instead of the
    # verifier-shaped bullets. This stays plain language: no internal
    # vocabulary, no raw enum slugs.
    if draft.get("type") == "run_command":
        rc_line = _summarize_run_command_line(draft, ko=ko)
        head = (
            "지금까지 작성하신 내용을 이어서 받았어요:"
            if ko else
            "Continuing where you left off. So far:"
        )
        if origin == "review":
            head = (
                "마지막 검토 화면에서 이어서 받았어요:"
                if ko else
                "Picking up from the review screen. So far:"
            )
        elif origin == "advanced":
            head = (
                "직접 작성 모드에서 이어서 받았어요:"
                if ko else
                "Continuing from the rule editor. So far:"
            )
        # Optional matcher line for tool-context lifecycles.
        matcher_line: str | None = None
        trig_ = draft.get("trigger") if isinstance(draft.get("trigger"), dict) else {}
        mch = trig_.get("matcher") if isinstance(trig_, dict) else None
        if isinstance(mch, str) and mch.strip() and mch.strip() != "*":
            if ko:
                matcher_line = f"적용 대상: {mch.strip()}"
            else:
                matcher_line = f"applies to: {mch.strip()}"
        # Optional id line.
        id_line: str | None = None
        pid_ = draft.get("id")
        if isinstance(pid_, str) and pid_:
            if ko:
                id_line = f"이름: {pid_}"
            else:
                id_line = f"name: {pid_}"
        body_lines: list[str] = []
        if rc_line:
            body_lines.append(f"  - {rc_line}")
        if matcher_line:
            body_lines.append(f"  - {matcher_line}")
        if id_line:
            body_lines.append(f"  - {id_line}")
        # Surface dropped axes so a run_command wizard that handed off
        # mid-flight with, e.g., a verifier-vocabulary kind picked or
        # an unrecognised lifecycle, still emits the collapse note.
        # Mirrors the verifier branch — without this the operator's
        # pick disappears silently when the wider lifecycle table is
        # the only thing that saved the round-trip.
        rc_note = _build_collapse_note(
            ko=ko,
            dropped_action=dropped_action,
            dropped_kind=dropped_kind,
            dropped_lifecycle=dropped_lifecycle,
            dropped_tool_scope=dropped_tool_scope,
            dropped_payload=dropped_payload,
        )
        tail_q = (
            "\n\n남은 부분을 같이 채워볼까요?"
            if ko else
            "\n\nLet's fill in what's still missing."
        )
        rendered = head
        if body_lines:
            rendered += "\n" + "\n".join(body_lines)
        rendered += rc_note + tail_q
        return _to_plain_language(rendered)

    pieces: list[str] = []
    trig = draft.get("trigger") if isinstance(draft.get("trigger"), dict) else {}
    event = trig.get("event") if isinstance(trig, dict) else None
    matcher = trig.get("matcher") if isinstance(trig, dict) else None
    # Map event back to the 3-bucket label.
    lifecycle_label: str | None = None
    if isinstance(event, str):
        # Reverse-lookup against the conversational lifecycle table.
        for slug, ev in _LIFECYCLE_TO_EVENT.items():
            if ev == event:
                lifecycle_label = (
                    _LIFECYCLE_LABEL_KO[slug] if ko else _LIFECYCLE_LABEL_EN[slug]
                )
                break
    if lifecycle_label:
        if ko:
            pieces.append(f"발동 시점: {lifecycle_label}")
        else:
            pieces.append(f"when: {lifecycle_label}")
    if isinstance(matcher, str) and matcher.strip() and matcher.strip() != "*":
        if ko:
            pieces.append(f"적용 대상: {matcher.strip()}")
        else:
            pieces.append(f"applies to: {matcher.strip()}")
    reqs = draft.get("requires")
    if isinstance(reqs, list) and reqs and isinstance(reqs[0], dict):
        first = reqs[0]
        kind = first.get("kind") or ("step" if "step" in first else None)
        if isinstance(kind, str):
            kind_label = (
                _REQUIRES_KIND_LABEL_KO.get(kind) if ko
                else _REQUIRES_KIND_LABEL_EN.get(kind)
            )
            if kind_label:
                if ko:
                    pieces.append(f"확인 방식: {kind_label}")
                else:
                    pieces.append(f"check: {kind_label}")
    action = draft.get("action") or draft.get("on_missing")
    if isinstance(action, str) and action in _ON_MISSING_VALUES:
        action_label = (
            _ACTION_LABEL_KO[action] if ko else _ACTION_LABEL_EN[action]
        )
        if ko:
            pieces.append(f"실패 시: {action_label}")
        else:
            pieces.append(f"on failure: {action_label}")
    pid = draft.get("id")
    if isinstance(pid, str) and pid:
        if ko:
            pieces.append(f"이름: {pid}")
        else:
            # The wizard's user-facing label for this field is "Name"
            # (Step 5 heading). Mirror that here for consistency with
            # the surface the operator just left.
            pieces.append(f"name: {pid}")

    if not pieces:
        # When the merge degraded everything (e.g. wizard authored a
        # D58-only lifecycle that has no conversational equivalent),
        # this branch used to print "haven't filled much in yet",
        # which is actively misleading. Call out the silent
        # degradation when we detected one.
        if dropped_lifecycle or dropped_action or dropped_kind:
            head = (
                "방금 작성하신 내용 중 일부는 대화형에서 다루지 못하는 항목이라 함께 정리해야 해요."
                if ko else
                "Some of what you had does not map cleanly onto the chat surface; we'll work through it together."
            )
        else:
            return (
                "지금까지 입력하신 내용이 거의 없네요. 처음부터 같이 채워볼게요."
                if ko else
                "It looks like you haven't filled much in yet. Let's pick up from the start together."
            )
        body = ""
    else:
        if origin == "review":
            head = (
                "마지막 검토 화면에서 이어서 받았어요:"
                if ko else
                "Picking up from the review screen. Here is what you had so far:"
            )
        elif origin == "advanced":
            head = (
                "직접 작성 모드에서 이어서 받았어요:"
                if ko else
                "Continuing from the rule editor. Here is what you had so far:"
            )
        else:
            head = (
                "지금까지 작성하신 내용을 이어서 받았어요:"
                if ko else
                "Continuing from where you were. Here is what you had so far:"
            )
        body = "\n".join(f"  - {p}" for p in pieces)

    # Build collapse notes. We emit ONE note bullet per dropped axis so
    # the user knows exactly which surface degraded. Each rendered label
    # passes through the plain-language tables above; we never
    # interpolate the raw slug. The same renderer is reused by the
    # run_command branch above so the wiring stays consistent across
    # archetypes.
    note = _build_collapse_note(
        ko=ko,
        dropped_action=dropped_action,
        dropped_kind=dropped_kind,
        dropped_lifecycle=dropped_lifecycle,
        dropped_tool_scope=dropped_tool_scope,
        dropped_payload=dropped_payload,
    )

    tail_q = (
        "\n\n남은 부분을 같이 채워볼까요?"
        if ko else
        "\n\nLet's fill in what's still missing."
    )
    rendered = head
    if body:
        rendered += "\n" + body
    rendered += note + tail_q
    return _to_plain_language(rendered)


def _questions_for_missing(
    draft: dict[str, Any], missing: list[str], ko: bool,
) -> list[Question]:
    """Build the canonical question set for the first MAX_QUESTIONS_PER_TURN
    still-missing fields. Mirrors `step_compile`'s fallback so the first
    handoff turn behaves like every subsequent turn."""
    out: list[Question] = []
    for f in missing[:MAX_QUESTIONS_PER_TURN]:
        if f == "requires_body":
            out.append(_question_for_requires_body(draft, ko))
        else:
            try:
                out.append(_question_for_field(f, ko))  # type: ignore[arg-type]
            except ValueError:
                continue
    return out


def _bytesize(obj: Any) -> int:
    """Cheap upper-bound byte size for the inbound dict guard."""
    import json as _json
    try:
        return len(_json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


# Fields the wizard URL ferries for the wizard-only action archetypes
# (inject_context / input_rewrite / strip). When the action is dropped
# the body fields would silently evaporate; we collect them into a
# `dropped_payload` and surface them in the assistant message so the
# operator's bytes are never lost without a trace.
_DROPPED_ACTION_BODY_KEYS_PER_ACTION: dict[str, tuple[str, ...]] = {
    "inject_context": (
        "injectTemplate", "injectLabelKo", "injectLabelEn",
    ),
    "input_rewrite": (
        "rewriterKind", "rewriterField", "rewriterPrefix",
        "rewriterStripRepeat", "rewriterFrom", "rewriterTo",
        "rewriterPattern", "rewriterReplacement", "rewriterCount",
    ),
    # `strip` has no body fields on the wizard URL; included for
    # completeness so a future rev that adds one updates the table.
    "strip": (),
}


def build_handoff_turn(
    *,
    wizard_state: dict[str, Any] | None,
    draft_ir: dict[str, Any] | None,
    origin: str | None = None,
    locale_hint: str | None = None,
    runtime_id: str | None = None,
) -> dict[str, Any]:
    """Build the first conversational turn for a seeded handoff.

    See module docstring for the wire shape contract. Raises
    `HandoffContextError` (maps to 422 at the route) on malformed input.

    `origin` is the authoring surface the user just left
    ("guided" / "advanced" / "review"). Used to vary the summary
    headline so the chat picks up with the right framing (e.g.
    "Continuing from the rule editor" vs "Picking up from the review
    screen"). Optional; defaults to the generic framing.

    `locale_hint` is an explicit "ko" / "en" override for the
    summary language. The dashboard already knows the operator's
    locale from the URL / cookie; forwarding it here avoids the
    case where a Korean-locale operator authoring an English-only
    policy gets an English seed because `_detect_korean` only sees
    English in the merged draft.
    """
    if wizard_state is not None and not isinstance(wizard_state, dict):
        raise HandoffContextError("wizard_state must be an object or null")
    if draft_ir is not None and not isinstance(draft_ir, dict):
        raise HandoffContextError("draft_ir must be an object or null")

    ws: dict[str, Any] = wizard_state or {}
    di: dict[str, Any] = draft_ir or {}

    if _bytesize(ws) > _MAX_STATE_BYTES:
        raise HandoffContextError("wizard_state too large")
    if _bytesize(di) > _MAX_STATE_BYTES:
        raise HandoffContextError("draft_ir too large")

    if origin is not None and origin not in ("guided", "advanced", "review"):
        # Tolerate but normalize; do not 422 on a future origin token.
        origin = None

    # Track ahead of the merge so the summary can mention archetype
    # collapses (input_rewrite / inject_context / strip → audit; long
    # tail of wizard-only condition kinds → none).
    #
    # D66 — `run_command` is NOT a dropped archetype anymore. It
    # round-trips through `_project_run_command_state` so the
    # conversational compiler picks it up as a `type: "run_command"`
    # draft. Treating it as dropped would emit a misleading collapse
    # note in the summary ("rolling shell-command was collapsed to the
    # closest default") while the draft actually carries the body.
    raw_action = ws.get("action")
    dropped_action: str | None = None
    if (
        isinstance(raw_action, str)
        and raw_action
        and raw_action not in _ON_MISSING_VALUES
        and raw_action != "run_command"
    ):
        dropped_action = raw_action

    raw_cond_kind = ws.get("conditionKind")
    dropped_kind: str | None = None
    _CONVERSATIONAL_KINDS = {
        "regex", "llm_critic", "shacl", "evidence_ref",
        "fetch_domain", "domain_allowlist", "none",
    }
    if (
        isinstance(raw_cond_kind, str)
        and raw_cond_kind
        and raw_cond_kind not in _CONVERSATIONAL_KINDS
    ):
        dropped_kind = raw_cond_kind

    # Track dropped lifecycle (anything outside the 3-bucket
    # conversational vocabulary, e.g. permission_request / session_*
    # / subagent_* / worktree_*). Without this the summary would say
    # "haven't filled much in yet" when the user actually filled in a
    # complete D58 lifecycle slot.
    #
    # When the wizard handed off committing to action=run_command AND
    # the lifecycle is in the wider `_RUN_COMMAND_LIFECYCLE_TO_EVENT`
    # table, the lifecycle is NOT dropped — the run_command branch in
    # `_draft_from_wizard_state` projects it directly onto the trigger
    # so the round-trip survives. Only an unrecognised slug (or a
    # non-run_command action with a D58-only slug) counts as dropped.
    raw_lifecycle = ws.get("lifecycle")
    dropped_lifecycle: str | None = None
    lifecycle_landed_via_run_command = (
        isinstance(raw_action, str)
        and raw_action == "run_command"
        and isinstance(raw_lifecycle, str)
        and raw_lifecycle in _RUN_COMMAND_LIFECYCLE_TO_EVENT
    )
    if (
        isinstance(raw_lifecycle, str)
        and raw_lifecycle
        and raw_lifecycle not in _LIFECYCLE_TO_EVENT
        and not lifecycle_landed_via_run_command
    ):
        dropped_lifecycle = raw_lifecycle

    # If the lifecycle was dropped, an in-progress toolScope would also
    # disappear from the merged draft (no place to land it). Report
    # that too so the user knows their tool pick was cleared. We also
    # report a tool scope drop when the lifecycle landed via the
    # run_command wider table but is not a tool-context lifecycle (the
    # toolScope still cannot land in that case).
    raw_tool_scope = ws.get("toolScope")
    dropped_tool_scope: str | None = None
    if isinstance(raw_tool_scope, str):
        v = raw_tool_scope.strip()
        if v and v != "*":
            if dropped_lifecycle:
                dropped_tool_scope = v
            elif (
                lifecycle_landed_via_run_command
                and raw_lifecycle not in _TOOL_CONTEXT_LIFECYCLES
            ):
                dropped_tool_scope = v

    # Collect the operator's per-archetype body fields when their
    # parent action was dropped. The conversational vocabulary cannot
    # store these (no slot in the draft) so the bytes would silently
    # vanish; surfacing them in the assistant summary at least lets
    # the operator copy-paste back into the next reply.
    dropped_payload: dict[str, str] = {}
    if dropped_action:
        for k in _DROPPED_ACTION_BODY_KEYS_PER_ACTION.get(dropped_action, ()):
            v = ws.get(k)
            if isinstance(v, str) and v.strip():
                dropped_payload[k] = v.strip()

    # 1. project wizard state → draft.
    from_wizard = _draft_from_wizard_state(ws)

    # 2. sanitize the raw-editor draft (drops `gate_binary`, `type`,
    #    `sentinel_re`, `on_signature_invalid`; coerces subtrees).
    sanitized = _sanitize_draft_so_far(di) if di else {}

    # 3. overlay the wizard-derived draft on top of the sanitized raw
    #    editor draft. The wizard surface is the more recent author
    #    intent so its values win on conflict.
    merged = _merge_drafts(sanitized, from_wizard)

    # Language detection: locale_hint (forwarded from the dashboard)
    # wins over draft-content heuristic so a Korean-locale operator
    # gets a Korean seed even when the draft body is entirely English.
    if locale_hint == "ko":
        ko = True
    elif locale_hint == "en":
        ko = False
    else:
        ko = _detect_korean(history=None, draft=merged)

    # 4. compute missing fields + canonical questions for the first
    #    turn. Mirrors the fallback branch of `step_compile`.
    missing = _missing_fields_for_draft(merged)
    questions = _questions_for_missing(merged, missing, ko)

    # 5. summary line.
    assistant_message = _summarize_so_far(
        merged, ko=ko,
        dropped_action=dropped_action, dropped_kind=dropped_kind,
        dropped_lifecycle=dropped_lifecycle,
        dropped_tool_scope=dropped_tool_scope,
        dropped_payload=dropped_payload or None,
        origin=origin,
    )

    # 6. ready_to_save: only if missing is empty AND validator agrees.
    needs_more = len(missing) > 0
    ready_to_save = False
    if not needs_more:
        ok, _ = _draft_passes_ir_validator(merged)
        ready_to_save = ok
        if not ok:
            needs_more = True

    wire_draft: dict[str, Any] | None = merged if merged else None

    # Feasibility check at the wizard -> conversational handoff seam.
    # OFFLINE: no LLM call.  Only draft-shape findings (rows 1-10) apply.
    # Mirrors the feasibility_wire shape emitted by nl_compiler_interactive.
    feasibility_wire: dict[str, Any] | None = None
    if wire_draft is not None:
        from . import feasibility as _feas  # noqa: PLC0415

        effective_runtime = runtime_id or "claude-code"
        finding = _feas.classify_draft(wire_draft, effective_runtime)
        if finding is not None:
            # Localize explanation from COPY_TABLE (ko follows locale_hint
            # / _detect_korean result already computed above).
            entry = _feas.COPY_TABLE.get(finding.code)
            if entry:
                _en, _ko_str, _alt = entry
                base = _ko_str if ko else _en
                explanation = base + (" " + _alt if _alt else "")
            else:
                explanation = finding.code

            # Build alternatives per the handoff-parity rule:
            #   codex silent_noop codes -> [keep_for_cc, magi_agent_handoff]
            #   all other findings     -> []
            if finding.code in _feas._CODEX_SILENT_NOOP_CODES:
                # Build a minimal, scrubbed intent_summary from the draft.
                trig_ = wire_draft.get("trigger") or {}
                _event = trig_.get("event") if isinstance(trig_, dict) else None
                _matcher = trig_.get("matcher") if isinstance(trig_, dict) else None
                _action = wire_draft.get("action")
                parts = [p for p in [_event, _matcher, _action] if p]
                raw_summary = ("authored in the wizard: " + "/".join(parts)) if parts else "authored in the wizard"
                intent_summary = _to_plain_language(raw_summary)[:200]
                alternatives: list[dict[str, Any]] = [
                    {"kind": "keep_for_cc"},
                    _feas.handoff_cta(intent_summary, ko=ko),
                ]
            else:
                alternatives = []

            feasibility_wire = {
                "runtime_id": effective_runtime,
                "class": finding.cls.value,
                "code": finding.code,
                "explanation": explanation,
                "alternatives": alternatives,
            }

    return {
        "assistant_message": assistant_message,
        "draft": wire_draft,
        "missing_fields": list(missing),
        "questions": [q.to_dict() for q in questions],
        "needs_more": needs_more,
        "ready_to_save": ready_to_save,
        "feasibility": feasibility_wire,
    }


__all__ = [
    "HandoffContextError",
    "build_handoff_turn",
]


def _assert_run_command_allowlists_match() -> None:
    """Module-load assertion: keep the `_RUN_COMMAND_WIZARD_KEYS`
    allowlist, the per-field branches in `_project_run_command_state`,
    the canonical lifecycle map, and the KO/EN label tables honest.

    Drift risk this catches:

    1. `_RUN_COMMAND_WIZARD_KEYS` is a documented allowlist. The
       per-field branches in `_project_run_command_state` read
       `state.get("runCommandFoo")` directly. A future contributor who
       widens one without widening the other could break the
       allowlist's "anything outside this set is silently ignored"
       contract. We walk the source of the projection function and
       assert every `state.get("runCommandFoo")` literal it reads is
       in the constant — mirrors `_assert_sanitizer_matches_allowlists`
       in nl_compiler_interactive.py.

    2. `_RUN_COMMAND_LIFECYCLE_TO_EVENT` is the canonical source of
       truth for wider-lifecycle handling. Both KO and EN label tables
       must cover every slug there so the summary line never falls
       back to the generic "at that moment" phrasing on a slug the
       canonical map names. A future page.tsx row added without
       widening the label tables trips here.

    3. `_script_name` MUST be dropped by `_sanitize_draft_so_far` on
       the save round-trip. The docstring promised the leading
       underscore would handle that; the actual mechanism is that
       `_script_name` is not on the run_command allowlist. We probe
       the sanitizer to make sure that contract still holds.
    """
    # Probe 1: walk the projection function's source.
    src = inspect.getsource(_project_run_command_state)
    literals = set(_re_check.findall(r"state\.get\(\s*[\"'](runCommand[A-Za-z0-9_]+)[\"']", src))
    extra = literals - _RUN_COMMAND_WIZARD_KEYS
    if extra:
        raise AssertionError(
            "_project_run_command_state reads run_command state keys "
            f"outside _RUN_COMMAND_WIZARD_KEYS: {sorted(extra)}"
        )
    missing = _RUN_COMMAND_WIZARD_KEYS - literals
    if missing:
        raise AssertionError(
            "_RUN_COMMAND_WIZARD_KEYS declares run_command state keys "
            "the projection function does not read: "
            f"{sorted(missing)}"
        )
    # Probe 2: KO/EN label table coverage for the canonical event map.
    canonical = set(_RUN_COMMAND_LIFECYCLE_TO_EVENT.keys())
    missing_ko = canonical - set(_RUN_COMMAND_LIFECYCLE_LABEL_KO.keys())
    missing_en = canonical - set(_RUN_COMMAND_LIFECYCLE_LABEL_EN.keys())
    if missing_ko:
        raise AssertionError(
            "_RUN_COMMAND_LIFECYCLE_LABEL_KO missing rows: "
            f"{sorted(missing_ko)}"
        )
    if missing_en:
        raise AssertionError(
            "_RUN_COMMAND_LIFECYCLE_LABEL_EN missing rows: "
            f"{sorted(missing_en)}"
        )
    # Probe 3: `_script_name` is dropped by the sanitizer on the save
    # round-trip. The docstring on `_project_run_command_state`
    # historically claimed the underscore prefix was the mechanism; the
    # ACTUAL mechanism is that `_script_name` is not on the
    # run_command allowlist. Probe both directions.
    probe = {
        "id": "probe-id",
        "version": "0.1",
        "trigger": {"event": "Stop", "matcher": "*"},
        "type": "run_command",
        "command": "echo hi",
        "_script_name": "should-be-dropped",
    }
    cleaned = _sanitize_draft_so_far(probe)
    if "_script_name" in cleaned:
        raise AssertionError(
            "_sanitize_draft_so_far must drop `_script_name` from the "
            "save round-trip; the leading underscore is a marker only, "
            "not a language-level passthrough mechanism."
        )


_assert_run_command_allowlists_match()
