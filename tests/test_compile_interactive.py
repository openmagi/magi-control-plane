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
    # Isolate each client in its OWN temp directory. create_app derives the
    # policy-GROUP store path as `<dir>/policy-groups.json` (sibling of the
    # policy store), so a shared parent dir (plain /tmp) would let one
    # test's saved compound policy leak into another test's context-aware
    # reuse detection. A per-call mkdtemp keeps the group store isolated.
    d = tempfile.mkdtemp(prefix="magi-cp-compile-")
    path = os.path.join(d, "policies.json")
    with open(path, "w") as f:
        f.write("[]")
    return path


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
    # Q102 — id auto-gen: once every behavioral field is filled,
    # the server synthesises an id from matcher + verifier/action so
    # the operator never has to type a policy id by hand. The id is
    # overrideable in a follow-up turn by the operator; for the
    # default flow ready_to_save flips True here.
    assert body5["ready_to_save"] is True, body5
    assert body5["missing_fields"] == []
    # Auto-id is matcher + action slug (no verifier step in this
    # regex-based draft).
    assert d5["id"] == "bash-block"
    # The draft round-trips through the IR loader cleanly.
    from magi_cp.policy.ir import policy_from_dict
    p = policy_from_dict(d5)
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
    # Q102 — bad id explicitly typed by the operator is dropped, and
    # the server falls back to the auto-id slug so the operator is
    # never blocked. The auto-id has the canonical
    # `<matcher>-<verifier>-<action>` shape; the malicious
    # "../escape" is gone from the draft.
    assert body["draft"].get("id") != "../escape"
    # ready_to_save flips True via auto-id (the prior behavioral
    # fields are all set in `prior`).
    assert body["ready_to_save"] is True
    assert body["missing_fields"] == []
    # Auto-id shape: lowercase, hyphenated, no traversal chars.
    auto_id = body["draft"].get("id", "")
    assert auto_id and "/" not in auto_id and ".." not in auto_id


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


def test_provider_error_compile_interactive_maps_to_502():
    """A configured but failing provider (wrong key, rate-limit, network error)
    returns 502 - NOT 500 - so the proxy can classify it as provider_error
    and show the actionable 'check your API key' flash (R5-01)."""
    from magi_cp.llm.provider import LlmProviderError

    class _BadKeyProvider:
        def complete(self, messages):  # noqa: ANN001
            raise LlmProviderError("anthropic http error: 401 invalid api key")

    c = _client(llm_compiler=_BadKeyProvider())

    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": None, "answers": None},
    )
    assert r.status_code == 502, r.text
    assert "LLM provider error" in r.text
    # The upstream body is truncated so env-var secrets or full error
    # blobs don't leak through to the client.
    assert "invalid api key" in r.text


def test_provider_error_compile_endpoint_maps_to_502():
    """Same contract for the one-shot /policies/compile endpoint (R5-01)."""
    from magi_cp.llm.provider import LlmProviderError

    class _BadKeyProvider:
        def complete(self, messages):  # noqa: ANN001
            raise LlmProviderError("openai http error: 429 rate limit exceeded")

    # Reviewer won't be called because compiler raises first.
    c = _client(llm_compiler=_BadKeyProvider(), llm_reviewer=_BadKeyProvider())

    r = c.post(
        "/policies/compile",
        headers=HEADERS,
        json={"nl": "block bash when citation_verify fails"},
    )
    assert r.status_code == 502, r.text
    assert "LLM provider error" in r.text


def test_unconfigured_provider_503_unchanged_after_pr4():
    """Provider-None (no provider configured at all) must STILL return 503
    with the existing wording so the provider_unconfigured proxy branch
    stays intact (regression guard for R5-01 fix, PR-4)."""
    c = _client(llm_compiler=None)

    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": None, "answers": None},
    )
    assert r.status_code == 503
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


def test_assistant_message_is_state_authored_not_llm_authored():
    """Q103 — the LLM's `assistant_message` is dropped on every turn
    and the server emits its own state-machine-driven copy. Even when
    the LLM tries to leak internal vocabulary or push marketing text,
    the wire `assistant_message` reflects the deterministic builder
    output for the current conversation state.
    """
    # The LLM emits a forbidden-term grab-bag and a confident "ready"
    # pre-claim. Both must be invisible on the wire.
    bogus = (
        "Draft is ready! Pick a regex or a SHACL shape. Use llm_critic "
        "when you need an LLM judge. Build the EvidenceReq with the "
        "right kind. Lifecycle drives the matcher. Then choose "
        "on_missing. 한글로도 LLM이 판단합니다, kind를 고르세요. "
        "Marketing tagline: buy our enterprise edition."
    )
    canned = _llm_response(message=bogus, updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": None, "answers": None},
    )
    assert r.status_code == 200
    msg = r.json()["assistant_message"]
    # The LLM's bogus content is verifiably gone — not a single one of
    # its distinctive phrases leaks through.
    for leaked in (
        "Draft is ready",
        "Marketing tagline",
        "enterprise edition",
        "EvidenceReq",
        "llm_critic",
    ):
        assert leaked not in msg, (leaked, msg)
    # Internal vocabulary that the LLM used must not appear in any
    # form (the deterministic builder never emits these terms).
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
    # The deterministic S0 copy is what surfaces.
    assert msg == "What should we check?"


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
    # Evidence-archetype fields landed. (The test's point is that a
    # verifier phrasing stays evidence, not run_command; the requested
    # block at Stop is honestly downgraded to audit by AF-5 since block is
    # not available there.)
    assert draft["requires"][0]["kind"] == "regex"
    assert draft["action"] == "audit"


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


# ── D65 follow-up regression tests ────────────────────────────────────


