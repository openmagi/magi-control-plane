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
from typing import Any

from .nl_compiler_interactive import (
    _LIFECYCLE_TO_EVENT,
    _ON_MISSING_VALUES,
    _REQUIRES_KINDS,
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


def _draft_from_wizard_state(state: dict[str, Any]) -> dict[str, Any]:
    """Project the wizard's URL state into a draft IR shape.

    Reuses `_apply_answer_to_draft`'s per-field allowlists so a value
    that survives the projection has already passed canonical validation.
    Anything the wizard can hold but the conversational vocabulary
    cannot model (rewriter spec, context_injection template, the long
    tail of D58 lifecycles) is silently dropped — the operator will see
    the collapse reflected in the assistant summary so they know what
    landed.
    """
    draft: dict[str, Any] = {}

    # Lifecycle. The conversational vocab supports the 3-bucket
    # mapping in `_LIFECYCLE_TO_EVENT`. Anything else degrades to
    # "still missing" so the assistant re-asks.
    lifecycle = state.get("lifecycle")
    if isinstance(lifecycle, str) and lifecycle in _LIFECYCLE_TO_EVENT:
        _apply_answer_to_draft(draft, "lifecycle", lifecycle)

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
    cond_kind = state.get("conditionKind")
    if isinstance(cond_kind, str):
        if cond_kind in _REQUIRES_KINDS:
            _apply_answer_to_draft(draft, "requires", cond_kind)
            # Body, per-kind.
            if cond_kind == "regex":
                pat = state.get("pattern")
                if isinstance(pat, str) and pat.strip():
                    _apply_answer_to_draft(draft, "requires_body", pat.strip())
            elif cond_kind == "llm_critic":
                crit = state.get("llmCriterion")
                if isinstance(crit, str) and crit.strip():
                    _apply_answer_to_draft(draft, "requires_body", crit.strip())
            elif cond_kind == "shacl":
                ttl = state.get("shaclTtl")
                if isinstance(ttl, str) and ttl.strip():
                    _apply_answer_to_draft(draft, "requires_body", ttl.strip())
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
    action = state.get("action")
    if isinstance(action, str):
        if action in _ON_MISSING_VALUES:
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
    """
    out: dict[str, Any] = copy.deepcopy(base) if base else {}
    if not isinstance(overlay, dict):
        return out
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
            if v:
                out["requires"] = v
            continue
        if v in (None, "", []):
            continue
        out[k] = v
    return out


def _summarize_so_far(
    draft: dict[str, Any],
    *,
    ko: bool,
    dropped_action: str | None,
    dropped_kind: str | None,
) -> str:
    """Plain-language single-paragraph summary of the merged draft.

    Used as the first assistant turn on the conversational shell after a
    handoff. Mirrors the brief's "so far: …" framing. Never names an
    internal field, never emits an internal vocabulary token (the
    `_to_plain_language` scrubber runs at the end as defense in depth).
    """
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
            pieces.append(f"id: {pid}")

    if not pieces:
        return (
            "지금까지 입력하신 내용이 거의 없네요. 처음부터 같이 채워볼게요."
            if ko else
            "It looks like you haven't filled much in yet. Let's pick up from the start together."
        )

    if ko:
        head = "지금까지 작성하신 내용을 이어서 받았어요:"
    else:
        head = "Continuing from where you were. Here is what you had so far:"
    body = "\n".join(f"  - {p}" for p in pieces)

    note: str | None = None
    if dropped_action and dropped_kind:
        if ko:
            note = (
                f"\n\n참고: '{dropped_action}' 동작과 '{dropped_kind}' 조건은 "
                "대화형에서 다루지 못해서 가까운 기본값으로 정리했어요."
            )
        else:
            note = (
                f"\n\nNote: the '{dropped_action}' action and the "
                f"'{dropped_kind}' check do not map cleanly to the "
                "chat surface, so they were collapsed to the closest "
                "default."
            )
    elif dropped_action:
        if ko:
            note = (
                f"\n\n참고: '{dropped_action}' 동작은 대화형에서 다루지 못해서 "
                "기본값으로 정리했어요."
            )
        else:
            note = (
                f"\n\nNote: the '{dropped_action}' action does not map "
                "cleanly to the chat surface, so it was collapsed to "
                "the closest default."
            )
    elif dropped_kind:
        if ko:
            note = (
                f"\n\n참고: '{dropped_kind}' 조건은 대화형에서 다루지 못해서 "
                "가까운 기본값으로 정리했어요."
            )
        else:
            note = (
                f"\n\nNote: the '{dropped_kind}' check does not map "
                "cleanly to the chat surface, so it was collapsed to "
                "the closest default."
            )

    tail_q = (
        "\n\n남은 부분을 같이 채워볼까요?"
        if ko else
        "\n\nLet's fill in what's still missing."
    )
    return _to_plain_language(head + "\n" + body + (note or "") + tail_q)


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


def build_handoff_turn(
    *,
    wizard_state: dict[str, Any] | None,
    draft_ir: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the first conversational turn for a seeded handoff.

    See module docstring for the wire shape contract. Raises
    `HandoffContextError` (maps to 422 at the route) on malformed input.
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

    # Track ahead of the merge so the summary can mention archetype
    # collapses (input_rewrite / inject_context / strip → audit; long
    # tail of wizard-only condition kinds → none).
    raw_action = ws.get("action")
    dropped_action: str | None = None
    if isinstance(raw_action, str) and raw_action and raw_action not in _ON_MISSING_VALUES:
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

    # 1. project wizard state → draft.
    from_wizard = _draft_from_wizard_state(ws)

    # 2. sanitize the raw-editor draft (drops `gate_binary`, `type`,
    #    `sentinel_re`, `on_signature_invalid`; coerces subtrees).
    sanitized = _sanitize_draft_so_far(di) if di else {}

    # 3. overlay the wizard-derived draft on top of the sanitized raw
    #    editor draft. The wizard surface is the more recent author
    #    intent so its values win on conflict.
    merged = _merge_drafts(sanitized, from_wizard)

    ko = _detect_korean(history=None, draft=merged)

    # 4. compute missing fields + canonical questions for the first
    #    turn. Mirrors the fallback branch of `step_compile`.
    missing = _missing_fields_for_draft(merged)
    questions = _questions_for_missing(merged, missing, ko)

    # 5. summary line.
    assistant_message = _summarize_so_far(
        merged, ko=ko,
        dropped_action=dropped_action, dropped_kind=dropped_kind,
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

    return {
        "assistant_message": assistant_message,
        "draft": wire_draft,
        "missing_fields": list(missing),
        "questions": [q.to_dict() for q in questions],
        "needs_more": needs_more,
        "ready_to_save": ready_to_save,
    }


__all__ = [
    "HandoffContextError",
    "build_handoff_turn",
]
