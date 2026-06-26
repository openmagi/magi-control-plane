# Troubleshooting

Common failure modes. Grouped by source.

## Install

### `python3.11+ not found`

The installer needs Python 3.11 or newer.

```bash
# macOS
brew install python@3.12

# Debian / Ubuntu
sudo apt install python3.12
```

### `docker: command not found`

The Docker Compose stack needs a working `docker` binary. Install
Docker Desktop on macOS or Windows. On Linux, `apt install docker-ce`
plus add your user to the `docker` group.

### Installer hangs at "downloading managed-settings.json"

Network blocked between your laptop and the cloud. Test with:

```bash
curl -fsSL https://<your-instance>/healthz
```

If the host is unreachable, your network or VPN is blocking it.

## Local gate

### Bash command always blocked, even with valid citations

1. Sentinel regex actually matches the command?

   ```bash
   echo "<your-cmd>" | grep -P "$(jq -r '.policy.sentinel_re' < policy.json)"
   ```

2. WAL has a fresh token for the `(subject, payload_hash)` pair?

   ```bash
   ls -la "$HOME/.magi-cp/local/tokens/"
   ```

3. Token expired? Default TTL is 600 seconds. Re-emit:

   ```bash
   magi-cp emit --subject S1 --payload-hash P1 ...
   ```

### Bash command runs without consulting the gate

`managed-settings.json` is not being read by Claude Code.

- File at `~/.claude/managed-settings.json` (macOS / Linux). On older
  Claude Code builds it lives at
  `~/Library/Application Support/ClaudeCode/managed-settings.json`.
- Restart Claude Code after install.
- `magi-gate.sh` is on PATH and matches the hook command in
  `managed-settings.json`.

### `magi-cp-gate not on PATH`

Add `~/.local/bin` to PATH:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
exec zsh
```

## Cloud

### `503 service unavailable: auth not configured`

One of `MAGI_CP_API_KEY`, `MAGI_CP_HITL_API_KEY`, `MAGI_CP_ADMIN_API_KEY`
is unset and a request hit the matching endpoint. Multi-tenant mode
also accepts DB-issued `mcp_*` keys via
`POST /admin/tenants/{id}/keys`, but the env path is the default for
single-tenant deploys.

### `503 LLM providers not configured`

`POST /policies/compile` needs `MAGI_CP_LLM_COMPILER` and
`MAGI_CP_LLM_REVIEWER` env vars pointing at a `module:factory`. See
[Operator > Environment](./operator.md#environment).

### `private key ... must be mode 0600`

A keypair file has loose permissions (often 0644 after a backup
restore). Fix:

```bash
chmod 0600 <key>
```

The cloud refuses to load a private key that the filesystem says could
be world-readable.

### `KeyStore has no active key`

The on-disk layout is missing `<MAGI_CP_KEY_DIR>/ACTIVE`. Either run
`magi-cp keys rotate-active` (creates one) or boot the cloud once
(`magi-cp cloud`) which calls `ensure_keypair()` at startup.

## Dashboard

### `cloud unreachable` banner on every page

The Next.js server cannot reach the cloud.

- Default cloud URL is `http://127.0.0.1:8787`. Override with
  `MAGI_CP_CLOUD_URL`.
- Cloud not running on that port? `curl <url>/healthz` from the same host.
- 5 s default timeout. Bump with `MAGI_CP_CLOUD_TIMEOUT_MS` on slow infra.

### `/policies/new` UI shows 503

Same root cause as the cloud LLM error. Set `MAGI_CP_LLM_COMPILER` and
`MAGI_CP_LLM_REVIEWER` and restart the cloud.

## Multi-tenant

### Newly-issued API key returns 401

- Tenant suspended (`GET /admin/tenants/{id}` returns `"status":"suspended"`).
- Key revoked (`revoked_at` set in the `api_keys` row).
- Wrong header. Use `X-Api-Key`, not `Authorization`.

### `/admin/tenants/*` returns 401 with a valid HMAC

The body might have been re-serialized by your HTTP client (`jq` for
example strips whitespace on re-encode). HMAC is over RAW bytes. Sign
exactly what you POST. With `curl`, use `--data-binary @file.json`,
never `-d`.

## Observability

### `/metrics` returns 404

The `[observability]` extra is not installed.

```bash
pip install -e .[observability]
```

The endpoint is attached only when `prometheus_client` imports cleanly.

### Counters never increment in test runs

`/metrics` and counter wiring are attached only via
`_build_production_app`, not bare `create_app`. The test suite uses
`create_app` to keep the suite deterministic. Use the
`_client_production_like` fixture from `tests/test_observability.py` if
you need metric assertions.

## Key rotation

### `cannot revoke active kid X; rotate to a new key first`

Self-protection. You cannot revoke the key the cloud is currently
signing with. Run `magi-cp keys rotate-active`, then revoke the old
`kid`.

### Old tokens fail to verify after rotation

Expected if you ran `magi-cp keys retire <old>` before all in-flight
tokens expired. The default TTL is 600 s. Rotate, then wait at least
600 s before retiring.

## Backup and restore

### `chain_ok: false` in /ledger after restore

Hash-chain mismatch. Possible causes:

- Partial restore (DB but not the keypair dir, so signatures fail).
- The DB was manually edited after the backup.
- Backup file truncated.

Restore the matching keypair dir plus DB pair, or roll forward to a
clean backup.
