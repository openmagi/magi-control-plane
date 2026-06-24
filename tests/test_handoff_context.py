"""D57g — POST /policies/handoff-context + the underlying serializer.

The route is offline (no LLM call) and admin-key gated. The serializer
re-uses the same per-field allowlists that gate /policies/compile-
interactive so any value that survives the merge is already canonical.

Coverage:
  - empty handoff → canonical first questions + neutral intro line.
  - guided wizard mid-flow → assistant summary lists what's filled,
    canonical questions cover what's missing.
  - raw IR draft → IR fields survive sanitisation (gate_binary smuggled
    via the body does NOT land on the merged draft).
  - both inputs together → wizard state wins on conflict (most recent
    author intent).
  - archetype collapse (action=inject_context / input_rewrite) is
    called out in the assistant message.
  - oversize body → 422; missing admin key → 403.
"""
from __future__ import annotations

import tempfile

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.policy.handoff_context import (
    HandoffContextError, build_handoff_turn,
)


HEADERS = {"X-Admin-Api-Key": "test-admin-key"}


@pytest.fixture(autouse=True)
def _admin_key(monkeypatch):
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", "test-admin-key")


def _tmp_store_path() -> str:
    f = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    f.write("[]")
    f.close()
    return f.name


def _client() -> TestClient:
    app = create_app(
        dsn="sqlite:///:memory:",
        policy_store_path=_tmp_store_path(),
    )
    return TestClient(app)


# ── serializer-direct tests ───────────────────────────────────────────


def test_empty_handoff_returns_neutral_intro_and_first_questions():
    out = build_handoff_turn(wizard_state=None, draft_ir=None)
    assert "missing_fields" in out
    # Every canonical field is missing on an empty handoff.
    assert "lifecycle" in out["missing_fields"]
    assert "matcher" in out["missing_fields"]
    # Canonical first questions: lifecycle + matcher.
    qids = [q["id"] for q in out["questions"]]
    assert qids == ["q_lifecycle", "q_matcher"]
    # No draft, no ready_to_save.
    assert out["draft"] is None
    assert out["ready_to_save"] is False
    assert out["needs_more"] is True


def test_guided_wizard_midflow_summarizes_filled_and_asks_missing():
    """Lifecycle + tool + condition picked but no id yet. The summary
    line should call out lifecycle / matcher / check / action, and
    the remaining question set should cover (requires_body, on_missing)
    or (on_missing, id) depending on what's filled."""
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "conditionKind": "regex",
        "pattern": "(^|\\s)sudo\\s",
        "action": "block",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    msg = out["assistant_message"]
    # English by default (no Hangul). The labels mirror page.tsx.
    assert "before a tool runs" in msg
    assert "Bash" in msg
    assert "block" in msg
    # `id` should be the only remaining missing field.
    assert out["missing_fields"] == ["id"]
    qids = [q["id"] for q in out["questions"]]
    assert qids == ["q_id"]
    # The draft is shaped enough to surface to the client.
    assert out["draft"] is not None
    assert out["draft"]["trigger"]["event"] == "PreToolUse"
    assert out["draft"]["trigger"]["matcher"] == "Bash"
    assert out["draft"]["requires"][0]["kind"] == "regex"
    assert out["draft"]["requires"][0]["pattern"] == "(^|\\s)sudo\\s"
    assert out["draft"]["action"] == "block"


def test_korean_state_surfaces_korean_summary():
    """A description with Hangul flips the assistant prose to Korean."""
    state = {
        "lifecycle": "after_tool_use",
        "toolScope": "WebFetch",
        "description": "외부 fetch 결과 감사",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    msg = out["assistant_message"]
    # Korean lifecycle label and the action header use Hangul.
    assert "도구 실행 후" in msg
    assert "WebFetch" in msg


def test_raw_ir_smuggled_gate_binary_does_not_land():
    """A hand-crafted draft_ir cannot smuggle gate_binary onto the
    merged draft. _sanitize_draft_so_far is the boundary."""
    draft_ir = {
        "id": "block-rm",
        "version": "0.1",
        "trigger": {
            "host": "claude-code",
            "event": "PreToolUse",
            "matcher": "Bash",
        },
        "requires": [{"kind": "regex", "pattern": "rm -rf"}],
        "action": "block",
        # smuggled fields the sanitizer drops:
        "gate_binary": "/etc/malicious",
        "on_signature_invalid": "allow",
        "sentinel_re": ".*",
        "type": "permission",
    }
    out = build_handoff_turn(wizard_state=None, draft_ir=draft_ir)
    merged = out["draft"]
    assert merged is not None
    assert "gate_binary" not in merged
    assert "on_signature_invalid" not in merged
    assert "sentinel_re" not in merged
    assert "type" not in merged
    # Author-supplied fields still survive.
    assert merged["trigger"]["matcher"] == "Bash"
    assert merged["action"] == "block"
    assert merged["id"] == "block-rm"


def test_wizard_state_wins_over_draft_ir_on_conflict():
    """The wizard surface is the more recent author intent (the user
    just clicked Continue from inside it), so conflicting matchers
    should resolve to the wizard's pick."""
    draft_ir = {
        "id": "old-id",
        "version": "0.1",
        "trigger": {
            "host": "claude-code",
            "event": "PreToolUse",
            "matcher": "Edit",
        },
        "action": "audit",
    }
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "action": "block",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=draft_ir)
    merged = out["draft"]
    assert merged is not None
    assert merged["trigger"]["matcher"] == "Bash"
    assert merged["action"] == "block"
    # id was not overwritten by the wizard state (no id field present),
    # so the raw editor's value survives.
    assert merged["id"] == "old-id"


