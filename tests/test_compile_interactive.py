"""D55a — POST /policies/compile-interactive end-to-end tests.

Verifies the conversational policy compiler:

  - turn 1: empty history + null draft → no draft yet, the first two
            canonical questions surface (lifecycle + matcher).
  - turn 2: user answers lifecycle → draft.trigger.event is populated;
            matcher question is still pending.
  - turn N: all four required fields present → ready_to_save=true and
            the draft passes the EvidencePolicy validator.

Edge cases:
  - oversize history → 422
  - unconfigured provider → 503 with the same body shape as
    /policies/compile
  - malformed answer ids (q_ that wasn't in the previous turn's
    questions) → 422

The LLM is a deterministic stub (FakeLlmProvider); these tests exercise
the server-side merge + question logic, NOT a real model. The plain-
language scrubber is asserted directly so that an LLM regression that
leaks internal vocab is caught at the boundary.
"""
from __future__ import annotations

import json
import tempfile

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.llm.provider import FakeLlmProvider


HEADERS = {"X-Admin-Api-Key": "test-admin-key"}


@pytest.fixture(autouse=True)
def _admin_key(monkeypatch):
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", "test-admin-key")


def _tmp_store_path() -> str:
    f = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    f.write("[]")
    f.close()
    return f.name


def _client(*, llm_compiler=None, llm_reviewer=None) -> TestClient:
    app = create_app(
        dsn="sqlite:///:memory:",
        policy_store_path=_tmp_store_path(),
        llm_compiler=llm_compiler,
        llm_reviewer=llm_reviewer,
    )
    return TestClient(app)


def _llm_response(*, message: str = "", updates: dict | None = None,
                   questions: list[dict] | None = None) -> str:
    """Build a canned JSON string the stub LLM returns for one turn."""
    body: dict = {"assistant_message": message}
    if updates is not None:
        body["draft_updates"] = updates
    if questions is not None:
        body["questions"] = questions
    return json.dumps(body)


# ── happy-path conversation flow ──────────────────────────────────────


def test_turn1_empty_history_returns_canonical_first_questions():
    """No history, no draft — the LLM proposes nothing, and the server
    falls back to the canonical (lifecycle, matcher) question pair.
    Per the brief, draft is None on this first turn."""
    canned = _llm_response(
        message="Let's start. When should this run?",
        updates={},
        questions=[],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))

    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": None, "answers": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["draft"] is None, body
    assert body["needs_more"] is True
    assert body["ready_to_save"] is False
    assert "lifecycle" in body["missing_fields"]
    assert "matcher" in body["missing_fields"]
    assert "requires" in body["missing_fields"]
    assert "on_missing" in body["missing_fields"]

    # Server slices to the first MAX_QUESTIONS_PER_TURN missing fields
    # in canonical order — so we get q_lifecycle + q_matcher.
    qids = [q["id"] for q in body["questions"]]
    assert qids == ["q_lifecycle", "q_matcher"]
    targets = [q["targets_field"] for q in body["questions"]]
    assert targets == ["lifecycle", "matcher"]


