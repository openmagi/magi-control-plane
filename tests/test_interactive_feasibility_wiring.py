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

def test_d_illegal_block_at_stop_is_downgraded_not_dead_end():
    """AF-5: Stop+*+block is illegal, but instead of a matrix_illegal_triple
    dead-end the server now deterministically downgrades it to a saveable
    audit draft and surfaces the honest enforce_downgraded_to_audit finding.
    (matrix_illegal_triple still covers non-enforce illegal shapes such as an
    explicit tool matcher at Stop; see the classify_draft unit tests.)"""
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
    assert f["code"] == "enforce_downgraded_to_audit", f
    assert f["runtime_id"] == "claude-code", f
    assert body["draft"]["action"] == "audit", body["draft"]
    # Not a matrix_illegal_triple dead-end: the draft is coherent and the
    # wizard proceeds to ask for an id (normal flow) rather than surfacing a
    # raw validator error.
    assert "illegal combination" not in (body["assistant_message"] or "")


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


# ---------------------------------------------------------------------------
# REV-PR-3 (GAP-A): anti-silent-downgrade wiring
# ---------------------------------------------------------------------------

def _citation_audit_draft() -> dict:
    return {
        "id": "final-answer-citation-audit",
        "type": "evidence",
        "trigger": {"event": "Stop", "matcher": "*"},
        "requires": [{"kind": "step", "step": "citation_verify",
                      "verdict": "pass"}],
        "action": "audit",
    }


def test_f_downgrade_finding_on_block_intent_citation():
    """Operator asks to block missing citations at the final answer; the LLM
    (per its prompt) lands an audit draft at Stop -> the wire carries the
    honest enforce_downgraded_to_audit finding, not a stale illegal triple,
    and the draft stays saveable (audit is legal)."""
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user",
                         "content": "block the final answer when citations are missing"}],
            "draft_so_far": _citation_audit_draft(),
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    f = body["feasibility"]
    assert f is not None, body
    assert f["code"] == "enforce_downgraded_to_audit", f
    assert f["class"] == "degraded", f
    # Magi Agent handoff offered as the in-bounds enforce route.
    assert any(a.get("kind") == "magi_agent_handoff" for a in f["alternatives"]), f
    # Draft is a legal audit policy -> saveable; the point is disclosure.
    assert body["ready_to_save"] is True, body
    # The downgrade copy is present in the message.
    assert "audit" in body["assistant_message"].lower()


def test_f_no_finding_on_audit_intent():
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user", "content": "경고만 남겨줘"}],
            "draft_so_far": _citation_audit_draft(),
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["feasibility"] is None


def test_f_downgrade_finding_replaces_stale_illegal_triple():
    """When the extractor lands block (illegal at Stop) but the LLM downgrades
    to audit, the fresher enforce_downgraded_to_audit finding wins over the
    stale matrix_illegal_triple."""
    # LLM proposes audit for the action (mirrors the citation->audit mandate).
    canned = _llm_response(message="ok", updates={"action": "audit"}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    draft = _citation_audit_draft()
    draft["action"] = "block"  # extractor/prior-turn block on an illegal triple
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user",
                         "content": "actually block it, don't just log"}],
            "draft_so_far": draft,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    f = r.json()["feasibility"]
    assert f is not None
    assert f["code"] == "enforce_downgraded_to_audit", f


def test_g_block_restore_when_legal():
    """PreToolUse+Bash: operator says block, LLM proposes audit -> the server
    restores the operator's explicit legal block."""
    canned = _llm_response(message="ok", updates={"action": "audit"}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    draft = {
        "id": "bash-rrn-block", "type": "evidence",
        "trigger": {"event": "PreToolUse", "matcher": "Bash"},
        "requires": [{"kind": "step", "step": "privilege_scan",
                      "verdict": "pass"}],
        "action": "audit",
    }
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user",
                         "content": "block any shell command that contains an RRN"}],
            "draft_so_far": draft,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["draft"]["action"] == "block", body["draft"]
    # A legal block is not a downgrade.
    assert body["feasibility"] is None, body


