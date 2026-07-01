"""Deterministic Policy IR -> Codex CLI ``requirements.toml`` compiler.

Sibling of ``compiler.py`` (the CC managed-settings emitter). Codex is
another native target format, not a semantic transform, so it lives next
to the CC compiler and consumes the same Policy IR. See the design doc
Section 3.2 (file layout) + Section 6.2 (``requirements.toml`` shape).

Guarantees mirror ``compile_to_managed_settings``:

  - Pure function: no clock, no randomness, no env reads.
  - Byte-stable: same input list -> byte-identical output, and a
    reordered input list -> the SAME output (events + matchers are
    sorted). The TOML is hand-emitted (no ``tomli_w`` dependency) so the
    byte layout is fully under our control.

P1 scope was the straight-through translation. P2 (this file) adds two
of the four gap shims that manifest as extra managed-config hook entries:

  - Shim A (Section 4.1): a PreToolUse policy targeting a Codex
    silent-skip tool ALSO emits a ``PermissionRequest`` hook + a
    ``PostToolUse`` audit hook on the same tool, so the gate still sees
    the tool post-hoc.
  - Shim D (Section 4.4): a subagent-lifecycle policy (SubagentStart /
    SubagentStop) ALSO emits belt-and-suspenders ``spawn_agent``
    PreToolUse + PostToolUse mirror hooks (``spawn_agent`` IS covered),
    so the user-triggered fan-out path is captured even when Codex's
    internal reviewers do not fire the lifecycle hook.

Shims B (additionalContext downgrade) and C (SessionEnd synthesis) live
in the runtime driver (``runtime/codex.py``), not here, because they are
verdict-time / parse-time transforms with no managed-config surface.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from .ir import (
    AnyPolicy, ContextInjectionPolicy, EvidencePolicy, InputRewritePolicy,
    McpGatingPolicy, PermissionPolicy, RunCommandPolicy, SubagentPolicy,
)


# The single gate command every Codex hook entry shells out to. The
# ``--runtime codex`` flag is the CLI shortcut for setting
# ``MAGI_CP_RUNTIME=codex``; the dispatcher still sniffs the payload in
# case only the env var is set. Matches design doc Section 6.2.
CODEX_GATE_COMMAND = "/usr/local/bin/magi-cp gate --runtime codex"
CODEX_HOOK_TIMEOUT_MS = 5000

# Shim D (Section 4.4): the subagent lifecycle events whose fanout may
# miss Codex internal reviewers. A policy on one of these gets the
# parent-side ``spawn_agent`` mirror hooks below. Kept local to the
# emitter (no runtime import) to stay a pure policy-layer module.
_SUBAGENT_LIFECYCLE_EVENTS: frozenset[str] = frozenset({
    "SubagentStart", "SubagentStop",
})
# Shim D: the covered tool the belt-and-suspenders mirror hooks bind to.
_SUBAGENT_SPAWN_TOOL = "spawn_agent"


@dataclass(frozen=True)
class CodexRequirementsBundle:
    """The three artifacts the Codex managed-config install writes.

    ``requirements_toml`` — the ``[features]`` block + ``[[hooks.<Event>]]``
    tables (installed at ``/etc/codex/requirements.toml``).
    ``hooks_json_sidecar`` — a CC drop-in ``hooks.json`` shape for the
    Codex layer that also accepts the JSON hook format (design doc
    Section 2.3); byte-stable JSON.
    ``context_templates`` — ``{sha256: template_bytes}`` sidecar map,
    identical shape to the CC compiler's sidecars.

    LIVE-TEST NOTE (2026-07-01, §11.4 F2/F3/F5): user ``~/.codex/config.toml``
    ``[[hooks.*]]`` blocks do NOT fire under ``codex exec`` (headless) even
    with ``--dangerously-bypass-hook-trust`` — proven empirically (tool ran,
    zero hook fires). So the ENFORCED path is the MANAGED
    ``/etc/codex/requirements.toml`` layer (``ManagedHooksRequirements``,
    precedence mdm > system > project > session_flags > plugin), and the
    working interactive registration shape is a PLUGIN ``hooks.json`` (nested
    ``{"hooks": {Event: [{matcher, hooks:[{type,command}]}]}}``) — which is
    what ``hooks_json_sidecar`` already emits. Permission DECISIONS in
    requirements.toml are deny-only (``forbidden``/``prompt``, never
    ``allow``; most-restrictive merge). Do NOT ship enforcement via user
    config.toml hooks; that surface is a ``codex exec`` gate-bypass.
    """

    requirements_toml: str
    hooks_json_sidecar: str
    context_templates: dict[str, str] = field(default_factory=dict)


def _context_template_hash(template: str) -> str:
    """Stable sha256(template) sidecar key. Mirrors the CC compiler so
    the same template hashes identically across both runtimes."""
    return hashlib.sha256(template.encode("utf-8")).hexdigest()


def _hook_pairs(policies: list[AnyPolicy]) -> tuple[
    dict[str, set[str]], dict[str, str], bool,
]:
    """Collect (event -> {matchers}) plus the context-template sidecar
    map plus a ``has_subagent`` flag from the policy list.

    Native-surface archetypes (Permission / Mcp) do NOT produce hook
    entries — they compile to Codex's permission/mcp config out of band,
    same as CC. SubagentPolicy flips ``has_subagent`` so the
    ``[features].multi_agent`` toggle is emitted, but does not itself add
    a hook table in P1 (the belt-and-suspenders spawn_agent hook is a P2
    shim-D concern).
    """
    events: dict[str, set[str]] = {}
    context_templates: dict[str, str] = {}
    has_subagent = False

    def _add(event: str, matcher: str) -> None:
        events.setdefault(event, set()).add(matcher)

    for p in policies:
        if isinstance(p, EvidencePolicy):
            _add(p.trigger.event, p.trigger.matcher)
        elif isinstance(p, InputRewritePolicy):
            _add(p.trigger.event, p.trigger.matcher)
        elif isinstance(p, RunCommandPolicy):
            _add(p.trigger.event, p.trigger.matcher)
        elif isinstance(p, ContextInjectionPolicy):
            _add(p.event, p.matcher)
            context_templates[_context_template_hash(p.template)] = p.template
        elif isinstance(p, SubagentPolicy):
            has_subagent = True
        elif isinstance(p, (PermissionPolicy, McpGatingPolicy)):
            # Native-surface: no hook table.
            continue
        else:  # pragma: no cover — mirror the CC compiler's guard
            raise ValueError(
                f"codex emitter: unsupported policy type {type(p).__name__}"
            )
    return events, context_templates, has_subagent


def _emitter_event_matcher(p: AnyPolicy) -> tuple[str | None, str | None]:
    """(event, matcher) for a hook-producing policy, or ``(None, None)``
    for a native-surface archetype (Permission / Mcp / Subagent) that has
    no trigger. Mirrors ``runtime.codex._policy_event_matcher`` without
    importing the runtime layer."""
    if isinstance(p, ContextInjectionPolicy):
        return (p.event, p.matcher)
    trig = getattr(p, "trigger", None)
    if trig is not None:
        return (trig.event, trig.matcher)
    return (None, None)


def _add_gap_shim_fallbacks(
    policies: list[AnyPolicy], events: dict[str, set[str]],
) -> bool:
    """Fold Shim A + Shim D fallback hook entries into ``events``.

    Shim A (Section 4.1): a PreToolUse policy on a silent-skip tool gets
    a ``PermissionRequest`` + ``PostToolUse`` audit hook on the same
    tool. Shim D (Section 4.4): a subagent-lifecycle policy gets
    parent-side ``spawn_agent`` PreToolUse + PostToolUse mirror hooks.

    ``events`` is a set-valued map, so a fallback that coincides with an
    existing primary hook (or another policy's fallback) dedupes for
    free and the caller's sort keeps the output byte-stable.

    Returns ``True`` when at least one Shim D ``spawn_agent`` mirror hook
    was added. ``spawn_agent`` (and therefore any PreToolUse/PostToolUse
    hook bound to it) is gated on ``features.multi_agent = true`` (design
    doc Section 2.5). A subagent-LIFECYCLE policy (Evidence / RunCommand /
    InputRewrite triggered on SubagentStart/SubagentStop) authored WITHOUT
    an accompanying ``SubagentPolicy`` would otherwise leave the mirror
    hooks bound to a tool Codex never enables — silently inert. The caller
    ORs this flag into ``has_subagent`` so the feature toggle is emitted
    whenever a mirror hook exists.
    """
    # Lazy import: the silent-skip tool list is canonical in the runtime
    # driver (per the P2 brief). Importing at call time (not module load)
    # keeps this pure policy-layer module free of a runtime import cycle.
    from ..runtime.codex import CODEX_SILENT_SKIP_TOOLS

    def _add(event: str, matcher: str) -> None:
        events.setdefault(event, set()).add(matcher)

    added_subagent_mirror = False
    for p in policies:
        event, matcher = _emitter_event_matcher(p)
        if event is None:
            continue
        # Shim A: PreToolUse silent-skip tool -> PermissionRequest +
        # PostToolUse audit fallback on the same tool.
        if event == "PreToolUse" and matcher in CODEX_SILENT_SKIP_TOOLS:
            _add("PermissionRequest", matcher)
            _add("PostToolUse", matcher)
        # Shim D: subagent lifecycle -> parent-side spawn_agent mirror.
        if event in _SUBAGENT_LIFECYCLE_EVENTS:
            _add("PreToolUse", _SUBAGENT_SPAWN_TOOL)
            _add("PostToolUse", _SUBAGENT_SPAWN_TOOL)
            added_subagent_mirror = True
    return added_subagent_mirror


def _toml_str(value: str) -> str:
    """Emit a TOML basic string literal for ``value``.

    Codex matchers + our fixed command are plain ASCII in practice, but
    escape the TOML-significant bytes defensively so a matcher containing
    a quote or backslash never breaks the file.
    """
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def compile_to_codex_requirements(
    policies: list[AnyPolicy],
) -> CodexRequirementsBundle:
    """Compile a list of any-typed policies to a Codex requirements
    bundle. Deterministic + byte-stable + order-invariant.

    Every policy is ``validate()``-d at the compile boundary (fail-fast,
    same as the CC compiler) and duplicate ids are rejected.
    """
    seen_ids: set[str] = set()
    for p in policies:
        p.validate()
        if p.id in seen_ids:
            raise ValueError(f"중복 policy id: {p.id!r}")
        seen_ids.add(p.id)

    events, context_templates, has_subagent = _hook_pairs(policies)
    # P2 Shim A + Shim D: fold the gap-shim fallback hooks in before the
    # deterministic sort so they share the byte-stability guarantee. A
    # Shim D mirror binds to ``spawn_agent``, which Codex only enables
    # under ``features.multi_agent = true`` — so a lifecycle-triggered
    # policy without an accompanying SubagentPolicy still forces the
    # feature toggle on, otherwise the mirror hooks would be inert.
    added_subagent_mirror = _add_gap_shim_fallbacks(policies, events)
    has_subagent = has_subagent or added_subagent_mirror

    # ── requirements.toml ────────────────────────────────────────────
    lines: list[str] = []
    lines.append("[features]")
    lines.append("hooks = true")
    if has_subagent:
        # multi_agent only when at least one subagent policy exists
        # (design doc Section 6.2).
        lines.append("multi_agent = true")
    lines.append("")

    for event in sorted(events):
        for matcher in sorted(events[event]):
            lines.append(f"[[hooks.{event}]]")
            lines.append(f"matcher = {_toml_str(matcher)}")
            lines.append(f"[[hooks.{event}.hooks]]")
            lines.append('type = "command"')
            lines.append(f"command = {_toml_str(CODEX_GATE_COMMAND)}")
            lines.append(f"timeout = {CODEX_HOOK_TIMEOUT_MS}")
            lines.append("")

    # Exactly one trailing newline; no double-blank at EOF.
    requirements_toml = "\n".join(lines).rstrip("\n") + "\n"

    # ── hooks.json sidecar (CC drop-in shape) ────────────────────────
    hooks_obj: dict[str, list[dict]] = {}
    for event in sorted(events):
        entries: list[dict] = []
        for matcher in sorted(events[event]):
            entries.append({
                "matcher": matcher,
                "hooks": [{
                    "type": "command",
                    "command": CODEX_GATE_COMMAND,
                    "timeout": CODEX_HOOK_TIMEOUT_MS,
                }],
            })
        hooks_obj[event] = entries
    hooks_json_sidecar = json.dumps(
        {"hooks": hooks_obj},
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )

    return CodexRequirementsBundle(
        requirements_toml=requirements_toml,
        hooks_json_sidecar=hooks_json_sidecar,
        context_templates=context_templates,
    )


__all__ = [
    "CodexRequirementsBundle",
    "compile_to_codex_requirements",
    "CODEX_GATE_COMMAND",
    "CODEX_HOOK_TIMEOUT_MS",
]
