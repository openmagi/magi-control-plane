"""magi-cp env catalog helpers.

Central home for reading process-wide feature flags so their names live
in exactly one place. Only the flags this file registers are read via
these helpers; unrelated env vars (DSN, key paths) stay read at their
point of use.

Keep this file tiny. It is imported by hot paths and must not pull in
heavy modules at import time.
"""
from __future__ import annotations

import os


# ── Phase 1..4 rollout gate for the pack-centric session-scoped runtime.
# Design brief: docs/plans/2026-06-30-pack-centric-session-scoped-runtime.md
#
# Phase 1 (this file's introduction) only REGISTERS the name. The gate
# resolution shift lives in Phase 2 — the gate binary reads this flag to
# decide whether to walk `session_active_packs` + the floor pack instead
# of the legacy per-policy `enabled` bit. Phase 5 flips the default to
# True.
#
# Default OFF: unset / any non-truthy value keeps the legacy pipeline.
_PACK_CENTRIC_ENV = "MAGI_CP_PACK_CENTRIC_RUNTIME"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def pack_centric_runtime_enabled() -> bool:
    """Return True iff MAGI_CP_PACK_CENTRIC_RUNTIME is set to a truthy
    value.

    Truthy values (case-insensitive): ``1``, ``true``, ``yes``, ``on``.
    Anything else (including unset) returns False so P1 remains a
    schema-only migration.
    """
    raw = os.environ.get(_PACK_CENTRIC_ENV)
    if raw is None:
        return False
    return raw.strip().lower() in _TRUTHY


__all__ = [
    "pack_centric_runtime_enabled",
]
