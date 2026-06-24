"""P3 cloud DB — SQLAlchemy ledger + HITL queue.

PR4: legacy `matter` / `doc_id` columns dropped from `hitl_item` (after
backfill); `LedgerRepo.append()` takes `subject=`; `HitlRepo.enqueue()`
takes `subject=` + `payload_hash=` (no legacy aliases).
"""
import pytest

from magi_cp.cloud.db import make_engine, init_schema, LedgerRepo, HitlRepo, HitlStatus


@pytest.fixture
def engine():
    eng = make_engine("sqlite:///:memory:")
    init_schema(eng)
    return eng


# ── Ledger ──────────────────────────────────────────────────────────
def test_ledger_append_and_list(engine):
    led = LedgerRepo(engine)
    e1 = led.append(subject="S1", body={"k": "v"}, token="t1")
    e2 = led.append(subject="S1", body={"k": "w"}, token="t2")
    assert e1.h != e2.h
    assert e2.prev == e1.h
    items = led.list_all()
    assert len(items) == 2
    assert items[0].h == e1.h


def _tamper_body_via_sql(engine, *, subject, new_body):
    """Test-only emulation of an attacker with DB write access. Not on repo (M5).

    PR4: the LedgerEntry DB column is still named `matter` (rename
    deferred to a future PR) — this helper queries via the underlying
    column name even though the wire vocabulary uses `subject`."""
    from sqlalchemy import update
    from sqlalchemy.orm import Session
    from magi_cp.cloud.db import LedgerEntry
    with Session(engine) as s:
        s.execute(update(LedgerEntry).where(LedgerEntry.matter == subject).values(body=new_body))
        s.commit()


def test_ledger_chain_includes_body(engine):
    """LOCK: hashing body + token (P1 review fix)."""
    led = LedgerRepo(engine)
    led.append(subject="S1", body={"verdict": "pass"}, token="t1")
    assert led.verify_chain()
    _tamper_body_via_sql(engine, subject="S1", new_body={"verdict": "TAMPERED"})
    assert not led.verify_chain()


def test_ledger_list_by_subject(engine):
    """PR4: canonical lookup by subject. Older `list_by_matter` removed."""
    led = LedgerRepo(engine)
    led.append(subject="A", body={"k": "1"}, token="t1")
    led.append(subject="B", body={"k": "2"}, token="t2")
    led.append(subject="A", body={"k": "3"}, token="t3")
    only_a = led.list_by_subject("A")
    assert len(only_a) == 2
    assert {e.body["k"] for e in only_a} == {"1", "3"}


# ── HITL queue ──────────────────────────────────────────────────────
def test_hitl_enqueue_and_pending(engine):
    h = HitlRepo(engine)
    item = h.enqueue(subject="S1", payload_hash="P1", reason="verbatim_review",
                     payload={"citations": []})
    assert item.status == HitlStatus.pending
    pending = h.list_pending()
    assert len(pending) == 1
    assert pending[0].id == item.id


def test_hitl_approve_marks_and_records_approver(engine):
    h = HitlRepo(engine)
    item = h.enqueue(subject="S1", payload_hash="P1", reason="x", payload={})
    h.approve(item.id, approver="partner@firm.example", note="reviewed")
    refreshed = h.get(item.id)
    assert refreshed.status == HitlStatus.approved
    assert refreshed.approver == "partner@firm.example"
    assert refreshed.note == "reviewed"
    assert h.list_pending() == []


def test_hitl_reject(engine):
    h = HitlRepo(engine)
    item = h.enqueue(subject="S1", payload_hash="P1", reason="x", payload={})
    h.reject(item.id, approver="partner@firm.example", note="fix citations")
    refreshed = h.get(item.id)
    assert refreshed.status == HitlStatus.rejected


