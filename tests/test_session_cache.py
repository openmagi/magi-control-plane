"""P2 pack-centric runtime — gate binary cache + inheritance + sticky.

Design brief: 2026-06-30-pack-centric-session-scoped-runtime (private planning repo)
(§ "Gate binary cache", decisions 2 + 3).

Covered here (per implementation brief "Tests" bullet):

  * Cache invalidation test: touch the invalidation file, next
    resolution triggers a refetch.
  * Per-session sentinel isolation: a touch on session-A does NOT
    invalidate the cache row for session-B.
  * Round-trip race: a touch that lands DURING the fetcher round-trip
    still invalidates the very next lookup.
  * ``touch_invalidation_file`` returns True on success / False on
    OSError (and logs a WARN).
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
import logging
import os
import time

import pytest

from magi_cp.local.session_cache import (
    SessionCache,
    SessionCacheEntry,
    bootstrap_sticky_packs,
    build_entry_from_state,
    current_invalidation_mtime,
    current_invalidation_signal,
    inherit_packs_on_subagent,
    invalidation_file_path,
    load_sticky_packs_for_project,
    resolve_via_cache,
    sticky_packs_file_path,
    touch_invalidation_file,
)


# Test coordinates used throughout — the sentinel is per-(session, tenant)
# so tests scope by these unless they need cross-session behaviour.
_S = "sess-1"
_T = "tenant-1"


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
    path = invalidation_file_path(_S, _T)
    # Path shape: {state}/cache-invalidation/{tenant}.{hash}/{session}.{hash}
    # Bubbles under the env-var-scoped state dir set by the autouse fixture.
    assert "/state/" in path or "\\state\\" in path
    assert "cache-invalidation" in path
    # The readable id is kept as a component prefix (a collision-resistant
    # hash suffix is appended), so both coordinates still appear in path.
    assert _S in path
    assert _T in path


def test_invalidation_file_path_is_collision_resistant():
    """Fix: the coordinate→path mapping must be injective. The old
    separator-collapsing mapping aliased distinct ids
    (``a/b`` and ``a_b`` both → ``a_b``; ``..x`` and ``x`` both → ``x``),
    letting one session's touch invalidate — or fail to invalidate —
    another session's cache row through a shared sentinel.
    """
    # Session axis: previously-colliding pairs must now differ.
    assert invalidation_file_path("a/b", "t") != invalidation_file_path("a_b", "t")
    assert invalidation_file_path("..x", "t") != invalidation_file_path("x", "t")
    # Tenant axis too.
    assert invalidation_file_path("s", "a/b") != invalidation_file_path("s", "a_b")
    assert invalidation_file_path("s", "..x") != invalidation_file_path("s", "x")


def test_collision_resistant_sentinels_do_not_cross_invalidate():
    """End-to-end: a CLI touch on session ``a/b`` must NOT invalidate the
    cache row for the (formerly-aliased) session ``a_b``.
    """
    cache = SessionCache()
    touch_invalidation_file("a/b", _T)
    touch_invalidation_file("a_b", _T)
    cache.put("a/b", _T, _entry(session_id="a/b", active=["x"]))
    cache.put("a_b", _T, _entry(session_id="a_b", active=["y"]))
    assert cache.get("a/b", _T) is not None
    assert cache.get("a_b", _T) is not None
    # Touch only ``a/b``.
    time.sleep(1.05)
    touch_invalidation_file("a/b", _T)
    assert cache.get("a/b", _T) is None
    got = cache.get("a_b", _T)
    assert got is not None, "aliased sentinel cross-invalidated a distinct session"
    assert got.active_packs == ["y"]


def test_invalidation_file_path_is_per_session_and_tenant():
    """Two distinct coordinates → two distinct paths (per-session
    isolation lens: one session's invalidation cannot flush another's).
    """
    p_a = invalidation_file_path("A", "tenant")
    p_b = invalidation_file_path("B", "tenant")
    p_x = invalidation_file_path("A", "other-tenant")
    assert p_a != p_b
    assert p_a != p_x
    assert p_b != p_x


def test_invalidation_file_path_rejects_traversal():
    """A ``..`` in session_id or tenant_id must not escape the state
    dir. The safe-component helper collapses separators + strips
    leading dots so nothing lands outside the coordinate subtree.
    """
    path = invalidation_file_path("../evil", "tenant")
    # Whatever normalization we do, the result must remain rooted at the
    # state dir + include a cache-invalidation segment. Traversal to a
    # parent dir would land the path OUTSIDE the state root.
    assert "cache-invalidation" in path
    # The evil component was stripped of its leading dots.
    assert path.rsplit(os.sep, 1)[-1] not in ("..", ".", "")


def test_current_invalidation_mtime_missing_returns_zero():
    # Autouse fixture left the state dir empty; no file yet.
    assert current_invalidation_mtime(_S, _T) == 0.0


def test_touch_invalidation_file_creates_it_and_returns_true():
    ok = touch_invalidation_file(_S, _T)
    assert ok is True
    assert os.path.exists(invalidation_file_path(_S, _T))
    mtime = current_invalidation_mtime(_S, _T)
    assert mtime > 0.0


def test_touch_invalidation_file_is_not_world_readable():
    """P2 hardening: the sentinel is a security-relevant freshness
    signal — a non-root neighbour must not be able to read (and thus
    spoof / freeze) it. It is written 0600, and its parent dir tree is
    created 0700, mirroring the gate's pubkey/heartbeat cache."""
    import stat as _stat

    assert touch_invalidation_file(_S, _T) is True
    path = invalidation_file_path(_S, _T)
    assert _stat.S_IMODE(os.stat(path).st_mode) == 0o600
    parent = os.path.dirname(path)
    assert (os.stat(parent).st_mode & 0o077) == 0


