"""v2.0-W6a — Admin API for tenant/key management.

Used by clawy's Stripe webhook to (a) create a tenant on subscription start,
(b) suspend on payment failure, (c) issue API keys for the user's dashboard
"create key" button.

Auth: HMAC-SHA256 over the raw request body, presented as `X-Magi-Signature`.
The shared secret lives in `MAGI_CP_ADMIN_HMAC_SECRET` env var on both sides
(clawy's Vercel env + control-plane env). Constant-time compare. No bearer
token — webhooks fire from many IPs, HMAC is the safer surface.
"""
import hashlib
import hmac
import json
import tempfile

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
    mac = hmac.new(HMAC_SECRET.encode("utf-8"), signing, hashlib.sha256)
    return mac.hexdigest()


def _tmp_store():
    f = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    f.write("[]")
    f.close()
    return f.name


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAGI_CP_ADMIN_HMAC_SECRET", HMAC_SECRET)


def _client():
    from magi_cp.cloud.app import create_app
    app = create_app(
        dsn="sqlite:///:memory:",
        policy_store_path=_tmp_store(),
    )
    return TestClient(app)


def _post(c: TestClient, path: str, payload: dict) -> "TestClient":
    import time as _time
    body = json.dumps(payload).encode("utf-8")
    ts = str(int(_time.time()))
    return c.post(path, headers={
        "X-Magi-Signature": _sign("POST", path, ts, body),
        "X-Magi-Timestamp": ts,
        "Content-Type": "application/json",
    }, content=body)


# ── auth ───────────────────────────────────────────────────────────
class TestAuth:
    def test_missing_signature_401(self):
        c = _client()
        r = c.post("/admin/tenants",
                   json={"tenant_id": "user_x", "plan": "pro"})
        assert r.status_code == 401

    def test_wrong_signature_401(self):
        c = _client()
        body = json.dumps({"tenant_id": "user_x", "plan": "pro"}).encode()
        r = c.post("/admin/tenants",
                   headers={"X-Magi-Signature": "wrong",
                            "Content-Type": "application/json"},
                   content=body)
        assert r.status_code == 401

    def test_503_when_hmac_secret_unset(self, monkeypatch):
        monkeypatch.delenv("MAGI_CP_ADMIN_HMAC_SECRET")
        c = _client()
        body = json.dumps({"tenant_id": "user_x", "plan": "pro"}).encode()
        r = c.post("/admin/tenants",
                   headers={"X-Magi-Signature": "any",
                            "Content-Type": "application/json"},
                   content=body)
        assert r.status_code == 503


# ── create tenant ──────────────────────────────────────────────────
class TestCreateTenant:
    def test_create_returns_tenant_record(self):
        c = _client()
        r = _post(c, "/admin/tenants",
                  {"tenant_id": "user_alice", "plan": "pro"})
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "user_alice"
        assert body["status"] == "active"
        assert body["plan"] == "pro"

    def test_create_is_idempotent_on_duplicate_returns_existing(self):
        c = _client()
        _post(c, "/admin/tenants",
              {"tenant_id": "user_alice", "plan": "pro"})
        r = _post(c, "/admin/tenants",
                  {"tenant_id": "user_alice", "plan": "starter"})
        # Idempotent on existing tenant — returns 200, no error, no plan change
        assert r.status_code == 200
        assert r.json()["plan"] == "pro"


# ── suspend / reactivate ───────────────────────────────────────────
class TestStatusEndpoints:
    def test_suspend_then_get(self):
        c = _client()
        _post(c, "/admin/tenants",
              {"tenant_id": "user_alice", "plan": "pro"})
        r = _post(c, "/admin/tenants/user_alice/suspend",
                  {"reason": "payment_failed"})
        assert r.status_code == 200
        assert r.json()["status"] == "suspended"

    def test_reactivate(self):
        c = _client()
        _post(c, "/admin/tenants",
              {"tenant_id": "user_alice", "plan": "pro"})
        _post(c, "/admin/tenants/user_alice/suspend",
              {"reason": "payment_failed"})
        r = _post(c, "/admin/tenants/user_alice/reactivate", {})
        assert r.status_code == 200
        assert r.json()["status"] == "active"


# ── issue key ──────────────────────────────────────────────────────
class TestIssueKey:
    def test_issue_returns_cleartext_once(self):
        c = _client()
        _post(c, "/admin/tenants",
              {"tenant_id": "user_alice", "plan": "pro"})
        r = _post(c, "/admin/tenants/user_alice/keys", {})
        assert r.status_code == 200
        body = r.json()
        assert body["api_key"].startswith("mcp_")
        assert body["prefix"] == body["api_key"][:8]
        assert body["tenant_id"] == "user_alice"

    def test_issued_key_authenticates_against_verify_dispatch(self):
        """End-to-end: issue a key, then use it on /verify/{step}."""
        c = _client()
        _post(c, "/admin/tenants",
              {"tenant_id": "user_alice", "plan": "pro"})
        issued = _post(c, "/admin/tenants/user_alice/keys", {}).json()
        # /verify/{step} returns 200 with this DB-issued key (no MAGI_CP_API_KEY env)
        r = c.post("/verify/privilege_scan",
                   headers={"X-Api-Key": issued["api_key"]},
                   json={"payload": {"text": "clean text"}})
        # No verifier_registry passed to the test client — expect 503.
        # (We don't wire registry here because we're only testing key auth.)
        assert r.status_code in (200, 503)
        assert r.status_code != 401   # auth passed

    def test_issue_key_for_missing_tenant_404(self):
        c = _client()
        r = _post(c, "/admin/tenants/ghost/keys", {})
        assert r.status_code == 404


# ── revoke key ──────────────────────────────────────────────────────
class TestRevokeKey:
    def test_revoke_then_key_no_longer_authenticates(self):
        c = _client()
        _post(c, "/admin/tenants",
              {"tenant_id": "user_alice", "plan": "pro"})
        issued = _post(c, "/admin/tenants/user_alice/keys", {}).json()
        # Revoke
        r = _post(c, f"/admin/tenants/user_alice/keys/{issued['id']}/revoke", {})
        assert r.status_code == 200
        # Now the key cannot authenticate
        r = c.post("/verify/privilege_scan",
                   headers={"X-Api-Key": issued["api_key"]},
                   json={"payload": {"text": "clean"}})
        assert r.status_code == 401