def test_mixed_archetype_payload_drops_verifier_fields_on_run_command():
    """Issue 1 P1 — when the LLM emits a mixed-archetype payload
    (type=run_command + requires + action), the verifier-only fields
    MUST NOT land on the draft. Iteration order of dict keys must not
    affect the outcome.
    """
    canned = _llm_response(
        message="This rule will run: pytest -q at the final answer.",
        updates={
            # Iteration order in CPython 3.7+ preserves insertion;
            # verifier-shaped keys come AFTER `type` here so the
            # pre-pop in step_compile is the only defense.
            "type": "run_command",
            "command": "pytest -q",
            "runtime": "bash",
            "trigger": {"event": "Stop", "matcher": "*"},
            "requires": [{"kind": "regex", "pattern": r"\bhttps?://\S+"}],
            "action": "block",
            "on_missing": "block",
        },
        questions=[],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [
                {"role": "user", "content": "run pytest -q at final answer"},
            ],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    assert draft["type"] == "run_command"
    assert draft["command"] == "pytest -q"
    assert "requires" not in draft, draft
    assert "action" not in draft, draft
    assert "on_missing" not in draft, draft


def test_verifier_intent_with_runnable_tool_name_stays_evidence():
    """Issue 2 P1 — "ensure pytest passes at the final answer" is a
    VERIFIER intent even though it names a tool ("pytest"). The
    server-side heuristic refuses `type=run_command` when the user
    turn matches a verifier verb without a runnable verb.
    """
    canned = _llm_response(
        message="I'll add a check at the final answer.",
        updates={
            # The LLM mis-classifies the request as a run_command. The
            # server-side heuristic must reject the discriminator.
            "type": "run_command",
            "command": "pytest",
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
                 "content": "ensure pytest passes at the final answer"},
            ],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    draft = r.json()["draft"] or {}
    # The discriminator must NOT land.
    assert draft.get("type") != "run_command", draft
    # And the body fields the LLM tried to smuggle must not appear.
    for forbidden in ("command", "script_path", "runtime", "args",
                       "timeout_ms", "fail_closed"):
        assert forbidden not in draft, (forbidden, draft)


def test_mixed_runnable_and_verifier_verb_admits_run_command():
    """The verifier-intent heuristic must NOT swallow phrasings that
    explicitly contain a runnable verb. "Run pytest to verify the
    tests passed" IS a run_command — the user said "run".
    """
    canned = _llm_response(
        message="This rule will run: pytest at the final answer.",
        updates={
            "type": "run_command",
            "command": "pytest",
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
                 "content": "run pytest to verify the tests passed"},
            ],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    assert draft["type"] == "run_command"
    assert draft["command"] == "pytest"


def test_llm_key_order_command_before_type_lands_command():
    """Issue 8 P1 — the run_command merge MUST NOT depend on LLM dict
    key order. With `command` emitted BEFORE `type`, the inline
    command must still land on the draft.
    """
    canned = _llm_response(
        message="This rule will run: pytest -q at the final answer.",
        updates={
            # Deliberately emit body fields BEFORE the discriminator;
            # the pre-pass in step_compile must commit `type` first.
            "command": "pytest -q",
            "runtime": "bash",
            "trigger": {"event": "Stop", "matcher": "*"},
            "type": "run_command",
        },
        questions=[],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [
                {"role": "user", "content": "run pytest -q at final answer"},
            ],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    assert draft["type"] == "run_command"
    assert draft["command"] == "pytest -q", draft
    assert draft["runtime"] == "bash"


def test_continuation_turn_no_retype_drops_late_verifier_fields():
    """Issue 1 P1 — on a continuation turn the draft already carries
    type=run_command and the LLM sends only `requires` + `action`
    without re-stating `type`. The verifier-only fields MUST be
    rejected and the draft stays a run_command.
    """
    canned = _llm_response(
        message="Got it.",
        updates={
            "requires": [{"kind": "regex", "pattern": r"\bhttps?://\S+"}],
            "action": "block",
        },
        questions=[],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    prior = {
        "type": "run_command",
        "id": "rerun-pytest",
        "trigger": {
            "host": "claude-code", "event": "Stop", "matcher": "*",
        },
        "command": "pytest -q",
        "runtime": "bash",
    }
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": prior, "answers": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    draft = body["draft"]
    assert draft["type"] == "run_command"
    assert draft["command"] == "pytest -q"
    assert "requires" not in draft, draft
    assert "action" not in draft, draft


def test_script_id_alias_round_trips_through_sanitizer():
    """Issue 3 P2 — a friendly client may echo `script_id` (the wire
    vocabulary) back to the server in `draft_so_far`. The sanitizer
    must alias it onto the IR field `script_path` so the value
    survives one wizard round-trip.
    """
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    sid = "a" * 64
    poisoned = {
        "type": "run_command",
        "id": "run-our-script",
        "trigger": {
            "host": "claude-code", "event": "Stop", "matcher": "*",
        },
        "runtime": "bash",
        # Wire vocab — the IR uses `script_path` internally.
        "script_id": sid,
    }
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": poisoned, "answers": None},
    )
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    assert draft.get("script_path") == sid, draft


def test_script_id_and_command_in_same_update_deterministic_winner():
    """Issue 6 P2 — when the LLM emits BOTH a valid script_id and an
    inline command in one payload, the winner must be deterministic
    regardless of dict iteration order. Policy: the uploaded script
    wins; the inline command is dropped.
    """
    sid = "b" * 64
    # Emit script_id BEFORE command.
    canned_a = _llm_response(
        message="ok",
        updates={
            "type": "run_command",
            "script_id": sid,
            "command": "pytest -q",
            "runtime": "bash",
            "trigger": {"event": "Stop", "matcher": "*"},
        },
        questions=[],
    )
    # Emit command BEFORE script_id.
    canned_b = _llm_response(
        message="ok",
        updates={
            "type": "run_command",
            "command": "pytest -q",
            "script_id": sid,
            "runtime": "bash",
            "trigger": {"event": "Stop", "matcher": "*"},
        },
        questions=[],
    )
    for canned in (canned_a, canned_b):
        c = _client(llm_compiler=FakeLlmProvider([canned]))
        r = c.post(
            "/policies/compile-interactive",
            headers=HEADERS,
            json={
                "history": [
                    {"role": "user",
                     "content": "run our script at final answer"},
                ],
                "draft_so_far": None,
                "answers": None,
            },
        )
        assert r.status_code == 200, r.text
        draft = r.json()["draft"]
        # Deterministic winner: script_id beats command.
        assert draft.get("script_path") == sid, draft
        assert "command" not in draft, draft


def test_script_id_missing_drops_requires_body_question():
    """Issue 7 P1 — when the assistant_message points at /scripts, the
    wizard MUST NOT also ask "Which command should we run?". The
    requires_body question is suppressed so the operator's only call
    to action is the /scripts link.
    """
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
    assert "/scripts" in body["assistant_message"], body
    question_ids = {q["id"] for q in body["questions"]}
    assert "q_requires_body" not in question_ids, body


def test_scripts_substring_in_source_path_still_synthesizes_fallback():
    """Issue 10 P2 — the /scripts fallback gate is a whole-word match.
    An LLM message that mentions a source path like `/scripts/foo.py`
    in unrelated prose does NOT suppress the fallback; the wizard
    must still synthesise the upload-first guidance.
    """
    canned = _llm_response(
        # Mentions /scripts/foo.py as a source path, NOT as the route.
        message="I saw your reference to /scripts/foo.py in the spec.",
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
    # The synthesised guidance mentions the /scripts route.
    msg = body["assistant_message"]
    # Server-synthesised fallback must have run — message contains
    # the canonical "Upload it at /scripts" phrasing (en) OR the
    # Korean equivalent.
    assert ("/scripts and come back" in msg
            or "/scripts에 업로드한 뒤" in msg), msg


# ── #100 — LLM intent extraction must survive (no canned override) ────


def test_q100_llm_extracted_citation_intent_survives_to_draft():
    """When the user types a Korean freeform request that names a
    verifier (citation_verify) and a lifecycle hint ("final answer"),
    and the LLM dutifully proposes a draft_updates payload that maps
    that intent to {trigger.event=Stop, requires=[step:citation_verify]},
    the server MUST merge it onto the draft. A previous revision
    silently dropped the LLM's draft_updates whenever the proposed
    question set fell outside the canonical slice; this test pins
    that draft_updates lives on its own merge path and is not
    contingent on the question logic.
    """
    canned = _llm_response(
        message="리서치 작업에서 citation_verify 를 최종 답변 직전에 돌리도록 잡았어요.",
        updates={
            "id": "research-citations",
            "description": "research citation verify",
            "trigger": {"event": "Stop", "matcher": "*"},
            "requires": [{"kind": "step", "step": "citation_verify",
                          "verdict": "pass"}],
            "action": "audit",
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
                 "content":
                     "리서치 작업시 외부 출처를 활용한 주장에는 "
                     "반드시 citation을 달게 해줘"},
            ],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    draft = body["draft"]
    assert draft is not None, body
    # Verifier extracted onto requires[]. Server normalises
    # `{kind:"step", step, verdict}` down to the legacy `{step, verdict}`
    # row shape for the EvidencePolicy validator; either shape proves
    # the extraction made it onto requires[].
    req = draft.get("requires")
    assert isinstance(req, list) and len(req) == 1, req
    assert req[0].get("step") == "citation_verify"
    assert req[0].get("verdict") == "pass"
    # Lifecycle extracted onto trigger.event.
    assert draft["trigger"]["event"] == "Stop", draft
    # Action populated.
    assert draft.get("action") == "audit", draft
    # All four required fields populated → ready_to_save true, no more
    # questions.
    assert body["ready_to_save"] is True, body
    assert body["questions"] == [], body


def test_q100_system_prompt_carries_extraction_directive():
    """Pin the EXTRACTION DIRECTIVE in the system prompt so a future
    refactor that strips it (returning to canned-first behaviour)
    trips this test. The directive is what tells the LLM to read the
    freeform user text and emit draft_updates BEFORE thinking about
    questions; removing it is the root cause of the "conversational
    mode is identical to guided" UX bug screenshotted in #100.
    """
    from magi_cp.policy.nl_compiler_interactive import (
        _SYSTEM_INTERACTIVE_TMPL,
    )
    tmpl = _SYSTEM_INTERACTIVE_TMPL
    assert "EXTRACTION DIRECTIVE" in tmpl, tmpl[:200]
    # The Korean and English verifier vocabulary must both appear so
    # an operator typing Korean ("출처", "인용") gets the same
    # extraction quality as one typing English.
    assert "citation_verify" in tmpl
    assert "출처" in tmpl
    assert "인용" in tmpl
    assert "privilege_scan" in tmpl
    assert "source_allowlist" in tmpl
    assert "structured_output" in tmpl
    assert "prompt_injection_screen" in tmpl


def test_q100_source_allowlist_korean_phrasing_extraction():
    """Korean operators commonly say "신뢰할 수 있는 출처" or "외부 web
    search 출처" rather than "allowlist". The Q100 follow-up directive
    + few-shot example must extract source_allowlist from that natural
    phrasing.
    """
    canned = _llm_response(
        message="리서치 작업의 WebFetch 출처를 source_allowlist 로 잡았어요.",
        updates={
            "id": "research-source-allowlist-audit",
            "description": "Audit WebFetch source allowlist on research",
            "trigger": {"event": "PreToolUse", "matcher": "WebFetch"},
            "requires": [{"kind": "step", "step": "source_allowlist",
                          "verdict": "pass"}],
            "action": "audit",
        },
        questions=[],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{
                "role": "user",
                "content": "리서치 목적으로 외부 web search를 할 때 "
                           "신뢰할 수 있는 출처인지를 검사하고 "
                           "로그를 남기고 싶어",
            }],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    draft = body["draft"] or {}
    # Q102 — this phrasing is AMBIGUOUS ("신뢰할 수 있는 출처" could be
    # any of source_allowlist / prompt_injection_screen /
    # citation_verify). The server now strips the LLM's guessed
    # verifier and surfaces the disambiguation menu instead of
    # committing source_allowlist confidently.
    assert not draft.get("requires"), draft
    assert draft.get("trigger", {}).get("matcher") == "WebFetch"
    assert draft.get("action") == "audit"
    assert body["ready_to_save"] is False
    msg = body.get("assistant_message", "")
    assert "도메인 허용 목록" in msg


def test_q100_directive_carries_disambiguation_rule():
    """Pin the system prompt's disambiguation rule so a future
    refactor that drops it (and slides the LLM back into "guess
    source_allowlist for any trustworthy-source phrase" behaviour)
    trips loudly.
    """
    from magi_cp.policy.nl_compiler_interactive import (
        _SYSTEM_INTERACTIVE_TMPL,
    )
    tmpl = _SYSTEM_INTERACTIVE_TMPL
    # The disambiguation rule + the four ambiguous trigger phrases.
    assert "DISAMBIGUATION RULE" in tmpl
    assert "신뢰도" in tmpl
    assert "신뢰성" in tmpl
    assert "출처 검증" in tmpl
    assert "trusted source" in tmpl
    # The rule must list the 5 wired verifiers under "Pick a
    # verifier ONLY when".
    assert "Pick a verifier ONLY when" in tmpl
    assert "source_allowlist" in tmpl
    assert "prompt_injection_screen" in tmpl
    assert "citation_verify" in tmpl
    assert "privilege_scan" in tmpl
    assert "structured_output" in tmpl
    # The few-shot example for the ambiguous phrasing must NOT
    # emit a verifier; the prior research-source-allowlist-audit
    # example was the source of the bias.
    assert "research-source-allowlist-audit" not in tmpl
    # Turn-1 mandate language still pinned (kept for unambiguous
    # extraction cases).
    assert "TURN 1 MANDATE" in tmpl


# ── #100 final: deterministic extraction (no LLM dependency) ──────────


def test_q100_deterministic_extraction_korean_source_allowlist():
    """The exact freeform Kevin typed in the screenshot. Server MUST
    populate source_allowlist + WebFetch + PreToolUse + audit BEFORE
    the LLM is called, so the LLM behavior is irrelevant for the
    happy-path extraction.
    """
    # The exact KO screenshot phrase Kevin tested with. "소스의 신뢰도
    # 검사" reads as a verify intent but does NOT name a specific
    # verifier — three verifiers all read as "source trustworthiness"
    # depending on intent. The extractor MUST NOT guess; instead it
    # leaves `requires` unset and the server emits a disambiguation
    # menu in assistant_message so the operator picks. Matcher /
    # action are still inferred unambiguously and survive.
    canned = _llm_response(message="확인했어요.", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{
                "role": "user",
                "content": "리서치 업무에서 외부 자료를 참조할 때 "
                           "소스의 신뢰도를 검사해서 기록을 남기면 좋겠어.",
            }],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    draft = body["draft"] or {}
    # requires MUST be unset on this ambiguous phrasing.
    assert not draft.get("requires"), draft
    # The unambiguous fields (matcher + action) DO get extracted.
    assert draft.get("trigger", {}).get("matcher") == "WebFetch"
    assert draft.get("action") == "audit"
    # The disambiguation menu must appear in assistant_message.
    msg = body.get("assistant_message", "")
    assert "도메인 허용 목록" in msg, msg
    assert "인용 검증" in msg, msg
    assert "인젝션" in msg, msg
    assert "민감정보 스캔" in msg, msg
    assert "스키마 검증" in msg, msg


def test_q100_deterministic_extraction_citation_korean():
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{
                "role": "user",
                "content": "최종 답변에서 인용한 출처가 진짜인지 확인하고 "
                           "안 맞으면 경고만 띄워줘",
            }],
            "draft_so_far": None,
            "answers": None,
        },
    )
    body = r.json()
    draft = body["draft"]
    assert draft is not None, body
    req = draft.get("requires")
    assert req[0].get("step") == "citation_verify"
    assert draft["trigger"]["event"] == "Stop"


def test_q100_deterministic_extraction_does_not_overwrite_existing_draft():
    """When draft_so_far already has fields, extraction must NOT
    overwrite them. The user's prior answers / LLM-set fields win.
    """
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    prior_draft = {
        "trigger": {
            "host": "claude-code",
            "event": "Stop",
            "matcher": "*",
        },
        "requires": [{"kind": "step", "step": "citation_verify",
                      "verdict": "pass"}],
        "action": "block",
    }
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            # The freeform text names source_allowlist, but the prior
            # draft already commits to citation_verify + block. The
            # extractor's source_allowlist guess must lose.
            "history": [{
                "role": "user",
                "content": "신뢰할 수 있는 출처만 허용하고 싶어 audit",
            }],
            "draft_so_far": prior_draft,
            "answers": None,
        },
    )
    body = r.json()
    draft = body["draft"]
    # The extractor's source_allowlist guess must NOT overwrite the prior
    # citation_verify commitment - that is what this test guards.
    assert draft["requires"][0]["step"] == "citation_verify"
    # The prior draft's block at Stop is illegal, so AF-5 honestly downgrades
    # it to audit (extraction precedence is unaffected by that repair).
    assert draft["action"] == "audit"


# ── Q103 — conversation state model ───────────────────────────────────


def test_q103_conversation_state_returns_correct_state_for_each_shape():
    """`_conversation_state(draft)` is a pure function of the draft and
    returns the canonical state enum for the five shaped drafts the
    state model recognises. No history, no turn counting.
    """
    from magi_cp.policy.nl_compiler_interactive import _conversation_state

    # S0: no requires committed yet. Empty dict, None, missing
    # `requires` all collapse to the same state.
    assert _conversation_state(None) == "S0_intent_unknown"
    assert _conversation_state({}) == "S0_intent_unknown"
    assert _conversation_state(
        {"trigger": {"event": "Stop", "matcher": "*"}}
    ) == "S0_intent_unknown"

    # S1: requires row seeded but the body field is still empty
    # (kind picked, no pattern / criterion / shape_ttl / step yet).
    assert _conversation_state({
        "trigger": {"event": "Stop", "matcher": "*"},
        "requires": [{"kind": "regex", "pattern": ""}],
    }) == "S1_verifier_selected"
    assert _conversation_state({
        "trigger": {"event": "Stop", "matcher": "*"},
        "requires": [{"kind": "llm_critic", "criterion": ""}],
    }) == "S1_verifier_selected"

    # S2: body filled, id empty. on_missing may still be missing —
    # the state model still reports S2 because body is filled.
    assert _conversation_state({
        "trigger": {"event": "PreToolUse", "matcher": "Bash"},
        "requires": [{"kind": "regex", "pattern": r"\brm -rf\b"}],
        "action": "block",
        # id intentionally missing.
    }) == "S2_body_filled"

    # S4: every field filled, validator passes.
    s4_draft = {
        "id": "bash-block",
        "trigger": {
            "host": "claude-code",
            "event": "PreToolUse",
            "matcher": "Bash",
        },
        "requires": [{"kind": "regex", "pattern": r"\brm -rf\b"}],
        "action": "block",
    }
    assert _conversation_state(s4_draft) == "S4_ready"

    # S3: id present but the validator still rejects — e.g. a regex
    # pattern that exceeds the IR's 2000-char per-body cap. The
    # missing-fields heuristic says "complete" (id + body + trigger +
    # action all present) but `policy_from_dict` raises.
    assert _conversation_state({
        "id": "bash-block",
        "trigger": {
            "host": "claude-code",
            "event": "PreToolUse",
            "matcher": "Bash",
        },
        # 3000-char pattern: passes the body-empty check (truthy string)
        # but the IR's EvidenceReq.validate() rejects on `>2000 chars`.
        "requires": [{"kind": "regex", "pattern": "x" * 3000}],
        "action": "block",
    }) == "S3_id_pending"


