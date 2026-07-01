"""P2 pack-centric runtime — gate binary cache + inheritance + sticky.

Design brief: docs/plans/2026-06-30-pack-centric-session-scoped-runtime.md
(§ "Gate binary cache", decisions 2 + 3).

Covered here (per implementation brief "Tests" bullet):

  * Cache invalidation test: touch the invalidation file, next
    resolution triggers a refetch.
  * Subagent inheritance test: SubagentStart hook with parent having
    packs [A, B] results in POST activate A + activate B on child
    session (order preserved, floor pack skipped).
  * Sticky pack test: fresh session with sticky-packs.json for the
    project ends up with those packs activated.

Plus regression guards for the cache's mtime semantics + fetcher
contract that the implementation-brief spec requires.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from magi_cp.local.session_cache import (
    SessionCache,
    SessionCacheEntry,
    bootstrap_sticky_packs,
    build_entry_from_state,
    current_invalidation_mtime,
    inherit_packs_on_subagent,
    invalidation_file_path,
    load_sticky_packs_for_project,
    resolve_via_cache,
    sticky_packs_file_path,
    touch_invalidation_file,
)


# ── autouse: isolate every test into its own state dirs ──────────────
@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path, monkeypatch):
    """Every test gets a fresh ~/.magi-cp/ layout under tmp_path so a
    prior test's invalidation touch or sticky file cannot leak into
    the next.
    """
    local = tmp_path / "local"
    state = tmp_path / "state"
    local.mkdir()
    state.mkdir()
    monkeypatch.setenv("MAGI_CP_LOCAL_DIR", str(local))
    monkeypatch.setenv("MAGI_CP_STATE_DIR", str(state))
    monkeypatch.setenv(
        "MAGI_CP_STICKY_PACKS_FILE",
        str(tmp_path / "sticky-packs.json"),
    )


# ── path resolution ──────────────────────────────────────────────────
def test_invalidation_file_path_lives_under_state_dir(tmp_path):
    path = invalidation_file_path()
    assert path.endswith("cache-invalidation")
    # Bubbles under the env-var-scoped state dir set by the autouse fixture.
    assert "/state/" in path


def test_current_invalidation_mtime_missing_returns_zero():
    # Autouse fixture left the state dir empty; no file yet.
    assert current_invalidation_mtime() == 0.0


def test_touch_invalidation_file_creates_it():
    touch_invalidation_file()
    assert os.path.exists(invalidation_file_path())
    mtime = current_invalidation_mtime()
    assert mtime > 0.0


def test_touch_invalidation_file_bumps_mtime():
    touch_invalidation_file()
    first = current_invalidation_mtime()
    # >=1s is needed for a portable filesystem mtime bump. Filesystems
    # vary in sub-second resolution; sleep past a second so we do not
    # flake on ext4 / APFS defaults.
    time.sleep(1.05)
    touch_invalidation_file()
    second = current_invalidation_mtime()
    assert second > first


# ── cache freshness ──────────────────────────────────────────────────
def _entry(active=(), floor=None, by_hook=None, mtime=None):
    if mtime is None:
        mtime = current_invalidation_mtime()
    return SessionCacheEntry(
        active_packs=list(active),
        floor_pack_id=floor,
        policies_by_hook=dict(by_hook or {}),
        loaded_at=time.time(),
        invalidation_mtime=mtime,
    )


def test_cache_hit_returns_stored_entry():
    cache = SessionCache()
    entry = _entry(active=["pack/a"], floor="user-pack/floor")
    cache.put("s", "t", entry)
    got = cache.get("s", "t")
    assert got is not None
    assert got.active_packs == ["pack/a"]


def test_cache_hit_scoped_by_session_and_tenant():
    cache = SessionCache()
    cache.put("s1", "t1", _entry(active=["pack/a"]))
    assert cache.get("s2", "t1") is None
    assert cache.get("s1", "t2") is None


def test_cache_invalidation_via_mtime_bump():
    """The implementation-brief cache-invalidation test: touch the
    sentinel, next lookup triggers a refetch (cache.get returns
    None).
    """
    cache = SessionCache()
    touch_invalidation_file()   # give the row a real mtime to snapshot
    entry = _entry(active=["pack/a"])
    cache.put("s", "t", entry)
    assert cache.get("s", "t") is not None
    # Simulate the CLI ran /magi:pack:* between hook calls.
    time.sleep(1.05)
    touch_invalidation_file()
    assert cache.get("s", "t") is None


def test_cache_drop_is_explicit_lane():
    cache = SessionCache()
    cache.put("s", "t", _entry(active=["pack/a"]))
    cache.drop("s", "t")
    assert cache.get("s", "t") is None


# ── build_entry_from_state ───────────────────────────────────────────
def test_build_entry_from_state_folds_by_hook_list_into_tuple_keyed_dict():
    state = {
        "active_packs": ["pack/x"],
        "floor_pack_id": "user-pack/floor",
        "policies_by_hook": [
            {"event": "PreToolUse", "matcher": "Bash",
             "policies": [{"id": "a"}]},
            {"event": "PreToolUse", "matcher": None,
             "policies": [{"id": "b"}]},
        ],
    }
    entry = build_entry_from_state(state, invalidation_mtime=42.0)
    assert entry.active_packs == ["pack/x"]
    assert entry.floor_pack_id == "user-pack/floor"
    assert entry.policies_by_hook == {
        ("PreToolUse", "Bash"): [{"id": "a"}],
        ("PreToolUse", None): [{"id": "b"}],
    }
    assert entry.invalidation_mtime == 42.0


def test_build_entry_from_state_defaults_are_safe():
    # Empty envelope: nothing to fold, but no exceptions.
    entry = build_entry_from_state({}, invalidation_mtime=1.0)
    assert entry.active_packs == []
    assert entry.floor_pack_id is None
    assert entry.policies_by_hook == {}


def test_build_entry_from_state_drops_malformed_rows():
    """Defensive parser: garbage rows in the cloud reply do not
    poison the cache.
    """
    state = {
        "active_packs": ["ok", 42, None],   # 42 + None must be stripped
        "floor_pack_id": 123,               # non-str → None
        "policies_by_hook": [
            {"event": "PreToolUse", "matcher": "Bash",
             "policies": [{"id": "keep"}]},
            "not-a-dict",
            {"event": "", "matcher": "x", "policies": []},
            {"event": "Stop", "matcher": "*", "policies": [1, "x", {"id": "y"}]},
        ],
    }
    entry = build_entry_from_state(state, invalidation_mtime=0.0)
    assert entry.active_packs == ["ok"]
    assert entry.floor_pack_id is None
    # Only the well-formed rows survive; policies list keeps only dict items.
    assert entry.policies_by_hook == {
        ("PreToolUse", "Bash"): [{"id": "keep"}],
        ("Stop", "*"): [{"id": "y"}],
    }


# ── resolve_via_cache: hit + miss + fetcher plumbing ─────────────────
def test_resolve_via_cache_miss_populates_via_fetcher():
    cache = SessionCache()
    fetched = []

    def fetch(session_id, tenant_id):
        fetched.append((session_id, tenant_id))
        return build_entry_from_state({
            "active_packs": ["pack/x"],
            "floor_pack_id": "user-pack/floor",
            "policies_by_hook": [
                {"event": "PreToolUse", "matcher": "Bash",
                 "policies": [{"id": "a"}]},
            ],
        })

    out = resolve_via_cache(
        session_id="s", tenant_id="t",
        event="PreToolUse", matcher="Bash",
        cache=cache, fetcher=fetch,
    )
    assert out == [{"id": "a"}]
    assert fetched == [("s", "t")]

    # Second call is a cache hit — fetcher not invoked again.
    out2 = resolve_via_cache(
        session_id="s", tenant_id="t",
        event="PreToolUse", matcher="Bash",
        cache=cache, fetcher=fetch,
    )
    assert out2 == [{"id": "a"}]
    assert fetched == [("s", "t")]   # unchanged


def test_resolve_via_cache_refetches_after_invalidation():
    cache = SessionCache()
    fetches = {"n": 0}

    def fetch(session_id, tenant_id):
        fetches["n"] += 1
        # Snapshot the current mtime so the built entry is fresh.
        return build_entry_from_state({
            "active_packs": [f"pack/{fetches['n']}"],
            "floor_pack_id": None,
            "policies_by_hook": [
                {"event": "E", "matcher": "M",
                 "policies": [{"id": f"iter-{fetches['n']}"}]},
            ],
        })

    r1 = resolve_via_cache(
        session_id="s", tenant_id="t", event="E", matcher="M",
        cache=cache, fetcher=fetch,
    )
    assert r1 == [{"id": "iter-1"}]

    time.sleep(1.05)
    touch_invalidation_file()

    r2 = resolve_via_cache(
        session_id="s", tenant_id="t", event="E", matcher="M",
        cache=cache, fetcher=fetch,
    )
    assert r2 == [{"id": "iter-2"}]
    assert fetches["n"] == 2


def test_resolve_via_cache_returns_empty_for_unknown_hook():
    cache = SessionCache()

    def fetch(session_id, tenant_id):
        return build_entry_from_state({
            "policies_by_hook": [
                {"event": "PreToolUse", "matcher": "Bash",
                 "policies": [{"id": "a"}]},
            ],
        })

    out = resolve_via_cache(
        session_id="s", tenant_id="t",
        event="Stop", matcher=None,
        cache=cache, fetcher=fetch,
    )
    assert out == []


# ── subagent inheritance ────────────────────────────────────────────
def test_inherit_packs_on_subagent_replays_parent_active_list():
    """Implementation-brief test: parent packs [A, B] → child gets
    POST activate A then activate B (order preserved).
    """
    calls = []

    def activate(child_id, tenant_id, pack_id):
        calls.append((child_id, tenant_id, pack_id))

    requested = inherit_packs_on_subagent(
        parent_active_packs=["pack/A", "pack/B"],
        floor_pack_id="user-pack/floor",
        child_session_id="child_1",
        tenant_id="t",
        activate_fn=activate,
    )
    assert requested == ["pack/A", "pack/B"]
    assert calls == [
        ("child_1", "t", "pack/A"),
        ("child_1", "t", "pack/B"),
    ]


def test_inherit_packs_skips_floor_pack():
    """The floor pack is always-on server-side (decision 7). Replaying
    an activate for it would 400 with ``floor_pack_locked``; the
    helper skips it defensively.
    """
    calls = []

    def activate(child_id, tenant_id, pack_id):
        calls.append(pack_id)

    inherit_packs_on_subagent(
        parent_active_packs=["user-pack/floor", "pack/A"],
        floor_pack_id="user-pack/floor",
        child_session_id="c",
        tenant_id="t",
        activate_fn=activate,
    )
    assert calls == ["pack/A"]


def test_inherit_packs_skips_falsy_ids():
    calls = []

    def activate(child_id, tenant_id, pack_id):
        calls.append(pack_id)

    inherit_packs_on_subagent(
        parent_active_packs=["pack/A", "", None, 42, "pack/B"],  # type: ignore[list-item]
        floor_pack_id=None,
        child_session_id="c",
        tenant_id="t",
        activate_fn=activate,
    )
    assert calls == ["pack/A", "pack/B"]


def test_inherit_packs_propagates_activate_failure():
    """A partial inherit is worse than a visible error — the helper
    lets exceptions bubble so the subagent-start hook can retry.
    """
    def activate(*a, **kw):
        raise RuntimeError("cloud unreachable")

    with pytest.raises(RuntimeError, match="cloud unreachable"):
        inherit_packs_on_subagent(
            parent_active_packs=["pack/A"],
            floor_pack_id=None,
            child_session_id="c",
            tenant_id="t",
            activate_fn=activate,
        )


# ── sticky-pack loader + bootstrap ───────────────────────────────────
def _write_sticky(mapping: dict) -> None:
    path = sticky_packs_file_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mapping, f)


def test_load_sticky_packs_missing_file_returns_empty():
    assert load_sticky_packs_for_project("/proj") == []


def test_load_sticky_packs_for_project_reads_json_list():
    _write_sticky({"/proj": ["pack/A", "pack/B"]})
    assert load_sticky_packs_for_project("/proj") == ["pack/A", "pack/B"]


def test_load_sticky_packs_project_not_present_returns_empty():
    _write_sticky({"/other": ["pack/A"]})
    assert load_sticky_packs_for_project("/proj") == []


def test_load_sticky_packs_malformed_json_returns_empty():
    path = sticky_packs_file_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("not json at all }")
    assert load_sticky_packs_for_project("/proj") == []


def test_load_sticky_packs_non_list_value_returns_empty():
    _write_sticky({"/proj": "not-a-list"})
    assert load_sticky_packs_for_project("/proj") == []


def test_load_sticky_packs_filters_non_string_entries():
    _write_sticky({"/proj": ["pack/A", 42, None, "", "pack/B"]})
    assert load_sticky_packs_for_project("/proj") == ["pack/A", "pack/B"]


def test_bootstrap_sticky_packs_activates_each_id():
    """Implementation-brief test: fresh session with a sticky mapping
    ends up with those packs activated.
    """
    _write_sticky({"/proj": ["pack/A", "pack/B"]})
    calls = []

    def activate(session_id, tenant_id, pack_id):
        calls.append((session_id, tenant_id, pack_id))

    requested = bootstrap_sticky_packs(
        session_id="s", tenant_id="t",
        project_path="/proj",
        activate_fn=activate,
    )
    assert requested == ["pack/A", "pack/B"]
    assert calls == [
        ("s", "t", "pack/A"),
        ("s", "t", "pack/B"),
    ]


def test_bootstrap_sticky_packs_no_file_is_noop():
    # No sticky file exists; bootstrap must not raise and must not
    # call the activate fn.
    called = []
    requested = bootstrap_sticky_packs(
        session_id="s", tenant_id="t",
        project_path="/proj",
        activate_fn=lambda *a, **k: called.append(a),
    )
    assert requested == []
    assert called == []


def test_bootstrap_sticky_packs_no_entry_for_project_is_noop():
    _write_sticky({"/other": ["pack/A"]})
    called = []
    requested = bootstrap_sticky_packs(
        session_id="s", tenant_id="t",
        project_path="/proj",
        activate_fn=lambda *a, **k: called.append(a),
    )
    assert requested == []
    assert called == []
