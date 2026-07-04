# Getting started

Stand up `magi-control-plane` on your own machine, point Claude Code at
it, and confirm a deny verdict in about two minutes.

## Prerequisites

- Docker + Docker Compose v2. The one-line installer brings up the whole
  stack (cloud + dashboard) in containers. The installer prints a
  platform-specific install hint and exits if Docker is missing.
- Claude Code 2.0+ on the same machine (`claude --version`). Older builds
  do not honor the `PreToolUse` hook contract this gate uses.
- `openssl` on PATH (used once to generate local keys).

This is the single-operator, self-host model: you run the control plane,
and the installer generates your keys locally. There is no remote tenant
key to obtain first.

## Install

Run the one-line installer:

```bash
curl -fsSL https://cp.openmagi.ai/install.sh | bash
```

The installer:

1. Checks Docker + Compose v2 and picks free host ports (defaults 3000
   for the dashboard, 8787 for the cloud; auto-bumps on conflict).
2. Writes `~/.magi/control-plane/{docker-compose.yml,.env}` with random
   keys (`MAGI_CP_API_KEY`, `MAGI_CP_ADMIN_API_KEY`,
   `MAGI_CP_HITL_API_KEY`, `MAGI_CP_ADMIN_HMAC_SECRET`).
3. Pulls the public images (`ghcr.io/openmagi/magi-cp` +
   `...-dashboard`) and runs `docker compose up -d`.
4. Waits for `/healthz` on the cloud and `/welcome` on the dashboard.
5. Drops `~/.claude/managed-settings.json` + `~/.local/bin/magi-gate.sh`,
   wires the hook to your local cloud URL, and installs the
   `/magi:pack-*` slash commands under `~/.claude/commands/magi/`.
6. Persists your key + cloud URL to `~/.config/magi-cp/env` (0600) and
   sources it from `~/.zshrc` / `~/.bashrc`.

Restart Claude Code so it re-reads `managed-settings.json`, then open the
dashboard (`http://localhost:3000` by default) in your browser.

Conversational authoring and the LLM critic stay off until you add an
`ANTHROPIC_API_KEY` (and optionally `OPENAI_API_KEY`) to
`~/.magi/control-plane/.env` and re-run `docker compose up -d`. The
deterministic gate, prebuilt policies, and the guided wizard all work
without any provider key.

## First policy

Open the dashboard at `/rules`. It has three tabs: **Policies**, **Packs**,
and **Evidence** (each check plus the ledger records it emits).

- On **Policies**, toggle a prebuilt policy on. Prebuilts are vetted
  policies (each owning one or more IR rules); enabling one materializes
  it as a saved policy and the next compiled `managed-settings.json`
  includes its rules.
- To author your own, open `/policies/new`. The default mode is the
  conversational compiler: describe the intent in natural language and it
  drafts a policy over the turn, compound-aware (one intent can own an
  audit rule plus a precondition rule). A guided wizard and a raw IR
  editor are the other two modes.

Packs group policies by intent (research-mode, coding-safety, and so on).
Activate one for the current session with `/magi:pack-activate <pack_id>`
inside Claude Code; the always-on floor pack fires regardless. See
[Architecture](./architecture.md#packs-policies-and-rules) for the model.

## Verify the gate fires

In a fresh terminal:

```bash
echo FILE_COURT_DEMO_demo_payload | bash ~/.local/bin/magi-gate.sh
```

With a matching policy active you get a deny decision as JSON on stdout
(the gate always exits 0; the decision is in the payload, not the exit
code). Issue a verifier token with `magi-cp emit`, then re-run; the
second call returns allow.

## Next

- [Architecture](./architecture.md): the three-layer model plus packs,
  policies, and rules.
- [Policy IR](./policy-ir.md): the IR schema reference.
- [Verifiers](./verifiers.md): the wired verifiers and how to add one.
- [CLI](./cli.md): every `magi-cp` subcommand.
- [Troubleshooting](./troubleshooting.md): common errors.
