"""PR-3 - feasibility wiring: runtime_id + finding injection in step_compile.

Tests verify that:
  (a) rt="codex" + draft with PreToolUse/WebFetch surfaces codex_matcher_inert
      as a silent_noop finding; assistant_message carries the localized prefix;
      draft is preserved; ready_to_save unchanged.
  (b) rt="codex" + PreToolUse/Bash (native) -> feasibility is None.
  (c) rt=None (unset) existing scenarios are byte-identical minus the new
      feasibility key (feasibility=None, rest equals baseline).
  (d) ILLEGAL triple (Stop+*+block) -> matrix_illegal_triple finding.
  (e) intent "분당 5번으로 제한해줘" (rate limit) -> rate_limit_window,
      not-expressible, draft NOT seeded with a verifier, questions=[].
"""
from __future__ import annotations

import json
import os
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
    d = tempfile.mkdtemp(prefix="magi-cp-feasibility-")
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


def _llm_response(*, message: str = "", updates: dict | None = None,
                   questions: list | None = None) -> str:
    body: dict = {"assistant_message": message}
    if updates is not None:
        body["draft_updates"] = updates
    if questions is not None:
        body["questions"] = questions
    return json.dumps(body)


# ---------------------------------------------------------------------------
# (a) codex + PreToolUse/WebFetch - codex_matcher_inert (silent_noop)
# ---------------------------------------------------------------------------

def test_a_codex_webfetch_matcher_inert():
    """rt=codex, draft with PreToolUse/WebFetch -> codex_matcher_inert finding.

    assistant_message must contain the English prefix (EN history), draft is
    preserved, ready_to_save same as without runtime_id.
    """
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))

    # Draft pre-seeded with the inert combination.
    draft = {"trigger": {"event": "PreToolUse", "matcher": "WebFetch"}}

    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user", "content": "block WebFetch requests"}],
            "draft_so_far": draft,
            "answers": None,
            "runtime_id": "codex",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["feasibility"] is not None, body
    f = body["feasibility"]
    assert f["class"] == "silent_noop", f
    assert f["code"] == "codex_matcher_inert", f
    assert f["runtime_id"] == "codex", f
    assert isinstance(f["alternatives"], list), f

    # assistant_message contains the localized prefix text
    am = body["assistant_message"]
    # The EN prefix mentions "no direct equivalent in Codex" or "zero times"
    assert "Codex" in am, f"prefix missing in: {am!r}"

    # draft is preserved (trigger still present with event+matcher)
    returned_draft = body["draft"]
    assert returned_draft is not None, body
    trig = returned_draft.get("trigger") or {}
    assert trig.get("event") == "PreToolUse", returned_draft
    assert trig.get("matcher") == "WebFetch", returned_draft

    # ready_to_save check - should be same as same request without runtime_id
    # (incomplete draft: missing requires/action/id -> not ready)
    assert body["ready_to_save"] is False


def test_a_codex_webfetch_ready_to_save_parity():
    """ready_to_save is the same with or without runtime_id on an incomplete draft."""
    canned = _llm_response(message="ok", updates={}, questions=[])
    draft = {"trigger": {"event": "PreToolUse", "matcher": "WebFetch"}}

    c_rt = _client(llm_compiler=FakeLlmProvider([canned]))
    r_rt = c_rt.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user", "content": "block WebFetch"}],
            "draft_so_far": draft,
            "answers": None,
            "runtime_id": "codex",
        },
    )
    c_no = _client(llm_compiler=FakeLlmProvider([_llm_response(message="ok", updates={}, questions=[])]))
    r_no = c_no.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user", "content": "block WebFetch"}],
            "draft_so_far": draft,
            "answers": None,
        },
    )
    assert r_rt.status_code == 200, r_rt.text
    assert r_no.status_code == 200, r_no.text
    assert r_rt.json()["ready_to_save"] == r_no.json()["ready_to_save"]


# ---------------------------------------------------------------------------
# (b) codex + PreToolUse/Bash - native, no finding
# ---------------------------------------------------------------------------

def test_b_codex_bash_is_native():
    """rt=codex, Bash maps to exec_command via _CC_TO_CODEX_TOOL -> feasibility=None."""
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))

    draft = {"trigger": {"event": "PreToolUse", "matcher": "Bash"}}

    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user", "content": "block bash commands"}],
            "draft_so_far": draft,
            "answers": None,
            "runtime_id": "codex",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["feasibility"] is None, body


