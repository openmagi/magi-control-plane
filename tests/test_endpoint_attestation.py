"""P10 — endpoint attestation tests.

Cover the heartbeat REST round-trip, stale detection, and tenant
scoping. Hardware-side gate helpers live in `local/gate.py`; the
tests here exercise the cloud schema + REST surface."""
from __future__ import annotations
import os
import time

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.db import (
    EndpointHeartbeatRepo, init_schema, is_stale, make_engine,
)
from magi_cp.verifier.protocol import VerifierRegistry


# ── repo unit tests ──────────────────────────────────────────────────


@pytest.fixture
def engine():
    e = make_engine("sqlite:///:memory:")
    init_schema(e)
    return e


def test_repo_beat_upserts(engine):
    repo = EndpointHeartbeatRepo(engine)
    hb = repo.beat(
        endpoint_id="ep-1", tenant_id="t1",
        active_policy_digest="a" * 64, agent_version="0.1.0",
    )
    assert hb.endpoint_id == "ep-1"
    assert hb.active_policy_digest == "a" * 64

    # Second beat updates last_seen + digest
    time.sleep(0.01)
    hb2 = repo.beat(
        endpoint_id="ep-1", tenant_id="t1",
        active_policy_digest="b" * 64, agent_version="0.2.0",
    )
    assert hb2.active_policy_digest == "b" * 64
    assert hb2.agent_version == "0.2.0"
    # Same row (PK on endpoint_id)
    rows = repo.list_by_tenant("t1")
    assert len(rows) == 1


def test_repo_tenant_scoping(engine):
    repo = EndpointHeartbeatRepo(engine)
    repo.beat(endpoint_id="ep-a", tenant_id="t1",
              active_policy_digest=None)
    repo.beat(endpoint_id="ep-b", tenant_id="t2",
              active_policy_digest=None)
    assert [r.endpoint_id for r in repo.list_by_tenant("t1")] == ["ep-a"]
    assert [r.endpoint_id for r in repo.list_by_tenant("t2")] == ["ep-b"]


def test_is_stale_threshold(engine):
    repo = EndpointHeartbeatRepo(engine)
    hb = repo.beat(endpoint_id="ep-1", tenant_id="t1",
                    active_policy_digest=None)
    # Now: fresh
    assert is_stale(hb) is False
    # 25h later: stale
    future = int(time.time()) + 25 * 3600
    assert is_stale(hb, now=future) is True


# ── REST integration ─────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CP_API_KEY", "dev-key")
    monkeypatch.setenv("MAGI_CP_KEY_DIR", str(tmp_path / "keys"))
    dsn = f"sqlite:///{tmp_path / 'cp.sqlite'}"
    monkeypatch.setenv("MAGI_CP_DSN", dsn)
    monkeypatch.setenv("MAGI_CP_POLICY_STORE",
                       str(tmp_path / "policies.json"))
    app = create_app(verifier_registry=VerifierRegistry())
    return TestClient(app)


