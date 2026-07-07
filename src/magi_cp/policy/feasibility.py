"""Deterministic feasibility classifier for magi-cp policy drafts.

Two entry points:

  classify_draft(draft, runtime_id) -> FeasibilityFinding | None
      Draft-shape checks (decision table rows 1-10).
      Returns None when the draft is native on the given runtime (no finding).
      Never raises on a partial draft; returns None if a rule cannot be
      evaluated yet.

  classify_intent(user_text) -> IntentFinding | None
      Intent-lexicon checks (rows 11-16).
      Returns None when no known non-native intent pattern matched.
      High-precision; false negatives are acceptable.

Neither entry point calls an LLM.  All deterministic data sources are
imported lazily inside the entry-point functions (one-way dependency, no
import back-edge from this module).

COPY_TABLE maps each finding code to operator-facing EN and KO explanations
plus an in-bounds alternative sentence where applicable.  This is the ONLY
place feasibility copy lives - code and copy must not drift.
"""

from __future__ import annotations

import dataclasses
import enum
import re
import urllib.parse


# ---------------------------------------------------------------------------
# Enforce-intent lexicon (single source of truth; review.py aliases this)
# ---------------------------------------------------------------------------
# High-precision block-family verbs. Single source of truth; review.py
# aliases this. AF-2 (P1-5): bare "stop" was dropped because it
# false-positives on the Stop hook name ("log it at the stop event"); the
# remaining verbs are unambiguous enforce requests.
ENFORCE_INTENT_RE = re.compile(
    r"\b(?:block|deny|prevent|forbid|reject|refuse|require|must not|hold)\b"
    r"|차단|막아|금지|거부|막기|못하게|하면\s*안|중단|멈춰|멈추",
    re.IGNORECASE,
)

