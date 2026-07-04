"""Shim C cloud-side fallback: synthetic ``SessionEnd`` for Codex.

Design brief: 2026-06-30-codex-runtime-adapter-design (private planning repo)
Section 4.3. Codex has ``Stop`` (turn-end) but no ``SessionEnd``. The
runtime driver (``runtime/codex.py``) synthesizes a ``SessionEnd`` from a
``Stop`` payload with a truthy ``stop_hook_active`` (Shim C step 1/2).
This module is the fallback (step 3) for the case where
``stop_hook_active`` never fires: a periodic cloud job finds Codex
sessions whose ``last_seen_at`` is older than the tenant TTL AND whose
active packs include at least one policy that requires a session-end
fanout, then dispatches a synthetic ``SessionEnd`` to the evidence-emit
path.

The core (``find_synthetic_session_end_targets`` /
``sweep_synthetic_session_end``) is dependency-injected — it takes an
iterable of session rows, a ``now`` clock, a
``pack_requires_session_end`` predicate, and an ``emit_fanout`` sink — so
it is unit-testable without a DB or scheduler. The production wiring
(query ``session_active_packs`` where ``runtime_id='codex'``, resolve the
predicate from the pack store, POST the fanout) is a thin adapter over
this core and is deliberately left to the scheduler layer.

Everything here is inert with ``MAGI_CP_CODEX_RUNTIME_ENABLED`` off: no
tenant carries ``runtime_id='codex'`` rows, so the sweep returns an empty
target list on every pass.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Protocol


# 30 minutes. Tenant-configurable per Section 4.3 via a policy-pack meta
# (``codex_synthetic_session_end_ttl_seconds``); resolved by
# ``resolve_ttl_seconds`` below.
CODEX_SYNTHETIC_SESSION_END_TTL_SECONDS = 1800

_CODEX_RUNTIME_ID = "codex"


@dataclass(frozen=True)
class SyntheticSessionEnd:
    """The fanout event a stale Codex session produces. Handed to the
    ``emit_fanout`` sink, which POSTs it to the evidence-emit path."""

    session_id: str
    tenant_id: str
    runtime_id: str = _CODEX_RUNTIME_ID
    reason: str = "codex_synthetic_session_end_ttl"


class _SessionRow(Protocol):
    """Structural view of the ``session_active_packs`` row the sweep
    reads. The ORM model (``cloud.db.SessionActivePacks``) satisfies it;
    tests pass a lightweight stand-in.

    ``last_synthetic_session_end_at`` is optional (absent -> never
    synthesized): the idempotency high-water mark bumped by
    ``mark_synthesized`` after a fanout fires. A row is re-armed only when
    fresh activity advances ``last_seen_at`` past it."""

    runtime_id: str
    session_id: str
    tenant_id: str
    last_seen_at: int
    pack_ids: list
    last_synthetic_session_end_at: int | None


def resolve_ttl_seconds(meta: dict | None) -> int:
    """Resolve the stale-session TTL from a tenant / pack meta dict.

    A positive int override under
    ``codex_synthetic_session_end_ttl_seconds`` wins; anything else
    (missing key, non-int, bool, non-positive) falls back to the
    30-minute default. Booleans are rejected explicitly because
    ``isinstance(True, int)`` is truthy in Python."""
    if meta:
        raw = meta.get("codex_synthetic_session_end_ttl_seconds")
        if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
            return raw
    return CODEX_SYNTHETIC_SESSION_END_TTL_SECONDS


def find_synthetic_session_end_targets(
    rows: Iterable[_SessionRow],
    *,
    now: int,
    pack_requires_session_end: Callable[[str], bool],
    ttl_seconds: int = CODEX_SYNTHETIC_SESSION_END_TTL_SECONDS,
) -> list[_SessionRow]:
    """Return the session rows that need a synthetic ``SessionEnd``.

    A row qualifies iff ALL hold (Section 4.3):
      1. ``runtime_id == 'codex'`` — CC sessions have a real SessionEnd.
      2. it is stale — ``now - last_seen_at >= ttl_seconds``.
      3. it has NOT already been synthesized for this stale window — i.e.
         ``last_synthetic_session_end_at`` is unset OR strictly older than
         ``last_seen_at`` (fresh activity re-arms it). This makes the sweep
         idempotent: a periodic pass (every 5 min against a 30 min TTL)
         re-selecting the same still-stale row a second time returns it
         only until ``mark_synthesized`` records the synthesis, after which
         it is excluded until the session sees new activity.
      4. at least one active pack requires a session-end fanout, per the
         injected ``pack_requires_session_end`` predicate.
    """
    targets: list[_SessionRow] = []
    for row in rows:
        if getattr(row, "runtime_id", "claude-code") != _CODEX_RUNTIME_ID:
            continue
        last_seen = int(row.last_seen_at)
        if now - last_seen < ttl_seconds:
            continue
        synth = getattr(row, "last_synthetic_session_end_at", None)
        if synth is not None and int(synth) >= last_seen:
            # Already synthesized for this stale window; no new activity
            # has re-armed it.
            continue
        pack_ids = list(getattr(row, "pack_ids", None) or [])
        if any(pack_requires_session_end(pid) for pid in pack_ids):
            targets.append(row)
    return targets


def sweep_synthetic_session_end(
    rows: Iterable[_SessionRow],
    *,
    now: int,
    emit_fanout: Callable[[SyntheticSessionEnd], None],
    pack_requires_session_end: Callable[[str], bool],
    ttl_seconds: int = CODEX_SYNTHETIC_SESSION_END_TTL_SECONDS,
    mark_synthesized: Callable[[_SessionRow, int], None] | None = None,
) -> list[SyntheticSessionEnd]:
    """Fire a synthetic ``SessionEnd`` for every stale Codex session with
    a session-end-requiring pack. Returns the fanout events dispatched
    (empty when nothing is stale). ``emit_fanout`` is the sink that POSTs
    each event to the evidence-emit path.

    Idempotency: after firing, each row's synthesis high-water mark is
    recorded via ``mark_synthesized(row, now)`` so a later sweep pass over
    the same still-stale row does NOT re-emit (dedup by ``session_id`` +
    ``last_synthetic_session_end_at``, per ``find_synthetic_session_end_targets``
    condition 3). The production wiring passes a ``mark_synthesized`` that
    persists the timestamp to ``session_active_packs``. When omitted, the
    default bumps the in-memory attribute best-effort, which is enough to
    make a repeated sweep over the same row objects a no-op."""
    fired: list[SyntheticSessionEnd] = []
    for row in find_synthetic_session_end_targets(
        rows, now=now, ttl_seconds=ttl_seconds,
        pack_requires_session_end=pack_requires_session_end,
    ):
        event = SyntheticSessionEnd(
            session_id=row.session_id, tenant_id=row.tenant_id,
        )
        emit_fanout(event)
        if mark_synthesized is not None:
            mark_synthesized(row, now)
        else:
            _default_mark_synthesized(row, now)
        fired.append(event)
    return fired


def _default_mark_synthesized(row: _SessionRow, now: int) -> None:
    """Best-effort in-memory dedup marker: stamp the row's synthesis
    high-water mark so a repeat sweep over the same object excludes it.
    Silently no-ops if the row rejects the attribute (frozen / slots),
    in which case the caller must supply a persisting ``mark_synthesized``."""
    try:
        setattr(row, "last_synthetic_session_end_at", now)
    except (AttributeError, TypeError):
        pass


__all__ = [
    "CODEX_SYNTHETIC_SESSION_END_TTL_SECONDS",
    "SyntheticSessionEnd",
    "resolve_ttl_seconds",
    "find_synthetic_session_end_targets",
    "sweep_synthetic_session_end",
]