def test_q103_conversation_state_handles_run_command_drafts():
    """run_command drafts share the state enum but S1 is unreachable.
    Body absence collapses to S0; body present + no id = S2; ready = S4.
    """
    from magi_cp.policy.nl_compiler_interactive import _conversation_state

    # S0 — type=run_command but neither `command` nor `script_path`.
    assert _conversation_state({
        "type": "run_command",
        "trigger": {"event": "Stop", "matcher": "*"},
    }) == "S0_intent_unknown"

    # S2 — body set via `command`, no id.
    assert _conversation_state({
        "type": "run_command",
        "trigger": {
            "host": "claude-code", "event": "Stop", "matcher": "*",
        },
        "runtime": "bash",
        "command": "pytest -q",
    }) == "S2_body_filled"

    # S4 — body + id + valid trigger.
    assert _conversation_state({
        "type": "run_command",
        "id": "rerun-pytest",
        "trigger": {
            "host": "claude-code", "event": "Stop", "matcher": "*",
        },
        "runtime": "bash",
        "command": "pytest -q",
    }) == "S4_ready"


def test_q103_build_assistant_message_emits_state_correct_copy():
    """`_build_assistant_message(state, draft, ko=...)` returns the
    deterministic copy mapped to each state. Verifies both KO and EN
    surfaces for the five states + the S0 ambiguity fork.
    """
    from magi_cp.policy.nl_compiler_interactive import (
        _build_assistant_message,
    )

    # S0 + nothing extracted, KO + EN.
    msg_ko = _build_assistant_message(
        "S0_intent_unknown", {}, ko=True,
    )
    assert msg_ko == "어떤 검사를 원하시는지 알려주세요."
    msg_en = _build_assistant_message(
        "S0_intent_unknown", {}, ko=False,
    )
    assert msg_en == "What should we check?"

    # S0 + ambiguous extraction → disambiguation menu (5 verifiers).
    msg_amb = _build_assistant_message(
        "S0_intent_unknown", {}, ko=True, ambiguous=True,
    )
    assert "도메인 허용 목록" in msg_amb
    assert "인용 검증" in msg_amb
    assert "인젝션" in msg_amb
    assert "민감정보 스캔" in msg_amb
    assert "스키마 검증" in msg_amb

    # S0 + something extracted (matcher + action), no verifier yet.
    msg_partial = _build_assistant_message(
        "S0_intent_unknown",
        {"trigger": {"matcher": "WebFetch"}, "action": "audit"},
        ko=True,
        extracted={
            "trigger": {"matcher": "WebFetch"},
            "action": "audit",
        },
    )
    assert "WebFetch" in msg_partial
    assert "audit" in msg_partial
    assert "다음으로" in msg_partial

    # S1 + per-kind body prompts.
    s1_regex = {"requires": [{"kind": "regex", "pattern": ""}]}
    assert "패턴" in _build_assistant_message(
        "S1_verifier_selected", s1_regex, ko=True,
    )
    s1_llm = {"requires": [{"kind": "llm_critic", "criterion": ""}]}
    assert "AI" in _build_assistant_message(
        "S1_verifier_selected", s1_llm, ko=True,
    )
    s1_shacl = {"requires": [{"kind": "shacl", "shape_ttl": ""}]}
    assert "SHACL" in _build_assistant_message(
        "S1_verifier_selected", s1_shacl, ko=True,
    )
    s1_step = {"requires": [{"kind": "step", "step": "", "verdict": "pass"}]}
    assert "검증자" in _build_assistant_message(
        "S1_verifier_selected", s1_step, ko=True,
    )

    # S2 — name-it prompt with auto-id preview embedded.
    s2_draft = {
        "trigger": {"event": "PreToolUse", "matcher": "Bash"},
        "requires": [{"kind": "regex", "pattern": r"\brm\b"}],
        "action": "block",
    }
    msg_s2 = _build_assistant_message(
        "S2_body_filled", s2_draft, ko=True,
    )
    assert "이름" in msg_s2
    assert "bash-block" in msg_s2  # auto-id preview

    # S3 — validator error surfaced with plain-language scrub.
    msg_s3 = _build_assistant_message(
        "S3_id_pending", {"id": "x"}, ko=True,
        validator_error="EvidenceReq.pattern is missing",
    )
    # Internal vocab is scrubbed.
    assert "EvidenceReq" not in msg_s3
    # Plain-language guidance is present.
    assert "한 단계 더" in msg_s3

    # S4 — ready message embeds the id.
    msg_s4 = _build_assistant_message(
        "S4_ready", {"id": "block-bash-rm"}, ko=True,
    )
    assert "초안 준비됐어요" in msg_s4
    assert "block-bash-rm" in msg_s4
    assert "이 정책 저장" in msg_s4
    msg_s4_en = _build_assistant_message(
        "S4_ready", {"id": "block-bash-rm"}, ko=False,
    )
    assert "Draft is ready" in msg_s4_en
    assert "block-bash-rm" in msg_s4_en


def test_q103_should_apply_ambiguity_disambiguation_predicate():
    """`_should_apply_ambiguity_disambiguation(draft, extracted)` is
    the single-line replacement for the prior first-turn-only hack.
    Returns True iff the post-merge draft is still in S0 AND the
    extractor flagged ambiguity.
    """
    from magi_cp.policy.nl_compiler_interactive import (
        _should_apply_ambiguity_disambiguation,
    )

    # Ambiguous extraction + S0 draft → True.
    assert _should_apply_ambiguity_disambiguation(
        {}, {"__verifier_ambiguous__": True},
    ) is True
    assert _should_apply_ambiguity_disambiguation(
        None, {"__verifier_ambiguous__": True},
    ) is True

    # Ambiguous extraction but draft already has a verifier row
    # (S1+) → False. Replaces the prior `if draft.get("requires"):`
    # hack with a state-based check.
    assert _should_apply_ambiguity_disambiguation(
        {"requires": [{"kind": "regex", "pattern": "x"}]},
        {"__verifier_ambiguous__": True},
    ) is False

    # No ambiguity flag → False regardless of state.
    assert _should_apply_ambiguity_disambiguation(
        {}, {"trigger": {"matcher": "WebFetch"}},
    ) is False
    assert _should_apply_ambiguity_disambiguation({}, None) is False
    assert _should_apply_ambiguity_disambiguation({}, {}) is False


def test_q103_llm_assistant_message_is_dropped_marketing_text():
    """The LLM emits "totally bogus marketing text" as its
    assistant_message, but the server replaces it with the
    deterministic state-correct message. Pins the contract that the
    LLM cannot author the user-facing status line.
    """
    bogus = (
        "BUY OUR ENTERPRISE EDITION! Draft is ready! "
        "Click here to upgrade. Limited time offer. "
        "100% money back guarantee. Five-star reviews."
    )
    canned = _llm_response(message=bogus, updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": None, "answers": None},
    )
    assert r.status_code == 200, r.text
    msg = r.json()["assistant_message"]
    # No fragment of the bogus LLM text leaks.
    for leaked in (
        "BUY OUR", "ENTERPRISE EDITION", "money back", "Click here",
        "Draft is ready", "Five-star",
    ):
        assert leaked not in msg, (leaked, msg)
    # The deterministic S0 message is what surfaces (empty draft,
    # empty history → S0 + nothing extracted, English locale).
    assert msg == "What should we check?"


def test_q103_llm_assistant_message_is_dropped_when_state_is_s4():
    """When the merged draft is fully ready, the LLM's premature
    "I'm working on it" or unrelated message MUST be replaced by the
    deterministic S4 ready copy. This used to be enforced by a phrase
    list override; the state machine now owns it directly.
    """
    bogus = "Hold on, still thinking — give me a moment."
    canned = _llm_response(message=bogus, updates={}, questions=[])
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
    assert body["ready_to_save"] is True
    msg = body["assistant_message"]
    # LLM's "give me a moment" framing is gone.
    assert "Hold on" not in msg
    assert "give me a moment" not in msg
    # Deterministic S4 framing surfaces.
    assert "Draft is ready" in msg
    assert "block-bash-rm" in msg
    assert "Save this policy" in msg


