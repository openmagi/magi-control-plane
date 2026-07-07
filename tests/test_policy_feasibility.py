"""PR-2: deterministic feasibility classifier.

Covers the full decision table (design 2026-07-06-magi-cp-authoring-
feasibility-runtime-awareness-design.md Section 2.3): draft-shape checks
(rows 1-10) via ``classify_draft`` and intent-lexicon checks (rows 11-16)
via ``classify_intent``. The classifier is pure and deterministic - the
LLM never computes a feasibility verdict.
"""
from __future__ import annotations

import pytest

from magi_cp.policy import feasibility as f
from magi_cp.policy.feasibility import (
    COPY_TABLE,
    classify_draft,
    classify_intent,
    render_capability_boundary,
)


def _verdict(finding) -> tuple[str, str] | None:
    return None if finding is None else (finding.cls.value, finding.code)


def _draft(event: str, matcher: str, action: str = "audit",
           **extra) -> dict:
    d = {"trigger": {"event": event, "matcher": matcher}, "action": action}
    d.update(extra)
    return d


# ── classify_draft: decision table rows 1-10 ──────────────────────────

@pytest.mark.parametrize("draft, runtime, expected", [
    # Row 1 - inject_context on an excluded event (any runtime).
    (_draft("Stop", "*", "inject_context"), "claude-code",
     ("degraded", "cc_context_channel_excluded")),
    (_draft("SubagentStop", "*", "inject_context"), "codex",
     ("degraded", "cc_context_channel_excluded")),
    # Row 2 - Bash/Edit/... on codex translate and fire (native).
    (_draft("PreToolUse", "Bash", "block"), "codex", None),
    (_draft("PreToolUse", "Edit", "block"), "codex", None),
    (_draft("PreToolUse", "Task", "block"), "codex", None),
    # Row 3 - read-family tool on codex fires zero times.
    (_draft("PreToolUse", "Read"), "codex",
     ("silent_noop", "codex_matcher_inert")),
    (_draft("PreToolUse", "Grep"), "codex",
     ("silent_noop", "codex_matcher_inert")),
    # Row 4 - the compiler's own source_allowlist default lands
    # PreToolUse+WebFetch, which is the self-inflicted silent_noop.
    (_draft("PreToolUse", "WebFetch"), "codex",
     ("silent_noop", "codex_matcher_inert")),
    # PostToolUse+WebFetch (prompt_injection_screen default) IS live on
    # codex - PostToolUse fires; inertness only bites PreToolUse.
    (_draft("PostToolUse", "WebFetch"), "codex", None),
    # Row 5 - event Codex never fires.
    (_draft("Notification", "*"), "codex",
     ("silent_noop", "codex_event_not_live")),
    (_draft("FileChanged", "*"), "codex",
     ("silent_noop", "codex_event_not_live")),
    # Row 6 - SessionEnd rides Stop on codex (inform, not warn).
    (_draft("SessionEnd", "*"), "codex",
     ("degraded", "codex_no_session_end")),
    # Row 7 - subagent lifecycle fanout gap.
    (_draft("SubagentStart", "*"), "codex",
     ("degraded", "codex_internal_subagent_gap")),
    # Row 8 - ask downgrades to block on codex at these events.
    (_draft("PostToolUse", "Bash", "ask"), "codex",
     ("degraded", "codex_ask_downgrades_to_block")),
    # Row 10 - an illegal matrix triple, any runtime.
    (_draft("Stop", "*", "block"), "claude-code",
     ("not-expressible", "matrix_illegal_triple")),
])
def test_classify_draft_rows(draft, runtime, expected) -> None:
    assert _verdict(classify_draft(draft, runtime)) == expected


def test_claude_code_is_not_subject_to_codex_rules() -> None:
    # A read-family tool that is silent_noop on codex is native on CC.
    assert classify_draft(_draft("PreToolUse", "Read"), "claude-code") is None
    assert classify_draft(_draft("Notification", "*"), "claude-code") is None
    # Bash on CC is native.
    assert classify_draft(_draft("PreToolUse", "Bash", "block"),
                          "claude-code") is None


def test_legal_triple_is_native() -> None:
    # audit on Stop is legal (unlike block on Stop).
    assert classify_draft(_draft("Stop", "*", "audit"), "claude-code") is None


