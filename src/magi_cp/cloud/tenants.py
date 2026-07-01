"""v2.0-W6a — tenants + per-tenant API keys.

Two tables, both ORM-backed and joined to the existing `Base` so init_schema
picks them up. Auth flow:

  client → X-Api-Key: mcp_<24chars> → authenticate_request(engine, key)
    → SHA-256(key) → api_keys lookup → JOIN tenants
    → AuthOk(tenant_id, status) if active+non-revoked else None

Legacy single-tenant flow (existing tests, single-tenant deployments):
  MAGI_CP_API_KEY env value → maps to a synthetic "default" tenant. This
  branch runs BEFORE the DB lookup so a misconfigured DB doesn't break
  existing setups.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass

from sqlalchemy import (
    BigInteger, Engine, ForeignKey, String, UniqueConstraint, select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base, BigInt


# ── tables ─────────────────────────────────────────────────────────
class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    plan: Mapped[str] = mapped_column(String(32), nullable=False, default="free")
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    suspended_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # P5 pack-centric runtime: epoch-seconds stamp set the first time the
    # boot migration moved this tenant's enabled policies into its floor
    # pack. NULL means "not yet migrated"; the migration keys idempotency
    # on this column so it never re-runs for a tenant. Additive + nullable
    # so a pre-P5 DB reads unchanged after the `init_schema` DDL upgrade.
    # Design brief: docs/plans/2026-06-30-pack-centric-session-scoped-runtime.md
    pack_centric_migrated_at: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True,
    )
    # Codex runtime adapter: which coding-agent runtime this tenant's
    # gate speaks. Defaults to ``claude-code`` so every existing tenant
    # is unaffected; the dashboard runtime picker (P4) flips it per
    # tenant, gated globally by MAGI_CP_CODEX_RUNTIME_ENABLED. Additive +
    # non-null with a server default so a pre-adapter DB reads unchanged
    # after the init_schema DDL upgrade.
    # Design brief: docs/plans/2026-06-30-codex-runtime-adapter-design.md
    runtime_id: Mapped[str] = mapped_column(
        String(32), nullable=False, default="claude-code",
        server_default="claude-code",
    )


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True,
    )
    hashed_key: Mapped[str] = mapped_column(String(64), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_used_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    revoked_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    __table_args__ = (
        UniqueConstraint("hashed_key", name="uq_api_keys_hashed_key"),
    )


# ── dataclasses ────────────────────────────────────────────────────
@dataclass
class TenantRecord:
    id: str
    status: str
    plan: str
    expires_at: int | None


@dataclass
class IssuedKey:
    id: int
    tenant_id: str
    cleartext: str   # returned ONCE; caller must surface to user immediately
    prefix: str


@dataclass
class ListedKey:
    id: int
    tenant_id: str
    prefix: str
    created_at: int
    last_used_at: int | None
    revoked_at: int | None


@dataclass
class AuthOk:
    tenant_id: str
    status: str
    api_key_id: int | None   # None for legacy env-key path


# ── helpers ────────────────────────────────────────────────────────
_KEY_PREFIX = "mcp_"
_KEY_ENTROPY_BYTES = 15   # base32-encoded → 24 chars
_PREFIX_DISPLAY_LEN = 8


def _hash_key(cleartext: str) -> str:
    return hashlib.sha256(cleartext.encode("utf-8")).hexdigest()


def _new_key_cleartext() -> str:
    import base64
    raw = secrets.token_bytes(_KEY_ENTROPY_BYTES)
    body = base64.b32encode(raw).decode("ascii").rstrip("=").lower()
    return f"{_KEY_PREFIX}{body}"


# ── repos ──────────────────────────────────────────────────────────
class TenantRepo:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create(self, *, tenant_id: str, plan: str = "free",
               expires_at: int | None = None) -> TenantRecord:
        with Session(self.engine) as s:
            t = Tenant(
                id=tenant_id, status="active", plan=plan,
                created_at=int(time.time()), expires_at=expires_at,
            )
            s.add(t)
            s.commit()
            return TenantRecord(t.id, t.status, t.plan, t.expires_at)

    def get(self, tenant_id: str) -> TenantRecord | None:
        with Session(self.engine) as s:
            t = s.get(Tenant, tenant_id)
            if t is None:
                return None
            return TenantRecord(t.id, t.status, t.plan, t.expires_at)

    def suspend(self, tenant_id: str, *, reason: str) -> None:
        with Session(self.engine) as s:
            t = s.get(Tenant, tenant_id)
            if t is None:
                raise KeyError(tenant_id)
            t.status = "suspended"
            t.suspended_reason = reason
            s.commit()

    def reactivate(self, tenant_id: str) -> None:
        with Session(self.engine) as s:
            t = s.get(Tenant, tenant_id)
            if t is None:
                raise KeyError(tenant_id)
            t.status = "active"
            t.suspended_reason = None
            s.commit()

    def set_plan(self, tenant_id: str, *, plan: str,
                  expires_at: int | None = None) -> None:
        with Session(self.engine) as s:
            t = s.get(Tenant, tenant_id)
            if t is None:
                raise KeyError(tenant_id)
            t.plan = plan
            if expires_at is not None:
                t.expires_at = expires_at
            s.commit()


class ApiKeyRepo:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def issue(self, *, tenant_id: str) -> IssuedKey:
        cleartext = _new_key_cleartext()
        hashed = _hash_key(cleartext)
        prefix = cleartext[:_PREFIX_DISPLAY_LEN]
        with Session(self.engine) as s:
            k = ApiKey(
                tenant_id=tenant_id, hashed_key=hashed, prefix=prefix,
                created_at=int(time.time()),
            )
            s.add(k)
            s.commit()
            s.refresh(k)
            return IssuedKey(id=k.id, tenant_id=tenant_id,
                              cleartext=cleartext, prefix=prefix)

    def revoke(self, key_id: int) -> None:
        with Session(self.engine) as s:
            k = s.get(ApiKey, key_id)
            if k is None:
                raise KeyError(key_id)
            k.revoked_at = int(time.time())
            s.commit()

    def list_for_tenant(self, tenant_id: str) -> list[ListedKey]:
        with Session(self.engine) as s:
            rows = s.execute(
                select(ApiKey).where(ApiKey.tenant_id == tenant_id)
            ).scalars().all()
            return [
                ListedKey(
                    id=k.id, tenant_id=k.tenant_id, prefix=k.prefix,
                    created_at=k.created_at, last_used_at=k.last_used_at,
                    revoked_at=k.revoked_at,
                )
                for k in rows
            ]

    def authenticate(self, cleartext: str) -> AuthOk | None:
        """Public alias for direct testing / admin tooling."""
        return self._authenticate_db_key(cleartext)

    def _authenticate_db_key(self, cleartext: str) -> AuthOk | None:
        """DB-backed key lookup. Returns None on miss / revoked / suspended."""
        hashed = _hash_key(cleartext)
        with Session(self.engine) as s:
            row = s.execute(
                select(ApiKey, Tenant)
                .join(Tenant, Tenant.id == ApiKey.tenant_id)
                .where(ApiKey.hashed_key == hashed)
            ).first()
            if row is None:
                return None
            key, tenant = row
            if key.revoked_at is not None:
                return None
            if tenant.status != "active":
                return None
            # Update last_used_at best-effort; never block auth on this write.
            try:
                key.last_used_at = int(time.time())
                s.commit()
            except Exception:
                pass
            return AuthOk(tenant_id=tenant.id, status=tenant.status,
                          api_key_id=key.id)


# ── auth surface ───────────────────────────────────────────────────
def authenticate_request(engine: Engine, presented: str | None) -> AuthOk | None:
    """Resolve a presented X-Api-Key to an AuthOk or None.

    Order:
      1. Legacy env path — MAGI_CP_API_KEY matches → synthetic "default" tenant.
      2. DB path — hash + lookup in api_keys joined to tenants.

    Constant-time compare on the env key prevents timing oracle on misconfig.
    """
    if not presented:
        return None
    env_key = os.environ.get("MAGI_CP_API_KEY") or ""
    if env_key and hmac.compare_digest(env_key, presented):
        return AuthOk(tenant_id="default", status="active", api_key_id=None)
    repo = ApiKeyRepo(engine)
    return repo._authenticate_db_key(presented)


__all__ = [
    "Tenant", "ApiKey",
    "TenantRecord", "IssuedKey", "ListedKey", "AuthOk",
    "TenantRepo", "ApiKeyRepo",
    "authenticate_request",
]
