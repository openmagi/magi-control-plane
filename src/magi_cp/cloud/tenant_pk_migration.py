"""TENANT-2 / TENANT-3: rebuild endpoint_heartbeat + compiled_policy_snapshot
primary keys to include tenant_id.

Both tables previously keyed on a single caller-influenceable column
(``endpoint_heartbeat.endpoint_id`` and ``compiled_policy_snapshot.digest``),
so one tenant could overwrite or read another tenant's row. The PK is rebuilt
to a composite ``(tenant_id, <old_pk>)``.

Idempotent + guarded by a PK inspection, so wiring it into
``init_schema -> _apply_migrations`` makes it a no-op once applied. A fresh DB
created by ``create_all`` from the updated ORM already has the composite PK, so
the SQLite rebuild below only fires on a pre-fix table.

SQLite cannot ``ALTER TABLE ... ADD PRIMARY KEY``; the rebuild is a
create-copy-drop-rename. Migration safety: the old single-column PK was unique,
so ``(tenant_id, old_pk)`` is also unique and cannot collide on copy.

This is a schema migration. init_schema applies it automatically, but on a
large already-deployed Postgres take a backup first (a PK swap briefly locks
the table). Rollback is a DB restore; reverting the app code alone leaves the
composite PK in place (create_all never drops it).
"""
from __future__ import annotations

from sqlalchemy import inspect as _inspect, text
from sqlalchemy.engine import Engine


def _pk_columns(insp, table: str) -> list[str]:
    pk = insp.get_pk_constraint(table)
    return list(pk.get("constrained_columns") or [])


def upgrade(engine: Engine) -> None:
    """Rebuild both PKs to include tenant_id. Idempotent."""
    insp = _inspect(engine)
    tables = set(insp.get_table_names())
    dialect = engine.dialect.name

    if "endpoint_heartbeat" in tables:
        if "tenant_id" not in _pk_columns(insp, "endpoint_heartbeat"):
            if dialect == "sqlite":
                _rebuild_heartbeat_sqlite(engine)
            else:
                _rebuild_pk_generic(
                    engine, "endpoint_heartbeat", ("tenant_id", "endpoint_id"),
                )

    insp = _inspect(engine)  # refresh
    if "compiled_policy_snapshot" in tables:
        if "tenant_id" not in _pk_columns(insp, "compiled_policy_snapshot"):
            if dialect == "sqlite":
                _rebuild_snapshot_sqlite(engine)
            else:
                _rebuild_pk_generic(
                    engine, "compiled_policy_snapshot", ("tenant_id", "digest"),
                )


def _rebuild_pk_generic(engine: Engine, table: str,
                        pk_cols: tuple[str, ...]) -> None:
    """Postgres path: drop the implicit ``<table>_pkey`` and re-add the
    composite PK. tenant_id is already NOT NULL on both tables."""
    cols = ", ".join(pk_cols)
    with engine.begin() as conn:
        conn.execute(text(
            f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_pkey"
        ))
        conn.execute(text(
            f"ALTER TABLE {table} ADD PRIMARY KEY ({cols})"
        ))


def _rebuild_heartbeat_sqlite(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS endpoint_heartbeat__tpk_new"))
        conn.execute(text(
            "CREATE TABLE endpoint_heartbeat__tpk_new ("
            "  tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',"
            "  endpoint_id VARCHAR(64) NOT NULL,"
            "  last_seen BIGINT NOT NULL,"
            "  active_policy_digest VARCHAR(64),"
            "  agent_version VARCHAR(64),"
            "  label VARCHAR(128),"
            "  signed_attestation VARCHAR(256),"
            "  last_nonce VARCHAR(64),"
            "  PRIMARY KEY (tenant_id, endpoint_id)"
            ")"
        ))
        conn.execute(text(
            "INSERT INTO endpoint_heartbeat__tpk_new "
            "(tenant_id, endpoint_id, last_seen, active_policy_digest, "
            " agent_version, label, signed_attestation, last_nonce) "
            "SELECT tenant_id, endpoint_id, last_seen, active_policy_digest, "
            "agent_version, label, signed_attestation, last_nonce "
            "FROM endpoint_heartbeat"
        ))
        conn.execute(text("DROP TABLE endpoint_heartbeat"))
        conn.execute(text(
            "ALTER TABLE endpoint_heartbeat__tpk_new "
            "RENAME TO endpoint_heartbeat"
        ))


def _rebuild_snapshot_sqlite(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "DROP TABLE IF EXISTS compiled_policy_snapshot__tpk_new"
        ))
        conn.execute(text(
            "CREATE TABLE compiled_policy_snapshot__tpk_new ("
            "  tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',"
            "  digest VARCHAR(64) NOT NULL,"
            "  ts BIGINT NOT NULL,"
            "  policy_ids JSON NOT NULL,"
            "  PRIMARY KEY (tenant_id, digest)"
            ")"
        ))
        conn.execute(text(
            "INSERT INTO compiled_policy_snapshot__tpk_new "
            "(tenant_id, digest, ts, policy_ids) "
            "SELECT tenant_id, digest, ts, policy_ids "
            "FROM compiled_policy_snapshot"
        ))
        conn.execute(text("DROP TABLE compiled_policy_snapshot"))
        conn.execute(text(
            "ALTER TABLE compiled_policy_snapshot__tpk_new "
            "RENAME TO compiled_policy_snapshot"
        ))


__all__ = ["upgrade"]
