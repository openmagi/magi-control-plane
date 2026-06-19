# magi-control-plane

> agent-governance control plane — for Claude Code first, agent-agnostic by design.
>
> 2026-06-19. MVP build in progress. spike(`../magi-cp-spike/`) validated all tech thesis;
> spec = `../docs/plans/2026-06-18-magi-control-plane-v0-plan.md §8`; build plan =
> `../docs/plans/2026-06-19-magi-control-plane-mvp-build.md`.

## What this is
Hooks-into-Claude-Code (and later Codex/OpenCode) middleware that lets organizations
enforce their *own* procedures + capture cryptographically-signed evidence trails.
Beachhead: Korean legal filing (citation existence + verbatim verification + partner sign-off).

Three layers:
- **Local** (OSS-able): `magi-cp` CLI, CC hook helper, MCP server. Verify-only — no signing keys.
- **Cloud** (paid SaaS): policy authority, Ed25519 signer, tamper-evident ledger, HITL queue, dashboard.
- **Floor**: managed-settings + plugin = users can't disable. *Subscription expiry = fail-closed*.

## Quick start (dev)
```bash
make install          # editable install + dev deps
make test             # pytest
make cloud-dev        # start cloud API
make build-plugin     # compile policies/*.json → plugin/managed-settings.json
```

## Layout
- `src/magi_cp/verifier/`  — citation verifier (3-way verdict, SourceResolver protocol)
- `src/magi_cp/policy/`    — Policy IR + deterministic compiler (LLM-free)
- `src/magi_cp/evidence/`  — Ed25519 sign/verify, hash-chain ledger, local WAL
- `src/magi_cp/cloud/`     — FastAPI: `/citation_verify` `/hitl` `/pubkey` `/ledger`
- `src/magi_cp/local/`     — CC hook gate + emit (CLI entry points)
- `src/magi_cp/mcp/`       — stdio MCP server: `verify_citations`, `lbox_fetch`
- `plugin/`                — `.claude-plugin` bundle (managed-settings is build target)
- `web/`                   — Next.js dashboard (HITL queue + audit)
- `tests/`                 — pytest

## Status: P7 complete (alpha)
- [x] P0 directory/skeleton
- [x] P1 core port from spike (TDD, 49 tests)
- [x] P2 MCP server (verify_citations + lbox_fetch, 9 tests)
- [x] P3 cloud API (FastAPI + Docker, 35 tests; security-hardened over 2 rounds)
- [x] P4 CC plugin bundle (managed-settings + gate shim, 13 tests)
- [x] P5 HITL + Next.js dashboard (14 vitest)
- [x] P6 NLI advisory (8 tests)
- [x] P7 E2E + 3-perspective review (5 E2E tests)

Test totals: **123 Python + 14 web = 137**. See `magi-cp-spike/` for the
pre-MVP spike that validated the core thesis. See `SECURITY.md` for v0
deferments before partner pilot.

## CLI surface (after `pip install -e .`)
- `magi-cp gate` — PreToolUse hook reader (stdin JSON in, exit + JSON out)
- `magi-cp emit --matter --doc-id …` — request citation_verify, cache in WAL
- `magi-cp await-approval --hitl-id N` — poll until HITL decides, write token to WAL
- `magi-cp compile <policy.json> <out.json>` — Policy IR → managed-settings
- `magi-cp cloud` — run FastAPI cloud server (dev)
- `magi-cp mcp` — stdio MCP server
