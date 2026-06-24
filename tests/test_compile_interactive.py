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


def test_full_walkthrough_requires_body_and_id_before_ready():
    """End-to-end happy path. After the four behavioral choices the
    wizard still asks for the pattern body (so the seeded EvidenceReq
    is not empty) and the policy id (so the IR loader does not
    KeyError). `ready_to_save=True` is only reported once the merged
    draft round-trips through `policy_from_dict()` cleanly.
    """
    # Six turns: lifecycle, matcher, requires (type), requires_body,
    # on_missing, id. The LLM is minimal (no updates, no questions);
    # the server's canonical question + answer-merge does the work.
    canned_each = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned_each] * 6))

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

    # Turn 3: answer q_requires (the TYPE choice). The wizard seeds an
    # empty EvidenceReq and reports requires_body as still missing.
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
    body3 = r3.json()
    d3 = body3["draft"]
    assert d3["requires"][0]["kind"] == "regex"
    assert d3["requires"][0]["pattern"] == ""
    assert body3["ready_to_save"] is False, body3
    assert "requires_body" in body3["missing_fields"], body3
    assert any(q["id"] == "q_requires_body" for q in body3["questions"])

    # Turn 4: answer q_requires_body. The pattern body lands on the
    # first requires item.
    r4 = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [],
            "draft_so_far": d3,
            "answers": {"q_requires_body": r"\brm -rf\b"},
        },
    )
    assert r4.status_code == 200, r4.text
    d4 = r4.json()["draft"]
    assert d4["requires"][0]["pattern"] == r"\brm -rf\b"

    # Turn 5: answer q_on_missing.
    r5 = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [],
            "draft_so_far": d4,
            "answers": {"q_on_missing": "block"},
        },
    )
    assert r5.status_code == 200, r5.text
    body5 = r5.json()
    d5 = body5["draft"]
    assert d5["action"] == "block"
    # Still not ready: id is missing.
    assert body5["ready_to_save"] is False
    assert "id" in body5["missing_fields"]

    # Turn 6: answer q_id. The draft now passes the IR validator and
    # ready_to_save flips to True.
    r6 = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [],
            "draft_so_far": d5,
            "answers": {"q_id": "block-bash-rm"},
        },
    )
    assert r6.status_code == 200, r6.text
    body = r6.json()
    assert body["ready_to_save"] is True, body
    assert body["missing_fields"] == []
    assert body["draft"]["id"] == "block-bash-rm"
    # The draft round-trips through the IR loader cleanly.
    from magi_cp.policy.ir import policy_from_dict
    p = policy_from_dict(body["draft"])
    assert p.action == "block"
    assert p.trigger.matcher == "Bash"


# ── new follow-up tests for the hardening pass ────────────────────────


def test_empty_requires_body_blocks_ready_to_save():
    """An empty pattern / criterion / shape_ttl / step must NOT be
    reported as ready_to_save, even when every behavioral field is
    filled. The IR validator would reject the draft on PUT.
    """
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    draft = {
        "id": "block-bash",
        "description": "x",
        "trigger": {
            "host": "claude-code", "event": "PreToolUse", "matcher": "Bash",
        },
        # Empty body: seeded by an earlier q_requires answer.
        "requires": [{"kind": "regex", "pattern": ""}],
        "action": "block",
    }
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": draft, "answers": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ready_to_save"] is False, body
    assert "requires_body" in body["missing_fields"]


def test_draft_so_far_drops_gate_binary_and_unknown_keys():
    """A client cannot smuggle `gate_binary` or other archetype fields
    via draft_so_far. The sanitizer strips them on entry."""
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    poisoned = {
        "id": "block-bash",
        "trigger": {
            "host": "claude-code", "event": "PreToolUse", "matcher": "Bash",
        },
        # SECURITY: every one of these is intentionally NOT in the
        # sanitizer allowlist. They must vanish on the wire.
        "gate_binary": "/tmp/x.sh",
        "on_signature_invalid": "allow",
        "type": "permission",
        "pattern": "evil",
        "permission": "allow",
        "tool_allowlist": ["Bash"],
        "sentinel_re": "(?P<x>.*)",
    }
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": poisoned, "answers": None},
    )
    assert r.status_code == 200, r.text
    out = r.json()["draft"]
    for forbidden in ("gate_binary", "on_signature_invalid", "type",
                       "pattern", "permission", "tool_allowlist",
                       "sentinel_re"):
        assert forbidden not in out, (forbidden, out)