def test_g_no_restore_when_illegal_yields_finding():
    """Stop+citation: operator says block, LLM proposes audit -> action stays
    audit (block illegal) AND the downgrade finding fires."""
    canned = _llm_response(message="ok", updates={"action": "audit"}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user",
                         "content": "block the final answer, don't just record"}],
            "draft_so_far": _citation_audit_draft(),
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["draft"]["action"] == "audit", body["draft"]
    assert body["feasibility"]["code"] == "enforce_downgraded_to_audit"


def test_h_q_on_missing_filtered_at_stop():
    """A Stop draft missing on_missing must offer only 'audit' (block/ask are
    illegal at Stop), so the operator cannot click into a dead end."""
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    draft = {
        "id": "cite", "type": "evidence",
        "trigger": {"event": "Stop", "matcher": "*"},
        "requires": [{"kind": "step", "step": "citation_verify",
                      "verdict": "pass"}],
        # no action -> q_on_missing will be asked
    }
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user", "content": "verify citations at the end"}],
            "draft_so_far": draft,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    qs = {q["id"]: q for q in body["questions"]}
    if "q_on_missing" in qs:
        vals = {o["value"] for o in (qs["q_on_missing"].get("options") or [])}
        assert vals == {"audit"}, vals


def test_i_intent_finding_outranks_downgrade():
    """A rows 11-16 intent finding (rate limit) still wins over the downgrade
    finding on the same audit-at-Stop draft."""
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user",
                         "content": "block calls if more than 5 per minute"}],
            "draft_so_far": _citation_audit_draft(),
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    f = r.json()["feasibility"]
    assert f is not None
    assert f["code"] == "rate_limit_window", f


def test_g_no_restore_on_negated_block():
    """REV-PR-3 fix (review MEDIUM): 'don't block it, just record' must NOT
    restore block at a block-legal event; the operator explicitly declined
    enforcement, so the LLM's audit stands."""
    canned = _llm_response(message="ok", updates={"action": "audit"}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    draft = {
        "id": "bash-rrn", "type": "evidence",
        "trigger": {"event": "PreToolUse", "matcher": "Bash"},
        "requires": [{"kind": "step", "step": "privilege_scan",
                      "verdict": "pass"}],
        "action": "audit",
    }
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user",
                         "content": "don't block it, just record RRN in shell"}],
            "draft_so_far": draft,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["draft"]["action"] == "audit", body["draft"]


