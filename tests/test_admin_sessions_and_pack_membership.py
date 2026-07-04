"""P4 pack-centric dashboard feeders.

Design brief: 2026-06-30-pack-centric-session-scoped-runtime (private planning repo)

Covered here:
  - GET /admin/sessions lists a tenant's recent sessions + active packs,
    most-recently-seen first, admin-key gated.
  - GET /admin/sessions requires the admin key.
  - SessionActivePacksRepo.list_by_tenant orders by last_seen_at desc
    and scopes to the tenant.
  - PUT /policies with pack_ids appends the saved policy id to each
    selected user pack (including the floor pack) in one transaction.
  - PUT /policies with a built-in pack/... id is a 400 (immutable
    membership); an omitted pack_ids is an orphan (no membership).
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.db import SessionActivePacksRepo
from magi_cp.cloud.keys import KeyStore
from magi_cp.cloud.tenants import ApiKeyRepo, TenantRepo
from magi_cp.policy.floor_pack import FLOOR_PACK_ID


ADMIN_KEY = "admin-sessions-key"
LEGACY_API_KEY = "admin-sessions-legacy-key"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", ADMIN_KEY)
    monkeypatch.setenv("MAGI_CP_API_KEY", LEGACY_API_KEY)
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", "admin-sessions-hitl-key")


@pytest.fixture
def cloud(tmp_path):
    ks = KeyStore(dir=str(tmp_path / "keys"))
    dsn = f"sqlite:///{tmp_path}/cloud.sqlite"
    app = create_app(
        keystore=ks,
        dsn=dsn,
        policy_store_path=str(tmp_path / "policies.json"),
        pack_store_path=str(tmp_path / "packs.json"),
    )
    client = TestClient(app)
    engine = app.state.engine
    tenants = TenantRepo(engine)
    keys = ApiKeyRepo(engine)
    tenants.create(tenant_id="tenant-a")
    tenants.create(tenant_id="tenant-b")
    issued_a = keys.issue(tenant_id="tenant-a")
    issued_b = keys.issue(tenant_id="tenant-b")
    return {
        "app": app,
        "client": client,
        "engine": engine,
        "key_a": issued_a.cleartext,
        "key_b": issued_b.cleartext,
    }


# ── GET /admin/sessions ───────────────────────────────────────────────


def test_admin_sessions_requires_admin_key(cloud):
    r = cloud["client"].get("/admin/sessions")
    assert r.status_code == 401


def test_admin_sessions_lists_active_packs_for_tenant(cloud):
    # Seed two sessions for tenant-a via the activate endpoint.
    hdr = {"X-Api-Key": cloud["key_a"]}
    cloud["client"].post(
        "/session/sess_older/packs/activate",
        headers=hdr, json={"pack_id": "pack/research-mode"},
    )
    time.sleep(1.05)
    cloud["client"].post(
        "/session/sess_newer/packs/activate",
        headers=hdr, json={"pack_id": "pack/coding-safety"},
    )

    r = cloud["client"].get(
        "/admin/sessions?tenant_id=tenant-a",
        headers={"X-Admin-Api-Key": ADMIN_KEY},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tenant_id"] == "tenant-a"
    ids = [row["session_id"] for row in body["items"]]
    # Most-recently-seen first.
    assert ids == ["sess_newer", "sess_older"]
    newer = body["items"][0]
    assert newer["active_packs"] == ["pack/coding-safety"]
    assert newer["floor_pack_id"] == FLOOR_PACK_ID
    assert newer["last_seen_at"] >= newer["activated_at"]


def test_admin_sessions_scopes_by_tenant(cloud):
    cloud["client"].post(
        "/session/sess_a/packs/activate",
        headers={"X-Api-Key": cloud["key_a"]},
        json={"pack_id": "pack/research-mode"},
    )
    cloud["client"].post(
        "/session/sess_b/packs/activate",
        headers={"X-Api-Key": cloud["key_b"]},
        json={"pack_id": "pack/research-mode"},
    )
    r = cloud["client"].get(
        "/admin/sessions?tenant_id=tenant-a",
        headers={"X-Admin-Api-Key": ADMIN_KEY},
    )
    ids = [row["session_id"] for row in r.json()["items"]]
    assert ids == ["sess_a"]
    assert "sess_b" not in ids


def test_list_by_tenant_orders_desc(cloud):
    repo = SessionActivePacksRepo(cloud["engine"])
    repo.activate("s1", "tenant-a", "pack/research-mode")
    time.sleep(1.05)
    repo.activate("s2", "tenant-a", "pack/coding-safety")
    rows = repo.list_by_tenant("tenant-a")
    assert [r.session_id for r in rows] == ["s2", "s1"]


# ── PUT /policies pack_ids membership ─────────────────────────────────


def _minimal_evidence_policy(policy_id: str) -> dict:
    """A minimal EvidencePolicy IR that saves cleanly (regex requires
    resolve to 'enforcing' without needing a registered verifier)."""
    return {
        "id": policy_id,
        "type": "evidence",
        "trigger": {"event": "PostToolUse", "matcher": "Write"},
        "requires": [
            {
                "kind": "regex",
                "pattern": "TODO",
                "flags": "",
            }
        ],
        "action": "audit",
    }


def _put_policy(cloud, policy_id, *, pack_ids=None):
    body: dict = {
        "policy": _minimal_evidence_policy(policy_id),
        "source": "user",
        "enabled": True,
    }
    if pack_ids is not None:
        body["pack_ids"] = pack_ids
    return cloud["client"].put(
        f"/policies/{policy_id}",
        headers={"X-Admin-Api-Key": ADMIN_KEY},
        json=body,
    )


def _create_user_pack(cloud, name):
    return cloud["client"].post(
        "/policy-packs",
        headers={"X-Admin-Api-Key": ADMIN_KEY},
        json={"name": name, "policy_ids": []},
    )


def test_put_policy_without_pack_ids_is_orphan(cloud):
    r = _put_policy(cloud, "user/orphan-1")
    assert r.status_code == 200, r.text
    assert r.json().get("pack_ids") == []


def test_put_policy_joins_selected_user_pack(cloud):
    pack = _create_user_pack(cloud, "Research Mode")
    assert pack.status_code == 200, pack.text
    pack_id = pack.json()["id"]

    r = _put_policy(cloud, "user/joined-1", pack_ids=[pack_id])
    assert r.status_code == 200, r.text
    assert r.json()["pack_ids"] == [pack_id]

    # The pack now lists the policy as a member.
    detail = cloud["client"].get(
        f"/policy-packs/{pack_id}",
        headers={"X-Admin-Api-Key": ADMIN_KEY},
    )
    assert "user/joined-1" in detail.json()["policy_ids"]


def test_put_policy_rejects_builtin_pack(cloud):
    r = _put_policy(
        cloud, "user/reject-1", pack_ids=["pack/research-mode"],
    )
    assert r.status_code == 400, r.text


def test_put_policy_pack_join_is_idempotent(cloud):
    pack = _create_user_pack(cloud, "Idem Pack")
    pack_id = pack.json()["id"]
    _put_policy(cloud, "user/idem-1", pack_ids=[pack_id])
    _put_policy(cloud, "user/idem-1", pack_ids=[pack_id])
    detail = cloud["client"].get(
        f"/policy-packs/{pack_id}",
        headers={"X-Admin-Api-Key": ADMIN_KEY},
    )
    members = detail.json()["policy_ids"]
    assert members.count("user/idem-1") == 1
