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


def test_authors_policy_owning_both_rules(client):
    r = client.post("/policies/compound", json={"draft": _draft(), "source": "org"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "verified-trade" and body["kind"] == "compound"
    # audit + gate + 3 ledger-protection denies
    assert body["rule_ids"][:2] == ["verified-trade-audit", "verified-trade-gate"]
    assert len(body["rule_ids"]) == 5
    # every member rule readable
    for pid in body["rule_ids"]:
        assert client.get(f"/policies/{pid}", headers=ADMIN).status_code == 200
    # the policy shows up as ONE grouping (not loose rules)
    groups = client.get("/policies/groups", headers=ADMIN).json()["policies"]
    vt = next(g for g in groups if g["id"] == "verified-trade")
    assert vt["kind"] == "compound" and len(vt["rule_ids"]) == 5


def test_resave_drops_stale_rules(client):
    client.post("/policies/compound", json={"draft": _draft(), "source": "org"}, headers=ADMIN)
    # re-save the same policy id but as a simple one-rule policy -> old two rules
    # (audit+gate) must be replaced by the single new rule.
    simple = {"type": "permission", "id": "verified-trade",
              "trigger": {"event": "PreToolUse", "matcher": "Bash"},
              "permission": "deny", "pattern": "Bash(curl:*)"}
    r = client.post("/policies/compound", json={"draft": simple, "source": "org"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["rule_ids"] == ["verified-trade"]
    # the old member rules are gone
    assert client.get("/policies/verified-trade-audit", headers=ADMIN).status_code == 404


def test_delete_cascades_to_member_rules(client):
    client.post("/policies/compound", json={"draft": _draft(), "source": "org"}, headers=ADMIN)
    d = client.delete("/policies/groups/verified-trade", headers=ADMIN)
    assert d.status_code == 200
    assert "verified-trade-audit" in d.json()["rule_ids"] and len(d.json()["rule_ids"]) == 5
    assert client.get("/policies/verified-trade-audit", headers=ADMIN).status_code == 404


def test_unknown_compound_type_is_400(client):
    r = client.post("/policies/compound", json={"draft": {"id": "x", "type": "nope"}, "source": "org"}, headers=ADMIN)
    assert r.status_code == 400


def test_invalid_member_is_atomic_400_nothing_saved(client):
    bad = _draft(gate={"event": "PreToolUse", "matcher": ""})
    r = client.post("/policies/compound", json={"draft": bad, "source": "org"}, headers=ADMIN)
    assert r.status_code == 400
    assert client.get("/policies/verified-trade-audit", headers=ADMIN).status_code == 404
    assert client.get("/policies/verified-trade-gate", headers=ADMIN).status_code == 404


def test_cross_ownership_rejected_409(client):
    # policy A owns verified-trade-audit (via a simple draft with that id).
    a = {"type": "permission", "id": "verified-trade-audit",
         "trigger": {"event": "PreToolUse", "matcher": "Bash"},
         "permission": "deny", "pattern": "Bash(rm:*)"}
    assert client.post("/policies/compound", json={"draft": a, "source": "org"}, headers=ADMIN).status_code == 200
    # policy B (the compound) tries to also own verified-trade-audit -> 409.
    r = client.post("/policies/compound", json={"draft": _draft(), "source": "org"}, headers=ADMIN)
    assert r.status_code == 409, r.text


def test_bad_policy_id_rejected(client):
    bad = _draft(id="has spaces!!")
    r = client.post("/policies/compound", json={"draft": bad, "source": "org"}, headers=ADMIN)
    assert r.status_code == 400