def test_hitl_double_approve_is_rejected(engine):
    """Idempotency: approving already-decided item should not change status."""
    h = HitlRepo(engine)
    item = h.enqueue(subject="S1", payload_hash="P1", reason="x", payload={})
    h.approve(item.id, approver="a@x.example")
    with pytest.raises(ValueError, match="already"):
        h.approve(item.id, approver="b@x.example")


# ── PR4: canonical-only keying (legacy aliases removed) ─────────────
class TestPr4Keying:
    """PR4: HitlRepo.enqueue takes ONLY subject + payload_hash. The legacy
    matter / doc_id kwargs were removed; a caller passing them hits a
    TypeError at the Python boundary (clean signal, no silent acceptance)."""

    def test_enqueue_with_canonical_keys_only(self, engine):
        h = HitlRepo(engine)
        item = h.enqueue(
            subject="session_abc", payload_hash="sha256-deadbeef",
            reason="x", payload={},
        )
        refreshed = h.get(item.id)
        assert refreshed.subject == "session_abc"
        assert refreshed.payload_hash == "sha256-deadbeef"

    def test_enqueue_legacy_kwargs_raises_type_error(self, engine):
        """Passing the removed legacy kwarg is a clean TypeError —
        no silent accept under an alias, no hidden double-write."""
        h = HitlRepo(engine)
        with pytest.raises(TypeError):
            h.enqueue(matter="M1", doc_id="D1",  # type: ignore[call-arg]
                       reason="x", payload={})

    def test_enqueue_missing_subject_raises(self, engine):
        h = HitlRepo(engine)
        with pytest.raises(TypeError):
            h.enqueue(payload_hash="P1", reason="x", payload={})  # type: ignore[call-arg]

    def test_enqueue_missing_payload_hash_raises(self, engine):
        h = HitlRepo(engine)
        with pytest.raises(TypeError):
            h.enqueue(subject="S1", reason="x", payload={})  # type: ignore[call-arg]

    def test_subject_index_exists(self, engine):
        """PR4 contract: index on (subject, status) for the dashboard
        listing query. The legacy (matter, status) index is dropped by
        the PR4 migration script."""
        from sqlalchemy import inspect
        insp = inspect(engine)
        idx_names = {ix["name"] for ix in insp.get_indexes("hitl_item")}
        assert "ix_hitl_subject_status" in idx_names

    def test_canonical_columns_are_only_keying(self, engine):
        """PR4 ORM declaration: hitl_item has subject + payload_hash but
        no matter / doc_id (on a freshly-created table)."""
        from sqlalchemy import inspect
        insp = inspect(engine)
        cols = {c["name"] for c in insp.get_columns("hitl_item")}
        assert "subject" in cols
        assert "payload_hash" in cols
        # Fresh schema must not declare the dropped columns.
        assert "matter" not in cols
        assert "doc_id" not in cols


