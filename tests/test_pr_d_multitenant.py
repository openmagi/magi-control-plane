"""PR-D: multitenant scope correctness.

- TENANT-1: LedgerRepo.list_by_subject filters by tenant, and the HITL detail
  route scopes its ledger context by the item's tenant.
- TENANT-2: endpoint_heartbeat keys on (tenant_id, endpoint_id); two tenants
  with the same endpoint_id keep distinct rows.
- TENANT-3: compiled_policy_snapshot keys on (tenant_id, digest); two tenants
  recording the same digest keep distinct rows.
- migration: the PK rebuild is idempotent and preserves rows on a pre-fix DB.
"""
from __future__ import annotations

import tempfile

import pytest  # noqa: F401
from sqlalchemy import inspect as _inspect, text
from sqlalchemy.orm import Session

from magi_cp.cloud.db import (
    CompiledPolicySnapshotRepo,
    EndpointHeartbeatRepo,
    HitlRepo,
    LedgerRepo,
    init_schema,
    make_engine,
)


def _mem_engine():
    e = make_engine("sqlite:///:memory:")
    init_schema(e)
    return e


# ── TENANT-1: ledger subject scoping ─────────────────────────────────
def test_list_by_subject_filters_by_tenant():
    e = _mem_engine()
    led = LedgerRepo(e)
    led.append(subject="s1", body={"who": "A"}, token="", tenant_id="tenant_a")
    led.append(subject="s1", body={"who": "B"}, token="", tenant_id="tenant_b")

    a = led.list_by_subject("s1", tenant_id="tenant_a")
    assert [r.body["who"] for r in a] == ["A"]

    b = led.list_by_subject("s1", tenant_id="tenant_b")
    assert [r.body["who"] for r in b] == ["B"]

    # Unscoped (internal chain callers) still sees both.
    both = led.list_by_subject("s1")
    assert {r.body["who"] for r in both} == {"A", "B"}


def test_hitl_detail_ledger_context_is_tenant_scoped(monkeypatch):
    from fastapi.testclient import TestClient
    from magi_cp.cloud.app import create_app

    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", "hitl-test-key")
    store = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    store.write("[]")
    store.close()
    app = create_app(dsn="sqlite:///:memory:", policy_store_path=store.name)
    engine = app.state.engine

    led = LedgerRepo(engine)
    hitl = HitlRepo(engine)
    led.append(subject="sess1", body={"secret": "A-body"}, token="",
               tenant_id="tenant_a")
    led.append(subject="sess1", body={"secret": "B-body"}, token="",
               tenant_id="tenant_b")
    item = hitl.enqueue(reason="review", payload={}, subject="sess1",
                        payload_hash="a" * 64, tenant_id="tenant_a")

    c = TestClient(app)
    r = c.get(f"/hitl/{item.id}/detail",
              headers={"X-Hitl-Api-Key": "hitl-test-key"})
    assert r.status_code == 200
    bodies = [e["body"] for e in r.json()["ledger_context"]]
    blob = repr(bodies)
    assert "A-body" in blob
    assert "B-body" not in blob   # tenant B's ledger body must not leak


# ── TENANT-2: heartbeat PK isolation ─────────────────────────────────
def test_heartbeat_same_endpoint_id_two_tenants_distinct_rows():
    e = _mem_engine()
    repo = EndpointHeartbeatRepo(e)
    repo.beat(endpoint_id="host-1", tenant_id="tenant_a",
              active_policy_digest="digA", nonce="n1")
    repo.beat(endpoint_id="host-1", tenant_id="tenant_b",
              active_policy_digest="digB", nonce="n2")

    a = repo.get("host-1", "tenant_a")
    b = repo.get("host-1", "tenant_b")
    assert a is not None and a.active_policy_digest == "digA"
    assert b is not None and b.active_policy_digest == "digB"
    # Tenant A did not overwrite tenant B.
    assert a.tenant_id == "tenant_a"
    assert b.tenant_id == "tenant_b"


def test_heartbeat_pk_is_composite():
    e = _mem_engine()
    pk = _inspect(e).get_pk_constraint("endpoint_heartbeat")
    assert set(pk["constrained_columns"]) == {"tenant_id", "endpoint_id"}


# ── TENANT-3: snapshot PK isolation ──────────────────────────────────
def test_snapshot_same_digest_two_tenants_distinct_rows():
    e = _mem_engine()
    repo = CompiledPolicySnapshotRepo(e)
    repo.record(digest="deadbeef", tenant_id="tenant_a", policy_ids=["p-a"])
    repo.record(digest="deadbeef", tenant_id="tenant_b", policy_ids=["p-b"])

    assert repo.known_digests_for_tenant("tenant_a") == {"deadbeef"}
    assert repo.known_digests_for_tenant("tenant_b") == {"deadbeef"}
    # Both rows persisted (tenant B's record did not no-op against A's digest).
    with Session(e) as s:
        n = s.execute(text("SELECT COUNT(*) FROM compiled_policy_snapshot")).scalar()
    assert n == 2


def test_snapshot_pk_is_composite():
    e = _mem_engine()
    pk = _inspect(e).get_pk_constraint("compiled_policy_snapshot")
    assert set(pk["constrained_columns"]) == {"tenant_id", "digest"}


# ── migration: pre-fix single-column PK -> composite, rows preserved ──
def test_migration_rebuilds_prefix_heartbeat_table():
    from magi_cp.cloud.tenant_pk_migration import upgrade

    e = make_engine("sqlite:///:memory:")
    # Build the OLD-shape table (endpoint_id-only PK) and seed a row.
    with e.begin() as conn:
        conn.execute(text(
            "CREATE TABLE endpoint_heartbeat ("
            "  endpoint_id VARCHAR(64) NOT NULL PRIMARY KEY,"
            "  tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',"
            "  last_seen BIGINT NOT NULL,"
            "  active_policy_digest VARCHAR(64),"
            "  agent_version VARCHAR(64),"
            "  label VARCHAR(128),"
            "  signed_attestation VARCHAR(256),"
            "  last_nonce VARCHAR(64)"
            ")"
        ))
        conn.execute(text(
            "INSERT INTO endpoint_heartbeat "
            "(endpoint_id, tenant_id, last_seen) VALUES "
            "('host-1', 'tenant_a', 100)"
        ))

    upgrade(e)

    pk = _inspect(e).get_pk_constraint("endpoint_heartbeat")
    assert set(pk["constrained_columns"]) == {"tenant_id", "endpoint_id"}
    with e.begin() as conn:
        row = conn.execute(text(
            "SELECT tenant_id, last_seen FROM endpoint_heartbeat "
            "WHERE endpoint_id='host-1'"
        )).first()
    assert row == ("tenant_a", 100)

    # Idempotent: running again is a no-op (PK already composite).
    upgrade(e)
    pk2 = _inspect(e).get_pk_constraint("endpoint_heartbeat")
    assert set(pk2["constrained_columns"]) == {"tenant_id", "endpoint_id"}
