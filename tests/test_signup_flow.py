"""v2.1-D2 — public alpha-signup intake + /tenants/me + /admin/signups."""
import hashlib
import hmac
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient


HMAC_SECRET = "test-hmac-secret-for-admin-api"


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("MAGI_CP_API_KEY", "test-api-key")
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", "test-hitl-key")
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setenv("MAGI_CP_ADMIN_HMAC_SECRET", HMAC_SECRET)


def _tmp_store():
    f = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    f.write("[]"); f.close()
    return f.name


def _client():
    from magi_cp.cloud.app import create_app
    app = create_app(dsn="sqlite:///:memory:", policy_store_path=_tmp_store())
    return TestClient(app)


def _sign(body: bytes) -> str:
    return hmac.new(HMAC_SECRET.encode(), body, hashlib.sha256).hexdigest()


# ── POST /signup ────────────────────────────────────────────────────
class TestSignup:
    def test_signup_with_minimal_email(self):
        c = _client()
        r = c.post("/signup", json={"email": "lawyer@firm.example.com"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body["id"], int)
        assert body["status"] == "pending"

    def test_signup_with_full_fields(self):
        c = _client()
        r = c.post("/signup", json={
            "email": "kevin@example.com",
            "firm": "Acme & Acme LLC",
            "role": "managing-partner",
            "use_case": "Block fund-transfer bash without partner approval.",
            "referrer": "twitter",
        })
        assert r.status_code == 200
        # Admin can now list it
        r2 = c.get("/admin/signups",
                   headers={"X-Admin-Api-Key": "test-admin-key"})
        items = r2.json()["items"]
        assert any(it["firm"] == "Acme & Acme LLC" for it in items)

    def test_signup_rejects_invalid_email(self):
        c = _client()
        r = c.post("/signup", json={"email": "not-an-email"})
        assert r.status_code == 422

    def test_signup_rate_limited_per_ip(self):
        c = _client()
        for i in range(3):
            r = c.post("/signup", json={"email": f"x{i}@example.com"})
            assert r.status_code == 200
        # 4th from same IP (TestClient uses localhost) is throttled
        r = c.post("/signup", json={"email": "x4@example.com"})
        assert r.status_code == 429

    def test_signup_normalises_email_case(self):
        c = _client()
        c.post("/signup", json={"email": "MiXeD@Example.COM"})
        rows = c.get("/admin/signups",
                     headers={"X-Admin-Api-Key": "test-admin-key"}).json()["items"]
        assert any(it["email"] == "mixed@example.com" for it in rows)


# ── /admin/signups requires admin key ────────────────────────────
def test_admin_signups_requires_admin_key():
    c = _client()
    r = c.get("/admin/signups")
    assert r.status_code == 401

    r2 = c.get("/admin/signups", headers={"X-Admin-Api-Key": "wrong"})
    assert r2.status_code == 401


# ── /admin/signups/{id}/status ────────────────────────────────────
def test_admin_can_change_signup_status():
    c = _client()
    r = c.post("/signup", json={"email": "a@b.com"})
    sid = r.json()["id"]
    r2 = c.post(
        f"/admin/signups/{sid}/status?status=approved&notes=provisioned",
        headers={"X-Admin-Api-Key": "test-admin-key"},
    )
    assert r2.status_code == 200
    rows = c.get("/admin/signups",
                 headers={"X-Admin-Api-Key": "test-admin-key"}).json()["items"]
    matched = [it for it in rows if it["id"] == sid][0]
    assert matched["status"] == "approved"
    assert matched["notes"] == "provisioned"


def test_admin_status_change_404_for_missing_id():
    c = _client()
    r = c.post("/admin/signups/9999/status?status=rejected",
               headers={"X-Admin-Api-Key": "test-admin-key"})
    assert r.status_code == 404


# ── /tenants/me ───────────────────────────────────────────────────
class TestTenantsMe:
    def _provision_tenant(self, c, tid):
        body = json.dumps({"tenant_id": tid, "plan": "free"}).encode()
        c.post("/admin/tenants",
               headers={"X-Magi-Signature": _sign(body),
                        "Content-Type": "application/json"},
               content=body)
        body2 = b"{}"
        sig2 = _sign(body2)
        r = c.post(f"/admin/tenants/{tid}/keys",
                   headers={"X-Magi-Signature": sig2,
                            "Content-Type": "application/json"},
                   content=body2)
        return r.json()["api_key"]

    def test_env_key_returns_default_synthetic_tenant(self):
        c = _client()
        r = c.get("/tenants/me", headers={"X-Api-Key": "test-api-key"})
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "default"
        assert body["status"] == "active"
        assert body["synthetic"] is True

    def test_db_key_returns_real_tenant(self):
        c = _client()
        key = self._provision_tenant(c, "user_pilot_1")
        r = c.get("/tenants/me", headers={"X-Api-Key": key})
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "user_pilot_1"
        assert body["status"] == "active"
        assert body["plan"] == "free"
        assert body["synthetic"] is False

    def test_tenants_me_requires_api_key(self):
        c = _client()
        r = c.get("/tenants/me")
        assert r.status_code == 401
