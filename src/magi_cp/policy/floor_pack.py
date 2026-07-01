"""P1 pack-centric runtime: floor-pack seeder.

Design brief: docs/plans/2026-06-30-pack-centric-session-scoped-runtime.md
(§ "Floor pack" + decisions 6 + 7).

The floor pack is the tenant's "always-on" pack. Every session union-in
the floor before evaluating its own activated packs (Phase 2 wires the
gate). Kevin's decisions locked here:

  - 6. Floor pack ships EMPTY (no policies). Migration will populate it
       later; do not force materialization on any existing tenant that
       has zero policies.
  - 7. Floor pack CANNOT be deactivated. Its membership is editable,
       but the always-on bit is server-locked (nothing in this module
       ever flips ``is_floor`` back to False).

Concurrency: ``ensure_floor_pack`` accepts an optional asyncio.Lock so
the caller can serialize the read + write against other pack-store
mutations. The lock is optional (no-op when omitted) so tests and
single-tenant scripts don't have to manage async plumbing.

Tenant scoping caveat: today's ``PackStore`` is a single JSON file per
install. The beta ships single-tenant (Kevin decision 8) so "at most
one floor pack per tenant" reduces to "at most one floor pack per
install". The ``tenant_id`` parameter is retained for the API-layer
handshake so Phase 5's DB-backed multi-tenant migration is drop-in.
"""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — type-only imports
    from ..cloud.pack_store import PackStore


# Canonical id of the seeded floor pack. Kept stable so the dashboard
# can hard-code the ALWAYS-ON badge without a lookup.
FLOOR_PACK_SLUG = "floor"
FLOOR_PACK_ID = f"user-pack/{FLOOR_PACK_SLUG}"
FLOOR_PACK_NAME = "Floor"
FLOOR_PACK_DESCRIPTION = (
    "Always-on policy pack. Every session unions in the floor before "
    "evaluating its own activated packs."
)


def _find_floor(rows: list) -> "object | None":
    """Return the first row with ``is_floor=True`` or None. Written to
    tolerate legacy rows that were saved before the ``is_floor`` field
    existed (``getattr`` defaults to False).
    """
    for row in rows:
        if getattr(row, "is_floor", False):
            return row
    return None


def ensure_floor_pack(
    tenant_id: str,
    pack_store: "PackStore",
    pack_store_lock: asyncio.Lock | None = None,
) -> str:
    """Return the tenant's floor pack id, creating an empty one lazily
    when it does not exist yet.

    Idempotent: subsequent calls return the same id without mutating
    the store. The seeded pack has zero policies (decision 6) and
    ``is_floor=True`` (decision 7). Repeated calls under contention are
    safe: ``PackStore.save`` enforces the at-most-one-floor invariant
    on write.

    Async note: the async endpoints hold ``pack_store_lock`` around
    read + write to prevent an interleaved second seed. This helper is
    intentionally sync (matches every other pack_store call site); the
    caller manages the lock via ``async with`` before invoking.

    ``tenant_id`` is accepted for API-shape stability (Phase 5's
    multi-tenant migration keys the seed decision on it) but the
    current single-tenant store ignores it — see module docstring.
    """
    del tenant_id  # retained for signature stability; see docstring.
    del pack_store_lock  # caller manages the lock; see docstring.
    rows = pack_store.load()
    existing = _find_floor(rows)
    if existing is not None:
        return existing.id
    # Import here so ``policy/floor_pack.py`` stays a leaf module — the
    # cloud/pack_store.py import path pulls sqlalchemy transitively via
    # cloud/db.py, which we do not want at module import time.
    from ..cloud.pack_store import UserPackRow
    new_row = UserPackRow(
        id=FLOOR_PACK_ID,
        name=FLOOR_PACK_NAME,
        description=FLOOR_PACK_DESCRIPTION,
        policy_ids=[],
        is_floor=True,
    )
    rows.append(new_row)
    pack_store.save(rows)
    return FLOOR_PACK_ID


async def ensure_floor_pack_async(
    tenant_id: str,
    pack_store: "PackStore",
    pack_store_lock: asyncio.Lock | None = None,
) -> str:
    """Async wrapper around :func:`ensure_floor_pack`.

    Holds ``pack_store_lock`` for the read+write critical section so a
    concurrent activate on the SAME session cannot race two seeds
    against the pack store. Returns the same floor pack id.

    ``nullcontext`` fallback lets callers pass ``None`` when no lock is
    wired (tests, one-shot scripts) without a branchy call site.
    """
    guard = pack_store_lock if pack_store_lock is not None else nullcontext()
    if isinstance(guard, asyncio.Lock):
        async with guard:
            return ensure_floor_pack(tenant_id, pack_store)
    return ensure_floor_pack(tenant_id, pack_store)


__all__ = [
    "FLOOR_PACK_SLUG",
    "FLOOR_PACK_ID",
    "FLOOR_PACK_NAME",
    "FLOOR_PACK_DESCRIPTION",
    "ensure_floor_pack",
    "ensure_floor_pack_async",
]
