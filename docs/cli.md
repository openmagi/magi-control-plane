# CLI

The `magi-cp` CLI is the single binary that runs the cloud, the local
gate, and the ops surface. Installed via `pip install magi-cp`; a source
checkout uses the same entrypoint after `pip install -e .`.

## Commands at a glance

```text
magi-cp cloud                run the FastAPI cloud server
magi-cp gate                 PreToolUse hook reader (stdin -> exit + JSON)
magi-cp emit                 request a verifier call, cache the token in WAL
magi-cp await-approval       poll HITL, write the issued token to WAL
magi-cp compile <ir> <out>   render Policy IR -> managed-settings.json
magi-cp keys                 Ed25519 signing-key lifecycle
magi-cp mcp                  stdio MCP server (Claude Desktop integration)
magi-cp share <sessionId>    package a Claude Code run as a public share link
```

## cloud

Run the FastAPI service.

```bash
magi-cp cloud --host 0.0.0.0 --port 8787
```

Reads env: `MAGI_CP_API_KEY`, `MAGI_CP_ADMIN_API_KEY`,
`MAGI_CP_ADMIN_HMAC_SECRET`, `MAGI_CP_HITL_API_KEY`, `MAGI_CP_KEY_DIR`,
`MAGI_CP_LLM_COMPILER`, `MAGI_CP_LLM_REVIEWER`,
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`. See [Operator > Environment](./operator.md#environment).

## gate

The hook entrypoint. Claude Code calls this for every `PreToolUse`
matching the configured matcher. Reads the command on stdin and writes
either `exit 0` (allow) or a JSON deny on stdout.

```bash
echo "FILE_COURT_M1_payload" | magi-cp gate
```

Reads env: `MAGI_CP_API_KEY`, `MAGI_CP_CLOUD_URL`, `MAGI_CP_LOCAL_DIR`,
`MAGI_CP_CLOUD_TIMEOUT_MS`.

The gate never opens an outbound LLM connection. The only network call
is `GET /pubkey?kid=...` when the pinned key is not yet cached locally.

## emit

Request a verifier call and write the issued token into the WAL.

```bash
magi-cp emit \
  --subject M1 \
  --payload-hash D1 \
  --citations '[{"id":"src_1","authority":"..."}]' \
  --corpus-override '{...}'
```

Returns the verdict and writes the signed token to
`~/.magi-cp/local/wal.jsonl` on `pass`.

## await-approval

Poll the HITL queue for an approval bound to your subject and payload
hash. Writes the issued token into the WAL when the queue resolves.

```bash
magi-cp await-approval --subject M1 --payload-hash D1 --timeout 600
```

## compile

Render IR to `managed-settings.json`. Used by `make build-plugin`.

```bash
magi-cp compile policies/legal-filing.json plugin/managed-settings.json
```

## keys

Lifecycle for the cloud's Ed25519 signer.

```bash
magi-cp keys list
magi-cp keys rotate-active --reason "scheduled-quarterly"
magi-cp keys retire <kid> --after "2026-10-01T00:00Z"
magi-cp keys provision \
  --tenant-id <uuid> \
  --plan default \
  --email <subscriber_email>
```

`provision` is the operator-side tenant onboarding command. See
[Operator > Tenant provisioning](./operator.md#tenant-provisioning).

## mcp

Run the stdio MCP server. Lets Claude Desktop or another MCP client
query the cloud (policies, ledger, HITL) over stdio.

```bash
magi-cp mcp
```

## share

Package a Claude Code session as a public, redacted share link.

```bash
magi-cp share <sessionId>                 # uploads, prints the URL
magi-cp share <sessionId> --dry-run       # builds + redacts locally
```

Reads env: `MAGI_CP_API_KEY` (tenant), `MAGI_CP_CLOUD_URL`. The link
points at `<MAGI_CP_SHARE_BASE_URL>/r/<token>`. See [Share runs](./share-runs.md).

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Allow (gate) / success (other commands). |
| 1 | Generic failure. |
| 2 | Cloud unreachable. |
| 3 | Auth rejected. |
| 4 | Local invariant violation (corrupt WAL, missing pubkey cache). |

The gate intentionally has only two outcomes: `0` (Claude Code runs the
command) or non-zero plus a JSON deny on stdout (Claude Code refuses).
Any non-zero exit denies; the JSON body carries the operator-facing
reason string.
