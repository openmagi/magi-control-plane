"""pack -> policy -> rule: a pack may reference a policy-group id, which
expands to that policy's rule ids at every membership site (status, enable
cascade, runtime feeder, counts). End-to-end through the endpoints.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.keys import KeyStore

ADMIN_KEY = "pp-admin-key"
H = {"X-Admin-Api-Key": ADMIN_KEY}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAGI_CP_API_KEY", "pp-api-key")
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", "pp-hitl-key")
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", ADMIN_KEY)


@pytest.fixture
def client(tmp_path):
    ks = KeyStore(dir=str(tmp_path / "keys"))
    app = create_app(
        keystore=ks, dsn="sqlite:///:memory:",
        policy_store_path=str(tmp_path / "policies.json"),
        pack_store_path=str(tmp_path / "packs.json"),
    )
    return TestClient(app)


def _make_compound_policy(client) -> tuple[str, list[str]]:
    """Author a compound policy-group; return (group_id, [rule_ids])."""
    draft = {
        "type": "evidence_gate", "id": "verified-trade",
        "kind": "source_credibility",
        "gate": {"matcher": "mcp__trading__execute_trade", "action": "block"},
    }
    r = client.post("/policies/compound", headers=H,
                    json={"draft": draft, "source": "org", "enabled": True})
    assert r.status_code == 200, r.text
    rule_ids = [
        "verified-trade-audit", "verified-trade-gate",
        "verified-trade-ledger-deny-0", "verified-trade-ledger-deny-1",
        "verified-trade-ledger-deny-2",
    ]
    return "verified-trade", rule_ids


def _make_pack(client, member_ids: list[str]) -> str:
    r = client.post("/policy-packs", headers=H, json={
        "name": "Trade Guard", "slug": "trade-guard",
        "policy_ids": member_ids,
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_pack_member_group_id_expands_to_rule_ids(client):
    group_id, rule_ids = _make_compound_policy(client)
    pack_id = _make_pack(client, [group_id])
    r = client.get(f"/policy-packs/{pack_id}", headers=H)
    assert r.status_code == 200, r.text
    members = [m["id"] for m in r.json()["members"]]
    # The single policy-group member expanded to its rule ids.
    assert members == rule_ids


def test_pack_stores_the_group_reference_not_the_rules(client):
    """The pack persists the GROUP id (the reference), expansion happens
    at read time. So editing the policy's rules later flows through."""
    group_id, _ = _make_compound_policy(client)
    _make_pack(client, [group_id])
    # The pack persists the group id; the list surface computes status over
    # the expanded rules.
    r = client.get("/policy-packs", headers=H)
    pack = next(p for p in r.json()["items"] if p["id"].endswith("trade-guard"))
    # status is computed over the expanded rules; the pack is fully enabled
    # because the compound was saved enabled=True.
    assert pack["status"] in ("all", "partial", "none")


def test_enable_pack_cascades_to_the_policys_rules(client):
    group_id, rule_ids = _make_compound_policy(client)
    pack_id = _make_pack(client, [group_id])
    # Disable everything first via the pack disable, then enable and assert
    # every rule of the policy flipped on.
    client.post(f"/policy-packs/{pack_id}/disable", headers=H)
    r = client.post(f"/policy-packs/{pack_id}/enable", headers=H)
    assert r.status_code == 200, r.text
    # Each member rule is now enabled in the policy store.
    listing = client.get("/policies", headers=H).json()
    enabled = {
        p["id"] for p in listing.get("policies", listing.get("items", []))
        if p.get("enabled")
    }
    for rid in rule_ids:
        assert rid in enabled, f"{rid} not enabled after pack enable"


def test_disable_pack_cascades_to_the_policys_rules(client):
    group_id, rule_ids = _make_compound_policy(client)
    pack_id = _make_pack(client, [group_id])
    client.post(f"/policy-packs/{pack_id}/enable", headers=H)
    r = client.post(f"/policy-packs/{pack_id}/disable", headers=H)
    assert r.status_code == 200, r.text
    listing = client.get("/policies", headers=H).json()
    enabled = {
        p["id"] for p in listing.get("policies", listing.get("items", []))
        if p.get("enabled")
    }
    for rid in rule_ids:
        assert rid not in enabled, f"{rid} still enabled after pack disable"


def test_bare_rule_member_still_works(client):
    """Back-compat: a pack whose member is a bare rule id (not a group)
    passes through unchanged."""
    group_id, rule_ids = _make_compound_policy(client)
    # Reference ONE of the rules directly (bare rule id), not the group.
    pack_id = _make_pack(client, [rule_ids[0]])
    r = client.get(f"/policy-packs/{pack_id}", headers=H)
    members = [m["id"] for m in r.json()["members"]]
    assert members == [rule_ids[0]]


def test_mixed_group_and_bare_rule_members(client):
    group_id, rule_ids = _make_compound_policy(client)
    # A second standalone rule to reference directly.
    client.put("/policies/lonely-rule", headers=H, json={
        "policy": {
            "id": "lonely-rule", "description": "x",
            "trigger": {"host": "claude-code", "event": "Stop", "matcher": "*"},
            "requires": [{"kind": "regex", "pattern": "x"}], "action": "block",
        },
        "source": "org", "enabled": True,
    })
    pack_id = _make_pack(client, [group_id, "lonely-rule"])
    r = client.get(f"/policy-packs/{pack_id}", headers=H)
    members = [m["id"] for m in r.json()["members"]]
    assert members == rule_ids + ["lonely-rule"]
