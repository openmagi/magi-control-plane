# Run-share link (control-plane viral feature) — design + build plan

**Date:** 2026-06-24
**Product:** magi-control-plane. People run **Claude Code** with Magi control plane
attached; `magi share <run>` turns a run into a shareable public link (Vercel-preview
style bottom-up virality + non-developer onboarding surface).

## Resolved decisions (Kevin)

1. **Surface = magi-cp.** CLI uploads a redacted view to `api.openmagi.ai`
   (`src/magi_cp/cloud`), mints a token, public page renders on `cloud.openmagi.ai`
   (`web/`).
2. **Trigger = explicit `magi share <run>`** (opt-in, default private).
3. **Run data source = Claude Code transcript + Magi governance.** The rich summary
   (goal/result/model/tokens/trace) is NOT in magi-cp (which only has per-tool-call
   verifier verdicts). It lives in Claude Code's own session JSONL
   (`~/.claude/projects/<cwd>/<sessionId>.jsonl`). Magi's governance verdicts overlay
   the trust section.
4. **Redaction = vendored copy.** magi-cp does not import magi-agent (vendoring
   convention, e.g. `cloud/presets_catalog.py`). Port `run_redaction.py`
   (`build_public_run_view` + `redact_public_text`, schema `openmagi.runView.v1`) into
   `src/magi_cp/share/` with a provenance comment. Redaction runs CLI-side (the raw
   ~/.claude data is local); the API may re-scrub on ingest as defense-in-depth.

## Claude Code transcript format (verified on a real file)

`~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl`, one JSON event per line:
- `type:"user"` -> `message.content` (str or block list). First real user message = **goal**.
- `type:"assistant"` -> `message.content` (blocks: thinking/text/tool_use), `message.model`
  (e.g. `claude-opus-4-8`), `message.usage.{input_tokens,output_tokens,cache_*}`. Last
  text block = **result**. Sum usage across events.
- tool calls = assistant `tool_use` blocks (name, input); results = `user` `tool_result` blocks.
- `type:"pr-link"` -> `{prNumber, prUrl}` = a deliverable for the results section.
- `type:"ai-title"` -> session title (bonus header). `sessionId` = run identity.
- No cost field; derive from tokens + a price table, or omit cost in v1.

## runView mapping (Claude Code -> openmagi.runView.v1)

| view field | source |
|---|---|
| summary.goal | first `user` message text |
| summary.result | last `assistant` text block |
| summary.model | dominant/last `assistant` message.model |
| summary.usage | sum of assistant `message.usage` input/output tokens |
| summary.status | derived (completed / aborted) |
| summary.title (new opt) | last `ai-title` |
| results (new opt) | `pr-link` events (PR urls) |
| trace[] | assistant `tool_use` blocks in order (name + redacted input summary), paired with the `tool_result` |
| governance[] | magi-cp local ledger verdicts for this session's subject (verdict + reason + kind) |

The `trace`/`governance` arrays reuse the `openmagi.runView.v1` shape so the vendored
`build_public_run_view` allowlist projection + `redact_public_text` apply unchanged.

NOTE for PR-2: the producer adds two fields beyond the magi-agent shape:
`results` (top-level, PR links) and `summary.title`. The vendored
`build_public_run_view` allowlist MUST be extended to pass `results` (scrubbing
urls) and `summary.title` (scrubbed), or they are silently dropped from the
public view.

## Build plan (PRs in magi-control-plane)

- **PR-1 (producer + vendored redaction):** `src/magi_cp/share/`
  - `claude_code_view.py`: read a session JSONL -> `openmagi.runView.v1` dict.
  - `redaction.py`: vendored `build_public_run_view` + `redact_public_text` (provenance
    comment pointing at magi-agent). `build_public_run_view(claude_code_view(...))`.
  - TDD: golden over a small synthetic transcript; linearity + leak tests carried over.
- **PR-2 (CLI):** `src/magi_cp/cli/share.py` + one elif in `cli/__main__.py`. `magi share
  <sessionId|run>`: locate the transcript, build+redact the view, POST to
  `MAGI_CP_CLOUD_URL/v1/runs/share` with `X-Api-Key`, print the public URL. Default-OFF
  behind a flag if needed.
- **PR-3 (API):** `SharedRun` table (token hash + tenant_id + view_json + created_at +
  expires_at, mirror `tenants.py` `_hash_key`); `POST /v1/runs/share`
  (`require_tenant_auth`, validate `schemaVersion==openmagi.runView.v1`, re-scrub,
  store, return token); `GET /share/run/{token}` (no auth, lookup-by-hash + expiry).
  Mind deny-all CORS (`app.py:507`).
- **PR-4 (dashboard):** `web/app/r/[token]/page.tsx` server component, keyless
  `getSharedRun` in `web/lib/cloud.ts`, add `/r` to `MARKETING_PUBLIC` in
  `web/middleware.ts`. Render summary -> results -> trace -> governance. Marketing
  terminal dark/green tokens (`(marketing)/layout.tsx`), `web/components/ui` primitives,
  noindex. "Review before making public" confirmation in the CLI/where the link is shown.

PR-1 is the foundation (everything renders its output). PR-1->PR-2->PR-3->PR-4 sequential
on data dependency, but PR-3 (API/table) and PR-4 (page) can overlap once the view schema
is fixed by PR-1.

## Safety

Redaction is best-effort (vendored `redact_public_text` residuals: bare unkeyed
high-entropy tokens, slash-values under generic keys, IPv6 ULA). Default private; share
is explicit; public page noindex; show "review before making public". The CLI builds from
the user's own ~/.claude data, so the user is the data owner accepting the share.