def test_heartbeat_round_trip(client):
    digest = "a" * 64
    r = client.post(
        "/endpoints/ep-1/heartbeat",
        headers={"X-Api-Key": "dev-key"},
        json={
            "endpoint_id": "ep-1",
            "active_policy_digest": digest,
            "agent_version": "0.1.5",
            "label": "macbook",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["endpoint_id"] == "ep-1"
    assert body["active_policy_digest"] == digest
    assert body["agent_version"] == "0.1.5"

    r = client.get("/endpoints", headers={"X-Api-Key": "dev-key"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["endpoint_id"] == "ep-1"
    assert items[0]["active_policy_digest"] == digest
    assert items[0]["stale"] is False


def test_heartbeat_rejects_endpoint_id_mismatch(client):
    r = client.post(
        "/endpoints/ep-1/heartbeat",
        headers={"X-Api-Key": "dev-key"},
        json={"endpoint_id": "ep-OTHER"},
    )
    assert r.status_code == 400
    assert "mismatch" in r.json()["detail"].lower()


def test_heartbeat_rejects_invalid_digest_shape(client):
    r = client.post(
        "/endpoints/ep-1/heartbeat",
        headers={"X-Api-Key": "dev-key"},
        json={"endpoint_id": "ep-1", "active_policy_digest": "not-a-sha"},
    )
    # pydantic rejects on pattern/length
    assert r.status_code == 422


def test_heartbeat_null_digest_allowed(client):
    """First-boot case: gate has no managed-settings yet, posts null
    digest. Cloud accepts and stores null — dashboard surfaces as
    'authored but not loaded'."""
    r = client.post(
        "/endpoints/ep-1/heartbeat",
        headers={"X-Api-Key": "dev-key"},
        json={"endpoint_id": "ep-1"},
    )
    assert r.status_code == 200
    assert r.json()["active_policy_digest"] is None


def test_heartbeat_requires_auth(client):
    r = client.post(
        "/endpoints/ep-1/heartbeat",
        json={"endpoint_id": "ep-1"},
    )
    assert r.status_code == 401


def test_endpoints_list_requires_auth(client):
    r = client.get("/endpoints")
    assert r.status_code == 401


# ── Issue #1 P0 (#1, #2): replay window + classification ─────────────


def test_heartbeat_rejects_ts_out_of_window(client):
    """Old ts (>5min from now) is refused so a captured payload can't
    be replayed."""
    r = client.post(
        "/endpoints/ep-1/heartbeat",
        headers={"X-Api-Key": "dev-key"},
        json={"endpoint_id": "ep-1", "ts": 1, "nonce": "abcd1234efgh5678"},
    )
    assert r.status_code == 400
    assert "ts" in r.json()["detail"].lower()


def test_heartbeat_rejects_nonce_reuse(client):
    """Same nonce twice in a row is a replay — refuse."""
    payload = {
        "endpoint_id": "ep-1",
        "ts": int(time.time()),
        "nonce": "abcd1234efgh5678",
    }
    r1 = client.post("/endpoints/ep-1/heartbeat",
                      headers={"X-Api-Key": "dev-key"}, json=payload)
    assert r1.status_code == 200
    # Replay
    r2 = client.post("/endpoints/ep-1/heartbeat",
                      headers={"X-Api-Key": "dev-key"}, json=payload)
    assert r2.status_code == 409


def test_endpoints_list_classifies_digests(client):
    """Issue #1 P0 (#2): every endpoint gets a `policy_status` label
    so the dashboard distinguishes `confirmed` / `not-loaded` /
    `unknown` instead of just showing 12 hex chars."""
    r = client.post(
        "/endpoints/ep-1/heartbeat",
        headers={"X-Api-Key": "dev-key"},
        json={"endpoint_id": "ep-1"},
    )
    assert r.status_code == 200
    listing = client.get("/endpoints", headers={"X-Api-Key": "dev-key"}).json()
    # Null digest → not-loaded
    assert listing["items"][0]["policy_status"] == "not-loaded"
    # Top-level meta surfaces
    assert "stale_threshold_s" in listing
    assert "recommended_heartbeat_interval_s" in listing


def test_endpoints_list_unknown_digest_when_gate_lies(client):
    """A digest the cloud never authored → `unknown`."""
    r = client.post(
        "/endpoints/ep-fake/heartbeat",
        headers={"X-Api-Key": "dev-key"},
        json={"endpoint_id": "ep-fake", "active_policy_digest": "f" * 64},
    )
    assert r.status_code == 200
    listing = client.get("/endpoints", headers={"X-Api-Key": "dev-key"}).json()
    statuses = {i["endpoint_id"]: i["policy_status"] for i in listing["items"]}
    # Without a policy_store wired into the test app, cloud_active is
    # None and every non-null digest renders as "unknown".
    assert statuses["ep-fake"] == "unknown"


def test_stale_threshold_env_override(client, monkeypatch):
    """Issue #1 P1 (#18): MAGI_CP_STALE_ENDPOINT_SECONDS tunes the
    threshold without code change."""
    monkeypatch.setenv("MAGI_CP_STALE_ENDPOINT_SECONDS", "60")
    listing = client.get("/endpoints", headers={"X-Api-Key": "dev-key"}).json()
    assert listing["stale_threshold_s"] == 60
