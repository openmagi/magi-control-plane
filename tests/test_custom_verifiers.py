"""Tenant-scoped custom verifier CRUD + runtime integration."""
from __future__ import annotations

import os
from fastapi.testclient import TestClient


def _client(monkeypatch):
    monkeypatch.setenv("MAGI_CP_API_KEY", "test-tenant-key")
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", "test-hitl-key")
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setenv("MAGI_CP_ADMIN_HMAC_SECRET", "test-hmac-secret")
    monkeypatch.setenv("MAGI_CP_DSN", "sqlite:///:memory:")

    from magi_cp.cloud.app import create_app
    from magi_cp.cloud.db import make_engine, init_schema
    from magi_cp.verifier.protocol import VerifierRegistry
    from magi_cp.verifier.builtins import register_builtins

    engine = make_engine("sqlite:///:memory:")
    init_schema(engine)
    registry = VerifierRegistry()
    register_builtins(registry)
    app = create_app(verifier_registry=registry)
    app.state.engine = engine
    return TestClient(app)


def _auth():
    return {"X-Api-Key": "test-tenant-key"}


def test_list_starts_empty(monkeypatch):
    c = _client(monkeypatch)
    r = c.get("/tenants/verifiers", headers=_auth())
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_upsert_then_list(monkeypatch):
    c = _client(monkeypatch)
    spec = {
        "step": "custom_keyword",
        "name": "Custom keyword check",
        "category": "SECURITY",
        "description": "Block when XYZZY appears",
        "kind": "regex",
        "config": {
            "pattern": r"\bXYZZY\b",
            "on_match": "deny",
            "reasons": ["forbidden keyword"],
        },
        "enabled": True,
    }
    r = c.post("/tenants/verifiers", json=spec, headers=_auth())
    assert r.status_code == 200, r.text
    assert r.json()["step"] == "custom_keyword"

    listed = c.get("/tenants/verifiers", headers=_auth()).json()
    assert len(listed["items"]) == 1
    assert listed["items"][0]["step"] == "custom_keyword"
    assert listed["items"][0]["enabled"] is True


def test_upsert_rejects_bad_regex(monkeypatch):
    c = _client(monkeypatch)
    bad = {
        "step": "bad_pattern",
        "name": "Bad pattern",
        "category": "SECURITY",
        "description": "broken regex",
        "kind": "regex",
        "config": {"pattern": "(", "on_match": "deny", "reasons": []},
        "enabled": True,
    }
    r = c.post("/tenants/verifiers", json=bad, headers=_auth())
    assert r.status_code == 422
    assert "regex" in r.json()["detail"].lower()


def test_upsert_rejects_bad_step_name(monkeypatch):
    c = _client(monkeypatch)
    bad = {
        "step": "BadStep",
        "name": "n",
        "category": "SECURITY",
        "description": "",
        "kind": "regex",
        "config": {"pattern": ".", "on_match": "deny", "reasons": []},
        "enabled": True,
    }
    r = c.post("/tenants/verifiers", json=bad, headers=_auth())
    assert r.status_code == 422


def test_toggle_enabled(monkeypatch):
    c = _client(monkeypatch)
    c.post("/tenants/verifiers", headers=_auth(), json={
        "step": "tog_test",
        "name": "tog",
        "category": "ANSWER",
        "description": "",
        "kind": "regex",
        "config": {"pattern": "FOO", "on_match": "deny", "reasons": []},
        "enabled": True,
    })
    r = c.post(
        "/tenants/verifiers/tog_test/enabled?enabled=false",
        headers=_auth(),
    )
    assert r.status_code == 200
    assert r.json() == {"step": "tog_test", "enabled": False}


def test_delete(monkeypatch):
    c = _client(monkeypatch)
    c.post("/tenants/verifiers", headers=_auth(), json={
        "step": "del_test",
        "name": "del",
        "category": "ANSWER",
        "description": "",
        "kind": "regex",
        "config": {"pattern": "FOO", "on_match": "deny", "reasons": []},
        "enabled": True,
    })
    r = c.delete("/tenants/verifiers/del_test", headers=_auth())
    assert r.status_code == 200
    assert r.json() == {"step": "del_test", "deleted": True}
    listed = c.get("/tenants/verifiers", headers=_auth()).json()
    assert listed == {"items": []}


def test_verifiers_endpoint_includes_custom(monkeypatch):
    c = _client(monkeypatch)
    c.post("/tenants/verifiers", headers=_auth(), json={
        "step": "merge_test",
        "name": "merged",
        "category": "FACT",
        "description": "shows up in /verifiers",
        "kind": "regex",
        "config": {"pattern": "BARRR", "on_match": "review", "reasons": []},
        "enabled": True,
    })
    r = c.get("/verifiers", headers=_auth())
    assert r.status_code == 200
    presets = r.json()["presets"]
    matching = [p for p in presets if p["step"] == "merge_test"]
    assert len(matching) == 1
    assert matching[0]["is_custom"] is True
    assert matching[0]["enforcement"] == "enforcing"  # enabled=True


def test_verify_dispatch_runs_custom(monkeypatch):
    c = _client(monkeypatch)
    c.post("/tenants/verifiers", headers=_auth(), json={
        "step": "dispatch_test",
        "name": "dispatch",
        "category": "SECURITY",
        "description": "regex check at /verify endpoint",
        "kind": "regex",
        "config": {
            "pattern": r"SECRET[_-]?LEAK",
            "on_match": "deny",
            "reasons": ["caught a secret leak pattern"],
        },
        "enabled": True,
    })

    # 'pass' path — payload does not match
    r = c.post(
        "/verify/dispatch_test",
        headers=_auth(),
        json={"payload": {"text": "all clear"}, "matter": "m", "doc_id": "d"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "pass"

    # 'deny' path — payload matches
    r = c.post(
        "/verify/dispatch_test",
        headers=_auth(),
        json={"payload": {"text": "found SECRET_LEAK here"}, "matter": "m", "doc_id": "d"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "deny"
    assert "caught a secret leak pattern" in r.json()["reasons"]


def test_verify_dispatch_404_when_disabled(monkeypatch):
    c = _client(monkeypatch)
    c.post("/tenants/verifiers", headers=_auth(), json={
        "step": "disabled_test",
        "name": "disabled",
        "category": "SECURITY",
        "description": "",
        "kind": "regex",
        "config": {"pattern": "FOO", "on_match": "deny", "reasons": []},
        "enabled": False,   # explicitly disabled
    })
    r = c.post(
        "/verify/disabled_test",
        headers=_auth(),
        json={"payload": {"text": "FOO"}, "matter": "m", "doc_id": "d"},
    )
    assert r.status_code == 404
