# Getting started

Stand up `magi-control-plane`, point Claude Code at it, and confirm a deny
verdict in about two minutes.

## Prerequisites

- Docker (Compose v2). The one-line installer ships a docker-compose stack.
- Claude Code 2.0+ on the same machine (`claude --version`).
- Two terminals: one for the stack, one for verification.

If you do not have Docker yet, install Docker Desktop (macOS, Windows) or
the docker-ce package (Linux). The installer aborts early with a hint if
docker is missing.

## Install

Run the one-line installer against your deploy hostname and tenant key:

```bash
curl -fsSL https://<your-instance>/install.sh | bash -s -- mcp_YOUR_KEY
```

The installer:

1. Installs the Python `magi-cp` package.
2. Drops `~/.claude/managed-settings.json` and `~/.local/bin/magi-gate.sh`.
3. Writes `~/.config/magi-cp/env` (0600) with `MAGI_CP_API_KEY` and
   `MAGI_CP_CLOUD_URL`.
4. Adds source lines to `~/.zshrc` / `~/.bashrc`.
5. Runs a smoke test against a sentinel command. A clean install ends in
   `deny` because no verifier has issued a token yet. That is the success
   signal.

Restart Claude Code so it re-reads `managed-settings.json`.

## First policy

Open the dashboard at `https://<your-instance>/rules` and toggle a
prebuilt rule on. Prebuilts are vetted Policy IR rows; toggling one
publishes it to the cloud and the next compiled `managed-settings.json`
includes it.

If you prefer to author from scratch, go to `/policies/new`, describe
the rule in natural language, and edit the IR draft before saving.

## Verify the gate fires

In a fresh terminal:

```bash
echo FILE_COURT_DEMO_demo_payload | bash ~/.local/bin/magi-gate.sh
```

You should see a deny verdict on stdout. Issue a verifier token with
`magi-cp emit`, then re-run; the second call returns allow.

## Next

- [Architecture](./architecture.md): the three-layer model.
- [Policy IR](./policy-ir.md): the IR schema reference.
- [Verifiers](./verifiers.md): the wired verifiers and how to add one.
- [Troubleshooting](./troubleshooting.md): common errors.
