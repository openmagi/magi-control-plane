# AGENTS.md. canonical operating guide for agent harnesses

This file is the canonical place for agent-only operating notes in this
repo. Claude Code reads `CLAUDE.md`, Codex CLI / OpenCode / Cursor and
other harnesses read this file. To keep both surfaces in sync,
`CLAUDE.md` is a one-line pointer to this file.

User-facing project docs live in `README.md` and `CONTRIBUTING.md`.
Anything an agent needs that is not in those two files belongs here.

## Final-smoke phase (every workflow)

Every workflow's final-smoke phase SHOULD run
[`scripts/run-e2e-from-workflow.sh`](scripts/run-e2e-from-workflow.sh)
unless the change is provably docs-only.

The wrapper packages docker bring-up + Playwright + teardown into one
command. The canonical seven-step recipe (each step's stop-on-failure
rules, known skip reasons, env knobs) is at
[`docs/workflows/final-smoke-template.md`](docs/workflows/final-smoke-template.md).

**Opt-out marker.** A workflow whose diff is genuinely docs-only (no
files under `web/`, `src/`, `tests/`, `scripts/`, `plugin/`, or
`docker-compose*.yml`) may add the marker `# smoke: docs-only` to its
workflow header (or commit message) and skip the wrapper. Anything
that touches code, infra, or test fixtures requires the wrapper.

Exit codes from the wrapper:

| code | meaning |
| ---- | ------- |
| `0`  | GREEN. All scenarios passed or honestly SKIPPED. |
| `1`  | RED. At least one scenario FAILED. Fix and re-run. |
| `2`  | INFRA-SKIP. Docker missing, ports busy, build broke, browser binary missing, preflight tripped. Not a code regression. |
| `3`  | INTERRUPTED. SIGINT / SIGTERM mid-run. Treat as "unknown, do not advertise as smoke result." |

The wrapper disambiguates `playwright test` exit 1 by reading
`tests/e2e/.report/preflight.json` and `tests/e2e/.report/report.json`:
a graceful preflight skip and a "zero FAIL rows" report both re-route
to exit 2. Only a true scenario-level failure produces exit 1.

`pytest -q`, `cd web && npx tsc --noEmit`, `cd web && npx vitest run`,
and `cd web && npm run build` remain mandatory pre-commit gates
regardless of whether a workflow runs the E2E harness.

## House rules

- No em-dashes anywhere (source, tests, docs, commit messages).
- Brand: "Open Magi". Runtime: `magi-agent`.
- Korean primary in `web/lib/i18n/locales/ko.ts`; English in
  `web/lib/i18n/locales/en.ts`.
- Client components read `locale` from props, never close over a `t`
  factory. Sub-path imports only inside client trees.
- One commit per PR. Fast-forward push to `openmagi/main`.
- Never commit secrets. `.env*` files stay local.
- Commit messages via heredoc to avoid quoting bugs.

## Pointer surface check

Future agents grepping for the smoke pointer should hit both files:

```bash
grep -l 'run-e2e-from-workflow' AGENTS.md CLAUDE.md
```

If either file is missing the pointer, restore it before merging your
workflow.