# ── PR3: backfill script (still tested — it's the prerequisite for PR4) ──
class TestPr3Backfill:
    """The backfill script copies legacy matter/doc_id into the canonical
    subject/payload_hash columns. PR4 still ships the script because
    operators upgrading from pre-PR3 must run it BEFORE PR4's drop."""

    def _build_pre_pr4_table(self, engine):
        """Synthesise a pre-PR4 `hitl_item` (PR3 shape: both legacy and
        canonical columns present) directly via DDL. We bypass the ORM
        because the PR4 ORM declaration only carries the canonical
        columns."""
        from sqlalchemy import text
        # Start fresh so we don't collide with init_schema's ORM table.
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS hitl_item"))
            conn.execute(text(
                "CREATE TABLE hitl_item ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  version INTEGER NOT NULL DEFAULT 0,"
                "  ts_created INTEGER NOT NULL,"
                "  ts_decided INTEGER,"
                "  tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',"
                "  matter VARCHAR(64),"
                "  doc_id VARCHAR(64),"
                "  subject VARCHAR(128),"
                "  payload_hash VARCHAR(128),"
                "  reason VARCHAR(64) NOT NULL,"
                "  payload JSON NOT NULL,"
                "  status VARCHAR(16) NOT NULL DEFAULT 'pending',"
                "  approver VARCHAR(256),"
                "  note TEXT"
                ")"
            ))
            conn.execute(text(
                "CREATE INDEX ix_hitl_subject_status ON hitl_item (subject, status)"
            ))

    def _insert_legacy_row(self, engine, *, matter, doc_id, ts=0):
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO hitl_item "
                "(ts_created, tenant_id, matter, doc_id, "
                " subject, payload_hash, reason, payload, status) "
                "VALUES (:ts, 'default', :m, :d, NULL, NULL, "
                "'legacy', '{\"citations\": []}', 'pending')"
            ), {"ts": ts, "m": matter, "d": doc_id})
            row_id = conn.execute(text(
                "SELECT id FROM hitl_item ORDER BY id DESC LIMIT 1"
            )).scalar_one()
        return int(row_id)

    @pytest.fixture
    def pre_pr4_engine(self):
        """An engine with the PR3-shape table (both column pairs present)
        but no ORM `init_schema()` — the backfill script targets exactly
        this state."""
        eng = make_engine("sqlite:///:memory:")
        self._build_pre_pr4_table(eng)
        return eng

    def test_backfill_populates_canonical_from_legacy(self, pre_pr4_engine):
        from scripts.migrate_pr3_backfill import backfill_hitl
        ids = [self._insert_legacy_row(pre_pr4_engine, matter=f"M{i}", doc_id=f"D{i}")
               for i in range(5)]
        n = backfill_hitl(pre_pr4_engine, chunk_size=2)
        assert n == 5
        # Read back via raw SQL — the ORM model is PR4-shape and no
        # longer carries the legacy columns.
        from sqlalchemy import text
        with pre_pr4_engine.begin() as conn:
            for row_id, i in zip(ids, range(5)):
                row = conn.execute(text(
                    "SELECT matter, doc_id, subject, payload_hash "
                    "FROM hitl_item WHERE id = :id"
                ), {"id": row_id}).one()
                assert row.subject == f"M{i}"
                assert row.payload_hash == f"D{i}"
                # Legacy columns untouched.
                assert row.matter == f"M{i}"
                assert row.doc_id == f"D{i}"

    def test_backfill_is_idempotent(self, pre_pr4_engine):
        from scripts.migrate_pr3_backfill import backfill_hitl
        for i in range(3):
            self._insert_legacy_row(pre_pr4_engine, matter=f"M{i}", doc_id=f"D{i}")
        n1 = backfill_hitl(pre_pr4_engine)
        n2 = backfill_hitl(pre_pr4_engine)
        assert n1 == 3
        assert n2 == 0   # nothing left to update — idempotent

    def test_backfill_handles_empty_table(self, pre_pr4_engine):
        from scripts.migrate_pr3_backfill import backfill_hitl
        n = backfill_hitl(pre_pr4_engine)
        assert n == 0

    def test_backfill_skips_rows_with_both_keys_null_does_not_loop(
        self, pre_pr4_engine,
    ):
        """Regression for issues #2 / #8 — see prior PR3 spec."""
        from sqlalchemy import text
        from scripts.migrate_pr3_backfill import backfill_hitl
        with pre_pr4_engine.begin() as conn:
            for m, d in [("M_OK", "D_OK"), (None, None), ("M_OK2", "D_OK2")]:
                conn.execute(text(
                    "INSERT INTO hitl_item "
                    "(ts_created, tenant_id, matter, doc_id, "
                    " subject, payload_hash, reason, payload, status) "
                    "VALUES (0, 'default', :m, :d, NULL, NULL, "
                    "'x', '{}', 'pending')"
                ), {"m": m, "d": d})
        n = backfill_hitl(pre_pr4_engine, chunk_size=1)
        assert n == 2

    def test_backfill_skips_empty_string_keys(self, pre_pr4_engine):
        """Regression for issue #4: empty strings treated as missing."""
        from sqlalchemy import text
        from scripts.migrate_pr3_backfill import backfill_hitl
        with pre_pr4_engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO hitl_item "
                "(ts_created, tenant_id, matter, doc_id, "
                " subject, payload_hash, reason, payload, status) "
                "VALUES (0, 'default', '', '', NULL, NULL, "
                "'x', '{}', 'pending')"
            ))
            conn.execute(text(
                "INSERT INTO hitl_item "
                "(ts_created, tenant_id, matter, doc_id, "
                " subject, payload_hash, reason, payload, status) "
                "VALUES (0, 'default', 'REAL', 'REAL_D', NULL, NULL, "
                "'x', '{}', 'pending')"
            ))
        n = backfill_hitl(pre_pr4_engine)
        assert n == 1