def test_touch_invalidation_file_bumps_mtime():
    assert touch_invalidation_file(_S, _T) is True
    first = current_invalidation_mtime(_S, _T)
    # >=1s is needed for a portable filesystem mtime bump. Filesystems
    # vary in sub-second resolution; sleep past a second so we do not
    # flake on ext4 / APFS defaults.
    time.sleep(1.05)
    assert touch_invalidation_file(_S, _T) is True
    second = current_invalidation_mtime(_S, _T)
    assert second > first


def test_touch_invalidation_file_returns_false_on_oserror(caplog):
    """When the state dir is a file (or otherwise unwritable) the touch
    must return False AND log a WARN with the path + errno so the CLI
    can surface a "cache may be stale" warning instead of silently
    reporting OK to the operator.
    """
    # Point the state dir at a REGULAR FILE so os.makedirs on any
    # sub-path raises OSError (FileExistsError / NotADirectoryError).
    import tempfile
    fd, blocker = tempfile.mkstemp()
    os.close(fd)
    try:
        os.environ["MAGI_CP_STATE_DIR"] = blocker
        with caplog.at_level(logging.WARNING,
                             logger="magi_cp.local.session_cache"):
            ok = touch_invalidation_file(_S, _T)
        assert ok is False, "touch must signal failure on OSError"
        # A WARN record was emitted with the coordinates + path.
        assert any(
            "touch_invalidation_file failed" in rec.message
            and _S in rec.message
            and _T in rec.message
            for rec in caplog.records
        ), caplog.records
    finally:
        os.environ.pop("MAGI_CP_STATE_DIR", None)
        try:
            os.unlink(blocker)
        except OSError:
            pass


# ── cache freshness ──────────────────────────────────────────────────
def _entry(session_id=_S, tenant_id=_T, active=(), floor=None,
           by_hook=None, mtime=None):
    # Snapshot the CURRENT (mtime, nonce) so a put()→get() with no
    # intervening touch is a hit. Freshness now compares the pair, so the
    # nonce must be stamped too (not just the mtime).
    cur_mtime, cur_nonce = current_invalidation_signal(session_id, tenant_id)
    if mtime is None:
        mtime = cur_mtime
    return SessionCacheEntry(
        active_packs=list(active),
        floor_pack_id=floor,
        policies_by_hook=dict(by_hook or {}),
        loaded_at=time.time(),
        invalidation_mtime=mtime,
        invalidation_nonce=cur_nonce,
    )


def test_cache_hit_returns_stored_entry():
    cache = SessionCache()
    entry = _entry(active=["pack/a"], floor="user-pack/floor")
    cache.put(_S, _T, entry)
    got = cache.get(_S, _T)
    assert got is not None
    assert got.active_packs == ["pack/a"]


def test_cache_hit_scoped_by_session_and_tenant():
    cache = SessionCache()
    cache.put("s1", "t1", _entry(session_id="s1", tenant_id="t1",
                                  active=["pack/a"]))
    assert cache.get("s2", "t1") is None
    assert cache.get("s1", "t2") is None


def test_cache_invalidation_via_mtime_bump():
    """The implementation-brief cache-invalidation test: touch the
    sentinel, next lookup triggers a refetch (cache.get returns
    None).
    """
    cache = SessionCache()
    touch_invalidation_file(_S, _T)   # give the row a real mtime to snapshot
    entry = _entry(active=["pack/a"])
    cache.put(_S, _T, entry)
    assert cache.get(_S, _T) is not None
    # Simulate the CLI ran /magi:pack:* between hook calls.
    time.sleep(1.05)
    touch_invalidation_file(_S, _T)
    assert cache.get(_S, _T) is None


