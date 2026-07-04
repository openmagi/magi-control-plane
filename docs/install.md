# Install

Full install guide. For a one-paragraph quickstart, see
[Getting started](./getting-started.md).

`magi-control-plane` is self-host, single-operator software. The
installer stands up the cloud + dashboard in Docker on your machine,
generates your keys locally, and wires Claude Code to the local cloud.
There is no remote tenant key to obtain first.

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Docker + Compose v2 | macOS / Linux / Windows. The installer aborts with a platform-specific hint if `docker` or the compose plugin is missing. |
| `openssl` | Used once to generate random keys for `.env`. |
| Claude Code 2.0+ | `claude --version`. Older builds do not honor the `PreToolUse` hook contract this gate uses. |
| A browser | The dashboard is the primary authoring surface. |

Python is not required on the host: the CLI ships inside the container
images. You only need a local `python3` if you want to run the
`managed-settings.json` rewrite step by hand (the installer uses it when
present and falls back gracefully otherwise).

## One-line installer

```bash
curl -fsSL https://cp.openmagi.ai/install.sh | bash
```

The script:

1. Checks Docker + Docker Compose v2 (prints an install hint and exits if
   either is missing).
2. Picks free host ports (defaults 3000 dashboard, 8787 cloud;
   auto-bumps up to +50 on conflict). Override with `MAGI_CP_DASH_PORT`
   / `MAGI_CP_CLOUD_PORT`.
3. Downloads `docker-compose.yml` into `~/.magi/control-plane/` and
   generates `~/.magi/control-plane/.env` (0600) with random
   `MAGI_CP_API_KEY`, `MAGI_CP_ADMIN_API_KEY`, `MAGI_CP_HITL_API_KEY`,
   and `MAGI_CP_ADMIN_HMAC_SECRET`.
4. Runs `docker compose pull` + `docker compose up -d --force-recreate`
   (pulls `ghcr.io/openmagi/magi-cp` + `ghcr.io/openmagi/magi-cp-dashboard`).
5. Waits for `/healthz` on the cloud and `/welcome` on the dashboard.
6. Downloads `~/.claude/managed-settings.json` +
   `~/.local/bin/magi-gate.sh` from the dashboard, rewrites the hook
   command to the installed gate path and the local cloud URL, and
   installs the `/magi:pack-*` slash commands under
   `~/.claude/commands/magi/`.
7. Persists `MAGI_CP_API_KEY` + `MAGI_CP_CLOUD_URL` to
   `~/.config/magi-cp/env` (0600) and sources it from `~/.zshrc` /
   `~/.bashrc`.

Re-running is idempotent: an existing `.env` keeps its keys, the compose
file and slash commands are preserved once present (delete a file to
refetch it), and assigned ports stay put. Restart Claude Code afterwards
so it re-reads `managed-settings.json`.

## Enable conversational authoring

The deterministic gate, prebuilt policies, and the guided wizard work
with no LLM provider. Conversational compile (`/policies/compile`,
`/policies/compile-interactive`) and the LLM critic stay off until you
add a provider key. Uncomment the provider lines in
`~/.magi/control-plane/.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-...      # activates the anthropic compiler
OPENAI_API_KEY=sk-...             # activates the openai reviewer (cross-model review)
```

then re-run the containers:

```bash
cd ~/.magi/control-plane && docker compose up -d --force-recreate
```

## Manage the stack

```bash
cd ~/.magi/control-plane
docker compose logs -f          # tail cloud + dashboard
docker compose pull && docker compose up -d   # upgrade to latest images
docker compose down             # stop
```

## Environment variables (local gate)

These are the vars the on-laptop gate and CLI read. Server-side vars live
in `~/.magi/control-plane/.env`; see [Operator](./operator.md#environment).

| Variable | Default | Purpose |
|----------|---------|---------|
| `MAGI_CP_API_KEY` | (from installer) | Tenant key. Sent as `X-Api-Key` on every cloud request. |
| `MAGI_CP_CLOUD_URL` | `http://127.0.0.1:8787` | Cloud base URL the local gate calls. The installer sets this to your local cloud port. |
| `MAGI_CP_LOCAL_DIR` | `~/.magi-cp/local` | WAL and pubkey cache. |
| `MAGI_CP_ALLOW_PLAIN_HTTP` | `0` | Allow a non-HTTPS cloud URL (loopback is always allowed). |
| `MAGI_CP_AUTO_ACTIVATE_PACKS` | (unset) | Comma-separated pack ids auto-activated on `SessionStart`. |

## Manual install (no installer script)

If you would rather wire it up yourself, run the stack from a repo clone
(see [Operator > Deploy](./operator.md#deploy)) and then point Claude
Code at it:

```bash
mkdir -p ~/.claude
curl -fsSL http://localhost:3000/api/downloads/managed-settings \
  -o ~/.claude/managed-settings.json

mkdir -p ~/.local/bin
curl -fsSL http://localhost:3000/api/downloads/gate-binary \
  -o ~/.local/bin/magi-gate.sh
chmod +x ~/.local/bin/magi-gate.sh

mkdir -p ~/.config/magi-cp
cat > ~/.config/magi-cp/env <<EOF
MAGI_CP_API_KEY=<the MAGI_CP_API_KEY from your .env>
MAGI_CP_CLOUD_URL=http://localhost:8787
EOF
chmod 600 ~/.config/magi-cp/env

echo '[ -f "$HOME/.config/magi-cp/env" ] && set -a && . "$HOME/.config/magi-cp/env" && set +a' \
  >> ~/.zshrc
```

Edit `~/.claude/managed-settings.json` so the hook `command` points at
`~/.local/bin/magi-gate.sh` and its `env.MAGI_CP_CLOUD_URL` matches your
cloud port, then restart Claude Code.

## Common failures

| Symptom | Fix |
|---------|-----|
| `Docker not found` | Install Docker Desktop (macOS/Windows) or `curl -fsSL https://get.docker.com \| sh` (Linux), then re-run. |
| `Docker Compose v2 not found` | Update Docker Desktop, or `apt-get install docker-compose-plugin` on Linux. |
| `docker compose pull failed` | Read `/tmp/magi-pull.log`. Behind a proxy, set `HTTPS_PROXY`. Confirm GHCR is reachable. |
| Cloud never becomes healthy | `cd ~/.magi/control-plane && docker compose logs cloud`. |
| Dashboard never becomes ready | `cd ~/.magi/control-plane && docker compose logs dashboard`. |
| `magi-gate.sh not on PATH` | Add `~/.local/bin` to PATH in `~/.zshrc` or `~/.bashrc`. |
| Claude Code never invokes the gate | Restart Claude Code, then confirm the hook command in `~/.claude/managed-settings.json` points at the installed gate path. |

More entries live in [Troubleshooting](./troubleshooting.md).
