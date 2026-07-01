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
from magi_cp.policy.floor_pack import FLOOR_PACK_ID
from magi_cp.policy.ir import EvidencePolicy, EvidenceReq, Trigger
from magi_cp.policy.resolved import PolicyOverride


ADMIN_KEY = "res-admin-key"
LEGACY_API_KEY = "res-legacy-api-key"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", ADMIN_KEY)
    monkeypatch.setenv("MAGI_CP_API_KEY", LEGACY_API_KEY)
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", "res-hitl-key")
    # Flag OFF by default; tests that need ON monkeypatch explicitly.
    monkeypatch.delenv("MAGI_CP_PACK_CENTRIC_RUNTIME", raising=False)


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
    # Floor is lazily seeded by GET, matching /session/{id}/packs.
    assert body["floor_pack_id"] == FLOOR_PACK_ID
    assert body["pack_centric_enabled"] is False
    assert body["policies_by_hook"] == []


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
