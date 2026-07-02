"""PR-C / AUTH-2: admin key revoke is scoped to the tenant in the path.

admin_revoke_key previously ignored its path tenant_id and called
repo.revoke(key_id) on a sequential autoincrement id, so a valid admin
signature could revoke ANY tenant's key by guessing the id. These tests pin
the ownership check.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import tempfile
import time

import pytest
from fastapi.testclient import TestClient


HMAC_SECRET = "test-hmac-secret-for-admin-api"


def _sign(method: str, path: str, ts: str, body: bytes) -> str:
    signing = (
        method.encode("utf-8") + b"\n"
        + path.encode("utf-8") + b"\n"
        + ts.encode("utf-8") + b"\n"
        + body
    )
    return hmac.new(HMAC_SECRET.encode("utf-8"), signing, hashlib.sha256).hexdigest()


def _tmp_store() -> str:
    f = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    f.write("[]")
    f.close()
    return f.name


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAGI_CP_ADMIN_HMAC_SECRET", HMAC_SECRET)


def _client() -> TestClient:
    from magi_cp.cloud.app import create_app
    app = create_app(dsn="sqlite:///:memory:", policy_store_path=_tmp_store())
    return TestClient(app)


def _post(c: TestClient, path: str, payload: dict):
    body = json.dumps(payload).encode("utf-8")
    ts = str(int(time.time()))
    return c.post(path, headers={
        "X-Magi-Signature": _sign("POST", path, ts, body),
        "X-Magi-Timestamp": ts,
        "Content-Type": "application/json",
    }, content=body)


def _provision(c: TestClient, tenant_id: str) -> tuple[int, str]:
    """Create a tenant + issue a key. Return (key_id, cleartext)."""
    r = _post(c, "/admin/tenants", {"tenant_id": tenant_id, "plan": "pro"})
    assert r.status_code == 200
    r = _post(c, f"/admin/tenants/{tenant_id}/keys", {})
    assert r.status_code == 200
    j = r.json()
    return j["id"], j["api_key"]


def test_cross_tenant_revoke_is_rejected_and_key_still_works():
    c = _client()
    a_key_id, a_key = _provision(c, "tenant_a")
    _provision(c, "tenant_b")

    # Attempt to revoke tenant A's key via tenant B's path.
    r = _post(c, f"/admin/tenants/tenant_b/keys/{a_key_id}/revoke", {})
    assert r.status_code == 404

    # A's key must still authenticate (it was NOT revoked).
    me = c.get("/tenants/me", headers={"X-Api-Key": a_key})
    assert me.status_code == 200
    assert me.json()["id"] == "tenant_a"


def test_owner_revoke_succeeds_and_key_stops_working():
    c = _client()
    a_key_id, a_key = _provision(c, "tenant_a")

    r = _post(c, f"/admin/tenants/tenant_a/keys/{a_key_id}/revoke", {})
    assert r.status_code == 200
    assert r.json()["revoked"] is True

    me = c.get("/tenants/me", headers={"X-Api-Key": a_key})
    assert me.status_code == 401