def test_q103_llm_premature_completion_claim_replaced_by_state_message():
    """Replaces the deleted "거의 다 됐어요 / 완성됐 / Draft is ready"
    pattern-match override. When the LLM falsely claims completion
    while the draft is still in S1 (verifier picked, body empty), the
    server now emits the deterministic S1 body-prompt copy instead.
    """
    bogus_ko = "거의 다 됐어요! 그냥 우측 Save 누르세요."
    canned = _llm_response(message=bogus_ko, updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    draft = {
        "trigger": {
            "host": "claude-code", "event": "PreToolUse", "matcher": "Bash",
        },
        # Verifier picked, body empty → S1.
        "requires": [{"kind": "regex", "pattern": ""}],
        "action": "block",
    }
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user", "content": "안녕"}],
            "draft_so_far": draft,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    msg = body["assistant_message"]
    # The bogus "거의 다 됐어요" is gone.
    assert "거의 다 됐어요" not in msg
    # Deterministic S1 regex-body prompt surfaces.
    assert "패턴" in msg
    # And the draft is not ready_to_save.
    assert body["ready_to_save"] is False


# ── Q101 — conv compose covers all guided archetypes + condition kinds


# Q101.1 — Lifecycle vocab expansion (KO + EN per added event). The
# guided wizard surfaces 30 lifecycle events; the conversational
# extractor now recognises a representative slice (~12) so an operator
# can name the event in freeform text. One assertion per (phrase,
# lifecycle) tuple; KO and EN tested independently.

import pytest  # noqa: E402


_Q101_LIFECYCLE_PHRASE_CASES: list[tuple[str, str]] = [
    # PreCompact / PostCompact (content-flow family).
    ("압축 전에 확인해줘", "PreCompact"),
    ("Run a check before compact", "PreCompact"),
    ("압축 후에 기록", "PostCompact"),
    ("Log after compact runs", "PostCompact"),
    # UserPromptSubmit / UserPromptExpansion.
    ("사용자 프롬프트 제출 시점에", "UserPromptSubmit"),
    ("On user prompt submit", "UserPromptSubmit"),
    ("프롬프트 확장 단계에서", "UserPromptExpansion"),
    ("During user prompt expansion", "UserPromptExpansion"),
    # SessionStart / SessionEnd.
    ("세션 시작 시", "SessionStart"),
    ("at session start", "SessionStart"),
    ("세션 종료 시", "SessionEnd"),
    ("at session end", "SessionEnd"),
    # Notification.
    ("알림이 발생했을 때", "Notification"),
    ("on notification", "Notification"),
    # Permission gate.
    ("권한 요청 시점에", "PermissionRequest"),
    ("On a permission request", "PermissionRequest"),
    ("권한 거부 후", "PermissionDenied"),
    ("After a permission denied", "PermissionDenied"),
    # Subagent / stop boundary.
    ("서브에이전트 시작 시", "SubagentStart"),
    ("on subagent start", "SubagentStart"),
    ("서브에이전트 종료 후", "SubagentStop"),
    ("after subagent stop", "SubagentStop"),
    ("정지 실패 후", "StopFailure"),
    ("after stop failure", "StopFailure"),
    # Elicitation.
    ("사용자에게 질문할 때 검사", "Elicitation"),
    ("during elicitation", "Elicitation"),
    ("사용자 응답 후 처리", "ElicitationResult"),
    ("after elicitation result", "ElicitationResult"),
    # Lifecycle / observability.
    ("태스크 생성 시점에 기록", "TaskCreated"),
    ("on task created", "TaskCreated"),
    ("태스크 완료 후 기록", "TaskCompleted"),
    ("after task completed", "TaskCompleted"),
    ("팀메이트 유휴 상태일 때", "TeammateIdle"),
    ("when teammate idle", "TeammateIdle"),
    ("메모리 파일 로드 시", "InstructionsLoaded"),
    ("on instructions loaded", "InstructionsLoaded"),
    ("작업 디렉토리 변경 시", "CwdChanged"),
    ("when cwd changed", "CwdChanged"),
    ("파일 변경 시점에", "FileChanged"),
    ("on file changed", "FileChanged"),
    ("워크트리 생성 시", "WorktreeCreate"),
    ("on worktree create", "WorktreeCreate"),
    ("메시지 표시 직전", "MessageDisplay"),
    ("on message display", "MessageDisplay"),
    # Tool-context family expansion.
    ("도구 실행 실패 후", "PostToolUseFailure"),
    ("after tool fails", "PostToolUseFailure"),
    ("도구 배치 후", "PostToolBatch"),
    ("after a batch of tools", "PostToolBatch"),
]


@pytest.mark.parametrize("phrase,expected_event", _Q101_LIFECYCLE_PHRASE_CASES)
def test_q101_lifecycle_phrase_extracts_event(phrase, expected_event):
    """Each Q101-added lifecycle phrase (KO + EN) lands on
    `extracted.trigger.event`. The extractor is called directly so the
    matrix-gate / merge logic does not interfere.
    """
    from magi_cp.policy.nl_compiler_interactive import (
        _extract_intent_from_text,
    )
    out = _extract_intent_from_text(phrase)
    trig = out.get("trigger")
    assert isinstance(trig, dict), (phrase, out)
    assert trig.get("event") == expected_event, (phrase, out)


# Q101.2 — Action archetype extraction (block / ask / audit /
# inject_context / input_rewrite / run_command).

_Q101_ACTION_PHRASE_CASES: list[tuple[str, str]] = [
    # ask (newly expanded vocab).
    ("사람 확인 받고 진행", "ask"),
    ("Ask a human first", "ask"),
    ("사람에게 묻고", "ask"),
    ("Ask the human before running", "ask"),
    # inject_context.
    ("추가 컨텍스트를 모델에 주입해줘", "inject_context"),
    ("inject context after the tool", "inject_context"),
    ("Add additional context to the next turn", "inject_context"),
    ("컨텍스트 추가하면 좋겠어", "inject_context"),
    # input_rewrite.
    ("입력 재작성으로 처리", "input_rewrite"),
    ("Rewrite the prompt before submission", "input_rewrite"),
    ("프롬프트 재작성", "input_rewrite"),
    # run_command — extractor flags via _ACTION_KEYWORDS too.
    ("스크립트 실행으로 검증", "run_command"),
    ("Run the script to verify", "run_command"),
    ("execute the script", "run_command"),
]


@pytest.mark.parametrize("phrase,expected_action", _Q101_ACTION_PHRASE_CASES)
def test_q101_action_archetype_phrase_extracts_action(phrase, expected_action):
    """Each Q101-added action archetype phrase (KO + EN) lands on
    `extracted.action`. block / audit phrases keep their pre-Q101
    behaviour; this test pins the new archetypes."""
    from magi_cp.policy.nl_compiler_interactive import (
        _extract_intent_from_text,
    )
    out = _extract_intent_from_text(phrase)
    assert out.get("action") == expected_action, (phrase, out)


# Q101.3 — Condition kind extraction (regex / llm_critic / shacl /
# none). Each non-none kind seeds an empty-body requires row of the
# corresponding kind so the wizard's S1 body prompt fires next.

_Q101_KIND_PHRASE_CASES: list[tuple[str, str]] = [
    ("정규식으로 검사", "regex"),
    ("Use a regex match", "regex"),
    ("패턴 매칭으로", "regex"),
    ("Pattern matching against tool output", "regex"),
    ("AI 판단으로 결정", "llm_critic"),
    ("AI judge picks the verdict", "llm_critic"),
    ("LLM critic decides", "llm_critic"),
    ("SHACL로 구조 검증", "shacl"),
    ("Use a structured rule", "shacl"),
    ("구조화된 규칙으로", "shacl"),
]


@pytest.mark.parametrize("phrase,expected_kind", _Q101_KIND_PHRASE_CASES)
def test_q101_condition_kind_phrase_seeds_empty_requires_row(
    phrase, expected_kind,
):
    """A condition-kind phrase (KO + EN) seeds an empty-body requires
    row of that kind. The wizard's S1 body prompt fires next when the
    draft contains this seed.
    """
    from magi_cp.policy.nl_compiler_interactive import (
        _extract_intent_from_text,
    )
    out = _extract_intent_from_text(phrase)
    req = out.get("requires")
    assert isinstance(req, list) and len(req) == 1, (phrase, out)
    assert req[0].get("kind") == expected_kind, (phrase, out)
    if expected_kind == "regex":
        assert req[0].get("pattern") == "", (phrase, out)
    elif expected_kind == "llm_critic":
        assert req[0].get("criterion") == "", (phrase, out)
    elif expected_kind == "shacl":
        assert req[0].get("shape_ttl") == "", (phrase, out)


_Q101_KIND_NONE_PHRASES: list[str] = [
    "no check needed, just fire on the trigger",
    "no verification, only audit",
    "without check, just record",
    "without verification, just notify",
    "검사 없이 트리거만",
    "확인 없이 그냥 기록",
    "검증 없이 audit",
    "그냥 트리거만 잡고",
]


@pytest.mark.parametrize("phrase", _Q101_KIND_NONE_PHRASES)
def test_q101_condition_kind_none_phrase_emits_record_only_signal(phrase):
    """kind=none phrases select the record-only ("emit signal")
    archetype: the extractor emits the EXPLICIT none signal - an empty
    `requires: []` list - plus the `__condition_kind_none__` marker, and
    pins action=audit (a "no check" is coherent only with record). The
    empty list is what makes an audit-only draft authorable while an
    ABSENT requires still reads as half-built (see
    `_is_record_only_draft`).
    """
    from magi_cp.policy.nl_compiler_interactive import (
        _extract_intent_from_text,
    )
    out = _extract_intent_from_text(phrase)
    assert out.get("requires") == [], (phrase, out)
    assert out.get("action") == "audit", (phrase, out)
    assert out.get("__condition_kind_none__") is True, (phrase, out)


# Q101.4 — Combinatorial smoke: 12 (lifecycle, action, condition kind)
# tuples through step_compile end-to-end. Each tuple builds a freeform
# user text whose phrases land on every column. The draft returned by
# the wire endpoint is asserted to carry the right values.

_Q101_COMBOS: list[tuple[str, str, str | None, str, str]] = [
    # (phrase, expected_event, expected_matcher, expected_action, kind_or_none)
    (
        "도구 실행 전에 정규식으로 검사하고 막아줘 bash",
        "PreToolUse", "Bash", "block", "regex",
    ),
    (
        "도구 실행 후에 정규식으로 검사하고 기록 남겨줘",
        "PostToolUse", None, "audit", "regex",
    ),
    (
        "Before tool runs, use a regex match and block it",
        "PreToolUse", None, "block", "regex",
    ),
    (
        "최종 응답 전에 AI 판단으로 검사하고 기록",
        "Stop", None, "audit", "llm_critic",
    ),
    (
        "After file changed, use a structured rule and log the result",
        "FileChanged", None, "audit", "shacl",
    ),
    (
        "세션 시작 시 추가 컨텍스트를 모델에 주입해줘",
        "SessionStart", None, "inject_context", None,
    ),
    (
        "도구 실행 전에 입력 재작성으로 처리",
        "PreToolUse", None, "input_rewrite", None,
    ),
    (
        "도구 실행 실패 후 스크립트 실행으로 처리",
        "PostToolUseFailure", None, "run_command", None,
    ),
    (
        "권한 요청 시 사람 확인 받고 진행",
        "PermissionRequest", None, "ask", None,
    ),
    (
        "Before final answer, use a regex match and audit",
        "Stop", None, "audit", "regex",
    ),
    (
        "압축 전에 AI judge picks the verdict and log it",
        "PreCompact", None, "audit", "llm_critic",
    ),
    (
        "파일 변경 시 정규식 검사하고 기록 audit",
        "FileChanged", None, "audit", "regex",
    ),
]


@pytest.mark.parametrize(
    "phrase,expected_event,expected_matcher,expected_action,expected_kind",
    _Q101_COMBOS,
)
def test_q101_combinatorial_smoke_step_compile(
    phrase, expected_event, expected_matcher, expected_action, expected_kind,
):
    """End-to-end smoke: each (lifecycle, action, kind) tuple lands on
    the wire draft after step_compile runs. The LLM stub is told to do
    nothing so the deterministic extractor is the sole source of the
    draft.
    """
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user", "content": phrase}],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    draft = body["draft"] or {}
    trig = draft.get("trigger") or {}
    assert trig.get("event") == expected_event, (phrase, draft)
    if expected_matcher is not None:
        assert trig.get("matcher") == expected_matcher, (phrase, draft)
    # Action values that aren't in _ON_MISSING_VALUES (inject_context /
    # input_rewrite / run_command) are still surfaced on the draft by
    # the merge; the wizard treats them as "still missing" so the
    # canonical on_missing question fires later. We only assert the
    # draft carries the right marker here.
    assert draft.get("action") == expected_action, (phrase, draft)
    if expected_kind in ("regex", "llm_critic", "shacl"):
        req = draft.get("requires")
        assert isinstance(req, list) and len(req) == 1, (phrase, draft)
        assert req[0].get("kind") == expected_kind, (phrase, draft)


# Q101.5 — Inject-context guardrail. When the operator names a
# lifecycle in `_CONTEXT_INJECTION_EXCLUDED_EVENTS` AND asks for
# inject_context in the same turn, the extractor rewrites the action
# to audit and sets a marker the assistant_message builder turns into
# a plain-language explanation.

_Q101_INJECT_EXCLUDED_PHRASES: list[tuple[str, str]] = [
    # 8 events live in _CONTEXT_INJECTION_EXCLUDED_EVENTS today.
    ("사용자에게 질문할 때 컨텍스트 주입", "Elicitation"),
    ("사용자 응답 후 추가 컨텍스트", "ElicitationResult"),
    ("워크트리 생성 시 추가 컨텍스트", "WorktreeCreate"),
    ("메시지 표시 직전 컨텍스트 주입", "MessageDisplay"),
    ("최종 응답 전에 추가 컨텍스트", "Stop"),
    ("정지 실패 후 컨텍스트 주입", "StopFailure"),
    ("세션 종료 시 컨텍스트 주입", "SessionEnd"),
    ("서브에이전트 종료 후 추가 컨텍스트", "SubagentStop"),
]


@pytest.mark.parametrize(
    "phrase,excluded_event", _Q101_INJECT_EXCLUDED_PHRASES,
)
def test_q101_inject_context_guardrail_rewrites_action_to_audit(
    phrase, excluded_event,
):
    """When lifecycle is in _CONTEXT_INJECTION_EXCLUDED_EVENTS AND
    action=inject_context, the extractor rewrites the action to audit
    and flags a marker explaining the rewrite.
    """
    from magi_cp.policy.nl_compiler_interactive import (
        _extract_intent_from_text,
    )
    out = _extract_intent_from_text(phrase)
    # Action is rewritten to audit, NOT inject_context.
    assert out.get("action") == "audit", (phrase, out)
    # Marker names the excluded event so the assistant_message builder
    # can surface it to the operator.
    assert out.get("__inject_context_rewritten__") == excluded_event, (
        phrase, out,
    )


def test_q101_inject_context_guardrail_explains_in_assistant_message_ko():
    """End-to-end Korean: the rewrite is surfaced in
    assistant_message so the operator reads why the action was
    changed before the next question.
    """
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{
                "role": "user",
                "content": "사용자에게 질문할 때 컨텍스트 주입",
            }],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    draft = body["draft"] or {}
    assert draft.get("action") == "audit", draft
    msg = body["assistant_message"]
    # The plain-language explanation must reference the excluded event
    # name and the rewrite target ("audit").
    assert "Elicitation" in msg, msg
    assert "audit" in msg, msg


