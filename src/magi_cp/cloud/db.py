"""SQLAlchemy models for cloud state.

Three tables:
  - ledger_entry: append-only hash-chained evidence ledger (canonical truth)
  - hitl_item:    review queue + decisions
  - issued_token: optional index of issued tokens by subject+payload_hash
                  (audit lookups)

Production note: SQLite is for dev / single-node. Switch to Postgres via DSN
in `make_engine`. Append-only is enforced at the API surface (no UPDATE/DELETE
methods on LedgerRepo); a malicious admin with DB write access can still
violate that, which the hash chain detects.

PR4 — legacy keying drop:
  HitlItem's `matter` / `doc_id` columns are removed by
  `scripts/migrate_pr4_drop_legacy.py`. Only `subject` + `payload_hash`
  remain. The migration refuses to run if any HITL row still has
  `subject IS NULL` (would lose data).

  LedgerEntry keeps its `matter` column name at the DB level — that rename
  is a deeper migration deferred. The wire / API surface only exposes
  `subject` now (the column is read-only from the ORM as `matter` for
  compatibility with the hash-chain genesis row).
"""
from __future__ import annotations
import enum
import hashlib
import json
import secrets
import time
from typing import Any

from sqlalchemy import (
    BigInteger, Index, Integer, JSON, Enum as SAEnum, Engine, String, Text,
    UniqueConstraint, create_engine, event, select, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

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
    # D52c follow-up: composite (tenant_id, id) covers the paginated
    # tenant-scoped read path (`WHERE tenant_id = :t AND id > :sid
    # ORDER BY id LIMIT :n`); composite (tenant_id, ts) covers the
    # since_secs window on /ledger/count. The standalone tenant_id
    # index above is kept for back-compat with existing dashboards
    # that still call list_by_tenant() without a cursor.
    __table_args__ = (
        UniqueConstraint("prev", name="uq_ledger_prev"),
        Index("ix_ledger_tenant_id_id", "tenant_id", "id"),
        Index("ix_ledger_tenant_id_ts", "tenant_id", "ts"),
    )


class EndpointHeartbeat(Base):
    """P10 — endpoint attestation.

    The gate POSTs a heartbeat every N minutes with a sha256 digest of
    the managed-settings JSON it has loaded. The dashboard renders
    "cloud-active vs endpoint-confirmed" off this table so an authored
    policy that never reached an endpoint is visible.

    `endpoint_id` is opaque; the gate reads it from
    `~/.config/magi-cp/env` (operator-set). PRIMARY KEY enforces one
    row per endpoint — the gate UPSERTs on each heartbeat.

    `tenant_id` scopes the row to the owning tenant so the dashboard
    doesn't surface cross-tenant endpoints.

    Issue #1 P0 (#1): trust model is honest TOFU-over-tenant-key. The
    cloud cannot prove the gate is actually enforcing the digest it
    claims — it can only confirm the gate (or anyone holding the
    tenant API key) submitted that digest at this timestamp. The
    `signed_attestation` column reserves space for a future Ed25519
    attestation bound to a per-endpoint enrollment keypair; until
    that is wired the column stays NULL and the dashboard labels
    heartbeats as "claimed", not "confirmed".
    """
    __tablename__ = "endpoint_heartbeat"
    endpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False, default="default",
    )
    last_seen: Mapped[int] = mapped_column(BigInteger, nullable=False)
    active_policy_digest: Mapped[str | None] = mapped_column(String(64),
                                                              nullable=True)
    agent_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Issue #1 P0 (#1): optional Ed25519(endpoint_id|ts|nonce|digest)
    # signature. Persisted opaquely; verification logic is gated on the
    # endpoint having an enrolled pubkey (future PR). Schema additive
    # so today's gates stay compatible.
    signed_attestation: Mapped[str | None] = mapped_column(
        String(256), nullable=True,
    )
    last_nonce: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CompiledPolicySnapshot(Base):
    """Issue #1 P0 (#2) + non-blocking #b — compiled-policy history.

    Stores (digest, ts, tenant_id, policy_set) for every distinct
    compile the cloud has emitted. The dashboard joins
    `endpoint_heartbeat.active_policy_digest` against this table to
    label gates as `confirmed` / `stale-policy` / `unknown` — without
    it, a digest matching an older but recently-rolled-back compile
    looks identical to a digest the cloud never authored (malicious or
    drifted gate).

    Append-only at the API surface; an operator cleaning up stale
    rows runs the dedicated `magi-cp-cloud snapshot prune` CLI."""
    __tablename__ = "compiled_policy_snapshot"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False, default="default",
    )
    ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Compact JSON of the policy id set (not the bytes — reproducible
    # from `policies.json` and the deterministic compiler).
    policy_ids: Mapped[list] = mapped_column(JsonCol, nullable=False)


class SessionActivePacks(Base):
    """P1 pack-centric runtime: per-session active pack list.

    Design brief: docs/plans/2026-06-30-pack-centric-session-scoped-runtime.md
    (§ "Session-state store").

    One row per CC session per tenant. ``pack_ids`` carries the packs
    the operator has activated for THIS session, ordered by activation
    time (oldest first). The tenant's floor pack is NOT stored here —
    Phase 2's gate resolution unions the floor in at read time so a
    schema-level edit to which pack is the floor is picked up without
    rewriting every row.

    Timestamps
    ~~~~~~~~~~
    ``activated_at`` — first successful activate in this session; not
    refreshed on subsequent activates.
    ``last_seen_at`` — refreshed on every read + write. The GC sweep
    (Phase 5, not built in P1) uses this + ``expires_at`` to prune
    dead sessions.
    ``expires_at`` — extended to ``now + 30d`` on every activate. This
    is a GC TTL, NOT the activation lifetime (per decision 5: activation
    persists until session end or explicit `/magi:pack:deactivate`).

    Concurrency: writes serialize via the API-layer lock; SQLite's WAL
    + short transaction gives us safe reads. Postgres pushes concurrent
    activates through the standard row lock.
    """
    __tablename__ = "session_active_packs"
    session_id: Mapped[str] = mapped_column(
        String(128), primary_key=True,
    )
    tenant_id: Mapped[str] = mapped_column(
        String(64), primary_key=True,
    )
    pack_ids: Mapped[list] = mapped_column(JsonCol, nullable=False)
    activated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_seen_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    __table_args__ = (
        Index(
            "ix_session_active_packs_tenant_expires",
            "tenant_id", "expires_at",
        ),
    )


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
    # PR4: canonical keying. `subject` = generic subject identifier
    # (e.g. "session_abc", "req_xyz", or for legal verticals a matter id);
    # `payload_hash` = sha256 of the canonical tool payload (or for legal:
    # doc_id).
    #
    # Width: 128 so callers can use either a bare 64-char hex digest OR a
    # prefixed form (`sha256-<64hex>` = 71 chars). The request-time
    # validators in `cloud/app.py` still pin the wire shape to 64 chars
    # via pydantic, this is the storage ceiling.
    #
    # The PR3 legacy columns (`matter`, `doc_id`) are dropped by
    # `scripts/migrate_pr4_drop_legacy.py`. After the migration only
    # canonical keys exist on this table. We keep the columns nullable
    # for the brief window before the migration runs on already-deployed
    # instances — `_pr4_apply_migrations` enforces NOT NULL at runtime
    # once the legacy columns have been dropped.
    subject: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
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
    __table_args__ = (
        Index("ix_hitl_subject_status", "subject", "status"),
    )


class SharedRun(Base):
    """A public run-share link: a redacted run view served by an opaque token.

    Only the token's sha256 is stored (the cleartext token is the URL secret,
    returned once at creation), mirroring ``ApiKey`` in ``tenants.py``. The
    stored ``view`` is the already-redacted ``openmagi.runView.v1`` projection.
    """
    __tablename__ = "shared_run"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    view: Mapped[dict] = mapped_column(JsonCol, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInt, nullable=False)
    expires_at: Mapped[int | None] = mapped_column(BigInt, nullable=True)
    revoked_at: Mapped[int | None] = mapped_column(BigInt, nullable=True)
    # Owner edits (range / hidden / redactions) applied over `view` at read time.
    # Non-destructive: `view` stays the full export; null = no edits.
    edits: Mapped[dict | None] = mapped_column(JsonCol, nullable=True)


class SharedRunRepo:
    """Create + read public run-share links. Token is minted here and only its
    hash persisted; ``get_active`` enforces revoke + expiry."""

    def __init__(self, engine: Engine):
        self.engine = engine

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create(self, *, tenant_id: str, view: dict, ttl_seconds: int | None = None) -> str:
        """Persist a redacted view and return the cleartext token (shown once)."""
        token = secrets.token_urlsafe(24)
        now = int(time.time())
        row = SharedRun(
            token_hash=self._hash(token),
            tenant_id=tenant_id,
            view=view,
            created_at=now,
            expires_at=(now + ttl_seconds) if ttl_seconds and ttl_seconds > 0 else None,
        )
        with Session(self.engine) as s:
            s.add(row)
            s.commit()
        return token

    def get_active(self, token: str) -> SharedRun | None:
        """Return the row for a token if it exists, is not revoked, and not expired."""
        with Session(self.engine) as s:
            row = s.get(SharedRun, self._hash(token))
            if row is None or row.revoked_at is not None:
                return None
            if row.expires_at is not None and row.expires_at <= int(time.time()):
                return None
            s.expunge(row)
            return row

    def revoke(self, token: str) -> bool:
        with Session(self.engine) as s:
            row = s.get(SharedRun, self._hash(token))
            if row is None or row.revoked_at is not None:
                return False
            row.revoked_at = int(time.time())
            s.commit()
            return True

    def list_by_tenant(self, tenant_id: str) -> list[SharedRun]:
        """All share links a tenant created, newest first. For the manage UI.

        The cleartext token is never returned (only its hash is stored), so the
        UI shows metadata + revoke, not the link itself (the link is shown once
        at creation time)."""
        with Session(self.engine) as s:
            rows = list(s.scalars(
                select(SharedRun)
                .where(SharedRun.tenant_id == tenant_id)
                .order_by(SharedRun.created_at.desc())
            ))
            for r in rows:
                s.expunge(r)
            return rows

    def get_by_hash(self, token_hash: str, tenant_id: str) -> SharedRun | None:
        """Tenant-scoped fetch by hash for the owner's editor (returns the FULL
        un-edited view + current edits). None if missing or cross-tenant."""
        with Session(self.engine) as s:
            row = s.get(SharedRun, token_hash)
            if row is None or row.tenant_id != tenant_id:
                return None
            s.expunge(row)
            return row

    def set_edits(self, token_hash: str, tenant_id: str, edits: dict | None) -> bool:
        """Store the owner's edits overlay. False if missing/cross-tenant/revoked."""
        with Session(self.engine) as s:
            row = s.get(SharedRun, token_hash)
            if row is None or row.tenant_id != tenant_id or row.revoked_at is not None:
                return False
            row.edits = edits
            s.commit()
            return True

    def revoke_by_hash(self, token_hash: str, tenant_id: str) -> bool:
        """Tenant-scoped revoke by token hash (the handle the manage UI has).

        Returns False if the row is missing, already revoked, or owned by a
        different tenant (no cross-tenant revoke)."""
        with Session(self.engine) as s:
            row = s.get(SharedRun, token_hash)
            if row is None or row.tenant_id != tenant_id or row.revoked_at is not None:
                return False
            row.revoked_at = int(time.time())
            s.commit()
            return True


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
    Base.metadata.create_all(engine)
    # PR3/PR4: idempotent in-place DDL upgrade for already-deployed
    # instances. `create_all` is `CREATE TABLE IF NOT EXISTS` only — it
    # never adds new columns/indexes to an existing table. Without this
    # step, any pre-PR3 deployment that pulls PR4 code keeps the old
    # schema (missing the `subject`/`payload_hash` columns +
    # `ix_hitl_subject_status` index) and the first /hitl read crashes
    # with `no such column: subject`.
    _apply_migrations(engine)


def _apply_migrations(engine: Engine) -> None:
    """Idempotently bring a pre-PR3 `hitl_item` table up to the PR3 shape.

    The DROP-COLUMN step that finally removes the legacy `matter` /
    `doc_id` columns is in `scripts/migrate_pr4_drop_legacy.py` — that
    script is the explicit cut-over operators run after the backfill
    has populated `subject` / `payload_hash` on every row. `init_schema`
    here only handles the additive PR3 steps.

    Three steps, all skipped when already applied:

      1. ADD COLUMN `subject` VARCHAR(128) NULL
      2. ADD COLUMN `payload_hash` VARCHAR(128) NULL
      3. CREATE INDEX `ix_hitl_subject_status` ON hitl_item(subject, status)

    Plus, on Postgres only:

      4. ALTER COLUMN `matter` DROP NOT NULL
      5. ALTER COLUMN `doc_id` DROP NOT NULL

    SQLite cannot DROP NOT NULL without a table rebuild — but SQLite paths
    almost always create fresh via `create_all`, which already declares the
    columns nullable in step 1's wake.

    Designed to be safe to call on every app startup — every operation is
    `IF NOT EXISTS`-shaped at the dialect level, or guarded by an
    `inspect(engine)` lookup beforehand.
    """
    from sqlalchemy import inspect as _inspect
    insp = _inspect(engine)
    # shared_run.edits: additive, nullable. A pre-edits deployment pulling this
    # code keeps the table from create_all (no new column) and the first edit
    # write would fail with `no such column: edits` without this.
    if "shared_run" in insp.get_table_names():
        if "edits" not in {c["name"] for c in insp.get_columns("shared_run")}:
            col_type = "JSONB" if engine.dialect.name == "postgresql" else "TEXT"
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE shared_run ADD COLUMN edits {col_type}"))
    if "hitl_item" not in insp.get_table_names():
        # Fresh DB — create_all just built the table from the PR4-shape
        # ORM declaration, nothing to migrate.
        return
    existing_cols = {c["name"] for c in insp.get_columns("hitl_item")}
    existing_idx = {ix["name"] for ix in insp.get_indexes("hitl_item")}
    dialect = engine.dialect.name

    with engine.begin() as conn:
        if "subject" not in existing_cols:
            conn.execute(text(
                "ALTER TABLE hitl_item ADD COLUMN subject VARCHAR(128)"
            ))
        if "payload_hash" not in existing_cols:
            conn.execute(text(
                "ALTER TABLE hitl_item ADD COLUMN payload_hash VARCHAR(128)"
            ))
        if "ix_hitl_subject_status" not in existing_idx:
            # Both SQLite and Postgres accept the IF NOT EXISTS form.
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_hitl_subject_status "
                "ON hitl_item (subject, status)"
            ))
        if dialect == "postgresql":
            # Drop NOT NULL on legacy columns so callers can supply only
            # the canonical pair. No-op when already nullable or when the
            # columns have already been dropped by PR4.
            for legacy_col in ("matter", "doc_id"):
                if legacy_col in existing_cols:
                    conn.execute(text(
                        f"ALTER TABLE hitl_item "
                        f"ALTER COLUMN {legacy_col} DROP NOT NULL"
                    ))


