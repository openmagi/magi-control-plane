"""P5 pack-centric runtime: boot migration + default-flip tests.

Design brief: docs/plans/2026-06-30-pack-centric-session-scoped-runtime.md
(§ "Migration" + Phase 5).

Covered here (per implementation-brief "Tests" bullet):

  1. Migration is idempotent: running it twice yields the same state
     (floor membership unchanged, tenant stamped once).
  2. Post-migration parity: the same set of policies that fired
     yesterday (per-policy ``enabled=true`` on the legacy path) fires
     today under the pack-centric path when the floor pack is the ONLY
     active set (a session with no /magi:pack activations).
  3. Legacy rollback (flag=false) still works after the migration.
"""
from __future__ import annotations

import pytest

from magi_cp.cloud.db import init_schema, make_engine
from magi_cp.cloud.pack_centric_migration import (
    migrate_tenants_to_pack_centric,
)
from magi_cp.cloud.pack_store import PackStore
from magi_cp.cloud.policy_store import PolicyStore
from magi_cp.cloud.tenants import Tenant, TenantRepo
from magi_cp.policy.floor_pack import FLOOR_PACK_ID
from magi_cp.policy.ir import EvidencePolicy, EvidenceReq, Trigger
from magi_cp.policy.resolved import PolicyOverride
from magi_cp.policy.resolver import (
    legacy_resolve_policies_for_hook,
    resolve_policies_for_hook,
)
from sqlalchemy import select
from sqlalchemy.orm import Session


# ── fixtures ─────────────────────────────────────────────────────────
def _make_policy(pid: str, *, event="PreToolUse", matcher="Bash",
                 action="block") -> EvidencePolicy:
    return EvidencePolicy(
        id=pid, description="t", version="0.1",
        trigger=Trigger(host="claude-code", event=event, matcher=matcher),
        sentinel_re=None,
        requires=[EvidenceReq(kind="step", step="citation_verify",
                              verdict="pass")],
        action=action, on_signature_invalid="deny",
        gate_binary="/usr/local/bin/magi-gate.sh",
    )


@pytest.fixture
def env(tmp_path):
    dsn = f"sqlite:///{tmp_path}/cloud.sqlite"
    engine = make_engine(dsn)
    init_schema(engine)
    policy_store = PolicyStore(path=str(tmp_path / "policies.json"))
    pack_store = PackStore(path=str(tmp_path / "packs.json"))
    return {
        "engine": engine,
        "policy_store": policy_store,
        "pack_store": pack_store,
    }


def _seed_policies(env, policies_and_flags):
    overrides = [
        PolicyOverride(policy=p, source="user", enabled=en)
        for p, en in policies_and_flags
    ]
    env["policy_store"].save(overrides)


def _floor_members(pack_store) -> list[str]:
    for row in pack_store.load():
        if row.is_floor:
            return list(row.policy_ids)
    return []


def _tenant_stamp(engine, tenant_id) -> int | None:
    with Session(engine) as s:
        t = s.get(Tenant, tenant_id)
        return None if t is None else t.pack_centric_migrated_at


# ── 1. migration moves enabled policies into the floor pack ──────────
def test_migration_moves_only_enabled_into_floor(env):
    TenantRepo(env["engine"]).create(tenant_id="tenant-a")
    _seed_policies(env, [
        (_make_policy("a", matcher="Bash"), True),
        (_make_policy("b", matcher="Read"), False),   # disabled: excluded
        (_make_policy("c", matcher="Write"), True),
    ])

    migrated = migrate_tenants_to_pack_centric(
        env["engine"], env["policy_store"], env["pack_store"], now=1000,
    )

    assert migrated == ["tenant-a"]
    # Only the enabled ids land in the floor; disabled "b" is excluded.
    assert sorted(_floor_members(env["pack_store"])) == ["a", "c"]
    assert _tenant_stamp(env["engine"], "tenant-a") == 1000


def test_migration_does_not_touch_enabled_bit(env):
    TenantRepo(env["engine"]).create(tenant_id="tenant-a")
    _seed_policies(env, [
        (_make_policy("a"), True),
        (_make_policy("b", matcher="Read"), False),
    ])
    migrate_tenants_to_pack_centric(
        env["engine"], env["policy_store"], env["pack_store"], now=1000,
    )
    # Decision: leave the per-policy enabled bit intact so a rollback is
    # byte-identical. The store still reports b as disabled, a as enabled.
    by_id = {o.policy.id: o.enabled for o in env["policy_store"].load()}
    assert by_id == {"a": True, "b": False}


