# tests/e2e. D73 end-to-end harness

Playwright-driven scenarios that click the magi-cp dashboard and watch
the cloud ledger respond, plus a `claude -p` subprocess wrapper for
runtime-side assertions on CC-hook policies.

**This harness is opt-in.** It is NOT wired into:

- `npm test` / `npm run build` under `web/`,
- `pytest -q` at the repo root (see `pyproject.toml` `norecursedirs`),
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
- **Docker** (for the cloud stack. can be skipped via env, see below).
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
cost. In a smoke-runner sandbox it may not be allowed; in that case
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
| `MAGI_CP_E2E_CLAUDE_BIN` | unset | Absolute path to the `claude` binary. Must point at an executable file. When unset the helper looks on PATH. |
| `MAGI_CP_E2E_CLAUDE_TIMEOUT_MS` | `60000` | Wall-time bound on each `claude -p` invocation. On timeout the wrapper escalates SIGTERM to SIGKILL after 5s and resolves with code=124 so the suite never hangs. |
| `MAGI_CP_API_KEY` / `MAGI_CP_ADMIN_API_KEY` / `MAGI_CP_ADMIN_HMAC_SECRET` | required | Read by the cloud HTTP client. Same as docker-compose. |
| `GIT_SHA` / `GIT_BRANCH` | unset | Override the values written into `report.json` `meta.git_sha` / `meta.branch`. Defaults derived from `git rev-parse`. |

## Expected docker state

The harness assumes one of:

1. **Auto bring-up (default):** the harness's `globalSetup` calls
   `upStack()` which runs `docker compose -f <repo>/docker-compose.yml
   -p <unique-project> up -d cloud` and waits for `/healthz`. The
   throwaway data dir lives under `$TMPDIR` and is removed on
   `globalTeardown -> downStack()`.
2. **Operator-managed stack:** set `MAGI_CP_E2E_SKIP_DOCKER=1` and
   bring the cloud + dashboard up yourself. The harness's preflight
   skips bring-up but still probes `/healthz` + dashboard `/`.

When `docker` is missing OR the daemon is down OR the compose file
cannot be located, the preflight writes
`.report/preflight.json` with `{ skip: true, reason }` and every spec
calls `test.skip(reason)`. The curated `report.json` is still written
(synthesized from the sidecar) so the workflow always sees an artifact.

The dashboard's Next.js dev server (or production build) MUST be up on
port 3787 (or whatever `MAGI_CP_E2E_BASE_URL` points at). Preflight
probes it explicitly so a missing dashboard surfaces as SKIP with
reason instead of a cryptic Playwright net error.

## Where the report lives

After a run, Playwright writes:

- `tests/e2e/.report/playwright.json`. raw Playwright JSON reporter.
- `tests/e2e/.report/playwright-html/`. Playwright's built-in HTML
  report (one HTML file + assets).
- `tests/e2e/.report/test-results/`. screenshots, traces.
- `tests/e2e/.report/preflight.json`. preflight sidecar (skip status +
  reasons + claude availability).

The `helpers/report.ts` module (also invoked from `globalTeardown`)
post-processes `playwright.json` into a curated:

- `tests/e2e/.report/report.json`. curated `{ scenarios[], totals,
  meta, missing? }`. Each scenario carries `errors[]`,
  `attachments[]`, `steps[]`, `ledger_rows[]`, `screenshots[]`, `trace`.
- `tests/e2e/.report/report.html`. a flat table linking to the
  Playwright assets, with per-step status when available.

The curated report is written even on graceful-skip paths (docker
missing, dashboard down, admin keys unset). When Playwright produced
no JSON the report builder synthesizes a SKIP entry per expected
scenario id from `EXPECTED_SCENARIO_FILE_PREFIXES`.

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

Scenarios 04 + 05 SKIP when `claude` is missing. The report still
reports green for the other three.

## Adding a scenario

1. Add a `scenarios/NN-foo.spec.ts` mirroring the existing scenarios.
   - Start with `const skipReason = assertHarnessReady(...)` +
     `test.skip(skipReason != null, skipReason ?? "")` so the
     centralized preflight cascades cleanly.
2. Reuse helpers from `helpers/`. New page-object verbs go on
   `helpers/dashboard.ts`.
3. Add a script alias in `package.json` (`e2e:foo`).
4. Update this README's scenario table.
5. Add the file prefix to `EXPECTED_SCENARIO_FILE_PREFIXES` in
   `helpers/report.ts` so a vanished scenario surfaces under
   `report.missing[]` rather than silently disappearing.

## Graceful-skip contract

Every preflight failure converts into a SKIP, not a FAIL:

- docker missing or daemon down -> preflight sidecar -> all specs skip.
- compose file cannot be located -> preflight sidecar -> all specs skip.
- cloud `/healthz` not 2xx -> preflight sidecar -> all specs skip.
- dashboard `/` not reachable -> preflight sidecar -> all specs skip.
- `MAGI_CP_ADMIN_API_KEY` / `MAGI_CP_API_KEY` unset -> preflight skip.
- `claude` binary missing OR not executable -> scenarios 04 + 05 skip
  (01-03 still run).
- claude binary hangs past `MAGI_CP_E2E_CLAUDE_TIMEOUT_MS` ->
  SIGTERM, then SIGKILL after 5s. result.code=124, scenario still
  produces a result.

In every case `report.json` + `report.html` are written. The curated
`report.json` carries `meta.git_sha`, `meta.branch`, `meta.node_version`,
`meta.playwright_version`, `meta.skipped_docker`, `meta.env_flags` so a
fix-pass agent can cross-reference the run.

## Not for CI

`.github/workflows/ci.yml` does NOT call `npm run e2e:*`. Workflow
agents that want a smoke phase to invoke the harness should do so
explicitly. D74 is reserved for that wiring.

## D74b: wiring this into a workflow's final-smoke phase

When a workflow script wants to add Playwright e2e to its final-smoke
phase, the path of least resistance is:

```bash
bash scripts/run-e2e-from-workflow.sh
```

That wrapper brings the cloud + dashboard up (or skips bring-up via
`MAGI_CP_E2E_SKIP_DOCKER=1`), runs the harness, tears the stack down,
and exits with a structured code:

- `0`  all scenarios passed (or honestly SKIPPED, claude missing etc.)
- `1`  at least one scenario FAILED
- `2`  harness infra failure (docker missing, ports busy, build broke)

See [`docs/workflows/final-smoke-template.md`](../../docs/workflows/final-smoke-template.md)
for the canonical seven-step recipe a new workflow should follow.