def test_g_no_restore_on_korean_negated_block():
    canned = _llm_response(message="ok", updates={"action": "audit"}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    draft = {
        "id": "bash-rrn", "type": "evidence",
        "trigger": {"event": "PreToolUse", "matcher": "Bash"},
        "requires": [{"kind": "step", "step": "privilege_scan",
                      "verdict": "pass"}],
        "action": "audit",
    }
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user", "content": "차단하지 말고 기록만 해줘"}],
            "draft_so_far": draft,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["draft"]["action"] == "audit"


# ── AF-5 (P1-1/P1-2): deterministic enforce-downgrade for block AND ask ──

def _priv_scan_draft(event, matcher, action):
    return {
        "id": "x", "type": "evidence",
        "trigger": {"event": event, "matcher": matcher},
        "requires": [{"kind": "step", "step": "privilege_scan", "verdict": "pass"}],
        "action": action,
    }


def test_af5_block_kept_at_stop_is_downgraded_deterministically():
    """LLM keeps block at Stop (audit-only). The server must downgrade to
    audit, surface the honest finding, and keep the draft saveable - never a
    matrix_illegal_triple / S3 dead-end."""
    canned = _llm_response(message="ok", updates={"action": "block"}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [{"role": "user",
                     "content": "block the final answer when citations are missing"}],
        "draft_so_far": _citation_audit_draft(),  # Stop/*/audit seed
        "answers": None,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["draft"]["action"] == "audit", body["draft"]
    assert body["feasibility"]["code"] == "enforce_downgraded_to_audit", body["feasibility"]
    assert body["ready_to_save"] is True, body
    assert "표현할 수 없" not in (body["assistant_message"] or "")
    assert "illegal combination" not in (body["assistant_message"] or "")


def test_af5_ask_at_illegal_event_downgraded():
    """ask at PostToolUse (ask illegal there) downgrades to audit with the
    honest finding rather than dead-ending."""
    canned = _llm_response(message="ok", updates={"action": "ask"}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    draft = _priv_scan_draft("PostToolUse", "Bash", "ask")
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [{"role": "user", "content": "require approval after each bash call"}],
        "draft_so_far": draft, "answers": None,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["draft"]["action"] == "audit", body["draft"]
    assert body["feasibility"]["code"] == "enforce_downgraded_to_audit", body["feasibility"]


def test_af5_legal_block_not_downgraded():
    """A legal block (PreToolUse/Bash) is untouched."""
    canned = _llm_response(message="ok", updates={"action": "block"}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    draft = _priv_scan_draft("PreToolUse", "Bash", "block")
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [{"role": "user", "content": "block RRN in shell"}],
        "draft_so_far": draft, "answers": None,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["draft"]["action"] == "block", body["draft"]
    assert body["feasibility"] is None, body


# ── AF-7 (P1-6): honest steer for out-of-bucket lifecycle events ──────

def test_af7_out_of_bucket_event_gets_honest_steer():
    """An event outside the 3 conversational buckets (PermissionRequest)
    must not silently morph; the operator gets an honest steer to the full
    editor."""
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [{"role": "user", "content": "권한 요청이 있을 때 기록 남겨줘"}],
        "draft_so_far": None, "answers": None,
    })
    assert r.status_code == 200, r.text
    am = r.json()["assistant_message"] or ""
    assert "PermissionRequest" in am, am
    assert ("고급 편집기" in am) or ("full editor" in am), am


def test_af7_in_bucket_event_no_steer():
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [{"role": "user", "content": "verify citations at the final answer"}],
        "draft_so_far": None, "answers": None,
    })
    assert r.status_code == 200, r.text
    am = r.json()["assistant_message"] or ""
    assert "full editor" not in am and "고급 편집기" not in am, am


# ── AF-8 (P1-10): deterministic pack steering (revive dead D75) ───────

def test_af8_research_mode_steers_to_pack():
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [{"role": "user", "content": "set up research mode"}],
        "draft_so_far": None, "answers": None,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    am = body["assistant_message"] or ""
    assert "research-mode" in am, am
    assert "/policy-packs" in am, am
    assert body["questions"] == [], body
    assert body["draft"] is None, body


def test_af8_coding_session_korean_steers_to_pack():
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [{"role": "user", "content": "코딩 세션 안전하게 해줘"}],
        "draft_so_far": None, "answers": None,
    })
    assert r.status_code == 200, r.text
    am = r.json()["assistant_message"] or ""
    assert "coding-safety" in am, am


def test_af8_concrete_request_not_hijacked_by_pack_steer():
    """A concrete request that names a check/tool stays on the normal path
    even if it mentions 'research'."""
    canned = _llm_response(message="ok", updates={"trigger": {"event": "PreToolUse", "matcher": "WebFetch"}, "action": "audit"}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [{"role": "user",
                     "content": "리서치 목적으로 외부 web search 출처를 audit 로그로 남겨줘"}],
        "draft_so_far": None, "answers": None,
    })
    assert r.status_code == 200, r.text
    am = r.json()["assistant_message"] or ""
    assert "/policy-packs" not in am, am


# ── AF-9 (P1-7): steer for non-evidence archetypes authored in chat ──

def test_af9_inject_context_on_legal_event_steers_not_morphs():
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [{"role": "user", "content": "inject context before each bash"}],
        "draft_so_far": None, "answers": None,
    })
    assert r.status_code == 200, r.text
    am = r.json()["assistant_message"] or ""
    assert ("full editor" in am) or ("고급 편집기" in am), am