def test_llm_cannot_smuggle_gate_binary_or_host():
    """The LLM-merge whitelist refuses `gate_binary`, `host`, `type`,
    `on_signature_invalid`. A prompt-injected response that tries to
    write any of them must NOT land on the draft."""
    canned = _llm_response(
        message="ok",
        updates={
            "trigger": {
                "host": "evil-runtime", "event": "PreToolUse",
                "matcher": "Bash",
            },
            "gate_binary": "/tmp/pwn.sh",
            "on_signature_invalid": "allow",
            "type": "permission",
        },
        questions=[],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [],
            "draft_so_far": None,
            "answers": {"q_lifecycle": "before_tool_use"},
        },
    )
    assert r.status_code == 200, r.text
    out = r.json()["draft"]
    # host pinned to claude-code regardless of the LLM's pivot attempt.
    assert out["trigger"]["host"] == "claude-code"
    # The other three keys never make it onto the draft.
    for forbidden in ("gate_binary", "on_signature_invalid", "type"):
        assert forbidden not in out, (forbidden, out)


def test_matcher_answer_rejects_unknown_tool():
    """A bogus matcher value (e.g. "banana") must NOT be written onto
    the draft. The wizard re-asks the question on the next turn."""
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    prior = {
        "trigger": {
            "host": "claude-code", "event": "PreToolUse",
        },
    }
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [],
            "draft_so_far": prior,
            "answers": {"q_matcher": "banana"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Matcher was rejected; still missing.
    assert "matcher" in body["missing_fields"]
    assert "matcher" not in body["draft"].get("trigger", {}), body


def test_llm_requires_drop_bad_items():
    """LLM-supplied requires items that don't validate are dropped.
    An uncompilable regex must not land on the draft."""
    canned = _llm_response(
        message="ok",
        updates={
            "requires": [
                {"kind": "regex", "pattern": "[unclosed"},
                # A valid item alongside; it should land.
                {"kind": "regex", "pattern": r"\brm\b"},
            ],
        },
        questions=[],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [],
            "draft_so_far": {
                "trigger": {
                    "host": "claude-code", "event": "PreToolUse",
                    "matcher": "Bash",
                },
            },
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    reqs = r.json()["draft"]["requires"]
    patterns = [r.get("pattern") for r in reqs]
    assert "[unclosed" not in patterns, reqs
    assert r"\brm\b" in patterns, reqs


def test_oversize_assistant_turn_422():
    """Assistant turns are capped symmetrically with user turns; an
    11K-char assistant turn no longer slips through the pydantic
    boundary."""
    c = _client(llm_compiler=FakeLlmProvider([]))
    big = "y" * 5_000
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "assistant", "content": big}],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 422, r.text


def test_answers_per_value_cap_422():
    """A single huge answer value is rejected at the pydantic boundary."""
    c = _client(llm_compiler=FakeLlmProvider([]))
    huge = "x" * 5_000
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [],
            "draft_so_far": None,
            "answers": {"q_matcher": huge},
        },
    )
    assert r.status_code == 422, r.text


