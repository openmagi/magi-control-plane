# Workflow final-smoke phase. canonical template

This is the reference recipe that every future `magi-control-plane`
workflow should follow in its final-smoke phase. Past workflows (D75-D82
and friends) shipped Playwright selectors and URL contracts that the
D73 E2E harness asserts on, but the harness was never wired into the
smoke phase. D74a confirmed the harness runs cleanly against the live
dashboard. D74b makes "run the E2E harness" a default step.

A workflow's final-smoke phase MUST stop on the first failure. Each
step below is sequenced; do NOT parallelize.

## The seven steps

Run them from the repository root unless noted.

### 1. `pytest -q`

Python unit + integration suite for the cloud and gate layers. ~325
tests at time of writing. Fast, hermetic.

- **Stop on failure:** yes. A Python regression breaks every downstream
  step. Do not advance until pytest is green.
- **Known skip reasons:** none. If a test is skipped it is annotated in
  the suite itself (e.g. optional `pyshacl` provider).

### 2. `cd web && npx tsc --noEmit`

TypeScript type-check for the dashboard. Catches the bulk of refactor
breakage before the bundler ever starts.

- **Stop on failure:** yes.
- **Known skip reasons:** none.

### 3. `cd web && npx vitest run`

React unit tests for dashboard components and helpers. Includes the
i18n drift gate.

- **Stop on failure:** yes.
- **Known skip reasons:** none.

### 4. `cd web && npm run build`

Production Next.js build. Verifies the dashboard compiles end-to-end
under the same flags `docker compose` will run with.

- **Stop on failure:** yes.
- **Known skip reasons:** the `prebuild` hook copies
  `scripts/quickstart.sh` into `public/install.sh`. If that copy fails
  the build still proceeds (`|| true`). That is intentional and not a
  smoke failure.

### 5. Bring up the local stack

```bash
cd /Users/kevin/Desktop/claude_code/magi-control-plane
docker compose up -d cloud
(cd web && npm run start &)   # or `npm run dev` for a hot-reload smoke
```

Wait for both to report healthy:

- cloud: `curl -fsS http://127.0.0.1:8787/healthz | jq -r .status` returns `ok`
- dashboard: `curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3787/` returns `200`

The D73 harness's `globalSetup` brings the cloud container up itself
when `MAGI_CP_E2E_SKIP_DOCKER` is unset; if your workflow runner
already has the stack up, set `MAGI_CP_E2E_SKIP_DOCKER=1` instead.

- **Stop on failure:** yes. Treat docker / port-bind failures as infra
  errors (exit code 2 from the wrapper script, see step 6).
- **Known skip reasons:**
  - **`docker` daemon not available:** the wrapper script downgrades
    the whole phase to "infra-skip" and reports exit code 2. The
    workflow agent should record the skip and continue, not abort.
  - **CI sandbox forbids `playwright install --with-deps`:** same
    handling: exit 2, infra-skip.

### 6. `cd tests/e2e && npx playwright test --reporter=list`

Drive the five baseline scenarios:

| id | scenario | needs `claude` binary? |
| -- | -------- | ---------------------- |
| 01 | wizard happy path | no |
| 02 | prebuilt toggle roundtrip | no |
| 03 | scripts upload and use | no |
| 04 | run_command roundtrip | yes |
| 05 | inject_context roundtrip | yes |

- **Stop on failure:** yes when a scenario FAILS. SKIP does NOT count
  as failure.
- **Known skip reasons:**
  - `claude` binary missing (PATH lookup, `MAGI_CP_E2E_CLAUDE_BIN`
    unset, or hook not installed in the CC settings.json the harness
    can locate): scenarios 04 + 05 self-SKIP with a precise reason.
    01-03 still run.
  - `MAGI_CP_E2E_SKIP_DOCKER=1` was set but the operator-managed
    cloud / dashboard never came up: the preflight downgrades the
    whole suite to SKIP with the reason in
    `tests/e2e/.report/preflight.json`.

