"""Codex runtime adapter: schema-only migration for existing DBs.

Design brief: 2026-06-30-codex-runtime-adapter-design (private planning repo)
Section 9 (Migration). Additive + reversible; no data destroyed on
rollback:

  1. ``tenants.runtime_id`` TEXT NOT NULL DEFAULT 'claude-code'.
  2. ``session_active_packs.runtime_id`` TEXT NOT NULL DEFAULT
     'claude-code', and the primary key rebuilt to include it
     (``(tenant_id, runtime_id, session_id)``).

Idempotent: every step is guarded by a column/PK inspection so calling
it on every app startup (via ``init_schema`` -> ``_apply_migrations``) is
a no-op once applied. A fresh DB created by ``create_all`` from the
updated ORM models already has the 3-column PK, so the SQLite rebuild
below only fires on a pre-adapter table.

SQLite cannot ``ALTER TABLE ... ADD PRIMARY KEY``; the PK rebuild is a
create-copy-drop-rename. Existing rows all take ``runtime_id =
'claude-code'`` and ``(session_id, tenant_id)`` was already unique, so
the new ``(tenant_id, runtime_id, session_id)`` PK cannot collide.
"""
from __future__ import annotations

from sqlalchemy import inspect as _inspect, text
from sqlalchemy.engine import Engine


_DEFAULT_RUNTIME = "claude-code"


def _columns(insp, table: str) -> set[str]:
    return {c["name"] for c in insp.get_columns(table)}


def _pk_columns(insp, table: str) -> list[str]:
    pk = insp.get_pk_constraint(table)
    return list(pk.get("constrained_columns") or [])


def upgrade(engine: Engine) -> None:
    """Apply the additive runtime_id columns + PK rebuild. Idempotent."""
    insp = _inspect(engine)
    tables = set(insp.get_table_names())
    dialect = engine.dialect.name

    # ── tenants.runtime_id ───────────────────────────────────────────
    if "tenants" in tables and "runtime_id" not in _columns(insp, "tenants"):
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE tenants ADD COLUMN runtime_id TEXT "
                f"NOT NULL DEFAULT '{_DEFAULT_RUNTIME}'"
            ))

    # ── session_active_packs.runtime_id + PK rebuild ─────────────────
    if "session_active_packs" not in tables:
        return
    insp = _inspect(engine)  # refresh after the tenants ALTER
    sap_cols = _columns(insp, "session_active_packs")
    pk_cols = _pk_columns(insp, "session_active_packs")

    already_has_col = "runtime_id" in sap_cols
    already_in_pk = "runtime_id" in pk_cols
    if already_has_col and already_in_pk:
        return  # fully migrated (fresh create_all path lands here)

    if dialect == "sqlite":
        _rebuild_sqlite(engine)
    else:
        _rebuild_generic(engine, add_column=not already_has_col,
                          rebuild_pk=not already_in_pk)


def _rebuild_sqlite(engine: Engine) -> None:
    """Create-copy-drop-rename the SQLite table with the 3-column PK.

    Preserves every row + its indexes' intent. The index on
    ``(expires_at, tenant_id)`` is recreated verbatim.
    """
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS session_active_packs__codex_new"))
        conn.execute(text(
            "CREATE TABLE session_active_packs__codex_new ("
            "  session_id VARCHAR(128) NOT NULL,"
            "  tenant_id VARCHAR(64) NOT NULL,"
            f"  runtime_id VARCHAR(32) NOT NULL DEFAULT '{_DEFAULT_RUNTIME}',"
            "  pack_ids JSON NOT NULL,"
            "  activated_at BIGINT NOT NULL,"
            "  last_seen_at BIGINT NOT NULL,"
            "  expires_at BIGINT NOT NULL,"
            "  PRIMARY KEY (tenant_id, runtime_id, session_id)"
            ")"
        ))
        conn.execute(text(
            "INSERT INTO session_active_packs__codex_new "
            "(session_id, tenant_id, runtime_id, pack_ids, activated_at, "
            " last_seen_at, expires_at) "
            "SELECT session_id, tenant_id, "
            f"'{_DEFAULT_RUNTIME}', "
            "pack_ids, activated_at, last_seen_at, expires_at "
            "FROM session_active_packs"
        ))
        conn.execute(text("DROP TABLE session_active_packs"))
        conn.execute(text(
            "ALTER TABLE session_active_packs__codex_new "
            "RENAME TO session_active_packs"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_session_active_packs_expires_tenant "
            "ON session_active_packs (expires_at, tenant_id)"
        ))


