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
# Default ON (2026-07-01, no-default-OFF policy). This is a GLOBAL
# AVAILABILITY switch, not an auto-migration: with it ON the dispatcher
# still returns ``"cc"`` for every tenant whose ``tenants.runtime_id`` is
# ``"claude-code"`` (the column default), so existing CC tenants are
# byte-identical. Flipping the default ON only makes the Codex runtime
# SELECTABLE (the settings RuntimePicker becomes usable and the cloud
# stops rejecting ``runtime_id = "codex"``); a tenant reaches the Codex
# path only by explicitly choosing it. Per the policy the goal is to make
# what is implemented visible and let any breakage surface (the known
# ``codex exec`` config.toml-hook gap, §11.4 F2, then shows up rather than
# hiding). Operators roll the whole adapter back with an explicit falsy
# token (``0`` / ``false`` / ``no`` / ``off`` / empty); that is the global
# kill switch and reverts the dispatcher to "CC only".
_CODEX_RUNTIME_ENV = "MAGI_CP_CODEX_RUNTIME_ENABLED"


def codex_runtime_enabled() -> bool:
    """Return True unless MAGI_CP_CODEX_RUNTIME_ENABLED is set to an
    explicit falsy value.

    Default-ON flip (2026-07-01): unset returns True, so the Codex runtime
    adapter is globally AVAILABLE. Per-tenant routing still flows through
    ``tenants.runtime_id`` (default ``"claude-code"``), so this only makes
    Codex selectable, not active for existing tenants. The only way to
    disable the adapter globally (dispatcher forced to "CC only") is an
    explicit falsy value: ``0`` / ``false`` / ``no`` / ``off``
    (case-insensitive) or the empty string. Any other value (including the
    truthy tokens) keeps it ON.
    """
    raw = os.environ.get(_CODEX_RUNTIME_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in _FALSY


# ── Run-command surface gate.
_ALLOW_RUN_COMMAND_ENV = "MAGI_CP_ALLOW_RUN_COMMAND"


def _run_command_allowed() -> bool:
    """D63 env knob: refuse RunCommandPolicy saves + /scripts uploads
    when `MAGI_CP_ALLOW_RUN_COMMAND=0`.

    Default-ON: any unset / blank / non-"0" value enables the surface.
    The self-host docker compose ships with the flag implicitly on; the
    hosted image overrides it to "0" so the multi-tenant fleet never
    spawns an inline subprocess off an authenticated REST request.
    """
    raw = os.environ.get(_ALLOW_RUN_COMMAND_ENV)
    if raw is None:
        return True
    return raw.strip() != "0"


__all__ = [
    "pack_centric_runtime_enabled",
    "codex_runtime_enabled",
    "_run_command_allowed",
]