def test_partial_draft_never_raises() -> None:
    # Missing action / matcher must not blow up the matrix check.
    assert classify_draft({"trigger": {"event": "PreToolUse"}},
                          "claude-code") is None
    assert classify_draft({}, "codex") is None
    assert classify_draft({"trigger": {}}, "codex") is None


def test_none_runtime_treated_as_claude_code() -> None:
    # A codex-only silent_noop must not fire when runtime is unset.
    assert classify_draft(_draft("PreToolUse", "Read"), None) is None


# ── classify_intent: decision table rows 11-16 ────────────────────────

@pytest.mark.parametrize("text, expected", [
    # Row 11 - evidence beyond the 5 wired verifiers.
    ("only if the evidence ledger shows the tests actually ran",
     ("magi-agent-only", "magi_evidence_catalog")),
    ("증거 원장을 보고 판단해줘", ("magi-agent-only", "magi_evidence_catalog")),
    # Row 12 - inline per-claim citations.
    ("cite each claim inline in the answer",
     ("magi-agent-only", "magi_source_citation")),
    ("문장별 인용을 본문에 달아줘",
     ("magi-agent-only", "magi_source_citation")),
    # Row 13 - cross-session state.
    ("block it if it did the same thing yesterday",
     ("magi-agent-only", "cross_session_state")),
    ("세션들에 걸쳐 누적된 걸 봐줘",
     ("magi-agent-only", "cross_session_state")),
    # Row 14 - rate limits.
    ("분당 5번으로 제한해줘", ("not-expressible", "rate_limit_window")),
    ("limit to 5 calls per minute", ("not-expressible", "rate_limit_window")),
    # Row 15 - token/cost budget.
    ("stop after $2 of token budget", ("not-expressible", "token_budget")),
    ("토큰 한도를 걸어줘", ("not-expressible", "token_budget")),
    # Row 16 - retroactive undo.
    ("roll back the tool call after it ran",
     ("not-expressible", "retroactive_undo")),
])
def test_classify_intent_rows(text, expected) -> None:
    assert _verdict(classify_intent(text)) == expected


@pytest.mark.parametrize("text", [
    "block bash with an RRN",           # normal privilege_scan draft
    "확인",                              # bare ambiguous verb
    "verify the citations at the end",   # citation_verify audit, not inline
    "audit every WebFetch",              # normal draft intent
    "",                                  # empty
])
def test_classify_intent_precision_guards(text) -> None:
    assert classify_intent(text) is None


# ── copy table + capability boundary ──────────────────────────────────

_ALL_CODES = [
    "cc_context_channel_excluded", "codex_matcher_inert",
    "codex_event_not_live", "codex_no_session_end",
    "codex_internal_subagent_gap", "codex_ask_downgrades_to_block",
    "matrix_illegal_triple", "magi_evidence_catalog",
    "magi_source_citation", "cross_session_state", "rate_limit_window",
    "token_budget", "retroactive_undo",
]


@pytest.mark.parametrize("code", _ALL_CODES)
def test_copy_table_has_en_and_ko_for_every_code(code) -> None:
    assert code in COPY_TABLE
    # Entry shape is (english, korean, in_bounds_alternative | None).
    entry = COPY_TABLE[code]
    assert len(entry) == 3
    en, ko, alt = entry
    assert en and isinstance(en, str)
    assert ko and isinstance(ko, str)
    # KO copy must actually contain Hangul.
    assert any("가" <= ch <= "힣" for ch in ko)
    # The alternative slot is either a non-empty string or None.
    assert alt is None or (isinstance(alt, str) and alt)


def test_capability_boundary_claude_code_lists_all_8_excluded_events() -> None:
    text = render_capability_boundary("claude-code")
    for ev in ("Elicitation", "ElicitationResult", "WorktreeCreate",
               "MessageDisplay", "Stop", "StopFailure", "SessionEnd",
               "SubagentStop"):
        assert ev in text


def test_capability_boundary_codex_adds_live_events_and_inert_tools() -> None:
    text = render_capability_boundary("codex")
    assert "PreToolUse" in text
    assert "Read" in text  # an inert read-family tool named


# ── structural: no fastapi / web imports (deterministic core) ─────────

def test_module_has_no_fastapi_or_web_imports() -> None:
    import inspect
    src = inspect.getsource(f)
    assert "fastapi" not in src.lower()
    assert "from ..cloud" not in src


