"""run-share API: POST /v1/runs/share (authed) + GET /share/run/{token} (public)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.db import SharedRunRepo, make_engine
from magi_cp.cloud.keys import KeyStore

API_KEY = "share-api"
HEADERS = {"X-Api-Key": API_KEY}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAGI_CP_API_KEY", API_KEY)
    monkeypatch.setenv("MAGI_CP_SHARE_BASE_URL", "https://cloud.test")


@pytest.fixture
def client(tmp_path):
    ks = KeyStore(dir=str(tmp_path / "keys"))
    app = create_app(keystore=ks, dsn="sqlite:///:memory:",
                     policy_store_path=str(tmp_path / "policies.json"))
    return TestClient(app)


def _view(goal="do the thing"):
    return {
        "schemaVersion": "openmagi.runView.v1",
        "sessionId": "s1",
        "summary": {"goal": goal, "result": "done", "model": "claude-opus-4-8",
                    "status": "completed", "usage": {"inputTokens": 5, "outputTokens": 2}},
        "trace": [{"name": "Bash", "status": "ok", "activityType": "ToolCall"}],
        "governance": [],
        "counts": {"stepCount": 1},
    }


# --- repo unit tests ---
def test_repo_create_then_get_active(tmp_path):
    repo = SharedRunRepo(make_engine("sqlite:///:memory:"))
    # in-memory engine is per-connection; reuse the SAME repo/engine
    from magi_cp.cloud.db import init_schema
    init_schema(repo.engine)
    token = repo.create(tenant_id="t1", view=_view())
    row = repo.get_active(token)
    assert row is not None and row.tenant_id == "t1"
    assert row.view["sessionId"] == "s1"


def test_repo_get_missing_returns_none(tmp_path):
    repo = SharedRunRepo(make_engine("sqlite:///:memory:"))
    from magi_cp.cloud.db import init_schema
    init_schema(repo.engine)
    assert repo.get_active("nope") is None


def test_repo_expired_returns_none(monkeypatch):
    from magi_cp.cloud import db
    repo = SharedRunRepo(make_engine("sqlite:///:memory:"))
    db.init_schema(repo.engine)
    token = repo.create(tenant_id="t1", view=_view(), ttl_seconds=1)
    # jump past expiry
    real = db.time.time
    monkeypatch.setattr(db.time, "time", lambda: real() + 10)
    assert repo.get_active(token) is None


def test_repo_revoke(tmp_path):
    from magi_cp.cloud.db import init_schema
    repo = SharedRunRepo(make_engine("sqlite:///:memory:"))
    init_schema(repo.engine)
    token = repo.create(tenant_id="t1", view=_view())
    assert repo.revoke(token) is True
    assert repo.get_active(token) is None
    assert repo.revoke(token) is False  # already revoked


# --- endpoint integration ---
def test_share_requires_auth(client):
    r = client.post("/v1/runs/share", json={"view": _view()})
    assert r.status_code == 401


def test_share_create_and_public_get_roundtrip(client):
    r = client.post("/v1/runs/share", json={"view": _view()}, headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["url"].startswith("https://cloud.test/r/")
    token = body["token"]

    # public GET, no auth
    g = client.get(f"/share/run/{token}")
    assert g.status_code == 200
    view = g.json()["view"]
    assert view["summary"]["goal"] == "do the thing"


def test_share_rejects_wrong_schema(client):
    r = client.post("/v1/runs/share", json={"view": {"schemaVersion": "nope"}}, headers=HEADERS)
    assert r.status_code == 400


def test_share_rejects_missing_view(client):
    r = client.post("/v1/runs/share", json={}, headers=HEADERS)
    assert r.status_code == 400


def test_share_re_scrubs_on_ingest(client):
    # Server must redact even if the client uploads an un-redacted view.
    token_secret = "ghp_" + "A" * 36
    v = _view(goal=f"deploy {token_secret}")
    r = client.post("/v1/runs/share", json={"view": v}, headers=HEADERS)
    token = r.json()["token"]
    view = client.get(f"/share/run/{token}").json()["view"]
    assert token_secret not in view["summary"]["goal"]


def test_public_get_missing_token_404(client):
    assert client.get("/share/run/doesnotexist").status_code == 404


def test_session_id_is_scrubbed(client):
    secret = "ghp_" + "B" * 36
    v = _view()
    v["sessionId"] = f"sess {secret} /Users/kevin/.ssh/id_rsa"
    r = client.post("/v1/runs/share", json={"view": v}, headers=HEADERS)
    view = client.get(f"/share/run/{r.json()['token']}").json()["view"]
    assert secret not in view["sessionId"]
    assert "id_rsa" not in view["sessionId"]


def test_non_json_body_is_400_not_500(client):
    r = client.post("/v1/runs/share", content=b"not json{", headers=HEADERS)
    assert r.status_code == 400
