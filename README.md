# magi-control-plane

> Open-source governance gate for Claude Code (Codex / OpenCode next).
> Apache 2.0. Beachhead: Korean legal filing (citation existence +
> verbatim verification + partner sign-off).

## Two ways to use it

| Path | Cost | What you operate | When to pick this |
|------|------|------------------|-------------------|
| **Self-host** (this repo) | Free forever | Your K8s / fly.io / docker | You want full control, on-prem, or air-gapped audit chain |
| **Clawy Pro+ (hosted)** | Included in your Pro+ subscription | Nothing — we run it | You already pay for Clawy Pro+ and want governance without ops |

Same code on both paths — the hosted version is just this repo running
on Kevin's infra with Stripe-driven tenant provisioning. The dashboard
at `cloud.openmagi.ai` is the hosted instance; self-hosters point their
own DNS at their own deploy.

Three layers:
- **Local** (this repo's `magi-cp` CLI + Claude Code hook): client side, verify-only, no signing keys
- **Cloud** (`src/magi_cp/cloud/`): policy authority, Ed25519 signer, tamper-evident ledger, HITL queue, dashboard, NL→IR authoring
- **Floor**: managed-settings + plugin = users can't disable mid-session. Subscription expiry (hosted) or license expiry (self-host) = fail-closed by design.

## Quick start (dev)
```bash
make install          # editable install + dev deps
make test             # pytest (325 Python tests)
make cloud-dev        # start cloud API on :8787  (factory → registry-wired + builtins)
make build-plugin     # compile policies/*.json → plugin/managed-settings.json
```

The cloud is reachable at `http://127.0.0.1:8787`. `GET /healthz` is public; everything else
needs a key (see § Environment variables).

## v1.1 highlights
- **Verifier registry + 5 wired verifiers** — `citation_verify`, `privilege_scan`,
  `source_allowlist`, `structured_output`, `prompt_injection_screen`. Each implements
  the `Verifier` protocol (`name`, `step`, `category`, `enforcement`, `description`,
  `input_schema`, `run`) and dispatches via `Verdict { status: pass|review|deny, reasons }`.
- **MCP auto-exposure** — every registered verifier surfaces as an MCP tool;
  `magi-cp mcp` serves the same 5 + legacy `verify_citations` / `lbox_fetch`.
- **/presets catalog** — `GET /presets` (no auth) merges live registry ("enforcing")
  with a 38-entry vendor catalog from magi-agent ("preview"). Honest labels: 5 wired,
  33 surfaced for parity with magi-agent's customize tab but NOT enforced here.
- **NL→IR compiler** — `POST /policies/compile` (admin key, requires LLM provider env)
  runs a 3-gate authoring flow: LLM compile → critic LLM review → human approve via
  `PUT /policies/{id}`. Compile **never auto-persists**. Substrate defences: nonce-guarded
  `<UNTRUSTED>` fence, evidence-friction precheck, server-side Policy schema validation
  on the compiled IR.

## Environment variables
| Var | Required by | Purpose |
|---|---|---|
| `MAGI_CP_API_KEY` | cloud (citation_verify, ledger) | tenant data-plane key |
| `MAGI_CP_HITL_API_KEY` | cloud (HITL queue) | reviewer key |
| `MAGI_CP_ADMIN_API_KEY` | cloud (policies CRUD + compile) | admin key |
| `MAGI_CP_KEY_DIR` | cloud | Ed25519 keypair dir (default `~/.magi-cp/cloud`) |
| `MAGI_CP_DSN` | cloud | SQLAlchemy DSN (default `sqlite:///./magi-cp.sqlite`) |
| `MAGI_CP_POLICY_STORE` | cloud | path to policies.json (default `~/.magi-cp/policies.json`) |
| `MAGI_CP_LLM_COMPILER` | cloud `/policies/compile` | `mod.path:factory` returning an `LlmProvider` |
| `MAGI_CP_LLM_REVIEWER` | cloud `/policies/compile` | distinct provider for the critic gate |
| `MAGI_CP_CLOUD_URL` | local CLI + dashboard | default `http://127.0.0.1:8787` |
| `MAGI_CP_LOCAL_DIR` | local gate/emit | WAL + tokens dir (default `~/.magi-cp/local`) |

Without `MAGI_CP_LLM_COMPILER` + `MAGI_CP_LLM_REVIEWER`, `/policies/compile` returns
**503 LLM providers not configured**. v1.2 ships two reference providers (no SDK dep):

```bash
# Anthropic (default model: claude-sonnet-4-6)
export ANTHROPIC_API_KEY=sk-ant-…
export MAGI_CP_LLM_COMPILER=magi_cp.llm.anthropic_provider:anthropic_default
# Use OpenAI as the reviewer for diversity (recommended over same-model self-review)
export OPENAI_API_KEY=sk-…
export MAGI_CP_LLM_REVIEWER=magi_cp.llm.openai_provider:openai_default
```

Override the model with `ANTHROPIC_MODEL=…` or `OPENAI_MODEL=…`.

## Curl recipes
**See the catalog (5 wired + 38 preview):**
```bash
curl -s http://127.0.0.1:8787/presets | jq '.presets | length, (map(select(.enforcement == "enforcing")) | length)'
```

**Author a policy directly (no LLM):**
```bash
curl -s -X PUT http://127.0.0.1:8787/policies/legal-filing/v1 \
  -H "X-Admin-Api-Key: $MAGI_CP_ADMIN_API_KEY" -H 'Content-Type: application/json' \
  -d '{
    "policy": {
      "id": "legal-filing/v1", "version": "0.1",
      "description": "Korean legal filing",
      "trigger": {"host": "claude-code", "event": "PreToolUse", "matcher": "Bash"},
      "sentinel_re": "FILE_COURT_(?P<matter>[A-Za-z0-9]+)_(?P<doc_id>[A-Za-z0-9]+)",
      "requires": [{"step": "citation_verify", "verdict": "pass"}],
      "on_missing": "deny", "on_signature_invalid": "deny"
    },
    "source": "platform"
  }'
```

**Pull the compiled managed-settings JSON (what Claude Code consumes):**
```bash
curl -s http://127.0.0.1:8787/policies/legal-filing/v1/compiled \
  -H "X-Admin-Api-Key: $MAGI_CP_ADMIN_API_KEY" | jq .managed_settings
```

**NL→IR compile (requires LLM providers wired):**
```bash
curl -s -X POST http://127.0.0.1:8787/policies/compile \
  -H "X-Admin-Api-Key: $MAGI_CP_ADMIN_API_KEY" -H 'Content-Type: application/json' \
  -d '{"nl": "법원 filing 시 인용을 결정론으로 검증하고 미통과는 차단"}' \
  | jq '{ir, review, schema_issues}'
```

**Run a wired verifier directly (any step except citation_verify, which has its own route):**
```bash
curl -s -X POST http://127.0.0.1:8787/verify/privilege_scan \
  -H "X-Api-Key: $MAGI_CP_API_KEY" -H 'Content-Type: application/json' \
  -d '{"payload": {"text": "[CONFIDENTIAL DRAFT] do not file yet"}, "matter": "M1", "doc_id": "D1"}' \
  | jq '{verdict, reasons, token}'
```

Verdicts: `pass` → token issued; `deny` → no token (ledger records); `review` → token with review flag for HITL routing.

## Layout
- `src/magi_cp/verifier/`  — Verifier protocol + registry, 5 wired verifiers
- `src/magi_cp/policy/`    — Policy IR + deterministic compiler (LLM-free)
- `src/magi_cp/llm/`       — LlmProvider Protocol + FakeLlmProvider (for tests)
- `src/magi_cp/evidence/`  — Ed25519 sign/verify, hash-chain ledger, local WAL
- `src/magi_cp/cloud/`     — FastAPI: `/citation_verify` `/hitl` `/pubkey` `/ledger` `/policies` `/policies/compile` `/presets`
- `src/magi_cp/local/`     — CC hook gate + emit (CLI entry points)
- `src/magi_cp/mcp/`       — stdio MCP server: registry-aware, auto-exposes wired verifiers
- `plugin/`                — `.claude-plugin` bundle (managed-settings is build target)
- `web/`                   — Next.js dashboard: HITL queue + audit + policies + presets
- `tests/`                 — pytest

## CLI surface (after `pip install -e .`)
- `magi-cp gate` — PreToolUse hook reader (stdin JSON in, exit + JSON out)
- `magi-cp emit --matter --doc-id …` — request citation_verify, cache in WAL
- `magi-cp await-approval --hitl-id N` — poll until HITL decides, write token to WAL
- `magi-cp compile <policy.json> <out.json>` — Policy IR → managed-settings
- `magi-cp cloud` — run FastAPI cloud server (registry-wired)
- `magi-cp mcp` — stdio MCP server (registry-wired)
- `magi-cp keys rotate|list|revoke` — Ed25519 signing key lifecycle (W7b)

## Key rotation (W7b)
KeyStore manages N keypairs under `MAGI_CP_KEY_DIR` (default
`~/.magi-cp/cloud`); the file `ACTIVE` names the current signing kid.
`/pubkey` returns a `{kid: pem}` map so clients can verify tokens signed by
any current key, not just the active one.

Recommended cron (daily):
```bash
magi-cp keys rotate          # mint new active kid; old keys keep verifying
# wait at least TOKEN_TTL_SECONDS (600s) for in-flight tokens to expire
magi-cp keys list            # find non-active kids
magi-cp keys revoke <old>    # delete the old keypair
```

Legacy single-keypair deploys (one `ed25519_*.pem` pair in `MAGI_CP_KEY_DIR`)
are auto-migrated to the multi-key layout on first cloud boot — no manual
step required.

## Production deploy

**Single-node Docker**: `docker compose up -d` runs the cloud on SQLite + a
PVC volume. Sufficient for the alpha pilot.

**Multi-node Kubernetes**: `helm install magi-cp charts/magi-cp -f my-values.yaml`.
Required values for HA:
- `replicaCount > 1`
- `postgres.dsn` set to your Postgres cluster (see `pyproject.toml`'s
  `[postgres]` extra — `pip install -e .[postgres]` adds the driver)
- `secretRef.name` pointing at a Secret with all `MAGI_CP_*` keys
- `llm.compiler` / `llm.reviewer` set to provider factory paths
- `serviceMonitor.enabled: true` if running kube-prometheus

**Backup** (`scripts/backup.sh <out-dir>`) tar-balls the keypair dir + policy
store + database snapshot (SQLite `.backup` or `pg_dump`). Optionally pipes
through `age` when `MAGI_CP_BACKUP_RECIPIENT` is set.

**Observability**:
- `pip install -e .[observability]` enables structlog (JSON to stderr) and
  exposes `/metrics` (Prometheus exposition). Both are no-ops without the
  extra.
- Counters: `magi_cp_verify_total{step,verdict,tenant_id}`,
  `magi_cp_compile_total{review_ok}`, `magi_cp_ledger_append_total{tenant_id,verdict}`,
  `magi_cp_hitl_enqueue_total{tenant_id}`
- Histograms: `magi_cp_verify_latency_seconds{step}`,
  `magi_cp_compile_latency_seconds`

## Pre-flight LIVE smoke (run once before your first demo)
```bash
# 1. Set real API keys
export ANTHROPIC_API_KEY=sk-ant-…
export OPENAI_API_KEY=sk-…

# 2. Hit both providers + run end-to-end NL→IR compile
python -m scripts.smoke_live_llm

# 3. Start the cloud (separate terminal)
make cloud-dev

# 4. Test the bash-gate pipeline against a fake corpus
export MAGI_CP_API_KEY=$(uuidgen)
magi-cp emit --matter M1 --doc-id D1 \
  --quote "test quote text" --ref "test ref" \
  --corpus-override '{"X":"test quote text body"}'
# Then trigger a PreToolUse hook with FILE_COURT_M1_D1 in the command —
# the gate reads the WAL token and ALLOWs. Without it: DENY.
```

Confirms: real LLM round-trip works, models exist, JSON parses, gate↔cloud↔WAL token flow live.

## Dashboard (v2.1 polish)

The dashboard at `web/` was refactored with the Magi design system in
`design-system/magi-control-plane/`:

- **Tokens-only theming** — colors, typography, spacing, motion are
  CSS variables (Tailwind v4 reads them via `@theme`), so the whole
  surface re-themes from one file (`web/app/globals.css`).
- **Vendored primitives** — `Button`, `Badge`, `Card`, `Input`,
  `Textarea`, `Select`, `Code`, `CodeBlock`, `KPI`, `EmptyState`,
  `ErrorState`, `Skeleton`, `CopyButton`, `PageHeader`, `NavBar`,
  `LangSelect`, `SubmitButton` under `web/components/ui/`.
- **i18n** — Korean default + English switcher (cookie + server
  action). Dictionary at `web/lib/i18n/dict.ts`; pages call `getT()`
  for translations and `getIntl()` for number/date formatters.
- **Accessibility** — skip link, focus-visible rings, aria-current on
  nav, mobile drawer (`<768px`), Pretendard Variable + Inter pairing,
  `prefers-reduced-motion` collapses animations.
- **Korea-first formatting** — `Intl.NumberFormat("ko-KR")` and
  `Intl.DateTimeFormat("ko-KR", { dateStyle, timeStyle, timeZone:
  "UTC" })` everywhere.

Run the dashboard:

```bash
cd web && npm install && npm run dev  # http://127.0.0.1:3787
```

## Self-host deploy

The app has two surfaces; production puts them on two hostnames.

| Component | Runtime | Why |
|-----------|---------|-----|
| Next.js dashboard (`web/`) | Vercel-style serverless OR your Node hosting | Server actions, edge i18n |
| Python FastAPI cloud (`src/magi_cp/cloud/`) | Kubernetes (`charts/magi-cp/`) or fly.io | Long-running, Ed25519 keypair on PVC, 5–20s LLM compile |

The dashboard reads `MAGI_CP_PUBLIC_CLOUD_URL=https://your-api-host`
from env; the gate's runtime URL ends up pointing there via the
`/api/install-config` endpoint that `install.sh` consults.

### Primary deploy — K8s (`charts/magi-cp/`)

```bash
helm install magi-cp ./charts/magi-cp \
  -f charts/magi-cp/examples/production-values.yaml \
  --namespace magi-cp --create-namespace
```

cert-manager `ClusterIssuer`s in `charts/magi-cp/examples/cert-manager-issuer.yaml`.
Multi-replica needs Postgres (set `postgres.dsn`). NGINX hardening +
security headers default-ON via `ingress.hardening.enabled: true`.
Point `api.openmagi.ai` at the Ingress LoadBalancer IP.

### Dashboard — Vercel

```bash
cd web && vercel --prod
# Vercel env vars (mirror these from the K8s magi-cp-secrets Secret):
#   MAGI_CP_PUBLIC_SITE_URL  = https://cloud.openmagi.ai
#   MAGI_CP_PUBLIC_CLOUD_URL = https://api.openmagi.ai
#   MAGI_CP_API_KEY, MAGI_CP_HITL_API_KEY, MAGI_CP_ADMIN_API_KEY
#   MAGI_CP_ADMIN_HMAC_SECRET
```

Point `cloud.openmagi.ai` CNAME → `cname.vercel-dns.com`.

### Alternative — fly.io single-node

For solo self-hosters without K8s, `deploy/fly.toml` brings up the
FastAPI cloud on fly.io in the Tokyo region (Korea-adjacent). The
dashboard then lives on the same fly.io app or runs locally.

```bash
cd deploy && fly launch --copy-config --no-deploy --name magi-cp
fly secrets set MAGI_CP_API_KEY=… MAGI_CP_ADMIN_API_KEY=… …
fly vol create magi_data --region nrt --size 3
fly deploy && fly cert add api.openmagi.ai
```

## Hosted via Clawy Pro+

If you don't want to run the infra yourself: `magi-control-plane` is
bundled into every **[Clawy Pro+](https://clawy.pro/pricing)** subscription.
When you subscribe, our Stripe webhook calls `POST /admin/tenants` on
the hosted instance (HMAC-signed contract — see `src/magi_cp/cloud/app.py`
`_attach_admin_tenant_routes`), provisions your tenant, and emails you
the `mcp_…` API key. Same code path as `scripts/quickstart.sh` from
there.

No separate sign-up here. The hosted instance at `cloud.openmagi.ai`
exists for Pro+ subscribers; everyone else self-hosts.

## Setup (after you have an `mcp_…` key)

```bash
curl -fsSL https://<your-instance>/install.sh | bash -s -- mcp_YOUR_KEY
```

This:
1. installs the Python `magi-cp` package
2. drops `~/.claude/managed-settings.json` + `~/.local/bin/magi-gate.sh`
3. persists your key + cloud URL to `~/.config/magi-cp/env` (0600)
4. runs the smoke test to confirm the gate fires

See `docs/install.md` for the full guide (KO + EN).

## Status
v2.1 ga-candidate — **437 Python + 72 web = 509 tests**. LLM providers hardened against
live-API failure modes (error-body extraction, max_tokens truncation, 429 retry,
finish_reason=length, response_format=json_object, asyncio.to_thread). Reviewed across
security, integration, "what would break in a demo" + 2026-06 live API spec angles.

What landed in v1.2 on top of v1.1:
- **Real LLM providers** — `magi_cp.llm.anthropic_provider` and `openai_provider`
  (no SDK dep; httpx direct). Wire via env (see above). Mix-and-match: Anthropic
  compiler + OpenAI critic for diversity.
- **Generic verifier dispatch** — `POST /verify/{step}` routes any registered verifier
  through the same token + ledger flow as `/citation_verify` (which keeps its specialized
  NLI path). Operators can now exercise privilege_scan / source_allowlist /
  structured_output / prompt_injection_screen directly from HTTP, not only via MCP.
- **/policies/compile UI** — `/policies/compile` page (textarea + IR/review/schema_issues
  render + "Edit & save" handoff to `/policies/new?draft=…` for prefilled save).

**NLI advisory is intentionally citation-only.** The entailment classifier scores
"does the quote follow from the source text" — a question only `citation_verify` actually
asks. Forcing NLI onto regex verifiers (privilege/injection) or URL/JSON verifiers
(source/structured) would either be a no-op or push false-positives by trying to extract
semantic meaning from a deterministic-by-design check. Not a gap; a deliberate scope.

See `SECURITY.md` for v0 deferments before partner pilot and the threat model that
shapes the cloud's auth + key rotation story.
