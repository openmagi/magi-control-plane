"""Deterministic Policy IR → CC managed-settings.json compiler.

Guarantees: pure function (no LLM, no clock, no randomness), same input ⇒ same
output (byte-stable). Policy order preserved in `_magi_policies` meta.

P2/P3 — hybrid compilation. The compiler emits to the *real* CC
managed-settings buckets per archetype:

  EvidencePolicy        → hooks.<event>[]  ({type: "command"} → gate_binary)
  PermissionPolicy      → permissions.{allow,deny,ask}[]
  SubagentPolicy        → permissions.deny += ["Agent(<subagent_type>)"]
                          (binary disable; per-subagent tool scoping requires
                          a Markdown sidecar in .claude/agents/, out of v1
                          scope — see ir.SubagentPolicy docstring)
  McpGatingPolicy       → allowedMcpServers[] / deniedMcpServers[]
                          (top-level `mcp.*` map does NOT exist in the CC
                          schema; see Issue #1 P0 #10)
  ContextInjectionPolicy → hooks.<event>[]  ({type: "command"} → context-write
                          shim emitting the template via additionalContext;
                          the speculative {type: "write"} entry is NOT a
                          valid CC hook type — see Issue #1 P0 #3/#8)

The native-surface routes (permission/subagent/mcp) do not hit the
gate-binary at runtime — CC consumes them out of managed-settings
directly. EvidencePolicy + ContextInjectionPolicy both compile to
`{type: "command"}` hook entries; the former calls the gate binary,
the latter calls a tiny context-write shim.

Managed-only exclusivity flags (issue #1 P0 #11): each native-surface
bucket pairs with its corresponding exclusivity key when *any* policy
of that archetype opts in (the default). Authors who explicitly want
their floor to be additive set `exclusive=False` on the policy.
"""
from __future__ import annotations
import hashlib
import json
import sys

from .ir import (
    AnyPolicy, ContextInjectionPolicy, EvidencePolicy, McpGatingPolicy,
    PermissionPolicy, SubagentPolicy, load_policy,
)


# Default shim binaries — operators can override at deploy time via
# explicit policy fields (not implemented in v1; current value is the
# documented install path). The context shim resolves the template by
# sha256 against a sidecar directory.
DEFAULT_CONTEXT_WRITE_SHIM = "/usr/local/bin/magi-cp-context-write"


def _context_template_hash(template: str) -> str:
    """Stable sha256(template) used as the sidecar filename and the
    `--id` arg the shim receives. Pure function of the template bytes."""
    return hashlib.sha256(template.encode("utf-8")).hexdigest()


def compile_to_managed_settings(policies: list[AnyPolicy]) -> dict:
    """Compile a list of any-typed policies to CC managed-settings.

    Deterministic: same input list ⇒ byte-identical output. Policy order
    is preserved both in the per-bucket emission order and in the
    `_magi_policies` meta list (the audit trail of what compiled into
    this settings file).
    """
    seen_ids: set[str] = set()
    for p in policies:
        # Each archetype owns its own validate(); call it for fail-fast
        # at the compile boundary in addition to construction time.
        p.validate()
        if p.id in seen_ids:
            raise ValueError(f"중복 policy id: {p.id!r}")
        seen_ids.add(p.id)

    # EvidencePolicy carries trigger.host; the rest do not need a host
    # gate (they're settings-shape primitives). Only check evidence.
    for p in policies:
        if isinstance(p, EvidencePolicy) and p.trigger.host != "claude-code":
            raise ValueError(
                f"policy '{p.id}': host 'claude-code'만 지원(v0); "
                f"got {p.trigger.host!r}"
            )

    permissions: dict[str, list[str] | str] = {
        "allow": [], "deny": [], "ask": [], "defaultMode": "default",
    }
    hooks: dict[str, list[dict]] = {}
    # Issue #1 P0 (#10): real CC keys.
    allowed_mcp_servers: list[dict] = []
    denied_mcp_servers: list[dict] = []
    # Issue #1 P0 (#3, #8): template content sidecar so the shim can
    # resolve by sha. The compiler returns it alongside the settings
    # dict so `compile_files` can persist both atomically.
    context_templates: dict[str, str] = {}

    # Track which native-surface buckets had any exclusive-mode policy
    # → set the corresponding managed-only flag.
    permission_exclusive = False
    mcp_exclusive = False

    for p in policies:
        if isinstance(p, PermissionPolicy):
            permissions[p.permission].append(p.pattern)
            if p.exclusive:
                permission_exclusive = True
        elif isinstance(p, SubagentPolicy):
            # Issue #1 P0 (#9): binary disable via
            # permissions.deny: ["Agent(<name>)"]. The per-subagent tool
            # allowlist is rejected at validate() time (no compile
            # target in v1).
            permissions["deny"].append(f"Agent({p.subagent_type})")
            # Subagent disables are always exclusive — the goal is fleet
            # lockdown of the named subagent.
            permission_exclusive = True
        elif isinstance(p, McpGatingPolicy):
            # Issue #1 P0 (#10): real CC arrays.
            entry = {"serverName": p.server}
            if p.action == "allow":
                allowed_mcp_servers.append(entry)
                if p.exclusive:
                    mcp_exclusive = True
            else:
                denied_mcp_servers.append(entry)
        elif isinstance(p, ContextInjectionPolicy):
            # Issue #1 P0 (#3, #8): {type: "command"} + shim invocation.
            # Template bytes ship in the sidecar dict keyed by sha256;
            # the shim reads `<sidecar-dir>/<sha>` and prints
            # `{"hookSpecificOutput": {"hookEventName": <event>,
            #   "additionalContext": <template>}}`.
            tpl_id = _context_template_hash(p.template)
            context_templates[tpl_id] = p.template
            hooks.setdefault(p.event, []).append({
                "matcher": p.matcher,
                "hooks": [{
                    "type": "command",
                    "command": (
                        f"{DEFAULT_CONTEXT_WRITE_SHIM} --event {p.event} "
                        f"--id {tpl_id}"
                    ),
                }],
            })
        elif isinstance(p, EvidencePolicy):
            hooks.setdefault(p.trigger.event, []).append({
                "matcher": p.trigger.matcher,
                "hooks": [{"type": "command", "command": p.gate_binary}],
            })
        else:
            raise ValueError(
                f"compiler: unsupported policy type {type(p).__name__}"
            )

    settings: dict = {
        "allowManagedHooksOnly": True,
        # Issue #1 P0 (#11): pair each native-surface bucket with its
        # exclusivity flag when any policy in that bucket opted in.
        # Default is exclusive=True; authors can opt out per policy.
        "permissions": permissions,
        "hooks": hooks,
        "allowedMcpServers": allowed_mcp_servers,
        "deniedMcpServers": denied_mcp_servers,
        "_magi_policies": [
            {
                "id": p.id,
                "type": _policy_type_label(p),
                "version": p.version,
                "description": p.description,
            }
            for p in policies
        ],
    }
    if permission_exclusive:
        settings["allowManagedPermissionRulesOnly"] = True
    if mcp_exclusive:
        settings["allowManagedMcpServersOnly"] = True
    if context_templates:
        # Operator-readable metadata (not consumed by CC) describing the
        # sidecar files the gate package is expected to materialize.
        settings["_magi_context_templates"] = sorted(context_templates.keys())
    return settings


