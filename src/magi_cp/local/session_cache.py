"""P2 pack-centric runtime: gate binary in-process cache + inheritance.

Design brief: docs/plans/2026-06-30-pack-centric-session-scoped-runtime.md
(§ "Gate binary cache", decisions 2 + 3).

The gate binary runs as a subprocess CC spawns per hook call. On a
tight loop (agent takes 40 tool actions in 5 seconds), we cannot afford
a cloud round-trip per invocation. The cache lives in-process, keyed on
``(session_id, tenant_id)``, and holds:

  * The session's active pack list (as seen by the cloud).
  * The floor pack id (for the ALWAYS-ON chip / audit).
  * A pre-resolved ``policies_by_hook`` map so hook dispatch is a
    dict lookup, not another linear scan.
  * A timestamp for the cache row.
  * The mtime of the cache-invalidation sentinel file at the moment the
    row was loaded, used by the invalidation check.

Cache lifetime is intentionally NOT wall-clock TTL. Decision 5 (locked
in the design doc) makes activation persist "until the session ends OR
the operator runs `/magi:pack:*`". Practical implementation:

  * The CLI (P3) touches ``~/.magi-cp/state/cache-invalidation`` on
    every ``/magi:pack:*`` invocation.
  * The gate polls the mtime of that file on every hook call. Cheap
    (microsecond stat). When the mtime shifts, every cached row is
    dropped and refetched lazily on next resolution. Steady-state
    tool-call loops therefore see one file stat and one dict lookup.

Subagent inheritance (decision 2)
=================================
When CC fires a ``SubagentStart`` hook, the parent session's active
pack list must be inherited by the child. The gate observes this hook
and, for every pack id in the parent's active list, POSTs an
``/session/{child}/packs/activate`` call. Order is preserved to keep
the floor-first rule intact.

Sticky pack (decision 3)
========================
Session ids do not survive CC restart. To keep operator flow from
crumbling every restart, a per-user ``~/.magi-cp/sticky-packs.json``
maps ``project_path -> [pack_id, ...]``. On the FIRST hook call for a
session (i.e. the cache miss), the gate reads that file, matches the
current project path, and POSTs activate for each id BEFORE the
initial resolve. Writes are driven by the CLI (``magi-cp session pack
sticky <pack_id>``) in P3; this module only reads.

All error paths degrade to "no sticky packs / no invalidation touch /
no cache hit" so a corrupt file cannot brick the gate.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable


# ── Path resolution helpers ───────────────────────────────────────────
# We keep these in this module so the CLI + gate + tests all agree on
# the exact file locations without threading the strings through five
# call sites. Overridable via MAGI_CP_LOCAL_DIR (already used by
# gate.py's WAL + pubkey cache).
def _local_dir() -> str:
    return os.environ.get(
        "MAGI_CP_LOCAL_DIR", os.path.expanduser("~/.magi-cp/local"),
    )


def _state_dir() -> str:
    """The signal directory the CLI touches on ``/magi:pack:*``.

    Distinct from ``local/`` (WAL, pubkey cache) so an operator
    scripting a chown / rm on the local cache does not accidentally
    stomp on the invalidation signal.
    """
    return os.environ.get(
        "MAGI_CP_STATE_DIR", os.path.expanduser("~/.magi-cp/state"),
    )


def invalidation_file_path() -> str:
    """Full path to the mtime-signal file the CLI touches.

    A missing file is treated as "invalidation mtime = 0.0" (see
    :func:`current_invalidation_mtime`), which keeps a fresh install
    from stampeding the cache before any slash-command has fired.
    """
    return os.path.join(_state_dir(), "cache-invalidation")


def sticky_packs_file_path() -> str:
    """Full path to the per-user sticky-packs file.

    The CLI is the only writer (P3). Missing file → no sticky packs.
    """
    return os.environ.get(
        "MAGI_CP_STICKY_PACKS_FILE",
        os.path.expanduser("~/.magi-cp/sticky-packs.json"),
    )


def current_invalidation_mtime() -> float:
    """Return the mtime of the invalidation sentinel, or ``0.0`` when
    the file is missing.

    Any OSError (permission, dir doesn't exist, ELOOP on a symlink
    swap) is coerced to ``0.0`` so a hostile-neighbour scenario
    cannot brick hook resolution. The worst outcome is that the cache
    goes stale-forever until the operator manually invalidates by
    creating the file; steady-state we recover on the next successful
    stat.
    """
    try:
        return os.stat(invalidation_file_path()).st_mtime
    except (FileNotFoundError, OSError):
        return 0.0


def touch_invalidation_file() -> None:
    """Bump the invalidation sentinel's mtime.

    Used by the CLI (``magi-cp session pack activate/...``) to force
    every gate in the process tree to refetch on the next hook. Silent
    on any OSError — a stale cache is a lesser evil than a raised
    exception on the CLI's happy path.
    """
    path = invalidation_file_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # Open + close truncates existing content but that is fine;
        # the file is a signal, not a payload. utime() alone would
        # fail when the file does not exist yet.
        with open(path, "a", encoding="utf-8"):
            pass
        os.utime(path, None)
    except OSError:
        pass


# ── Sticky-pack loader ────────────────────────────────────────────────
def load_sticky_packs_for_project(project_path: str) -> list[str]:
    """Return the sticky pack ids for ``project_path`` (empty list if
    the file is missing / malformed / has no entry for the project).

    The file shape is::

        {
          "/abs/path/to/project": ["pack/research-mode", "user-pack/mine"],
          ...
        }

    Order in the JSON list is preserved so the caller can activate in
    the operator-authored order. A non-list value under a project key
    is treated as "no sticky packs" (defensive; we never surface a
    silent type error to the gate's hook path).
    """
    path = sticky_packs_file_path()
    try:
        raw = open(path, encoding="utf-8").read()
    except (FileNotFoundError, OSError):
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    entry = data.get(project_path)
    if not isinstance(entry, list):
        return []
    out: list[str] = []
    for item in entry:
        if isinstance(item, str) and item:
            out.append(item)
    return out


# ── Cache row ─────────────────────────────────────────────────────────
@dataclass
class SessionCacheEntry:
    """One cached (session_id, tenant_id) row.

    ``policies_by_hook`` is keyed by ``(event, matcher)`` — the same
    coordinates the runtime shim passes into
    :func:`magi_cp.policy.resolver.resolve_policies_for_hook`. Values
    are lists of already-resolved policy dicts (serialised as they
    arrived from the cloud) so the caller does not have to walk the
    active pack list again on cache hit.
    """

    active_packs: list[str]
    floor_pack_id: str | None
    policies_by_hook: dict[tuple[str, str | None], list[dict]] = field(
        default_factory=dict,
    )
    loaded_at: float = 0.0
    invalidation_mtime: float = 0.0


# ── Cache class ───────────────────────────────────────────────────────
class SessionCache:
    """In-process cache for session-scoped pack resolution.

    Concurrency: the gate is single-process-per-hook, so the cache is
    only ever touched by one thread at a time. If a future embedding
    (e.g. an MCP server running the resolver in-process) shares this
    across threads, the caller must guard with an ``asyncio.Lock`` /
    ``threading.Lock`` — this class is not synchronised.
    """

    def __init__(self) -> None:
        # ``dict`` preserves insertion order, which is fine — cache
        # entries are not iterated in perf paths.
        self._entries: dict[tuple[str, str], SessionCacheEntry] = {}

    # ── read paths ────────────────────────────────────────────────
    def get(
        self, session_id: str, tenant_id: str,
    ) -> SessionCacheEntry | None:
        """Return the cached row for ``(session_id, tenant_id)`` iff it
        is still fresh, or ``None`` when a refetch is required.

        Freshness is decided by mtime comparison against the on-disk
        invalidation sentinel. When the sentinel's mtime differs from
        the row's snapshot we DROP the row so a caller that ignores
        the ``None`` and re-populates does not race a second reader.
        """
        key = (session_id, tenant_id)
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.invalidation_mtime != current_invalidation_mtime():
            # Drop stale row so the next read forces a refetch.
            self._entries.pop(key, None)
            return None
        return entry

    def put(
        self, session_id: str, tenant_id: str,
        entry: SessionCacheEntry,
    ) -> None:
        """Store a fresh row. The caller is expected to have populated
        ``entry.invalidation_mtime`` via
        :func:`current_invalidation_mtime` so freshness comparisons
        against later mtime bumps work.
        """
        self._entries[(session_id, tenant_id)] = entry

    def drop(self, session_id: str, tenant_id: str) -> None:
        """Explicit invalidation lane. Not usually needed — the mtime
        stat handles it — but exposed so a caller that KNOWS its row
        is stale (e.g. an in-band 401 rebind) can drop without
        touching the sentinel.
        """
        self._entries.pop((session_id, tenant_id), None)

    def size(self) -> int:
        """Diagnostic: how many rows are held. Not a metric surface
        (no publish); useful for tests only.
        """
        return len(self._entries)


# ── Top-level resolution helper ───────────────────────────────────────
FetchStateFn = Callable[[str, str], "SessionCacheEntry"]
"""Signature the caller supplies to :func:`resolve_via_cache` so we do
not hard-wire the HTTP transport into this module. The function must
return a fully-populated ``SessionCacheEntry`` (invalidation_mtime
included) or raise so the caller can decide the failure lane.
"""


def build_entry_from_state(
    state: dict, *, invalidation_mtime: float | None = None,
) -> SessionCacheEntry:
    """Materialise a ``SessionCacheEntry`` from the cloud's reply dict.

    The expected shape is::

        {
          "active_packs":  [pack_id, ...],
          "floor_pack_id": str | None,
          "policies_by_hook": [
            {"event": str, "matcher": str | None,
             "policies": [dict, ...]},
            ...
          ],
        }

    The wire uses a list of records instead of a JSON-object with a
    tuple key because JSON has no tuple keys. This helper folds the
    list into the in-memory tuple-keyed dict the cache stores.

    ``invalidation_mtime=None`` (default) reads the current mtime; a
    caller mocking time can pass a fixed value.
    """
    if invalidation_mtime is None:
        invalidation_mtime = current_invalidation_mtime()
    active = state.get("active_packs") or []
    if not isinstance(active, list):
        active = []
    floor_id = state.get("floor_pack_id")
    if floor_id is not None and not isinstance(floor_id, str):
        floor_id = None
    raw_by_hook = state.get("policies_by_hook") or []
    if not isinstance(raw_by_hook, list):
        raw_by_hook = []
    resolved: dict[tuple[str, str | None], list[dict]] = {}
    for row in raw_by_hook:
        if not isinstance(row, dict):
            continue
        event = row.get("event")
        if not isinstance(event, str) or not event:
            continue
        matcher = row.get("matcher")
        if matcher is not None and not isinstance(matcher, str):
            continue
        policies = row.get("policies") or []
        if not isinstance(policies, list):
            continue
        resolved[(event, matcher)] = [p for p in policies if isinstance(p, dict)]
    return SessionCacheEntry(
        active_packs=[str(p) for p in active if isinstance(p, str)],
        floor_pack_id=floor_id,
        policies_by_hook=resolved,
        loaded_at=time.time(),
        invalidation_mtime=invalidation_mtime,
    )


def resolve_via_cache(
    *,
    session_id: str,
    tenant_id: str,
    event: str,
    matcher: str | None,
    cache: SessionCache,
    fetcher: FetchStateFn,
) -> list[dict]:
    """Return the policies to evaluate for one hook call.

    Steps:
      1. Cache lookup (mtime-checked). Hit → return the
         ``policies_by_hook`` slice for ``(event, matcher)`` (empty
         list when the pack set covers no policies on this hook).
      2. Miss → invoke ``fetcher(session_id, tenant_id)`` to load
         fresh state, store, and return the slice.

    ``fetcher`` is the ONE injection point that talks to the cloud.
    The gate's real fetcher POSTs to ``/session/{id}/resolved`` (see
    the P2 endpoint added in ``app.py``); the tests inject a fake so
    this module has zero HTTP dependency.

    On fetcher exception the callable's exception propagates — the
    gate decides the fail-open vs fail-closed lane (existing shim
    behaviour: soft-fail-open on transient network errors, hard fail
    on config).
    """
    entry = cache.get(session_id, tenant_id)
    if entry is None:
        entry = fetcher(session_id, tenant_id)
        cache.put(session_id, tenant_id, entry)
    return list(entry.policies_by_hook.get((event, matcher), []))


# ── Subagent inheritance ──────────────────────────────────────────────
ActivateFn = Callable[[str, str, str], None]
"""Signature the caller supplies to :func:`inherit_packs_on_subagent`
so we do not hard-wire the HTTP transport here. Arguments are
``(session_id, tenant_id, pack_id)`` — matches the cloud endpoint's
positional shape."""


def inherit_packs_on_subagent(
    *,
    parent_active_packs: Iterable[str],
    floor_pack_id: str | None,
    child_session_id: str,
    tenant_id: str,
    activate_fn: ActivateFn,
) -> list[str]:
    """Activate the parent's active packs on the child session.

    Design brief decision 2: the child session inherits the parent's
    active pack list, ordered. The floor pack is skipped (the child
    session inherits it via ``ensure_floor_pack`` on the cloud side
    lazily on next resolution — replaying an activate for the floor
    pack would 400 with ``floor_pack_locked`` anyway).

    Returns the list of pack ids that were requested (in order) so the
    caller can log / audit. Errors from ``activate_fn`` propagate on
    the FIRST failure — we do not swallow because a partial inherit is
    worse than a visible error (the operator can retry the subagent
    start).
    """
    to_activate: list[str] = []
    for pid in parent_active_packs:
        if not isinstance(pid, str) or not pid:
            continue
        if pid == floor_pack_id:
            # Skip the floor: it is always-on and cloud-side lazy-seeded.
            continue
        to_activate.append(pid)
    for pid in to_activate:
        activate_fn(child_session_id, tenant_id, pid)
    return to_activate


# ── Sticky-pack activation on first hook ──────────────────────────────
def bootstrap_sticky_packs(
    *,
    session_id: str,
    tenant_id: str,
    project_path: str,
    activate_fn: ActivateFn,
) -> list[str]:
    """Activate sticky packs on a fresh session boot.

    Called by the gate before its first cache load for a new session.
    Idempotent because ``/session/{id}/packs/activate`` is idempotent
    on the cloud side — a session that already has the sticky pack
    active just gets a ``changed=False`` no-op.

    Returns the list of pack ids requested so the caller can log.
    An empty return means either the sticky file is missing or the
    project path has no entry — either way, no cloud call fired.
    """
    ids = load_sticky_packs_for_project(project_path)
    for pid in ids:
        activate_fn(session_id, tenant_id, pid)
    return ids


__all__ = [
    "ActivateFn",
    "FetchStateFn",
    "SessionCache",
    "SessionCacheEntry",
    "bootstrap_sticky_packs",
    "build_entry_from_state",
    "current_invalidation_mtime",
    "inherit_packs_on_subagent",
    "invalidation_file_path",
    "load_sticky_packs_for_project",
    "resolve_via_cache",
    "sticky_packs_file_path",
    "touch_invalidation_file",
]
