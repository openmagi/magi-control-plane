# CLAUDE.md. operating notes for Claude Code agents in this repo

This file is read automatically by Claude Code when it enters the
`magi-control-plane` working tree. It is intentionally short. The
authoritative project guide is `README.md`; this file collects the
agent-only pointers that do not belong in user-facing docs.

## Final-smoke phase

When a workflow's final-smoke phase touches the dashboard, the wizard,
the cloud HTTP surface, or any policy authoring flow, that phase
SHOULD run the D73 Playwright E2E harness. See
[`docs/workflows/final-smoke-template.md`](docs/workflows/final-smoke-template.md)
for the canonical seven-step recipe and
[`scripts/run-e2e-from-workflow.sh`](scripts/run-e2e-from-workflow.sh)
for the one-line wrapper a workflow agent can invoke.

Exit-code convention from the wrapper:

- `0`: green (all scenarios passed or honestly SKIPPED)
- `1`: at least one scenario FAILED (fix and re-run)
- `2`: infra failure (docker missing, ports busy, build broke)

`pytest -q`, `tsc --noEmit`, `vitest run`, and `npm run build` remain
mandatory pre-commit gates regardless of whether a workflow runs the
E2E harness.

## House rules

- No em-dashes in source, tests, or docs.
- Brand: "Open Magi". Runtime: `magi-agent`.
- Korean primary in `web/lib/i18n/locales/ko.ts`; English in `en.ts`.
- Client components read `locale` props, not a `t` closure.
- One commit per PR. Fast-forward to `openmagi/main`.
- Never commit secrets. `.env*` files stay local.
