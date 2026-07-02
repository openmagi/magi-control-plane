# Codex PermissionPolicy native lowering (design)

Status: **DESIGN. Grounds the `TODO(live-test P2)` follow-up from the Codex
adapter (`2026-06-30-codex-runtime-adapter-design.md` sec 11.4).**
Author: Kevin
Date: 2026-07-01

## 1. Why

Today the Codex adapter emits only `[features]` + `[[hooks.<Event>]]`
command-hook tables. `PermissionPolicy` / `McpGatingPolicy` /
`SubagentPolicy` are SKIPPED (`_coverage_status_for` returns
`codex_native_config_pending`). So on Codex, a Magi permission policy
("deny `Bash(rm -rf*)`", "deny `Read(**/*.env)`") produces NO native deny;
enforcement leans entirely on the PreToolUse hook, whose firing under
`codex exec` is itself unverified (D9). This closes that gap by lowering
permission policies to Codex's REAL, MDM-enforced permission surface.

The Codex permission schema is now CONFIRMED from the official docs
(developers.openai.com/codex/permissions, /rules) + a live 0.137.0 probe.
No fabrication: every key below is documented.

## 2. Confirmed Codex permission model

Two distinct native surfaces, both enforceable from managed
`requirements.toml`:

### 2.1 Permission profiles (filesystem + network): TOML

```toml
default_permissions = "magi-enforced"

[permissions.magi-enforced]
description = "Magi-managed enforcement profile"
extends = ":workspace"          # or :read-only / :danger-full-access / another

[permissions.magi-enforced.filesystem]
":minimal" = "read"

[permissions.magi-enforced.filesystem.":workspace_roots"]
"." = "write"
"**/*.env" = "deny"             # deny reads+writes under matching paths

[permissions.magi-enforced.network]
enabled = true

[permissions.magi-enforced.network.domains]
"api.openai.com" = "allow"
"tracking.example.com" = "deny"
```

- Filesystem values: `read` / `write` / `deny` (no "prompt" tier).
- Network domain values: `allow` / `deny` (no "prompt" tier).
- Built-in base profiles: `:read-only`, `:workspace`, `:danger-full-access`.

### 2.2 Command rules (execve): requirements.toml `[rules].prefix_rules`

CONFIRMED from /codex/enterprise/managed-configuration. The USER layer uses
Starlark `.rules` files, but the MANAGED enforcement layer ships command
rules INLINE in `requirements.toml` as TOML (no Starlark, no separate file):

```toml
[rules]
prefix_rules = [
  { pattern = [{ token = "rm" }, { any_of = ["-rf", "-fr"] }],
    decision = "forbidden",
    justification = "Magi policy <id>" },
]
```

- `pattern` is a list of token matchers: `{ token = "X" }` (exact) or
  `{ any_of = ["a", "b"] }` (alternation), matched as an argv PREFIX.
- Decision in requirements.toml is DENY-ONLY: `"prompt"` or `"forbidden"`
  (never `"allow"`; confirms F5). Rules merge with user `.rules`,
  most-restrictive wins (`forbidden > prompt > allow`).
- So a CC `allow` command policy has NO requirements.toml rule (allow is the
  default absent a deny); only `deny` -> `forbidden` and `ask` -> `prompt`
  are emitted.

### 2.3 Not expressible as a profile / rule

- MCP server/tool gating: the permission docs explicitly state profiles
  govern only filesystem + network, NOT MCP-tool invocation. `McpGatingPolicy`
  therefore has NO native profile expression and MUST remain on the hook
  path (PreToolUse on the mcp tool). Documented residual, not a silent hole.
- `ask` on a filesystem path or network host: neither surface has a "prompt"
  tier (fs = read/write/deny, net = allow/deny). A CC `ask` permission on a
  file/host cannot be a native profile tier and downgrades to the hook path.
- Subagent disable (`SubagentPolicy` -> CC `Agent(<type>)` deny): no profile
  primitive; stays on the `spawn_agent` hook + `features.multi_agent` toggle
  (already handled by the existing emitter).

## 3. Mapping: CC PermissionPolicy IR -> Codex

Routing keys off the CC tool prefix in `PermissionPolicy.pattern`
(`<Tool>(<args>)`, validated by `ir._PERMISSION_PATTERN_RE`).

| CC pattern              | Codex surface                    | deny        | allow        | ask         |
| ----------------------- | -------------------------------- | ----------- | ------------ | ----------- |
| `Bash(<cmd>)`           | Starlark `prefix_rule`           | forbidden   | allow        | prompt      |
| `Read(<path>)`          | profile filesystem rule          | deny        | read         | hook (no prompt tier) |
| `Edit/Write/MultiEdit/NotebookEdit(<path>)` | profile filesystem rule | deny | write | hook |
| `Glob/Grep/LS(<path>)`  | profile filesystem rule          | deny        | read         | hook        |
| `WebFetch/WebSearch(<host>)` | profile network.domains     | deny        | allow        | hook        |
| `mcp__<server>...`      | NONE (profiles cannot express)   | hook        | hook         | hook        |

Security-review hardening (2026-07-02):
- NETWORK is allowlist-only. A per-domain `deny` while other traffic flows
  is NOT natively expressible (enabling network to deny one host would open
  the rest to the base default), so a `deny` network policy reports a hook
  downgrade (`codex_net_deny_hook`), not `enforced`. An `allow` policy builds
  a strict allowlist: `enabled = true` + the allowed hosts + a closing
  `"*" = "deny"` so unlisted hosts fail closed. A deny-only set emits NO
  network table (the base `:workspace` already blocks all network).