def test_q101_inject_context_guardrail_explains_in_assistant_message_en():
    """End-to-end English: same rewrite + explanation in the EN
    surface.
    """
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{
                "role": "user",
                "content": (
                    "At session end, inject context into the next turn"
                ),
            }],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    draft = body["draft"] or {}
    assert draft.get("action") == "audit", draft
    msg = body["assistant_message"]
    assert "SessionEnd" in msg, msg
    assert "audit" in msg, msg
    # The plain-language explanation MUST surface the rewrite reason in
    # English.
    assert "not available" in msg or "switched" in msg, msg


def test_q101_inject_context_legal_event_keeps_action_inject_context():
    """When lifecycle is NOT in the excluded set, action=inject_context
    is preserved (no rewrite, no marker)."""
    from magi_cp.policy.nl_compiler_interactive import (
        _extract_intent_from_text,
    )
    # PostToolUse is NOT in _CONTEXT_INJECTION_EXCLUDED_EVENTS so the
    # archetype is legal and the guardrail does NOT fire.
    out = _extract_intent_from_text(
        "도구 실행 후 추가 컨텍스트를 모델에 주입",
    )
    assert out.get("action") == "inject_context", out
    assert "__inject_context_rewritten__" not in out, out


def test_q101_inject_context_guardrail_fires_on_multi_turn_event():
    """Multi-turn case: lifecycle was set in a prior turn via answers;
    this turn the operator types "inject context" without naming a
    lifecycle. The post-merge guardrail rewrites the action because
    the effective event (from the prior draft) is excluded.

    Uses Stop as the prior event because it is BOTH in the wizard's
    `_EVENT_TO_LIFECYCLE` (survives sanitize on the client-supplied
    draft) AND in `_CONTEXT_INJECTION_EXCLUDED_EVENTS` (triggers the
    guardrail). The other excluded events (Elicitation /
    ElicitationResult / WorktreeCreate / MessageDisplay /
    StopFailure / SessionEnd / SubagentStop) are not in the wizard's
    sanitize allowlist so a multi-turn `draft_so_far` carrying them
    is dropped at the boundary — this is by design, the wizard only
    persists 3 high-level buckets across turns today.
    """
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    prior_draft = {
        "trigger": {
            "host": "claude-code",
            "event": "Stop",
            "matcher": "*",
        },
    }
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user", "content": "추가 컨텍스트 주입"}],
            "draft_so_far": prior_draft,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    draft = body["draft"] or {}
    assert draft.get("trigger", {}).get("event") == "Stop"
    assert draft.get("action") == "audit"
    msg = body["assistant_message"]
    assert "Stop" in msg, msg


# ── compound archetype: evidence_gate (audit + precondition) ───────────
# The conversational compiler authors a COMPOUND policy deterministically:
# "require a credible source before <tool> runs" carries type=evidence_gate
# through every turn and is expanded to member IR policies only at save
# (POST /policies/compound). These tests pass a FakeLlmProvider with NO
# canned responses, so any LLM call raises, proving the compound sub-flow
# never calls the model.


def _client_no_llm() -> TestClient:
    """A client whose compiler LLM raises if called. The compound
    sub-flow must be fully deterministic, so a compound turn must never
    reach `provider.complete`."""
    from magi_cp.llm.provider import FakeLlmProvider
    return _client(
        llm_compiler=FakeLlmProvider([]), llm_reviewer=FakeLlmProvider([]),
    )


def test_compound_full_intent_one_turn_ready_no_llm():
    """A single freeform turn naming the credible-source gate + the gated
    tool + a project scope compiles to a ready compound draft without any
    LLM call."""
    c = _client_no_llm()
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{
                "role": "user",
                "content": ("require a credible source before "
                            "mcp__trading__execute_trade runs, "
                            "only in ~/trading-mcp"),
            }],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["compound"] is True, body
    assert body["ready_to_save"] is True, body
    assert body["missing_fields"] == []
    d = body["draft"]
    assert d["type"] == "evidence_gate"
    assert d["gate"]["matcher"] == "mcp__trading__execute_trade"
    assert d["project_scope"] == "~/trading-mcp"
    assert d["id"] == "verified-execute-trade"
    # The wire draft carries the full archetype so it is a complete
    # POST /policies/compound body.
    assert d["kind"] == "source_credibility"
    assert d["audit"]["judge"] == "domain-credibility"
    assert d["gate"]["action"] == "block"


def test_compound_draft_expands_and_members_validate():
    """The compound draft the sub-flow emits expands to the audit +
    precondition + ledger-protection rules, and every member round-trips
    through the IR validator."""
    from magi_cp.policy.compound import expand_compound_draft
    from magi_cp.policy.ir import policy_from_dict
    c = _client_no_llm()
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{
                "role": "user",
                "content": ("block Bash unless a verified source was "
                            "checked first"),
            }],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    d = r.json()["draft"]
    members = expand_compound_draft(d)
    # audit + gate + 3 ledger-protection denies.
    assert len(members) == 5, [m["id"] for m in members]
    ids = {m["id"] for m in members}
    assert d["id"] + "-audit" in ids
    assert d["id"] + "-gate" in ids
    for m in members:
        policy_from_dict(m)  # raises on invalid


def test_compound_intent_without_tool_asks_for_gated_action():
    """When the freeform intent names the credible-source gate but NOT
    the gated tool, the sub-flow asks the single q_matcher question and
    stays not-ready."""
    c = _client_no_llm()
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{
                "role": "user",
                "content": "require a credible source before placing a trade",
            }],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["compound"] is True
    assert body["ready_to_save"] is False
    assert body["missing_fields"] == ["matcher"]
    qids = [q["id"] for q in body["questions"]]
    assert qids == ["q_matcher"]
    assert body["questions"][0]["targets_field"] == "matcher"


def test_compound_second_turn_answer_tool_becomes_ready():
    """Answering q_matcher on a compound draft fills gate.matcher, flips
    ready_to_save, and re-derives the id from the chosen tool."""
    c = _client_no_llm()
    prior = {
        "type": "evidence_gate",
        "kind": "source_credibility",
        "gate": {"matcher": ""},
    }
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [
                {"role": "user",
                 "content": "require a credible source before a trade"},
                {"role": "assistant", "content": "which tool?"},
            ],
            "draft_so_far": prior,
            "answers": {"q_matcher": "mcp__trading__execute_trade"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["compound"] is True
    assert body["ready_to_save"] is True
    d = body["draft"]
    assert d["gate"]["matcher"] == "mcp__trading__execute_trade"
    assert d["id"] == "verified-execute-trade"


def test_compound_illegal_tool_answer_reasks():
    """An illegal matcher answer is ignored so the wizard re-asks rather
    than persisting garbage on the compound draft."""
    c = _client_no_llm()
    prior = {"type": "evidence_gate", "gate": {"matcher": ""}}
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [
                {"role": "user",
                 "content": "require a credible source before a trade"},
                {"role": "assistant", "content": "which tool?"},
            ],
            "draft_so_far": prior,
            "answers": {"q_matcher": "not a legal matcher !!!"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ready_to_save"] is False
    assert body["missing_fields"] == ["matcher"]


def test_compound_client_echo_stays_compound_across_turns():
    """A client echoing a committed compound draft back keeps it a
    compound (the sanitizer preserves type=evidence_gate + nested
    audit/gate) rather than dropping into the single-policy flow."""
    c = _client_no_llm()
    prior = {
        "type": "evidence_gate",
        "kind": "source_credibility",
        "project_scope": "~/trading-mcp",
        "audit": {"event": "PostToolUse", "matcher": "WebFetch|Bash",
                  "extract": "url", "judge": "domain-credibility"},
        "gate": {"event": "PreToolUse",
                 "matcher": "mcp__trading__execute_trade",
                 "action": "block", "verdict": "pass", "reason": "no source"},
    }
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": prior, "answers": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["compound"] is True
    assert body["ready_to_save"] is True
    assert body["draft"]["type"] == "evidence_gate"


def test_compound_korean_intent():
    """A Korean credible-source-gate intent is classified as compound and
    surfaces Korean copy."""
    c = _client_no_llm()
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{
                "role": "user",
                "content": ("mcp__trading__execute_trade 실행 전에 "
                            "신뢰할 수 있는 출처를 먼저 확인하게 해줘"),
            }],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["compound"] is True
    assert body["ready_to_save"] is True
    assert body["draft"]["gate"]["matcher"] == "mcp__trading__execute_trade"


def test_run_command_intent_not_stolen_by_compound():
    """"run pytest before the final answer" is a run_command intent and
    must NOT be captured by the compound sub-flow (no credible-source
    cue). It reaches the LLM path as usual."""
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
            "history": [{"role": "user",
                         "content": "run pytest before the final answer"}],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["compound"] is False
    assert body["draft"]["type"] == "run_command"


def test_single_verifier_intent_not_stolen_by_compound():
    """"verify the citations before answering" is a single verifier
    (citation_verify), not a compound: it has a gate cue but no
    credible-SOURCE noun phrase, so it stays on the single-policy path."""
    canned = _llm_response(
        message="Got it, we'll verify citations.",
        updates={},
        questions=[],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user",
                         "content": "verify the citations before answering"}],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["compound"] is False
    assert body["draft"] is None or body["draft"].get("type") != "evidence_gate"


# ── compound context-aware reuse ───────────────────────────────────────
# When an existing enabled policy already records the same evidence kind,
# authoring a new evidence-gate reuses that producer (emit_audit=False)
# instead of duplicating the audit. The endpoint builds the context from
# the policy group store, so these tests save a first policy, then author
# a second.


def test_compound_reuses_existing_producer_across_policies():
    from magi_cp.policy.compound import expand_compound_draft
    c = _client_no_llm()
    # 1) author + save the first compound (creates the source_credibility
    #    producer).
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{
                "role": "user",
                "content": ("require a credible source before "
                            "mcp__trading__execute_trade"),
            }],
            "draft_so_far": None, "answers": None,
        },
    )
    d1 = r.json()["draft"]
    assert "emit_audit" not in d1  # first policy emits its own audit
    save1 = c.post("/policies/compound", headers=HEADERS,
                   json={"draft": d1, "source": "org", "enabled": True})
    assert save1.status_code == 200, save1.text

    # 2) author a SECOND compound for a different tool, same default kind.
    #    It must reuse the first policy's producer.
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{
                "role": "user",
                "content": ("require a credible source before "
                            "mcp__trading__cancel_order"),
            }],
            "draft_so_far": None, "answers": None,
        },
    )
    body = r.json()
    d2 = body["draft"]
    assert body["ready_to_save"] is True
    assert d2.get("emit_audit") is False, d2
    members = expand_compound_draft(d2)
    assert not any(m["id"].endswith("-audit") for m in members)
    assert members[0]["type"] == "evidence_precondition"


def test_compound_no_reuse_when_no_existing_producer():
    """With an empty store the first policy emits its own audit (no
    emit_audit flag, default True)."""
    c = _client_no_llm()
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{
                "role": "user",
                "content": "require a credible source before mcp__trading__execute_trade",
            }],
            "draft_so_far": None, "answers": None,
        },
    )
    assert "emit_audit" not in r.json()["draft"]


def test_compound_reuse_self_exclusion():
    """Re-authoring the SAME policy id must not make it reuse (and thereby
    drop) its OWN audit. _existing_audit_provider excludes self."""
    from magi_cp.policy.nl_compiler_interactive import _existing_audit_provider
    ctx = {"audit_kinds": {"source_credibility": ["verified-execute-trade"]}}
    # a different draft reuses it
    assert _existing_audit_provider(ctx, "source_credibility", "verified-cancel-order") \
        == "verified-execute-trade"
    # the same id does NOT reuse itself
    assert _existing_audit_provider(ctx, "source_credibility", "verified-execute-trade") \
        is None
    # unknown kind -> no provider
    assert _existing_audit_provider(ctx, "other_kind", "x") is None
    # no context -> no provider
    assert _existing_audit_provider(None, "source_credibility", "x") is None


