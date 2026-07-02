"""PR-C / AUTH-2: admin HMAC is bound to method + path + timestamp + body.

Body-only signing let a captured empty-body signature be replayed across the
revoke / reactivate / issue-key routes and had no freshness. These tests pin
the hardened contract: timestamp window + path binding.
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


def _post(c: TestClient, path: str, payload: dict, *, ts: str | None = None,
          sig_path: str | None = None):
    body = json.dumps(payload).encode("utf-8")
    ts = ts if ts is not None else str(int(time.time()))
    sig = _sign("POST", sig_path or path, ts, body)
    return c.post(path, headers={
        "X-Magi-Signature": sig,
        "X-Magi-Timestamp": ts,
        "Content-Type": "application/json",
    }, content=body)


def test_valid_timestamped_signature_accepted():
    c = _client()
    r = _post(c, "/admin/tenants", {"tenant_id": "user_ok", "plan": "pro"})
    assert r.status_code == 200


def test_missing_timestamp_401():
    c = _client()
    body = json.dumps({"tenant_id": "user_x"}).encode("utf-8")
    # A body-only signature with no timestamp header (the old scheme).
    sig = hmac.new(HMAC_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    r = c.post("/admin/tenants", headers={
        "X-Magi-Signature": sig, "Content-Type": "application/json",
    }, content=body)
    assert r.status_code == 401


def test_timestamp_skew_rejected():
    c = _client()
    stale = str(int(time.time()) - 600)   # 10 min in the past, > 300s window
    r = _post(c, "/admin/tenants", {"tenant_id": "user_stale"}, ts=stale)
    assert r.status_code == 401


def test_signature_bound_to_path_cannot_be_replayed_cross_route():
    c = _client()
    # A signature computed for one path must not authenticate a different path.
    body = json.dumps({"reason": "x"}).encode("utf-8")
    ts = str(int(time.time()))
    sig_for_a = _sign("POST", "/admin/tenants/A/suspend", ts, body)
    # Present that signature on tenant B's suspend route.
    r = c.post("/admin/tenants/B/suspend", headers={
        "X-Magi-Signature": sig_for_a,
        "X-Magi-Timestamp": ts,
        "Content-Type": "application/json",
    }, content=body)
    assert r.status_code == 401


def test_empty_body_signature_not_reusable_across_tenants():
    c = _client()
    # reactivate takes an effectively empty body; the old scheme signed the
    # constant HMAC(secret, b"") so one capture worked on every such route.
    # With path binding, a signature minted for tenant A's reactivate must not
    # authenticate tenant B's reactivate.
    body = b"{}"
    ts = str(int(time.time()))
    sig_a = _sign("POST", "/admin/tenants/A/reactivate", ts, body)
    r = c.post("/admin/tenants/B/reactivate", headers={
        "X-Magi-Signature": sig_a,
        "X-Magi-Timestamp": ts,
        "Content-Type": "application/json",
    }, content=body)
    assert r.status_code == 401
