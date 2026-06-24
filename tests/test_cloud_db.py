"""P3 cloud DB — SQLAlchemy ledger + HITL queue."""
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
    e1 = led.append(matter="M1", body={"k": "v"}, token="t1")
    e2 = led.append(matter="M1", body={"k": "w"}, token="t2")
    assert e1.h != e2.h
    assert e2.prev == e1.h
    items = led.list_all()
    assert len(items) == 2
    assert items[0].h == e1.h


def _tamper_body_via_sql(engine, *, matter, new_body):
    """Test-only emulation of an attacker with DB write access. Not on repo (M5)."""
    from sqlalchemy import update
    from sqlalchemy.orm import Session
    from magi_cp.cloud.db import LedgerEntry
    with Session(engine) as s:
        s.execute(update(LedgerEntry).where(LedgerEntry.matter == matter).values(body=new_body))
        s.commit()


def test_ledger_chain_includes_body(engine):
    """LOCK: hashing body + token (P1 review fix)."""
    led = LedgerRepo(engine)
    led.append(matter="M1", body={"verdict": "pass"}, token="t1")
    assert led.verify_chain()
    _tamper_body_via_sql(engine, matter="M1", new_body={"verdict": "TAMPERED"})
    assert not led.verify_chain()


# ── HITL queue ──────────────────────────────────────────────────────
def test_hitl_enqueue_and_pending(engine):
    h = HitlRepo(engine)
    item = h.enqueue(matter="M1", doc_id="D1", reason="verbatim_review",
                     payload={"citations": []})
    assert item.status == HitlStatus.pending
    pending = h.list_pending()
    assert len(pending) == 1
    assert pending[0].id == item.id


def test_hitl_approve_marks_and_records_approver(engine):
    h = HitlRepo(engine)
    item = h.enqueue(matter="M1", doc_id="D1", reason="x", payload={})
    h.approve(item.id, approver="partner@firm.example", note="reviewed")
    refreshed = h.get(item.id)
    assert refreshed.status == HitlStatus.approved
    assert refreshed.approver == "partner@firm.example"
    assert refreshed.note == "reviewed"
    assert h.list_pending() == []


def test_hitl_reject(engine):
    h = HitlRepo(engine)
    item = h.enqueue(matter="M1", doc_id="D1", reason="x", payload={})
    h.reject(item.id, approver="partner@firm.example", note="fix citations")
    refreshed = h.get(item.id)
    assert refreshed.status == HitlStatus.rejected


def test_hitl_double_approve_is_rejected(engine):
    """Idempotency: approving already-decided item should not change status."""
    h = HitlRepo(engine)
    item = h.enqueue(matter="M1", doc_id="D1", reason="x", payload={})
    h.approve(item.id, approver="a@x.example")
    with pytest.raises(ValueError, match="already"):
        h.approve(item.id, approver="b@x.example")


