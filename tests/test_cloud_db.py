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
