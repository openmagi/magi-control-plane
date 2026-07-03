"""Policy-integrity review: does an authored policy implement the intent?

Deterministic structural checks (always) + optional advisory LLM semantic
pass (never gates the verdict on its own).
"""
from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.llm.provider import FakeLlmProvider
from magi_cp.policy.review import review_policy_draft


def _gate(**over):
    d = {
        "type": "evidence_gate", "id": "verified-trade",
        "kind": "source_credibility",
        "gate": {"matcher": "mcp__trading__execute_trade", "action": "block"},
    }
    d.update(over)
    return d


# ── deterministic integrity ────────────────────────────────────────────

def test_review_good_compound_ok():
    v = review_policy_draft(_gate())
    assert v["ok"] is True
    assert v["issues"] == []


def test_review_missing_matcher_is_error():
    v = review_policy_draft(_gate(gate={"matcher": "", "action": "block"}))
    assert v["ok"] is False
    assert any("which action to gate" in i["message"] for i in v["issues"])


def test_review_non_enforcing_action_warns_but_not_error():
    # audit action records but does not stop the tool.
    v = review_policy_draft(_gate(gate={"matcher": "Bash", "action": "audit"}))
    # 'audit' is not block/ask -> a warn; still ok (no error) unless another
    # error fires. The gate itself is otherwise valid.
    assert any(i["severity"] == "warn" and "does not stop" in i["message"]
               for i in v["issues"])


def test_review_orphan_reuse_is_error():
    """emit_audit=False with no producer for the kind -> the gate can never
    be satisfied."""
    v = review_policy_draft(
        _gate(emit_audit=False, gate={"matcher": "Bash", "action": "block"}),
        context={"audit_kinds": {}},
    )
    assert v["ok"] is False
    assert any("reuses an existing" in i["message"] for i in v["issues"])


def test_review_reuse_with_live_producer_ok():
    v = review_policy_draft(
        _gate(id="verified-cancel", emit_audit=False,
              gate={"matcher": "Bash", "action": "block"}),
        context={"audit_kinds": {"source_credibility": ["verified-trade"]}},
    )
    assert v["ok"] is True


def test_review_single_rule_draft_is_quiet():
    # A non-compound draft has no compound structural checks; PUT validates it.
    v = review_policy_draft({"id": "r1", "trigger": {"event": "Stop"}})
    assert v["ok"] is True


# ── optional LLM semantic layer ────────────────────────────────────────

def _reviewer(ok: bool, issues: list[str]) -> FakeLlmProvider:
    import json
    return FakeLlmProvider([json.dumps({"ok": ok, "issues": issues})])


def test_review_semantic_adds_warn_but_stays_advisory():
    """A reviewer flagging a mismatch adds a warn issue; because the
    deterministic layer found no error, ok stays True (semantic is
    advisory and cannot gate the save on its own)."""
    v = review_policy_draft(
        _gate(), intent="block trades without a credible source",
        reviewer=_reviewer(False, ["The gate targets the wrong tool."]),
    )
    assert v["ok"] is True  # no deterministic error
    assert any(i["source"] == "semantic" and i["severity"] == "warn"
               for i in v["issues"])


def test_review_malformed_reviewer_is_ignored():
    v = review_policy_draft(
        _gate(), intent="x", reviewer=FakeLlmProvider(["not json"]),
    )
    # malformed semantic response yields no issues, not a crash
    assert v["ok"] is True
    assert all(i["source"] != "semantic" for i in v["issues"])


def test_review_no_reviewer_skips_semantic():
    v = review_policy_draft(_gate(), intent="anything", reviewer=None)
    assert all(i["source"] != "semantic" for i in v["issues"])


# ── endpoint ───────────────────────────────────────────────────────────

HEADERS = {"X-Admin-Api-Key": "test-admin-key"}


@pytest.fixture(autouse=True)
def _admin_key(monkeypatch):
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", "test-admin-key")


def _client(*, llm_reviewer=None) -> TestClient:
    d = tempfile.mkdtemp(prefix="magi-cp-review-")
    path = os.path.join(d, "policies.json")
    with open(path, "w") as f:
        f.write("[]")
    app = create_app(
        dsn="sqlite:///:memory:", policy_store_path=path,
        llm_compiler=FakeLlmProvider([]), llm_reviewer=llm_reviewer,
    )
    return TestClient(app)


def test_review_endpoint_deterministic_no_provider():
    """The review endpoint works with NO reviewer LLM (deterministic loop)."""
    c = _client(llm_reviewer=None)
    r = c.post("/policies/review", headers=HEADERS,
               json={"draft": _gate(), "intent": "block bad trades"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_review_endpoint_flags_orphan():
    c = _client(llm_reviewer=None)
    r = c.post("/policies/review", headers=HEADERS, json={
        "draft": _gate(id="orphan", emit_audit=True) | {
            "emit_audit": False,
            "gate": {"matcher": "Bash", "action": "block"},
        },
    })
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is False


def test_review_endpoint_requires_admin_key():
    c = _client()
    r = c.post("/policies/review", json={"draft": _gate()})
    assert r.status_code in (401, 403)