# ── AF-10 (P2-10): movable steer only names authorable events ─────────

def test_af10_movable_steer_omits_unauthorable_event():
    """privilege_scan authored at Stop can fire at PreToolUse, PostToolUse
    AND UserPromptSubmit (all block-legal), but the conversational flow can
    only author the 3 buckets. The chat steer must NOT name UserPromptSubmit."""
    canned = _llm_response(message="ok", updates={"action": "block"}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    draft = {
        "id": "x", "type": "evidence",
        "trigger": {"event": "Stop", "matcher": "*"},
        "requires": [{"kind": "step", "step": "privilege_scan", "verdict": "pass"}],
        "action": "audit",
    }
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [{"role": "user", "content": "block PII at the final answer"}],
        "draft_so_far": draft, "answers": None,
    })
    assert r.status_code == 200, r.text
    am = r.json()["assistant_message"] or ""
    assert "UserPromptSubmit" not in am, am
    # A block-legal bucketed event is still offered.
    assert ("PreToolUse" in am) or ("PostToolUse" in am), am


# ── AF-11 (P1-8ii): ready_to_save consults the verifier registry ──────

def test_af11_unregistered_verifier_not_ready_to_save():
    """A draft that names a verifier cp does not register must NOT report
    ready_to_save (it would 422 at Save). It reports needs_more instead."""
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    draft = {
        "id": "phantom", "type": "evidence",
        "trigger": {"event": "Stop", "matcher": "*"},
        "requires": [{"kind": "step", "step": "test_run", "verdict": "pass"}],
        "action": "audit",
    }
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [{"role": "user", "content": "require tests ran"}],
        "draft_so_far": draft, "answers": None,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ready_to_save"] is False, body


def test_af11_registered_verifier_still_ready():
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    draft = {
        "id": "cite-audit", "type": "evidence",
        "trigger": {"event": "Stop", "matcher": "*"},
        "requires": [{"kind": "step", "step": "citation_verify", "verdict": "pass"}],
        "action": "audit",
    }
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [{"role": "user", "content": "audit citations"}],
        "draft_so_far": draft, "answers": None,
    })
    assert r.status_code == 200, r.text
    assert r.json()["ready_to_save"] is True, r.json()


def test_af11_regex_kind_unaffected():
    """A non-step requirement (regex) is not a registry concern."""
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    draft = {
        "id": "rm-block", "type": "evidence",
        "trigger": {"event": "PreToolUse", "matcher": "Bash"},
        "requires": [{"kind": "regex", "pattern": r"\brm -rf\b"}],
        "action": "block",
    }
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [{"role": "user", "content": "block rm -rf"}],
        "draft_so_far": draft, "answers": None,
    })
    assert r.status_code == 200, r.text
    assert r.json()["ready_to_save"] is True, r.json()


# ── AF-12 (P2-4): compound path runs feasibility ─────────────────────

def test_af12_compound_response_has_feasibility_key():
    """Wire-shape parity: the compound path always carries a feasibility key
    (null when native), like the single-policy path."""
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [{"role": "user",
                     "content": "require a credible source before mcp__trading__execute_trade runs"}],
        "draft_so_far": None, "answers": None,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("compound") is True, body
    assert "feasibility" in body, body


def test_af12_compound_codex_surfaces_inert_finding():
    """On Codex, a compound whose gated tool is inert surfaces a feasibility
    finding instead of silently authoring an unenforced policy."""
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    # Gate a codex-inert tool (Read: dispatched as a shell sub-action on
    # Codex). WebFetch cannot be gated (it produces the evidence), so Read is
    # the reachable inert-gate case.
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [{"role": "user",
                     "content": "require a credible source before Read runs"}],
        "draft_so_far": None, "answers": None,
        "runtime_id": "codex",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("compound") is True, body
    assert body.get("feasibility") is not None, body
    assert body["feasibility"]["runtime_id"] == "codex", body["feasibility"]
    assert body["feasibility"]["code"] == "codex_matcher_inert", body["feasibility"]