# ── REV-PR-1: anti-silent-downgrade (GAP-A) ───────────────────────────
# classify_silent_downgrade fires when the operator asked to enforce
# (block/stop) but the applied draft records only (audit) at an event
# where NO enforce action is legal. movable_enforce_events computes the
# in-bounds "move it earlier" steer from the verifier descriptor.

def _step_draft(event: str, matcher: str, action: str, step: str) -> dict:
    return {
        "id": "x",
        "type": "evidence",
        "trigger": {"event": event, "matcher": matcher},
        "requires": [{"kind": "step", "step": step, "verdict": "pass"}],
        "action": action,
    }


def test_classify_silent_downgrade_block_intent_audit_at_stop() -> None:
    draft = _step_draft("Stop", "*", "audit", "citation_verify")
    finding = f.classify_silent_downgrade(
        "block the final answer when citations are missing", draft
    )
    assert finding is not None
    assert finding.cls is f.FeasibilityClass.degraded
    assert finding.code == "enforce_downgraded_to_audit"
    assert finding.detail.get("applied") == "audit"


def test_classify_silent_downgrade_korean_intent() -> None:
    draft = _step_draft("Stop", "*", "audit", "citation_verify")
    finding = f.classify_silent_downgrade("인용 없으면 최종 답변 차단해줘", draft)
    assert finding is not None
    assert finding.code == "enforce_downgraded_to_audit"


def test_classify_silent_downgrade_none_without_enforce_intent() -> None:
    draft = _step_draft("Stop", "*", "audit", "citation_verify")
    assert f.classify_silent_downgrade("경고만 남겨줘", draft) is None
    assert f.classify_silent_downgrade("just record it please", draft) is None


def test_classify_silent_downgrade_none_when_enforce_legal() -> None:
    # PreToolUse+Bash: block IS legal, so this is review.py territory,
    # not a silent downgrade. classify returns None.
    draft = _step_draft("PreToolUse", "Bash", "audit", "privilege_scan")
    assert f.classify_silent_downgrade(
        "block any shell command that contains an RRN", draft
    ) is None


def test_classify_silent_downgrade_none_on_partial_draft() -> None:
    draft = {
        "id": "x", "type": "evidence",
        "trigger": {"event": "Stop"},  # matcher missing
        "action": "audit",
    }
    assert f.classify_silent_downgrade("block it", draft) is None


def test_classify_silent_downgrade_none_when_action_not_audit() -> None:
    draft = _step_draft("Stop", "*", "block", "citation_verify")
    # action is block (illegal, caught elsewhere); not an audit downgrade.
    assert f.classify_silent_downgrade("block it", draft) is None


def test_copy_table_enforce_downgraded_bilingual() -> None:
    entry = COPY_TABLE.get("enforce_downgraded_to_audit")
    assert entry is not None
    en, ko, alt = entry
    assert en and ko and alt
    # No em-dash characters anywhere (repo house rule).
    for s in (en, ko, alt):
        assert "—" not in s


def test_movable_enforce_events_citation_verify_empty() -> None:
    draft = _step_draft("Stop", "*", "audit", "citation_verify")
    assert f.movable_enforce_events(draft) == ()


def test_movable_enforce_events_privilege_scan() -> None:
    # privilege_scan authored at Stop can move to PreToolUse (block legal).
    draft = _step_draft("Stop", "*", "audit", "privilege_scan")
    events = f.movable_enforce_events(draft)
    assert "PreToolUse" in events
    assert "Stop" not in events  # current event excluded


def test_movable_enforce_events_non_step_draft_empty() -> None:
    draft = {
        "id": "x", "type": "evidence",
        "trigger": {"event": "Stop", "matcher": "*"},
        "requires": [{"kind": "regex", "pattern": "x"}],
        "action": "audit",
    }
    assert f.movable_enforce_events(draft) == ()


def test_enforce_intent_re_matches_block_and_korean() -> None:
    assert f.ENFORCE_INTENT_RE.search("please block this")
    assert f.ENFORCE_INTENT_RE.search("인용 없으면 차단")
    assert f.ENFORCE_INTENT_RE.search("prevent the tool from running")
    assert not f.ENFORCE_INTENT_RE.search("record only please")
    assert not f.ENFORCE_INTENT_RE.search("그냥 기록만")


# ── AF-2 (P1-5): classify_silent_downgrade must not over-trigger ──────

def test_af2_downgrade_none_on_negated_enforce():
    draft = _step_draft("Stop", "*", "audit", "citation_verify")
    assert f.classify_silent_downgrade("차단하지 말고 기록만 남겨줘", draft) is None
    assert f.classify_silent_downgrade("don't block it, just record", draft) is None


