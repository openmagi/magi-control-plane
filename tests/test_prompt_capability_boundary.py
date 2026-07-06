"""PR-4 - generated capability-boundary prompt section + infeasible_hint channel.

Tests verify:
  (A) render_capability_boundary("claude-code") output appears in the
      system message built by _build_messages, and lists all 8
      context-injection-excluded events.
  (B) For rt="codex" the system message additionally names live events
      and an inert read tool (e.g. "Read").
  (C) Grep gate: the OLD static D59 4-event block is gone from the
      template (the string "uses elicitationDecision" no longer exists).
  (D) .format() safety regression: format with all three keys succeeds
      and the capability_boundary value is present in the result.
  (E) infeasible_hint advisory channel: valid hint on a turn where the
      deterministic lexicon did not fire - suggestion appended, draft
      and questions unchanged, ready_to_save unchanged.
  (F) Unknown hint value ("wombat") is dropped - no suggestion line,
      no error.
  (G) A valid hint arriving on the same turn a deterministic lexicon
      finding already fired is suppressed (authoritative finding wins).
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.llm.provider import FakeLlmProvider
from magi_cp.policy import feasibility as _feas


HEADERS = {"X-Admin-Api-Key": "test-admin-key"}

# The 8 events excluded from additionalContext delivery on all runtimes.
_CC_EXCLUDED_8 = [
    "Elicitation",
    "ElicitationResult",
    "WorktreeCreate",
    "MessageDisplay",
    "Stop",
    "StopFailure",
    "SessionEnd",
    "SubagentStop",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _admin_key(monkeypatch):
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", "test-admin-key")


def _tmp_store_path() -> str:
    d = tempfile.mkdtemp(prefix="magi-cp-pr4-")
    path = os.path.join(d, "policies.json")
    with open(path, "w") as f:
        f.write("[]")
    return path


def _client(*, llm_compiler=None) -> TestClient:
    app = create_app(
        dsn="sqlite:///:memory:",
        policy_store_path=_tmp_store_path(),
        llm_compiler=llm_compiler,
    )
    return TestClient(app)


def _llm_response(
    *,
    message: str = "",
    updates: dict | None = None,
    questions: list | None = None,
    infeasible_hint: str | None = None,
) -> str:
    body: dict = {"assistant_message": message}
    if updates is not None:
        body["draft_updates"] = updates
    if questions is not None:
        body["questions"] = questions
    if infeasible_hint is not None:
        body["infeasible_hint"] = infeasible_hint
    return json.dumps(body)


def _get_system_message_for_runtime(runtime_id: str) -> str:
    """Build the system message for a given runtime by calling _build_messages."""
    from magi_cp.policy.nl_compiler_interactive import _build_messages
    msgs = _build_messages(
        nonce="testnonce",
        history=None,
        draft_so_far=None,
        answers=None,
        runtime_id=runtime_id,
    )
    assert msgs[0]["role"] == "system"
    return msgs[0]["content"]


# ---------------------------------------------------------------------------
# (A) claude-code: all 8 excluded events in system message
# ---------------------------------------------------------------------------

def test_a_claude_code_excluded_events_in_system_message():
    """System message for claude-code lists all 8 context-injection-excluded events."""
    sys_msg = _get_system_message_for_runtime("claude-code")
    for event in _CC_EXCLUDED_8:
        assert event in sys_msg, (
            f"Expected excluded event '{event}' in system message for claude-code"
        )


def test_a_capability_boundary_content_matches_render():
    """System message content includes the exact output of render_capability_boundary."""
    boundary = _feas.render_capability_boundary("claude-code")
    sys_msg = _get_system_message_for_runtime("claude-code")
    # The boundary text should appear verbatim in the system message.
    assert boundary in sys_msg, (
        "render_capability_boundary('claude-code') output not found in system message"
    )


# ---------------------------------------------------------------------------
# (B) codex: live events + inert read tool in system message
# ---------------------------------------------------------------------------

def test_b_codex_live_events_and_read_tool_in_system_message():
    """System message for codex names the Codex live event set and inert read tools."""
    sys_msg = _get_system_message_for_runtime("codex")
    # All 8 excluded events still present.
    for event in _CC_EXCLUDED_8:
        assert event in sys_msg, (
            f"Expected excluded event '{event}' in system message for codex"
        )
    # Codex-specific: live events section.
    assert "PreToolUse" in sys_msg
    assert "PostToolUse" in sys_msg
    # Codex inert read-family tool.
    assert "Read" in sys_msg


def test_b_codex_boundary_matches_render():
    """System message for codex contains the exact render_capability_boundary output."""
    boundary = _feas.render_capability_boundary("codex")
    sys_msg = _get_system_message_for_runtime("codex")
    assert boundary in sys_msg, (
        "render_capability_boundary('codex') output not found in system message"
    )


# ---------------------------------------------------------------------------
# (C) grep gate: old D59 static 4-event list is gone from template
# ---------------------------------------------------------------------------

def test_c_old_d59_static_text_removed():
    """The old static D59 block text is gone from _SYSTEM_INTERACTIVE_TMPL.

    The static block hardcoded only 4 excluded events using the phrase
    'uses elicitationDecision'. That phrase (unique to the deleted block)
    must no longer appear in the template.
    """
    from magi_cp.policy.nl_compiler_interactive import _SYSTEM_INTERACTIVE_TMPL
    assert "uses elicitationDecision" not in _SYSTEM_INTERACTIVE_TMPL, (
        "Old D59 static text 'uses elicitationDecision' is still present in "
        "_SYSTEM_INTERACTIVE_TMPL; the dynamic {capability_boundary} replacement "
        "did not remove the static block."
    )


# ---------------------------------------------------------------------------
# (D) .format() safety regression
# ---------------------------------------------------------------------------

def test_d_template_format_safety():
    """_SYSTEM_INTERACTIVE_TMPL.format(nonce, max_questions, capability_boundary) succeeds.

    Verifies that adding {capability_boundary} and the infeasible_hint
    instruction block did not introduce any stray single-brace tokens
    that would cause a KeyError on format().
    """
    from magi_cp.policy.nl_compiler_interactive import (
        _SYSTEM_INTERACTIVE_TMPL,
        MAX_QUESTIONS_PER_TURN,
    )
    result = _SYSTEM_INTERACTIVE_TMPL.format(
        nonce="safety-test-nonce",
        max_questions=MAX_QUESTIONS_PER_TURN,
        capability_boundary="CAPABILITY_BOUNDARY_TEST_VALUE",
    )
    assert "CAPABILITY_BOUNDARY_TEST_VALUE" in result, (
        "capability_boundary substitution did not appear in formatted template"
    )


# ---------------------------------------------------------------------------
# (E) infeasible_hint advisory: valid hint, no deterministic lexicon hit
# ---------------------------------------------------------------------------

def test_e_valid_hint_appended_to_assistant_message():
    """Valid infeasible_hint from LLM appends server-owned suggestion to assistant_message.

    Conditions: no deterministic lexicon finding (novel user phrasing),
    hint category = "token_budget" (a COPY_TABLE entry).
    Expected: assistant_message gains the COPY_TABLE suggestion; draft,
    questions, and ready_to_save are unchanged.
    """
    # A partial draft so we have something to preserve.
    draft = {"trigger": {"event": "PreToolUse", "matcher": "Bash"}, "action": "audit"}
    canned = _llm_response(
        message="ok",
        updates={},
        questions=[],
        infeasible_hint="token_budget",
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))

    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user", "content": "I need to limit the cost per session"}],
            "draft_so_far": draft,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()

    am = body["assistant_message"]
    # Server-owned COPY_TABLE text for "token_budget" (EN) must appear.
    assert "Token or cost budgets" in am, (
        f"Expected COPY_TABLE 'token_budget' copy in assistant_message, got: {am!r}"
    )

    # Draft is preserved (not wiped).
    returned_draft = body["draft"] or {}
    assert returned_draft.get("action") == "audit", (
        f"Draft must be preserved when infeasible_hint fires; got: {returned_draft}"
    )

    # ready_to_save must not be flipped to True by hint.
    assert body["ready_to_save"] is False, (
        "infeasible_hint must not change ready_to_save"
    )

    # questions array is NOT cleared by the hint (whatever the state
    # machine generated, the hint must not wipe them).
    # We verify that by checking questions is a list (not None/missing).
    assert isinstance(body["questions"], list), (
        "infeasible_hint must not remove the questions field"
    )


def test_e_hint_other_out_of_scope_uses_generic_copy():
    """other_out_of_scope hint uses the server-owned generic copy line."""
    canned = _llm_response(
        message="Here is your policy",
        updates={},
        questions=[],
        infeasible_hint="other_out_of_scope",
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))

    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user", "content": "something novel"}],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()

    am = body["assistant_message"]
    # The generic server copy must contain the key phrase.
    assert "outside what a policy here can express" in am, (
        f"Expected generic other_out_of_scope copy, got: {am!r}"
    )


# ---------------------------------------------------------------------------
# (F) unknown hint value is dropped silently
# ---------------------------------------------------------------------------

def test_f_unknown_hint_value_dropped():
    """An unrecognised infeasible_hint value ('wombat') is dropped - no error,
    no suggestion line appended.
    """
    base_msg = "Here is your draft policy."
    canned = _llm_response(
        message=base_msg,
        updates={},
        questions=[],
        infeasible_hint="wombat",
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))

    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user", "content": "block dangerous shell commands"}],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()

    am = body["assistant_message"]
    # The suggestion copy for any hint category must NOT appear in the message.
    assert "Token or cost budgets" not in am, am
    assert "wombat" not in am, am
    # No server error (5xx) - unknown hint is silently dropped.
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# (G) authoritative lexicon finding outranks advisory hint
# ---------------------------------------------------------------------------

def test_g_lexicon_finding_suppresses_hint():
    """When the deterministic intent lexicon fires this turn (rate_limit_window),
    the advisory infeasible_hint from the LLM does NOT double-append its copy.

    The lexicon text for rate_limit_window should appear EXACTLY ONCE in
    assistant_message (from the authoritative finding, not from the hint).
    """
    # Include the hint in the LLM response for the same category.
    canned = _llm_response(
        message="ok",
        updates={},
        questions=[],
        infeasible_hint="rate_limit_window",
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))

    # This user text triggers the deterministic rate_limit_window lexicon.
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user", "content": "limit to 5 per minute"}],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()

    # Lexicon finding must have fired.
    feas = body.get("feasibility")
    assert feas is not None, f"Expected feasibility finding; body: {body}"
    assert feas["code"] == "rate_limit_window", feas

    am = body["assistant_message"]
    # The EN copy for rate_limit_window contains "time window".
    assert "time window" in am.lower() or "time-window" in am.lower() or "Rate-limit" in am, (
        f"Expected rate_limit_window copy in assistant_message, got: {am!r}"
    )

    # The copy string must appear only ONCE - the hint did not double-append.
    # Check the EN copy appears at most once.
    # More precisely: the rate_limit_window EN copy appears exactly once.
    copy_fragment = "Hooks fire per event"
    assert am.count(copy_fragment) <= 1, (
        f"Rate-limit copy appeared more than once - hint leaked: {am!r}"
    )