# Back-compat alias — older imports referenced `_pr3_apply_migrations`
# directly. Keep the symbol pointing at the renamed helper.
_pr3_apply_migrations = _apply_migrations


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

    def append(self, *, subject: str, body: dict, token: str,
               max_retries: int = 5,
               tenant_id: str = "default") -> LedgerEntry:
        """Append a new entry to the global hash chain.

        PR4: kwarg renamed from `matter=` to `subject=`. The underlying DB
        column is still named `matter` (deeper rename deferred to a
        future PR — the hash-chain genesis row is keyed on that column
        name in operator backups). Internally we still write to
        `LedgerEntry.matter`.

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
                    matter=subject, prev=prev,
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
            for r in rows:
                s.expunge(r)
            return rows

    def list_by_tenant_page(
        self,
        tenant_id: str,
        *,
        since_id: int = 0,
        limit: int = 100,
        verifier: list[str] | None = None,
    ) -> list[LedgerEntry]:
        """D52c follow-up: paginated per-tenant ledger read.

        Pushes `since_id`, `verifier`-filter and `LIMIT` into the SQL
        layer so the database does the skipping. Old code path scanned
        every tenant row into Python on each request; this method
        scans only the page-sized window via the `(tenant_id, id)`
        index.

        `verifier` filter on `body['step']`: Postgres uses the JSONB
        text path operator (`body->>'step'`); SQLite uses
        `json_extract(body, '$.step')`. The fallback for non-PG/
        non-SQLite dialects is the in-Python filter on the
        already-paginated rows (correct but defeats the index).
        """
        wanted = [v for v in (verifier or []) if v]
        with Session(self.engine) as s:
            stmt = select(LedgerEntry).where(
                LedgerEntry.tenant_id == tenant_id,
                LedgerEntry.id > since_id,
            )
            dialect = self.engine.dialect.name
            if wanted:
                stmt = self._apply_step_filter(stmt, wanted, dialect)
            stmt = stmt.order_by(LedgerEntry.id).limit(limit)
            rows = list(s.scalars(stmt))
            for r in rows:
                s.expunge(r)
            if wanted and dialect not in ("postgresql", "sqlite"):
                # Fallback: filter in Python when dialect lacks a JSON
                # path operator. Correctness > index efficiency here.
                wanted_set = set(wanted)
                rows = [
                    r for r in rows
                    if isinstance(r.body, dict)
                    and r.body.get("step") in wanted_set
                ]
            return rows

    def count_by_tenant(
        self,
        tenant_id: str,
        *,
        verifier: list[str] | None = None,
        since_ts: int | None = None,
    ) -> int:
        """D52c follow-up: COUNT(*) for the verifier-emissions widget.

        Replaces the body-hydrating `list_by_tenant` scan with a
        single `SELECT COUNT(*)` so the dashboard fan-out is cheap.
        """
        from sqlalchemy import func
        with Session(self.engine) as s:
            stmt = select(func.count(LedgerEntry.id)).where(
                LedgerEntry.tenant_id == tenant_id,
            )
            if since_ts is not None:
                stmt = stmt.where(LedgerEntry.ts >= since_ts)
            wanted = [v for v in (verifier or []) if v]
            dialect = self.engine.dialect.name
            if wanted and dialect in ("postgresql", "sqlite"):
                stmt = self._apply_step_filter(stmt, wanted, dialect)
                return int(s.scalar(stmt) or 0)
            if not wanted:
                return int(s.scalar(stmt) or 0)
            # Dialect lacks a JSON path operator. Fall back to
            # hydrating + Python-filtering. Keeps correctness on
            # MySQL etc.; the supported dev/test/prod targets
            # (sqlite/postgres) take the indexed fast path above.
            rows = list(s.scalars(
                select(LedgerEntry).where(
                    LedgerEntry.tenant_id == tenant_id,
                )
            ))
            wanted_set = set(wanted)
            return sum(
                1 for r in rows
                if (since_ts is None or r.ts >= since_ts)
                and isinstance(r.body, dict)
                and r.body.get("step") in wanted_set
            )

    def list_recent_by_verifier(
        self,
        tenant_id: str,
        *,
        verifier: str,
        limit: int,
        since_ts: int | None = None,
    ) -> list[LedgerEntry]:
        """D53a: most-recent N rows for a single verifier, tenant scoped.

        Powers the "Recent emissions samples" inline list on the verifier
        catalog expander. Ordered DESC by id so the caller renders newest
        first (matching the operator's read order).

        `verifier` is the step name (e.g. `citation_verify`,
        `inline_regex`); empty / falsy returns `[]` so an unknown verifier
        is a clean empty list, not a 404 at the API surface above.

        `since_ts` (optional) bounds the window the same way the count
        endpoints do. The brief's default is 24h; the route layer passes
        `now - 86400` when the caller doesn't override.
        """
        if not verifier:
            return []
        limit = max(1, min(int(limit), 25))
        dialect = self.engine.dialect.name
        if dialect not in ("postgresql", "sqlite"):
            # The step filter is pushed into SQL only on the two
            # supported dialects. A silent Python-side post-filter on
            # the head 25 rows would produce a worse failure mode
            # (operator sees "no samples" for a verifier that has
            # plenty, just outside the head window). Surface the
            # misconfiguration loudly so it's caught at deploy time
            # rather than misread as a redaction or auth bug.
            raise NotImplementedError(
                f"list_recent_by_verifier requires postgresql or sqlite; "
                f"got dialect={dialect!r}",
            )
        with Session(self.engine) as s:
            stmt = select(LedgerEntry).where(
                LedgerEntry.tenant_id == tenant_id,
            )
            if since_ts is not None:
                stmt = stmt.where(LedgerEntry.ts >= since_ts)
            stmt = self._apply_step_filter(stmt, [verifier], dialect)
            stmt = stmt.order_by(LedgerEntry.id.desc()).limit(limit)
            rows = list(s.scalars(stmt))
            for r in rows:
                s.expunge(r)
            return rows

    def list_recent_window(
        self,
        tenant_id: str,
        *,
        limit: int,
        since_ts: int | None = None,
    ) -> list[LedgerEntry]:
        """D53b: most-recent N rows for a tenant inside an optional
        time window. Ordered DESC by id so callers (the policy
        dry-run replay) see newest first - matching the dashboard's
        read order and giving deterministic `sample_matched`
        selection without a post-fetch sort.

        Unlike `list_recent_by_verifier` this method does NOT push a
        step filter; the dry-run replay re-runs the proposed IR's
        requires[] in Python against the row body, so a SQL-side
        filter would prematurely narrow the window. `limit` is
        clamped server-side at the route layer (cap=10_000) to keep
        the replay bounded.
        """
        limit = max(1, int(limit))
        with Session(self.engine) as s:
            stmt = select(LedgerEntry).where(
                LedgerEntry.tenant_id == tenant_id,
            )
            if since_ts is not None:
                stmt = stmt.where(LedgerEntry.ts >= since_ts)
            stmt = stmt.order_by(LedgerEntry.id.desc()).limit(limit)
            rows = list(s.scalars(stmt))
            for r in rows:
                s.expunge(r)
            return rows

    def counts_by_step(
        self,
        tenant_id: str,
        *,
        steps: list[str],
        since_ts: int | None = None,
    ) -> dict[str, int]:
        """D52c follow-up: batched per-step count for the dashboard
        fan-out on the Rules → Verifiers tab.

        Single GROUP BY query returns {step: count} for every step in
        `steps` (missing keys → 0 added by the caller). The previous
        fan-out issued one /ledger/count HTTP call per verifier; this
        method does it in one SQL round-trip.
        """
        if not steps:
            return {}
        from sqlalchemy import func
        out: dict[str, int] = {s_: 0 for s_ in steps if s_}
        if not out:
            return {}
        wanted = list(out.keys())
        dialect = self.engine.dialect.name
        with Session(self.engine) as sess:
            if dialect == "postgresql":
                step_expr = LedgerEntry.body["step"].astext  # type: ignore[index]
            elif dialect == "sqlite":
                step_expr = func.json_extract(LedgerEntry.body, "$.step")
            else:
                # Fallback: hydrate + python aggregate. Single pass.
                stmt = select(LedgerEntry).where(
                    LedgerEntry.tenant_id == tenant_id,
                )
                if since_ts is not None:
                    stmt = stmt.where(LedgerEntry.ts >= since_ts)
                rows = list(sess.scalars(stmt))
                wanted_set = set(wanted)
                for r in rows:
                    if not isinstance(r.body, dict):
                        continue
                    step = r.body.get("step")
                    if step in wanted_set:
                        out[step] = out.get(step, 0) + 1
                return out
            stmt = (
                select(step_expr, func.count(LedgerEntry.id))
                .where(
                    LedgerEntry.tenant_id == tenant_id,
                    step_expr.in_(wanted),
                )
                .group_by(step_expr)
            )
            if since_ts is not None:
                stmt = stmt.where(LedgerEntry.ts >= since_ts)
            for step, n in sess.execute(stmt).all():
                if isinstance(step, str):
                    out[step] = int(n)
        return out

    @staticmethod
    def _apply_step_filter(stmt, wanted: list[str], dialect: str):
        """Push `body['step'] IN (...)` into SQL.

        Postgres → `body->>'step'` (JSONB text accessor).
        SQLite   → `json_extract(body, '$.step')`.
        Other dialects fall back to in-Python filtering at the call site.
        """
        from sqlalchemy import func
        if dialect == "postgresql":
            return stmt.where(
                LedgerEntry.body["step"].astext.in_(wanted)  # type: ignore[index]
            )
        if dialect == "sqlite":
            return stmt.where(
                func.json_extract(LedgerEntry.body, "$.step").in_(wanted)
            )
        return stmt

    def list_by_subject(self, subject: str) -> list[LedgerEntry]:
        """PR4 canonical name. The underlying DB column is still `matter`
        (deeper rename deferred) — this method queries it under the
        canonical wire vocabulary."""
        with Session(self.engine) as s:
            rows = list(s.scalars(
                select(LedgerEntry).where(LedgerEntry.matter == subject)
                .order_by(LedgerEntry.id)
            ))
            for r in rows:
                s.expunge(r)
            return rows

    def verify_chain(self) -> bool:
        """Walk the global hash chain.

        Streamed iteration via the ORM (no `list_all()` materialisation)
        so verify on a large chain stays bounded in memory. Order is
        primary-key ascending; first mismatch returns False immediately.
        """
        prev = ""
        with Session(self.engine) as s:
            rows = s.scalars(
                select(LedgerEntry).order_by(LedgerEntry.id)
            )
            for entry in rows:
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

    def enqueue(self, *,
                reason: str, payload: dict,
                subject: str,
                payload_hash: str,
                tenant_id: str = "default") -> HitlItem:
        """Enqueue a HITL review item.

        PR4: legacy `matter`/`doc_id` kwargs removed. Callers MUST supply
        the canonical (subject, payload_hash) pair. Neither may be None —
        we never silently insert with NULL for either, that would defeat
        the `ix_hitl_subject_status` index and confuse audit lookups.
        """
        if subject is None or payload_hash is None:
            raise ValueError(
                "HitlRepo.enqueue requires subject and payload_hash"
            )
        # Mirror tenant_id into the payload too — the dashboard reads the
        # payload to render and our HITL detail endpoint already returns
        # the payload verbatim, so this gives reviewer dashboards a stable
        # filter key without an API change.
        scoped_payload = {**payload, "tenant_id": tenant_id}
        with Session(self.engine) as s:
            item = HitlItem(
                ts_created=int(time.time()),
                tenant_id=tenant_id,
                subject=subject, payload_hash=payload_hash,
                reason=reason, payload=scoped_payload,
                status=HitlStatus.pending,
            )
            s.add(item)
            s.commit()
            s.refresh(item)
            s.expunge(item)
            return item

    def get(self, item_id: int) -> HitlItem | None:
        with Session(self.engine) as s:
            item = s.get(HitlItem, item_id)
            if item:
                s.expunge(item)
            return item

    def list_pending(self) -> list[HitlItem]:
        with Session(self.engine) as s:
            rows = list(s.scalars(
                select(HitlItem).where(HitlItem.status == HitlStatus.pending)
                .order_by(HitlItem.id)
            ))
            for r in rows:
                s.expunge(r)
            return rows

    def list_pending_by_tenant(self, tenant_id: str) -> list[HitlItem]:
        with Session(self.engine) as s:
            rows = list(s.scalars(
                select(HitlItem)
                .where(HitlItem.status == HitlStatus.pending,
                        HitlItem.tenant_id == tenant_id)
                .order_by(HitlItem.id)
            ))
            for r in rows:
                s.expunge(r)
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


# ── P10: endpoint heartbeat repo ─────────────────────────────────────
DEFAULT_STALE_ENDPOINT_SECONDS = 24 * 3600


def stale_endpoint_threshold_seconds() -> int:
    """Issue #1 P1 (#18): operator-tunable stale threshold.

    Reads `MAGI_CP_STALE_ENDPOINT_SECONDS` and returns a positive
    integer; falls back to `DEFAULT_STALE_ENDPOINT_SECONDS` (24h) on
    unset / invalid / non-positive. Pure function so the dashboard
    server-side renderer + the API both read the same value without
    drifting.
    """
    import os as _os
    raw = _os.environ.get("MAGI_CP_STALE_ENDPOINT_SECONDS")
    if not raw:
        return DEFAULT_STALE_ENDPOINT_SECONDS
    try:
        v = int(raw)
    except ValueError:
        return DEFAULT_STALE_ENDPOINT_SECONDS
    if v <= 0:
        return DEFAULT_STALE_ENDPOINT_SECONDS
    return v


# Back-compat: existing callers expect a module constant.
STALE_ENDPOINT_SECONDS = DEFAULT_STALE_ENDPOINT_SECONDS


class EndpointHeartbeatRepo:
    """Upsert / list endpoint heartbeats.

    Concurrent-safe via SQLAlchemy MERGE pattern: read-then-insert with
    PK collision → UPDATE. Postgres ON CONFLICT and SQLite INSERT OR
    REPLACE both work via the merge() helper Sqlalchemy provides; we
    use plain session.merge() for portability."""

    def __init__(self, engine: Engine):
        self.engine = engine

    def beat(self, *, endpoint_id: str, tenant_id: str,
             active_policy_digest: str | None,
             agent_version: str | None = None,
             label: str | None = None,
             signed_attestation: str | None = None,
             nonce: str | None = None) -> EndpointHeartbeat:
        with Session(self.engine) as s:
            row = EndpointHeartbeat(
                endpoint_id=endpoint_id,
                tenant_id=tenant_id,
                last_seen=int(time.time()),
                active_policy_digest=active_policy_digest,
                agent_version=agent_version,
                label=label,
                signed_attestation=signed_attestation,
                last_nonce=nonce,
            )
            merged = s.merge(row)
            s.commit()
            s.refresh(merged)
            s.expunge(merged)
            return merged

    def list_by_tenant(self, tenant_id: str) -> list[EndpointHeartbeat]:
        with Session(self.engine) as s:
            rows = list(s.scalars(
                select(EndpointHeartbeat)
                .where(EndpointHeartbeat.tenant_id == tenant_id)
                .order_by(EndpointHeartbeat.endpoint_id)
            ))
            for r in rows:
                s.expunge(r)
            return rows

    def get(self, endpoint_id: str) -> EndpointHeartbeat | None:
        with Session(self.engine) as s:
            row = s.get(EndpointHeartbeat, endpoint_id)
            if row:
                s.expunge(row)
            return row


def is_stale(hb: EndpointHeartbeat, *, now: int | None = None,
             threshold_s: int | None = None) -> bool:
    """True iff the heartbeat is older than the configured threshold.

    Issue #1 P1 (#18): threshold defaults to
    `stale_endpoint_threshold_seconds()` so tightening
    `MAGI_CP_STALE_ENDPOINT_SECONDS` takes effect without code changes.
    Explicit override (used by tests) bypasses the env lookup.

    Pure function so the dashboard renderer can reuse it for the
    red-flag styling without re-implementing the threshold."""
    cur = now if now is not None else int(time.time())
    thr = threshold_s if threshold_s is not None else stale_endpoint_threshold_seconds()
    return (cur - int(hb.last_seen)) > thr


class CompiledPolicySnapshotRepo:
    """Issue #1 P0 (#2) + non-blocking #b — snapshot history repo.

    `record(digest, policy_ids)` is idempotent (the digest is the
    primary key, no-op on duplicate). `known_digests_for_tenant()` lets
    the dashboard classify endpoint digests as confirmed-current,
    confirmed-historical, or unknown.
    """

    def __init__(self, engine: Engine):
        self.engine = engine

    def record(self, *, digest: str, tenant_id: str,
                policy_ids: list[str]) -> None:
        with Session(self.engine) as s:
            existing = s.get(CompiledPolicySnapshot, digest)
            if existing is not None:
                # Idempotent — the same digest must always describe the
                # same policy set (deterministic compile).
                return
            s.add(CompiledPolicySnapshot(
                digest=digest, tenant_id=tenant_id,
                ts=int(time.time()), policy_ids=list(policy_ids),
            ))
            s.commit()

    def known_digests_for_tenant(self, tenant_id: str) -> set[str]:
        with Session(self.engine) as s:
            rows = list(s.scalars(
                select(CompiledPolicySnapshot.digest)
                .where(CompiledPolicySnapshot.tenant_id == tenant_id)
            ))
            return set(rows)


# ── P1 pack-centric runtime: session-state store ──────────────────────
# Design brief: docs/plans/2026-06-30-pack-centric-session-scoped-runtime.md
#
# 30-day TTL for the GC sweep (decision 5 — activation lifetime is NOT
# TTL-driven; this bound is only how long an orphaned session sits in
# the store before the sweep can prune it).
SESSION_ACTIVE_PACK_TTL_SECONDS = 30 * 24 * 3600


class SessionActivePacksRepo:
    """CRUD for the per-session active-pack list.

    Exposes:

      - ``get(session_id, tenant_id)`` — read only; does NOT refresh
        ``last_seen_at``. Use ``touch()`` when the caller wants the row
        surfaced through a read-tracked codepath.
      - ``touch(session_id, tenant_id)`` — refresh ``last_seen_at`` on
        an existing row, no-op when the row is missing. Cheap idempotent
        read used by the GET endpoint.
      - ``activate(session_id, tenant_id, pack_id)`` — append the pack
        id if not already active. Extends ``expires_at`` by
        ``SESSION_ACTIVE_PACK_TTL_SECONDS``. Returns
        ``(row, changed)``: ``changed=False`` on idempotent no-op.
      - ``deactivate(session_id, tenant_id, pack_id)`` — remove the
        pack id if present. Returns ``(row, changed)`` — ``row=None``
        when the session row is missing (still idempotent).

    Concurrency: the API layer holds a per-request asyncio.Lock around
    activate + deactivate so two concurrent slash-commands cannot
    interleave a read + write on the same session_id. Rows are keyed
    by ``(session_id, tenant_id)`` so cross-tenant leakage is
    impossible even under a lock miss.
    """

    def __init__(self, engine: Engine):
        self.engine = engine

    def get(self, session_id: str, tenant_id: str) -> SessionActivePacks | None:
        with Session(self.engine) as s:
            row = s.get(SessionActivePacks, (session_id, tenant_id))
            if row is None:
                return None
            s.expunge(row)
            return row

    def touch(self, session_id: str, tenant_id: str) -> SessionActivePacks | None:
        """Refresh ``last_seen_at`` on an existing row. No-op when the
        row is missing (never creates a phantom row on a read). Returns
        the refreshed row so callers can render without a second query.
        """
        now = int(time.time())
        with Session(self.engine) as s:
            row = s.get(SessionActivePacks, (session_id, tenant_id))
            if row is None:
                return None
            row.last_seen_at = now
            s.commit()
            s.refresh(row)
            s.expunge(row)
            return row

    def activate(
        self, session_id: str, tenant_id: str, pack_id: str,
    ) -> tuple[SessionActivePacks, bool]:
        """Append ``pack_id`` to the session's active list.

        Idempotent: an already-active pack returns ``changed=False``
        and does NOT extend ``expires_at`` (extending on a no-op would
        let a chatty gate keep sessions alive forever). A real activate
        (new pack) does extend.

        On first activate for the session, seeds ``activated_at``
        alongside ``last_seen_at`` / ``expires_at``.
        """
        now = int(time.time())
        with Session(self.engine) as s:
            row = s.get(SessionActivePacks, (session_id, tenant_id))
            if row is None:
                row = SessionActivePacks(
                    session_id=session_id,
                    tenant_id=tenant_id,
                    pack_ids=[pack_id],
                    activated_at=now,
                    last_seen_at=now,
                    expires_at=now + SESSION_ACTIVE_PACK_TTL_SECONDS,
                )
                s.add(row)
                s.commit()
                s.refresh(row)
                s.expunge(row)
                return row, True
            current = list(row.pack_ids or [])
            if pack_id in current:
                # Idempotent no-op. Refresh last_seen_at only — see
                # docstring: TTL extension is reserved for real changes.
                row.last_seen_at = now
                s.commit()
                s.refresh(row)
                s.expunge(row)
                return row, False
            current.append(pack_id)
            row.pack_ids = current
            row.last_seen_at = now
            row.expires_at = now + SESSION_ACTIVE_PACK_TTL_SECONDS
            s.commit()
            s.refresh(row)
            s.expunge(row)
            return row, True

    def deactivate(
        self, session_id: str, tenant_id: str, pack_id: str,
    ) -> tuple[SessionActivePacks | None, bool]:
        """Remove ``pack_id`` from the session's active list. Idempotent
        for absent ids (returns ``changed=False``).

        Row retention: even when the active list becomes empty we KEEP
        the row so ``last_seen_at`` records that the session was here.
        Phase 5's GC sweep is the authoritative pruner.
        """
        now = int(time.time())
        with Session(self.engine) as s:
            row = s.get(SessionActivePacks, (session_id, tenant_id))
            if row is None:
                return None, False
            current = list(row.pack_ids or [])
            if pack_id not in current:
                row.last_seen_at = now
                s.commit()
                s.refresh(row)
                s.expunge(row)
                return row, False
            current.remove(pack_id)
            row.pack_ids = current
            row.last_seen_at = now
            s.commit()
            s.refresh(row)
            s.expunge(row)
            return row, True
