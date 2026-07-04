"""P1 Codex adapter: additive ``runtime_id`` migration.

Design brief: 2026-06-30-codex-runtime-adapter-design (private planning repo)
Section 9. The migration adds ``runtime_id`` to ``tenants`` and
``session_active_packs`` (defaulting to ``claude-code``) and rebuilds the
session-packs primary key to include it. Additive + reversible + no data
destroyed. These tests run it on a fresh pre-adapter SQLite DB, assert the
column + PK shape, and pin idempotency + no-collision + downgrade.
"""
from __future__ import annotations

from magi_cp.cloud.codex_runtime_migration import downgrade, upgrade
from magi_cp.cloud.db import init_schema, make_engine
from sqlalchemy import inspect as sa_inspect, text


# ── pre-adapter (2-column PK, no runtime_id) DB builder ──────────────
def _make_pre_adapter_db(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/cloud.sqlite")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE tenants (id VARCHAR(64) NOT NULL PRIMARY KEY)"
        ))
        conn.execute(text(
            "CREATE TABLE session_active_packs ("
            "  session_id VARCHAR(128) NOT NULL,"
            "  tenant_id VARCHAR(64) NOT NULL,"
            "  pack_ids JSON NOT NULL,"
            "  activated_at BIGINT NOT NULL,"
            "  last_seen_at BIGINT NOT NULL,"
            "  expires_at BIGINT NOT NULL,"
            "  PRIMARY KEY (session_id, tenant_id)"
            ")"
        ))
        conn.execute(text(
            "CREATE INDEX ix_session_active_packs_expires_tenant "
            "ON session_active_packs (expires_at, tenant_id)"
        ))
        conn.execute(text("INSERT INTO tenants (id) VALUES ('tenant-a')"))
        conn.execute(text(
            "INSERT INTO session_active_packs "
            "(session_id, tenant_id, pack_ids, activated_at, last_seen_at, "
            " expires_at) VALUES "
            "('s1', 'tenant-a', '[\"p1\"]', 1, 2, 3),"
            "('s2', 'tenant-a', '[\"p2\"]', 4, 5, 6)"
        ))
    return engine


def _cols(engine, table):
    return {c["name"] for c in sa_inspect(engine).get_columns(table)}


def _pk(engine, table):
    return list(
        sa_inspect(engine).get_pk_constraint(table)["constrained_columns"]
    )


# ── 1. ALTER runs cleanly on a fresh pre-adapter DB ──────────────────
def test_upgrade_adds_runtime_id_columns(tmp_path):
    engine = _make_pre_adapter_db(tmp_path)
    upgrade(engine)
    assert "runtime_id" in _cols(engine, "tenants")
    assert "runtime_id" in _cols(engine, "session_active_packs")


def test_upgrade_rebuilds_session_packs_pk_with_runtime_id(tmp_path):
    engine = _make_pre_adapter_db(tmp_path)
    upgrade(engine)
    assert set(_pk(engine, "session_active_packs")) == {
        "tenant_id", "runtime_id", "session_id",
    }


# ── 2. existing rows default to claude-code ──────────────────────────
def test_existing_rows_get_claude_code_runtime(tmp_path):
    engine = _make_pre_adapter_db(tmp_path)
    upgrade(engine)
    with engine.begin() as conn:
        tenants = conn.execute(
            text("SELECT runtime_id FROM tenants")
        ).scalars().all()
        packs = conn.execute(text(
            "SELECT session_id, runtime_id FROM session_active_packs "
            "ORDER BY session_id"
        )).all()
    assert tenants == ["claude-code"]
    assert packs == [("s1", "claude-code"), ("s2", "claude-code")]


def test_upgrade_preserves_all_rows(tmp_path):
    engine = _make_pre_adapter_db(tmp_path)
    upgrade(engine)
    with engine.begin() as conn:
        n = conn.execute(
            text("SELECT COUNT(*) FROM session_active_packs")
        ).scalar()
    assert n == 2


# ── 3. idempotent (re-run is a no-op, PK rebuild does not collide) ───
def test_upgrade_is_idempotent(tmp_path):
    engine = _make_pre_adapter_db(tmp_path)
    upgrade(engine)
    # Second run: columns + PK already migrated → guarded no-op.
    upgrade(engine)
    assert set(_pk(engine, "session_active_packs")) == {
        "tenant_id", "runtime_id", "session_id",
    }
    with engine.begin() as conn:
        n = conn.execute(
            text("SELECT COUNT(*) FROM session_active_packs")
        ).scalar()
    assert n == 2


def test_pk_rebuild_no_collision_with_existing_unique_rows(tmp_path):
    """The pre-adapter rows are unique on (session_id, tenant_id); folding
    them all onto runtime_id='claude-code' keeps (tenant, runtime, session)
    unique, so the rebuild cannot violate the new PK."""
    engine = _make_pre_adapter_db(tmp_path)
    upgrade(engine)
    # Inserting a genuinely new (tenant, runtime, session) triple works;
    # re-inserting an existing one raises (PK enforced post-rebuild).
    import pytest
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO session_active_packs "
            "(session_id, tenant_id, runtime_id, pack_ids, activated_at, "
            " last_seen_at, expires_at) VALUES "
            "('s1', 'tenant-a', 'codex', '[\"p3\"]', 7, 8, 9)"
        ))
    with pytest.raises(Exception):
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO session_active_packs "
                "(session_id, tenant_id, runtime_id, pack_ids, activated_at, "
                " last_seen_at, expires_at) VALUES "
                "('s1', 'tenant-a', 'claude-code', '[\"dup\"]', 1, 2, 3)"
            ))


# ── 4. fresh create_all DB already has the columns (no-op) ───────────
def test_fresh_create_all_db_is_already_migrated(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/fresh.sqlite")
    init_schema(engine)  # ORM create_all builds the 3-column-PK shape
    assert "runtime_id" in _cols(engine, "tenants")
    assert "runtime_id" in _cols(engine, "session_active_packs")
    assert set(_pk(engine, "session_active_packs")) == {
        "tenant_id", "runtime_id", "session_id",
    }
    # Explicit upgrade on the already-migrated DB is a clean no-op.
    upgrade(engine)
    assert set(_pk(engine, "session_active_packs")) == {
        "tenant_id", "runtime_id", "session_id",
    }


# ── 5. reversible downgrade ──────────────────────────────────────────
def test_downgrade_restores_two_column_pk_and_drops_columns(tmp_path):
    engine = _make_pre_adapter_db(tmp_path)
    upgrade(engine)
    downgrade(engine)
    assert "runtime_id" not in _cols(engine, "tenants")
    assert "runtime_id" not in _cols(engine, "session_active_packs")
    assert set(_pk(engine, "session_active_packs")) == {
        "session_id", "tenant_id",
    }
    # CC rows survive the collapse back onto the 2-column layer.
    with engine.begin() as conn:
        n = conn.execute(
            text("SELECT COUNT(*) FROM session_active_packs")
        ).scalar()
    assert n == 2
