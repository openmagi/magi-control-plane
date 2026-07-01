"""P1 pack-centric runtime — floor-pack seeder.

Design brief: docs/plans/2026-06-30-pack-centric-session-scoped-runtime.md

The floor pack is the tenant's "always-on" bundle. It ships empty
(decision 6), cannot be deactivated (decision 7), and is seeded lazily
on first activate / floor lookup (Phase 1 migration note).
"""
from __future__ import annotations

import pytest

from magi_cp.cloud.pack_store import PackStore, UserPackRow
from magi_cp.policy.floor_pack import (
    FLOOR_PACK_ID,
    FLOOR_PACK_SLUG,
    ensure_floor_pack,
)


@pytest.fixture
def pack_store(tmp_path):
    return PackStore(path=str(tmp_path / "packs.json"))


def test_ensure_floor_pack_seeds_when_missing(pack_store):
    pack_id = ensure_floor_pack("tenant-a", pack_store)
    assert pack_id == FLOOR_PACK_ID
    rows = pack_store.load()
    floor_rows = [r for r in rows if r.is_floor]
    assert len(floor_rows) == 1
    assert floor_rows[0].id == FLOOR_PACK_ID
    # decision 6: floor pack ships empty.
    assert floor_rows[0].policy_ids == []


def test_ensure_floor_pack_slug_matches_id(pack_store):
    assert FLOOR_PACK_ID == f"user-pack/{FLOOR_PACK_SLUG}"
    ensure_floor_pack("tenant-a", pack_store)
    # No collision with the immutable built-in prefix.
    for row in pack_store.load():
        assert not row.id.startswith("pack/")


def test_ensure_floor_pack_is_idempotent(pack_store):
    first = ensure_floor_pack("tenant-a", pack_store)
    second = ensure_floor_pack("tenant-a", pack_store)
    third = ensure_floor_pack("tenant-a", pack_store)
    assert first == second == third == FLOOR_PACK_ID
    floor_rows = [r for r in pack_store.load() if r.is_floor]
    assert len(floor_rows) == 1


def test_ensure_floor_pack_preserves_existing_user_packs(pack_store):
    # Seed a normal user pack first, then request the floor. Both must
    # coexist without the floor clobbering the pre-existing row.
    pack_store.save([
        UserPackRow(
            id="user-pack/research",
            name="Research",
            description="",
            policy_ids=["prebuilt/x"],
        ),
    ])
    ensure_floor_pack("tenant-a", pack_store)
    rows = pack_store.load()
    ids = sorted(r.id for r in rows)
    assert ids == [FLOOR_PACK_ID, "user-pack/research"]


def test_pack_store_rejects_duplicate_floor_on_save(pack_store):
    # Two rows both flagged is_floor must fail at save time — the gate
    # relies on the invariant to answer "which pack always fires".
    with pytest.raises(ValueError, match="at most one is_floor"):
        pack_store.save([
            UserPackRow(
                id="user-pack/floor-a", name="A", description="",
                policy_ids=[], is_floor=True,
            ),
            UserPackRow(
                id="user-pack/floor-b", name="B", description="",
                policy_ids=[], is_floor=True,
            ),
        ])


def test_pack_store_rejects_duplicate_floor_on_load(tmp_path):
    # Corrupt on-disk file with two is_floor rows must fail loud.
    path = str(tmp_path / "packs.json")
    open(path, "w", encoding="utf-8").write(
        '[{"id": "user-pack/floor-a", "name": "A", "description": "",'
        ' "policy_ids": [], "is_floor": true},'
        '{"id": "user-pack/floor-b", "name": "B", "description": "",'
        ' "policy_ids": [], "is_floor": true}]\n'
    )
    store = PackStore(path=path)
    with pytest.raises(ValueError, match="duplicate is_floor"):
        store.load()


def test_pack_store_legacy_rows_default_is_floor_false(tmp_path):
    # A pre-P1 packs.json without the is_floor key must load cleanly
    # and default to False on every row (no floor pack yet).
    path = str(tmp_path / "packs.json")
    open(path, "w", encoding="utf-8").write(
        '[{"id": "user-pack/a", "name": "A", "description": "",'
        ' "policy_ids": ["p1"]}]\n'
    )
    store = PackStore(path=path)
    rows = store.load()
    assert len(rows) == 1
    assert rows[0].is_floor is False


def test_floor_pack_survives_round_trip(pack_store):
    ensure_floor_pack("tenant-a", pack_store)
    # Load-save cycle must preserve is_floor exactly.
    rows = pack_store.load()
    pack_store.save(rows)
    reloaded = pack_store.load()
    floor_rows = [r for r in reloaded if r.is_floor]
    assert len(floor_rows) == 1
    assert floor_rows[0].id == FLOOR_PACK_ID
