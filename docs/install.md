# Install

Full install guide. For a one-paragraph quickstart, see
[Getting started](./getting-started.md).

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Docker (Compose v2) | macOS / Linux / Windows. The installer aborts if `docker` is not on PATH. |
| Python 3.11+ | Required by the `magi-cp` CLI. macOS: `brew install python@3.12`. Debian: `apt install python3.12`. |
| Claude Code 2.0+ | `claude --version`. Older builds do not honor the `PreToolUse` hook contract this gate uses. |
| A deploy hostname | The instance running the cloud (FastAPI). Self-hosters point at their own `https://api.example.com`. |
| A tenant key | An `mcp_…` API key issued by the cloud. Self-host: `magi-cp keys provision`. |

## One-line installer

```bash
curl -fsSL https://<your-instance>/install.sh | bash -s -- mcp_YOUR_KEY
```

The script:

1. Confirms `python3.11+` is on PATH (prints an install hint otherwise).
2. Installs the `magi-cp` package (`pip install --user magi-cp`).
3. Downloads `~/.claude/managed-settings.json` and
   `~/.local/bin/magi-gate.sh`.
4. Persists `MAGI_CP_API_KEY` + `MAGI_CP_CLOUD_URL` to
   `~/.config/magi-cp/env` (0600) and wires `~/.zshrc` / `~/.bashrc`.
5. Runs the smoke test, which proves the gate correctly DENIES a synthetic
   sentinel command when no verifier token is present in the WAL.

Restart Claude Code afterwards so it re-reads `managed-settings.json`.

## Verify the install

The smoke test is rerunnable any time:

```bash
bash <(curl -fsSL https://<your-instance>/install/smoke-test.sh)
```

A healthy install ends with `deny` on the sentinel command. That confirms
the local gate, the cloud verifier registry, and the WAL ledger are all
wired correctly.

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `MAGI_CP_API_KEY` | (required) | Tenant key. Sent as `X-Api-Key` on every cloud request. |
| `MAGI_CP_CLOUD_URL` | `http://127.0.0.1:8787` | Cloud base URL the local gate calls. |
| `MAGI_CP_LOCAL_DIR` | `~/.magi-cp/local` | WAL and pubkey cache. |
| `MAGI_CP_CLOUD_TIMEOUT_MS` | `5000` | Request timeout. Bump on slow infra. |
| `MAGI_CP_SHARE_BASE_URL` | `https://cloud.openmagi.ai` | Public base URL stamped onto `magi-cp share` links. |

See [Operator](./operator.md) for the server-side env vars
(`MAGI_CP_ADMIN_API_KEY`, `MAGI_CP_ADMIN_HMAC_SECRET`, signer key paths).

## Manual install (no installer script)

```bash
pip install --user magi-cp

mkdir -p ~/.claude
curl -fsSL https://<your-instance>/managed-settings.json \
  -o ~/.claude/managed-settings.json

mkdir -p ~/.local/bin
curl -fsSL https://<your-instance>/magi-gate.sh \
  -o ~/.local/bin/magi-gate.sh
chmod +x ~/.local/bin/magi-gate.sh

mkdir -p ~/.config/magi-cp
cat > ~/.config/magi-cp/env <<EOF
MAGI_CP_API_KEY=mcp_YOUR_KEY
MAGI_CP_CLOUD_URL=https://<your-instance>
EOF
chmod 600 ~/.config/magi-cp/env

echo '[ -f "$HOME/.config/magi-cp/env" ] && set -a && . "$HOME/.config/magi-cp/env" && set +a' \
  >> ~/.zshrc
```

Restart Claude Code.

## Common failures

| Symptom | Fix |
|---------|-----|
| `python3.11+ not found` | `brew install python@3.12` (macOS) or `apt install python3.12` (Debian). |
| `pip install failed` | Read `/tmp/magi-cp-install.log`. Behind a proxy, set `HTTPS_PROXY`. |
| Smoke test reports `cloud unreachable` | `curl https://<your-instance>/healthz`. If blocked, ask IT to allow the host. |
| Smoke test reports `key rejected` | Confirm the key matches the email exactly. Expired keys need a fresh provision. |
| `magi-cp-gate not on PATH` | Add `~/.local/bin` to PATH in `~/.zshrc` or `~/.bashrc`. |
| Claude Code never invokes the gate | Restart Claude Code, then confirm the hook command in `~/.claude/managed-settings.json` points at the installed gate path. |

More entries live in [Troubleshooting](./troubleshooting.md).
