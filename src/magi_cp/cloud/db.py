"""SQLAlchemy models for cloud state.

Three tables:
  - ledger_entry: append-only hash-chained evidence ledger (canonical truth)
  - hitl_item:    review queue + decisions
  - issued_token: optional index of issued tokens by matter+doc (audit lookups)

Production note: SQLite is for dev / single-node. Switch to Postgres via DSN
in `make_engine`. Append-only is enforced at the API surface (no UPDATE/DELETE
methods on LedgerRepo); a malicious admin with DB write access can still
violate that, which the hash chain detects.
"""
from __future__ import annotations
import enum
import hashlib
import json
import time
from typing import Any

from sqlalchemy import (
    BigInteger, Index, Integer, JSON, Enum as SAEnum, Engine, String, Text,
    UniqueConstraint, create_engine, event, select, text,
)
from sqlalchemy.dialects.postgresql import JSONB

# v2.0-W8a: prefer PostgreSQL's JSONB (binary, indexable) over generic JSON
# when running on PG. JSON variant fallback covers SQLite + MySQL dev paths.
# Existing data in the JSON column reads correctly under JSONB after migration
# (PG silently converts on read; explicit `ALTER COLUMN … TYPE jsonb USING
# … ::jsonb` is the right step for production migrations from older
# deploys — out of scope here since init_schema is create-all).
JsonCol = JSON().with_variant(JSONB, "postgresql")

# SQLite needs INTEGER (its ROWID alias) for autoincrement; Postgres needs BIGINT.
# This variant covers both: BIGINT on Postgres/MySQL, INTEGER on SQLite.
BigInt = BigInteger().with_variant(Integer, "sqlite")
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


# ── ORM base + tables ────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


class LedgerEntry(Base):
    """One global hash chain. `prev` is UNIQUE — this is the cross-worker race
    guard for chain integrity: two concurrent appends that read the same tail
    head will both try to insert the same `prev`, the loser hits IntegrityError
    and retries against the new tail. asyncio.Lock is a within-process
    fast-path on top of this DB-level invariant.

    Genesis row has prev="" — declared as UNIQUE("" allowed once).

    v2.0-W6a Phase 2: `tenant_id` scopes the chain VIEW per tenant. The
    underlying chain remains globally append-only (cross-tenant tampering
    still detectable), but reads filter by tenant_id so tenant A cannot see
    tenant B's entries.
    """
    __tablename__ = "ledger_entry"
    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tenant_id: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False, default="default",
    )
    matter: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    prev: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    body: Mapped[dict] = mapped_column(JsonCol, nullable=False)
    token: Mapped[str] = mapped_column(Text, nullable=False)
    h: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    __table_args__ = (UniqueConstraint("prev", name="uq_ledger_prev"),)


class HitlStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class HitlItem(Base):
    __tablename__ = "hitl_item"
    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    ts_created: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ts_decided: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # v2.0-W6a Phase 2: scope queue items to their owning tenant.
    tenant_id: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False, default="default",
    )
    matter: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    doc_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JsonCol, nullable=False)
    # native_enum=False: store as VARCHAR + CHECK so future status additions
    # are 1-line migrations (Postgres native ENUM is painful to ALTER).
    status: Mapped[HitlStatus] = mapped_column(
        SAEnum(HitlStatus, native_enum=False), nullable=False,
        default=HitlStatus.pending, index=True)
    approver: Mapped[str | None] = mapped_column(String(256), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    __mapper_args__ = {"version_id_col": version}   # optimistic lock on concurrent decide
    __table_args__ = (Index("ix_hitl_matter_status", "matter", "status"),)


# ── engine ───────────────────────────────────────────────────────────
def make_engine(dsn: str = "sqlite:///./magi-cp.sqlite") -> Engine:
    from sqlalchemy.pool import StaticPool
    kwargs: dict[str, Any] = {"future": True}
    if dsn.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in dsn:
            kwargs["poolclass"] = StaticPool
    engine = create_engine(dsn, **kwargs)
    # SQLite WAL: concurrent reads + serialized writes without "database is locked"
    # under load. No-op on Postgres.
    if dsn.startswith("sqlite") and ":memory:" not in dsn:
        @event.listens_for(engine, "connect")
        def _enable_wal(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()
    return engine


def init_schema(engine: Engine) -> None:
    # Lazy import so tenants module registers its tables (Tenant, ApiKey)
    # on Base.metadata before create_all runs. Without this import,
    # init_schema would only create ledger_entry + hitl_item.
    from . import tenants as _tenants_module  # noqa: F401
    from . import signups as _signups_module  # noqa: F401
    Base.metadata.create_all(engine)


# ── ledger ───────────────────────────────────────────────────────────
def _canonical(body: dict) -> str:
    return json.dumps(body, sort_keys=True, ensure_ascii=False)


def _chain_hash(prev: str, body: dict, token: str) -> str:
    return hashlib.sha256(
        (prev + "|" + _canonical(body) + "|" + token).encode("utf-8")
    ).hexdigest()


class LedgerRepo:
    def __init__(self, engine: Engine):
        self.engine = engine

    def append(self, *, matter: str, body: dict, token: str,
               max_retries: int = 5,
               tenant_id: str = "default") -> LedgerEntry:
        """Append a new entry to the global hash chain.

        Race protection: UNIQUE(prev) constraint at DB level. If two writers
        race against the same tail, one commits, the other hits IntegrityError
        and retries against the fresh tail. Works across uvicorn workers and
        K8s replicas — the asyncio.Lock in the API layer is just a fast path.

        v2.0-W6a Phase 2: `tenant_id` scopes per-tenant VIEWS via list_by_tenant.
        The chain remains globally append-only — `prev` still links across
        tenants so cross-tenant tampering is still detectable by verify_chain.
        """
        from sqlalchemy.exc import IntegrityError
        for attempt in range(max_retries):
            with Session(self.engine) as s:
                last = s.scalar(select(LedgerEntry).order_by(LedgerEntry.id.desc()).limit(1))
                prev = last.h if last else ""
                entry = LedgerEntry(
                    ts=int(time.time()), tenant_id=tenant_id,
                    matter=matter, prev=prev,
                    body=body, token=token, h=_chain_hash(prev, body, token),
                )
                s.add(entry)
                try:
                    s.commit()
                except IntegrityError:
                    s.rollback()
                    if attempt == max_retries - 1:
                        raise
                    continue
                s.refresh(entry)
                s.expunge(entry)
                return entry
        raise RuntimeError("ledger append exhausted retries (unreachable)")

    def list_all(self) -> list[LedgerEntry]:
        with Session(self.engine) as s:
            rows = list(s.scalars(select(LedgerEntry).order_by(LedgerEntry.id)))
            for r in rows:
                s.expunge(r)
            return rows

    def list_by_tenant(self, tenant_id: str) -> list[LedgerEntry]:
        """Per-tenant ledger view. Order preserved (by id ascending)."""
        with Session(self.engine) as s:
            rows = list(s.scalars(
                select(LedgerEntry).where(LedgerEntry.tenant_id == tenant_id)
                .order_by(LedgerEntry.id)
            ))
            for r in rows: s.expunge(r)
            return rows

    def list_by_matter(self, matter: str) -> list[LedgerEntry]:
        with Session(self.engine) as s:
            rows = list(s.scalars(
                select(LedgerEntry).where(LedgerEntry.matter == matter).order_by(LedgerEntry.id)
            ))
            for r in rows: s.expunge(r)
            return rows

    def verify_chain(self) -> bool:
        prev = ""
        for entry in self.list_all():
            if entry.prev != prev:
                return False
            if entry.h != _chain_hash(entry.prev, entry.body, entry.token):
                return False
            prev = entry.h
        return True



# ── HITL queue ───────────────────────────────────────────────────────
class HitlRepo:
    def __init__(self, engine: Engine):
        self.engine = engine

    def enqueue(self, *, matter: str, doc_id: str, reason: str, payload: dict,
                tenant_id: str = "default") -> HitlItem:
        # Mirror tenant_id into the payload too — the dashboard reads the
        # payload to render and our HITL detail endpoint already returns
        # the payload verbatim, so this gives reviewer dashboards a stable
        # filter key without an API change.
        scoped_payload = {**payload, "tenant_id": tenant_id}
        with Session(self.engine) as s:
            item = HitlItem(ts_created=int(time.time()),
                            tenant_id=tenant_id,
                            matter=matter, doc_id=doc_id,
                            reason=reason, payload=scoped_payload,
                            status=HitlStatus.pending)
            s.add(item); s.commit(); s.refresh(item); s.expunge(item)
            return item

    def get(self, item_id: int) -> HitlItem | None:
        with Session(self.engine) as s:
            item = s.get(HitlItem, item_id)
            if item: s.expunge(item)
            return item

    def list_pending(self) -> list[HitlItem]:
        with Session(self.engine) as s:
            rows = list(s.scalars(
                select(HitlItem).where(HitlItem.status == HitlStatus.pending)
                .order_by(HitlItem.id)
            ))
            for r in rows: s.expunge(r)
            return rows

    def list_pending_by_tenant(self, tenant_id: str) -> list[HitlItem]:
        with Session(self.engine) as s:
            rows = list(s.scalars(
                select(HitlItem)
                .where(HitlItem.status == HitlStatus.pending,
                        HitlItem.tenant_id == tenant_id)
                .order_by(HitlItem.id)
            ))
            for r in rows: s.expunge(r)
            return rows

    def _decide(self, item_id: int, *, new_status: HitlStatus, approver: str,
                note: str | None) -> None:
        """Concurrent-approve-safe via SQLAlchemy optimistic locking
        (version_id_col on HitlItem). Two concurrent _decide calls observe
        the same version; the loser's commit raises StaleDataError."""
        from sqlalchemy.orm.exc import StaleDataError
        with Session(self.engine) as s:
            # SELECT FOR UPDATE on Postgres (no-op on SQLite which serializes writes)
            stmt = select(HitlItem).where(HitlItem.id == item_id).with_for_update()
            item = s.scalar(stmt)
            if item is None:
                raise ValueError(f"hitl item {item_id} not found")
            if item.status != HitlStatus.pending:
                raise ValueError(f"hitl item {item_id} already {item.status.value}")
            item.status = new_status
            item.approver = approver
            item.note = note
            item.ts_decided = int(time.time())
            try:
                s.commit()
            except StaleDataError:
                raise ValueError(f"hitl item {item_id} concurrently modified")

    def approve(self, item_id: int, *, approver: str, note: str | None = None) -> None:
        self._decide(item_id, new_status=HitlStatus.approved, approver=approver, note=note)

    def reject(self, item_id: int, *, approver: str, note: str | None = None) -> None:
        self._decide(item_id, new_status=HitlStatus.rejected, approver=approver, note=note)