# ── PR3: subject + payload_hash keying ──────────────────────────────
class TestPr3Keying:
    """PR3 widens HitlItem to carry (subject, payload_hash) alongside the
    legacy (matter, doc_id). New rows double-write into both pairs so
    legacy readers AND canonical readers both work during the transition."""

    def test_enqueue_with_subject_payload_hash_only(self, engine):
        h = HitlRepo(engine)
        item = h.enqueue(
            subject="session_abc", payload_hash="sha256-deadbeef",
            reason="x", payload={},
        )
        # Both column pairs populated to the same value (double-write).
        refreshed = h.get(item.id)
        assert refreshed.subject == "session_abc"
        assert refreshed.payload_hash == "sha256-deadbeef"
        assert refreshed.matter == "session_abc"
        assert refreshed.doc_id == "sha256-deadbeef"

    def test_enqueue_with_legacy_only_still_works(self, engine):
        h = HitlRepo(engine)
        item = h.enqueue(matter="M1", doc_id="D1", reason="x", payload={})
        refreshed = h.get(item.id)
        # Canonical columns mirrored from the legacy input.
        assert refreshed.matter == "M1"
        assert refreshed.doc_id == "D1"
        assert refreshed.subject == "M1"
        assert refreshed.payload_hash == "D1"

    def test_enqueue_with_both_pairs_subject_wins(self, engine):
        """If a caller passes both pairs with conflicting values, the
        canonical (subject, payload_hash) input wins. This matters for
        callers mid-migration who pass legacy values defensively."""
        h = HitlRepo(engine)
        item = h.enqueue(
            subject="session_new", payload_hash="hash_new",
            matter="legacy_M", doc_id="legacy_D",
            reason="x", payload={},
        )
        refreshed = h.get(item.id)
        # subject input wins, mirrored into BOTH columns.
        assert refreshed.subject == "session_new"
        assert refreshed.payload_hash == "hash_new"
        assert refreshed.matter == "session_new"
        assert refreshed.doc_id == "hash_new"

    def test_enqueue_without_any_keying_raises(self, engine):
        h = HitlRepo(engine)
        with pytest.raises(ValueError, match="requires subject"):
            h.enqueue(reason="x", payload={})

    def test_display_helpers_prefer_subject_over_matter(self, engine):
        from magi_cp.cloud.db import (
            HitlItem, hitl_display_payload_hash, hitl_display_subject,
        )
        from sqlalchemy.orm import Session
        # Simulate a legacy row written before PR3 — only matter/doc_id
        # populated, canonical columns still NULL.
        with Session(engine) as s:
            legacy = HitlItem(
                ts_created=0, tenant_id="default",
                matter="LEG_M", doc_id="LEG_D",
                subject=None, payload_hash=None,
                reason="x", payload={"citations": []},
            )
            s.add(legacy); s.commit(); s.refresh(legacy); s.expunge(legacy)
        # And a PR3 row.
        h = HitlRepo(engine)
        pr3 = h.enqueue(subject="S1", payload_hash="P1",
                         reason="x", payload={})
        # Helpers prefer the canonical column when present, fall back to
        # legacy when only the legacy column is populated.
        assert hitl_display_subject(legacy) == "LEG_M"
        assert hitl_display_payload_hash(legacy) == "LEG_D"
        assert hitl_display_subject(pr3) == "S1"
        assert hitl_display_payload_hash(pr3) == "P1"

    def test_subject_index_exists(self, engine):
        """PR3 contract: index on (subject, status) for the dashboard
        listing query. Catches accidental removal in future migrations."""
        from sqlalchemy import inspect
        insp = inspect(engine)
        idx_names = {ix["name"] for ix in insp.get_indexes("hitl_item")}
        assert "ix_hitl_subject_status" in idx_names
        # And the legacy index is still there during the transition.
        assert "ix_hitl_matter_status" in idx_names