# ── 2. idempotency ───────────────────────────────────────────────────
def test_migration_is_idempotent(env):
    TenantRepo(env["engine"]).create(tenant_id="tenant-a")
    _seed_policies(env, [
        (_make_policy("a"), True),
        (_make_policy("c", matcher="Write"), True),
    ])

    first = migrate_tenants_to_pack_centric(
        env["engine"], env["policy_store"], env["pack_store"], now=1000,
    )
    floor_after_first = sorted(_floor_members(env["pack_store"]))

    # Second run: already-stamped tenant is skipped; floor unchanged.
    second = migrate_tenants_to_pack_centric(
        env["engine"], env["policy_store"], env["pack_store"], now=2000,
    )
    floor_after_second = sorted(_floor_members(env["pack_store"]))

    assert first == ["tenant-a"]
    assert second == []                       # nothing re-migrated
    assert floor_after_first == floor_after_second == ["a", "c"]
    # Stamp stays at the first-run value (never re-stamped).
    assert _tenant_stamp(env["engine"], "tenant-a") == 1000


def test_migration_idempotent_even_with_membership_overlap(env):
    """A second run whose enabled set already lives in the floor must be
    a pure no-op even if the tenant stamp were somehow cleared."""
    TenantRepo(env["engine"]).create(tenant_id="tenant-a")
    _seed_policies(env, [(_make_policy("a"), True)])
    migrate_tenants_to_pack_centric(
        env["engine"], env["policy_store"], env["pack_store"], now=1000,
    )
    # Clear the stamp to force the loop to re-enter the floor populate.
    with Session(env["engine"]) as s:
        t = s.get(Tenant, "tenant-a")
        t.pack_centric_migrated_at = None
        s.commit()
    migrate_tenants_to_pack_centric(
        env["engine"], env["policy_store"], env["pack_store"], now=3000,
    )
    # No duplicate "a" appended.
    assert _floor_members(env["pack_store"]) == ["a"]


# ── 3. synthetic default tenant (empty tenants table) ────────────────
def test_migration_seeds_default_tenant_when_table_empty(env):
    """Legacy single-tenant install: no persisted tenant row. The
    migration seeds a synthetic ``default`` tenant so the shared store's
    floor still gets populated and the flipped-on gate keeps firing."""
    _seed_policies(env, [(_make_policy("a"), True)])

    migrated = migrate_tenants_to_pack_centric(
        env["engine"], env["policy_store"], env["pack_store"], now=1000,
    )

    assert migrated == ["default"]
    assert _floor_members(env["pack_store"]) == ["a"]
    assert _tenant_stamp(env["engine"], "default") == 1000
    # Re-run: default is now stamped, so nothing re-migrates and no
    # second synthetic row is created.
    again = migrate_tenants_to_pack_centric(
        env["engine"], env["policy_store"], env["pack_store"], now=2000,
    )
    assert again == []
    with Session(env["engine"]) as s:
        assert list(s.execute(select(Tenant.id)).scalars()) == ["default"]


# ── 3b. durable audit ledger provenance ──────────────────────────────
def test_migration_writes_durable_audit_ledger_entry(env):
    """A schema-mutating migration that unions ids into the floor pack
    must leave a durable, append-only ledger record of exactly which ids
    moved for which tenant, so 'which policies migrated when' is
    answerable from canonical truth (not mutable floor state)."""
    from magi_cp.cloud.db import LedgerRepo

    TenantRepo(env["engine"]).create(tenant_id="tenant-a")
    _seed_policies(env, [
        (_make_policy("a", matcher="Bash"), True),
        (_make_policy("b", matcher="Read"), False),   # disabled: excluded
        (_make_policy("c", matcher="Write"), True),
    ])
    migrate_tenants_to_pack_centric(
        env["engine"], env["policy_store"], env["pack_store"], now=1000,
    )

    entries = [
        e for e in LedgerRepo(env["engine"]).list_all()
        if e.matter == "pack_centric_migration"
    ]
    assert len(entries) == 1
    body = entries[0].body
    assert body["tenant_id"] == "tenant-a"
    assert body["source"] == "p5_boot_migration"
    # The record captures the EXACT ids appended (a, c), not the full
    # enabled-set count, and not the excluded disabled id "b".
    assert sorted(body["appended_policy_ids"]) == ["a", "c"]
    assert body["floor_pack_id"] is not None