The curated `tests/e2e/.report/report.json` is written even on the
graceful-skip path, so the workflow can always attach it for the
final-review pass.

A workflow agent should prefer the convenience wrapper at
[`scripts/run-e2e-from-workflow.sh`](../../scripts/run-e2e-from-workflow.sh)
which packages the bring-up + harness + teardown dance behind a
single command with a structured exit code. The wrapper is
non-destructive: it only tears down what it brought up (probes
`cloud` and dashboard before starting; uses `docker compose stop cloud`
rather than `docker compose down` on cleanup; never touches other
compose services).

### 7. (optional) `docker compose down`

Only tear down when the workflow runner is ephemeral. If a developer
is iterating locally, leave the stack up. the next smoke run will
re-use it via `MAGI_CP_E2E_SKIP_DOCKER=1`.

## Exit-code convention

The wrapper script `scripts/run-e2e-from-workflow.sh` standardizes
exit codes so a workflow agent can branch on them:

| code | meaning |
| ---- | ------- |
| 0    | All scenarios passed (or honestly SKIPPED). Phase is GREEN. |
| 1    | At least one scenario FAILED. Phase is RED. Fix and re-run. |
| 2    | Harness infra failure (docker missing, playwright install blocked, browser binary missing, ports busy, preflight tripped). NOT a code regression. Phase is INFRA-SKIP. |
| 3    | Interrupted (SIGINT / SIGTERM). NOT green and NOT red. Treat as "unknown, do not advertise as a smoke result." |

The wrapper does NOT propagate `playwright test`'s native exit code as-is.
Codes are normalized into the four-bucket contract above:

- `playwright test` exit 0 maps to wrapper exit 0.
- `playwright test` exit 1 is disambiguated by reading
  `tests/e2e/.report/preflight.json` and `tests/e2e/.report/report.json`.
  A graceful preflight skip OR a curated report with zero FAIL rows
  re-routes to exit 2 (INFRA-SKIP). Only when at least one scenario
  carries a FAILED status does the wrapper emit exit 1.
- Any other `playwright test` non-zero code is treated as exit 2
  (INFRA-SKIP), matching the README's "graceful skip" contract.
- SIGINT / SIGTERM during the run sets the wrapper's exit to 3 even
  when the last completed command exited 0. The old single-trap
  pattern would have falsely reported GREEN here.

## What this template is NOT

- It is NOT a workflow script in itself. Individual workflows opt in
  by invoking the wrapper script (or the seven steps inline) from
  their own final-smoke phase.
- It does NOT replace `.github/workflows/ci.yml`. CI deliberately
  excludes the Playwright harness (see `tests/e2e/README.md` § "Not
  for CI"). Smoke is a developer / workflow concern.
- It does NOT install the `claude` binary or the magi-cp hook in
  `~/.claude/settings.json`. Scenarios 04 + 05 graceful-SKIP when
  those are missing; installing them is an operator step
  (`scripts/quickstart.sh`).

## Pointer for workflow authors

EVERY workflow's final-smoke phase SHOULD include this template's
seven steps unless the change is provably docs-only. The
path-of-least-resistance is:

```bash
# inside your workflow's final-smoke phase
bash scripts/run-e2e-from-workflow.sh
```

which runs steps 5, 6, and 7 with sensible defaults. Steps 1-4 are
already part of every workflow's pre-commit gates today, so adding
the wrapper invocation is the one-line activation.

**Opt-out marker.** A workflow whose diff is genuinely docs-only (no
files under `web/`, `src/`, `tests/`, `scripts/`, `plugin/`, or
`docker-compose*.yml`) may add the marker `# smoke: docs-only` to its
workflow header (or commit message) and skip the wrapper. Anything
that touches code, infra, or test fixtures requires the wrapper.

This removes the "judgment call" that the old soft phrasing left to
each workflow author.