# AF-2 (P1-4/P1-5): negated-BLOCK cue. Shared by the extractor (to
# suppress a false block extraction) and by `classify_silent_downgrade`
# (so the downgrade banner never fires on "don't block, just record").
# The negation must actually target a BLOCK verb: "don't block" is a
# block-negation, but "block it, don't just record" negates the record,
# not the block, so it must NOT match. The English arm requires a block
# verb within two words of the negation; the Korean arms match the
# block-stem-plus-negation shapes ("차단하지 말", "차단은 하지 마",
# "막지 마").
BLOCK_NEGATION_RE = re.compile(
    r"(?:don'?t|do\s+not|never)\s+(?:\w+\s+){0,2}?"
    r"(?:block|deny|refuse|forbid|prevent|reject)"
    r"|(?:차단|막|금지)\S*\s*(?:하지\s*마|하지\s*말|말고|안)"
    r"|차단하지|막지\s*마|금지하지",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Classification vocabulary
# ---------------------------------------------------------------------------


class FeasibilityClass(enum.Enum):
    """Classifies a policy draft's enforcement capability on a target runtime."""

    native = "native"
    # Fires and enforces as authored.

    degraded = "degraded"
    # Fires via a runtime shim with weaker semantics than on Claude Code.

    silent_noop = "silent_noop"
    # Saves green in a coverage report but fires zero times on the target
    # runtime.  The operator will see no enforcement effect.

    magi_agent_only = "magi-agent-only"
    # Expressible as a Magi Agent behaviour, not as a cp hook policy.

    not_expressible = "not-expressible"
    # Not expressible in either product today.


@dataclasses.dataclass(frozen=True)
class FeasibilityFinding:
    """Result of a draft-shape feasibility check (rows 1-10).

    Attributes:
        cls    - enforcement classification on the given runtime
        code   - stable machine token, never localized (see COPY_TABLE)
        detail - key/value context for message rendering; keys are short
                 identifiers, values are the offending token (e.g.
                 ``{"event": "Stop"}``, ``{"matcher": "Read"}``)
    """

    cls: FeasibilityClass
    code: str
    detail: dict[str, str] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class IntentFinding:
    """Result of an intent-lexicon scan (rows 11-16).

    Attributes:
        cls    - enforcement classification
        code   - stable machine token, never localized (see COPY_TABLE)
        detail - empty by default; may carry {"matched_phrase": "..."} for
                 debugging
    """

    cls: FeasibilityClass
    code: str
    detail: dict[str, str] = dataclasses.field(default_factory=dict)


# ---------------------------------------------------------------------------
# Operator-facing copy table (single source of truth for finding messages)
# ---------------------------------------------------------------------------
# Each entry: (en, ko, alternative)
# ``alternative`` is the in-bounds approach the operator should use instead;
# None where there is no direct substitute.

COPY_TABLE: dict[str, tuple[str, str, str | None]] = {
    # Row 1 - inject_context on excluded events
    "cc_context_channel_excluded": (
        "Injected context is not delivered on this event - the runtime drops "
        "it silently. Inject context on a supported event such as "
        "UserPromptSubmit or PreToolUse to reach the model.",
        "이 이벤트에서는 주입한 컨텍스트가 전달되지 않고 런타임이 조용히 "
        "무시합니다. UserPromptSubmit 또는 PreToolUse 같은 지원 이벤트에서 "
        "컨텍스트를 주입하세요.",
        "Inject context on UserPromptSubmit or SessionStart instead.",
    ),
    # Row 5 - event not in live set on Codex
    "codex_event_not_live": (
        "Codex does not fire hooks for this lifecycle event. "
        "The policy will save successfully but enforce zero times on Codex. "
        "Author on a live Codex event (PreToolUse, PostToolUse, SessionStart, "
        "UserPromptSubmit, Stop, SubagentStart, SubagentStop, PreCompact, "
        "PostCompact, PermissionRequest) or keep for Claude Code only.",
        "Codex는 이 라이프사이클 이벤트에 훅을 발생시키지 않습니다. "
        "정책은 저장되지만 Codex에서 적용 횟수가 0이 됩니다. "
        "Codex 라이브 이벤트(PreToolUse, PostToolUse, SessionStart, "
        "UserPromptSubmit, Stop, SubagentStart, SubagentStop, PreCompact, "
        "PostCompact, PermissionRequest)를 사용하거나 Claude Code 전용으로 "
        "유지하세요.",
        "Author on PreToolUse, PostToolUse, or UserPromptSubmit instead.",
    ),
    # Row 6 - SessionEnd on Codex
    "codex_no_session_end": (
        "Codex has no SessionEnd event. "
        "The policy fires via the Stop event shim with slightly weaker "
        "semantics (Stop is called once per session; SessionEnd is a separate "
        "lifecycle hook on Claude Code). "
        "Enforcement is active but the exact trigger timing differs.",
        "Codex에는 SessionEnd 이벤트가 없습니다. "
        "Stop 이벤트 심(shim)을 통해 약간 다른 시맨틱으로 실행됩니다. "
        "적용은 활성화되지만 트리거 타이밍이 다를 수 있습니다.",
        "Consider authoring on Stop directly for exact cross-runtime semantics.",
    ),
    # Row 7 - SubagentStart/Stop internal gap on Codex
    "codex_internal_subagent_gap": (
        "Codex's internal subagent reviewers may not receive this hook. "
        "The parent-side spawn_agent PreToolUse and PostToolUse mirrors are "
        "emitted, but hooks targeting SubagentStart or SubagentStop may not "
        "fire on every Codex subagent invocation.",
        "Codex의 내부 서브에이전트 리뷰어가 이 훅을 받지 못할 수 있습니다. "
        "부모 측 spawn_agent PreToolUse/PostToolUse 미러는 실행되지만 "
        "SubagentStart/SubagentStop 훅이 모든 Codex 서브에이전트 호출에서 "
        "발생하지 않을 수 있습니다.",
        "Author on PreToolUse with matcher Task/spawn_agent for the "
        "spawn decision instead.",
    ),
    # Row 8 - ask action downgrades to block on Codex
    "codex_ask_downgrades_to_block": (
        "The 'ask' action on this event is not supported by Codex. "
        "It will be emitted as a 'block' (deny) instead. "
        "The operator will see a harder stop rather than an interactive "
        "confirmation prompt.",
        "이 이벤트에서 'ask' 액션은 Codex에서 지원되지 않습니다. "
        "'block'(거부)으로 변환되어 실행됩니다. "
        "대화형 확인 프롬프트 대신 강제 차단이 발생합니다.",
        "Use 'block' explicitly if a hard stop is acceptable, or target a "
        "Claude Code session instead.",
    ),
    # Row 3 - PreToolUse + silent-skip tool on Codex
    "codex_matcher_inert": (
        "This tool has no direct equivalent in Codex. "
        "A before-a-tool-runs rule targeting this tool fires zero times on "
        "Codex - the policy is saved but unenforced. "
        "Codex dispatches reads as shell sub-actions rather than "
        "discrete tool events.",
        "이 툴은 Codex에서 직접 대응하는 항목이 없습니다. "
        "이 툴을 대상으로 한 도구 실행 전 규칙은 Codex에서 0번 실행됩니다. "
        "정책은 저장되지만 적용되지 않습니다.",
        "Target Codex shell and file operations by their Codex tool names "
        "instead.",
    ),
    # Row 10 - matrix illegal triple
    "matrix_illegal_triple": (
        "This combination of when the check runs, what it applies to, and "
        "what it does is not supported. The policy cannot be authored as "
        "described.",
        "이 검사가 실행되는 시점, 적용 대상, 동작의 조합은 지원되지 않습니다. "
        "설명하신 대로는 정책을 만들 수 없습니다.",
        None,
    ),
    # GAP-A / AF-5 - operator asked to enforce (block or pause for
    # approval), draft records only because no enforce action is available
    # at this point in the run.
    "enforce_downgraded_to_audit": (
        "You asked to block or pause for approval, but neither is available "
        "at this point in the run. The draft uses audit instead: it records "
        "every failure but does not stop anything. To actually hold delivery "
        "on this check, author it as a Magi Agent gate.",
        "차단이나 승인 대기를 요청하셨지만 이 시점에서는 둘 다 사용할 수 "
        "없습니다. 초안은 audit(기록)으로 작성되어 실패를 기록만 하고 실제로 "
        "막지는 않습니다. 실제 전달을 막으려면 Magi Agent 게이트로 작성하세요.",
        "Keep audit here to get a record of every failure.",
    ),
    # Row 11 - evidence catalog (Magi Agent only)
    "magi_evidence_catalog": (
        "Full evidence-ledger grounding (verifying that tests ran, diffs were "
        "inspected, etc.) is a Magi Agent capability, not a cp hook. "
        "Use the Magi Agent evidence catalog and evidence_gate policy type "
        "to require runtime-collected evidence before final delivery.",
        "전체 증거 원장 검증(테스트 실행 여부, diff 검사 등)은 "
        "cp 훅이 아닌 Magi Agent 기능입니다. "
        "Magi Agent 증거 카탈로그와 evidence_gate 정책 유형을 사용하세요.",
        "Use an evidence_gate policy with the built-in verifiers "
        "(privilege_scan, test_run, git_diff, code_diagnostics, "
        "commit_checkpoint).",
    ),
    # Row 12 - per-claim source citations (Magi Agent only)
    "magi_source_citation": (
        "Inline per-claim source citations are a Magi Agent delivery feature. "
        "A cp hook cannot inject citations into an already-streamed answer. "
        "Magi Agent's source registry emits citation markers during the turn.",
        "인라인 출처 인용은 Magi Agent 전달 기능입니다. "
        "cp 훅은 이미 스트리밍된 답변에 인용을 삽입할 수 없습니다. "
        "Magi Agent의 소스 레지스트리를 사용하세요.",
        "Enable Magi Agent's source citation policy (citation_verify at Stop "
        "audits; inline citations require the agent-side source registry).",
    ),
    # Row 13 - cross-session state (Magi Agent only)
    "cross_session_state": (
        "Cross-session memory and historical state are managed by the Magi "
        "Agent memory layer, not cp hooks. "
        "Hooks execute per-event and do not persist context across sessions.",
        "세션 간 메모리와 이력 상태는 cp 훅이 아닌 "
        "Magi Agent 메모리 레이어가 관리합니다. "
        "훅은 이벤트별로 실행되며 세션 간 컨텍스트를 유지하지 않습니다.",
        "Use Magi Agent memory skills (MEMORY.md, hipocampus) for "
        "cross-session context.",
    ),
    # Row 14 - rate-limit window (not expressible)
    "rate_limit_window": (
        "Rate-limiting by time window (per-minute, per-hour) is not "
        "expressible as a cp hook policy today. "
        "Hooks fire per event and do not maintain a rolling counter.",
        "시간 창(분당, 시간당) 속도 제한은 현재 cp 훅 정책으로 "
        "표현할 수 없습니다. "
        "훅은 이벤트별로 실행되며 롤링 카운터를 유지하지 않습니다.",
        None,
    ),
    # Row 15 - token/cost budget (not expressible)
    "token_budget": (
        "Token or cost budgets are not expressible as a cp hook policy today. "
        "Hooks do not have access to session-level token accounting.",
        "토큰 또는 비용 한도는 현재 cp 훅 정책으로 표현할 수 없습니다. "
        "훅은 세션 수준의 토큰 계산에 접근할 수 없습니다.",
        None,
    ),
    # Row 16 - retroactive undo (not expressible)
    "retroactive_undo": (
        "Retroactively undoing a tool call after it has executed is not "
        "expressible as a cp hook policy today. "
        "Use PreToolUse block to prevent execution before it starts.",
        "실행 후 소급적으로 툴 호출을 되돌리는 것은 현재 cp 훅 정책으로 "
        "표현할 수 없습니다. "
        "실행 전 차단에는 PreToolUse block을 사용하세요.",
        "Use PreToolUse with block action to stop execution before it starts.",
    ),
}


# ---------------------------------------------------------------------------
# Magi Agent handoff CTA copy (EN / KO, with-route / without-route)
# Lives here so all operator copy is co-located with COPY_TABLE.
# ---------------------------------------------------------------------------

# CTA text keyed by (ko: bool, route_present: bool).
# EN/KO outer, route-present inner.
_HANDOFF_CTA: dict[tuple[bool, bool], str] = {
    (False, True): (
        "Author this in Magi Agent Customize to get this capability."
    ),
    (False, False): (
        "Author this behaviour in Magi Agent Customize to get this capability."
    ),
    (True, True): (
        "Magi Agent Customize에서 이 동작을 설정하면 이 기능을 사용할 수 있습니다."
    ),
    (True, False): (
        "Magi Agent Customize에서 이 동작을 설정하면 이 기능을 사용할 수 있습니다."
    ),
}

# Codes that belong to magi_agent_only and get a handoff CTA.
_MAGI_AGENT_ONLY_CODES: frozenset[str] = frozenset({
    "magi_evidence_catalog",
    "magi_source_citation",
    "cross_session_state",
})

# Codex silent-noop draft codes that get both keep_for_cc + handoff.
_CODEX_SILENT_NOOP_CODES: frozenset[str] = frozenset({
    "codex_matcher_inert",
    "codex_event_not_live",
})


def magi_agent_route(intent_summary: str) -> str | None:
    """Return a deep-link URL into the Magi Agent Customize flow, or None.

    When MAGI_CP_MAGI_AGENT_CONSOLE_URL is unset (the default), returns None
    so callers emit a text-only CTA rather than a dead link.

    The ``?intent=`` query parameter carries a URL-encoded plain-language
    summary of what the operator intended.  The magi-agent side may use it
    to pre-populate the compose widget; today it silently ignores unknown
    query params.

    Args:
        intent_summary - plain-language, scrubbed one-line summary of the
                         operator intent (jargon already stripped).
    """
    from ..config import magi_agent_console_url  # noqa: PLC0415

    base = magi_agent_console_url()
    if base is None:
        return None
    return f"{base}/customize?intent={urllib.parse.quote(intent_summary)}"


def handoff_cta(intent_summary: str, *, ko: bool) -> dict:
    """Build a ``magi_agent_handoff`` alternatives entry.

    Returns a dict with keys ``kind``, ``route`` (may be None), ``intent_summary``,
    and ``cta`` (localized CTA string).
    """
    route = magi_agent_route(intent_summary)
    cta = _HANDOFF_CTA[(ko, route is not None)]
    return {
        "kind": "magi_agent_handoff",
        "route": route,
        "intent_summary": intent_summary,
        "cta": cta,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _scan_first(
    text: str,
    vocab: tuple[tuple[str, tuple[str, ...]], ...],
) -> str | None:
    """Lowercased substring scan over a vocab table.

    Returns the first key whose any phrase matches, or None.  Korean phrases
    are case-insensitive via str.lower() (Korean has no case but English
    entries need normalisation).
    """
    if not text:
        return None
    needle = text.lower()
    for key, phrases in vocab:
        for ph in phrases:
            if ph.lower() in needle:
                return key
    return None


# Intent lexicons (rows 11-16). Narrow, unambiguous phrases only.
# False negatives are acceptable (the prompt is the second net).
# False positives produce advisory notices that the operator can dismiss.

_INTENT_VOCAB: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Row 11 - evidence beyond the 5 wired verifiers
    (
        "magi_evidence_catalog",
        (
            "evidence ledger",
            "the tests actually ran",
            "git diff evidence",
            "grade against the evidence",
            "증거 원장",
            "테스트가 실제로",
            "실제로 실행됐는지 증거",
        ),
    ),
    # Row 12 - inline per-claim source citations (NOT bare "citation")
    (
        "magi_source_citation",
        (
            "inline citation",
            "per-claim citation",
            "cite each claim inline",
            "문장별 인용",
            "인라인 출처",
        ),
    ),
    # Row 13 - cross-session state.
    # NOTE (P1-9): bare "yesterday" can incidentally hijack an in-scope
    # request ("block the script we talked about yesterday"), but it is also
    # a genuine cross-session condition in "if it did the same thing
    # yesterday". Disambiguating needs co-occurrence-with-a-state-cue logic
    # (a design change bordering on the hijack-vs-advisory decision), so it
    # is deferred; "yesterday" stays for now.
    (
        "cross_session_state",
        (
            "across sessions",
            "yesterday",
            "if it did x before",
            "누적",
            "세션들에 걸쳐",
            "지난 세션",
            "이전 세션에서",
        ),
    ),
    # Row 14 - rate-limit window
    (
        "rate_limit_window",
        (
            "per minute",
            "5 calls per",
            "hourly cap",
            "rate limit",
            "분당",
            "시간당",
            "초당",
            "n번 이상",
        ),
    ),
    # Row 15 - token/cost budget
    (
        "token_budget",
        (
            "token budget",
            "stop after $",
            "cost budget",
            "토큰 한도",
            "비용 한도",
            "예산 초과",
        ),
    ),
    # Row 16 - retroactive undo.
    # AF-4 (P1-9): bare "retract" and "롤백" were dropped - they are command
    # nouns that fire on in-scope requests to BLOCK a rollback/retract
    # command ("git 롤백 명령 실행되면 차단"). The remaining phrases describe
    # undoing an action AFTER it ran, which is the genuinely inexpressible
    # intent.
    (
        "retroactive_undo",
        (
            "roll back the tool call after",
            "undo the edit if",
            "undo it after",
            "실행된 뒤 되돌",
            "실행 후 되돌",
            "소급 취소",
        ),
    ),
)

# Maps intent code to its FeasibilityClass.
_INTENT_CLASS: dict[str, FeasibilityClass] = {
    "magi_evidence_catalog": FeasibilityClass.magi_agent_only,
    "magi_source_citation": FeasibilityClass.magi_agent_only,
    "cross_session_state": FeasibilityClass.magi_agent_only,
    "rate_limit_window": FeasibilityClass.not_expressible,
    "token_budget": FeasibilityClass.not_expressible,
    "retroactive_undo": FeasibilityClass.not_expressible,
}


# ---------------------------------------------------------------------------
# classify_draft (rows 1-10)
# ---------------------------------------------------------------------------


def classify_draft(
    draft: dict,
    runtime_id: str | None = None,
) -> FeasibilityFinding | None:
    """Classify a policy draft for the given runtime (default: claude-code).

    Evaluates decision-table rows 1-10 in priority order; returns the first
    finding.  Returns None if the draft is native on the runtime (no issue).

    Never raises on a partial draft - returns None if a required field is
    missing for a particular rule.

    Args:
        draft       - policy draft dict (from the interactive compiler)
        runtime_id  - one of "claude-code" or "codex" (default "claude-code")
    """
    # Lazy imports - one-way dependency, avoids circular import at module load.
    from ..policy.ir import _CONTEXT_INJECTION_EXCLUDED_EVENTS  # noqa: PLC0415
    from ..policy.matrix import validate_combination  # noqa: PLC0415
    from ..runtime.codex import (  # noqa: PLC0415
        CODEX_LIVE_EVENTS,
        CODEX_SILENT_SKIP_TOOLS,
        _CC_TO_CODEX_TOOL,
    )

    rt = runtime_id or "claude-code"

    trigger = draft.get("trigger") or {}
    event: str | None = trigger.get("event") if isinstance(trigger, dict) else None
    matcher: str | None = trigger.get("matcher") if isinstance(trigger, dict) else None
    action: str | None = draft.get("action")
    draft_type: str | None = draft.get("type")

    # -- Row 1 (any runtime): inject_context on excluded events -------------
    is_context = (action == "inject_context") or (draft_type == "context_injection")
    if is_context and event and event in _CONTEXT_INJECTION_EXCLUDED_EVENTS:
        return FeasibilityFinding(
            cls=FeasibilityClass.degraded,
            code="cc_context_channel_excluded",
            detail={"event": event},
        )

    # Rows 2-8 are Codex-specific.
    if rt == "codex":
        # -- Row 6 (codex): SessionEnd rides Stop - degraded, not silent ----
        # Evaluated before the general not-live catch (Row 5) because the
        # policy DOES fire on Codex via the Stop shim, making it degraded
        # rather than a complete noop.
        if event == "SessionEnd":
            return FeasibilityFinding(
                cls=FeasibilityClass.degraded,
                code="codex_no_session_end",
                detail={"event": "SessionEnd"},
            )

        # -- Row 7 (codex): SubagentStart/Stop internal subagent gap --------
        if event in ("SubagentStart", "SubagentStop"):
            return FeasibilityFinding(
                cls=FeasibilityClass.degraded,
                code="codex_internal_subagent_gap",
                detail={"event": event},
            )

        # -- Row 8 (codex): ask action downgrades to block on block-channel -
        if (
            action == "ask"
            and event in ("PostToolUse", "UserPromptSubmit")
        ):
            return FeasibilityFinding(
                cls=FeasibilityClass.degraded,
                code="codex_ask_downgrades_to_block",
                detail={"event": event, "action": "ask"},
            )

        # -- Row 2 (codex): PreToolUse + translatable CC tool = native ------
        # These map via _CC_TO_CODEX_TOOL and DO fire on Codex.
        if event == "PreToolUse" and matcher and matcher in _CC_TO_CODEX_TOOL:
            return None

        # -- Row 3 (codex): PreToolUse + silent-skip tool = noop ------------
        # Row 4 is a special case of Row 3 (PreToolUse+WebFetch hits this).
        if event == "PreToolUse" and matcher and matcher in CODEX_SILENT_SKIP_TOOLS:
            return FeasibilityFinding(
                cls=FeasibilityClass.silent_noop,
                code="codex_matcher_inert",
                detail={"matcher": matcher},
            )

        # -- Row 5 (codex): event not in live set ---------------------------
        if event and event not in CODEX_LIVE_EVENTS:
            return FeasibilityFinding(
                cls=FeasibilityClass.silent_noop,
                code="codex_event_not_live",
                detail={"event": event},
            )

    # -- Row 10 (any runtime): matrix illegal triple ------------------------
    # Only evaluated when the draft is complete (all three fields present).
    if event and matcher and action:
        try:
            validate_combination(event, matcher, action)
        except ValueError as exc:
            return FeasibilityFinding(
                cls=FeasibilityClass.not_expressible,
                code="matrix_illegal_triple",
                detail={"reason": str(exc)},
            )

    return None


# ---------------------------------------------------------------------------
# classify_intent (rows 11-16)
# ---------------------------------------------------------------------------


def classify_intent(user_text: str) -> IntentFinding | None:
    """Scan user text for known non-native intent patterns (rows 11-16).

    High-precision lexicon; false negatives are acceptable (the LLM prompt
    is the second safety net).  False positives produce advisory notices
    the operator can dismiss.

    Returns None when no pattern matched (including empty input).
    """
    code = _scan_first(user_text, _INTENT_VOCAB)
    if code is None:
        return None
    cls = _INTENT_CLASS[code]
    return IntentFinding(cls=cls, code=code, detail={})


# ---------------------------------------------------------------------------
# classify_silent_downgrade (REV-PR-1, GAP-A)
# ---------------------------------------------------------------------------


def classify_silent_downgrade(
    user_text: str,
    draft: dict,
) -> FeasibilityFinding | None:
    """Detect an enforce-intent draft that records only (audit) where no
    enforce action is legal.

    Returns a ``degraded`` finding (code ``enforce_downgraded_to_audit``)
    iff ALL of:

      1. the draft triple (event, matcher, action) is complete,
      2. the applied action is ``audit`` (records only),
      3. the operator's text asks to enforce (``ENFORCE_INTENT_RE``),
      4. NEITHER ``block`` NOR ``ask`` is matrix-legal at (event, matcher).

    When an enforce action IS legal at the triple, this returns None: that
    case is review.py's advisory territory (its advice is non-circular
    there) plus the REV-PR-3 block-restore. Deterministic, no LLM, never
    raises on a partial draft.
    """
    trigger = draft.get("trigger") or {}
    event = trigger.get("event") if isinstance(trigger, dict) else None
    matcher = trigger.get("matcher") if isinstance(trigger, dict) else None
    action = draft.get("action")
    if not (event and matcher and action):
        return None
    if action != "audit":
        return None
    text = user_text or ""
    if not ENFORCE_INTENT_RE.search(text):
        return None
    # AF-2 (P1-5): "차단하지 말고 기록만" asks for the OPPOSITE of enforcement;
    # the enforce token is a negated false positive. Do not claim the
    # operator asked to block.
    if BLOCK_NEGATION_RE.search(text):
        return None

    # Lazy import - one-way dependency, avoids a cycle at module load.
    from ..policy.matrix import validate_combination  # noqa: PLC0415

    for cand in ("block", "ask"):
        try:
            validate_combination(event, matcher, cand)
        except ValueError:
            continue
        # An enforce action is legal here - not a silent downgrade.
        return None
    return FeasibilityFinding(
        cls=FeasibilityClass.degraded,
        code="enforce_downgraded_to_audit",
        detail={"event": event, "applied": "audit"},
    )


def movable_enforce_events(draft: dict) -> tuple[str, ...]:
    """Descriptor-driven "move it earlier" steer for an enforce downgrade.

    For an evidence draft whose first requirement is a wired verifier step,
    return the sorted set of OTHER lifecycle events where that verifier can
    fire AND ``block`` is matrix-legal. Empty for a single-lifecycle
    verifier (e.g. citation_verify, Stop only), a non-step draft, or a
    missing descriptor. Never raises.
    """
    trigger = draft.get("trigger") or {}
    current_event = trigger.get("event") if isinstance(trigger, dict) else None
    requires = draft.get("requires") or []
    if not isinstance(requires, list) or not requires:
        return ()
    first = requires[0]
    if not isinstance(first, dict):
        return ()
    step = first.get("step")
    if first.get("kind") != "step" or not isinstance(step, str) or not step:
        return ()

    from ..policy.matrix import LEGAL_COMBINATIONS, MatcherClass  # noqa: PLC0415
    from ..verifier.descriptors import get_descriptor  # noqa: PLC0415

    descriptor = get_descriptor(step)
    if descriptor is None:
        return ()
    _CLASS_MAP = {
        "tool": MatcherClass.tool,
        "no_tool": MatcherClass.wildcard,
        "final": MatcherClass.wildcard,
    }
    out: set[str] = set()
    for spec in descriptor.get("triggers") or []:
        if not isinstance(spec, dict):
            continue
        ev = spec.get("event")
        mapped = _CLASS_MAP.get(spec.get("matcher_class"))
        if not isinstance(ev, str) or mapped is None or ev == current_event:
            continue
        if (ev, mapped, "block") in LEGAL_COMBINATIONS:
            out.add(ev)
    return tuple(sorted(out))


# ---------------------------------------------------------------------------
# render_capability_boundary
# ---------------------------------------------------------------------------

def _wired_verifier_steps() -> tuple[str, ...]:
    """The verifier step names ACTUALLY registered in this cp deployment.

    AF-3 (P1-8): the capability boundary previously advertised a hardcoded
    list (test_run, git_diff, code_diagnostics, commit_checkpoint) that cp
    does NOT register - those are magi-agent verifiers. A policy naming one
    of them reported "Draft is ready" then 422'd at Save. Deriving the list
    from the descriptor registry keeps the boundary truthful and drift-free.
    """
    try:
        from ..verifier.descriptors import all_descriptors  # noqa: PLC0415
        steps = sorted(
            d["step"] for d in all_descriptors()
            if isinstance(d, dict) and isinstance(d.get("step"), str) and d["step"]
        )
    except Exception:  # pragma: no cover - defensive; registry always present
        return ()
    return tuple(steps)

# Inert read-family tool names used in the Codex capability boundary text.
_CODEX_INERT_READ_TOOLS: tuple[str, ...] = (
    "Read",
    "Grep",
    "Glob",
    "WebFetch",
    "WebSearch",
    "NotebookRead",
)


def render_capability_boundary(runtime_id: str) -> str:
    """Return the capability boundary prompt text for the given runtime.

    Describes what the policy system can and cannot enforce, in plain
    operator-facing language.  PR-4 injects this into the system prompt.

    No internal jargon: event names are fine (they are hook identifiers that
    operators need to author on), but terms like "regex", "shacl", "matcher",
    "lifecycle" do not appear in the operator-facing sections.

    Args:
        runtime_id - "claude-code" or "codex"
    """
    # Lazy import for the excluded-events set.
    from ..policy.ir import _CONTEXT_INJECTION_EXCLUDED_EVENTS  # noqa: PLC0415

    excluded_sorted = sorted(_CONTEXT_INJECTION_EXCLUDED_EVENTS)

    lines: list[str] = []

    # -- Context injection limits (all runtimes) ---------------------------
    lines.append("## Context injection - events where additionalContext is not delivered")
    lines.append("")
    lines.append(
        "On the following events the inject_context action drops any "
        "additionalContext silently. Use UserPromptSubmit or PreToolUse "
        "to reach the model with injected context:"
    )
    for ev in excluded_sorted:
        lines.append(f"  - {ev}")
    lines.append("")

    # -- Codex-specific section --------------------------------------------
    if runtime_id == "codex":
        from ..runtime.codex import (  # noqa: PLC0415
            CODEX_LIVE_EVENTS,
        )
        live_sorted = sorted(CODEX_LIVE_EVENTS)
        lines.append("## Codex - events where hooks fire")
        lines.append("")
        lines.append(
            "Hooks fire on Codex only for the following events. "
            "A policy on any other event is saved but never enforced:"
        )
        for ev in live_sorted:
            lines.append(f"  - {ev}")
        lines.append("")

        lines.append("## Codex - tool operations with no direct hook event")
        lines.append("")
        lines.append(
            "The following Claude Code tool names have no direct Codex "
            "hook event. A PreToolUse hook on these tools fires zero times "
            "on Codex (file reads run as sub-actions of the shell tool):"
        )
        for t in _CODEX_INERT_READ_TOOLS:
            lines.append(f"  - {t}")
        lines.append("")

    # -- Closed expressibility contract (all runtimes) ---------------------
    lines.append("## What is expressible today")
    lines.append("")
    lines.append(
        "The following condition types are expressible as policy rules:"
    )
    lines.append("  - Pattern matching on tool input (regular-expression body check)")
    lines.append("  - AI-judge criteria (LLM evaluates a criterion against the output)")
    lines.append("  - Structured rule (field comparison, value constraint)")
    lines.append("")
    lines.append(
        "The following evidence verifiers are wired and can be required by "
        "an evidence-gate policy:"
    )
    for v in _wired_verifier_steps():
        lines.append(f"  - {v}")
    lines.append("")
    lines.append(
        "The following policy archetypes are available for structured "
        "policy authoring:"
    )
    lines.append("  - Privilege scan (block privileged operations)")
    lines.append("  - Source allowlist (restrict fetch targets)")
    lines.append("  - Prompt injection screen (detect injected instructions)")
    lines.append("  - Citation verify (require source citations at Stop)")
    lines.append("  - Evidence gate (require runtime evidence before final answer)")
    lines.append("")
    lines.append(
        "What is NOT expressible: time-window rate limits, token or cost "
        "budgets, retroactive undo of completed tool calls, or cross-session "
        "state conditions.  These require Magi Agent-layer capabilities."
    )

    return "\n".join(lines)
