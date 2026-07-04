# Runtimes

The gate enforces the same Policy IR across two host runtimes: **Claude
Code** and **Codex**. The gate auto-detects which one it is talking to
from the incoming hook payload; force it with `MAGI_CP_RUNTIME=cc|codex`.

## Claude Code

The primary surface. The operator installs `managed-settings.json` with a
`PreToolUse` (and `SessionStart`) hook that runs `magi-gate.sh`. On each
event the gate reads the hook JSON on stdin and returns a decision as JSON
on stdout (allow, or `permissionDecision:"deny"`). This is the path the
one-line installer wires by default. See [Install](./install.md).

Every archetype in [Policy IR](./policy-ir.md) is available here: the
gate-binary `EvidencePolicy`, the native `permissions.{allow,deny,ask}`
`PermissionPolicy`, the session-evidence pair, and the rest.

## Codex

Codex is supported via a native lowering adapter (default-on,
`MAGI_CP_CODEX_RUNTIME_ENABLED=1`). Rather than a stdin hook, a
`PermissionPolicy` is lowered to Codex's own permission surface:

- filesystem / network rules compile to a Codex permission profile;
- command rules compile to a `prefix_rule` in a managed
  `requirements.toml`.

A matcher translator bridges the two tool vocabularies (for example
Claude Code's `Bash` maps to Codex's `exec_command`, `Edit` to
`apply_patch`) so a single authored policy covers both runtimes.

### Install and enforce

```bash
magi-cp install --runtime codex
```

For a hard control the managed enforcement config lives at
`/etc/codex/requirements.toml` (override the dir with
`MAGI_CP_CODEX_ETC_DIR`). A user-level Codex config is advisory; the
root-installed managed file is the boundary, the same way
`managed-settings.json` is the boundary for Claude Code. Codex rules are
deny-oriented: they narrow what the agent may do, they do not grant new
capability.

### What does not lower to Codex

Archetypes that need the gate-binary round-trip or a Claude-Code-specific
hook channel (the `EvidencePolicy` verifier gate, `ContextInjectionPolicy`
via `additionalContext`, and the session-evidence pair) are Claude Code
only today. `PermissionPolicy` is the archetype that lowers cleanly to
both.

## See also

- [Architecture](./architecture.md#runtimes) for where runtimes sit in
  the three-layer model.
- [CLI > install](./cli.md#install) for the adapter installer.
