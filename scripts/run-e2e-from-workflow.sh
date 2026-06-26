#!/usr/bin/env bash
# D74b: e2e final-smoke wrapper for workflow agents.
#
# Brings the local stack up, runs the D73 Playwright harness, tears
# the stack back down (only what we started), and emits a structured
# exit code so a workflow script can branch on RED vs INFRA-SKIP vs
# INTERRUPTED without parsing logs.
#
# Exit codes
#   0  all scenarios passed (or honestly SKIPPED, never FAILED)
#   1  at least one scenario FAILED
#   2  harness infra failure (docker missing, ports busy, playwright
#      not installed, dashboard never came up healthy, etc.)
#   3  interrupted (SIGINT / SIGTERM). NOT green. NOT red. Caller must
#      treat as "unknown, do not advertise as smoke result."
#
# Environment overrides (all optional)
#   MAGI_CP_E2E_SKIP_DOCKER=1     stack already up, skip bring-up/down
#   MAGI_CP_E2E_BASE_URL          dashboard URL    (default :3787)
#   MAGI_CP_CLOUD_URL             cloud  URL       (default :8787)
#   MAGI_CP_E2E_KEEP_STACK=1      do NOT tear down on exit
#   MAGI_CP_E2E_DASHBOARD_CMD     command that launches the dashboard
#                                  (default: `npm run start` inside web/,
#                                  preceded by `npm run build` if no
#                                  .next dir is present or
#                                  MAGI_CP_E2E_FORCE_REBUILD=1)
#   MAGI_CP_E2E_FORCE_REBUILD=1   always run `npm run build` before
#                                  starting the dashboard (defensive
#                                  against stale .next from a prior run
#                                  or a different branch).
#   MAGI_CP_E2E_DASHBOARD_LOG     path for the dashboard process log
#                                  (default: tests/e2e/.report/dashboard.log)
#
# This script is a REFERENCE wrapper. Individual workflow scripts can
# inline the seven steps from docs/workflows/final-smoke-template.md
# instead. The goal is a single one-liner that a workflow agent can
# call without orchestrating docker + playwright by hand.

set -euo pipefail

# ---- resolve repo root ------------------------------------------------
SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
REPO_ROOT="$( cd -- "${SCRIPT_DIR}/.." &> /dev/null && pwd )"

# ---- helpers ----------------------------------------------------------
log()      { printf "\033[1;34m[run-e2e]\033[0m %s\n" "$1" >&2; }
log_ok()   { printf "\033[1;32m[run-e2e]\033[0m %s\n" "$1" >&2; }
log_warn() { printf "\033[1;33m[run-e2e]\033[0m %s\n" "$1" >&2; }
log_err()  { printf "\033[1;31m[run-e2e]\033[0m %s\n" "$1" >&2; }

CLOUD_URL="${MAGI_CP_CLOUD_URL:-http://127.0.0.1:8787}"
DASH_URL="${MAGI_CP_E2E_BASE_URL:-http://127.0.0.1:3787}"
SKIP_DOCKER="${MAGI_CP_E2E_SKIP_DOCKER:-}"
KEEP_STACK="${MAGI_CP_E2E_KEEP_STACK:-}"
DASH_LOG="${MAGI_CP_E2E_DASHBOARD_LOG:-${REPO_ROOT}/tests/e2e/.report/dashboard.log}"
DASH_CMD_OVERRIDE="${MAGI_CP_E2E_DASHBOARD_CMD:-}"
FORCE_REBUILD="${MAGI_CP_E2E_FORCE_REBUILD:-}"

# Export so the playwright config + harness preflight see the same URLs
# the wrapper probed. Otherwise an operator who overrides MAGI_CP_E2E_BASE_URL
# at the command line gets the wrapper probing X while playwright tests Y.
export MAGI_CP_E2E_BASE_URL="${DASH_URL}"
export MAGI_CP_CLOUD_URL="${CLOUD_URL}"
if [ -n "${SKIP_DOCKER}" ]; then
  export MAGI_CP_E2E_SKIP_DOCKER="${SKIP_DOCKER}"
