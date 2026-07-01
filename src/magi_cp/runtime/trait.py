"""Canonical `HookRuntime` trait + shared runtime-neutral types.

Design brief: docs/plans/2026-06-30-codex-runtime-adapter-design.md
(Section 3 "Architecture", L1/L6/L7 locked decisions).

This module owns the seam that decouples "what a coding-agent runtime
speaks on stdin" from "what Magi enforces." Every runtime driver
(`cc.py`, `codex.py`, and any future third runtime) implements the
`HookRuntime` Protocol below and translates between its native wire
shapes and the canonical `HookEvent` / `Verdict` dataclasses.

Nothing here imports a concrete driver — drivers import FROM this module
— so the trait stays the dependency sink at the bottom of the runtime
package.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..policy.ir import AnyPolicy


# ── Canonical inbound shape ──────────────────────────────────────────
@dataclass(frozen=True)
class HookEvent:
    """Runtime-neutral view of a single hook invocation.

    Every driver's ``parse_hook_payload`` produces one of these. The
    fields are the union of what Claude Code and Codex put on stdin;
    a field a given runtime does not send stays at its default. ``raw``
    keeps the original decoded payload so the CC policy path
    (``gate.evaluate``) can still read fields the canonical view does
    not surface, preserving byte-for-byte behaviour.
    """

    hook_event_name: str
    session_id: str = ""
    turn_id: str = ""
    cwd: str = ""
    tool_name: str = ""
    tool_input: dict = field(default_factory=dict)
    tool_response: dict | None = None
    model: str = ""
    permission_mode: str = ""
    transcript_path: str = ""
    # Codex-only: the alias list CC never sends. Empty tuple on CC.
    matcher_aliases: tuple[str, ...] = ()
    # The original decoded stdin payload, verbatim.
    raw: dict = field(default_factory=dict)


# ── Canonical verdict shape ──────────────────────────────────────────
@dataclass(frozen=True)
class Verdict:
    """Runtime-neutral decision the policy path hands back.

    ``decision`` is one of ``"allow"`` / ``"deny"`` / ``"ask"``. Drivers
    translate this into their native stdout envelope in ``emit_verdict``.
    ``updated_input`` / ``additional_context`` / ``system_message`` are
    the optional side channels; a driver whose runtime rejects one of
    them (e.g. Codex rejecting ``additionalContext`` on PreToolUse)
    downgrades it inside its own ``emit_verdict`` (P2 shims).
    """

    decision: str
    reason: str = ""
    hook_event_name: str = "PreToolUse"
    updated_input: dict | None = None
    additional_context: str | None = None
    system_message: str | None = None
    continue_: bool | None = None
    # Codex Shim B (P2) side inputs. ``context_scope`` tags an
    # ``additional_context`` payload as ``"turn"`` (downgrade to
    # ``systemMessage`` on the same event) or ``"session"`` (defer to the
    # next ``UserPromptSubmit`` via the per-session queue). ``session_id``
    # scopes the deferred-context queue file. Both default to "" so the CC
    # driver (which never reads them) stays byte-equivalent — the fields
    # are inert on every current ``decide()`` path.
    context_scope: str = ""
    session_id: str = ""


def merge_verdict_side_channels(
    obj: dict | None, verdict: "Verdict",
) -> dict | None:
    """Layer the universal ``continue`` / ``systemMessage`` side channels
    onto a driver's decision object.

    Design doc Section 2.2: both CC and Codex accept a top-level
    ``continue: false`` and ``systemMessage`` on every event, on top of
    whatever the per-event decision channel emits. Every ``emit_verdict``
    routes its final object through this helper so a policy path that
    populates ``Verdict.continue_`` / ``Verdict.system_message`` is not
    silently dropped (the field-completeness gap this closes).

    Returns ``obj`` unchanged when neither side channel is set, so the
    byte-equivalence contract holds on every current ``decide()`` path
    (both fields are ``None`` today). When a side channel IS set on a
    silent-allow verdict (``obj is None``), a fresh dict is minted so the
    channel still reaches stdout.
    """
    if verdict.continue_ is None and verdict.system_message is None:
        return obj
    merged: dict = dict(obj) if obj is not None else {}
    if verdict.continue_ is not None:
        merged["continue"] = verdict.continue_
    if verdict.system_message is not None:
        merged["systemMessage"] = verdict.system_message
    return merged


# ── Coverage report shapes ───────────────────────────────────────────
@dataclass(frozen=True)
class CoveragePolicyStatus:
    """Per-policy coverage annotation for a single runtime.

    ``status`` is ``"enforced"`` on the full-coverage path, or one of the
    Codex gap markers (``"codex_silent_skip"``,
    ``"codex_no_session_end"``, ``"codex_internal_subagent_gap"``,
    ``"codex_native_config_pending"``) once the P2 shims / native
    permission-mcp emitter land. ``downgrade`` names the compat fallback,
    or None when the policy enforces natively.
    """

    policy_id: str
    status: str
    downgrade: str | None = None


@dataclass(frozen=True)
class CoverageReport:
    """Roll-up of per-policy coverage for one runtime, powering the
    dashboard's per-policy strip + per-pack rollup (P4)."""

    runtime_id: str
    policies: tuple[CoveragePolicyStatus, ...] = ()

    @property
    def enforced_count(self) -> int:
        return sum(1 for p in self.policies if p.status == "enforced")

    @property
    def downgraded_count(self) -> int:
        return sum(1 for p in self.policies if p.downgrade is not None)


