# Architecture

`magi-control-plane` is a three-layer system. Each layer has a single,
clear job; the line between them is the security boundary.

```
+----------------+      +-----------------+      +-------------------+
|  Local         |  ->  |  Cloud          |  ->  |  Floor            |
|  CLI + hook    |      |  policy + ledger|      |  Claude Code      |
|  ~/.magi-cp    |      |  Ed25519 signer |      |  managed-settings |
+----------------+      +-----------------+      +-------------------+
```

## Local

The `magi-cp` CLI plus the `PreToolUse` hook on the user's machine.

- Client-side only. No signing keys. No LLM calls.
- Reads the command on stdin from Claude Code, matches a sentinel regex,
  checks the WAL for a verifier token bound to `(subject, payload_hash)`,
  and exits `0` (allow) or prints a JSON deny on stdout.
- WAL path: `~/.magi-cp/local/wal.jsonl` (override with `MAGI_CP_LOCAL_DIR`).
- Pubkey cache: per-`kid` cache so signature verification is offline-fast.

Trust model: the local layer treats the cloud as authoritative. Cloud
unreachable means fail-closed. License expiry means the gate denies all
sentinel commands until renewed.

## Cloud

The FastAPI service (`src/magi_cp/cloud/`).

- **Policy authority.** `policies/` rows are the source of truth for what
  the local gate enforces. The compiler in `src/magi_cp/policy/compiler.py`
  turns IR rows into a single `managed-settings.json` blob.
- **Verifier registry.** `src/magi_cp/verifier/` hosts a pluggable
  registry. The 5 wired verifiers (citation, privilege scan, source
  allowlist, structured output, prompt-injection screen) live in
  `verifier/builtins.py`. See [Verifiers](./verifiers.md).
- **Evidence ledger.** Append-only, hash-chained, Ed25519-signed. Every
  verifier verdict is sealed into the chain. `GET /ledger` exposes the
  chain plus a `chain_ok` boolean.
- **HITL queue.** Verdicts of kind `review` are routed to `/hitl`. A
  human approver issues a signed token; the gate caches it locally.
- **NL -> IR authoring.** `/policies/compile` accepts natural language
  and emits a draft IR, a critic LLM review, and a schema_issues list.
  The runtime gate never calls an LLM. The authoring path is the only
  place an LLM appears.

## Floor

`managed-settings.json` plus the Claude Code plugin enforce the hook so
the agent itself cannot disable the gate mid-session. License expiry is
fail-closed at the floor level too: if the WAL has no fresh token and
the cloud is unreachable, the gate denies.

## Flow of one gated call

1. Claude Code is about to run a sentinel-matching Bash command, e.g.
   `FILE_COURT_M1_payload_hash`.
2. Claude Code calls the registered `PreToolUse` hook (`magi-gate.sh`).
3. The hook reads the command on stdin and parses out `(subject, payload_hash)`.
4. The hook consults `~/.magi-cp/local/wal.jsonl` for a token bound to
   that pair.
5. Token present, signature verified against the pinned `kid`, not
   expired -> exit `0` -> Claude Code runs the command.
6. Token missing, stale, or signature invalid -> JSON deny on stdout ->
   Claude Code refuses to run the command.
7. The verdict (allow or deny) is appended to the hash-chained ledger.

## Verifier registry

The registry is a thin lookup keyed by `step` name. Each verifier
implements a `run(payload) -> Verdict` method and is registered at
boot. The 5 wired verifiers are in [Verifiers](./verifiers.md). Add a
custom verifier by registering it into the registry before
`_build_production_app` runs.

## Packs, policies, and rules

Above the IR sits a three-level authoring model:

- **Rule** is the minimal unit: one IR row in the policy store (an
  `EvidencePolicy`, `PermissionPolicy`, `RunCommandPolicy`, and so on).
  A rule is what compiles to a `managed-settings.json` hook and what
  precedence resolves over. See [Policy IR](./policy-ir.md).
- **Policy** is one authored intent that owns one or more rules. "Require
  a verified source before trading" is a single policy owning an audit
  rule plus a precondition rule. A rule authored directly is a policy
  owning exactly one rule. A policy does not compile to anything on its
  own; it expands to its rules.
- **Pack** is a named collection that references policies. At compile
  time each pack expands its policies to their rules. The floor pack is
  always on; other packs are activated per session.

### Session-scoped activation

Packs are activated for a Claude Code session, not globally. Inside a
session, `/magi:pack-activate <pack_id>` turns a pack on until the
session ends (or `/magi:pack-deactivate`). The gate can also
auto-activate packs at `SessionStart` from the comma-separated
`MAGI_CP_AUTO_ACTIVATE_PACKS` env var. The pack-centric runtime is
default-on (`MAGI_CP_PACK_CENTRIC_RUNTIME=1`).

## Runtimes

The gate enforces one Policy IR across four host runtimes: Claude Code
(the primary hook-based surface), Codex (a native permission-lowering
adapter), Gajae-Code (`gjc`, a frozen-shim adapter), and Hermes (a
shell-hook adapter). The gate auto-detects which one from the hook
payload, or takes an explicit `MAGI_CP_RUNTIME` override. See
[Runtimes](./runtimes.md).

## Why fail-closed

Cloud unreachable means license expiry equals bundle expiry. Operators
cannot fall back to ungoverned execution the moment the cloud blips.
For unit-test environments, set `MAGI_CP_LOCAL_DIR` to an empty path
before launching Claude Code to suppress the gate.
