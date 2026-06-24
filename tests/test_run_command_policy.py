"""D63 — RunCommandPolicy backend tests.

Covers:
  - IR validators (exactly-one-of command/script_path, runtime literal,
    args caps, timeout bounds, matrix coherence).
  - matrix.py rows: every supported event accepts run_command on the
    correct matcher class.
  - script_store add/list/get/delete semantics including dedupe-by-hash
    and in-use refusal.
  - cloud /scripts POST/GET/DELETE round-trip and env-gated refusal.
  - cloud /policies/run_command spec resolution.
  - local gate execute_run_command soft / fail-closed lanes.
"""
from __future__ import annotations

import base64
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.script_store import (
    MAX_SCRIPT_BYTES, ScriptStore, ScriptStoreConflict, ScriptStoreError,
    ScriptStoreInUseError,
)
from magi_cp.local.gate import execute_run_command
from magi_cp.policy.ir import RunCommandPolicy, Trigger, policy_from_dict
from magi_cp.policy.matrix import LEGAL_COMBINATIONS, MatcherClass


ADMIN_KEY_HEADER = {"X-Admin-Api-Key": "test-admin-key"}


@pytest.fixture
def admin_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", "test-admin-key")


@pytest.fixture
def tmp_paths(tmp_path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Hermetic temp dirs for policy + script + DB so each test gets a
    fresh slate without leaking into $HOME."""
    policy_path = tmp_path / "policies.json"
    custom_v_path = tmp_path / "custom_verifiers.json"
    db_path = tmp_path / "magi-cp.sqlite"
    monkeypatch.setenv("MAGI_CP_POLICY_STORE", str(policy_path))
    monkeypatch.setenv("MAGI_CP_CUSTOM_VERIFIER_STORE", str(custom_v_path))
    monkeypatch.setenv("MAGI_CP_SCRIPT_STORE_DIR", str(tmp_path))
    monkeypatch.setenv("MAGI_CP_DSN", f"sqlite:///{db_path}")
    monkeypatch.setenv("MAGI_CP_KEY_DIR", str(tmp_path / "keys"))
    return {
        "policy_path": str(policy_path),
        "script_dir": str(tmp_path),
        "db": str(db_path),
    }


@pytest.fixture
def client(admin_env, tmp_paths) -> TestClient:
    app = create_app()
    return TestClient(app)


# ── IR validators ───────────────────────────────────────────────────
def test_run_command_policy_requires_exactly_one_of_command_or_script():
    with pytest.raises(ValueError, match="exactly one of"):
        RunCommandPolicy(
            id="p.both",
            description="",
            trigger=Trigger(event="PreToolUse", matcher="Bash"),
            command="echo hi",
            script_path="a" * 64,
        )
    with pytest.raises(ValueError, match="exactly one of"):
        RunCommandPolicy(
            id="p.neither",
            description="",
            trigger=Trigger(event="PreToolUse", matcher="Bash"),
        )


def test_run_command_policy_runtime_validated():
    with pytest.raises(ValueError, match="runtime"):
        RunCommandPolicy(
            id="p.bad-runtime",
            description="",
            trigger=Trigger(event="PreToolUse", matcher="Bash"),
            command="echo",
            runtime="ruby",  # type: ignore[arg-type]
        )


def test_run_command_policy_timeout_bounds():
    with pytest.raises(ValueError, match="timeout_ms"):
        RunCommandPolicy(
            id="p.tiny",
            description="",
            trigger=Trigger(event="PreToolUse", matcher="Bash"),
            command="echo",
            timeout_ms=50,
        )
    with pytest.raises(ValueError, match="timeout_ms"):
        RunCommandPolicy(
            id="p.huge",
            description="",
            trigger=Trigger(event="PreToolUse", matcher="Bash"),
            command="echo",
            timeout_ms=60_000,
        )


def test_run_command_policy_args_caps():
    with pytest.raises(ValueError, match="too many args"):
        RunCommandPolicy(
            id="p.too-many",
            description="",
            trigger=Trigger(event="PreToolUse", matcher="Bash"),
            command="echo",
            args=["a"] * 17,
        )
    with pytest.raises(ValueError, match="too long"):
        RunCommandPolicy(
            id="p.long-arg",
            description="",
            trigger=Trigger(event="PreToolUse", matcher="Bash"),
            command="echo",
            args=["x" * 257],
        )


def test_run_command_policy_inline_length_cap():
    with pytest.raises(ValueError, match="inline command too long"):
        RunCommandPolicy(
            id="p.huge-inline",
            description="",
            trigger=Trigger(event="PreToolUse", matcher="Bash"),
            command="x" * 4_001,
        )


def test_run_command_policy_script_path_shape():
    # Script path must be a 16..64 hex id.
    with pytest.raises(ValueError, match="hex script id"):
        RunCommandPolicy(
            id="p.bad-script",
            description="",
            trigger=Trigger(event="PreToolUse", matcher="Bash"),
            script_path="/etc/passwd",
        )


def test_run_command_policy_round_trips_through_policy_from_dict():
    body = {
        "type": "run_command",
        "id": "p.rt",
        "description": "round-trip",
        "trigger": {"host": "claude-code", "event": "PreToolUse",
                    "matcher": "Bash"},
        "runtime": "bash",
        "command": "git status",
        "script_path": "",
        "args": ["--short"],
        "timeout_ms": 3000,
        "fail_closed": True,
        "version": "0.1",
    }
    p = policy_from_dict(body)
    assert isinstance(p, RunCommandPolicy)
    assert p.command == "git status"
    assert p.fail_closed is True
    assert p.timeout_ms == 3000


# ── matrix coverage ─────────────────────────────────────────────────
def test_matrix_run_command_legal_on_every_supported_event():
    from magi_cp.policy.ir import _SUPPORTED_EVENTS

    seen_events = {
        ev for ev, _kls, action in LEGAL_COMBINATIONS if action == "run_command"
    }
    # All 30 supported events accept run_command (CC stdout JSON
    # contract is uniform across hooks).
    assert seen_events == set(_SUPPORTED_EVENTS)


def test_matrix_run_command_tool_context_events_carry_per_tool_matchers():
    tool_context = {
        "PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch",
    }
    rc = [(ev, kls) for ev, kls, action in LEGAL_COMBINATIONS
          if action == "run_command"]
    for ev in tool_context:
        rows = {kls for e, kls in rc if e == ev}
        assert MatcherClass.tool in rows
        assert MatcherClass.mcp_tool in rows
        assert MatcherClass.tool_alt in rows
        assert MatcherClass.wildcard in rows
    # Non-tool-context lifecycles → wildcard only.
    for ev, kls in rc:
        if ev not in tool_context:
            assert kls is MatcherClass.wildcard


# ── script_store ────────────────────────────────────────────────────
def test_script_store_dedupes_by_hash(tmp_path):
    store = ScriptStore(dir=str(tmp_path))
    body = b"#!/bin/bash\necho hi\n"
    e1 = store.add(name="hello", runtime="bash", body=body)
    e2 = store.add(name="hello", runtime="bash", body=body)
    assert e1.id == e2.id
    assert e1.hash == e2.hash
    assert e1.size_bytes == len(body)


def test_script_store_conflict_on_same_name_different_body(tmp_path):
    store = ScriptStore(dir=str(tmp_path))
    store.add(name="probe", runtime="bash", body=b"echo a\n")
    with pytest.raises(ScriptStoreConflict):
        store.add(name="probe", runtime="bash", body=b"echo b\n")


def test_script_store_rejects_oversized_body(tmp_path):
    store = ScriptStore(dir=str(tmp_path))
    with pytest.raises(ScriptStoreError):
        store.add(
            name="huge", runtime="bash",
            body=b"x" * (MAX_SCRIPT_BYTES + 1),
        )


def test_script_store_delete_refuses_when_in_use(tmp_path):
    store = ScriptStore(dir=str(tmp_path))
    entry = store.add(name="x", runtime="bash", body=b"echo\n")
    with pytest.raises(ScriptStoreInUseError) as excinfo:
        store.delete(entry.id, referenced_by=["pol.a", "pol.b"])
    assert excinfo.value.policy_ids == ["pol.a", "pol.b"]


def test_script_store_delete_clears_unused(tmp_path):
    store = ScriptStore(dir=str(tmp_path))
    entry = store.add(name="x", runtime="bash", body=b"echo\n")
    removed = store.delete(entry.id)
    assert removed is not None
    assert removed.id == entry.id
    assert store.list() == []


# ── /scripts cloud routes ──────────────────────────────────────────
def _upload_script(client: TestClient, *, name: str, runtime: str,
                   body: bytes) -> dict:
    r = client.post(
        "/scripts",
        json={
            "name": name,
            "runtime": runtime,
            "body_b64": base64.b64encode(body).decode("ascii"),
        },
        headers=ADMIN_KEY_HEADER,
    )
    return {"status": r.status_code, "body": r.json()}


def test_scripts_upload_list_delete_roundtrip(client: TestClient):
    up = _upload_script(client, name="probe", runtime="bash",
                        body=b"#!/bin/bash\necho hello\n")
    assert up["status"] == 200
    sid = up["body"]["id"]
    assert up["body"]["runtime"] == "bash"
    listed = client.get("/scripts", headers=ADMIN_KEY_HEADER).json()
    assert any(e["id"] == sid for e in listed["items"])
    delr = client.delete(f"/scripts/{sid}", headers=ADMIN_KEY_HEADER)
    assert delr.status_code == 200


def test_scripts_upload_blocked_when_env_disabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("MAGI_CP_ALLOW_RUN_COMMAND", "0")
    r = client.post(
        "/scripts",
        json={
            "name": "x", "runtime": "bash",
            "body_b64": base64.b64encode(b"echo\n").decode("ascii"),
        },
        headers=ADMIN_KEY_HEADER,
    )
    assert r.status_code == 403
    assert "disabled" in r.json()["detail"].lower()


def test_run_command_policy_save_blocked_when_env_disabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("MAGI_CP_ALLOW_RUN_COMMAND", "0")
    r = client.put(
        "/policies/p.run-command",
        json={
            "policy": {
                "type": "run_command",
                "id": "p.run-command",
                "description": "blocked",
                "trigger": {"host": "claude-code", "event": "PreToolUse",
                            "matcher": "Bash"},
                "runtime": "bash",
                "command": "echo hi",
                "script_path": "",
                "args": [],
                "timeout_ms": 5000,
                "fail_closed": False,
                "version": "0.1",
            },
            "source": "bot",
            "enabled": True,
        },
        headers=ADMIN_KEY_HEADER,
    )
    assert r.status_code == 403


def test_run_command_policy_save_succeeds_by_default(client: TestClient):
    r = client.put(
        "/policies/p.run-command",
        json={
            "policy": {
                "type": "run_command",
                "id": "p.run-command",
                "description": "ok",
                "trigger": {"host": "claude-code", "event": "PreToolUse",
                            "matcher": "Bash"},
                "runtime": "bash",
                "command": "echo ok",
                "script_path": "",
                "args": [],
                "timeout_ms": 2000,
                "fail_closed": False,
                "version": "0.1",
            },
            "source": "bot",
            "enabled": True,
        },
        headers=ADMIN_KEY_HEADER,
    )
    assert r.status_code == 200, r.text
    # GET round-trip should round-trip the policy shape.
    g = client.get("/policies/p.run-command", headers=ADMIN_KEY_HEADER).json()
    assert g["policy"]["type"] == "run_command"
    assert g["policy"]["command"] == "echo ok"


def test_resolve_run_command_returns_spec(client: TestClient):
    client.put(
        "/policies/p.resolve",
        json={
            "policy": {
                "type": "run_command",
                "id": "p.resolve",
                "description": "",
                "trigger": {"host": "claude-code", "event": "PreToolUse",
                            "matcher": "Bash"},
                "runtime": "bash",
                "command": "echo from-cloud",
                "script_path": "",
                "args": ["a", "b"],
                "timeout_ms": 1500,
                "fail_closed": True,
                "version": "0.1",
            },
            "source": "bot",
            "enabled": True,
        },
        headers=ADMIN_KEY_HEADER,
    )
    r = client.post(
        "/policies/run_command",
        json={"policy_id": "p.resolve", "payload": {}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] is True
    assert body["spec"]["command"] == "echo from-cloud"
    assert body["spec"]["args"] == ["a", "b"]
    assert body["spec"]["fail_closed"] is True


def test_resolve_run_command_unknown_returns_not_matched(client: TestClient):
    r = client.post(
        "/policies/run_command",
        json={"policy_id": "p.unknown", "payload": {}},
    )
    assert r.status_code == 200
    assert r.json()["matched"] is False


# ── local gate execute_run_command ──────────────────────────────────
def test_execute_run_command_passes_through_stdout_json(tmp_path,
                                                         monkeypatch):
    monkeypatch.setenv("MAGI_CP_LOCAL_DIR", str(tmp_path))
    # Echo a CC-shaped hookSpecificOutput JSON.
    payload = {"hookSpecificOutput": {"permissionDecision": "allow"}}
    out = execute_run_command(
        policy_id="p.echo",
        runtime="bash",
        command=f"echo '{json.dumps(payload)}'",
        timeout_ms=5000,
    )
    assert out == payload


def test_execute_run_command_audit_lane_on_nonzero_exit(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CP_LOCAL_DIR", str(tmp_path))
    out = execute_run_command(
        policy_id="p.fail",
        runtime="bash",
        command="exit 7",
        timeout_ms=2000,
        fail_closed=False,
    )
    # Default lane: allow + ledger entry.
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    # Ledger row was written.
    ledger = tmp_path / "run_command_ledger.jsonl"
    assert ledger.exists()
    rows = [json.loads(r) for r in ledger.read_text().splitlines()]
    assert any(row["policy_id"] == "p.fail" and row["exit_code"] == 7
               for row in rows)


def test_execute_run_command_fail_closed_on_nonzero_exit(tmp_path,
                                                         monkeypatch):
    monkeypatch.setenv("MAGI_CP_LOCAL_DIR", str(tmp_path))
    out = execute_run_command(
        policy_id="p.fail-closed",
        runtime="bash",
        command="exit 1",
        timeout_ms=2000,
        fail_closed=True,
    )
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "non-zero exit" in out["hookSpecificOutput"][
        "permissionDecisionReason"
    ]


def test_execute_run_command_timeout_audit_default(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CP_LOCAL_DIR", str(tmp_path))
    out = execute_run_command(
        policy_id="p.slow",
        runtime="bash",
        command="sleep 1",
        timeout_ms=200,
        fail_closed=False,
    )
    # Audit lane → allow.
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_execute_run_command_timeout_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CP_LOCAL_DIR", str(tmp_path))
    out = execute_run_command(
        policy_id="p.slow-fc",
        runtime="bash",
        command="sleep 1",
        timeout_ms=200,
        fail_closed=True,
    )
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "timeout" in out["hookSpecificOutput"][
        "permissionDecisionReason"
    ].lower()