# ── PR3: backfill script ────────────────────────────────────────────
class TestPr3Backfill:
    """The backfill script copies legacy matter/doc_id into the canonical
    subject/payload_hash columns for rows written before PR3."""

    def _insert_legacy_row(self, engine, *, matter, doc_id, ts=0):
        from magi_cp.cloud.db import HitlItem
        from sqlalchemy.orm import Session
        with Session(engine) as s:
            item = HitlItem(
                ts_created=ts, tenant_id="default",
                matter=matter, doc_id=doc_id,
                # Legacy row — canonical columns NULL.
                subject=None, payload_hash=None,
                reason="legacy", payload={"citations": []},
            )
            s.add(item); s.commit(); s.refresh(item)
            row_id = item.id
            s.expunge(item)
            return row_id

    def test_backfill_populates_canonical_from_legacy(self, engine):
        from scripts.migrate_pr3_backfill import backfill_hitl
        ids = [self._insert_legacy_row(engine, matter=f"M{i}", doc_id=f"D{i}")
               for i in range(5)]
        n = backfill_hitl(engine, chunk_size=2)
        assert n == 5
        h = HitlRepo(engine)
        for row_id, i in zip(ids, range(5)):
            refreshed = h.get(row_id)
            assert refreshed.subject == f"M{i}"
            assert refreshed.payload_hash == f"D{i}"
            # Legacy columns untouched.
            assert refreshed.matter == f"M{i}"
            assert refreshed.doc_id == f"D{i}"

    def test_backfill_is_idempotent(self, engine):
        from scripts.migrate_pr3_backfill import backfill_hitl
        for i in range(3):
            self._insert_legacy_row(engine, matter=f"M{i}", doc_id=f"D{i}")
        n1 = backfill_hitl(engine)
        n2 = backfill_hitl(engine)
        assert n1 == 3
        assert n2 == 0   # nothing left to update — idempotent

    def test_backfill_skips_already_populated_rows(self, engine):
        """A row written through PR3 HitlRepo.enqueue already has both
        pairs populated. The backfill must not overwrite them."""
        from scripts.migrate_pr3_backfill import backfill_hitl
        # Mixed: 2 legacy rows, 2 PR3 rows.
        self._insert_legacy_row(engine, matter="OLD1", doc_id="OLDD1")
        self._insert_legacy_row(engine, matter="OLD2", doc_id="OLDD2")
        h = HitlRepo(engine)
        h.enqueue(subject="NEW1", payload_hash="NEWP1",
                  reason="x", payload={})
        h.enqueue(subject="NEW2", payload_hash="NEWP2",
                  reason="x", payload={})
        n = backfill_hitl(engine)
        assert n == 2   # only the legacy rows touched

    def test_backfill_handles_empty_table(self, engine):
        from scripts.migrate_pr3_backfill import backfill_hitl
        n = backfill_hitl(engine)
        assert n == 0

    def test_backfill_skips_rows_with_both_keys_null_does_not_loop(self, engine):
        """Regression for issues #2 / #8: a row with NULL/NULL legacy keys
        used to keep `subject IS NULL`, so the outer `WHERE subject IS NULL`
        scan re-fetched the same page forever. Cursor-style watermark fixes
        it. We run with a small budget that the original buggy script would
        blow through; the fixed script returns within a single forward
        scan."""
        from magi_cp.cloud.db import HitlItem
        from sqlalchemy.orm import Session
        from scripts.migrate_pr3_backfill import backfill_hitl
        # Insert a NULL/NULL legacy row alongside two valid ones.
        with Session(engine) as s:
            for matter, doc_id in [("M_OK", "D_OK"),
                                    (None, None),
                                    ("M_OK2", "D_OK2")]:
                s.add(HitlItem(
                    ts_created=0, tenant_id="default",
                    matter=matter, doc_id=doc_id,
                    subject=None, payload_hash=None,
                    reason="x", payload={},
                ))
            s.commit()
        # Tight chunk size forces the cursor to advance one row at a time,
        # which would have been the worst case under the old skip-and-stay
        # behaviour.
        n = backfill_hitl(engine, chunk_size=1)
        # Two valid rows backfilled; the NULL/NULL row was skipped (the
        # backfill is not in the business of inventing identifiers).
        assert n == 2

    def test_backfill_skips_empty_string_keys(self, engine):
        """Regression for issue #4: a legacy row with `matter == ''` AND
        `doc_id == ''` (empty strings, not NULL — possible under the old
        NOT NULL+default schema) used to be backfilled as
        `subject='', payload_hash=''`, which the truthy display helpers
        then treated as `None`. Result: the canonical columns advertised
        a row that downstream readers silently rejected. Treat empty
        strings the same as NULL — skip with a warning."""
        from magi_cp.cloud.db import HitlItem
        from sqlalchemy.orm import Session
        from scripts.migrate_pr3_backfill import backfill_hitl
        with Session(engine) as s:
            s.add(HitlItem(
                ts_created=0, tenant_id="default",
                matter="", doc_id="",
                subject=None, payload_hash=None,
                reason="x", payload={},
            ))
            # And one legit row to make sure progress still happens.
            s.add(HitlItem(
                ts_created=0, tenant_id="default",
                matter="REAL", doc_id="REAL_D",
                subject=None, payload_hash=None,
                reason="x", payload={},
            ))
            s.commit()
        n = backfill_hitl(engine)
        assert n == 1   # only the non-empty row touched