def test_turn2_lifecycle_answer_populates_draft_event():
    """After the user picks `before_tool_use`, the server writes
    trigger.event=PreToolUse to the draft; the matcher question is
    still pending because matcher is still missing."""
    # Turn 2's LLM just acknowledges; the server applies the answer.
    canned = _llm_response(
        message="Got it. Which action does this apply to?",
        updates={},
        questions=[],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))

    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [
                {"role": "assistant",
                 "content": "When should this run?"},
            ],
            "draft_so_far": None,
            "answers": {"q_lifecycle": "before_tool_use"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["draft"] is not None
    assert body["draft"]["trigger"]["event"] == "PreToolUse"
    assert body["draft"]["trigger"]["host"] == "claude-code"
    assert "lifecycle" not in body["missing_fields"]
    assert "matcher" in body["missing_fields"]

    # The next-turn question slice slides to (matcher, requires).
    qids = [q["id"] for q in body["questions"]]
    assert qids == ["q_matcher", "q_requires"]


def test_turn_n_all_fields_ready_to_save_and_ir_valid():
    """When every required field is present, ready_to_save=true,
    questions is empty, and the resulting draft passes the
    EvidencePolicy validator."""
    # The LLM can leak `kind` here; the server scrubs it.
    canned = _llm_response(
        message="Draft ready.",
        updates={
            "id": "block-bash-rm",
            "description": "Block destructive bash",
        },
        questions=[],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))

    draft = {
        "id": "block-bash-rm",
        "description": "Block destructive bash",
        "trigger": {
            "host": "claude-code", "event": "PreToolUse", "matcher": "Bash",
        },
        "requires": [{"kind": "regex", "pattern": r"\brm\b"}],
        "action": "block",
    }
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": draft, "answers": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["missing_fields"] == []
    assert body["needs_more"] is False
    assert body["ready_to_save"] is True
    assert body["questions"] == []

    # The resulting draft must pass the EvidencePolicy validator so
    # the operator can move straight to PUT /policies/{id}.
    from magi_cp.policy.ir import policy_from_dict
    p = policy_from_dict(body["draft"])
    assert p.action == "block"
    assert p.trigger.matcher == "Bash"


def test_full_four_turn_walkthrough_produces_valid_ir():
    """End-to-end happy path: lifecycle → matcher → requires →
    on_missing → save. Each turn uses one canned LLM response; the
    final draft validates."""
    # All four turns: the LLM is minimal (no updates, no questions);
    # the server's canonical question + answer-merge does the work.
    canned_each = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned_each] * 4))

    # Turn 1: answer q_lifecycle.
    r1 = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [],
            "draft_so_far": None,
            "answers": {"q_lifecycle": "before_tool_use"},
        },
    )
    assert r1.status_code == 200, r1.text
    d1 = r1.json()["draft"]
    assert d1["trigger"]["event"] == "PreToolUse"

    # Turn 2: answer q_matcher.
    r2 = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [],
            "draft_so_far": d1,
            "answers": {"q_matcher": "Bash"},
        },
    )
    assert r2.status_code == 200, r2.text
    d2 = r2.json()["draft"]
    assert d2["trigger"]["matcher"] == "Bash"

    # Turn 3: answer q_requires.
    r3 = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [],
            "draft_so_far": d2,
            "answers": {"q_requires": "regex"},
        },
    )
    assert r3.status_code == 200, r3.text
    d3 = r3.json()["draft"]
    assert d3["requires"][0]["kind"] == "regex"

    # Turn 4: answer q_on_missing.
    r4 = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [],
            "draft_so_far": d3,
            "answers": {"q_on_missing": "block"},
        },
    )
    assert r4.status_code == 200, r4.text
    body = r4.json()
    assert body["ready_to_save"] is True
    assert body["missing_fields"] == []
    assert body["draft"]["action"] == "block"


# ── edge cases ────────────────────────────────────────────────────────


def test_oversize_history_422():
    """History strictly longer than MAX_HISTORY_TURNS rejects at the
    pydantic boundary so the LLM is never called."""
    # 17 turns > MAX_HISTORY_TURNS (16).
    history = [
        {"role": "user", "content": f"turn {i}"} for i in range(17)
    ]
    c = _client(llm_compiler=FakeLlmProvider([]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": history, "draft_so_far": None, "answers": None},
    )
    assert r.status_code == 422


def test_user_message_over_2000_chars_422():
    """A single user turn longer than 2000 chars fails the user-message
    cap. The library and the endpoint both enforce this; the endpoint
    catches it first at the pydantic boundary."""
    c = _client(llm_compiler=FakeLlmProvider([]))
    huge = "x" * 2_001
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user", "content": huge}],
            "draft_so_far": None,
            "answers": None,
        },
    )
    # The library enforces the user-msg cap; the pydantic outer cap is
    # higher to allow assistant content. The library raise yields 422.
    assert r.status_code == 422


