"""D77 - cloud REST surface for the synthetic CC hook payload simulator.

POST /policies/{id}/test and POST /policy-packs/{id}/test live alongside
the existing dry-run / compile authoring surfaces. Contract:

  - admin-key gated (same surface as dry-run)
  - 404 on unknown policy / pack
  - 422 on missing payload
  - returns the test_runner.PolicyTestResult envelope
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.keys import KeyStore


API_KEY = "test-api-key"
ADMIN_KEY = "tester-admin-key"
HEADERS_ADMIN = {"X-Admin-Api-Key": ADMIN_KEY}


@pytest.fixture(autouse=True)
def _set_keys(monkeypatch):
    monkeypatch.setenv("MAGI_CP_API_KEY", API_KEY)
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", ADMIN_KEY)


@pytest.fixture
def app(tmp_path):
    ks = KeyStore(dir=str(tmp_path / "keys"))
    return create_app(
        keystore=ks,
        dsn="sqlite:///:memory:",
        policy_store_path=str(tmp_path / "policies.json"),
        pack_store_path=str(tmp_path / "packs.json"),
    )


@pytest.fixture
def client(app):
    return TestClient(app)


def _put_policy(client, policy_id: str, body: dict) -> None:
    r = client.put(
        f"/policies/{policy_id}",
        json={"policy": body, "source": "bot", "enabled": True},
        headers=HEADERS_ADMIN,
    )
    assert r.status_code in (200, 201), r.text


def test_policy_test_endpoint_requires_admin_key(client):
    r = client.post("/policies/foo/test", json={"payload": {}})
    assert r.status_code == 401


def test_policy_test_endpoint_404_on_unknown_id(client):
    r = client.post(
        "/policies/never-exists/test",
        json={"payload": {"hook_event_name": "PreToolUse"}},
        headers=HEADERS_ADMIN,
    )
    assert r.status_code == 404


def test_policy_test_endpoint_422_on_missing_payload(client):
    r = client.post(
        "/policies/foo/test", json={}, headers=HEADERS_ADMIN,
    )
    assert r.status_code == 422


def test_policy_test_endpoint_returns_deny_envelope_for_evidence(client):
    # P1 review fix: PermissionPolicy now returns INDETERMINATE
    # (CC owns the decision; we cannot honestly replay it offline);
    # the "deny envelope" surface contract is now pinned via
    # EvidencePolicy with a regex requires that resolves to a
    # missing-field deny. The runtime gate emits the
    # `hookSpecificOutput.permissionDecision='deny'` shape on this
    # path (cc_shapes.emit_deny_payload) and the simulator now reuses
    # that helper.
    _put_policy(client, "d77/deny-rmrf", {
        "id": "d77/deny-rmrf",
        "type": "evidence",
        "description": "block rm -rf via regex",
        "trigger": {"host": "claude-code", "event": "PreToolUse",
                    "matcher": "Bash"},
        "requires": [{"kind": "regex", "pattern": r"rm\s+-rf",
                       "field_path": "tool_input.command"}],
        "action": "block",
    })
    r = client.post(
        "/policies/d77/deny-rmrf/test",
        # Payload's command lacks "rm -rf" so the regex requires
        # FAILS, which under the EvidencePolicy combine semantics
        # means the action fires (block → deny envelope). This is the
        # path the cloud route's deny-shape contract pins.
        json={
            "payload": {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "ls -al"},
            },
        },
        headers=HEADERS_ADMIN,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["verdict"] == "deny"
    assert data["action"] == "block"
    assert data["policy_id"] == "d77/deny-rmrf"
    assert data["policy_type"] == "evidence"
    # gate.py + cc_shapes.emit_deny_payload contract: PreToolUse
    # carries the hookSpecificOutput envelope.
    hso = data["hook_specific_output"]
    assert "hookSpecificOutput" in hso
    assert hso["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_policy_test_endpoint_permission_archetype_indeterminate(client):
    # Companion contract: PermissionPolicy returns INDETERMINATE with
    # the per-archetype explanation. The dashboard renders "CC owns
    # this decision" instead of a fabricated verdict pill.
    _put_policy(client, "d77/permission-deny", {
        "id": "d77/permission-deny",
        "type": "permission",
        "description": "deny rm -rf via managed-settings",
        "trigger": {"host": "claude-code", "event": "PreToolUse",
                    "matcher": "Bash"},
        "permission": "deny",
        "pattern": "Bash(rm -rf /*)",
    })
    r = client.post(
        "/policies/d77/permission-deny/test",
        json={
            "payload": {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "rm -rf /"},
            },
        },
        headers=HEADERS_ADMIN,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["verdict"] == "indeterminate"
    assert data["action"] == "indeterminate"
    assert data["skipped_reason"] == "declarative-archetype-cc-owned"


def test_policy_test_endpoint_returns_skipped_on_trigger_mismatch(client):
    _put_policy(client, "d77/bash-only", {
        "id": "d77/bash-only",
        "type": "permission",
        "description": "bash only",
        "trigger": {"host": "claude-code", "event": "PreToolUse",
                    "matcher": "Bash"},
        "permission": "deny",
        "pattern": "Bash(curl *)",
    })
    r = client.post(
        "/policies/d77/bash-only/test",
        json={
            "payload": {
                "hook_event_name": "PreToolUse",
                "tool_name": "Read",
            },
        },
        headers=HEADERS_ADMIN,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["verdict"] == "skipped"
    assert data["skipped_reason"] == "trigger-mismatch"


def test_pack_test_endpoint_404_on_unknown_pack(client):
    r = client.post(
        "/policy-packs/never-exists/test",
        json={"payload": {"hook_event_name": "PreToolUse"}},
        headers=HEADERS_ADMIN,
    )
    assert r.status_code == 404


def test_pack_test_endpoint_runs_each_member(client):
    # Seed two policies, then bundle them in a user-pack.
    _put_policy(client, "d77/p1", {
        "id": "d77/p1",
        "type": "permission",
        "description": "p1",
        "trigger": {"host": "claude-code", "event": "PreToolUse",
                    "matcher": "Bash"},
        "permission": "deny",
        "pattern": "Bash(curl *)",
    })
    _put_policy(client, "d77/p2", {
        "id": "d77/p2",
        "type": "permission",
        "description": "p2",
        "trigger": {"host": "claude-code", "event": "PreToolUse",
                    "matcher": "Bash"},
        "permission": "ask",
        "pattern": "Bash(rm *)",
    })
    pack_create = client.post(
        "/policy-packs",
        json={
            "name": "D77 test pack",
            "description": "two-member fixture",
            "policy_ids": ["d77/p1", "d77/p2"],
        },
        headers=HEADERS_ADMIN,
    )
    assert pack_create.status_code == 200, pack_create.text
    pack_id = pack_create.json()["id"]

    r = client.post(
        f"/policy-packs/{pack_id}/test",
        json={
            "payload": {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "rm -rf /"},
            },
        },
        headers=HEADERS_ADMIN,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["pack_id"] == pack_id
    assert data["member_count"] == 2
    assert len(data["members"]) == 2
    member_ids = {m["policy_id"] for m in data["members"]}
    assert member_ids == {"d77/p1", "d77/p2"}
