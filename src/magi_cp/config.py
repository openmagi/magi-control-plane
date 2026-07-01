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


# ── Rollout gate for the Codex CLI runtime adapter.
# Design brief: docs/plans/2026-06-30-codex-runtime-adapter-design.md
#
# Default OFF (opposite of the pack-centric flag). With this unset or
# falsy, the runtime dispatcher (``magi_cp.runtime.detect.detect_runtime``)
# returns ``"cc"`` unconditionally, so the entire Codex path is dead code
# and the Claude Code path is byte-identical to the pre-adapter gate.
#
# Operators opt a build into the Codex adapter by setting
# ``MAGI_CP_CODEX_RUNTIME_ENABLED`` to a truthy token (``1`` / ``true`` /
# ``yes`` / ``on``, case-insensitive). Per-tenant selection then flows
# through ``tenants.runtime_id`` (see the design doc's Section 9.3
# feature-flag ladder). This env var is the global kill switch.
_CODEX_RUNTIME_ENV = "MAGI_CP_CODEX_RUNTIME_ENABLED"


def codex_runtime_enabled() -> bool:
    """Return True only when MAGI_CP_CODEX_RUNTIME_ENABLED is set to an
    explicit truthy value.

    Default OFF: unset (or any non-truthy value) returns False. Only the
    canonical truthy tokens ``1`` / ``true`` / ``yes`` / ``on``
    (case-insensitive) enable the Codex runtime adapter. This is the
    global kill switch from the design doc's feature-flag ladder; the
    dispatcher treats a False here as "CC only".
    """
    raw = os.environ.get(_CODEX_RUNTIME_ENV)
    if raw is None:
        return False
    return raw.strip().lower() in _TRUTHY


__all__ = [
    "pack_centric_runtime_enabled",
    "codex_runtime_enabled",
]