def test_invalid_policy_id_answer_dropped():
    """A bad id answer (one that fails `_validate_id`) must NOT land on
    the draft."""
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    prior = {
        "trigger": {
            "host": "claude-code", "event": "PreToolUse", "matcher": "Bash",
        },
        "requires": [{"kind": "regex", "pattern": r"\brm\b"}],
        "action": "block",
    }
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [],
            "draft_so_far": prior,
            "answers": {"q_id": "../escape"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Bad id was rejected; missing_fields still includes id.
    assert "id" in body["missing_fields"], body
    assert "id" not in body["draft"], body


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
    """The server scrubs ALL eight forbidden internal terms (regex /
    shacl / llm_critic / matcher / lifecycle / on_missing /
    EvidenceReq / kind) out of any user-facing string, even when the
    LLM leaks them. Also exercises the Korean surface so a future
    scrubber regression that re-introduces any of these in either
    language ships red.
    """
    canned = _llm_response(
        message=(
            "Pick a regex or a regular expression or a SHACL shape. "
            "Use llm_critic when you need an LLM judge. Build the "
            "EvidenceReq with the right kind. Lifecycle drives the "
            "matcher. Then choose on_missing. "
            "한글로도 LLM이 판단합니다, kind를 고르세요."
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
    # Internal vocabulary is gone (all eight terms + the bare LLM
    # acronym + the lowercase EvidenceReq spelling).
    for forbidden in (
        "regex", "regular expression",
        "shacl",
        "llm_critic", "llm",
        "matcher",
        "lifecycle",
        "on_missing",
        "evidencereq",
        "kind",
    ):
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


# ── D65 run_command intent ────────────────────────────────────────────


def test_run_command_inline_command_lands_on_draft():
    """When the user names a specific inline command body
    ("run pytest -q at final answer"), the conversational compiler
    proposes type=run_command with command=<that text> and an
    appropriate event (Stop). The wizard's verifier vocabulary
    (`requires`, `action`) MUST NOT appear on the draft.
    """
    canned = _llm_response(
        message="This rule will run: pytest -q at the final answer.",
        updates={
            "type": "run_command",
            "command": "pytest -q",
            "runtime": "bash",
            "trigger": {"event": "Stop", "matcher": "*"},
        },
        questions=[],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [
                {"role": "user",
                 "content": "run pytest -q before the agent's final answer"},
            ],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    draft = body["draft"]
    assert draft is not None
    assert draft["type"] == "run_command"
    assert draft["command"] == "pytest -q"
    assert draft["runtime"] == "bash"
    assert draft["trigger"]["event"] == "Stop"
    assert draft["trigger"]["matcher"] == "*"
    # Evidence-archetype keys must NOT be present.
    assert "requires" not in draft
    assert "action" not in draft


def test_run_command_inline_git_status_pre_tool_use():
    """A different canonical phrasing: "run git status before each Bash
    call". The compiler should pick the matching event (PreToolUse +
    Bash) and write the inline command verbatim."""
    canned = _llm_response(
        message="This rule will run: git status before each Bash call.",
        updates={
            "type": "run_command",
            "command": "git status",
            "runtime": "bash",
            "trigger": {"event": "PreToolUse", "matcher": "Bash"},
        },
        questions=[],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [
                {"role": "user",
                 "content": "run git status before each bash call"},
            ],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    assert draft["type"] == "run_command"
    assert draft["command"] == "git status"
    assert draft["trigger"]["event"] == "PreToolUse"
    assert draft["trigger"]["matcher"] == "Bash"


def test_run_command_script_id_missing_prompts_for_upload():
    """When the user mentions a script that has not been uploaded yet
    ("our fact-check script"), the compiler commits to type=run_command
    with no body. The assistant_message MUST point the operator at
    `/scripts` so they can upload it and come back. The wizard reports
    the body as still missing so ready_to_save stays false.
    """
    # The LLM gives the canonical message; the server preserves it.
    canned = _llm_response(
        message=(
            "I'd run your fact-check script, but it isn't uploaded yet. "
            "Upload it at /scripts and come back to enable this rule."
        ),
        updates={
            "type": "run_command",
            "runtime": "bash",
            "trigger": {"event": "Stop", "matcher": "*"},
            "script_id": "",
        },
        questions=[],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [
                {"role": "user",
                 "content": "run our fact-check.py at final answer"},
            ],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    draft = body["draft"]
    assert draft["type"] == "run_command"
    # No inline command and no script_path was committed.
    assert not draft.get("command"), draft
    assert not draft.get("script_path"), draft
    # Assistant message links the operator to /scripts.
    assert "/scripts" in body["assistant_message"], body
    # ready_to_save stays false because the body is empty.
    assert body["ready_to_save"] is False, body
    # The body is reported as missing via the requires_body slot.
    assert "requires_body" in body["missing_fields"], body


def test_run_command_script_id_missing_server_fallback_message():
    """If the LLM forgets to mention /scripts, the server synthesizes
    the prompt so the operator still sees the link."""
    canned = _llm_response(
        message="",  # LLM didn't write a body
        updates={
            "type": "run_command",
            "runtime": "bash",
            "trigger": {"event": "Stop", "matcher": "*"},
        },
        questions=[],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [
                {"role": "user",
                 "content": "run our deploy script at final answer"},
            ],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Server-synthesised fallback mentions /scripts.
    assert "/scripts" in body["assistant_message"], body
    assert body["ready_to_save"] is False


def test_verifier_phrasing_still_produces_evidence_not_run_command():
    """A verifier-shaped phrasing ("block when citations missing") MUST
    still produce an evidence (verifier) policy. The compiler MUST NOT
    pivot to run_command just because the user wrote "block"."""
    # The LLM sees a verifier-shape phrasing — it returns a regex
    # verifier proposal, NOT a run_command.
    canned = _llm_response(
        message="This rule will block when no citation is present.",
        updates={
            "trigger": {"event": "Stop", "matcher": "*"},
            "requires": [
                {"kind": "regex", "pattern": r"\bhttps?://\S+"},
            ],
            "action": "block",
        },
        questions=[],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [
                {"role": "user",
                 "content": "block when citations are missing"},
            ],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    # No run_command discriminator, no run_command-only fields.
    assert draft.get("type") != "run_command"
    for forbidden in ("command", "script_path", "runtime", "args",
                       "timeout_ms", "fail_closed"):
        assert forbidden not in draft, (forbidden, draft)
    # Evidence-archetype fields landed.
    assert draft["requires"][0]["kind"] == "regex"
    assert draft["action"] == "block"


def test_run_command_full_walkthrough_passes_ir_validator():
    """An end-to-end happy path for run_command. Once the four
    behavioral fields (lifecycle, matcher, command body, id) are
    filled, the draft round-trips through `policy_from_dict()` and
    `ready_to_save` flips to True."""
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    draft = {
        "id": "rerun-pytest-on-stop",
        "description": "Run pytest at the agent's final answer",
        "type": "run_command",
        "trigger": {
            "host": "claude-code", "event": "Stop", "matcher": "*",
        },
        "runtime": "bash",
        "command": "pytest -q",
        "timeout_ms": 5_000,
        "fail_closed": False,
    }
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": draft, "answers": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["missing_fields"] == [], body
    assert body["needs_more"] is False
    assert body["ready_to_save"] is True

    # The resulting draft must pass the RunCommandPolicy validator.
    from magi_cp.policy.ir import policy_from_dict, RunCommandPolicy
    p = policy_from_dict(body["draft"])
    assert isinstance(p, RunCommandPolicy)
    assert p.command == "pytest -q"
    assert p.trigger.event == "Stop"


def test_llm_cannot_smuggle_dangerous_run_command_fields():
    """The run_command merge whitelist refuses oversized commands,
    bad script ids, illegal runtimes, and oversized timeouts even when
    the LLM proposes them."""
    canned = _llm_response(
        message="ok",
        updates={
            "type": "run_command",
            "command": "x" * 10_000,           # > 4_000 inline cap
            "runtime": "ruby",                 # not in {bash, python3, node}
            "args": ["ok", "x" * 1_000],       # second arg > 256
            "timeout_ms": 1_000_000,            # > 30_000 cap
            "fail_closed": "yes",              # not a bool
            "script_id": "nothex",             # bad shape
            "trigger": {"event": "Stop", "matcher": "*"},
        },
        questions=[],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": None, "answers": None},
    )
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    # The discriminator landed.
    assert draft["type"] == "run_command"
    # Every dangerous proposal was rejected.
    assert "command" not in draft, draft
    assert draft.get("runtime") != "ruby"
    assert "args" not in draft, draft
    assert "timeout_ms" not in draft, draft
    assert "fail_closed" not in draft, draft
    assert "script_path" not in draft, draft


def test_run_command_inline_command_via_requires_body_answer():
    """The wizard's `q_requires_body` answer path writes the inline
    command body on a run_command draft (rather than a regex pattern
    onto a requires item). This keeps the existing wizard question
    plumbing reusable for run_command authoring."""
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    prior = {
        "type": "run_command",
        "id": "rerun-pytest",
        "trigger": {
            "host": "claude-code", "event": "Stop", "matcher": "*",
        },
        "runtime": "bash",
    }
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [],
            "draft_so_far": prior,
            "answers": {"q_requires_body": "pytest -q"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    draft = body["draft"]
    assert draft["command"] == "pytest -q"
    # The draft is now valid and saves.
    assert body["ready_to_save"] is True, body


def test_run_command_sanitizer_drops_unknown_runtime_on_draft_entry():
    """A client cannot smuggle an illegal `runtime` ("ruby") via
    draft_so_far when the draft is run_command. The sanitizer drops
    it on entry."""
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    poisoned = {
        "type": "run_command",
        "id": "rerun-pytest",
        "trigger": {
            "host": "claude-code", "event": "Stop", "matcher": "*",
        },
        "command": "pytest -q",
        "runtime": "ruby",  # not allowed
        "timeout_ms": 99_999,  # also out of range
        "fail_closed": "yes",  # not a bool
    }
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": poisoned, "answers": None},
    )
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    assert draft.get("runtime") != "ruby", draft
    assert "timeout_ms" not in draft or 100 <= draft["timeout_ms"] <= 30_000
    assert "fail_closed" not in draft or isinstance(draft["fail_closed"], bool)


def test_llm_cannot_smuggle_unsupported_type_discriminator():
    """The `type` discriminator is gated to `run_command` only. A
    prompt-injected pivot to `permission` / `subagent` / `mcp_gating`
    must NOT land on the draft (the wizard's question vocabulary
    cannot complete those archetypes)."""
    canned = _llm_response(
        message="ok",
        updates={
            "type": "permission",
            "trigger": {"event": "PreToolUse", "matcher": "Bash"},
        },
        questions=[],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": None, "answers": None},
    )
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    assert "type" not in draft or draft["type"] == "run_command", draft
