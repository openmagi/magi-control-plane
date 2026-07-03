"""P2 pack-centric runtime — GET /session/{id}/resolved endpoint.

Design brief: docs/plans/2026-06-30-pack-centric-session-scoped-runtime.md
(§ "Runtime changes" + Phase 2).

The endpoint feeds the gate binary cache with a single-round-trip
envelope: active packs + floor id + folded ``policies_by_hook``.
Flag-OFF: matches the legacy per-policy ``enabled`` semantics.
Flag-ON: filters to (floor ∪ activated packs) and ignores the per-
policy ``enabled`` bit.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.keys import KeyStore
from magi_cp.cloud.pack_store import PackStore, UserPackRow
from magi_cp.cloud.policy_store import PolicyStore
from magi_cp.cloud.tenants import ApiKeyRepo, TenantRepo
from magi_cp.policy.ir import EvidencePolicy, EvidenceReq, Trigger
from magi_cp.policy.resolved import PolicyOverride


ADMIN_KEY = "res-admin-key"
LEGACY_API_KEY = "res-legacy-api-key"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", ADMIN_KEY)
    monkeypatch.setenv("MAGI_CP_API_KEY", LEGACY_API_KEY)
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", "res-hitl-key")
    # Flag OFF baseline; tests that need ON monkeypatch explicitly.
    # P5 flipped the default to ON, so we must set the explicit rollback
    # value "0" here rather than unsetting (unset now means ON).
    monkeypatch.setenv("MAGI_CP_PACK_CENTRIC_RUNTIME", "0")


def _stamp_migrated(engine, tenant_id: str, ts: int = 1) -> None:
    """Simulate a completed P5 boot migration for ``tenant_id`` by
    stamping ``pack_centric_migrated_at`` so the resolved endpoint treats
    it as pack-centric-active under the flag-ON gate."""
    from magi_cp.cloud.tenants import Tenant
    from sqlalchemy import update
    from sqlalchemy.orm import Session

    with Session(engine) as s:
        s.execute(
            update(Tenant)
            .where(Tenant.id == tenant_id)
            .values(pack_centric_migrated_at=ts)
        )
        s.commit()


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
def cloud(tmp_path):
    """Cloud + on-disk sqlite so we can seed the policy + pack stores
    directly and hit the resolver endpoint.
    """
    ks = KeyStore(dir=str(tmp_path / "keys"))
    dsn = f"sqlite:///{tmp_path}/cloud.sqlite"
    policy_path = str(tmp_path / "policies.json")
    pack_path = str(tmp_path / "packs.json")
    app = create_app(
        keystore=ks, dsn=dsn,
        policy_store_path=policy_path,
        pack_store_path=pack_path,
    )
    client = TestClient(app)
    engine = app.state.engine
    TenantRepo(engine).create(tenant_id="tenant-a")
    # P5 zero-downtime guard: the pack-centric path fires for a tenant
    # ONLY after the boot migration confirmed-populated its floor
    # (`pack_centric_migrated_at IS NOT NULL`). `create_app` does not run
    # the boot migration in tests, so stamp tenant-a directly to simulate
    # a migrated tenant for the flag-ON assertions below. Unstamped
    # fallback is covered by its own test.
    _stamp_migrated(engine, "tenant-a")
    key = ApiKeyRepo(engine).issue(tenant_id="tenant-a").cleartext
    return {
        "client": client,
        "app": app,
        "key": key,
        "policy_store": PolicyStore(path=policy_path),
        "pack_store": PackStore(path=pack_path),
        "policy_path": policy_path,
        "pack_path": pack_path,
    }


def _seed_policies(cloud, policies_and_flags):
    """Write ``policies_and_flags = [(policy, enabled), ...]`` straight to
    the store, bypassing the /policies PUT flow so tests keep small.
    """
    overrides = [
        PolicyOverride(policy=p, source="user", enabled=en)
        for p, en in policies_and_flags
    ]
    cloud["policy_store"].save(overrides)


def _seed_packs(cloud, packs):
    """packs = [(pack_id, [member_id, ...]), ...] — writes user packs."""
    rows = []
    for pid, members in packs:
        rows.append(UserPackRow(
            id=pid, name=pid, description="", policy_ids=list(members),
        ))
    cloud["pack_store"].save(rows)


# ── envelope shape + auth ────────────────────────────────────────────
def test_resolved_requires_api_key(cloud):
    r = cloud["client"].get("/session/s1/resolved")
    assert r.status_code == 401


def test_resolved_envelope_shape_on_empty_state(cloud):
    r = cloud["client"].get(
        "/session/s1/resolved",
        headers={"X-Api-Key": cloud["key"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"] == "s1"
    assert body["tenant_id"] == "tenant-a"
    assert body["active_packs"] == []
    # Under flag-OFF the endpoint MUST NOT trigger a lazy floor-pack
    # seed write (see P2 flag-neutrality fix). A first-time tenant
    # therefore sees ``floor_pack_id=None`` on this URL until either
    # the flag flips ON or another (write-legit) endpoint seeds it.
    assert body["floor_pack_id"] is None
    assert body["pack_centric_enabled"] is False
    assert body["policies_by_hook"] == []


def test_resolved_flag_off_does_not_seed_floor_or_touch_session_row(cloud):
    """P2 flag-neutrality regression: hitting this URL under flag-OFF
    must NOT write to ``session_active_packs`` and must NOT seed a
    floor pack row.

    Prior behaviour ran ``_resolve_floor(tenant_id)`` (which lazily
    seeds a floor pack row via ``ensure_floor_pack_async``) and
    ``SessionActivePacksRepo(engine).touch(session_id, tenant_id)``
    unconditionally, so a smoke probe against flag-OFF drifted the DB
    away from the "byte-identical drop-in" contract stamped on the
    commit message.
    """
    from magi_cp.cloud.db import SessionActivePacks
    from sqlalchemy.orm import Session
    from sqlalchemy import select

    # Sanity: no pack rows before the request.
    assert cloud["pack_store"].load() == []

    r = cloud["client"].get(
        "/session/probe-1/resolved",
        headers={"X-Api-Key": cloud["key"]},
    )
    assert r.status_code == 200, r.text

    # No floor pack seeded.
    assert cloud["pack_store"].load() == [], (
        "flag-OFF URL should not seed a floor pack row"
    )
    # No session_active_packs row created for the probed session.
    engine = cloud["app"].state.engine
    with Session(engine) as s:
        row = s.scalar(
            select(SessionActivePacks).where(
                SessionActivePacks.session_id == "probe-1",
            )
        )
        assert row is None, (
            "flag-OFF URL should not touch session_active_packs"
        )


# ── flag-OFF path (legacy parity) ────────────────────────────────────
def test_resolved_flag_off_returns_enabled_overrides_grouped_by_hook(cloud):
    """Under flag-OFF the envelope carries every enabled override
    grouped by (event, matcher). This is the legacy pipeline the gate
    would compute via the linear-scan today; the endpoint just
    pre-folds it for the cache.
    """
    _seed_policies(cloud, [
        (_make_policy("a", matcher="Bash"), True),
        (_make_policy("b", matcher="Bash"), False),   # disabled → drop
        (_make_policy("c", matcher="Read"), True),
    ])
    r = cloud["client"].get(
        "/session/s1/resolved",
        headers={"X-Api-Key": cloud["key"]},
    )
    body = r.json()
    # No pack activation touched; envelope still returns hook groups
    # because flag-OFF ignores active_packs.
    coord_to_ids = {
        (row["event"], row["matcher"]): [p["id"] for p in row["policies"]]
        for row in body["policies_by_hook"]
    }
    assert coord_to_ids.get(("PreToolUse", "Bash")) == ["a"]
    assert coord_to_ids.get(("PreToolUse", "Read")) == ["c"]


# ── flag-ON path (pack-centric) ──────────────────────────────────────
def test_resolved_flag_on_filters_by_active_pack_membership(
    cloud, monkeypatch,
):
    """Under flag-ON only policies whose id is in the (floor ∪
    activated) pack union survive. The per-policy ``enabled`` bit is
    NOT consulted.
    """
    monkeypatch.setenv("MAGI_CP_PACK_CENTRIC_RUNTIME", "1")
    _seed_policies(cloud, [
        (_make_policy("in-pack", matcher="Bash"), False),  # disabled but member
        (_make_policy("orphan", matcher="Bash"), True),   # enabled but no pack
    ])
    # Have to seed the pack AFTER floor has been created so the store's
    # is_floor invariant survives.
    cloud["client"].get(
        "/session/s1/packs", headers={"X-Api-Key": cloud["key"]},
    )
    existing_rows = cloud["pack_store"].load()
    existing_rows.append(UserPackRow(
        id="user-pack/mine", name="Mine", description="",
        policy_ids=["in-pack"],
    ))
    cloud["pack_store"].save(existing_rows)
    # Activate the user pack for this session.
    cloud["client"].post(
        "/session/s1/packs/activate",
        headers={"X-Api-Key": cloud["key"]},
        json={"pack_id": "user-pack/mine"},
    )
    r = cloud["client"].get(
        "/session/s1/resolved",
        headers={"X-Api-Key": cloud["key"]},
    )
    body = r.json()
    assert body["pack_centric_enabled"] is True
    assert body["active_packs"] == ["user-pack/mine"]
    coord_to_ids = {
        (row["event"], row["matcher"]): [p["id"] for p in row["policies"]]
        for row in body["policies_by_hook"]
    }
    # Only "in-pack" survives: it is a pack member (even though the
    # per-policy enabled=False). "orphan" is filtered out because no
    # active pack contains it.
    assert coord_to_ids.get(("PreToolUse", "Bash")) == ["in-pack"]


def test_resolved_flag_on_floor_ordered_first(cloud, monkeypatch):
    """Decision 1 ordering: floor-pack members lead, then activated."""
    monkeypatch.setenv("MAGI_CP_PACK_CENTRIC_RUNTIME", "1")
    _seed_policies(cloud, [
        (_make_policy("floor-p", matcher="Bash"), True),
        (_make_policy("pack-p", matcher="Bash"), True),
    ])
    # Seed the floor pack membership first via a lazy floor-seed.
    cloud["client"].get(
        "/session/s1/packs", headers={"X-Api-Key": cloud["key"]},
    )
    rows = cloud["pack_store"].load()
    updated = []
    for row in rows:
        if row.is_floor:
            row.policy_ids = ["floor-p"]
        updated.append(row)
    updated.append(UserPackRow(
        id="user-pack/mine", name="Mine", description="",
        policy_ids=["pack-p"],
    ))
    cloud["pack_store"].save(updated)
    cloud["client"].post(
        "/session/s1/packs/activate",
        headers={"X-Api-Key": cloud["key"]},
        json={"pack_id": "user-pack/mine"},
    )
    r = cloud["client"].get(
        "/session/s1/resolved",
        headers={"X-Api-Key": cloud["key"]},
    )
    body = r.json()
    coord_to_ids = {
        (row["event"], row["matcher"]): [p["id"] for p in row["policies"]]
        for row in body["policies_by_hook"]
    }
    # Floor-p leads because floor precedes activated packs in the walk.
    assert coord_to_ids.get(("PreToolUse", "Bash")) == ["floor-p", "pack-p"]


def test_resolved_flag_on_empty_floor_still_emits_activated_members(
    cloud, monkeypatch,
):
    """Floor prepended but empty; activated pack still emits its members."""
    monkeypatch.setenv("MAGI_CP_PACK_CENTRIC_RUNTIME", "1")
    _seed_policies(cloud, [(_make_policy("a", matcher="Bash"), False)])
    # Lazy-seed the empty floor.
    cloud["client"].get(
        "/session/s1/packs", headers={"X-Api-Key": cloud["key"]},
    )
    rows = cloud["pack_store"].load()
    rows.append(UserPackRow(
        id="user-pack/mine", name="Mine", description="",
        policy_ids=["a"],
    ))
    cloud["pack_store"].save(rows)
    cloud["client"].post(
        "/session/s1/packs/activate",
        headers={"X-Api-Key": cloud["key"]},
        json={"pack_id": "user-pack/mine"},
    )
    r = cloud["client"].get(
        "/session/s1/resolved",
        headers={"X-Api-Key": cloud["key"]},
    )
    body = r.json()
    coord_to_ids = {
        (row["event"], row["matcher"]): [p["id"] for p in row["policies"]]
        for row in body["policies_by_hook"]
    }
    assert coord_to_ids.get(("PreToolUse", "Bash")) == ["a"]


# ── P5 zero-downtime: unmigrated tenant fails closed to legacy ───────
def test_resolved_flag_on_unmigrated_tenant_falls_back_to_legacy(
    tmp_path, monkeypatch,
):
    """Global flag ON but the tenant's boot migration never completed
    (`pack_centric_migrated_at IS NULL`): the endpoint must NOT resolve
    against an empty floor (which would silently return zero policies for
    every hook — a total governance bypass). It must fall back to the
    legacy per-policy `enabled` resolver so yesterday's enabled set still
    fires today.
    """
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", ADMIN_KEY)
    monkeypatch.setenv("MAGI_CP_API_KEY", LEGACY_API_KEY)
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", "res-hitl-key")
    monkeypatch.setenv("MAGI_CP_PACK_CENTRIC_RUNTIME", "1")  # global ON

    ks = KeyStore(dir=str(tmp_path / "keys"))
    dsn = f"sqlite:///{tmp_path}/cloud.sqlite"
    policy_path = str(tmp_path / "policies.json")
    pack_path = str(tmp_path / "packs.json")
    app = create_app(
        keystore=ks, dsn=dsn,
        policy_store_path=policy_path,
        pack_store_path=pack_path,
    )
    client = TestClient(app)
    engine = app.state.engine
    # Create the tenant but DO NOT stamp pack_centric_migrated_at:
    # simulates a tenant whose best-effort boot migration failed.
    TenantRepo(engine).create(tenant_id="tenant-a")
    key = ApiKeyRepo(engine).issue(tenant_id="tenant-a").cleartext

    PolicyStore(path=policy_path).save([
        PolicyOverride(
            policy=_make_policy("a", matcher="Bash"), source="user",
            enabled=True,
        ),
        PolicyOverride(
            policy=_make_policy("b", matcher="Bash"), source="user",
            enabled=False,
        ),
    ])

    r = client.get(
        "/session/s1/resolved", headers={"X-Api-Key": key},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Fell back to legacy: envelope advertises pack_centric_enabled False
    # and the enabled policy "a" still fires (b is disabled → dropped).
    assert body["pack_centric_enabled"] is False
    coord_to_ids = {
        (row["event"], row["matcher"]): [p["id"] for p in row["policies"]]
        for row in body["policies_by_hook"]
    }
    assert coord_to_ids.get(("PreToolUse", "Bash")) == ["a"]
