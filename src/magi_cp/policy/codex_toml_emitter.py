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
    with ``--dangerously-bypass-hook-trust``, proven empirically (tool ran,
    zero hook fires). So the ENFORCED path is the MANAGED
    ``/etc/codex/requirements.toml`` layer (``ManagedHooksRequirements``,
    precedence mdm > system > project > session_flags > plugin), and the
    working interactive registration shape is a PLUGIN ``hooks.json`` (nested
    ``{"hooks": {Event: [{matcher, hooks:[{type,command}]}]}}``), which is
    what ``hooks_json_sidecar`` already emits. Permission DECISIONS in
    requirements.toml are deny-only (``forbidden``/``prompt``, never
    ``allow``; most-restrictive merge). Do NOT ship enforcement via user
    config.toml hooks; that surface is a ``codex exec`` gate-bypass.
    """

    requirements_toml: str
    hooks_json_sidecar: str
    context_templates: dict[str, str] = field(default_factory=dict)
    # PermissionPolicy native lowering (design 2026-07-01): the
    # ``[permissions.<profile>]`` profile block that defines the Magi-owned
    # enforcement profile (filesystem + network rules). Installed to the
    # managed config layer (``/etc/codex/managed_config.toml``), separate
    # from ``requirements.toml`` which carries the profile allowlist +
    # command ``[rules].prefix_rules``. Empty string when no
    # PermissionPolicy maps to a filesystem/network rule.
    permissions_toml: str = ""


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


def _emit_matchers(matchers: set[str]) -> list[str]:
    """Translate a set of CC tool-name matchers to Codex tool names and
    return them sorted + deduped (§11.4 F4).

    The internal ``events`` map is keyed on CC tool names (the IR grammar +
    the Shim A/D deny-lists all reason in CC names); translation happens
    ONLY here at the final emit boundary. Dedup is post-translation because
    distinct CC tools can collapse to one Codex tool (``Edit`` + ``Write``
    both -> ``apply_patch``); collapsing before the sort keeps the output
    byte-stable and avoids emitting two identical hook tables.
    """
    # Lazy import mirrors the CODEX_SILENT_SKIP_TOOLS import below: the
    # tool namespace is canonical in the runtime driver, kept out of this
    # pure policy-layer module's import graph.
    from ..runtime.codex import translate_matcher_cc_to_codex

    return sorted({translate_matcher_cc_to_codex(m) for m in matchers})


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
    # Escape every remaining C0 control char (U+0000-U+001F) as \uXXXX. The
    # IR permission grammar admits any arg byte except ``)`` and ``\n``
    # (``[^)\n]``), so a bare ``\r`` / NUL would otherwise land raw in a TOML
    # basic string and make the whole managed file INVALID -> Codex could
    # reject it and fall back to defaults (fail-open, every deny drops).
    escaped = "".join(
        c if ord(c) >= 0x20 or c in "\\\"" else f"\\u{ord(c):04x}"
        for c in escaped
    )
    return f'"{escaped}"'


# ── PermissionPolicy native lowering (design 2026-07-01) ─────────────
# The Magi-owned managed enforcement profile. Its filesystem/network rules
# live in ``managed_config.toml``; ``requirements.toml`` forces it via
# ``default_permissions`` + ``[allowed_permission_profiles]`` and carries
# command denies as ``[rules].prefix_rules``. All keys confirmed from
# developers.openai.com/codex/permissions + /enterprise/managed-configuration.
CODEX_PERMISSION_PROFILE = "magi-enforced"
CODEX_PERMISSION_BASE = ":workspace"

# CC tool -> Codex native surface (routing by the tool prefix of
# ``PermissionPolicy.pattern`` = ``<Tool>(<args>)``).
_FS_READ_TOOLS: frozenset[str] = frozenset({"Read", "Glob", "Grep"})
_FS_WRITE_TOOLS: frozenset[str] = frozenset({"Write", "Edit", "MultiEdit",
                                             "NotebookEdit"})
_NET_TOOLS: frozenset[str] = frozenset({"WebFetch", "WebSearch"})
# Restrictiveness rank for most-restrictive-wins merge on a shared key.
_FS_RANK = {"read": 0, "write": 1, "deny": 2}
_NET_RANK = {"allow": 0, "deny": 1}


@dataclass(frozen=True)
class PermissionLowering:
    """Structured result of lowering PermissionPolicy/McpGatingPolicy onto
    Codex native surfaces. Everything sorted/merged for byte-stability."""
    fs_rules: dict[str, str]           # glob -> read|write|deny
    net_domains: dict[str, str]        # host -> allow|deny
    command_rules: list[dict]          # {tokens: [...], decision, justification}
    hook_residual_ids: list[str]       # policies with no native surface


def _parse_permission_pattern(pattern: str) -> tuple[str, str]:
    """Split a CC ``PermissionPolicy.pattern`` into (tool, args).

    ``Bash(rm -rf *)`` -> ("Bash", "rm -rf *");
    ``Read(/etc/**)`` -> ("Read", "/etc/**");
    ``mcp__server__tool(x)`` -> ("mcp", "x"); bare ``Agent`` -> ("Agent", "").
    The tool is the leading identifier before ``(`` or the first ``__``.
    """
    head = pattern
    args = ""
    if "(" in head:
        head, _, rest = head.partition("(")
        args = rest[:-1] if rest.endswith(")") else rest
    tool = head.split("__", 1)[0]
    return tool, args.strip()


def _command_tokens(args: str) -> list[str]:
    """Extract argv prefix tokens from a CC ``Bash(...)`` arg body.

    CC uses a trailing ``:*`` or ``*`` to mean "prefix match"; Codex
    ``prefix_rule`` is inherently a prefix, so the wildcard tail is dropped.
    ``git push:*`` -> ["git", "push"]; ``rm -rf *`` -> ["rm", "-rf"].
    An empty body (bare ``Bash``) -> [] (matches all commands).
    """
    a = args.strip()
    if a.endswith(":*"):
        a = a[:-2]
    elif a.endswith("*"):
        a = a[:-1]
    return a.strip(": ").split()


def _lower_permissions(policies: list[AnyPolicy]) -> PermissionLowering:
    """Route PermissionPolicy/McpGatingPolicy onto Codex native surfaces.

    Filesystem/network ``ask`` has no native prompt tier, and MCP gating is
    not profile-expressible (per the permissions docs), so those fall to the
    hook path and are reported as ``hook_residual_ids``. Command ``allow``
    needs no rule (allow is the default absent a deny), so only ``deny`` ->
    ``forbidden`` and ``ask`` -> ``prompt`` emit a ``prefix_rule``.
    """
    fs: dict[str, str] = {}
    net: dict[str, str] = {}
    commands: list[dict] = []
    residual: list[str] = []

    def _merge_fs(glob: str, tier: str) -> None:
        cur = fs.get(glob)
        if cur is None or _FS_RANK[tier] > _FS_RANK[cur]:
            fs[glob] = tier

    def _merge_net(host: str, val: str) -> None:
        cur = net.get(host)
        if cur is None or _NET_RANK[val] > _NET_RANK[cur]:
            net[host] = val

    for p in policies:
        if isinstance(p, McpGatingPolicy):
            # MCP tool gating is not expressible as a permission profile;
            # it stays on the hook path (design 2.3).
            residual.append(p.id)
            continue
        if not isinstance(p, PermissionPolicy):
            continue
        tool, args = _parse_permission_pattern(p.pattern)
        decision = p.permission  # allow | deny | ask

        if tool == "Bash":
            toks = _command_tokens(args)
            if not toks:
                # A bare ``Bash`` / ``Bash(*)`` reduces to an empty prefix.
                # An empty ``prefix_rule`` pattern has UNCONFIRMED match-all
                # semantics (could match none = a silent no-op deny), so we
                # do NOT emit an ambiguous native rule; the hook path handles
                # a "deny all bash" intent instead (reported as a downgrade).
                residual.append(p.id)
            elif decision == "deny":
                commands.append({"tokens": toks,
                                 "decision": "forbidden", "id": p.id})
            elif decision == "ask":
                commands.append({"tokens": toks,
                                 "decision": "prompt", "id": p.id})
            # allow -> no rule (default). Nothing emitted.
            continue

        if tool in _FS_READ_TOOLS or tool in _FS_WRITE_TOOLS:
            glob = args or "**"
            if decision == "deny":
                _merge_fs(glob, "deny")
            elif decision == "allow":
                _merge_fs(glob, "write" if tool in _FS_WRITE_TOOLS else "read")
            else:  # ask: no filesystem prompt tier -> hook path
                residual.append(p.id)
            continue

        if tool in _NET_TOOLS:
            host = args
            if host.startswith("domain:"):
                host = host[len("domain:"):]
            host = host or "*"
            if decision == "deny":
                _merge_net(host, "deny")
            elif decision == "allow":
                _merge_net(host, "allow")
            else:  # ask: no network prompt tier -> hook path
                residual.append(p.id)
            continue

        # Agent / Task / TodoWrite / anything else: no native permission
        # surface (subagent handled via multi_agent + spawn_agent hook).
        residual.append(p.id)

    # Sort command rules deterministically (by tokens then decision).
    commands.sort(key=lambda c: (c["tokens"], c["decision"], c["id"]))
    return PermissionLowering(
        fs_rules=fs, net_domains=net,
        command_rules=commands, hook_residual_ids=sorted(residual),
    )


def permission_native_status(p: PermissionPolicy) -> tuple[str, str | None]:
    """Codex coverage ``(status, downgrade)`` for a single PermissionPolicy.

    Mirrors the routing in ``_lower_permissions`` so ``coverage_report`` can
    tell the operator whether a permission policy lands on a real native
    surface (``enforced``) or falls back to the hook path. Kept here so the
    routing table lives in exactly one place.
    """
    # A genuinely native surface returns downgrade=None (renders GREEN /
    # enforced). Only a hook fallback sets a downgrade note (renders amber).
    tool, args = _parse_permission_pattern(p.pattern)
    if tool == "Bash":
        if not _command_tokens(args):
            # Empty prefix -> ambiguous match-all; not emitted natively.
            return ("codex_command_matchall_unverified",
                    "hook PreToolUse fallback (empty command prefix)")
        # deny -> forbidden, ask -> prompt, allow -> default (all honored
        # by the requirements.toml prefix_rule / profile default).
        return ("enforced", None)
    if tool in _FS_READ_TOOLS or tool in _FS_WRITE_TOOLS:
        if p.permission == "ask":
            return ("codex_no_prompt_tier",
                    "hook PreToolUse fallback (fs has no prompt tier)")
        return ("enforced", None)
    if tool in _NET_TOOLS:
        if p.permission == "ask":
            return ("codex_no_prompt_tier",
                    "hook PreToolUse fallback (network has no prompt tier)")
        if p.permission == "deny":
            # Codex network is allowlist-only: a per-domain DENY while other
            # traffic flows is not natively expressible (turning network on
            # to deny one host would open the rest). The hook path enforces
            # a specific-domain deny.
            return ("codex_net_deny_hook",
                    "hook PreToolUse fallback (Codex network is allowlist-only)")
        return ("enforced", None)  # allow builds the default-deny allowlist
    if tool == "mcp":
        return ("codex_no_native_mcp_profile", "hook PreToolUse on the mcp tool")
    # Agent / Task / TodoWrite / anything else: no native permission surface.
    return ("codex_no_native_permission", "hook PreToolUse fallback")


def _emit_prefix_rule(rule: dict) -> str:
    """One inline TOML ``prefix_rule`` table for ``[rules].prefix_rules``."""
    toks = ", ".join(f"{{ token = {_toml_str(t)} }}" for t in rule["tokens"])
    return (
        f"  {{ pattern = [{toks}], decision = {_toml_str(rule['decision'])}, "
        f"justification = {_toml_str('Magi policy ' + rule['id'])} }},"
    )


def _emit_permissions_profile(low: PermissionLowering) -> str:
    """The ``[permissions.<profile>]`` block for ``managed_config.toml``.

    Empty string when there are no filesystem/network rules (the profile
    would be a no-op envelope). Deterministic: globs/hosts sorted.
    """
    # Network is only emitted for an ALLOWLIST intent (>=1 allow domain).
    # Codex network is default-deny when any allow entry exists ("If there
    # are no allow entries, domain requests are blocked", per the permissions
    # docs), so the listed allows ARE the allowlist and unlisted hosts are
    # denied automatically. A deny-only set turning network ON would OPEN
    # everything else, so fail-closed: deny-only -> no network table (the
    # base :workspace already blocks all network); allowlist -> enable + the
    # allow hosts. NOTE (live probe 2026-07-02): a bare ``"*"`` is a valid
    # domain key only as an ALLOW (global wildcard); Codex REJECTS ``"*" =
    # "deny"`` ("allowed exact hosts or scoped wildcards like *.example.com").
    # So no explicit default-deny tail is emitted (it is both invalid and
    # redundant with the default-deny-when-allowlisted semantics).
    net_allow = any(v == "allow" for v in low.net_domains.values())
    emit_net = low.net_domains and net_allow
    if not low.fs_rules and not emit_net:
        return ""
    lines: list[str] = []
    lines.append(f"[permissions.{CODEX_PERMISSION_PROFILE}]")
    lines.append('description = "Magi-managed enforcement profile"')
    lines.append(f"extends = {_toml_str(CODEX_PERMISSION_BASE)}")
    lines.append("")
    if low.fs_rules:
        lines.append(
            f'[permissions.{CODEX_PERMISSION_PROFILE}.filesystem.'
            f'":workspace_roots"]'
        )
        for glob in sorted(low.fs_rules):
            lines.append(f"{_toml_str(glob)} = {_toml_str(low.fs_rules[glob])}")
        lines.append("")
    if emit_net:
        # The allow hosts ARE the allowlist; unlisted hosts are denied by
        # default. No bare "*"="deny" tail (invalid + redundant).
        lines.append(f"[permissions.{CODEX_PERMISSION_PROFILE}.network]")
        lines.append("enabled = true")
        lines.append("")
        lines.append(
            f"[permissions.{CODEX_PERMISSION_PROFILE}.network.domains]"
        )
        for host in sorted(low.net_domains):
            lines.append(
                f"{_toml_str(host)} = {_toml_str(low.net_domains[host])}"
            )
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


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

    # PermissionPolicy native lowering (design 2026-07-01).
    low = _lower_permissions(policies)
    permissions_toml = _emit_permissions_profile(low)
    has_profile = bool(permissions_toml)

    # ── requirements.toml ────────────────────────────────────────────
    lines: list[str] = []
    # Top-level scalars must precede every table (TOML). ``default_permissions``
    # forces the Magi-owned profile when it carries filesystem/network rules.
    if has_profile:
        lines.append(
            f"default_permissions = {_toml_str(CODEX_PERMISSION_PROFILE)}"
        )
        lines.append("")
    lines.append("[features]")
    lines.append("hooks = true")
    if has_subagent:
        # multi_agent only when at least one subagent policy exists
        # (design doc Section 6.2).
        lines.append("multi_agent = true")
    lines.append("")

    # Profile allowlist: only the Magi profile is selectable, so the user
    # cannot fall back to a weaker one (the MDM enforcement).
    if has_profile:
        lines.append("[allowed_permission_profiles]")
        lines.append(f"{_toml_str(CODEX_PERMISSION_PROFILE)} = true")
        lines.append("")

    # Command denies/asks as inline prefix_rules (deny-only: prompt/forbidden).
    if low.command_rules:
        lines.append("[rules]")
        lines.append("prefix_rules = [")
        for rule in low.command_rules:
            lines.append(_emit_prefix_rule(rule))
        lines.append("]")
        lines.append("")

    for event in sorted(events):
        for matcher in _emit_matchers(events[event]):
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
        for matcher in _emit_matchers(events[event]):
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
        permissions_toml=permissions_toml,
    )


__all__ = [
    "CodexRequirementsBundle",
    "compile_to_codex_requirements",
    "PermissionLowering",
    "CODEX_PERMISSION_PROFILE",
    "CODEX_GATE_COMMAND",
    "CODEX_HOOK_TIMEOUT_MS",
]
