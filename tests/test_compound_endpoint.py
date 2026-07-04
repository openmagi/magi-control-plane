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
                     policy_store_path=str(tmp_path / "policies.json"),
                     pack_store_path=str(tmp_path / "packs.json"))
    return TestClient(app)


def _draft(**over):
    d = {
        "type": "evidence_gate",
        "id": "verified-trade",
        "description": "Require a credible source before trading",
        "kind": "source_credibility",
        "project_scope": "/home/user/trading-mcp",
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


def test_rule_level_toggle_cascades_to_owning_policy(client):
    # Disabling ONE member rule of a compound cascades to all its rules, so the
    # pair can never be half-toggled.
    client.post("/policies/compound", json={"draft": _draft(), "source": "org"}, headers=ADMIN)
    r = client.patch("/policies/verified-trade-gate/enabled", json={"enabled": False}, headers=ADMIN)
    assert r.status_code == 200
    assert set(r.json()["cascaded_rule_ids"]) >= {"verified-trade-audit", "verified-trade-gate"}
    groups = client.get("/policies/groups", headers=ADMIN).json()["policies"]
    vt = next(g for g in groups if g["id"] == "verified-trade")
    assert vt["enabled"] is False and vt["mixed"] is False


def test_policy_level_toggle_enables_all_rules(client):
    client.post("/policies/compound", json={"draft": _draft(), "source": "org"}, headers=ADMIN)
    client.patch("/policies/verified-trade-gate/enabled", json={"enabled": False}, headers=ADMIN)
    # re-enable at the policy level
    r = client.patch("/policies/groups/verified-trade/enabled", json={"enabled": True}, headers=ADMIN)
    assert r.status_code == 200 and len(r.json()["rule_ids"]) == 5
    groups = client.get("/policies/groups", headers=ADMIN).json()["policies"]
    vt = next(g for g in groups if g["id"] == "verified-trade")
    assert vt["enabled"] is True


def test_free_standing_rule_toggle_is_isolated(client):
    a = {"type": "permission", "id": "lone-rule",
         "trigger": {"event": "PreToolUse", "matcher": "Bash"},
         "permission": "deny", "pattern": "Bash(rm:*)"}
    client.post("/policies/compound", json={"draft": a, "source": "org"}, headers=ADMIN)
    r = client.patch("/policies/lone-rule/enabled", json={"enabled": False}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["cascaded_rule_ids"] == ["lone-rule"]


# ── A1: compound save honors pack_ids (was silently dropped) ───────────

def _make_pack(client, slug="tg"):
    r = client.post("/policy-packs", headers=ADMIN,
                    json={"name": "Trade Guard", "slug": slug, "policy_ids": []})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_compound_joins_requested_pack_by_group_id(client):
    pack_id = _make_pack(client)
    r = client.post("/policies/compound", headers=ADMIN,
                    json={"draft": _draft(), "source": "org",
                          "pack_ids": [pack_id]})
    assert r.status_code == 200, r.text
    assert r.json()["pack_ids"] == [pack_id]
    # The pack stores the POLICY-GROUP id (the reference), not the rules.
    got = client.get(f"/policy-packs/{pack_id}", headers=ADMIN).json()
    # members are expanded to the group's rule ids at read time.
    member_ids = [m["id"] for m in got["members"]]
    assert "verified-trade-audit" in member_ids
    assert "verified-trade-gate" in member_ids


def test_compound_pack_ids_reject_builtin_pack(client):
    r = client.post("/policies/compound", headers=ADMIN,
                    json={"draft": _draft(), "source": "org",
                          "pack_ids": ["pack/coding-safety"]})
    assert r.status_code == 400, r.text


def test_compound_pack_ids_unknown_user_pack_404(client):
    r = client.post("/policies/compound", headers=ADMIN,
                    json={"draft": _draft(), "source": "org",
                          "pack_ids": ["user-pack/nope"]})
    assert r.status_code == 404, r.text


def test_compound_without_pack_ids_is_unchanged(client):
    # Back-compat: omitting pack_ids returns [] and joins nothing.
    r = client.post("/policies/compound", headers=ADMIN,
                    json={"draft": _draft(), "source": "org"})
    assert r.status_code == 200, r.text
    assert r.json()["pack_ids"] == []
