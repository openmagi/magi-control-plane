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

P1 scope: every hook-producing policy maps to a Codex hook entry
pointing at the shared gate binary. The four gap shims (Section 4) land
in P2; this emitter does NOT yet add the ``PermissionRequest`` /
``PostToolUse`` fallbacks — it is the straight-through translation.
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