def test_unconfigured_provider_503_with_compile_endpoint_body_shape():
    """When MAGI_CP_LLM_COMPILER isn't wired, the endpoint returns 503
    with the SAME body shape /policies/compile uses so the dashboard's
    existing provider_unconfigured flash mapping lights up unchanged."""
    c = _client(llm_compiler=None)

    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": None, "answers": None},
    )
    assert r.status_code == 503
    # Same wording as /policies/compile.
    assert "LLM providers not configured" in r.text


def test_malformed_answer_id_422():
    """An answer whose question_id wasn't asked last turn rejects.

    Setup: draft has lifecycle + matcher already set, so the previous
    turn would have asked (q_requires, q_on_missing). Sending
    q_lifecycle as an answer is therefore illegal because the field is
    no longer in the question slice.
    """
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))

    prior_draft = {
        "trigger": {
            "host": "claude-code", "event": "PreToolUse", "matcher": "Bash",
        },
    }
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [],
            "draft_so_far": prior_draft,
            # q_lifecycle was NOT asked last turn (lifecycle filled).
            "answers": {"q_lifecycle": "before_tool_use"},
        },
    )
    assert r.status_code == 422
    assert "previous turn" in r.text.lower() or "expected" in r.text.lower()


def test_unknown_answer_id_format_422():
    """An answer id outside the `q_<field>` shape rejects."""
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [],
            "draft_so_far": None,
            "answers": {"banana": "before_tool_use"},
        },
    )
    assert r.status_code == 422


# ── auth + plain language ─────────────────────────────────────────────


def test_endpoint_requires_admin_key():
    c = _client(llm_compiler=FakeLlmProvider([
        _llm_response(message="ok", updates={}, questions=[]),
    ]))
    r = c.post(
        "/policies/compile-interactive",
        json={"history": [], "draft_so_far": None, "answers": None},
    )
    assert r.status_code == 401


def test_assistant_message_strips_internal_vocab():
    """The server scrubs internal terms (regex / shacl / llm_critic /
    matcher / lifecycle / on_missing) out of any user-facing string,
    even when the LLM leaks them."""
    canned = _llm_response(
        message=(
            "Pick a regex or shacl, then set on_missing. "
            "Lifecycle drives the matcher."
        ),
        updates={},
        questions=[],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": None, "answers": None},
    )
    assert r.status_code == 200
    msg = r.json()["assistant_message"]
    # Internal vocabulary is gone.
    for forbidden in ("regex", "shacl", "matcher",
                       "lifecycle", "on_missing"):
        assert forbidden.lower() not in msg.lower(), (forbidden, msg)
    # Plain-language replacements are present.
    assert "a pattern in the response" in msg.lower()
    assert "a structured rule" in msg.lower()


def test_at_most_two_questions_per_turn():
    """Even if the LLM proposes more, the server caps to 2."""
    # LLM tries to surface four questions — all canonical-id-shaped so
    # only the cap (not validation) should drop them.
    canned = _llm_response(
        message="",
        updates={},
        questions=[
            {"id": "q_lifecycle", "prompt": "when?",
             "kind": "single_select", "targets_field": "lifecycle",
             "options": []},
            {"id": "q_matcher", "prompt": "which action?",
             "kind": "text", "targets_field": "matcher", "options": []},
            {"id": "q_requires", "prompt": "what?",
             "kind": "single_select", "targets_field": "requires",
             "options": []},
            {"id": "q_on_missing", "prompt": "what to do?",
             "kind": "single_select", "targets_field": "on_missing",
             "options": []},
        ],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": None, "answers": None},
    )
    assert r.status_code == 200
    assert len(r.json()["questions"]) <= 2


def test_does_not_persist_policy_on_save_path():
    """ready_to_save=true is informational only — the endpoint NEVER
    writes to the policy store. The dashboard issues a separate PUT.
    """
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    draft = {
        "id": "block-bash",
        "description": "test",
        "trigger": {
            "host": "claude-code", "event": "PreToolUse", "matcher": "Bash",
        },
        "requires": [{"kind": "regex", "pattern": r"\brm\b"}],
        "action": "block",
    }
    before = c.get("/policies", headers=HEADERS).json()["items"]
    assert before == []
    c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": draft, "answers": None},
    )
    after = c.get("/policies", headers=HEADERS).json()["items"]
    assert after == []