def test_af2_downgrade_none_on_stop_event_name():
    draft = _step_draft("Stop", "*", "audit", "citation_verify")
    # "at the stop event" names the hook, it is not an enforce request.
    assert f.classify_silent_downgrade(
        "just log citation coverage at the stop event", draft) is None


def test_af2_enforce_intent_re_drops_bare_stop_keeps_block():
    assert f.ENFORCE_INTENT_RE.search("please block this")
    assert f.ENFORCE_INTENT_RE.search("prevent the fetch")
    assert f.ENFORCE_INTENT_RE.search("인용 없으면 차단")
    assert not f.ENFORCE_INTENT_RE.search("log it at the stop event")


def test_af2_block_negation_re_matches():
    assert f.BLOCK_NEGATION_RE.search("don't block it")
    assert f.BLOCK_NEGATION_RE.search("차단하지 말고 기록만")
    assert not f.BLOCK_NEGATION_RE.search("block it")


# ── AF-3 (P1-8): capability boundary must advertise cp's REAL verifiers ──

def test_af3_capability_boundary_lists_real_registered_verifiers():
    from magi_cp.verifier.descriptors import all_descriptors
    real = {d["step"] for d in all_descriptors()}
    text = render_capability_boundary("claude-code")
    # Every genuinely-registered verifier appears.
    for step in real:
        assert step in text, f"{step} missing from boundary"
    # None of the magi-agent-only verifiers cp does NOT register appear.
    for phantom in ("test_run", "git_diff", "code_diagnostics", "commit_checkpoint"):
        assert phantom not in text, f"{phantom} wrongly advertised"


def test_af3_wired_verifier_steps_matches_registry():
    from magi_cp.verifier.descriptors import all_descriptors
    assert set(f._wired_verifier_steps()) == {d["step"] for d in all_descriptors()}


# ── AF-4 (P1-9): rows 11-16 lexicon must not hijack in-scope requests ──

def test_af4_rollback_command_block_not_hijacked():
    # In-scope: block a git rollback COMMAND; not a retroactive-undo ask.
    assert f.classify_intent("git 롤백 명령 실행되면 차단해줘") is None
    assert f.classify_intent("block any bash that would retract a filing") is None


def test_af4_genuine_out_of_scope_still_fires():
    # Real retroactive-undo + cross-session intents must still classify.
    assert f.classify_intent(
        "roll back the tool call after it executes").code == "retroactive_undo"
    assert f.classify_intent(
        "undo the edit if it touches prod").code == "retroactive_undo"
    assert f.classify_intent(
        "이전 세션에서 뭐 했는지 기억해줘").code == "cross_session_state"
    assert f.classify_intent(
        "keep a running total across sessions").code == "cross_session_state"


# ── AF-9 (P2-6): honesty copy must not leak internal jargon ───────────

def test_af9_copy_table_no_matcher_triple_jargon():
    for code, (en, ko, _alt) in COPY_TABLE.items():
        for s in (en, ko):
            low = s.lower()
            assert "event-matcher-action" not in low, code
            assert "이벤트-매처-액션" not in s, code
            assert "additionalcontext" not in low, code
            # "matcher" as a bare internal term must not appear.
            assert "matcher" not in low, code


# ── AF-13 (P2-7): KO/EN lexicon symmetry ──────────────────────────────

def test_af13_enforce_re_matches_common_korean_verbs():
    assert f.ENFORCE_INTENT_RE.search("그 명령을 중단시켜")
    assert f.ENFORCE_INTENT_RE.search("멈춰줘")


def test_af13_downgrade_fires_on_korean_enforce_verb():
    draft = _step_draft("Stop", "*", "audit", "citation_verify")
    fnd = f.classify_silent_downgrade("인용 없으면 최종 답변 중단시켜", draft)
    assert fnd is not None and fnd.code == "enforce_downgraded_to_audit"


def test_af13_codex_event_not_live_ko_enumerates_events():
    en, ko, _alt = COPY_TABLE["codex_event_not_live"]
    # KO must enumerate the same live-event list as EN, not truncate to '등'.
    for ev in ("PreToolUse", "PostToolUse", "SessionStart", "UserPromptSubmit",
               "Stop", "PreCompact", "PermissionRequest"):
        assert ev in ko, ev
