# tests/e2e — D73 end-to-end harness

Playwright-driven scenarios that click the magi-cp dashboard and watch
the cloud ledger respond, plus a `claude -p` subprocess wrapper for
runtime-side assertions on CC-hook policies.

**This harness is opt-in.** It is NOT wired into:

- `npm test` / `npm run build` under `web/`,
- `pytest -q` at the repo root,
- the `.github/workflows/ci.yml` jobs.

It runs on demand via the `npm run e2e:*` scripts in this directory, or
from a workflow's final-smoke step.

## Why

After many workflows, the dashboard surface is wide and silent
regressions keep slipping past `tsc --noEmit` + `vitest`. Recent
examples:

- Step 3 -> 4 silent reject in the Guided wizard
- Step 4 -> 5 silent reject
- `/rules` client-side exception
- picker landing force-redirect (mode auto-picked)
- `NlAuthoringGuide` `t` closure crash

These all type-check. They all pass vitest. They all break the user
journey. D73 wires a Playwright harness that drives the journey and
asserts both the URL state and the cloud ledger.

## Prereqs

- **Node 20+** (Playwright requires it).
- **Docker** (for the cloud stack; can be skipped via env, see below).
- **Optional: `claude` binary** for scenarios 04 + 05. When absent,
  those scenarios mark themselves SKIP in the report (they do NOT fail).
- A `.env` with the same secrets the docker-compose stack requires:
  `MAGI_CP_HITL_API_KEY`, `MAGI_CP_API_KEY`, `MAGI_CP_ADMIN_API_KEY`,
  `MAGI_CP_ADMIN_HMAC_SECRET`.

## One-time install

```bash
cd tests/e2e
npm install
npm run e2e:install   # downloads chromium + deps
```

The `playwright install --with-deps chromium` step requires `sudo`
under Linux for the apt deps. On dev workstations this is a one-time
cost; in a smoke-runner sandbox it may not be allowed — in that case
the smoke phase should report "infra ok, scenarios not runnable here"
and move on.

## Run

All scenarios:

```bash
npm run e2e:full
```

A single scenario:

```bash
npm run e2e:wizard
npm run e2e:prebuilt
npm run e2e:scripts
npm run e2e:run-command
npm run e2e:inject-context
```

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAGI_CP_E2E_BASE_URL` | `http://127.0.0.1:3787` | Where the dashboard is reachable. |
| `MAGI_CP_CLOUD_URL` | `http://127.0.0.1:8787` | Where the cloud API is reachable. |
| `MAGI_CP_E2E_SKIP_DOCKER` | unset | When `1`, the harness assumes the stack is already up and skips compose bring-up / tear-down. |
| `MAGI_CP_E2E_CLAUDE_BIN` | unset | Absolute path to the `claude` binary. When unset the helper looks on PATH. |
| `MAGI_CP_E2E_CLAUDE_TIMEOUT_MS` | `60000` | Wall-time bound on each `claude -p` invocation. |
| `MAGI_CP_API_KEY` / `MAGI_CP_ADMIN_API_KEY` / `MAGI_CP_ADMIN_HMAC_SECRET` | required | Read by the cloud HTTP client. Same as docker-compose. |

## Expected docker state

The harness assumes:

- The `cloud` service from the repo's `docker-compose.yml` is up on
  port 8787, OR `MAGI_CP_E2E_SKIP_DOCKER=1` and the operator has
  already brought a stack up.
- The dashboard's Next.js dev server (or production build) is up on
  port 3787.

For a clean run the harness brings up a fresh `cloud` service under a
unique compose project name when `MAGI_CP_E2E_SKIP_DOCKER` is unset.
The throwaway data dir lives under `$TMPDIR` and is removed on
`downStack()`.

## Where the report lives

After a run, Playwright writes:

- `tests/e2e/.report/playwright.json` — raw Playwright JSON reporter.
- `tests/e2e/.report/playwright-html/` — Playwright's built-in HTML
  report (one HTML file + assets).
- `tests/e2e/.report/test-results/` — screenshots, traces.

The `helpers/report.ts` module post-processes `playwright.json` into a
curated:

- `tests/e2e/.report/report.json` — `{ scenarios[], totals, ... }` in
  the shape D73 specifies.
- `tests/e2e/.report/report.html` — a flat table linking to the
  Playwright assets.

To regenerate manually:

```bash
node helpers/report.js .report/playwright.json .report
```

## Scenarios

| id | name | LLM? |
| --- | --- | --- |
| 01 | wizard happy path | no |
| 02 | prebuilt toggle roundtrip | no |
| 03 | scripts upload and use | no |
| 04 | run_command roundtrip | yes (claude -p) |
| 05 | inject_context roundtrip | yes (claude -p) |

Scenarios 04 + 05 SKIP when `claude` is missing — the report still
reports green for the other three.

## Adding a scenario

1. Add a `scenarios/NN-foo.spec.ts` mirroring the existing scenarios.
2. Reuse helpers from `helpers/`. New page-object verbs go on
   `helpers/dashboard.ts`.
3. Add a script alias in `package.json` (`e2e:foo`).
4. Update this README's scenario table.

## Not for CI

`.github/workflows/ci.yml` does NOT call `npm run e2e:*`. Workflow
agents that want a smoke phase to invoke the harness should do so
explicitly. D74 is reserved for that wiring.
