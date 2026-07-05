<div align="center">

# magi-control-plane

**The open-source governance gate that makes your agent's rules executable.**

[Website](https://cp.openmagi.ai) ·
[Docs](https://cp.openmagi.ai/docs) ·
[Quickstart](docs/getting-started.md) ·
[Policy IR](docs/policy-ir.md)

[![CI](https://github.com/openmagi/magi-control-plane/actions/workflows/ci.yml/badge.svg)](https://github.com/openmagi/magi-control-plane/actions/workflows/ci.yml)
![status](https://img.shields.io/badge/status-early%20beta-f97316)
![install](https://img.shields.io/badge/install-Docker-2563eb)
![CLI](https://img.shields.io/badge/CLI-magi--cp-7c3aed)
![runtimes](https://img.shields.io/badge/runtimes-Claude%20Code%20%2B%20Codex-16a34a)
![ledger](https://img.shields.io/badge/ledger-Ed25519%20signed-0891b2)
![license](https://img.shields.io/badge/license-Apache--2.0-111827)

</div>

> **Early beta:** magi-control-plane is under active development. Expect rough edges.

> **Self-host, single operator:** one command brings up the cloud + dashboard in
> Docker on your machine and wires Claude Code to it. Your keys are generated
> locally; there is no remote tenant key to obtain first.

`magi-control-plane` is the runtime that sits between your coding agent and your
machine. It is a policy authority, a verifier registry, an Ed25519-signed
evidence ledger, a human-in-the-loop queue, and a natural-language policy
authoring dashboard. Every tool call, prompt, and session boundary can be gated
against your policies at runtime, and every verdict is sealed into a
tamper-evident chain.

## The Problem

Prompting is not control. An agent can say it read a document it never opened,
run a command you never approved, or ship a plausible answer with no audit
trail. Telling the model "always check first" is a hope, not a guarantee.

magi-control-plane turns those rules into something the runtime enforces. A
policy compiles to a Claude Code `managed-settings.json` hook (or a Codex
permission profile) that fires deterministically, independent of what the model
"decided". No evidence on record means the tool is denied, every time.

## Install

```bash
curl -fsSL https://cp.openmagi.ai/install.sh | bash
```

The installer:

1. checks Docker + Compose v2 and pulls the public GHCR images
2. generates `~/.magi/control-plane/.env` with random keys and runs
   `docker compose up -d`
3. drops `~/.claude/managed-settings.json` + `~/.local/bin/magi-gate.sh` and
   installs the `/magi:pack-*` slash commands
4. persists your key + cloud URL to `~/.config/magi-cp/env` (0600)

Then open the dashboard (`http://localhost:3000` by default) and toggle a
prebuilt policy on. See [Getting started](docs/getting-started.md) for the
two-minute walkthrough and [Install](docs/install.md) for the full guide.

## How it works

Three layers, and the line between them is the security boundary:

| Layer | Job |
| ----- | --- |
| **Local** | The `magi-cp` CLI + Claude Code hook on your machine. Verify-only, no signing keys. Fail-closed when the cloud is unreachable. |
| **Cloud** | The policy authority: verifier registry, Ed25519 signer, tamper-evident ledger, HITL queue, dashboard, and NL to IR authoring. |
| **Floor** | `managed-settings.json` + the plugin, so the agent cannot disable the gate mid-session. |

Policies are authored as a **pack -> policy -> rule** model: a rule is one
compiled unit, a policy is one authored intent that owns one or more rules, and
a pack groups policies you activate per session (the floor pack is always on).
See [Architecture](docs/architecture.md).

## CLI

```text
magi-cp gate                 PreToolUse / SessionStart hook reader (stdin -> JSON)
magi-cp session pack ...     session-scoped pack activation
magi-cp emit                 request a citation_verify token, cache it in the WAL
magi-cp await-approval       poll HITL, write the issued token to the WAL
magi-cp compile <ir> <out>   Policy IR -> managed-settings.json
magi-cp cloud                run the FastAPI cloud server
magi-cp mcp                  stdio MCP server
magi-cp keys                 Ed25519 signing-key lifecycle
magi-cp share <sessionId>    package a Claude Code run as a public share link
magi-cp install              install the runtime adapter (Codex / Claude Code)
```

Full reference: [CLI](docs/cli.md).

## Develop

```bash
make install          # editable install + dev deps
make test             # pytest
make cloud-dev        # cloud API on :8787  (registry-wired + builtins)
make build-plugin     # compile the demo policy -> plugin/managed-settings.json
cd web && npm install && npm run dev   # dashboard on :3000
```

The cloud is reachable at `http://127.0.0.1:8787`. `GET /healthz` and the
`GET /pubkey` endpoint are public; mutating routes need a key.

## Docs

Developer docs live under [`docs/`](docs/) and render on `cp.openmagi.ai/docs`.

| | |
| --- | --- |
| [Getting started](docs/getting-started.md) | Install, point Claude Code at the gate, see a deny. |
| [Your first real policy](docs/tutorial.md) | End-to-end: author, deny, issue evidence, allow, ledger. |
| [Install](docs/install.md) | Full install guide, env vars, common failures. |
| [Architecture](docs/architecture.md) | Three-layer model; packs, policies, rules. |
| [Runtimes](docs/runtimes.md) | Claude Code hooks and the Codex adapter. |
| [Policy IR](docs/policy-ir.md) | The IR schema, archetypes, and precedence. |
| [Verifiers](docs/verifiers.md) | The wired verifiers and registering your own. |
| [Session-evidence gate](docs/session-evidence.md) | Gate a tool on evidence from earlier this session. |
| [Session-evidence threat model](docs/session-evidence-threat-model.md) | What that gate does and does not defend. |
| [Operator](docs/operator.md) | Deploy, rotate keys, observability, backups. |
| [API](docs/api.md) | Cloud REST reference. |
| [CLI](docs/cli.md) | Every `magi-cp` command and exit code. |
| [Troubleshooting](docs/troubleshooting.md) | Common errors and resolutions. |
| [Share runs](docs/share-runs.md) | `magi-cp share` and the run-view contract. |

## License

Apache 2.0. See [LICENSE](LICENSE).
