"""Shim C cloud-side fallback: synthetic ``SessionEnd`` for Codex.

Design brief: docs/plans/2026-06-30-codex-runtime-adapter-design.md
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
    tests pass a lightweight stand-in."""

    runtime_id: str
    session_id: str
    tenant_id: str
    last_seen_at: int
    pack_ids: list


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
      3. at least one active pack requires a session-end fanout, per the
         injected ``pack_requires_session_end`` predicate.
    """
    targets: list[_SessionRow] = []
    for row in rows:
        if getattr(row, "runtime_id", "claude-code") != _CODEX_RUNTIME_ID:
            continue
        if now - int(row.last_seen_at) < ttl_seconds:
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
) -> list[SyntheticSessionEnd]:
    """Fire a synthetic ``SessionEnd`` for every stale Codex session with
    a session-end-requiring pack. Returns the fanout events dispatched
    (empty when nothing is stale). ``emit_fanout`` is the sink that POSTs
    each event to the evidence-emit path."""
    fired: list[SyntheticSessionEnd] = []
    for row in find_synthetic_session_end_targets(
        rows, now=now, ttl_seconds=ttl_seconds,
        pack_requires_session_end=pack_requires_session_end,
    ):
        event = SyntheticSessionEnd(
            session_id=row.session_id, tenant_id=row.tenant_id,
        )
        emit_fanout(event)
        fired.append(event)
    return fired


__all__ = [
    "CODEX_SYNTHETIC_SESSION_END_TTL_SECONDS",
    "SyntheticSessionEnd",
    "resolve_ttl_seconds",
    "find_synthetic_session_end_targets",
    "sweep_synthetic_session_end",
]
