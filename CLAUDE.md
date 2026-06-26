# CLAUDE.md. operating notes for Claude Code agents in this repo

The canonical agent guide is [`AGENTS.md`](AGENTS.md). It carries the
final-smoke phase pointer, the wrapper exit-code legend, and the
house rules. Codex CLI, OpenCode, Cursor, and any other harness that
does not auto-load `CLAUDE.md` will read `AGENTS.md` by convention,
so we keep one source of truth.

Quick pointers (full detail in `AGENTS.md`):

- Every workflow's final-smoke phase SHOULD run
  [`scripts/run-e2e-from-workflow.sh`](scripts/run-e2e-from-workflow.sh)
  unless the diff is provably docs-only (opt-out marker:
  `# smoke: docs-only`).
- Canonical recipe:
  [`docs/workflows/final-smoke-template.md`](docs/workflows/final-smoke-template.md).
- Exit codes: `0` GREEN, `1` RED, `2` INFRA-SKIP, `3` INTERRUPTED.
- `pytest -q`, `tsc --noEmit`, `vitest run`, `npm run build` remain
  mandatory pre-commit gates.

See `README.md` and `CONTRIBUTING.md` for project house rules.
