# Troubleshooting

Common failure modes when running the cloud, the gate, and the dashboard.
Grouped by error symptom.

## Cloud (`magi-cp cloud`)

### `503 service unavailable: auth not configured`

One of `MAGI_CP_API_KEY` / `MAGI_CP_HITL_API_KEY` / `MAGI_CP_ADMIN_API_KEY`
is unset and a request hit the matching endpoint. v2.0 multi-tenant mode
also accepts DB-issued `mcp_*` keys via `/admin/tenants/{id}/keys`, but the
env-key path is still the single-tenant default.

### `503 LLM providers not configured`

`POST /policies/compile` requires `MAGI_CP_LLM_COMPILER` and
`MAGI_CP_LLM_REVIEWER` env vars pointing at provider factories. See the
[README env vars table](../README.md#environment-variables).

### `cloud-dev` reports `AttributeError: module 'magi_cp.cloud.app' has no attribute 'app'`

You're on a pre-v2 Makefile. Pull and re-`make install`; the cloud-dev
target now points at the `--factory` form.

### `private key … must be mode 0600`

The keypair file has a permissive mode (e.g. 0644 after a backup restore).
Run `chmod 0600 <key>` and retry. The cloud refuses to load a private key
that the filesystem says could be world-readable.

### `KeyStore has no active key`

The on-disk layout is missing `<MAGI_CP_KEY_DIR>/ACTIVE`. Either run
`magi-cp keys rotate` (creates one) or call the cloud once
(`magi-cp cloud`) which calls `ensure_keypair()` at boot.

## Gate (`magi-cp gate`)

### Bash command always blocked even with valid citations

1. Check the sentinel regex actually matches:
   `echo "<your-cmd>" | grep -P "$(jq -r '.policy.sentinel_re' < policy.json)"`
2. Check the WAL has a fresh token for the right `(matter, doc_id)`:
   `ls -la $HOME/.magi-cp/local/tokens/`
3. Token may be expired (TTL is 600s by default). Re-emit:
   `magi-cp emit --matter M1 --doc-id D1 …`

### Bash command runs without consulting the gate

managed-settings.json isn't being read by Claude Code. Check:
- File at `~/Library/Application Support/ClaudeCode/managed-settings.json`
  (or platform equivalent)
- Restart CC after install
- `magi-gate.sh` is on PATH (the hook command in managed-settings is the
  literal path)

## Dashboard (`web/`)

### `cloud unreachable` banner on every page

The Next.js server can't reach the cloud:
- Default cloud URL is `http://127.0.0.1:8787` — override with
  `MAGI_CP_CLOUD_URL`
- Cloud not running on that port? `curl <url>/healthz` from the same host
- 5s default timeout; bump with `MAGI_CP_CLOUD_TIMEOUT_MS` if you're on
  slow infra

### `/policies/compile` UI shows 503

Same root cause as the cloud error — set `MAGI_CP_LLM_COMPILER` /
`MAGI_CP_LLM_REVIEWER` on the cloud env and restart.

## Multi-tenant

### Newly-issued API key returns 401

- Tenant might be suspended (`{"status":"suspended"}` in `/admin/tenants/{id}`)
- Key revoked (`revoked_at` set in the api_keys row)
- Wrong header — must be `X-Api-Key`, not `Authorization`

### `/admin/tenants/*` returns 401 with valid HMAC

The body might have been re-serialized by your HTTP client (e.g. jq
re-encoding strips whitespace). HMAC is over RAW bytes — sign exactly what
you POST. Use `--data-binary @file.json` with `curl`, not `-d`.

## Observability

### `/metrics` returns 404

The `[observability]` extra isn't installed: `pip install -e .[observability]`
adds prometheus-client. The endpoint is attached only when
`prometheus_client` imports cleanly.

### Counters never increment in test runs

`/metrics` and counter wiring are attached only via `_build_production_app`,
not bare `create_app`. Tests intentionally skip this — use the
`_client_production_like` fixture from `tests/test_observability.py` if
you need metric assertions.

## Key rotation

### `cannot revoke active kid X; rotate() to a new key first`

Self-protection: you can't revoke the key the cloud is currently signing
with. Run `magi-cp keys rotate` first (creates a new active), then revoke
the old kid.

### Old tokens fail to verify after rotation

Expected if you ran `magi-cp keys revoke <old>` before all in-flight
tokens expired. The TTL is `TOKEN_TTL_SECONDS = 600` (10 min); rotate then
wait ≥ 600s before revoking.

## Backup / restore

### `scripts/backup.sh` errors `unsupported DSN scheme`

The script supports `sqlite*` and `postgresql*`/`postgres*` DSNs. For
other backends (MySQL, etc.), substitute your own DB dump tool — keep the
keypair dir + policies.json bytes untouched.

### Restored backup: `chain_ok: false` in /ledger

Hash chain failure after restore means the ledger bytes don't match what
the `prev` linkages claim. Possible causes:
- Partial restore (only DB, not keypair dir → token signatures fail
  verification)
- Manual edit of the database after backup
- Backup file truncated
