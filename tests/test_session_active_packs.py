"""P1 pack-centric runtime — session-active-pack endpoints.

Design brief: docs/plans/2026-06-30-pack-centric-session-scoped-runtime.md

Endpoints (all require tenant auth via X-Api-Key):
  - POST /session/{session_id}/packs/activate   {pack_id}
  - POST /session/{session_id}/packs/deactivate {pack_id}
  - GET  /session/{session_id}/packs

Covered here:
  - activate idempotency (double-activate stays a 200 no-op)
  - deactivate refuses the tenant's floor pack (400)
  - GET seeds the floor pack lazily so the envelope always carries it
  - activate extends expires_at to +30d
  - tenant scoping — a tenant cannot see or mutate another tenant's row
  - unknown pack_id is a 404
  - GET refreshes last_seen_at
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.db import (
    SESSION_ACTIVE_PACK_TTL_SECONDS,
    SessionActivePacksRepo,
    make_engine,
)
from magi_cp.cloud.keys import KeyStore
from magi_cp.cloud.tenants import ApiKeyRepo, TenantRepo
from magi_cp.policy.floor_pack import FLOOR_PACK_ID


ADMIN_KEY = "sess-admin-key"
LEGACY_API_KEY = "sess-legacy-api-key"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", ADMIN_KEY)
    monkeypatch.setenv("MAGI_CP_API_KEY", LEGACY_API_KEY)
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", "sess-hitl-key")


@pytest.fixture
def cloud(tmp_path):
    """Build a cloud app pointing at an on-disk sqlite so the app's
    engine and our direct SessionActivePacksRepo share the same DB.
    ``sqlite:///:memory:`` does NOT share state across engine
    instances, which we need for the tenant-scoping test below.
    """
    ks = KeyStore(dir=str(tmp_path / "keys"))
    dsn = f"sqlite:///{tmp_path}/cloud.sqlite"
    app = create_app(
        keystore=ks,
        dsn=dsn,
        policy_store_path=str(tmp_path / "policies.json"),
        pack_store_path=str(tmp_path / "packs.json"),
    )
    client = TestClient(app)

    # Provision two real tenants + one API key each so we can prove
    # tenant scoping. The legacy MAGI_CP_API_KEY still resolves to the
    # synthetic "default" tenant — the two DB tenants use fresh keys.
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
        "dsn": dsn,
        "key_a": issued_a.cleartext,
        "key_b": issued_b.cleartext,
    }


# ── auth / envelope shape ─────────────────────────────────────────────


def test_get_requires_api_key(cloud):
    r = cloud["client"].get("/session/sess_1/packs")
    assert r.status_code == 401


def test_get_seeds_floor_pack_lazily(cloud):
    # First read on a fresh tenant must return the seeded floor id.
    r = cloud["client"].get(
        "/session/sess_1/packs",
        headers={"X-Api-Key": cloud["key_a"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["floor_pack_id"] == FLOOR_PACK_ID
    assert body["active_packs"] == []
    assert body["activated_at"] is None
    assert body["last_seen_at"] is None
    assert body["session_id"] == "sess_1"


# ── activate ──────────────────────────────────────────────────────────


def test_activate_appends_pack_id(cloud):
    r = cloud["client"].post(
        "/session/sess_1/packs/activate",
        headers={"X-Api-Key": cloud["key_a"]},
        json={"pack_id": "pack/research-mode"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["active_packs"] == ["pack/research-mode"]
    assert body["floor_pack_id"] == FLOOR_PACK_ID
    assert body["activated_at"] is not None
    assert body["last_seen_at"] >= body["activated_at"]


def test_activate_is_idempotent(cloud):
    hdr = {"X-Api-Key": cloud["key_a"]}
    r1 = cloud["client"].post(
        "/session/sess_1/packs/activate",
        headers=hdr, json={"pack_id": "pack/research-mode"},
    )
    r2 = cloud["client"].post(
        "/session/sess_1/packs/activate",
        headers=hdr, json={"pack_id": "pack/research-mode"},
    )
    assert r1.status_code == 200 and r2.status_code == 200
    # Second call is a no-op; the list stays length 1.
    assert r2.json()["active_packs"] == ["pack/research-mode"]


def test_activate_multiple_packs_preserves_order(cloud):
    hdr = {"X-Api-Key": cloud["key_a"]}
    cloud["client"].post(
        "/session/sess_1/packs/activate",
        headers=hdr, json={"pack_id": "pack/research-mode"},
    )
    cloud["client"].post(
        "/session/sess_1/packs/activate",
        headers=hdr, json={"pack_id": "pack/coding-safety"},
    )
    r = cloud["client"].get(
        "/session/sess_1/packs", headers=hdr,
    )
    assert r.json()["active_packs"] == [
        "pack/research-mode", "pack/coding-safety",
    ]


def test_activate_unknown_pack_id_is_404(cloud):
    r = cloud["client"].post(
        "/session/sess_1/packs/activate",
        headers={"X-Api-Key": cloud["key_a"]},
        json={"pack_id": "pack/does-not-exist"},
    )
    assert r.status_code == 404


def test_activate_missing_pack_id_is_422(cloud):
    r = cloud["client"].post(
        "/session/sess_1/packs/activate",
        headers={"X-Api-Key": cloud["key_a"]},
        json={},
    )
    assert r.status_code == 422


def test_activate_extends_expires_at_to_30d(cloud):
    before = int(time.time())
    r = cloud["client"].post(
        "/session/sess_1/packs/activate",
        headers={"X-Api-Key": cloud["key_a"]},
        json={"pack_id": "pack/research-mode"},
    )
    assert r.status_code == 200
    # Read expires_at directly through the repo (the wire envelope
    # deliberately does not surface it — it is a GC hint, not an
    # activation-lifetime signal).
    repo = SessionActivePacksRepo(cloud["engine"])
    row = repo.get("sess_1", "tenant-a")
    assert row is not None
    delta = row.expires_at - before
    # 30d ± a small tolerance for slow CI clocks.
    assert SESSION_ACTIVE_PACK_TTL_SECONDS - 5 <= delta \
        <= SESSION_ACTIVE_PACK_TTL_SECONDS + 5


# ── deactivate ────────────────────────────────────────────────────────


def test_deactivate_removes_active_pack(cloud):
    hdr = {"X-Api-Key": cloud["key_a"]}
    cloud["client"].post(
        "/session/sess_1/packs/activate",
        headers=hdr, json={"pack_id": "pack/research-mode"},
    )
    r = cloud["client"].post(
        "/session/sess_1/packs/deactivate",
        headers=hdr, json={"pack_id": "pack/research-mode"},
    )
    assert r.status_code == 200
    assert r.json()["active_packs"] == []


def test_deactivate_absent_pack_is_noop(cloud):
    hdr = {"X-Api-Key": cloud["key_a"]}
    r = cloud["client"].post(
        "/session/sess_1/packs/deactivate",
        headers=hdr, json={"pack_id": "pack/coding-safety"},
    )
    assert r.status_code == 200
    assert r.json()["active_packs"] == []


def test_deactivate_refuses_floor_pack(cloud):
    hdr = {"X-Api-Key": cloud["key_a"]}
    # Seed the floor pack first so ensure_floor_pack has run.
    cloud["client"].get("/session/sess_1/packs", headers=hdr)
    r = cloud["client"].post(
        "/session/sess_1/packs/deactivate",
        headers=hdr, json={"pack_id": FLOOR_PACK_ID},
    )
    assert r.status_code == 400, r.text
    body = r.json()
    # FastAPI wraps the dict in `detail` — the important thing is that
    # the floor-lock message names the pack so the operator sees why.
    detail = body.get("detail", body)
    if isinstance(detail, dict):
        assert detail.get("error") == "floor_pack_locked"
        assert detail.get("floor_pack_id") == FLOOR_PACK_ID
    else:  # pragma: no cover — string fallback
        assert "floor" in str(detail).lower()


# ── tenant scoping ────────────────────────────────────────────────────


def test_tenant_cannot_see_another_tenants_active_packs(cloud):
    # Tenant A activates a pack on session s1.
    cloud["client"].post(
        "/session/s1/packs/activate",
        headers={"X-Api-Key": cloud["key_a"]},
        json={"pack_id": "pack/research-mode"},
    )
    # Tenant B reads the SAME session id — must see an empty list
    # because activate keyed on (session_id, tenant_id).
    r = cloud["client"].get(
        "/session/s1/packs",
        headers={"X-Api-Key": cloud["key_b"]},
    )
    assert r.status_code == 200
    assert r.json()["active_packs"] == []


def test_tenant_scoping_on_deactivate(cloud):
    # Tenant A activates; tenant B deactivates the SAME pack on the
    # same session id. B's deactivate must NOT touch A's row.
    cloud["client"].post(
        "/session/s1/packs/activate",
        headers={"X-Api-Key": cloud["key_a"]},
        json={"pack_id": "pack/research-mode"},
    )
    cloud["client"].post(
        "/session/s1/packs/deactivate",
        headers={"X-Api-Key": cloud["key_b"]},
        json={"pack_id": "pack/research-mode"},
    )
    # A's list must be unchanged.
    r = cloud["client"].get(
        "/session/s1/packs",
        headers={"X-Api-Key": cloud["key_a"]},
    )
    assert r.json()["active_packs"] == ["pack/research-mode"]


# ── last_seen_at semantics ────────────────────────────────────────────


def test_get_refreshes_last_seen_at(cloud):
    hdr = {"X-Api-Key": cloud["key_a"]}
    cloud["client"].post(
        "/session/sess_1/packs/activate",
        headers=hdr, json={"pack_id": "pack/research-mode"},
    )
    repo = SessionActivePacksRepo(cloud["engine"])
    first = repo.get("sess_1", "tenant-a")
    first_last_seen = first.last_seen_at
    # Sleep a beat so the clock ticks; SQLite is millisecond-agnostic
    # on integers so we need >=1s to see the bump.
    time.sleep(1.1)
    r = cloud["client"].get("/session/sess_1/packs", headers=hdr)
    assert r.status_code == 200
    later = repo.get("sess_1", "tenant-a")
    assert later.last_seen_at >= first_last_seen + 1


def test_get_missing_row_still_returns_envelope(cloud):
    hdr = {"X-Api-Key": cloud["key_a"]}
    r = cloud["client"].get("/session/fresh/packs", headers=hdr)
    assert r.status_code == 200
    body = r.json()
    assert body["active_packs"] == []
    assert body["floor_pack_id"] == FLOOR_PACK_ID
    assert body["session_id"] == "fresh"
