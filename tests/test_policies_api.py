"""v1-P2 — /policies CRUD API."""
import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.keys import KeyStore


API_KEY = "p-api-key"
HITL_KEY = "p-hitl-key"
ADMIN_KEY = "p-admin-key"

ADMIN = {"X-Admin-Api-Key": ADMIN_KEY}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAGI_CP_API_KEY", API_KEY)
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", HITL_KEY)
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", ADMIN_KEY)


@pytest.fixture
def client(tmp_path):
    ks = KeyStore(dir=str(tmp_path / "keys"))
    app = create_app(keystore=ks, dsn="sqlite:///:memory:",
                     policy_store_path=str(tmp_path / "policies.json"))
    return TestClient(app)


def _valid_policy(**override):
    base = {
        "id": "legal-filing/v1",
        "description": "t",
        "version": "0.1",
        "trigger": {"host": "claude-code", "event": "PreToolUse", "matcher": "Bash"},
        "sentinel_re": r"FILE_COURT_(?P<matter>[A-Za-z0-9]+)_(?P<doc_id>[A-Za-z0-9]+)",
        "requires": [{"step": "citation_verify", "verdict": "pass"}],
        "on_missing": "deny",
        "on_signature_invalid": "deny",
        "gate_binary": "/usr/local/bin/magi-gate.sh",
    }
    base.update(override)
    return base


def _put(client, pid, body, *, source="org", enabled=True):
    return client.put(f"/policies/{pid}",
                      json={"policy": body, "source": source, "enabled": enabled},
                      headers=ADMIN)


# ── auth ─────────────────────────────────────────────────────────────
def test_admin_endpoints_require_key(client):
    assert client.get("/policies").status_code == 401
    assert client.put("/policies/x", json={}).status_code == 401
    assert client.patch("/policies/x/enabled", json={"enabled": False}).status_code == 401
    assert client.get("/policies/x").status_code == 401
    assert client.get("/policies/x/compiled").status_code == 401


def test_admin_unset_env_fails_closed_503(client, monkeypatch):
    monkeypatch.delenv("MAGI_CP_ADMIN_API_KEY")
    r = client.get("/policies", headers={"X-Admin-Api-Key": "anything"})
    assert r.status_code == 503
    # round-2 review: env var name must NOT leak to caller.
    assert "MAGI_CP_ADMIN_API_KEY" not in r.text


def test_put_rejects_reserved_id_suffix(client):
    """policy id must not end with /compiled or /enabled (sibling-route collision)."""
    body = _valid_policy(id="foo/compiled")
    r = client.put("/policies/foo/compiled",
                   json={"policy": body, "source": "org", "enabled": True},
                   headers=ADMIN)
    assert r.status_code == 400
    assert "reserved" in r.json()["detail"].lower() or "compiled" in r.json()["detail"].lower()


# ── empty list ───────────────────────────────────────────────────────
def test_list_starts_empty(client):
    r = client.get("/policies", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["items"] == []


# ── create via PUT ───────────────────────────────────────────────────
def test_put_creates_policy(client):
    r = _put(client, "legal-filing/v1", _valid_policy())
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["id"] == "legal-filing/v1"
    assert j["source"] == "org"
    assert j["enabled"] is True


def test_put_rejects_id_mismatch(client):
    r = _put(client, "wrong/id", _valid_policy(id="legal-filing/v1"))
    assert r.status_code == 400
    assert "id mismatch" in r.json()["detail"].lower()


def test_put_rejects_illegal_matrix_combo(client):
    body = _valid_policy(on_missing="log")    # PreToolUse + Bash + log is illegal
    r = _put(client, "legal-filing/v1", body)
    assert r.status_code == 400
    assert "illegal" in r.json()["detail"].lower()


def test_put_rejects_bad_source(client):
    r = client.put("/policies/x",
                   json={"policy": _valid_policy(id="x"),
                         "source": "ghost", "enabled": True},
                   headers=ADMIN)
    assert r.status_code == 422


# ── list / get / compiled ────────────────────────────────────────────
def test_list_after_put(client):
    _put(client, "legal-filing/v1", _valid_policy())
    r = client.get("/policies", headers=ADMIN)
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == "legal-filing/v1"
    assert items[0]["enabled"] is True
    assert items[0]["source"] == "org"
    assert items[0]["enforcement"]   # label present


def test_get_returns_resolved_view(client):
    _put(client, "legal-filing/v1", _valid_policy())
    r = client.get("/policies/legal-filing/v1", headers=ADMIN)
    assert r.status_code == 200
    j = r.json()
    assert j["id"] == "legal-filing/v1"
    assert j["policy"]["trigger"]["event"] == "PreToolUse"
    assert "compiled_sha256" in j


def test_get_unknown_returns_404(client):
    assert client.get("/policies/ghost", headers=ADMIN).status_code == 404


def test_compiled_returns_managed_settings(client):
    _put(client, "legal-filing/v1", _valid_policy())
    r = client.get("/policies/legal-filing/v1/compiled", headers=ADMIN)
    assert r.status_code == 200
    j = r.json()
    assert j["managed_settings"]["allowManagedHooksOnly"] is True
    assert j["managed_settings"]["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
    assert j["sha256"] and len(j["sha256"]) == 64


def test_compiled_same_input_same_sha256(client):
    """Deterministic compiler: same policy compiles to same sha256."""
    _put(client, "legal-filing/v1", _valid_policy())
    a = client.get("/policies/legal-filing/v1/compiled", headers=ADMIN).json()["sha256"]
    b = client.get("/policies/legal-filing/v1/compiled", headers=ADMIN).json()["sha256"]
    assert a == b


# ── patch enabled ────────────────────────────────────────────────────
def test_patch_enabled_toggles(client):
    _put(client, "legal-filing/v1", _valid_policy())
    r = client.patch("/policies/legal-filing/v1/enabled",
                     json={"enabled": False}, headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    again = client.get("/policies/legal-filing/v1", headers=ADMIN).json()
    assert again["enabled"] is False


def test_patch_enabled_unknown_404(client):
    r = client.patch("/policies/ghost/enabled",
                     json={"enabled": False}, headers=ADMIN)
    assert r.status_code == 404


# ── persistence ──────────────────────────────────────────────────────
def test_put_persists_across_app_restart(tmp_path):
    """Two TestClients sharing the same policy_store_path see the same data."""
    ks = KeyStore(dir=str(tmp_path / "keys"))
    psp = str(tmp_path / "policies.json")
    app1 = create_app(keystore=ks, dsn="sqlite:///:memory:", policy_store_path=psp)
    c1 = TestClient(app1)
    _put(c1, "legal-filing/v1", _valid_policy())

    app2 = create_app(keystore=ks, dsn="sqlite:///:memory:", policy_store_path=psp)
    c2 = TestClient(app2)
    items = c2.get("/policies", headers=ADMIN).json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == "legal-filing/v1"


# ── update existing keeps file deterministic ─────────────────────────
def test_put_overwrites_existing_same_id(client):
    _put(client, "x", _valid_policy(id="x", description="orig"))
    _put(client, "x", _valid_policy(id="x", description="updated"))
    j = client.get("/policies/x", headers=ADMIN).json()
    assert j["policy"]["description"] == "updated"
    # only 1 entry total — overwrite, not append
    items = client.get("/policies", headers=ADMIN).json()["items"]
    assert len(items) == 1
