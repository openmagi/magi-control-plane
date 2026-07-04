# CLI

The `magi-cp` CLI is the single binary that runs the cloud, the local
gate, and the ops surface. It ships inside the container images; a source
checkout exposes the same entrypoint after `pip install -e .`.

## Commands at a glance

```text
magi-cp gate                 PreToolUse / SessionStart hook reader (stdin -> JSON)
magi-cp session pack ...      session-scoped pack activation
magi-cp emit                 request a verifier call, cache the token in WAL
magi-cp await-approval       poll HITL, write the issued token to WAL
magi-cp compile <ir> <out>   render Policy IR -> managed-settings.json
magi-cp cloud                run the FastAPI cloud server
magi-cp mcp                  stdio MCP server (Claude Desktop integration)
magi-cp keys                 Ed25519 signing-key lifecycle
magi-cp share <sessionId>    package a Claude Code run as a public share link
magi-cp install              install the runtime adapter surface (Codex / Claude Code)
```

## gate

The hook entrypoint. Claude Code (or Codex) calls this for every matching
hook event. Reads the hook JSON on stdin and writes a decision as JSON on
stdout.

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"FILE_COURT_M1_payload"}}' | magi-cp gate
```

The gate always exits `0`. The decision lives in the JSON on stdout: an
allow, or a `permissionDecision:"deny"` (equivalently a top-level
`decision:"block"`). A cloud-unreachable gate is a fail-closed deny, also
at exit `0`. The runtime auto-detects the host (Claude Code vs Codex);
force it with `MAGI_CP_RUNTIME=cc|codex`.

Reads env: `MAGI_CP_API_KEY`, `MAGI_CP_CLOUD_URL`, `MAGI_CP_LOCAL_DIR`,
`MAGI_CP_AUTO_ACTIVATE_PACKS`. The gate opens no outbound LLM connection;
its only network calls are to the cloud (verify + `GET /pubkey`).

## session

Session-scoped pack activation. Packs group policies by intent; activating
one turns its policies on for the current Claude Code session (the floor
pack is always on). Claude Code users usually reach these through the
`/magi:pack-activate`, `/magi:pack-deactivate`, and `/magi:pack-status`
slash commands, which shell out to this CLI.

```bash
magi-cp session pack activate research-mode
magi-cp session pack deactivate research-mode
magi-cp session pack status
magi-cp session pack sticky research-mode --project   # persist for this project
```

Flags: `--cloud-url`, `--api-key`, `--session-id`, `--tenant-id`. Exit
codes: `0` success; `1` cloud unreachable or HTTP error; `2` missing
session id / missing api key / bad args.

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

## cloud

Run the FastAPI service.

```bash
magi-cp cloud --host 0.0.0.0 --port 8787
```

Reads env: `MAGI_CP_API_KEY`, `MAGI_CP_ADMIN_API_KEY`,
`MAGI_CP_ADMIN_HMAC_SECRET`, `MAGI_CP_HITL_API_KEY`, `MAGI_CP_KEY_DIR`,
`MAGI_CP_LLM_COMPILER`, `MAGI_CP_LLM_REVIEWER`, `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`. See [Operator > Environment](./operator.md#environment).

## mcp

Run the stdio MCP server. Lets Claude Desktop or another MCP client query
the cloud (policies, ledger, HITL) over stdio.

```bash
magi-cp mcp
```

## keys

Lifecycle for the cloud's Ed25519 signer. Keys live under
`MAGI_CP_KEY_DIR` (default `~/.magi-cp/cloud`).

```bash
magi-cp keys list                # list keys with active / verifying status
magi-cp keys rotate              # mint a new active key; keep prior keys
magi-cp keys revoke <old_kid>    # delete a non-active key
```

`rotate` keeps prior keys so in-flight tokens still verify; wait at least
`TOKEN_TTL_SECONDS` (600 s) before revoking the old `kid`. Exit codes:
`0` success; `1` `list` with no keys / no active key; `2` `revoke`
refused (e.g. the active kid) or unknown subcommand.

## share

Package a Claude Code session as a public, redacted share link.

```bash
magi-cp share <sessionId>                    # uploads, prints the URL
magi-cp share <sessionId> --dry-run          # builds + redacts locally
magi-cp share <sessionId> --allow-plain-http # permit a non-HTTPS cloud (loopback only)
```

Reads env: `MAGI_CP_API_KEY` (tenant), `MAGI_CP_CLOUD_URL`. The link
points at `<MAGI_CP_SHARE_BASE_URL>/r/<token>`. Exit codes: `0`
success / dry-run; `1` transcript not found / upload or network failure;
`2` missing api key / non-http(s) or refused plain-http scheme. See
[Share runs](./share-runs.md).

## install

Install the runtime adapter surface (managed-settings + gate shim for
Claude Code, or the Codex profile + `requirements.toml`). The one-line
installer calls this for you; run it directly to (re)wire a specific
runtime.

## Exit codes

The gate is special: it **always exits 0** and signals a deny through the
JSON decision on stdout (see [gate](#gate) above). The other subcommands
use conventional codes:

| Code | Meaning |
|------|---------|
| 0 | Success. |
| 1 | Runtime failure: cloud unreachable, HTTP error, transcript not found, upload/network failure. |
| 2 | Caller error: missing key / session id, bad arguments, refused scheme, or a refused key revoke. |

Codes above 2 are not used. Do not gate automation on a non-zero exit
from `magi-cp gate`; read the JSON decision instead.