fi

# Tracks whether THIS script started the dashboard / cloud. Cleanup
# only tears down what we brought up.
STARTED_DASHBOARD_PID=""
STARTED_CLOUD=""

# ---- pre-flight: tools we need ---------------------------------------
if ! command -v node >/dev/null 2>&1; then
  log_err "node not found on PATH. Install Node 20+ and re-run."
  exit 2
fi

if [ -z "${SKIP_DOCKER}" ] && ! command -v docker >/dev/null 2>&1; then
  log_err "docker not found on PATH (and MAGI_CP_E2E_SKIP_DOCKER unset). Install docker or rerun with MAGI_CP_E2E_SKIP_DOCKER=1."
  exit 2
fi

# ---- npm install in tests/e2e if needed ------------------------------
if [ ! -d "${REPO_ROOT}/tests/e2e/node_modules/@playwright/test" ]; then
  log "tests/e2e/node_modules missing, running npm install"
  (cd "${REPO_ROOT}/tests/e2e" && npm install --no-audit --no-fund) || {
    log_err "npm install in tests/e2e failed."
    exit 2
  }
fi

# ---- playwright chromium browser (guarded install) -------------------
# Without this a fresh runner sees "browserType.launch: Executable
# doesn't exist" and Playwright exits 1, which the contract calls RED.
# Treat that path as infra-skip per README.
PW_CACHE_DIR="${PLAYWRIGHT_BROWSERS_PATH:-${HOME}/.cache/ms-playwright}"
if [ ! -d "${PW_CACHE_DIR}" ] || ! ls "${PW_CACHE_DIR}" 2>/dev/null | grep -q '^chromium-'; then
  log "playwright chromium not found under ${PW_CACHE_DIR}; installing (no sudo)"
  if ! (cd "${REPO_ROOT}/tests/e2e" && npx playwright install chromium) >&2; then
    log_warn "playwright install chromium failed. treating as INFRA-SKIP."
    exit 2
  fi
fi

# ---- helpers ---------------------------------------------------------
# Returns 0 once URL responds 200 within deadline.
wait_for_url() {
  local url="$1"
  local timeout_s="${2:-60}"
  local deadline=$(( $(date +%s) + timeout_s ))
  while [ "$(date +%s)" -lt "${deadline}" ]; do
    local code
    code="$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 4 "${url}" 2>/dev/null || echo "000")"
    if [ "${code}" = "200" ]; then
      return 0
    fi
    sleep 1
  done
  return 1
}

probe_dashboard_already_up() {
  local code
  code="$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 2 "${DASH_URL}/" 2>/dev/null || echo "000")"
  [ "${code}" = "200" ]
}

probe_cloud_already_up() {
  local code
  code="$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 2 "${CLOUD_URL}/healthz" 2>/dev/null || echo "000")"
  [ "${code}" = "200" ]
}

start_dashboard() {
  mkdir -p "$(dirname "${DASH_LOG}")"
  : > "${DASH_LOG}"
  local cmd
  if [ -n "${DASH_CMD_OVERRIDE}" ]; then
    cmd="${DASH_CMD_OVERRIDE}"
  else
    if [ -n "${FORCE_REBUILD}" ] || [ ! -d "${REPO_ROOT}/web/.next" ]; then
      if [ -n "${FORCE_REBUILD}" ]; then
        log "MAGI_CP_E2E_FORCE_REBUILD set. running npm run build to avoid stale .next"
      else
        log "web/.next not found. running npm run build first"
      fi
      if ! (cd "${REPO_ROOT}/web" && npm run build) >> "${DASH_LOG}" 2>&1; then
        log_err "web build failed before dashboard could start. See ${DASH_LOG}."
        return 2
      fi
    fi
    cmd="npm run start"
  fi
  log "starting dashboard: ${cmd} (log: ${DASH_LOG})"
  # Use `setsid` (when available) so the dashboard runs in its own
  # process group and `kill -- -PGID` on cleanup reaps node grandchildren
  # too. nohup+bash on its own leaks the npm/next child to init and
  # poisons the next bring-up via probe_dashboard_already_up.
  local launcher="bash"
  if command -v setsid >/dev/null 2>&1; then
    launcher="setsid bash"
  fi
  ( cd "${REPO_ROOT}/web" && nohup ${launcher} -c "exec ${cmd}" >> "${DASH_LOG}" 2>&1 & echo $! > "${REPO_ROOT}/tests/e2e/.report/dashboard.pid" )
  STARTED_DASHBOARD_PID="$(cat "${REPO_ROOT}/tests/e2e/.report/dashboard.pid" 2>/dev/null || true)"
  if [ -z "${STARTED_DASHBOARD_PID}" ]; then
    log_err "failed to capture dashboard PID"
    return 2
  fi
  if ! wait_for_url "${DASH_URL}/" 90; then
    log_err "dashboard did not become healthy at ${DASH_URL}/ within 90s. See ${DASH_LOG}."
    return 2
  fi
  log_ok "dashboard healthy at ${DASH_URL}/"
  return 0
}