def context_template_sidecars(policies: list[AnyPolicy]) -> dict[str, str]:
    """Return the {sha256: template_bytes} sidecar map for the given
    policy set. Pure function; deterministic. Separated from
    `compile_to_managed_settings` so the JSON output stays
    CC-consumable (no compiler-private keys leak into managed-settings).
    """
    out: dict[str, str] = {}
    for p in policies:
        if isinstance(p, ContextInjectionPolicy):
            out[_context_template_hash(p.template)] = p.template
    return out


def _policy_type_label(p: AnyPolicy) -> str:
    """Surface a stable string per archetype for the meta list. The
    dataclass `type` field already carries the right value; we read it
    rather than `type(p).__name__` so a future rename of the dataclass
    doesn't change the on-disk meta."""
    return getattr(p, "type", "evidence")


def compile_files(policy_paths: list[str], out_path: str) -> dict:
    """Compile a list of on-disk policy JSON files to a managed-settings
    bundle on disk.

    Issue #1 P0 (#3, #8) & non-blocking #a: the bundle is a *directory*
    when any ContextInjectionPolicy is present (managed-settings.json
    sits alongside `context-templates/<sha>.txt` sidecar files). When
    no context policies are present we keep the original single-file
    layout for back-compat. The digest written to the file is the
    sha256 of the bytes the gate hashes (so dashboard
    `compiled_sha256` and gate `active_policy_digest` align).
    """
    import os
    policies = [load_policy(p) for p in policy_paths]
    settings = compile_to_managed_settings(policies)
    # Sidecar bytes live outside the JSON file so the managed-settings
    # blob CC reads stays valid.
    sidecars = context_template_sidecars(policies)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    if sidecars:
        # Write to <out_dir>/context-templates/<sha>.txt — the shim's
        # documented location. Operator overrides via
        # MAGI_CP_CONTEXT_TEMPLATES_DIR are honored downstream by the
        # gate package; this is the install-time default.
        out_dir = os.path.dirname(out_path) or "."
        side_dir = os.path.join(out_dir, "context-templates")
        os.makedirs(side_dir, exist_ok=True)
        for sha, body in sidecars.items():
            with open(os.path.join(side_dir, f"{sha}.txt"),
                      "w", encoding="utf-8") as f:
                f.write(body)
    return settings


def main() -> int:  # pragma: no cover (CLI shim)
    if len(sys.argv) < 3:
        print("usage: python -m magi_cp.policy.compiler <policy.json> [...] <out.json>",
              file=sys.stderr)
        return 2
    compile_files(sys.argv[1:-1], sys.argv[-1])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