def test_cache_invalidation_is_per_session_isolated():
    """P1 fix regression: an invalidation on session-A must NOT flush
    the cache row for session-B on the same tenant.

    Without per-session sentinels, one CLI touch triggered by
    /magi:pack:activate on session-A drops every cached row for every
    concurrent session on the same tenant.
    """
    cache = SessionCache()
    # Seed both sessions with fresh mtimes.
    touch_invalidation_file("sess-A", _T)
    touch_invalidation_file("sess-B", _T)
    cache.put("sess-A", _T, _entry(session_id="sess-A", active=["a"]))
    cache.put("sess-B", _T, _entry(session_id="sess-B", active=["b"]))
    assert cache.get("sess-A", _T) is not None
    assert cache.get("sess-B", _T) is not None
    # Operator activates a pack on session-A only.
    time.sleep(1.05)
    touch_invalidation_file("sess-A", _T)
    # Session-A drops (as expected); session-B MUST stay hot.
    assert cache.get("sess-A", _T) is None
    got_b = cache.get("sess-B", _T)
    assert got_b is not None, (
        "cross-session flush regressed: touching sess-A's sentinel "
        "invalidated sess-B's cache row"
    )
    assert got_b.active_packs == ["b"]


def test_touch_writes_changing_nonce_without_sleep():
    """Fix: the sentinel body carries a strictly-changing nonce, so two
    touches inside the SAME mtime granularity tick still produce an
    observable change signal.
    """
    touch_invalidation_file(_S, _T)
    _, n1 = current_invalidation_signal(_S, _T)
    # No sleep — on a coarse-mtime FS these two touches could share mtime.
    touch_invalidation_file(_S, _T)
    _, n2 = current_invalidation_signal(_S, _T)
    assert n1 and n2
    assert n1 != n2, "nonce must change on every touch regardless of mtime"


def test_cache_invalidation_when_mtime_forced_equal():
    """Fix regression: invalidation must NOT depend on mtime advancing.

    We simulate a filesystem whose mtime granularity is coarser than the
    interval between two touches by FORCING the sentinel's mtime back to
    its pre-touch value after the second touch. The body nonce still
    changed, so the stale row must be dropped even though mtime is
    unchanged — the exact FAT/old-ext/NFS failure mode the lens flags.
    """
    cache = SessionCache()
    touch_invalidation_file(_S, _T)
    path = invalidation_file_path(_S, _T)
    fixed = os.stat(path).st_mtime
    cache.put(_S, _T, _entry(active=["a"]))
    assert cache.get(_S, _T) is not None
    # Second touch (new nonce), then pin mtime back so it looks unchanged.
    touch_invalidation_file(_S, _T)
    os.utime(path, (fixed, fixed))
    assert os.stat(path).st_mtime == fixed, "precondition: mtime pinned equal"
    assert cache.get(_S, _T) is None, (
        "stale row survived a same-mtime-tick touch: invalidation must "
        "compare the body nonce, not rely on mtime granularity"
    )


def test_cache_drop_is_explicit_lane():
    cache = SessionCache()
    cache.put(_S, _T, _entry(active=["pack/a"]))
    cache.drop(_S, _T)
    assert cache.get(_S, _T) is None


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


def test_build_entry_from_state_requires_invalidation_mtime():
    """P1 fix: ``invalidation_mtime`` is mandatory. A caller that
    forgets to snapshot pre-round-trip cannot silently fall through to
    ``current_invalidation_mtime()`` and open the round-trip race.
    """
    with pytest.raises(TypeError):
        # No invalidation_mtime kwarg — must raise, not default.
        build_entry_from_state({})  # type: ignore[call-arg]


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
        }, invalidation_mtime=0.0)

    out = resolve_via_cache(
        session_id=_S, tenant_id=_T,
        event="PreToolUse", matcher="Bash",
        cache=cache, fetcher=fetch,
    )
    assert out == [{"id": "a"}]
    assert fetched == [(_S, _T)]

    # Second call is a cache hit — fetcher not invoked again.
    out2 = resolve_via_cache(
        session_id=_S, tenant_id=_T,
        event="PreToolUse", matcher="Bash",
        cache=cache, fetcher=fetch,
    )
    assert out2 == [{"id": "a"}]
    assert fetched == [(_S, _T)]   # unchanged


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
        }, invalidation_mtime=0.0)

    r1 = resolve_via_cache(
        session_id=_S, tenant_id=_T, event="E", matcher="M",
        cache=cache, fetcher=fetch,
    )
    assert r1 == [{"id": "iter-1"}]

    time.sleep(1.05)
    touch_invalidation_file(_S, _T)

    r2 = resolve_via_cache(
        session_id=_S, tenant_id=_T, event="E", matcher="M",
        cache=cache, fetcher=fetch,
    )
    assert r2 == [{"id": "iter-2"}]
    assert fetches["n"] == 2