# ---- teardown traps --------------------------------------------------
# Split traps: EXIT runs cleanup with the *real* rc; INT/TERM force rc=3
# (INTERRUPTED) before falling through. The old single-trap pattern
# read $? from the trap entry, which on signal-driven entry was the
# last completed command's status. A SIGTERM that arrived after a
# successful curl produced rc=0 and exit 0 (false GREEN).
RC_OVERRIDE=""

on_signal() {
  # Override rc and unregister so the EXIT trap below uses our value.
  RC_OVERRIDE=3
  log_warn "received signal; treating run as INTERRUPTED (exit 3)."
  trap - INT TERM
  # Cleanup runs from the EXIT trap with rc=3.
  exit 3
}
on_exit() {
  local rc=$?
  trap - EXIT INT TERM
  if [ -n "${RC_OVERRIDE}" ]; then
    rc="${RC_OVERRIDE}"
  fi
  if [ -n "${KEEP_STACK}" ]; then
    log "MAGI_CP_E2E_KEEP_STACK set; skipping teardown."
    exit "${rc}"
  fi
  if [ -n "${STARTED_DASHBOARD_PID}" ]; then
    log "stopping dashboard (pid=${STARTED_DASHBOARD_PID})"
    # Kill the whole process group (negative pid). If setsid was
    # unavailable, fall back to direct kill + lsof belt below.
    kill -TERM "-${STARTED_DASHBOARD_PID}" 2>/dev/null \
      || kill -TERM "${STARTED_DASHBOARD_PID}" 2>/dev/null \
      || true
    sleep 2
    kill -KILL "-${STARTED_DASHBOARD_PID}" 2>/dev/null \
      || kill -KILL "${STARTED_DASHBOARD_PID}" 2>/dev/null \
      || true
    # Belt-and-suspenders: anything still listening on the dashboard
    # port is a leaked node from this run (or a previous crash). The
    # operator-managed case is handled at start time (we never set
    # STARTED_DASHBOARD_PID if probe_dashboard_already_up was true).
    if command -v lsof >/dev/null 2>&1; then
      local port_part="${DASH_URL##*:}"
      local port="${port_part%%/*}"
      if [ -n "${port}" ]; then
        local pids
        pids="$(lsof -ti "tcp:${port}" 2>/dev/null || true)"
        if [ -n "${pids}" ]; then
          # shellcheck disable=SC2086
          kill -9 ${pids} 2>/dev/null || true
        fi
      fi
    fi
  fi
  if [ -z "${SKIP_DOCKER}" ] && [ -n "${STARTED_CLOUD}" ]; then
    # `stop cloud` (not `down`) leaves the container around for
    # `docker logs` and does not blow away any other compose services
    # the operator may have running.
    log "docker compose stop cloud"
    (cd "${REPO_ROOT}" && docker compose stop cloud >/dev/null 2>&1) || true
  fi
  exit "${rc}"
}
trap on_exit EXIT
trap on_signal INT TERM

