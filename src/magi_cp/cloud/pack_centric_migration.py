"""P5 pack-centric runtime: boot-time enabled-policy -> floor-pack migration.

Design brief: 2026-06-30-pack-centric-session-scoped-runtime (private planning repo)
(§ "Migration" + Phase 5 rollout row).

Phase 5 flips ``MAGI_CP_PACK_CENTRIC_RUNTIME`` on by default. The
pack-centric gate resolution ignores the per-policy ``enabled`` bit and
only fires policies that belong to an active pack (the tenant's floor
pack + any session-activated packs). To keep the flip zero-downtime,
every policy that fired *yesterday* (``enabled=true``) must still fire
*today*. This migration guarantees that by moving each tenant's enabled
policy ids into its floor pack once, at cloud boot.

Locked semantics (design doc "Migration"):

  1. Ensure the floor pack exists (``ensure_floor_pack``; ships empty).
  2. For every policy with ``enabled=true``, add its id to the floor
     pack's member list, idempotently (check membership before append).
  3. Do NOT touch the policy's ``enabled`` bit. The flipped-on gate
     ignores it anyway, and leaving it intact keeps a rollback to the
     legacy path (flag=false) byte-identical.
  4. Stamp ``tenants.pack_centric_migrated_at`` so a re-boot never
     re-runs the migration for an already-migrated tenant.

Idempotency has two layers: the per-tenant DB stamp (skips whole
tenants) AND the membership check (skips already-present ids). Running
this twice yields the same state.

Single-tenant beta caveat (decision 8): today's ``PackStore`` /
``PolicyStore`` are one JSON file per install, not tenant-scoped. So
"for each tenant" collapses to "migrate the shared store once per
unmigrated tenant row". The membership check makes repeated store
mutation a no-op, so multiple tenant rows sharing one store converge
safely. When Phase 5's DB-backed multi-tenant migration lands, swap the
shared ``policy_store`` / ``pack_store`` for per-tenant stores keyed on
``tenant.id``. The loop shape stays identical.

Synthetic default tenant: a legacy single-tenant install authenticates
via ``MAGI_CP_API_KEY`` and never persists a ``tenants`` row (the
"default" tenant is synthetic). When the table is empty we seed that
"default" row so the shared store still gets its floor populated and the
flipped-on gate keeps firing. We only do this when the table is
completely empty, so a multi-tenant deploy whose rows are all already
migrated is left untouched.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from ..policy.floor_pack import ensure_floor_pack
from .tenants import Tenant

if TYPE_CHECKING:  # pragma: no cover (type-only imports)
    from .pack_store import PackStore
    from .policy_store import PolicyStore


_LOG = logging.getLogger("magi_cp.pack_centric_migration")

# Tenant id used for the synthetic single-tenant install (matches the
# ``MAGI_CP_API_KEY`` legacy auth path in ``tenants.authenticate_request``).
_DEFAULT_TENANT_ID = "default"


def _enabled_policy_ids(policy_store: "PolicyStore") -> list[str]:
    """Return the ids of every ``enabled=true`` policy, de-duplicated,
    preserving first-seen order.

    Best-effort: a malformed policy store must never crash cloud boot,
    so a load failure degrades to "no enabled policies" with a logged
    warning rather than aborting the migration.
    """
    try:
        overrides = policy_store.load()
    except Exception:  # pragma: no cover (defensive)
        _LOG.exception(
            "pack-centric migration: policy store failed to load; "
            "treating as zero enabled policies",
        )
        return []
    seen: set[str] = set()
    out: list[str] = []
    for ov in overrides:
        if not getattr(ov, "enabled", False):
            continue
        pid = ov.policy.id
        if pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
    return out


def _populate_floor(
    tenant_id: str,
    pack_store: "PackStore",
    enabled_ids: list[str],
) -> tuple[str | None, list[str]]:
    """Ensure the floor pack exists and union ``enabled_ids`` into its
    member list.

    Returns ``(floor_pack_id, appended_ids)`` where ``appended_ids`` is
    the exact list of ids newly appended on this call (empty when the
    floor already covered every enabled id — the idempotent case). The
    caller uses ``appended_ids`` for the durable audit record + log line
    so "which policies migrated when" is answerable from the ledger
    rather than from mutable floor-pack state.
    """
    floor_id = ensure_floor_pack(tenant_id, pack_store)
    rows = pack_store.load()
    floor = None
    for row in rows:
        if row.id == floor_id or getattr(row, "is_floor", False):
            floor = row
            break
    if floor is None:  # pragma: no cover (ensure_floor_pack just made it)
        return floor_id, []
    appended: list[str] = []
    for pid in enabled_ids:
        if pid not in floor.policy_ids:
            floor.policy_ids.append(pid)
            appended.append(pid)
    if appended:
        pack_store.save(rows)
    return floor_id, appended


def migrate_tenants_to_pack_centric(
    engine: Engine,
    policy_store: "PolicyStore",
    pack_store: "PackStore",
    *,
    now: int | None = None,
) -> list[str]:
    """Migrate every not-yet-migrated tenant's enabled policies into its
    floor pack and stamp ``pack_centric_migrated_at``.

    Returns the list of tenant ids migrated on this call. Idempotent: a
    second call returns ``[]`` because every tenant is now stamped.

    ``now`` overrides the epoch-seconds stamp for deterministic tests.

    The whole thing is a no-op when ``pack_store`` is not wired
    (self-host misconfig): there is nowhere to seed the floor, so the
    caller is left on whatever the legacy path already did.
    """
    if pack_store is None:
        return []
    ts = int(now if now is not None else time.time())
    enabled_ids = _enabled_policy_ids(policy_store)

    migrated: list[str] = []
    seeded_default = False
    # Per-tenant provenance captured for the durable ledger audit + the
    # human-facing log line. Records the EXACT ids appended per tenant,
    # not the size of the full enabled set (which over-counts on an
    # idempotent re-run / partially-migrated deploy).
    appended_by_tenant: dict[str, list[str]] = {}
    floor_by_tenant: dict[str, str | None] = {}

    with Session(engine) as s:
        pending = list(
            s.execute(
                select(Tenant).where(
                    Tenant.pack_centric_migrated_at.is_(None)
                )
            ).scalars()
        )
        if not pending:
            # No unmigrated rows. Either everything is migrated already
            # (leave it), or the table is empty (synthetic single-tenant
            # install). Seed the "default" row so the shared store is
            # migrated and the flipped-on gate keeps firing.
            any_row = s.execute(select(Tenant.id).limit(1)).first()
            if any_row is None:
                s.add(Tenant(
                    id=_DEFAULT_TENANT_ID,
                    status="active",
                    plan="free",
                    created_at=ts,
                    pack_centric_migrated_at=None,
                ))
                s.flush()
                seeded_default = True
                pending = list(
                    s.execute(
                        select(Tenant).where(
                            Tenant.pack_centric_migrated_at.is_(None)
                        )
                    ).scalars()
                )

        # Commit PER TENANT (not once after the whole loop) so each
        # processed tenant is either fully migrated-and-stamped or not
        # touched at all. A crash mid-loop can never leave a tenant's
        # floor file mutated but its stamp unwritten (the floor union is
        # idempotent, and the stamp lands in its own transaction right
        # after). This is the crash-safety the review lens asserts and
        # is what the per-tenant-store multi-tenant future requires.
        for tenant in pending:
            floor_id, appended = _populate_floor(
                tenant.id, pack_store, enabled_ids,
            )
            tenant.pack_centric_migrated_at = ts
            s.commit()
            migrated.append(tenant.id)
            appended_by_tenant[tenant.id] = appended
            floor_by_tenant[tenant.id] = floor_id

    # Durable audit: a schema-mutating migration that touches the live
    # policy set must leave an entry in the append-only hash-chained
    # ledger so "which policies migrated when" is answerable six months
    # later from canonical truth, not from mutable floor-pack state.
    # Best-effort: the stamps are already committed, so an audit failure
    # must not undo a successful (zero-downtime) migration; it is logged
    # loudly instead. Ledger appends are extremely rare to fail.
    if migrated:
        try:
            from .db import LedgerRepo
            ledger = LedgerRepo(engine)
            for tid in migrated:
                if seeded_default and tid == _DEFAULT_TENANT_ID:
                    # Record that this tenant row was auto-provisioned by
                    # the P5 boot migration (vs genuinely provisioned) so
                    # a later auditor can tell the two apart.
                    ledger.append(
                        subject="tenant_autoprovisioned",
                        body={
                            "tenant_id": tid,
                            "ts": ts,
                            "source": "p5_pack_centric_migration",
                        },
                        token="", tenant_id=tid,
                    )
                ledger.append(
                    subject="pack_centric_migration",
                    body={
                        "tenant_id": tid,
                        "floor_pack_id": floor_by_tenant.get(tid),
                        # Full enabled set considered + the exact delta
                        # appended to the floor on this boot.
                        "enabled_policy_ids": list(enabled_ids),
                        "appended_policy_ids": list(
                            appended_by_tenant.get(tid, [])
                        ),
                        "ts": ts,
                        "source": "p5_boot_migration",
                    },
                    token="", tenant_id=tid,
                )
        except Exception:  # pragma: no cover (defensive)
            _LOG.exception(
                "pack-centric migration: durable audit-ledger append "
                "failed after tenants were stamped; migration state is "
                "intact but provenance was not recorded",
            )

        total_appended = sum(
            len(v) for v in appended_by_tenant.values()
        )
        _LOG.info(
            "pack-centric migration: stamped %d tenant(s) %r; appended "
            "%d policy id(s) to floor pack(s): %r",
            len(migrated), migrated, total_appended, appended_by_tenant,
        )
    return migrated


__all__ = [
    "migrate_tenants_to_pack_centric",
]
