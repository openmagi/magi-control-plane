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


def test_policy_test_endpoint_returns_block_envelope(client):
    _put_policy(client, "d77/deny-rmrf", {
        "id": "d77/deny-rmrf",
        "type": "permission",
        "description": "block rm -rf",
        "trigger": {"host": "claude-code", "event": "PreToolUse",
                    "matcher": "Bash"},
        "permission": "deny",
        "pattern": "Bash(rm -rf /*)",
    })
    r = client.post(
        "/policies/d77/deny-rmrf/test",
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
    assert data["verdict"] == "deny"
    assert data["action"] == "block"
    assert data["policy_id"] == "d77/deny-rmrf"
    assert data["policy_type"] == "permission"
    assert "hookSpecificOutput" in data["hook_specific_output"]


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
