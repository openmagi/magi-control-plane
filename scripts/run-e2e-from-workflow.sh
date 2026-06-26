#!/usr/bin/env bash
# D74b: e2e final-smoke wrapper for workflow agents.
#
# Brings the local stack up, runs the D73 Playwright harness, tears
# the stack back down, and emits a structured exit code so a workflow
# script can branch on RED vs INFRA-SKIP without parsing logs.
#
# Exit codes
#   0  all scenarios passed (or honestly SKIPPED, never FAILED)
#   1  at least one scenario FAILED
#   2  harness infra failure (docker missing, ports busy, playwright
#      not installed, dashboard never came up healthy, etc.)
#
# Environment overrides (all optional)
#   MAGI_CP_E2E_SKIP_DOCKER=1     stack already up, skip bring-up/down
#   MAGI_CP_E2E_BASE_URL          dashboard URL    (default :3787)
#   MAGI_CP_CLOUD_URL             cloud  URL       (default :8787)
#   MAGI_CP_E2E_KEEP_STACK=1      do NOT tear down on exit
#   MAGI_CP_E2E_DASHBOARD_CMD     command that launches the dashboard
#                                  (default: `npm run start` inside web/,
#                                  preceded by `npm run build` if no
#                                  .next dir is present)
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

# Tracks whether THIS script started the dashboard process. Used by
# the EXIT trap so we never kill a dashboard the operator was running.
STARTED_DASHBOARD_PID=""

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

# ---- dashboard bring-up helper ---------------------------------------
# Returns 0 once dashboard responds 200 on `/` within deadline.
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

start_dashboard() {
  mkdir -p "$(dirname "${DASH_LOG}")"
  : > "${DASH_LOG}"
  local cmd
  if [ -n "${DASH_CMD_OVERRIDE}" ]; then
    cmd="${DASH_CMD_OVERRIDE}"
  elif [ -d "${REPO_ROOT}/web/.next" ]; then
    cmd="npm run start"
  else
    log "web/.next not found. running npm run build first"
    if ! (cd "${REPO_ROOT}/web" && npm run build) >> "${DASH_LOG}" 2>&1; then
      log_err "web build failed before dashboard could start. See ${DASH_LOG}."
      return 2
    fi
    cmd="npm run start"
  fi
  log "starting dashboard: ${cmd} (log: ${DASH_LOG})"
  ( cd "${REPO_ROOT}/web" && nohup bash -c "${cmd}" >> "${DASH_LOG}" 2>&1 & echo $! > "${REPO_ROOT}/tests/e2e/.report/dashboard.pid" )
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

# ---- teardown trap ---------------------------------------------------
cleanup() {
  local rc=$?
  if [ -n "${KEEP_STACK}" ]; then
    log "MAGI_CP_E2E_KEEP_STACK set; skipping teardown."
    return
  fi
  if [ -n "${STARTED_DASHBOARD_PID}" ]; then
    log "stopping dashboard (pid=${STARTED_DASHBOARD_PID})"
    kill "${STARTED_DASHBOARD_PID}" 2>/dev/null || true
    # Give Next.js a beat to flush; SIGKILL if it ignores TERM.
    sleep 2
    kill -9 "${STARTED_DASHBOARD_PID}" 2>/dev/null || true
  fi
  if [ -z "${SKIP_DOCKER}" ]; then
    log "docker compose down (cloud stack)"
    (cd "${REPO_ROOT}" && docker compose down >/dev/null 2>&1) || true
  fi
  exit "${rc}"
}
trap cleanup EXIT INT TERM

# ---- bring up cloud --------------------------------------------------
if [ -z "${SKIP_DOCKER}" ]; then
  log "docker compose up -d cloud"
  if ! (cd "${REPO_ROOT}" && docker compose up -d cloud) >&2; then
    log_err "docker compose up failed."
    exit 2
  fi
  if ! wait_for_url "${CLOUD_URL}/healthz" 60; then
    log_err "cloud /healthz did not return 200 within 60s at ${CLOUD_URL}/healthz."
    exit 2
  fi
  log_ok "cloud healthy at ${CLOUD_URL}/healthz"
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
log "running playwright e2e:full"
PW_RC=0
( cd "${REPO_ROOT}/tests/e2e" && npx playwright test --reporter=list ) || PW_RC=$?

# Distinguish "scenario failed" (rc=1) from "infra blew up" (rc=2). Any
# rc >= 2 from playwright is treated as infra-skip.
if [ "${PW_RC}" -eq 0 ]; then
  log_ok "all scenarios passed (or skipped). GREEN."
  exit 0
elif [ "${PW_RC}" -eq 1 ]; then
  log_err "at least one scenario FAILED. See tests/e2e/.report/."
  exit 1
else
  log_warn "playwright exited with infra-shaped code ${PW_RC} (treating as INFRA-SKIP)."
  exit 2
fi