# ---- bring up cloud --------------------------------------------------
if [ -z "${SKIP_DOCKER}" ]; then
  if probe_cloud_already_up; then
    log "cloud already healthy at ${CLOUD_URL}/healthz. not starting a second instance"
  else
    log "docker compose up -d cloud"
    if ! (cd "${REPO_ROOT}" && docker compose up -d cloud) >&2; then
      log_err "docker compose up failed."
      exit 2
    fi
    STARTED_CLOUD=1
    if ! wait_for_url "${CLOUD_URL}/healthz" 60; then
      log_err "cloud /healthz did not return 200 within 60s at ${CLOUD_URL}/healthz."
      exit 2
    fi
    log_ok "cloud healthy at ${CLOUD_URL}/healthz"
  fi
else
  log "MAGI_CP_E2E_SKIP_DOCKER set; assuming cloud already healthy at ${CLOUD_URL}"
fi

# ---- bring up dashboard ----------------------------------------------
if probe_dashboard_already_up; then
  log "dashboard already up at ${DASH_URL}/. not starting a second instance"
else
  if ! start_dashboard; then
    exit 2
  fi
fi

# ---- run playwright --------------------------------------------------
# Note: do NOT pass --reporter=list. playwright.config.ts already lists
# list + json + html. Overriding the reporter array would suppress the
# json file, which the curated report.ts depends on (and which the
# exit-code disambiguation below reads).
log "running playwright e2e:full"
PW_RC=0
( cd "${REPO_ROOT}/tests/e2e" && npx playwright test ) || PW_RC=$?

# ---- exit-code disambiguation ----------------------------------------
# Playwright exits 1 for two very different cases:
#   (a) at least one scenario asserted-FAIL (the documented RED case)
#   (b) infra-shaped failure (globalSetup threw, config parse error,
#       missing browser binary, missing admin keys). All of (b) look
#       like RED to the contract, sending future agents to hunt for
#       code regressions that do not exist.
#
# Disambiguate with two sidecar artifacts:
#   - tests/e2e/.report/preflight.json -> { skip: true, reason }
#     means the harness gracefully stopped before any scenario ran.
#   - tests/e2e/.report/report.json     -> totals.{pass,fail,skip}
#     means scenarios were observed. Only report.json.totals.fail > 0
#     is a true RED.
REPORT_JSON="${REPO_ROOT}/tests/e2e/.report/report.json"
PREFLIGHT_JSON="${REPO_ROOT}/tests/e2e/.report/preflight.json"

preflight_skipped=0
if [ -f "${PREFLIGHT_JSON}" ]; then
  if node -e "process.exit(JSON.parse(require('fs').readFileSync('${PREFLIGHT_JSON}','utf8')).skip === true ? 0 : 1)" 2>/dev/null; then
    preflight_skipped=1
  fi
fi

report_fail_count=""
if [ -f "${REPORT_JSON}" ]; then
  report_fail_count="$(node -e "try { const r = JSON.parse(require('fs').readFileSync('${REPORT_JSON}','utf8')); process.stdout.write(String(r.totals && r.totals.fail || 0)); } catch (_) { process.stdout.write(''); }" 2>/dev/null || true)"
fi

if [ "${PW_RC}" -eq 0 ]; then
  log_ok "all scenarios passed (or skipped). GREEN."
  exit 0
fi

if [ "${preflight_skipped}" = "1" ]; then
  log_warn "preflight reported skip; treating as INFRA-SKIP."
  exit 2
fi

if [ "${PW_RC}" -eq 1 ]; then
  if [ -n "${report_fail_count}" ] && [ "${report_fail_count}" -gt 0 ] 2>/dev/null; then
    log_err "${report_fail_count} scenario(s) FAILED. See tests/e2e/.report/."
    exit 1
  fi
  log_warn "playwright exited 1 but curated report shows no FAIL rows; treating as INFRA-SKIP."
  exit 2
fi

log_warn "playwright exited with infra-shaped code ${PW_RC} (treating as INFRA-SKIP)."
exit 2