# ── PR4: drop-legacy migration script ───────────────────────────────
class TestPr4DropLegacy:
    """`scripts/migrate_pr4_drop_legacy.py` is the explicit cut-over —
    drops `matter` / `doc_id` columns and the legacy index. Refuses
    to run if any row still has `subject IS NULL` (would lose data)."""

    def _build_pre_pr4_table(self, engine):
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS hitl_item"))
            conn.execute(text(
                "CREATE TABLE hitl_item ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  version INTEGER NOT NULL DEFAULT 0,"
                "  ts_created INTEGER NOT NULL,"
                "  ts_decided INTEGER,"
                "  tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',"
                "  matter VARCHAR(64),"
                "  doc_id VARCHAR(64),"
                "  subject VARCHAR(128),"
                "  payload_hash VARCHAR(128),"
                "  reason VARCHAR(64) NOT NULL,"
                "  payload JSON NOT NULL,"
                "  status VARCHAR(16) NOT NULL DEFAULT 'pending',"
                "  approver VARCHAR(256),"
                "  note TEXT"
                ")"
            ))
            conn.execute(text(
                "CREATE INDEX ix_hitl_matter_status ON hitl_item (matter, status)"
            ))
            conn.execute(text(
                "CREATE INDEX ix_hitl_subject_status ON hitl_item (subject, status)"
            ))

    def _insert(self, engine, *, matter, doc_id, subject, payload_hash):
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO hitl_item "
                "(ts_created, tenant_id, matter, doc_id, "
                " subject, payload_hash, reason, payload, status) "
                "VALUES (0, 'default', :m, :d, :s, :p, "
                "'x', '{}', 'pending')"
            ), {"m": matter, "d": doc_id, "s": subject, "p": payload_hash})

    def test_drop_refuses_when_subject_null_present(self):
        """Backfill incomplete → migration MUST refuse. The legacy values
        are the only usable identifier for those rows; dropping the
        columns would silently destroy the link to the originating call.
        """
        from scripts.migrate_pr4_drop_legacy import (
            BackfillIncomplete, drop_legacy_columns,
        )
        eng = make_engine("sqlite:///:memory:")
        self._build_pre_pr4_table(eng)
        # One backfilled row + one un-backfilled row (subject IS NULL).
        self._insert(eng, matter="A", doc_id="X",
                     subject="A", payload_hash="X")
        self._insert(eng, matter="B", doc_id="Y",
                     subject=None, payload_hash=None)
        with pytest.raises(BackfillIncomplete, match="subject IS NULL"):
            drop_legacy_columns(eng)
        # Schema unchanged — refusal must be early, before any DDL.
        from sqlalchemy import inspect
        cols = {c["name"] for c in inspect(eng).get_columns("hitl_item")}
        assert "matter" in cols and "doc_id" in cols

    def test_drop_refuses_with_clear_message_on_pre_pr3_schema(self):
        """If `subject` doesn't exist yet (pre-PR3), refuse with an
        actionable hint that points operators at the backfill script."""
        from sqlalchemy import text
        from scripts.migrate_pr4_drop_legacy import (
            BackfillIncomplete, drop_legacy_columns,
        )
        eng = make_engine("sqlite:///:memory:")
        with eng.begin() as conn:
            conn.execute(text(
                "CREATE TABLE hitl_item ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  version INTEGER NOT NULL DEFAULT 0,"
                "  ts_created INTEGER NOT NULL,"
                "  ts_decided INTEGER,"
                "  tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',"
                "  matter VARCHAR(64),"
                "  doc_id VARCHAR(64),"
                "  reason VARCHAR(64) NOT NULL,"
                "  payload JSON NOT NULL,"
                "  status VARCHAR(16) NOT NULL DEFAULT 'pending',"
                "  approver VARCHAR(256),"
                "  note TEXT"
                ")"
            ))
        with pytest.raises(BackfillIncomplete, match="subject"):
            drop_legacy_columns(eng)

    def test_drop_succeeds_when_backfill_complete(self):
        """Happy path: every row has subject populated. Legacy columns +
        index are dropped; canonical index survives."""
        from sqlalchemy import inspect
        from scripts.migrate_pr4_drop_legacy import drop_legacy_columns
        eng = make_engine("sqlite:///:memory:")
        self._build_pre_pr4_table(eng)
        self._insert(eng, matter="A", doc_id="X",
                     subject="A", payload_hash="X")
        self._insert(eng, matter="B", doc_id="Y",
                     subject="B", payload_hash="Y")
        result = drop_legacy_columns(eng)
        assert result["rows_kept_null_subject"] == 0
        cols = {c["name"] for c in inspect(eng).get_columns("hitl_item")}
        assert "matter" not in cols
        assert "doc_id" not in cols
        assert "subject" in cols
        assert "payload_hash" in cols
        idx = {ix["name"] for ix in inspect(eng).get_indexes("hitl_item")}
        assert "ix_hitl_matter_status" not in idx
        assert "ix_hitl_subject_status" in idx

    def test_drop_is_idempotent(self):
        """Running on an already-PR4 schema is a clean no-op."""
        from scripts.migrate_pr4_drop_legacy import drop_legacy_columns
        eng = make_engine("sqlite:///:memory:")
        init_schema(eng)
        # Already canonical-only by ORM declaration.
        h = HitlRepo(eng)
        h.enqueue(subject="S", payload_hash="P", reason="x", payload={})
        result = drop_legacy_columns(eng)
        # All steps report `already_applied` — nothing left to drop.
        assert all(v == "already_applied" for v in result["steps"].values())

    def test_drop_dry_run_makes_no_changes(self):
        """`--dry-run` reports plan without modifying the schema."""
        from sqlalchemy import inspect
        from scripts.migrate_pr4_drop_legacy import drop_legacy_columns
        eng = make_engine("sqlite:///:memory:")
        self._build_pre_pr4_table(eng)
        self._insert(eng, matter="A", doc_id="X",
                     subject="A", payload_hash="X")
        result = drop_legacy_columns(eng, dry_run=True)
        assert result["dry_run"] is True
        # Plan reports the steps that would run.
        assert any(v.startswith("would_run") for v in result["steps"].values())
        # Schema still has the legacy columns.
        cols = {c["name"] for c in inspect(eng).get_columns("hitl_item")}
        assert "matter" in cols
        assert "doc_id" in cols

    # ── PR4 FIX cycle ───────────────────────────────────────────────
    def test_check_refuses_cleanly_when_hitl_item_missing(self, tmp_path):
        """Operator pointed the script at the wrong DSN. `inspect().get_columns
        ('hitl_item')` would raise NoSuchTableError; we want a clean
        BackfillIncomplete with an actionable hint, not a Python traceback."""
        from scripts.migrate_pr4_drop_legacy import (
            BackfillIncomplete, check_backfill_complete,
        )
        # Fresh DB, no `init_schema` → no `hitl_item` table at all.
        eng = make_engine(f"sqlite:///{tmp_path / 'empty.sqlite'}")
        with pytest.raises(BackfillIncomplete,
                           match="hitl_item table does not exist"):
            check_backfill_complete(eng)

    def test_main_cli_exits_1_on_null_subject(self, capsys, monkeypatch, tmp_path):
        """The CLI must return exit code 1 + a clear stderr message + NO DDL
        executed when the backfill is incomplete. Exercises the full main()
        entry point, not just drop_legacy_columns()."""
        from sqlalchemy import inspect
        from scripts.migrate_pr4_drop_legacy import main
        db_path = tmp_path / "pre-pr4.sqlite"
        eng = make_engine(f"sqlite:///{db_path}")
        self._build_pre_pr4_table(eng)
        self._insert(eng, matter="A", doc_id="X",
                     subject="A", payload_hash="X")
        self._insert(eng, matter="B", doc_id="Y",
                     subject=None, payload_hash=None)
        # Force --yes so the irreversibility gate is not what trips this case.
        rc = main(["--dsn", f"sqlite:///{db_path}", "--yes"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "backfill" in err.lower() or "subject is null" in err.lower()
        # Schema untouched — refusal must be early, before any DDL.
        cols = {c["name"] for c in inspect(eng).get_columns("hitl_item")}
        assert "matter" in cols
        assert "doc_id" in cols

    def test_main_cli_refuses_without_yes_or_tty(self, capsys, monkeypatch, tmp_path):
        """Non-dry-run + no --yes + non-TTY stdin → refuse with exit 1.
        Protects against CI runners with prod DB creds accidentally
        dropping columns."""
        from sqlalchemy import inspect
        from scripts.migrate_pr4_drop_legacy import main
        db_path = tmp_path / "ready.sqlite"
        eng = make_engine(f"sqlite:///{db_path}")
        self._build_pre_pr4_table(eng)
        self._insert(eng, matter="A", doc_id="X",
                     subject="A", payload_hash="X")
        # pytest's captured stdin is non-TTY by default.
        rc = main(["--dsn", f"sqlite:///{db_path}"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "--yes" in err or "irreversible" in err.lower()
        # Schema still has the legacy columns.
        cols = {c["name"] for c in inspect(eng).get_columns("hitl_item")}
        assert "matter" in cols
        assert "doc_id" in cols

    def test_main_cli_proceeds_with_yes_flag(self, capsys, tmp_path):
        """`--yes` short-circuits the confirmation prompt; legacy columns
        drop when backfill is complete."""
        from sqlalchemy import inspect
        from scripts.migrate_pr4_drop_legacy import main
        db_path = tmp_path / "ready.sqlite"
        eng = make_engine(f"sqlite:///{db_path}")
        self._build_pre_pr4_table(eng)
        self._insert(eng, matter="A", doc_id="X",
                     subject="A", payload_hash="X")
        rc = main(["--dsn", f"sqlite:///{db_path}", "--yes"])
        assert rc == 0
        cols = {c["name"] for c in inspect(eng).get_columns("hitl_item")}
        assert "matter" not in cols
        assert "doc_id" not in cols

    def test_main_cli_dry_run_skips_confirmation(self, capsys, tmp_path):
        """`--dry-run` is always safe and does not require --yes."""
        from sqlalchemy import inspect
        from scripts.migrate_pr4_drop_legacy import main
        db_path = tmp_path / "dryrun.sqlite"
        eng = make_engine(f"sqlite:///{db_path}")
        self._build_pre_pr4_table(eng)
        self._insert(eng, matter="A", doc_id="X",
                     subject="A", payload_hash="X")
        rc = main(["--dsn", f"sqlite:///{db_path}", "--dry-run"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "DRY RUN" in out
        # Schema untouched.
        cols = {c["name"] for c in inspect(eng).get_columns("hitl_item")}
        assert "matter" in cols and "doc_id" in cols

    def test_main_cli_no_table_exits_1_with_clean_message(self, capsys, tmp_path):
        """Wrong-DSN guard at the CLI layer: no traceback, exit 1, hint."""
        from scripts.migrate_pr4_drop_legacy import main
        db_path = tmp_path / "empty.sqlite"
        # Fresh DB, no init_schema → no hitl_item.
        rc = main(["--dsn", f"sqlite:///{db_path}", "--yes"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "hitl_item" in err
        assert "does not exist" in err


# ── PR3: in-place DDL migration on already-deployed instances ───────
class TestPr3InPlaceMigration:
    """init_schema must idempotently bring a pre-PR3 hitl_item table up to
    the PR3 shape (add subject / payload_hash columns + the new index).
    `Base.metadata.create_all` alone is `CREATE TABLE IF NOT EXISTS`-shaped
    and never runs `ALTER TABLE ADD COLUMN`, so any deployed instance
    pulling PR3+ code would crash on its first /hitl read without this.
    """

    def _build_pre_pr3_table(self, engine):
        """Synthesise a pre-PR3 `hitl_item` directly via DDL."""
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE hitl_item ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  version INTEGER NOT NULL DEFAULT 0,"
                "  ts_created INTEGER NOT NULL,"
                "  ts_decided INTEGER,"
                "  tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',"
                "  matter VARCHAR(64) NOT NULL,"
                "  doc_id VARCHAR(64) NOT NULL,"
                "  reason VARCHAR(64) NOT NULL,"
                "  payload JSON NOT NULL,"
                "  status VARCHAR(16) NOT NULL DEFAULT 'pending',"
                "  approver VARCHAR(256),"
                "  note TEXT"
                ")"
            ))
            conn.execute(text(
                "CREATE INDEX ix_hitl_matter_status ON hitl_item (matter, status)"
            ))
            conn.execute(text(
                "INSERT INTO hitl_item "
                "(ts_created, tenant_id, matter, doc_id, reason, payload, status) "
                "VALUES (0, 'default', 'LEGACY_M', 'LEGACY_D', "
                "'legacy', '{\"citations\": []}', 'pending')"
            ))

    def test_init_schema_adds_pr3_columns_to_existing_table(self):
        """The reproducer for the original PR3 failure mode: a pre-PR3
        table is in place; pulling PR4 code and calling init_schema must
        add the new columns + index without erroring. Legacy data is
        preserved (it's still there until PR4 drop runs)."""
        from sqlalchemy import inspect
        from magi_cp.cloud.db import make_engine, init_schema
        engine = make_engine("sqlite:///:memory:")
        self._build_pre_pr3_table(engine)
        cols = {c["name"] for c in inspect(engine).get_columns("hitl_item")}
        assert "subject" not in cols
        assert "payload_hash" not in cols
        init_schema(engine)
        cols = {c["name"] for c in inspect(engine).get_columns("hitl_item")}
        assert "subject" in cols
        assert "payload_hash" in cols
        idx = {ix["name"] for ix in inspect(engine).get_indexes("hitl_item")}
        assert "ix_hitl_subject_status" in idx
        # Legacy index is still present (PR4 drop script removes it later).
        assert "ix_hitl_matter_status" in idx

    def test_init_schema_is_idempotent_on_pr4_schema(self):
        """Running init_schema twice on an already-PR4 table must be a
        no-op (no errors, no spurious ALTERs). The fresh PR4 table has
        no legacy columns at all."""
        from sqlalchemy import inspect
        from magi_cp.cloud.db import make_engine, init_schema
        engine = make_engine("sqlite:///:memory:")
        init_schema(engine)        # first call: fresh PR4 build
        init_schema(engine)        # second call: idempotent
        cols = {c["name"] for c in inspect(engine).get_columns("hitl_item")}
        assert {"subject", "payload_hash"} <= cols
        # Fresh schema does not declare the dropped columns.
        assert "matter" not in cols
        assert "doc_id" not in cols