# ── Managed-config bundle ────────────────────────────────────────────
@dataclass(frozen=True)
class ManagedConfigBundle:
    """The managed-config artifacts a driver emits for its runtime.

    ``files`` maps a bundle-relative filename to its serialized bytes as
    text (e.g. ``"managed-settings.json"`` for CC, ``"requirements.toml"``
    + ``"hooks.json"`` for Codex). ``context_templates`` is the
    ``{sha256: template_bytes}`` sidecar map both runtimes materialize
    next to their managed config.
    """

    files: dict[str, str] = field(default_factory=dict)
    context_templates: dict[str, str] = field(default_factory=dict)


# ── Install paths ────────────────────────────────────────────────────
@dataclass(frozen=True)
class InstallPaths:
    """Where the installer drops managed config + slash commands for a
    runtime. Absolute paths (``~`` expanded by the caller at install
    time). See Section 6 of the design doc."""

    managed_config_dir: str
    slash_commands_dir: str
    context_templates_dir: str


# ── The trait ────────────────────────────────────────────────────────
@runtime_checkable
class HookRuntime(Protocol):
    """The seam every coding-agent runtime driver implements.

    Structural typing: a driver satisfies this Protocol by exposing the
    ``runtime_id`` attribute plus the five methods below. ``get_runtime``
    (in ``__init__``) returns concrete instances.
    """

    runtime_id: str

    def parse_hook_payload(self, raw_stdin: bytes) -> HookEvent:
        """Runtime-specific stdin JSON -> canonical HookEvent."""
        ...

    def emit_verdict(self, verdict: Verdict) -> bytes:
        """Canonical Verdict -> runtime-specific stdout JSON bytes."""
        ...

    def emit_managed_config(self, ir: list[AnyPolicy]) -> ManagedConfigBundle:
        """Policy IR -> managed config files for this runtime."""
        ...

    def coverage_report(self, ir: list[AnyPolicy]) -> CoverageReport:
        """Per-policy: does THIS runtime enforce it, downgrade, or skip?"""
        ...

    def default_install_paths(self) -> InstallPaths:
        """Where the installer drops managed config + slash commands."""
        ...


__all__ = [
    "HookRuntime",
    "HookEvent",
    "Verdict",
    "merge_verdict_side_channels",
    "CoveragePolicyStatus",
    "CoverageReport",
    "ManagedConfigBundle",
    "InstallPaths",
]
