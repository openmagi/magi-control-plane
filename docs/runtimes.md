# Runtimes

The gate enforces the same Policy IR across four host runtimes: **Claude
Code**, **Codex**, **Gajae-Code** (`gjc`), and **Hermes**. One authored
policy, whichever runtime the agent runs under.

## How a runtime is selected

Each request resolves to exactly one runtime, in this order:

1. **Explicit override**: `MAGI_CP_RUNTIME` (`cc` / `codex` / `gjc` /
   `hermes`, plus aliases like `gajae`). Managed configs set this so the
   sandbox never guesses. A runtime named here but disabled degrades to
   `cc` rather than routing.
2. **Payload sniff**: the drivers are disjoint by construction. A `gjc`
   payload carries `gjc_event`, a Codex payload carries
   `matcher_aliases` / `turn_id`, a Hermes payload is snake_case with an
   `extra` key, and Claude Code is the fallback. First match wins (gjc,
   then Codex, then Hermes).

Each non-CC runtime has an availability flag, all **default-on**
(`MAGI_CP_{CODEX,GJC,HERMES}_RUNTIME_ENABLED`; set an explicit falsy value
to force `cc` as a kill switch). Default-on means "selectable", not
"applied": a tenant whose `runtime_id` is the default `claude-code` still
routes to CC until it is switched. The dashboard runtime picker
(`/settings`) currently exposes Claude Code and Codex; `gjc` and `hermes`
are selected via `MAGI_CP_RUNTIME` or a managed config.

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

## Gajae-Code (gjc)

Gajae-Code is governed through a frozen shim: a small, pinned bundle the
agent's tool calls pass through, which dispatches to the gate via
`magi-cp gate --runtime gjc`. On stdin the shim sends a `tool_call`
envelope carrying a `gjc_event`; on stdout the gate returns
`{"block": true, "reason": "MAGI: ..."}` for a deny (an `ask` is
downgraded to a deny-with-guidance) or an empty body for allow. No policy
logic lives in the shim; the gate binary stays the single evaluator.

Unmapped tool names pass through raw (an `ssh` stays `ssh`), so nothing
slips past the matcher silently. Install the shim bundle and run the
health checks with:

```bash
magi-cp install --runtime gjc
magi-cp doctor
```

## Hermes

Hermes (`NousResearch/hermes-agent`) speaks a declarative shell-hook wire
that is already Claude-Code-compatible on the block channel, so the driver
needs no verdict-field remap. It does carry a tool-name normalization
table over Hermes's ~70-name registry (the CC-mappable core is ~20); every
other name passes through raw under an "allow + audit" posture, tagged
`hermes_unmapped_tool`, so an unmapped call is recorded rather than
silently allowed.

Hermes is a driver-level runtime today: detection and the gate contract
are wired, selected via `MAGI_CP_RUNTIME=hermes` or a managed config.
There is no dedicated first-party installer yet (unlike Codex and gjc).

## See also

- [Architecture](./architecture.md#runtimes) for where runtimes sit in
  the three-layer model.
- [CLI > install](./cli.md#install) for the adapter installer.
