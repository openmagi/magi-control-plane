"""P2 pack-centric runtime: gate binary in-process cache + inheritance.

Design brief: 2026-06-30-pack-centric-session-scoped-runtime (private planning repo)
(§ "Gate binary cache", decisions 2 + 3).

Process model
=============
The primary consumer today is the gate binary CC spawns per hook call.
A cold-start subprocess means an in-process ``SessionCache`` cannot
carry policies across hooks by itself. Two follow-ups are already
planned in the design doc:

  * On-disk cache persistence (``~/.magi-cp/state/cache/{tenant_id}/
    {session_id}.json``, atomic rename, mtime as freshness proof).
  * A long-lived gate daemon (systemd unit / launchd agent) CC hooks
    talk to over a UNIX socket.

Neither has landed yet. The cache class in this module IS still useful
even under subprocess-per-hook because it defines the freshness /
invalidation contract every persistence strategy has to honour, and
because an in-process embedder (e.g. MCP server, python test harness)
can reuse it directly. When P3 wires either persistence option, the
new lane MUST reuse ``SessionCacheEntry`` shape + the mtime comparison
so cross-route mutations remain observable.

Cache contents (per ``(session_id, tenant_id)`` row):

  * The session's active pack list (as seen by the cloud).
  * The floor pack id (for the ALWAYS-ON chip / audit).
  * A pre-resolved ``policies_by_hook`` map so hook dispatch is a
    dict lookup, not another linear scan.
  * A timestamp for the cache row.
  * The ``(mtime, nonce)`` of the cache-invalidation sentinel file at
    the moment the row was loaded, used by the invalidation check.

Cache lifetime is intentionally NOT wall-clock TTL. Decision 5 (locked
in the design doc) makes activation persist "until the session ends OR
the operator runs `/magi:pack:*`". Practical implementation:

  * The CLI (P3) touches the ``(session_id, tenant_id)``-scoped
    invalidation sentinel on every ``/magi:pack:*`` invocation.
  * The gate polls the mtime of that file on every hook call. Cheap
    (microsecond stat). When the mtime shifts, the row is dropped and
    refetched lazily on next resolution. Steady-state tool-call loops
    therefore see one file stat and one dict lookup.

Per-session isolation
=====================
The sentinel is a per-``(session_id, tenant_id)`` file:
``~/.magi-cp/state/cache-invalidation/{tenant_id}/{session_id}``.
An earlier iteration used a single global sentinel; that caused a
cross-session flush (a CLI ``/magi:pack:activate`` on session-A dropped
the cache row for every other concurrent session, stampeding the
cloud). Per-session sentinels keep invalidation surgical.

The coordinate -> path mapping (:func:`_safe_component`) is
collision-resistant: it appends a short sha256 of the ORIGINAL id, so
two distinct sessions (or tenants) can never resolve to the SAME
sentinel file. Separator-collapsing alone was NOT injective
(``a/b`` and ``a_b`` both collapsed to ``a_b``, and ``..x`` and ``x``
both stripped to ``x``), which under a crafted-input threat model would
let one session's touch invalidate -- or fail to invalidate -- another
session's row through a shared sentinel.

Invalidation signal (why not mtime alone)
=========================================
The freshness proof is NOT the sentinel's mtime by itself. mtime is not
a strictly-monotonic change signal: on a filesystem whose mtime
granularity is >= the interval between two touches (1-2s on FAT / some
NFS / older ext), two CLI touches landing in the same granularity tick
share an mtime, so an mtime-equality check would keep serving the STALE
row after the second touch. To make invalidation independent of FS
mtime resolution, :func:`touch_invalidation_file` also writes a fresh,
unique nonce into the sentinel's body on every touch, and the freshness
check compares the ``(mtime, nonce)`` pair. Two rapid touches always
produce a different nonce even when the mtime tick collides, so a stale
row is observably dropped within one hook call regardless of the
underlying filesystem.

Invalidation scope (what the sentinel does NOT cover)
=====================================================
The sentinel is a SESSION-scoped freshness signal, not a
membership-drift signal. It is bumped by (a) session end and (b) a CLI
``/magi:pack:*`` touch of the per-session sentinel. Pack MEMBERSHIP
edits made through the dashboard -- adding/removing a policy from a
pack, or editing the always-on FLOOR pack's membership -- do NOT bump
any session's sentinel today. Consequently, once a persistence lane
exists (the on-disk / daemon follow-ups above), a long-lived active
session would keep evaluating its cached ``policies_by_hook`` snapshot
(including a stale floor pack) until the operator runs a CLI pack
command or the session ends. Tightening the floor pack (a "never
bypass" safety edit) would not reach already-active sessions.

For membership edits to be observable on an active cached session, the
CLOUD must touch the per-``(session_id, tenant_id)`` sentinel for every
affected active session on a membership write (reusing
:func:`touch_invalidation_file` -- its nonce bump is now the reliable
drift signal). Until that cloud-side fan-out lands, membership edits are
observable only on session end or the next ``/magi:pack:*`` touch.

P3 persistence authors: the ``(mtime, nonce)`` pair is the freshness
contract for SESSION-scoped activation changes. It is NOT sufficient on
its own for membership drift -- do not assume a cache hit implies the
underlying pack contents are unchanged.

Round-trip race
===============
``resolve_via_cache`` snapshots ``current_invalidation_signal`` (the
``(mtime, nonce)`` pair) BEFORE calling the fetcher and stamps the
returned entry with that pre-fetch signal. A CLI ``touch`` that lands
DURING the fetch therefore changes the sentinel's nonce (and mtime) away
from the entry's stamped signal, so the very next hook call observes the
invalidation. The alternative (fetcher-supplied post-round-trip signal)
opened a window where a mid-fetch touch could be masked forever.

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

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable


_LOGGER = logging.getLogger(__name__)


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


def _safe_component(name: str) -> str:
    """Return ``name`` mapped to a filesystem-safe, collision-resistant
    path component.

    Session ids come from CC (uuidv4) and tenant ids are typed but the
    invalidation path is a security-adjacent surface with two distinct
    requirements:

    * **Traversal safety.** A caller that manages to pass ``../foo``
      must not escape the state dir. We replace any path separator with
      ``_`` and strip leading dots so a ``..`` cannot travel up the
      tree.
    * **Injectivity.** Two distinct ids must never map to the SAME
      component, or a CLI touch for one session could invalidate -- or
      fail to invalidate -- another session's cache row through a shared
      sentinel. Separator-collapsing alone is NOT injective:
      ``a/b`` and ``a_b`` both collapse to ``a_b``; ``..x`` and ``x``
      both strip to ``x``. We therefore append a short sha256 of the
      ORIGINAL id. The readable (sanitised) prefix is kept purely for
      debuggability; the hash suffix is what guarantees distinct ids get
      distinct files. The traversal strip stays on top as
      defence-in-depth so the readable prefix can never itself escape.

    Empty input becomes ``__anon__`` so we never join an empty component
    (which would collapse the path to the state dir root and let a stat
    racy-collide across coordinates). Only the empty string maps there;
    every non-empty id resolves to a hashed component.
    """
    if not isinstance(name, str) or not name:
        return "__anon__"
    cleaned = name.replace(os.sep, "_").replace("/", "_").replace("\\", "_")
    cleaned = cleaned.lstrip(".")
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
    # Cap the readable prefix so a pathological id cannot blow past the
    # per-component NAME_MAX (255); the hash suffix preserves uniqueness
    # regardless of truncation. Fall back to ``id`` when the sanitised
    # prefix is empty (e.g. an all-dots/all-separator id) so the
    # component never starts with a bare ``.``.
    prefix = cleaned[:96] or "id"
    return f"{prefix}.{digest}"


def invalidation_file_path(session_id: str, tenant_id: str) -> str:
    """Full path to the mtime-signal file the CLI touches for the given
    ``(session_id, tenant_id)`` coordinates.

    A missing file is treated as "invalidation mtime = 0.0" (see
    :func:`current_invalidation_mtime`), which keeps a fresh install
    from stampeding the cache before any slash-command has fired.

    The path is per-coordinate so a CLI touch on session-A does NOT
    invalidate the cache row for session-B (see P1 fix note above).
    """
    return os.path.join(
        _state_dir(),
        "cache-invalidation",
        _safe_component(tenant_id),
        _safe_component(session_id),
    )


def sticky_packs_file_path() -> str:
    """Full path to the per-user sticky-packs file.

    The CLI is the only writer (P3). Missing file → no sticky packs.
    """
    return os.environ.get(
        "MAGI_CP_STICKY_PACKS_FILE",
        os.path.expanduser("~/.magi-cp/sticky-packs.json"),
    )


def session_state_file_path() -> str:
    """Full path to the state file the gate writes the last-seen CC
    session id into and the ``magi-cp session pack …`` CLI reads back.

    The gate is the WRITER (see :func:`persist_session_id`, called from
    ``gate.evaluate`` on every hook it observes); the CLI is the READER
    (``cli.resolve_session_id`` tier-4 fallback). Both resolve the path
    here so the writer/reader contract can never drift. Overridable via
    ``MAGI_CP_SESSION_FILE`` so tests never touch a real ``~/.magi-cp``
    tree.
    """
    return os.environ.get(
        "MAGI_CP_SESSION_FILE",
        os.path.join(_state_dir(), "session.json"),
    )


def _make_secure_dir(path: str) -> None:
    """Create ``path`` (and parents) with mode 0700.

    ``os.makedirs(mode=0o700)`` can only REMOVE bits via the process
    umask, never add them, so the resulting tree is at most 0700 —
    never group/world readable regardless of a loose operator umask.
    Mirrors the defence-in-depth the gate applies to its pubkey /
    heartbeat cache. Pre-existing dirs keep their mode (we do not chmod
    an operator's directory out from under them).
    """
    if not path:
        return
    os.makedirs(path, mode=0o700, exist_ok=True)


def persist_session_id(session_id: str) -> bool:
    """Atomically persist the last-seen CC session id to the state file.

    This is the PRODUCER for the tier-4 session-id fallback the CLI
    reads (:func:`session_state_file_path`). Called by the gate from
    ``gate.evaluate`` on every hook it observes so a bare
    ``magi-cp session pack activate <id>`` (no ``--session-id``, no
    ``MAGI_CP_SESSION_ID``) resolves to the session CC is actually
    running.

    The write is hardened like the gate's other trust files:
      * ``tmp + os.replace`` so a crash mid-write cannot leave a
        truncated / half-written state file the reader would choke on.
      * ``O_CREAT|O_TRUNC|O_NOFOLLOW`` at mode 0o600 so a pre-planted
        symlink cannot redirect the write and the file is not
        world/group readable regardless of umask.

    Returns ``True`` when the id landed on disk, ``False`` on any
    OSError (unwritable state dir, symlink swap) — best-effort; a
    missed write just means the CLI falls back to erroring on "no
    session id" rather than resolving, exactly as before this producer
    existed.
    """
    if not isinstance(session_id, str) or not session_id:
        return False
    path = session_state_file_path()
    try:
        _make_secure_dir(os.path.dirname(path))
        tmp = f"{path}.tmp.{os.getpid()}"
        fd = os.open(
            tmp,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
            0o600,
        )
        try:
            os.write(
                fd,
                json.dumps(
                    {"session_id": session_id}, ensure_ascii=False,
                ).encode("utf-8"),
            )
        finally:
            os.close(fd)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        _LOGGER.warning(
            "persist_session_id failed: session=%r errno=%s path=%s "
            "reason=%s",
            session_id, getattr(exc, "errno", None), path, exc,
        )
        return False


# Read at most this many bytes of the sentinel body. The nonce is a
# short token; a bounded read means a hostile-neighbour that grows the
# file cannot force an unbounded read on every hook call.
_MAX_NONCE_BYTES = 256


def current_invalidation_signal(
    session_id: str, tenant_id: str,
) -> tuple[float, str]:
    """Return the ``(mtime, nonce)`` freshness signal for the
    ``(session_id, tenant_id)`` sentinel, or ``(0.0, "")`` when the file
    is missing.

    The ``nonce`` is the sentinel's body, written fresh (and unique) on
    every :func:`touch_invalidation_file` call. Comparing the PAIR --
    not the mtime alone -- makes invalidation independent of filesystem
    mtime granularity: two rapid touches always change the nonce even
    when the coarse mtime tick collides (see module docstring
    §"Invalidation signal").

    The file is opened ONCE (fstat for mtime + a bounded body read for
    the nonce) so the hot path is a single open, not a stat plus a
    separate read.

    Any OSError (permission, dir doesn't exist, ELOOP on a symlink swap)
    is coerced to ``(0.0, "")`` so a hostile-neighbour scenario cannot
    brick hook resolution. A read failure degrades toward "row looks
    stale -> refetch", never toward "serve stale silently"; steady-state
    we recover on the next successful read.
    """
    try:
        with open(
            invalidation_file_path(session_id, tenant_id), "rb",
        ) as handle:
            mtime = os.fstat(handle.fileno()).st_mtime
            body = handle.read(_MAX_NONCE_BYTES)
        return (mtime, body.decode("utf-8", "replace").strip())
    except (FileNotFoundError, OSError):
        return (0.0, "")


def current_invalidation_mtime(session_id: str, tenant_id: str) -> float:
    """Return just the mtime half of :func:`current_invalidation_signal`
    (``0.0`` when the file is missing).

    Retained for callers/tests that only care about the mtime; the
    freshness check itself compares the full ``(mtime, nonce)`` pair so
    it does not depend on mtime granularity.
    """
    return current_invalidation_signal(session_id, tenant_id)[0]


def _fresh_nonce() -> str:
    """A short, unique, strictly-changing token for the sentinel body.

    ``time.time_ns()`` supplies a high-resolution, monotone-ish prefix;
    the random suffix guarantees two touches in the SAME nanosecond
    still differ. The value only has to CHANGE per touch -- it is never
    parsed, only compared for equality.
    """
    return f"{time.time_ns():x}-{os.urandom(12).hex()}"


def touch_invalidation_file(session_id: str, tenant_id: str) -> bool:
    """Bump the ``(session_id, tenant_id)`` invalidation sentinel's mtime.

    Used by the CLI (``magi-cp session pack activate/...``) to force
    the gate serving THIS session to refetch on the next hook.

    Returns ``True`` when the touch was observed on disk (path
    writable, mtime advanced), ``False`` otherwise. A ``False`` return
    is a real operator-facing failure — the cache will keep serving
    the stale row until the sentinel is writable again or the process
    exits. The CLI wrapper (P3) surfaces the ``False`` case as a
    warning to the operator so ``/magi:pack:activate`` does not report
    OK when the gate never observed the bump.

    We do NOT re-raise the OSError here. Callers that fail to observe
    a bump today (e.g. the tests' happy path) would spuriously start
    seeing exceptions from unrelated cleanup lanes, and the CLI's own
    happy path prefers a boolean it can render as a warning over an
    uncaught exception. A structured WARN log is emitted at the failure
    site so ops has a breadcrumb regardless of the caller's error
    treatment.
    """
    path = invalidation_file_path(session_id, tenant_id)
    try:
        _make_secure_dir(os.path.dirname(path) or ".")
        # Write a fresh, unique nonce into the body (truncating any
        # prior value). The nonce is the mtime-granularity-independent
        # change signal: two rapid touches always differ here even when
        # the filesystem's coarse mtime tick would otherwise collide.
        # The write itself bumps mtime; the explicit utime() keeps mtime
        # advancing as a secondary signal / defence-in-depth.
        #
        # Hardened like the gate's other trust files (pubkey / heartbeat
        # cache): O_NOFOLLOW refuses a pre-planted symlink swap, and mode
        # 0o600 keeps the sentinel out of a non-root neighbour's reach so
        # its (mtime, nonce) freshness signal cannot be spoofed or frozen
        # to keep the gate serving a stale policy map after a deactivate.
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
            0o600,
        )
        try:
            os.write(fd, _fresh_nonce().encode("utf-8"))
        finally:
            os.close(fd)
        os.utime(path, None)
        return True
    except OSError as exc:
        _LOGGER.warning(
            "touch_invalidation_file failed: session=%r tenant=%r "
            "errno=%s path=%s reason=%s",
            session_id, tenant_id,
            getattr(exc, "errno", None), path, exc,
        )
        return False


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
    # Body nonce of the sentinel at load time. Paired with
    # ``invalidation_mtime`` so freshness detection does not depend on
    # filesystem mtime granularity (see module docstring
    # §"Invalidation signal"). Empty string when the sentinel was
    # missing at load time.
    invalidation_nonce: str = ""


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

        Freshness is decided by comparing the ``(mtime, nonce)`` pair
        against the ``(session_id, tenant_id)``-scoped on-disk
        invalidation sentinel. When EITHER half differs from the row's
        snapshot we DROP the row so a caller that ignores the ``None``
        and re-populates does not race a second reader. Comparing the
        nonce (not the mtime alone) keeps invalidation reliable on
        coarse-mtime filesystems — see module docstring
        §"Invalidation signal".

        Per-session sentinel means an invalidation on session-A does
        NOT flush session-B — see module docstring.
        """
        key = (session_id, tenant_id)
        entry = self._entries.get(key)
        if entry is None:
            return None
        cur_mtime, cur_nonce = current_invalidation_signal(
            session_id, tenant_id,
        )
        if (
            entry.invalidation_mtime != cur_mtime
            or entry.invalidation_nonce != cur_nonce
        ):
            # Drop stale row so the next read forces a refetch.
            self._entries.pop(key, None)
            return None
        return entry

    def put(
        self, session_id: str, tenant_id: str,
        entry: SessionCacheEntry,
    ) -> None:
        """Store a fresh row. The caller is expected to have populated
        both ``entry.invalidation_mtime`` and ``entry.invalidation_nonce``
        via :func:`current_invalidation_signal` (with the SAME
        ``(session_id, tenant_id)`` coordinates the row is keyed on) so
        freshness comparisons against later sentinel bumps work.

        ``resolve_via_cache`` handles this stamping automatically by
        snapshotting the ``(mtime, nonce)`` pair BEFORE the fetcher
        round-trip; direct callers must do the same.
        """
        self._entries[(session_id, tenant_id)] = entry

    def drop(self, session_id: str, tenant_id: str) -> None:
        """Explicit invalidation lane. Not usually needed — the
        sentinel signal handles it — but exposed so a caller that KNOWS
        its row is stale (e.g. an in-band 401 rebind) can drop without
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
return a fully-populated ``SessionCacheEntry``; the caller
(``resolve_via_cache``) overwrites the ``(invalidation_mtime,
invalidation_nonce)`` pair with a pre-fetch snapshot so a mid-round-trip
CLI touch is not masked.
"""


def build_entry_from_state(
    state: dict, *, invalidation_mtime: float,
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

    ``invalidation_mtime`` is REQUIRED. A fetcher that reads the
    current mtime AFTER its round-trip opens a race window where a
    CLI touch landing during the fetch is masked forever (see module
    docstring §"Round-trip race"). ``resolve_via_cache`` snapshots the
    correct pre-fetch mtime and passes it back in through the entry it
    receives from the fetcher — the fetcher itself can pass ``0.0`` or
    any sentinel, ``resolve_via_cache`` overwrites the field on the
    entry with the pre-fetch snapshot before ``cache.put``.
    """
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
      1. Cache lookup (signal-checked). Hit → return the
         ``policies_by_hook`` slice for ``(event, matcher)`` (empty
         list when the pack set covers no policies on this hook).
      2. Miss → snapshot ``current_invalidation_signal`` (the
         ``(mtime, nonce)`` pair) BEFORE calling the fetcher (closes the
         "CLI touches sentinel mid-fetch" race — see module docstring
         §"Round-trip race"), invoke ``fetcher(session_id, tenant_id)``,
         overwrite the entry's ``(invalidation_mtime,
         invalidation_nonce)`` with the pre-fetch snapshot, store, and
         return the slice.

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
        # SNAPSHOT the (mtime, nonce) pair BEFORE the fetch begins. A
        # CLI touch that lands between this line and cache.put changes
        # the sentinel nonce (and mtime), so the stored signal no longer
        # matches and the very next .get() drops the row. Deferring the
        # signal read until after the fetcher returns (the pre-fix
        # behaviour) would let the fetcher's post-round-trip signal mask
        # the touch permanently. The nonce also closes the coarse-mtime
        # variant of this race where the touch shares an mtime tick.
        mtime_snapshot, nonce_snapshot = current_invalidation_signal(
            session_id, tenant_id,
        )
        entry = fetcher(session_id, tenant_id)
        # Overwrite whatever signal the fetcher stamped with our
        # pre-fetch snapshot. The fetcher is not required to know the
        # snapshot timing rule — this module owns it.
        entry.invalidation_mtime = mtime_snapshot
        entry.invalidation_nonce = nonce_snapshot
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
    caller can log / audit.

    Failure semantics
    ~~~~~~~~~~~~~~~~~
    We do NOT wrap ``activate_fn`` in a try/except. If it succeeds for
    pack A and raises for pack B, the caller sees the raised exception
    AND the child session is left with pack A activated and pack B
    not. The caller must retry the subagent-start hook to converge —
    which is safe because ``/session/{id}/packs/activate`` is
    idempotent server-side (a re-activated pack returns changed=False
    without side-effects, per the P1 endpoint contract).

    An earlier docstring claimed this helper delivered atomicity ("we
    do not swallow because a partial inherit is worse than a visible
    error"). That was a misread: the behaviour is fail-fast, not
    atomic. Fail-fast is fine because idempotent retry converges; if a
    future caller needs true atomicity, wrap this in a rollback loop
    that ``/deactivate`` on the successfully-activated ids before
    re-raising.
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
    "current_invalidation_signal",
    "inherit_packs_on_subagent",
    "invalidation_file_path",
    "load_sticky_packs_for_project",
    "persist_session_id",
    "resolve_via_cache",
    "session_state_file_path",
    "sticky_packs_file_path",
    "touch_invalidation_file",
]