def test_resolve_via_cache_closes_mid_fetch_touch_race():
    """P1 fix regression: a CLI touch that lands DURING the fetcher
    round-trip must still invalidate the very next lookup.

    Timeline of the race we are guarding:
      1. cache.get() → miss (empty cache).
      2. fetcher() begins its "cloud round-trip" (simulated by a
         fake fetcher that TOUCHES the sentinel before returning).
      3. Under the pre-fix behaviour the fetcher's returned
         SessionCacheEntry stamped invalidation_mtime = post-fetch
         mtime, i.e. the same mtime the sentinel now shows → cache
         hit on the next lookup, stale data served forever.
      4. Under the fix ``resolve_via_cache`` snapshots the pre-fetch
         mtime and stamps THAT onto the entry, so the post-fetch
         sentinel mtime is strictly higher and the next .get() drops
         the row.
    """
    cache = SessionCache()
    fetch_calls = {"n": 0}

    def fetch(session_id, tenant_id):
        fetch_calls["n"] += 1
        # Simulate the CLI activating a pack while the round-trip is
        # in flight — the sentinel gets touched during the fetcher.
        time.sleep(1.05)
        touch_invalidation_file(session_id, tenant_id)
        # Return an entry with the CURRENT mtime (as an unaware fetcher
        # would). The cache module must overwrite this with its
        # pre-fetch snapshot.
        return build_entry_from_state({
            "policies_by_hook": [
                {"event": "PreToolUse", "matcher": "Bash",
                 "policies": [{"id": f"iter-{fetch_calls['n']}"}]},
            ],
        }, invalidation_mtime=current_invalidation_mtime(
            session_id, tenant_id,
        ))

    r1 = resolve_via_cache(
        session_id=_S, tenant_id=_T,
        event="PreToolUse", matcher="Bash",
        cache=cache, fetcher=fetch,
    )
    assert r1 == [{"id": "iter-1"}]

    # The mid-fetch touch means the cache row's stamped mtime is
    # STRICTLY LESS than the sentinel's current mtime → next lookup
    # must miss and re-invoke the fetcher.
    r2 = resolve_via_cache(
        session_id=_S, tenant_id=_T,
        event="PreToolUse", matcher="Bash",
        cache=cache, fetcher=fetch,
    )
    assert r2 == [{"id": "iter-2"}], (
        "mid-fetch invalidation race regressed: stale policies would "
        "have fired indefinitely because the cached row swallowed the "
        "post-round-trip mtime"
    )
    assert fetch_calls["n"] == 2


def test_resolve_via_cache_returns_empty_for_unknown_hook():
    cache = SessionCache()

    def fetch(session_id, tenant_id):
        return build_entry_from_state({
            "policies_by_hook": [
                {"event": "PreToolUse", "matcher": "Bash",
                 "policies": [{"id": "a"}]},
            ],
        }, invalidation_mtime=0.0)

    out = resolve_via_cache(
        session_id=_S, tenant_id=_T,
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
    """Fail-fast: activate_fn exceptions bubble on the first failure so
    the subagent-start hook can retry (idempotent server-side).
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


def test_inherit_packs_partial_inherit_survives_on_second_failure():
    """Failure semantics regression: if activate_fn succeeds for pack A
    and raises for pack B, the child is left with pack A active AND
    the exception bubbles. Caller retries the whole hook; idempotent
    server-side activate converges the child to [A, B] on the next
    attempt. The docstring documents this reality — previous wording
    misleadingly implied atomicity.
    """
    activated: list[str] = []

    def activate(child_id, tenant_id, pack_id):
        if pack_id == "pack/B":
            raise RuntimeError("cloud transient")
        activated.append(pack_id)

    with pytest.raises(RuntimeError, match="cloud transient"):
        inherit_packs_on_subagent(
            parent_active_packs=["pack/A", "pack/B"],
            floor_pack_id=None,
            child_session_id="c",
            tenant_id="t",
            activate_fn=activate,
        )
    # Partial inherit: pack/A landed before pack/B raised. The caller
    # must retry to converge.
    assert activated == ["pack/A"]


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