def test_inject_context_action_is_collapsed_and_called_out():
    """The conversational vocab cannot model inject_context; the
    serialiser collapses it (no on_missing landed) and the assistant
    summary mentions the collapse."""
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "action": "inject_context",
        "injectTemplate": "Reminder: be careful",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    # `on_missing` lands in missing_fields because action wasn't kept.
    assert "on_missing" in out["missing_fields"]
    assert "inject_context" in out["assistant_message"]
    # The injected template never lands on the draft (it has no slot
    # on the conversational vocabulary).
    if out["draft"] is not None:
        assert "template" not in out["draft"]


def test_unknown_condition_kind_is_collapsed_and_called_out():
    """A wizard state with a domain_allowlist condition is mapped down
    to a regex requires; an unrecognised kind would be dropped silently
    and the summary should mention it."""
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "WebFetch",
        "conditionKind": "banana",  # unknown kind
        "action": "audit",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    msg = out["assistant_message"]
    assert "banana" in msg
    # Without a kind the wizard still leaves the requires slot empty.
    assert "requires" in out["missing_fields"]


def test_domain_allowlist_compiles_to_regex_requirement():
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "WebFetch",
        "conditionKind": "domain_allowlist",
        "allowlist": "github.com, npmjs.com",
        "action": "ask",
        "id": "webfetch-allow",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    merged = out["draft"]
    assert merged is not None
    reqs = merged.get("requires") or []
    assert reqs, "expected requires to land"
    assert reqs[0]["kind"] == "regex"
    # The domain is regex-escaped so the literal dot is `\.`.
    assert r"github\.com" in reqs[0]["pattern"]
    assert r"npmjs\.com" in reqs[0]["pattern"]


def test_oversized_state_raises():
    blob = "x" * 30_000
    with pytest.raises(HandoffContextError):
        build_handoff_turn(
            wizard_state={"description": blob}, draft_ir=None,
        )


def test_complete_draft_is_ready_to_save():
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "conditionKind": "regex",
        "pattern": "(^|\\s)sudo\\s",
        "action": "block",
        "id": "block-sudo",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    assert out["ready_to_save"] is True
    assert out["needs_more"] is False
    assert out["questions"] == []


# ── route-level tests ─────────────────────────────────────────────────


def test_route_returns_seeded_turn_with_questions():
    c = _client()
    r = c.post(
        "/policies/handoff-context",
        headers=HEADERS,
        json={
            "wizard_state": {
                "lifecycle": "before_tool_use",
                "toolScope": "Bash",
                "action": "block",
            },
            "draft_ir": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "assistant_message" in body
    assert "questions" in body
    assert "draft" in body
    assert "missing_fields" in body
    assert "needs_more" in body
    assert "ready_to_save" in body


def test_route_403_without_admin_key():
    c = _client()
    r = c.post(
        "/policies/handoff-context",
        json={"wizard_state": None, "draft_ir": None},
    )
    # The require_admin_key dependency returns 401 / 403; both are
    # canonical "no auth" responses.
    assert r.status_code in (401, 403)


def test_route_extra_fields_are_rejected():
    """`extra=forbid` on HandoffContextReq catches typos / smuggled keys."""
    c = _client()
    r = c.post(
        "/policies/handoff-context",
        headers=HEADERS,
        json={
            "wizard_state": {},
            "draft_ir": None,
            "smuggled": "x",
        },
    )
    assert r.status_code == 422


def test_route_oversize_wizard_state_422():
    c = _client()
    r = c.post(
        "/policies/handoff-context",
        headers=HEADERS,
        json={
            "wizard_state": {"description": "x" * 30_000},
            "draft_ir": None,
        },
    )
    assert r.status_code == 422