def test_compound_context_build_skips_disabled_and_reusers():
    """The endpoint context excludes disabled policies and policies that
    themselves reuse (emit_audit=False), so a dead/absent producer is
    never offered for reuse."""
    from magi_cp.cloud.routes.compile import _build_compile_context

    class _FakeRec:
        def __init__(self, id, draft, enabled):
            self.id, self.draft, self.enabled = id, draft, enabled

    class _FakeStore:
        def load(self):
            return [
                _FakeRec("p-enabled",
                         {"type": "evidence_gate", "kind": "source_credibility"}, True),
                _FakeRec("p-disabled",
                         {"type": "evidence_gate", "kind": "source_credibility"}, False),
                _FakeRec("p-reuser",
                         {"type": "evidence_gate", "kind": "source_credibility",
                          "emit_audit": False}, True),
                _FakeRec("p-notcompound", {"type": "run_command"}, True),
            ]

    ctx = _build_compile_context(_FakeStore())
    assert ctx["audit_kinds"].get("source_credibility") == ["p-enabled"]


def test_compound_context_build_none_store():
    from magi_cp.cloud.routes.compile import _build_compile_context
    assert _build_compile_context(None) == {"audit_kinds": {}}


# ── A2/UX-01/UX-02: compound tool-answer lands from freeform chat ──────
# The compound "which tool?" question is kind=text; the dashboard has no
# text-answer channel, so the tool name arrives as freeform history with
# answers=null. The sub-flow must scan the latest user turn.


def test_compound_freeform_tool_answer_lands():
    """Intent without a tool asks q_matcher; a later freeform turn naming
    the tool (answers=null) must set the matcher, not re-ask forever."""
    c = _client_no_llm()
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [{"role": "user",
                     "content": "require a credible source before placing a trade"}],
        "draft_so_far": None, "answers": None,
    })
    b = r.json()
    assert b["ready_to_save"] is False
    assert b["missing_fields"] == ["matcher"]
    # freeform tool name, NO answers payload
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [
            {"role": "user", "content": "require a credible source before a trade"},
            {"role": "assistant", "content": "which tool?"},
            {"role": "user", "content": "mcp__trading__execute_trade"},
        ],
        "draft_so_far": b["draft"], "answers": None,
    })
    b2 = r.json()
    assert b2["ready_to_save"] is True, b2
    assert b2["draft"]["gate"]["matcher"] == "mcp__trading__execute_trade"


def test_compound_bare_tool_answer_lands():
    c = _client_no_llm()
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [{"role": "user",
                     "content": "require a credible source before the risky action"}],
        "draft_so_far": None, "answers": None,
    })
    b = r.json()
    assert b["missing_fields"] == ["matcher"]
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [{"role": "user", "content": "the Bash tool"}],
        "draft_so_far": b["draft"], "answers": None,
    })
    assert r.json()["draft"]["gate"]["matcher"] == "Bash"


def test_compound_correction_overwrites_with_change_cue():
    """A later turn with a change cue ("no, gate Bash instead") overwrites
    the gated tool (UX-02)."""
    c = _client_no_llm()
    prior = {"type": "evidence_gate", "kind": "source_credibility",
             "gate": {"matcher": "mcp__trading__execute_trade"}}
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [{"role": "user", "content": "no, gate Bash instead"}],
        "draft_so_far": prior, "answers": None,
    })
    assert r.json()["draft"]["gate"]["matcher"] == "Bash"


def test_compound_confirmation_does_not_clobber_matcher():
    """An incidental tool mention with NO change cue must not overwrite."""
    c = _client_no_llm()
    prior = {"type": "evidence_gate", "kind": "source_credibility",
             "gate": {"matcher": "mcp__trading__execute_trade"}}
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [{"role": "user", "content": "looks good, save it"}],
        "draft_so_far": prior, "answers": None,
    })
    assert r.json()["draft"]["gate"]["matcher"] == "mcp__trading__execute_trade"


def test_compound_reuse_dropped_when_producer_gone():
    """B1/IF-09: an echoed draft that pinned emit_audit=False on an earlier
    turn drops it when the producer no longer exists (empty store), so the
    policy restores its self-producing default instead of a dead gate."""
    c = _client_no_llm()
    prior = {
        "type": "evidence_gate", "kind": "source_credibility",
        "emit_audit": False,
        "gate": {"matcher": "mcp__trading__execute_trade"},
    }
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [], "draft_so_far": prior, "answers": None,
    })
    d = r.json()["draft"]
    # No producer in the (empty) store -> emit_audit restored to default.
    assert d.get("emit_audit") is not False, d
    from magi_cp.policy.compound import expand_compound_draft
    # self-produces again: audit member present.
    assert any(m["id"].endswith("-audit") for m in expand_compound_draft(d))


# ── H4/CV-07: conversational evidence-gate "ask" cue ───────────────────

def test_compound_ask_cue_sets_ask_action():
    """"ask for approval before X" must set gate.action=ask, not silently
    default to block (CV-07)."""
    c = _client_no_llm()
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [{"role": "user",
                     "content": "ask for approval of a credible source before mcp__trading__execute_trade runs"}],
        "draft_so_far": None, "answers": None,
    })
    d = r.json()["draft"]
    assert d["gate"]["matcher"] == "mcp__trading__execute_trade"
    assert d["gate"]["action"] == "ask"


def test_compound_ask_cue_korean():
    c = _client_no_llm()
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [{"role": "user",
                     "content": "mcp__trading__execute_trade 실행 전에 신뢰할 수 있는 출처를 승인 받게 해줘"}],
        "draft_so_far": None, "answers": None,
    })
    d = r.json()["draft"]
    assert d["gate"]["action"] == "ask"


def test_compound_no_ask_cue_defaults_block():
    c = _client_no_llm()
    r = c.post("/policies/compile-interactive", headers=HEADERS, json={
        "history": [{"role": "user",
                     "content": "require a credible source before mcp__trading__execute_trade"}],
        "draft_so_far": None, "answers": None,
    })
    assert r.json()["draft"]["gate"]["action"] == "block"


def test_rev_pr4_s4_audit_draft_discloses_record_only():
    """REV-PR-4 (GAP-C): a ready audit draft must disclose that it records
    but does not block, so record-only is never mistaken for enforcement."""
    from magi_cp.policy.nl_compiler_interactive import _build_assistant_message

    audit_draft = {
        "id": "cite-audit", "type": "evidence",
        "trigger": {"event": "Stop", "matcher": "*"},
        "requires": [{"kind": "step", "step": "citation_verify",
                      "verdict": "pass"}],
        "action": "audit",
    }
    en = _build_assistant_message("S4_ready", audit_draft, ko=False)
    assert "does not block anything" in en
    assert "Draft is ready" in en  # base ready line still present
    ko = _build_assistant_message("S4_ready", audit_draft, ko=True)
    assert "차단하지 않습니다" in ko
    assert "초안 준비됐어요" in ko


def test_rev_pr4_s4_block_draft_has_no_disclosure():
    from magi_cp.policy.nl_compiler_interactive import _build_assistant_message

    block_draft = {
        "id": "rrn-block", "type": "evidence",
        "trigger": {"event": "PreToolUse", "matcher": "Bash"},
        "requires": [{"kind": "step", "step": "privilege_scan",
                      "verdict": "pass"}],
        "action": "block",
    }
    en = _build_assistant_message("S4_ready", block_draft, ko=False)
    assert "does not block anything" not in en
    assert "Draft is ready" in en


def test_rev_pr4_s4_run_command_draft_has_no_disclosure():
    from magi_cp.policy.nl_compiler_interactive import _build_assistant_message

    rc_draft = {
        "id": "lint-after-edit", "type": "run_command",
        "trigger": {"event": "PostToolUse", "matcher": "Edit"},
        "command": "npm run lint", "runtime": "bash",
    }
    en = _build_assistant_message("S4_ready", rc_draft, ko=False)
    assert "does not block anything" not in en


# ── AF-1 (P0-1): kill false "cannot be expressed" on extractor-seeded
# illegal triples ─────────────────────────────────────────────────────

def test_af1_verify_confirm_is_not_ask():
    """'확인' meaning 'verify/check' must NOT extract the ask action (the
    canonical few-shot '인용한 출처가 진짜인지 확인' produced Stop/*/ask)."""
    from magi_cp.policy.nl_compiler_interactive import _extract_intent_from_text
    out = _extract_intent_from_text("인용한 출처가 진짜인지 확인하고 안 맞으면 경고만 띄워줘")
    assert out.get("action") != "ask", out


def test_af1_ask_still_extracts_with_object():
    """A real ask-for-human intent still extracts ask."""
    from magi_cp.policy.nl_compiler_interactive import _extract_intent_from_text
    assert _extract_intent_from_text("사람 확인 받고 진행해줘").get("action") == "ask"
    assert _extract_intent_from_text("ask a human before running").get("action") == "ask"
    assert _extract_intent_from_text("승인 받고 실행해").get("action") == "ask"


def test_af1_final_event_resets_default_tool_matcher():
    """A verifier default tool matcher is widened to '*' when the operator
    overrode the event to a non-tool-context event (Stop)."""
    from magi_cp.policy.nl_compiler_interactive import _extract_intent_from_text
    out = _extract_intent_from_text("log PII in the final answer")
    trig = out.get("trigger") or {}
    assert trig.get("event") == "Stop", out
    assert trig.get("matcher") == "*", out


def test_af1_explicit_tool_at_final_event_not_reset():
    """When the operator NAMES a tool, keep it (the illegal combo surfaces
    honestly rather than being silently widened)."""
    from magi_cp.policy.nl_compiler_interactive import _extract_intent_from_text
    out = _extract_intent_from_text("block Bash at the final answer")
    trig = out.get("trigger") or {}
    assert trig.get("event") == "Stop", out
    assert trig.get("matcher") == "Bash", out


def test_af1_tool_matcher_preserved_on_tool_event():
    from magi_cp.policy.nl_compiler_interactive import _extract_intent_from_text
    out = _extract_intent_from_text("block any shell command that contains an RRN")
    trig = out.get("trigger") or {}
    assert trig.get("event") == "PreToolUse", out
    assert trig.get("matcher") == "Bash", out