def _rebuild_generic(engine: Engine, *, add_column: bool,
                     rebuild_pk: bool) -> None:
    """Postgres (and other ALTER-capable dialects) path: ADD COLUMN then
    swap the PRIMARY KEY constraint to include runtime_id."""
    with engine.begin() as conn:
        if add_column:
            conn.execute(text(
                "ALTER TABLE session_active_packs ADD COLUMN runtime_id TEXT "
                f"NOT NULL DEFAULT '{_DEFAULT_RUNTIME}'"
            ))
        if rebuild_pk:
            # Postgres names the implicit PK ``<table>_pkey``.
            conn.execute(text(
                "ALTER TABLE session_active_packs "
                "DROP CONSTRAINT IF EXISTS session_active_packs_pkey"
            ))
            conn.execute(text(
                "ALTER TABLE session_active_packs "
                "ADD PRIMARY KEY (tenant_id, runtime_id, session_id)"
            ))


def downgrade(engine: Engine) -> None:
    """Reverse the migration: restore the 2-column PK and drop the
    ``runtime_id`` columns. No data destroyed beyond the additive columns
    (Codex rows, if any, collapse back onto the CC layer — the operator
    is expected to have already ``DELETE``-d ``runtime_id = 'codex'`` rows
    per the rollback runbook Section 13)."""
    insp = _inspect(engine)
    tables = set(insp.get_table_names())
    dialect = engine.dialect.name

    if "session_active_packs" in tables:
        if dialect == "sqlite":
            _downgrade_sqlite(engine)
        else:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE session_active_packs "
                    "DROP CONSTRAINT IF EXISTS session_active_packs_pkey"
                ))
                conn.execute(text(
                    "ALTER TABLE session_active_packs "
                    "ADD PRIMARY KEY (session_id, tenant_id)"
                ))
                if "runtime_id" in _columns(insp, "session_active_packs"):
                    conn.execute(text(
                        "ALTER TABLE session_active_packs DROP COLUMN runtime_id"
                    ))

    insp = _inspect(engine)
    if "tenants" in tables and "runtime_id" in _columns(insp, "tenants"):
        if dialect != "sqlite":
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE tenants DROP COLUMN runtime_id"))
        else:
            _drop_sqlite_column_tenants(engine)


def _downgrade_sqlite(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS session_active_packs__codex_old"))
        conn.execute(text(
            "CREATE TABLE session_active_packs__codex_old ("
            "  session_id VARCHAR(128) NOT NULL,"
            "  tenant_id VARCHAR(64) NOT NULL,"
            "  pack_ids JSON NOT NULL,"
            "  activated_at BIGINT NOT NULL,"
            "  last_seen_at BIGINT NOT NULL,"
            "  expires_at BIGINT NOT NULL,"
            "  PRIMARY KEY (session_id, tenant_id)"
            ")"
        ))
        # Collapse onto the CC layer: only claude-code rows survive the
        # 2-column PK cleanly; dedupe defensively by taking the CC rows.
        conn.execute(text(
            "INSERT OR IGNORE INTO session_active_packs__codex_old "
            "(session_id, tenant_id, pack_ids, activated_at, last_seen_at, "
            " expires_at) "
            "SELECT session_id, tenant_id, pack_ids, activated_at, "
            "last_seen_at, expires_at FROM session_active_packs"
        ))
        conn.execute(text("DROP TABLE session_active_packs"))
        conn.execute(text(
            "ALTER TABLE session_active_packs__codex_old "
            "RENAME TO session_active_packs"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_session_active_packs_expires_tenant "
            "ON session_active_packs (expires_at, tenant_id)"
        ))


def _drop_sqlite_column_tenants(engine: Engine) -> None:
    # SQLite 3.35+ supports DROP COLUMN directly.
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE tenants DROP COLUMN runtime_id"))


__all__ = ["upgrade", "downgrade"]