- A `Bash` policy whose arg reduces to an EMPTY prefix (`Bash(*)`, bare
  `Bash`) is NOT emitted as a native rule (an empty `prefix_rule` pattern has
  unconfirmed match-all semantics and could be a silent no-op); it reports
  `codex_command_matchall_unverified` and rides the hook path.
- `_toml_str` escapes all C0 control chars (U+0000-U+001F) so a grammar-legal
  but control-char-bearing pattern can never make the managed file invalid
  TOML (which Codex could reject, dropping every deny = fail-open).
- Filesystem `allow` is honored (the path is granted) but does NOT by itself
  deny the rest; only `deny` tightens filesystem (same as CC allow-rule
  semantics). The base `extends = ":workspace"` sets the default posture.

Notes:
- Command decisions map 1:1 (allow/prompt/forbidden), the cleanest case.
- Filesystem `allow` splits by tool intent: read tools -> `read`, mutation
  tools -> `write`.
- `ask` is only expressible for commands (Starlark `prompt`); for fs/net it
  downgrades to the hook path, reported honestly in coverage.
- `PermissionPolicy.pattern` argument extraction: for `Bash(rm -rf*)` the
  Starlark `pattern` is the leading literal argv tokens (`["rm","-rf"]`); a
  trailing `*` is dropped (prefix match is inherent to `prefix_rule`). For
  file/host patterns the glob is used verbatim as the fs/domain key.

## 4. Emission strategy (Magi-owned managed profile)

Magi owns ONE managed profile `magi-enforced`, installed root-owned, forced
via `requirements.toml`. All keys below are CONFIRMED from the enterprise
managed-config doc:

1. Profile definition goes in the managed config layer
   (`/etc/codex/managed_config.toml`): `[permissions.magi-enforced]`
   `extends` a conservative base (`:workspace` by default; configurable),
   with filesystem + network rules from the mapped policies layered on.
2. `requirements.toml` (`/etc/codex/requirements.toml`) carries the ENFORCE
   surface:
   - `default_permissions = "magi-enforced"`.
   - `[allowed_permission_profiles]` table with `"magi-enforced" = true`
     (and nothing else true) so the user cannot select a weaker profile.
   - `[rules]` `prefix_rules = [...]` for the command (Bash) denies/asks.
3. `CodexRequirementsBundle` grows: `permissions_toml` (the profile block for
   managed_config.toml) and the `requirements.toml` gains the
   `[allowed_permission_profiles]` + `[rules]` sections. Deterministic +
   byte-stable like today.

Deny-only-in-requirements nuance (F5): a profile may contain `allow`, but a
managed requirements.toml RULE can only tighten (prompt/forbidden). Magi's
enforcement policies are deny/ask (tightening) so this is satisfied; a Magi
`allow` permission policy lowers to a profile `read`/`write`/`allow` grant on
the OWNED profile (which is legal in a profile definition), not a
requirements.toml rule.

## 5. Coverage reporting changes

`_coverage_status_for` flips the pending statuses to real ones:
- Command/file/network deny/allow -> `enforced` (native profile / rule).
- Command `ask` -> `enforced` (Starlark `prompt`).
- File/network `ask` -> `codex_no_prompt_tier` downgrade -> hook fallback.
- `McpGatingPolicy` -> `codex_no_native_mcp_profile` downgrade -> hook path.
- `SubagentPolicy` -> unchanged (multi_agent + spawn_agent hook).

## 6. Build plan (PRs, all default-ON flag, additive, TDD)

- **PR1 (schema confirm + envelope):** confirm the two open wiring details
  (managed `.rules` vs inline `prefix_rule` in requirements.toml;
  `allowed_permission_profiles` exact key) from the enterprise doc / a live
  probe. Add `permissions_toml` + `command_rules` to `CodexRequirementsBundle`
  and the profile envelope + `default_permissions` + allowlist. Golden tests.
- **PR2 (filesystem lowering):** `Read/Edit/Write/...` PermissionPolicy ->
  `[permissions.magi-enforced.filesystem...]` read/write/deny. Tests +
  coverage flip.
- **PR3 (network lowering):** `WebFetch/WebSearch` -> `network.domains`
  allow/deny. Tests + coverage flip.
- **PR4 (command lowering):** `Bash` -> Starlark `prefix_rule`
  allow/prompt/forbidden, byte-stable emitter + argv extraction. Tests.
- **PR5 (residuals + coverage honesty):** MCP + fs/net `ask` downgrade
  wiring, coverage_report statuses, installer writes the new artifacts,
  design-doc sec 11.4 reconciliation (`TODO(live-test P2)` -> done for the
  native surface; MCP residual documented). Multi-angle review + merge.

## 7. Confirmed wiring (was: open items) + remaining probe

CONFIRMED from /codex/enterprise/managed-configuration (no longer open):
- Profile allowlist key = `[allowed_permission_profiles]` table
  (`"<profile>" = true|false`) + top-level `default_permissions = "<name>"`.
- Managed command rules ride INLINE in `requirements.toml` under `[rules]`
  as `prefix_rules = [{ pattern = [{token=...}|{any_of=[...]}], decision =
  "prompt"|"forbidden", justification = "..." }]`. No managed Starlark file.
- Also available (out of scope for v1): `allowed_approval_policies`,
  `allowed_sandbox_modes`.

REMAINING (does NOT block the build, additive on the default-ON flag):
- Whether `codex exec` (headless) actually honors managed permission
  profiles + `[rules].prefix_rules` (the D9 firing question, for the
  PERMISSION surface). Needs a rooted `/etc/codex` install probe. The
  lowering is emitted correctly regardless; this only affects the coverage
  claim under headless exec.
