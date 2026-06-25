# Run-share: hosted activation checklist

What it takes to make `magi-cp share` produce real public links served by our
infra (api.openmagi.ai + cloud.openmagi.ai). The code is merged; this is the
deploy/ops side. Run by an operator with fly.io + Vercel access.

## What the feature needs at runtime

- **API (api.openmagi.ai, fly.io / `deploy/fly.toml` / `charts/magi-cp`)**
  - The `shared_run` table. `init_schema` is create-all, so it is created
    automatically on cloud boot; no manual migration. (Postgres: confirm the
    deploy DB user can `CREATE TABLE`; it already creates the other tables.)
  - Routes: `POST /v1/runs/share`, `GET /share/run/{token}`, `GET /v1/runs/share`,
    `POST /v1/runs/share/{token_hash}/revoke` (ship with the image).
  - Env:
    - `MAGI_CP_SHARE_BASE_URL=https://cloud.openmagi.ai` (default already this;
      set explicitly so the returned `url` points at the dashboard).
    - `MAGI_CP_SHARE_TTL_SECONDS`; optional link expiry in seconds (unset/`0`
      = no expiry).
  - Tenants need a valid `mcp_…` API key (existing tenant-auth) to call
    `POST /v1/runs/share`.

- **Dashboard (cloud.openmagi.ai, Vercel)**
  - `MAGI_CP_CLOUD_URL=https://api.openmagi.ai` (server-side fetch target; the
    public `/r/[token]` page and the authed `/shared` page both read it via
    `lib/cloud.ts`). This is already set for the existing console; confirm it.
  - `MAGI_CP_API_KEY` (already set) for the authed `/shared` list/revoke.
  - On a marketing-only deploy (`MAGI_CP_MARKETING_ONLY=1`), `/r` is already in
    the `MARKETING_PUBLIC` allowlist (middleware.ts), so public links render.

## Deploy steps

1. Build + push the API image (fly.io) from the merged `main`; `scripts/deploy-alpha.sh`
   or the K8s chart (`charts/magi-cp/`). Confirm `MAGI_CP_SHARE_BASE_URL` is set.
2. Redeploy the Vercel dashboard from `main`. Confirm `MAGI_CP_CLOUD_URL` points
   at api.openmagi.ai.
3. Confirm boot created `shared_run` (logs / `\dt` on the DB).

## Post-deploy smoke (one real link, end to end)

```bash
export MAGI_CP_CLOUD_URL=https://api.openmagi.ai
export MAGI_CP_API_KEY=<a real tenant key>

# pick any local Claude Code session id
sid=$(ls -t ~/.claude/projects/*/*.jsonl | head -1 | xargs -I{} basename {} .jsonl)

magi-cp share "$sid" --dry-run        # 1. view builds + redacts locally (no upload)
magi-cp share "$sid"                  # 2. uploads -> prints https://cloud.openmagi.ai/r/<token>
# 3. open the printed URL in a logged-out browser -> the run renders, noindex
# 4. dashboard -> Shared runs -> the link appears -> Revoke -> the URL now 404s
```

Checklist:
- [ ] `--dry-run` prints a redacted `openmagi.runView.v1` (no secrets/paths)
- [ ] real run returns a `cloud.openmagi.ai/r/<token>` URL
- [ ] the URL renders the run for a logged-out visitor (summary + trace), `noindex`
- [ ] a planted secret in the goal is scrubbed on the page (defense-in-depth: the
      server re-scrubs on ingest even if the client did not)
- [ ] `/shared` lists the link; Revoke makes the public URL 404

## Notes / residuals

- Redaction is best-effort (see `src/magi_cp/share/redaction.py` residuals: bare
  unkeyed high-entropy tokens, slash-values under non-credential keys, IPv6 ULA).
  The CLI prints a "review before sharing publicly" note. Keep links private by
  default; sharing is explicit.
- The dashboard `/shared` page shows metadata + revoke only; it cannot show the
  link URL (only the token hash is stored; the URL is shown once at creation).
- Governance overlay (magi-cp verdicts merged into a run's trust section) is not
  wired yet; the trace + summary render, governance is currently empty.