def test_af1_no_stale_finding_when_llm_repairs_to_legal():
    """When the extractor seeds an illegal triple but the LLM repairs it to a
    legal audit draft, the wire must carry NO feasibility finding (the stale
    _f1 must not survive)."""
    import json as _json
    import os as _os
    import tempfile as _tf

    from fastapi.testclient import TestClient
    from magi_cp.cloud.app import create_app
    from magi_cp.llm.provider import FakeLlmProvider
    _d = _tf.mkdtemp()
    _p = _os.path.join(_d, "p.json")
    with open(_p, "w") as _fh:
        _fh.write("[]")
    _os.environ["MAGI_CP_ADMIN_API_KEY"] = "k"
    # LLM emits the few-shot's own audit repair.
    canned = _json.dumps({"assistant_message": "", "draft_updates": {"action": "audit"}, "questions": []})
    app = create_app(dsn="sqlite:///:memory:", policy_store_path=_p,
                     llm_compiler=FakeLlmProvider([canned]))
    c = TestClient(app)
    r = c.post("/policies/compile-interactive", headers={"X-Admin-Api-Key": "k"}, json={
        "history": [{"role": "user",
                     "content": "최종 답변에서 인용한 출처가 진짜인지 확인하고 안 맞으면 경고만 띄워줘"}],
        "draft_so_far": None, "answers": None,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["feasibility"] is None, body["feasibility"]
    # And it must not carry the false "cannot be expressed" copy.
    assert "표현할 수 없" not in (body["assistant_message"] or "")
    assert "cannot be expressed" not in (body["assistant_message"] or "")


# ── AF-2 (P1-4): negated block must not extract action=block ──────────

def test_af2_negated_block_en_extracts_audit_not_block():
    from magi_cp.policy.nl_compiler_interactive import _extract_intent_from_text
    out = _extract_intent_from_text("don't block it, just record web fetches")
    assert out.get("action") != "block", out


def test_af2_negated_block_ko_extracts_audit_not_block():
    from magi_cp.policy.nl_compiler_interactive import _extract_intent_from_text
    out = _extract_intent_from_text("기록만 하고 차단은 하지 마")
    assert out.get("action") != "block", out


def test_af2_plain_block_still_extracts_block():
    from magi_cp.policy.nl_compiler_interactive import _extract_intent_from_text
    assert _extract_intent_from_text("block rm -rf").get("action") == "block"


# ── AF-6 (P1-3): extractor block family aligned with enforce verbs ────

def test_af6_prevent_forbid_reject_extract_block():
    from magi_cp.policy.nl_compiler_interactive import _extract_intent_from_text
    assert _extract_intent_from_text(
        "prevent shell commands that contain an RRN").get("action") == "block"
    assert _extract_intent_from_text(
        "forbid fetching from evil.com").get("action") == "block"
    assert _extract_intent_from_text(
        "reject the tool call if it touches prod").get("action") == "block"


def test_af6_korean_enforce_verbs_extract_block():
    from magi_cp.policy.nl_compiler_interactive import _extract_intent_from_text
    assert _extract_intent_from_text("그 명령을 금지해줘").get("action") == "block"
    assert _extract_intent_from_text("못하게 해줘").get("action") == "block"


def test_af6_require_approval_stays_ask_not_block():
    # "require approval" is an ASK intent - must not be captured by the
    # widened block family.
    from magi_cp.policy.nl_compiler_interactive import _extract_intent_from_text
    assert _extract_intent_from_text(
        "require approval before running bash").get("action") == "ask"


def test_af6_negated_prevent_not_extracted():
    from magi_cp.policy.nl_compiler_interactive import _extract_intent_from_text
    out = _extract_intent_from_text("don't prevent it, just record")
    assert out.get("action") != "block", out
# ── Cluster A: wider lifecycle round-trips through step_compile ────────


def test_wide_evidence_event_is_ready_to_save_end_to_end():
    """A complete SessionStart evidence draft passed as draft_so_far must
    survive the sanitize-on-entry pass with its event intact, report no
    missing lifecycle, ask no q_lifecycle, and flip ready_to_save=True.

    Before Cluster A the entry sanitizer deleted the wider event and the
    missing-fields gate re-listed 'lifecycle', so Save stayed disabled and
    the lifecycle dropdown re-appeared even though the IR validator already
    accepted SessionStart."""
    draft = {
        "id": "sess-scan",
        "version": "0.1",
        "trigger": {
            "host": "claude-code", "event": "SessionStart", "matcher": "*",
        },
        "requires": [
            {"kind": "llm_critic", "criterion": "the session context is safe"},
        ],
        "action": "audit",
    }
    canned = _llm_response(message="Draft ready.", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))

    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": draft, "answers": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["draft"]["trigger"]["event"] == "SessionStart", body["draft"]
    assert "lifecycle" not in body["missing_fields"], body["missing_fields"]
    assert body["ready_to_save"] is True, body["assistant_message"]
    assert body["needs_more"] is False
    qids = [q["id"] for q in body["questions"]]
    assert "q_lifecycle" not in qids, qids


def test_wide_run_command_event_is_ready_to_save_end_to_end():
    """A complete PermissionRequest run_command draft round-trips: no
    missing lifecycle, no q_lifecycle, ready_to_save=True."""
    draft = {
        "type": "run_command",
        "id": "perm-audit",
        "version": "0.1",
        "trigger": {
            "host": "claude-code", "event": "PermissionRequest", "matcher": "*",
        },
        "command": "echo hi",
        "runtime": "bash",
    }
    canned = _llm_response(message="Draft ready.", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))

    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": draft, "answers": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["draft"]["trigger"]["event"] == "PermissionRequest", body["draft"]
    assert "lifecycle" not in body["missing_fields"], body["missing_fields"]
    assert body["ready_to_save"] is True, body["assistant_message"]
    qids = [q["id"] for q in body["questions"]]
    assert "q_lifecycle" not in qids, qids


# ── R1-01 question-slice fix (Cluster B PR-2) ─────────────────────────


def test_r1_01_llm_question_outside_slice_is_not_emitted():
    """R1-01: an LLM-proposed question whose targets_field is outside the
    canonical priority slice (missing[:MAX_QUESTIONS_PER_TURN]) must NOT
    be emitted. Otherwise the user answers the shown question, but the
    next-turn validator (which reconstructs the legal id set from the
    same slice) would 422 with 'answer id was not in the previous
    turn's questions'.

    Setup: no draft yet, so lifecycle+matcher are the first two missing
    fields. LLM proposes q_requires (index 2, outside the [:2] slice).
    Expected: server ignores the out-of-slice proposal and falls back to
    the canonical (q_lifecycle, q_matcher) pair.
    """
    canned = _llm_response(
        message="ok",
        updates={},
        questions=[
            {
                "id": "q_requires",
                "prompt": "what verifier?",
                "kind": "single_select",
                "targets_field": "requires",
                "options": [],
            },
        ],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": None, "answers": None},
    )
    assert r.status_code == 200, r.text
    qids = [q["id"] for q in r.json()["questions"]]
    assert "q_requires" not in qids, (
        "out-of-slice question must not be emitted", qids
    )
    assert "q_lifecycle" in qids, qids


def test_r1_01_follow_up_answer_does_not_422():
    """R1-01: after the slice-constrained turn, answering one of the
    emitted questions on the next turn must succeed (no 422). This is the
    downstream consequence: if an out-of-slice question had been emitted,
    the user's answer would 422 because the validator only recognises
    the slice.
    """
    # Turn 1: LLM proposes out-of-slice q_requires; server falls back to
    # canonical (q_lifecycle, q_matcher).
    canned1 = _llm_response(
        message="ok",
        updates={},
        questions=[
            {
                "id": "q_requires",
                "prompt": "verifier?",
                "kind": "single_select",
                "targets_field": "requires",
                "options": [],
            },
        ],
    )
    # Turn 2: straightforward LLM stub, no questions proposed.
    canned2 = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned1, canned2]))

    r1 = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": None, "answers": None},
    )
    assert r1.status_code == 200, r1.text
    draft1 = r1.json()["draft"]

    # Turn 2: answer q_lifecycle (one of the actually emitted questions).
    # Must succeed because q_lifecycle was in the prior turn's slice.
    r2 = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [],
            "draft_so_far": draft1,
            "answers": {"q_lifecycle": "before_tool_use"},
        },
    )
    assert r2.status_code == 200, (
        "answering a slice-legal question must not 422", r2.text
    )
    assert r2.json()["draft"]["trigger"]["event"] == "PreToolUse"


# ── R2-03 freeform regex validation (Cluster B PR-2) ─────────────────


def test_r2_03_invalid_freeform_regex_is_rejected():
    """R2-03: when the freeform body fallback fires (prior assistant turn
    is a body question, latest user turn is the freeform answer) and the
    answer is an invalid regex, the bad pattern must NOT be written to the
    draft. The body question must re-fire (requires_body stays missing)
    and ready_to_save must stay False.
    """
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))

    draft = {
        "trigger": {
            "host": "claude-code", "event": "PreToolUse", "matcher": "Bash",
        },
        "requires": [{"kind": "regex", "pattern": ""}],
        "action": "block",
    }
    # The freeform anchor "what pattern" must appear in the prior
    # assistant turn so _looks_like_body_answer returns True.
    history = [
        {"role": "assistant",
         "content": "Let me know what pattern to detect."},
        {"role": "user",
         "content": "(unclosed"},  # invalid regex
    ]
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": history, "draft_so_far": draft, "answers": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["draft"]["requires"][0]["pattern"] == "", (
        "invalid regex must not land on the draft", body["draft"]
    )
    assert body["ready_to_save"] is False
    assert "requires_body" in body["missing_fields"]


def test_r2_03_invalid_freeform_regex_surfaces_error_line():
    """R2-03: the assistant_message must contain the plain-language
    does-not-compile notice (EN and KO variants both checked).
    """
    canned = _llm_response(message="ok", updates={}, questions=[])

    draft = {
        "trigger": {
            "host": "claude-code", "event": "PreToolUse", "matcher": "Bash",
        },
        "requires": [{"kind": "regex", "pattern": ""}],
        "action": "block",
    }
    # EN path: anchor "what pattern" triggers the freeform fallback.
    history_en = [
        {"role": "assistant", "content": "Tell me what pattern to match."},
        {"role": "user", "content": "(unclosed"},
    ]
    c_en = _client(llm_compiler=FakeLlmProvider([canned]))
    r_en = c_en.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": history_en, "draft_so_far": draft, "answers": None},
    )
    msg_en = r_en.json()["assistant_message"]
    # Must mention compile error; must NOT say "regex".
    assert "compile" in msg_en.lower(), msg_en
    assert "regex" not in msg_en.lower(), (
        "error line must not leak internal term 'regex'", msg_en
    )

    # KO path: anchor "어떤 패턴을" triggers the freeform fallback.
    history_ko = [
        {"role": "assistant", "content": "어떤 패턴을 찾아야 하나요?"},
        {"role": "user", "content": "(unclosed"},
    ]
    c_ko = _client(llm_compiler=FakeLlmProvider([canned]))
    r_ko = c_ko.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": history_ko, "draft_so_far": draft, "answers": None},
    )
    msg_ko = r_ko.json()["assistant_message"]
    assert "패턴" in msg_ko, msg_ko
    assert "regex" not in msg_ko.lower(), (
        "Korean error line must not leak internal term 'regex'", msg_ko
    )


def test_r2_03_valid_freeform_regex_lands_normally():
    """R2-03 regression: a VALID freeform regex body still lands on the
    draft without an error message.
    """
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))

    draft = {
        "trigger": {
            "host": "claude-code", "event": "PreToolUse", "matcher": "Bash",
        },
        "requires": [{"kind": "regex", "pattern": ""}],
        "action": "block",
    }
    history = [
        {"role": "assistant", "content": "Tell me what pattern to detect."},
        {"role": "user", "content": r"\brm -rf\b"},  # valid regex
    ]
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": history, "draft_so_far": draft, "answers": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["draft"]["requires"][0]["pattern"] == r"\brm -rf\b", (
        "valid regex must land on the draft", body["draft"]
    )
    # No compile-error noise in the message.
    assert "compile" not in body["assistant_message"].lower(), (
        "no error expected for valid regex", body["assistant_message"]
    )


# ── R2-04 matcher case normalization (Cluster B PR-2) ────────────────


def test_r2_04_lowercase_matcher_is_case_normalized():
    """R2-04: the q_matcher answer writer must normalise lowercase tool
    names to canonical PascalCase so 'bash' and 'webfetch' reach the
    draft as 'Bash' and 'WebFetch'.
    """
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned] * 4))

    for raw, expected in [
        ("bash", "Bash"),
        ("webfetch", "WebFetch"),
        ("Bash", "Bash"),       # already canonical - no change
        ("WebFetch", "WebFetch"),
    ]:
        r = c.post(
            "/policies/compile-interactive",
            headers=HEADERS,
            json={
                "history": [],
                "draft_so_far": None,
                "answers": {"q_matcher": raw},
            },
        )
        assert r.status_code == 200, f"{raw}: {r.text}"
        got = r.json()["draft"]["trigger"]["matcher"]
        assert got == expected, f"raw={raw!r}: expected {expected!r}, got {got!r}"


def test_r2_04_space_stripped_tool_name_is_normalized():
    """R2-04: 'web fetch' (with space) maps to 'WebFetch'."""
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))

    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [],
            "draft_so_far": None,
            "answers": {"q_matcher": "web fetch"},
        },
    )
    assert r.status_code == 200, r.text
    got = r.json()["draft"]["trigger"]["matcher"]
    assert got == "WebFetch", got


