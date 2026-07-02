"""PR-A / MIGRATION-1: the boot-time pack-centric migration must run under
a cross-process advisory lock on Postgres (so concurrent replica boots do
not race) and run directly on SQLite (single-writer, single-node).

We test the seam `_run_pack_centric_migration_locked` in isolation so the
test stays hermetic (no real Postgres): a fake engine reports the dialect
and records the SQL executed on its connection.
"""
from __future__ import annotations

import magi_cp.cloud.app as app_mod


class _FakeResult:
    def __init__(self, calls: list[str], sql: str) -> None:
        calls.append(sql)


class _FakeConn:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def __enter__(self) -> "_FakeConn":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, statement, params=None):
        # statement is a sqlalchemy TextClause; str() yields the SQL text.
        self._calls.append(str(statement))
        return _FakeResult([], str(statement))

    def commit(self) -> None:
        self._calls.append("COMMIT")


class _FakeDialect:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeEngine:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.dialect = _FakeDialect(name)
        self._calls = calls

    def connect(self) -> _FakeConn:
        return _FakeConn(self._calls)


def test_sqlite_runs_migration_directly_without_advisory_lock(monkeypatch):
    ran: list[str] = []
    monkeypatch.setattr(
        "magi_cp.cloud.pack_centric_migration.migrate_tenants_to_pack_centric",
        lambda engine, ps, pk: ran.append("migrated"),
    )
    sql_calls: list[str] = []
    engine = _FakeEngine("sqlite", sql_calls)

    app_mod._run_pack_centric_migration_locked(engine, object(), object())

    assert ran == ["migrated"]
    # No advisory-lock SQL on SQLite (engine.connect never used for locking).
    assert not any("advisory" in c.lower() for c in sql_calls)


def test_postgres_wraps_migration_in_advisory_lock(monkeypatch):
    order: list[str] = []
    monkeypatch.setattr(
        "magi_cp.cloud.pack_centric_migration.migrate_tenants_to_pack_centric",
        lambda engine, ps, pk: order.append("migrate"),
    )

    # Interleave the migration call into the same ordered log as the SQL so
    # we can assert lock -> migrate -> unlock ordering.
    engine = _FakeEngine("postgresql", order)

    app_mod._run_pack_centric_migration_locked(engine, object(), object())

    # Advisory lock acquired, migration run, then unlocked. (order holds the
    # interleaved SQL text + the "migrate" marker.)
    joined = " | ".join(order).lower()
    assert "pg_advisory_lock" in joined
    assert "pg_advisory_unlock" in joined
    lock_i = next(i for i, c in enumerate(order) if "pg_advisory_lock" in c.lower())
    mig_i = order.index("migrate")
    unlock_i = next(i for i, c in enumerate(order) if "pg_advisory_unlock" in c.lower())
    assert lock_i < mig_i < unlock_i


def test_postgres_unlocks_even_if_migration_raises(monkeypatch):
    order: list[str] = []

    def _boom(engine, ps, pk):
        order.append("migrate-raised")
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "magi_cp.cloud.pack_centric_migration.migrate_tenants_to_pack_centric",
        _boom,
    )
    engine = _FakeEngine("postgresql", order)

    raised = False
    try:
        app_mod._run_pack_centric_migration_locked(engine, object(), object())
    except RuntimeError:
        raised = True

    assert raised is True
    # The lock must be released on the failure path too.
    assert any("pg_advisory_unlock" in c.lower() for c in order)
