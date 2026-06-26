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
implements a `verify(request) -> verdict` method and is registered at
boot. The 5 wired verifiers are in [Verifiers](./verifiers.md). Add a
custom verifier by registering a subclass into the registry before
`_build_production_app` runs.

## Why fail-closed

Cloud unreachable means license expiry equals bundle expiry. Operators
cannot fall back to ungoverned execution the moment the cloud blips.
For unit-test environments, set `MAGI_CP_LOCAL_DIR` to an empty path
before launching Claude Code to suppress the gate.
