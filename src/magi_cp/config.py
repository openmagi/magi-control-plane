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


# ── Rollout gate for the pack-centric session-scoped runtime.
# Design brief: docs/plans/2026-06-30-pack-centric-session-scoped-runtime.md
#
# Phase 1 registered the name; Phase 2 wired the gate resolution shift
# (walk `session_active_packs` + the floor pack instead of the legacy
# per-policy `enabled` bit). Phase 5 (this change) FLIPS THE DEFAULT TO
# ON: unset now means the pack-centric runtime is active, because the
# boot migration has moved every enabled policy into the tenant's floor
# pack so the same set that fired yesterday fires today.
#
# Default ON. Operators who need to roll back to the legacy per-policy
# `enabled` path set MAGI_CP_PACK_CENTRIC_RUNTIME to an explicit falsy
# value (``0`` / ``false`` / ``no`` / ``off``). See the design doc's
# "Migration" + Phase 5 sections for the rollback contract.
_PACK_CENTRIC_ENV = "MAGI_CP_PACK_CENTRIC_RUNTIME"

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off", ""})


def pack_centric_runtime_enabled() -> bool:
    """Return True unless MAGI_CP_PACK_CENTRIC_RUNTIME is set to an
    explicit falsy value.

    Phase 5 default flip: unset returns True (pack-centric runtime is
    the canonical path). The only way to reach the legacy per-policy
    ``enabled`` pipeline is an explicit falsy value: ``0``, ``false``,
    ``no``, ``off`` (case-insensitive), or the empty string. Any other
    value (including the truthy tokens ``1`` / ``true`` / ``yes`` /
    ``on``) keeps the runtime ON.
    """
    raw = os.environ.get(_PACK_CENTRIC_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in _FALSY


__all__ = [
    "pack_centric_runtime_enabled",
]
