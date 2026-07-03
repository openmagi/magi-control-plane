"""POST /policies/compound — expand a compound draft + save its members."""
import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.keys import KeyStore

ADMIN = {"X-Admin-Api-Key": "a-key"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAGI_CP_API_KEY", "p-key")
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", "a-key")


@pytest.fixture
def client(tmp_path):
    ks = KeyStore(dir=str(tmp_path / "keys"))
    app = create_app(keystore=ks, dsn="sqlite:///:memory:",
                     policy_store_path=str(tmp_path / "policies.json"))
    return TestClient(app)


def _draft(**over):
    d = {
        "type": "evidence_gate",
        "id": "verified-trade",
        "description": "Require a credible source before trading",
        "kind": "source_credibility",
        "project_scope": "/Users/kevin/trading-mcp",
        "audit": {"event": "PostToolUse", "matcher": "WebFetch|Bash"},
        "gate": {"event": "PreToolUse", "matcher": "mcp__trading__execute_trade",
                 "action": "block", "reason": "verify a source first"},
    }
    d.update(over)
    return d


def test_requires_admin(client):
    assert client.post("/policies/compound", json={"draft": _draft(), "source": "org"}).status_code == 401


def test_expands_and_saves_both_members(client):
    r = client.post("/policies/compound", json={"draft": _draft(), "source": "org"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ids"] == ["verified-trade-audit", "verified-trade-gate"]
    assert body["types"] == ["evidence_audit", "evidence_precondition"]
    # both are now readable via GET
    for pid in body["ids"]:
        assert client.get(f"/policies/{pid}", headers=ADMIN).status_code == 200


def test_unknown_compound_type_is_400(client):
    r = client.post("/policies/compound", json={"draft": {"type": "nope"}, "source": "org"}, headers=ADMIN)
    assert r.status_code == 400


def test_invalid_member_is_atomic_400_nothing_saved(client):
    # A gate with no matcher fails IR validation -> whole compound rejected,
    # and the audit member must NOT be left behind.
    bad = _draft(gate={"event": "PreToolUse", "matcher": ""})
    r = client.post("/policies/compound", json={"draft": bad, "source": "org"}, headers=ADMIN)
    assert r.status_code == 400
    assert client.get("/policies/verified-trade-audit", headers=ADMIN).status_code == 404
    assert client.get("/policies/verified-trade-gate", headers=ADMIN).status_code == 404