def test_r2_04_unknown_matcher_is_left_as_is_and_rejected():
    """R2-04: an unknown matcher (not in _BUILTIN_TOOLS, not MCP) is left
    as-is and rejected by the downstream legality check (the draft's
    trigger.matcher stays empty). No PascalCase guessing for unknowns.
    """
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))

    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [],
            "draft_so_far": None,
            "answers": {"q_matcher": "banana"},
        },
    )
    assert r.status_code == 200, r.text
    trig = (r.json()["draft"] or {}).get("trigger") or {}
    # The unknown value must NOT land as the matcher.
    assert trig.get("matcher") != "banana", trig


# ── R1-02 non-destructive LLM requires merge (Cluster C) ─────────────


def test_r1_02_llm_empty_body_does_not_wipe_filled_regex():
    """Core regression: R1-02.

    A draft has a FILLED verifier body (pattern="SSN-\\d+"). On the
    next turn the LLM emits draft_updates={"requires":[{"kind":"regex",
    "pattern":""}]} (empty body re-seed). The merge MUST preserve the
    filled body; the empty-body item is dropped in favour of the
    existing filled item. ready_to_save must remain True.

    Before the fix this test FAILS because the wholesale replace wipes
    the pattern and ready_to_save silently flips back to False.
    """
    # Draft is already complete with a filled regex body.
    filled_draft = {
        "id": "ssn-block",
        "trigger": {
            "host": "claude-code",
            "event": "PreToolUse",
            "matcher": "Bash",
        },
        "requires": [{"kind": "regex", "pattern": r"SSN-\d+"}],
        "action": "block",
    }
    # LLM re-emits an empty-body requires item (regression scenario).
    canned = _llm_response(
        message="ok",
        updates={"requires": [{"kind": "regex", "pattern": ""}]},
        questions=[],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": filled_draft, "answers": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    draft = body["draft"]
    # The filled pattern must be preserved.
    assert draft["requires"][0]["pattern"] == r"SSN-\d+", draft
    # The draft must still be ready to save (was not regressed).
    assert body["ready_to_save"] is True, body


def test_r1_02_genuine_correction_lands_new_nonempty_pattern():
    """R1-02: a non-empty incoming pattern is a genuine LLM correction
    and must land, replacing the previous pattern.
    """
    filled_draft = {
        "id": "old-pattern",
        "trigger": {
            "host": "claude-code",
            "event": "PreToolUse",
            "matcher": "Bash",
        },
        "requires": [{"kind": "regex", "pattern": r"OLD-\d+"}],
        "action": "block",
    }
    canned = _llm_response(
        message="ok",
        updates={"requires": [{"kind": "regex", "pattern": r"NEW-\d+"}]},
        questions=[],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": filled_draft, "answers": None},
    )
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    # Non-empty correction must land.
    assert draft["requires"][0]["pattern"] == r"NEW-\d+", draft


def test_r1_02_empty_existing_slot_accepts_llm_empty_seed():
    """R1-02: when the existing requires slot is itself empty (wizard
    seed state), an incoming empty-body LLM item is accepted as today --
    the seeded state is preserved, not blocked.
    """
    # Draft has a seeded (empty) regex item -- the wizard's state.
    seeded_draft = {
        "trigger": {
            "host": "claude-code",
            "event": "PreToolUse",
            "matcher": "Bash",
        },
        "requires": [{"kind": "regex", "pattern": ""}],
    }
    canned = _llm_response(
        message="ok",
        updates={"requires": [{"kind": "regex", "pattern": ""}]},
        questions=[],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": seeded_draft, "answers": None},
    )
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    # Seeded state persists (kind landed, body still empty).
    assert draft["requires"][0]["kind"] == "regex", draft
    assert draft["requires"][0]["pattern"] == "", draft


def test_r1_02_sibling_trigger_matcher_cannot_be_blanked():
    """Sibling-branch audit: trigger.matcher is already protected by the
    `m.strip() and ...` non-empty guard in the trigger branch. An LLM
    that sends matcher="" cannot blank a previously set matcher. This
    test documents that no additional guard is needed in the trigger
    branch (it only fills-when-valid, not fills-always).
    """
    # Draft already has matcher="Bash".
    prior_draft = {
        "trigger": {
            "host": "claude-code",
            "event": "PreToolUse",
            "matcher": "Bash",
        },
    }
    # LLM tries to blank the matcher.
    canned = _llm_response(
        message="ok",
        updates={"trigger": {"event": "PreToolUse", "matcher": ""}},
        questions=[],
    )
    c = _client(llm_compiler=FakeLlmProvider([canned]))
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": prior_draft, "answers": None},
    )
    assert r.status_code == 200, r.text
    trig = r.json()["draft"]["trigger"]
    # The existing matcher is preserved -- the trigger branch already
    # rejects empty matcher values via m.strip() check.
    assert trig["matcher"] == "Bash", trig


# ── Audit-only (record-only, "emit signal") authorable in the wizard ──
# QA-harness finding: an audit-only policy with no verifier
# (`requires: []`, action=audit) is a valid saveable IR but was
# UNAUTHORABLE through the wizard - `_missing_fields_for_draft` always
# demanded a non-empty requires so `ready_to_save` never flipped, and
# q_requires offered no "no check" option. These tests lock the fix.


def test_q_requires_offers_none_option():
    """q_requires now offers a 5th "just record, no check" option so the
    operator can SELECT the record-only archetype (previously only the 4
    verifier kinds were selectable)."""
    from magi_cp.policy.nl_compiler_interactive import _question_for_field

    for ko in (True, False):
        q = _question_for_field("requires", ko)
        assert q.id == "q_requires"
        values = [o.value for o in (q.options or [])]
        assert values == ["regex", "llm_critic", "shacl", "step", "none"], (
            ko, values
        )


def test_audit_only_via_none_answer_reaches_ready_to_save():
    """Drive the wizard to a record-only policy by answering
    q_requires="none". ready_to_save flips True with requires == [] and
    action == "audit", and the saved IR round-trips."""
    canned_each = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned_each] * 4))

    # Turn 1: lifecycle.
    d = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": None,
              "answers": {"q_lifecycle": "before_tool_use"}},
    ).json()["draft"]
    assert d["trigger"]["event"] == "PreToolUse"

    # Turn 2: matcher.
    d = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": d,
              "answers": {"q_matcher": "Bash"}},
    ).json()["draft"]
    assert d["trigger"]["matcher"] == "Bash"

    # Turn 3: requires="none" - the record-only choice. This must set an
    # EXPLICIT empty requires list AND coerce action=audit AND NOT ask a
    # q_requires_body follow-up.
    body = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": d,
              "answers": {"q_requires": "none"}},
    ).json()
    d = body["draft"]
    assert d["requires"] == [], d
    assert d["action"] == "audit", d
    assert "requires_body" not in body["missing_fields"], body
    assert not any(q["id"] == "q_requires_body" for q in body["questions"]), \
        body

    # id is auto-synthesised once behavioral fields are filled, so
    # ready_to_save flips on this same turn.
    assert body["ready_to_save"] is True, body
    assert body["missing_fields"] == [], body

    # The saved IR round-trips cleanly through the loader.
    from magi_cp.policy.ir import policy_from_dict
    p = policy_from_dict(d)
    assert p.action == "audit"
    assert p.requires == []


def test_audit_only_via_freeform_phrase_reaches_ready_to_save():
    """A freeform "record only, no check" phrasing is recognised by the
    deterministic extractor and drives the wizard to the same record-only
    draft (requires == [], action == "audit", ready_to_save True)."""
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned] * 2))

    # "before a tool runs on Bash, just record it - no check needed."
    body = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{
                "role": "user",
                "content": (
                    "도구 실행 전에 bash 작업을 검사 없이 그냥 기록만 해줘"
                ),
            }],
            "draft_so_far": None,
            "answers": None,
        },
    ).json()
    d = body["draft"] or {}
    assert d.get("requires") == [], d
    assert d.get("action") == "audit", d
    assert d["trigger"]["event"] == "PreToolUse", d
    assert d["trigger"]["matcher"] == "Bash", d
    assert body["ready_to_save"] is True, body

    from magi_cp.policy.ir import policy_from_dict
    p = policy_from_dict(d)
    assert p.action == "audit" and p.requires == []


def test_absent_requires_without_none_signal_still_missing():
    """KEY SAFETY TEST. A genuinely half-built draft - requires ABSENT
    with NO explicit none signal - must still report `requires` missing
    and NOT save. Only a deliberate none choice writes the empty list, so
    an unintended empty-requires draft can never slip through."""
    from magi_cp.policy.nl_compiler_interactive import (
        _missing_fields_for_draft,
        _is_record_only_draft,
    )

    half_built = {
        "id": "half-built",
        "trigger": {"host": "claude-code", "event": "PreToolUse",
                    "matcher": "Bash"},
        "action": "audit",
        # requires key deliberately ABSENT.
    }
    assert not _is_record_only_draft(half_built)
    assert "requires" in _missing_fields_for_draft(half_built)

    # Explicit empty list + audit IS the record-only signal.
    record_only = dict(half_built, requires=[])
    assert _is_record_only_draft(record_only)
    assert "requires" not in _missing_fields_for_draft(record_only)


def test_none_plus_block_cannot_reach_ready_to_save():
    """A "none + block" combination is a contradiction (block on what?).
    The answer path coerces action=audit when requires="none", so the
    empty list can never co-exist with block; and even a hand-forged
    `requires: [] + action: block` draft is NOT treated as record-only
    (the gate requires action==audit), so `requires` reports missing and
    the draft cannot save."""
    from magi_cp.policy.nl_compiler_interactive import (
        _apply_answer_to_draft,
        _is_record_only_draft,
        _missing_fields_for_draft,
    )

    # Answer path: even if a block was already on the draft, choosing
    # none coerces it to audit.
    draft = {
        "trigger": {"host": "claude-code", "event": "PreToolUse",
                    "matcher": "Bash"},
        "action": "block",
    }
    _apply_answer_to_draft(draft, "requires", "none")
    assert draft["requires"] == []
    assert draft["action"] == "audit", draft

    # Hand-forged contradiction: empty requires + block is NOT record-only.
    forged = {
        "id": "forged",
        "trigger": {"host": "claude-code", "event": "PreToolUse",
                    "matcher": "Bash"},
        "requires": [],
        "action": "block",
    }
    assert not _is_record_only_draft(forged)
    assert "requires" in _missing_fields_for_draft(forged)


def test_none_state_survives_a_turn_round_trip():
    """The record-only state (explicit empty requires + audit) is echoed
    back to the client as `draft_so_far` and must survive the sanitizer +
    merge on the next turn without a spurious `requires` row reappearing
    or the empty list being stripped."""
    canned_each = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned_each] * 4))

    d = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": None,
              "answers": {"q_lifecycle": "before_tool_use"}},
    ).json()["draft"]
    d = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": d,
              "answers": {"q_matcher": "Bash"}},
    ).json()["draft"]
    d = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": d,
              "answers": {"q_requires": "none"}},
    ).json()["draft"]
    assert d["requires"] == [] and d["action"] == "audit"

    # Echo the record-only draft back with an empty follow-up turn: the
    # none state must persist (no verifier row re-introduced, list kept).
    body = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={"history": [], "draft_so_far": d, "answers": None},
    ).json()
    d2 = body["draft"]
    assert d2["requires"] == [], d2
    assert d2["action"] == "audit", d2
    assert body["ready_to_save"] is True, body


def test_verifier_kind_paths_still_work_regression():
    """Regression: the 4 verifier-kind paths still seed an empty-body
    requires row and report requires_body missing (they are NOT swept
    into the record-only branch)."""
    from magi_cp.policy.nl_compiler_interactive import (
        _apply_answer_to_draft,
        _missing_fields_for_draft,
        _is_record_only_draft,
    )

    for kind, body_key in (
        ("regex", "pattern"),
        ("llm_critic", "criterion"),
        ("shacl", "shape_ttl"),
        ("step", "step"),
    ):
        draft = {
            "trigger": {"host": "claude-code", "event": "PreToolUse",
                        "matcher": "Bash"},
            "action": "block",
        }
        _apply_answer_to_draft(draft, "requires", kind)
        assert isinstance(draft["requires"], list) and len(draft["requires"]) == 1
        assert not _is_record_only_draft(draft), kind
        # Empty body -> requires_body still reported missing.
        assert "requires_body" in _missing_fields_for_draft(draft), kind