def test_migration_audits_synthetic_default_tenant_autoprovision(env):
    """When the tenants table is empty the migration auto-provisions the
    synthetic ``default`` row; that origin must be recorded so an auditor
    can distinguish it from a genuinely provisioned tenant."""
    from magi_cp.cloud.db import LedgerRepo

    _seed_policies(env, [(_make_policy("a"), True)])
    migrate_tenants_to_pack_centric(
        env["engine"], env["policy_store"], env["pack_store"], now=1000,
    )
    subjects = [e.matter for e in LedgerRepo(env["engine"]).list_all()]
    assert "tenant_autoprovisioned" in subjects
    assert "pack_centric_migration" in subjects


# ── 4. multiple tenants ──────────────────────────────────────────────
def test_migration_stamps_every_pending_tenant(env):
    TenantRepo(env["engine"]).create(tenant_id="tenant-a")
    TenantRepo(env["engine"]).create(tenant_id="tenant-b")
    _seed_policies(env, [(_make_policy("a"), True)])

    migrated = migrate_tenants_to_pack_centric(
        env["engine"], env["policy_store"], env["pack_store"], now=1000,
    )
    assert sorted(migrated) == ["tenant-a", "tenant-b"]
    assert _tenant_stamp(env["engine"], "tenant-a") == 1000
    assert _tenant_stamp(env["engine"], "tenant-b") == 1000


# ── 5. post-migration parity (floor-only session == legacy) ──────────
def test_post_migration_floor_only_fires_same_as_legacy(env):
    """The heart of zero-downtime: after migrating, a session with NO
    activated packs (floor is the only active set) resolves the exact
    same policy list the legacy per-policy ``enabled`` path did."""
    TenantRepo(env["engine"]).create(tenant_id="tenant-a")
    policies = [
        (_make_policy("a", matcher="Bash"), True),
        (_make_policy("b", matcher="Bash"), False),   # disabled yesterday
        (_make_policy("c", matcher="Read"), True),     # other hook
        (_make_policy("d", matcher="Bash"), True),
    ]
    _seed_policies(env, policies)
    overrides = env["policy_store"].load()

    # Yesterday: legacy path on the PreToolUse/Bash hook.
    legacy_out = legacy_resolve_policies_for_hook(
        overrides, event="PreToolUse", matcher="Bash",
    )

    # Migrate, then resolve today under the pack-centric path with the
    # floor pack as the only active set (no /magi:pack activations).
    migrate_tenants_to_pack_centric(
        env["engine"], env["policy_store"], env["pack_store"], now=1000,
    )
    floor_members = _floor_members(env["pack_store"])
    member_lookup = {FLOOR_PACK_ID: floor_members}

    pack_out = resolve_policies_for_hook(
        session_id="s-fresh",
        tenant_id="tenant-a",
        event="PreToolUse",
        matcher="Bash",
        overrides=overrides,
        active_packs=[],                       # session made no /magi:pack calls
        floor_pack_id=FLOOR_PACK_ID,
        pack_member_lookup=lambda pid: member_lookup.get(pid, []),
        cloud_setting=True,                    # force pack-centric ON
    )

    # Same set fires. "a" + "d" match Bash; "b" was disabled (excluded
    # from the floor by the migration); "c" is a different matcher.
    assert [p.id for p in legacy_out] == ["a", "d"]
    assert sorted(p.id for p in pack_out) == ["a", "d"]


# ── 6. legacy rollback still works after migration ───────────────────
def test_legacy_rollback_still_works_after_migration(env, monkeypatch):
    """With the flag rolled back to a falsy value, the resolver takes the
    legacy branch and fires on the per-policy ``enabled`` bit, ignoring
    the floor pack the migration populated."""
    monkeypatch.setenv("MAGI_CP_PACK_CENTRIC_RUNTIME", "0")
    TenantRepo(env["engine"]).create(tenant_id="tenant-a")
    _seed_policies(env, [
        (_make_policy("a", matcher="Bash"), True),
        (_make_policy("b", matcher="Bash"), False),
    ])
    overrides = env["policy_store"].load()
    migrate_tenants_to_pack_centric(
        env["engine"], env["policy_store"], env["pack_store"], now=1000,
    )

    # Flag OFF (explicit rollback): the resolver ignores active_packs /
    # floor and uses the enabled bit. Only "a" fires; "b" is disabled.
    out = resolve_policies_for_hook(
        session_id="s",
        tenant_id="tenant-a",
        event="PreToolUse",
        matcher="Bash",
        overrides=overrides,
        active_packs=[FLOOR_PACK_ID],          # ignored under flag-OFF
        floor_pack_id=FLOOR_PACK_ID,           # ignored
        pack_member_lookup=lambda pid: ["a", "b"],  # ignored
    )
    assert [p.id for p in out] == ["a"]