# ── PR3: in-place DDL migration on already-deployed instances ───────
class TestPr3InPlaceMigration:
    """init_schema must idempotently bring a pre-PR3 hitl_item table up to
    the PR3 shape (add subject / payload_hash columns + the new index).
    `Base.metadata.create_all` alone is `CREATE TABLE IF NOT EXISTS`-shaped
    and never runs `ALTER TABLE ADD COLUMN`, so any deployed instance
    pulling PR3 code would crash on its first /hitl read without this.
    """

    def _build_pre_pr3_table(self, engine):
        """Synthesise a pre-PR3 `hitl_item` directly via DDL. We avoid the
        ORM here because the ORM declaration is PR3-shaped; we need to test
        the upgrade path on the OLD shape."""
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
            # One legacy row so the post-upgrade /hitl read path has data
            # to round-trip.
            conn.execute(text(
                "INSERT INTO hitl_item "
                "(ts_created, tenant_id, matter, doc_id, reason, payload, status) "
                "VALUES (0, 'default', 'LEGACY_M', 'LEGACY_D', "
                "'legacy', '{\"citations\": []}', 'pending')"
            ))

    def test_init_schema_adds_pr3_columns_to_existing_table(self):
        """The reproducer for the original failure: a pre-PR3 table is in
        place; pulling PR3 code and calling init_schema must add the new
        columns + index without erroring, and the ORM must then be able to
        list pending items without a 'no such column' OperationalError."""
        from sqlalchemy import inspect
        from magi_cp.cloud.db import HitlRepo, make_engine, init_schema
        engine = make_engine("sqlite:///:memory:")
        self._build_pre_pr3_table(engine)
        # Sanity check: PR3 columns are NOT yet present.
        cols = {c["name"] for c in inspect(engine).get_columns("hitl_item")}
        assert "subject" not in cols
        assert "payload_hash" not in cols
        # Run the upgrade.
        init_schema(engine)
        # PR3 columns appear; legacy data is preserved untouched.
        cols = {c["name"] for c in inspect(engine).get_columns("hitl_item")}
        assert "subject" in cols
        assert "payload_hash" in cols
        idx = {ix["name"] for ix in inspect(engine).get_indexes("hitl_item")}
        assert "ix_hitl_subject_status" in idx
        assert "ix_hitl_matter_status" in idx
        # Round-trip the legacy row via the ORM (this is what would crash
        # under the bug — `no such column: subject` once SQLAlchemy hydrated
        # the row).
        h = HitlRepo(engine)
        pending = h.list_pending()
        assert len(pending) == 1
        assert pending[0].matter == "LEGACY_M"
        assert pending[0].subject is None      # not backfilled yet
        assert pending[0].payload_hash is None

    def test_init_schema_is_idempotent_on_pr3_schema(self):
        """Running init_schema twice on an already-PR3 table must be a
        no-op (no errors, no spurious ALTERs)."""
        from sqlalchemy import inspect
        from magi_cp.cloud.db import make_engine, init_schema
        engine = make_engine("sqlite:///:memory:")
        init_schema(engine)        # first call: fresh build
        init_schema(engine)        # second call: idempotent upgrade
        cols = {c["name"] for c in inspect(engine).get_columns("hitl_item")}
        assert {"subject", "payload_hash", "matter", "doc_id"} <= cols
