"""PR-F: run-share TTL + GC, owner-edit coverage, /metrics token gate.

- SHARE-1: default share TTL is 30 days (opt out with =0); purge_expired GCs
  revoked/expired rows.
- SHARE-2: apply_share_edits redacts owner terms in top-level trace/results too.
- OBS-1: /metrics requires the bearer token when MAGI_CP_METRICS_TOKEN is set.
"""
from __future__ import annotations

import tempfile

from magi_cp.cloud.db import SharedRunRepo, make_engine, init_schema
from magi_cp.share.edits import apply_share_edits, REDACTION_PLACEHOLDER


def _mem():
    e = make_engine("sqlite:///:memory:")
    init_schema(e)
    return e


# ── SHARE-1: default TTL ─────────────────────────────────────────────
def test_share_default_ttl_is_30_days(monkeypatch):
    from fastapi.testclient import TestClient
    from magi_cp.cloud.app import create_app

    monkeypatch.setenv("MAGI_CP_API_KEY", "k")
    monkeypatch.delenv("MAGI_CP_SHARE_TTL_SECONDS", raising=False)
    store = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    store.write("[]")
    store.close()
    app = create_app(dsn="sqlite:///:memory:", policy_store_path=store.name)
    c = TestClient(app)
    r = c.post("/v1/runs/share", headers={"X-Api-Key": "k"},
               json={"view": {"schemaVersion": "openmagi.runView.v1"}})
    assert r.status_code == 200

    rows = SharedRunRepo(app.state.engine).list_by_tenant("default")
    assert len(rows) == 1
    # ~30 days out (allow a few seconds of clock movement).
    assert rows[0].expires_at is not None
    import time as _t
    delta = rows[0].expires_at - int(_t.time())
    assert 2_592_000 - 60 <= delta <= 2_592_000 + 60


def test_share_ttl_zero_means_no_expiry(monkeypatch):
    from fastapi.testclient import TestClient
    from magi_cp.cloud.app import create_app

    monkeypatch.setenv("MAGI_CP_API_KEY", "k")
    monkeypatch.setenv("MAGI_CP_SHARE_TTL_SECONDS", "0")
    store = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    store.write("[]")
    store.close()
    app = create_app(dsn="sqlite:///:memory:", policy_store_path=store.name)
    c = TestClient(app)
    c.post("/v1/runs/share", headers={"X-Api-Key": "k"},
           json={"view": {"schemaVersion": "openmagi.runView.v1"}})
    rows = SharedRunRepo(app.state.engine).list_by_tenant("default")
    assert rows[0].expires_at is None


# ── SHARE-1: GC ──────────────────────────────────────────────────────
def test_purge_expired_removes_revoked_and_expired_keeps_active():
    e = _mem()
    repo = SharedRunRepo(e)
    import time as _t
    active = repo.create(tenant_id="t", view={"x": 1}, ttl_seconds=3600)
    expired = repo.create(tenant_id="t", view={"x": 2}, ttl_seconds=1)
    revoked = repo.create(tenant_id="t", view={"x": 3}, ttl_seconds=3600)
    repo.revoke(revoked)

    # A `now` past the ttl=1 expiry but well before the ttl=3600 rows'.
    deleted = repo.purge_expired(now=int(_t.time()) + 100)
    assert deleted == 2                        # expired + revoked
    assert repo.get_active(active) is not None  # active survives
    assert repo.get_active(expired) is None
    assert repo.get_active(revoked) is None


# ── SHARE-2: owner edits cover trace/results ─────────────────────────
def test_apply_share_edits_redacts_trace_and_results():
    view = {
        "transcript": [{"kind": "text", "text": "hello SECRETTOKEN"}],
        "trace": [{"command": "curl h?key=SECRETTOKEN"}],
        "results": [{"prUrl": "https://x/SECRETTOKEN"}],
        "governance": [],
        "sources": [],
    }
    out = apply_share_edits(view, {"redactions": ["SECRETTOKEN"]})
    blob = repr(out)
    assert "SECRETTOKEN" not in blob
    assert REDACTION_PLACEHOLDER in repr(out["trace"])
    assert REDACTION_PLACEHOLDER in repr(out["results"])


# ── OBS-1: /metrics token gate ───────────────────────────────────────
def test_metrics_requires_token_when_set(monkeypatch):
    try:
        import prometheus_client  # noqa: F401
    except ImportError:
        import pytest
        pytest.skip("observability extra not installed")

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from magi_cp.cloud.observability import attach_metrics

    monkeypatch.setenv("MAGI_CP_METRICS_TOKEN", "scrape-secret")
    app = FastAPI()
    attach_metrics(app)
    c = TestClient(app)

    assert c.get("/metrics").status_code == 401
    assert c.get("/metrics",
                 headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = c.get("/metrics", headers={"Authorization": "Bearer scrape-secret"})
    assert ok.status_code == 200


def test_metrics_no_auth_when_token_unset(monkeypatch):
    try:
        import prometheus_client  # noqa: F401
    except ImportError:
        import pytest
        pytest.skip("observability extra not installed")

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from magi_cp.cloud.observability import attach_metrics

    monkeypatch.delenv("MAGI_CP_METRICS_TOKEN", raising=False)
    app = FastAPI()
    attach_metrics(app)
    c = TestClient(app)
    assert c.get("/metrics").status_code == 200