# ---------------------------------------------------------------------------
# (c) rt=None (unset) - regression guard: existing behavior unchanged
# ---------------------------------------------------------------------------

def test_c_rt_none_feasibility_key_is_null():
    """When runtime_id is absent, feasibility is null and other fields unchanged."""
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

    # New key present but null
    assert "feasibility" in body, body
    assert body["feasibility"] is None, body

    # Core fields unchanged from pre-PR-3 expected shape
    assert body["draft"] is None
    assert body["needs_more"] is True
    assert body["ready_to_save"] is False
    assert "lifecycle" in body["missing_fields"]


def test_c_rt_none_explicit_same_as_absent():
    """runtime_id=null in the body is equivalent to omitting the field."""
    canned_1 = _llm_response(message="ok", updates={}, questions=[])
    canned_2 = _llm_response(message="ok", updates={}, questions=[])

    c1 = _client(llm_compiler=FakeLlmProvider([canned_1]))
    c2 = _client(llm_compiler=FakeLlmProvider([canned_2]))

    base_body = {
        "history": [{"role": "user", "content": "check for dangerous commands"}],
        "draft_so_far": None,
        "answers": None,
    }

    r_absent = c1.post("/policies/compile-interactive", headers=HEADERS, json=base_body)
    r_null = c2.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={**base_body, "runtime_id": None},
    )

    assert r_absent.status_code == 200, r_absent.text
    assert r_null.status_code == 200, r_null.text

    b_absent = r_absent.json()
    b_null = r_null.json()

    # feasibility should be null in both cases
    assert b_absent.get("feasibility") is None
    assert b_null.get("feasibility") is None

    # Non-feasibility fields must be identical
    for key in ("draft", "needs_more", "ready_to_save", "missing_fields", "compound"):
        assert b_absent.get(key) == b_null.get(key), f"key={key} differs"


# ---------------------------------------------------------------------------
# (d) matrix_illegal_triple
# ---------------------------------------------------------------------------

def test_d_matrix_illegal_triple():
    """Stop+*+block is an illegal combination; feasibility surfaces the finding."""
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))

    # Pre-seeded complete illegal draft: event=Stop, matcher=*, action=block
    draft = {
        "trigger": {"event": "Stop", "matcher": "*"},
        "action": "block",
    }

    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user", "content": "block the final answer"}],
            "draft_so_far": draft,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["feasibility"] is not None, body
    f = body["feasibility"]
    assert f["code"] == "matrix_illegal_triple", f
    assert f["runtime_id"] == "claude-code", f


# ---------------------------------------------------------------------------
# (e) intent: rate limit window (not-expressible)
# ---------------------------------------------------------------------------

def test_e_rate_limit_intent_stops_seeding():
    """Korean rate-limit phrasing -> rate_limit_window, not-expressible.

    The draft must NOT be seeded with a verifier step.  questions == [].
    """
    # LLM stub tries to seed a verifier - these updates must be blocked.
    canned = _llm_response(
        message="I will set up a rate limit check",
        updates={
            "trigger": {"event": "PreToolUse", "matcher": "Bash"},
            "requires": [{"kind": "step", "step": "privilege_scan"}],
            "action": "audit",
        },
        questions=[
            {"id": "q_lifecycle", "targets_field": "lifecycle",
             "type": "single_select", "prompt": "When?",
             "options": [{"value": "before_tool_use", "label": "Before tool"}]},
        ],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))

    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user", "content": "분당 5번으로 제한해줘"}],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["feasibility"] is not None, body
    f = body["feasibility"]
    assert f["code"] == "rate_limit_window", f
    assert f["class"] == "not-expressible", f

    # Draft must NOT be seeded with the verifier that the LLM proposed
    returned_draft = body.get("draft")
    if returned_draft is not None:
        reqs = returned_draft.get("requires")
        assert not reqs, (
            f"draft should not be seeded with requires when intent is not-expressible; "
            f"got requires={reqs}"
        )

    # questions must be empty when intent_finding fires
    assert body["questions"] == [], body["questions"]

    # assistant_message should contain the Korean copy (history is Korean)
    am = body["assistant_message"]
    # The KO copy mentions "시간 창" or "롤링 카운터" or similar
    # The copy: "시간 창(분당, 시간당) 속도 제한은 현재 cp 훅 정책으로 표현할 수 없습니다."
    assert "수 없습니다" in am or "표현" in am, (
        f"expected Korean copy in assistant_message but got: {am!r}"
    )
