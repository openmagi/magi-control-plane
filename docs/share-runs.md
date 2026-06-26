# Share runs

`magi-cp share` packages a Claude Code session as a public, redacted
share link. Use it for support handoffs, design partner reviews, or
"here is what the gate did" demos.

## CLI flow

```bash
# Pick a session id (`~/.claude/projects/*/<id>.jsonl`).
sid=$(ls -t ~/.claude/projects/*/*.jsonl | head -1 | xargs -I{} basename {} .jsonl)

# Dry run: builds + redacts locally, prints the runView, uploads nothing.
magi-cp share "$sid" --dry-run

# Upload. Prints `https://<your-dashboard>/r/<token>`.
magi-cp share "$sid"
```

The link target points at the dashboard. The dashboard fetches the
`runView` from the cloud and renders it. Logged-out visitors can view
the page; bots are blocked via `noindex`.

## The `openmagi.runView.v1` contract

The wire format the cloud stores and the dashboard reads.

```jsonc
{
  "schema": "openmagi.runView.v1",
  "session_id": "<id>",
  "goal": "...",
  "summary": {
    "model": "...",
    "tokens_in": 0,
    "tokens_out": 0,
    "cost_usd": 0.0,
    "started_at": 0,
    "ended_at": 0
  },
  "trace": [
    {"role": "user", "ts": 0, "text": "..."},
    {"role": "assistant", "ts": 0, "text": "..."},
    {"role": "tool", "name": "Bash", "ts": 0, "input": {...}, "output": "..."}
  ],
  "governance": {
    "verdicts": [
      {"step": "citation_verify", "subject": "M1", "payload_hash": "D1", "status": "pass", "kid": "..."}
    ]
  }
}
```

- `schema` is the contract version. Renderers must reject unknown
  versions instead of guessing.
- `summary.cost_usd` is best-effort. Some providers do not report cost
  via the session log; the field is `0` in that case.
- `trace` is a flat array of role-tagged entries. Tool entries carry
  `input` and `output`.
- `governance.verdicts` is the magi-cp slice of the run: which
  verifiers fired, which passed, which were sent to HITL. Empty when
  the session ran without any gated commands.

## Redaction

The CLI applies a local redaction pass before upload. The cloud applies
a server-side re-pass on ingest (defense in depth) so a buggy client
cannot leak secrets even by accident. See `src/magi_cp/share/redaction.py`
for the patterns:

- Provider API keys (`sk-`, `mcp_`, `xoxb-`, ...).
- Tokens with high-entropy character classes.
- File paths under `$HOME` (replaced with `~`).
- Bearer tokens and basic auth in URLs.

Residuals (best-effort):

- Bare unkeyed high-entropy tokens (looks like a token, but no key
  preceded it).
- Slash-delimited values under non-credential-shaped keys.
- IPv6 ULA addresses.

The CLI prints a "review before sharing publicly" note. Treat share
links as private by default; the cloud does not index them.

## Dashboard surface

The dashboard exposes two routes:

- `/r/[token]` - public, no auth. Renders the run view for a logged-out
  visitor. Sends `noindex`. Available on marketing-only deploys
  (`MAGI_CP_MARKETING_ONLY=1`) through the middleware allowlist.
- `/shared` - authed. Lists the tenant's share links with `created_at`,
  `revoked_at`, and a revoke button. The full URL is shown ONCE at
  creation. Only the token hash is stored on the server.

## Lifecycle

| Event | Endpoint | Notes |
|-------|----------|-------|
| Create | `POST /v1/runs/share` | Returns `{token, token_hash, url}`. Save the URL immediately. |
| Read | `GET /share/run/{token}` | Public. Returns the runView until revoked or expired. |
| List | `GET /v1/runs/share` | Tenant-scoped. Returns metadata only (no URLs). |
| Revoke | `POST /v1/runs/share/{token_hash}/revoke` | Public URL 404s after revoke. |

`MAGI_CP_SHARE_TTL_SECONDS` sets a default link expiry (unset or `0`
means no expiry).

## Activation checklist (self-host)

1. The `shared_run` table is created on cloud boot via `init_schema`.
   No manual migration needed. (Postgres: confirm the deploy user has
   `CREATE TABLE` permission.)
2. Set `MAGI_CP_SHARE_BASE_URL=https://<your-dashboard>` so the URL the
   cloud returns points at the dashboard, not the cloud host.
3. The dashboard reads `MAGI_CP_CLOUD_URL` server-side for both the
   public `/r/[token]` page and the authed `/shared` list/revoke.

## Smoke (one real link, end to end)

```bash
export MAGI_CP_CLOUD_URL=https://<your-api>
export MAGI_CP_API_KEY=<a real tenant key>

sid=$(ls -t ~/.claude/projects/*/*.jsonl | head -1 | xargs -I{} basename {} .jsonl)

magi-cp share "$sid" --dry-run        # 1. local build + redact, no upload
magi-cp share "$sid"                  # 2. upload + print URL

# 3. open the printed URL in a logged-out browser; the run renders, noindex
# 4. dashboard -> Shared runs -> the link appears -> Revoke -> the URL 404s
```
