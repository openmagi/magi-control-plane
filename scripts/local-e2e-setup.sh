#!/usr/bin/env bash
# Local end-to-end setup for magi-control-plane.
#
# What it does (idempotent — safe to re-run):
#   1. Boot the cloud backend on 127.0.0.1:8787 (uvicorn, factory mode).
#   2. Verify the Next.js dashboard is on 127.0.0.1:3787 (start hint if not).
#   3. pip-install the magi-cp package in editable mode so `magi-cp-gate`
#      resolves on PATH.
#   4. Drop ~/.claude/managed-settings.json that wires PreToolUse hook
#      to magi-cp-gate.sh (one shell shim at ~/.local/bin/magi-cp-gate.sh
#      that just `exec`s the entry point).
#   5. Persist MAGI_CP_API_KEY + MAGI_CP_CLOUD_URL to a config file the
#      gate sources at runtime.
#   6. Print the smoke-test command + the manual Claude Code test path.
#
# Pre-existing single-tenant env mode: when MAGI_CP_API_KEY is set on
# the BACKEND env, that value IS the auth key (no HMAC admin / DB seed
# needed). We use the same key on the gate side and everything else
# follows.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_DIR="${REPO_ROOT}/_devdb"
LOG_DIR="/tmp/magi-cp"
DASH_PORT="${MAGI_CP_DASH_PORT:-3787}"
CLOUD_PORT="${MAGI_CP_CLOUD_PORT:-8787}"
TEST_KEY="${MAGI_CP_API_KEY:-mcp_localdev_$(openssl rand -hex 6)}"
LLM_KEY="${MAGI_CP_HITL_API_KEY:-hitl-localdev}"
ADMIN_KEY="${MAGI_CP_ADMIN_API_KEY:-admin-localdev}"
HMAC_SECRET="${MAGI_CP_ADMIN_HMAC_SECRET:-hmac-localdev-secret}"

step() { printf "\033[1;34m→\033[0m %s\n" "$1"; }
ok()   { printf "\033[1;32m✓\033[0m %s\n" "$1"; }
warn() { printf "\033[1;33m!\033[0m %s\n" "$1" >&2; }
fail() { printf "\033[1;31m✗\033[0m %s\n" "$1" >&2; exit 1; }

# ── 1. Backend ────────────────────────────────────────────────────
mkdir -p "$DB_DIR" "$LOG_DIR"

if lsof -nP -iTCP:${CLOUD_PORT} -sTCP:LISTEN >/dev/null 2>&1; then
  ok "backend already up on :${CLOUD_PORT}"
else
  step "starting backend on :${CLOUD_PORT}"
  (
    cd "$REPO_ROOT"
    env \
      MAGI_CP_API_KEY="$TEST_KEY" \
      MAGI_CP_HITL_API_KEY="$LLM_KEY" \
      MAGI_CP_ADMIN_API_KEY="$ADMIN_KEY" \
      MAGI_CP_ADMIN_HMAC_SECRET="$HMAC_SECRET" \
      MAGI_CP_DSN="sqlite:///${DB_DIR}/cloud.sqlite" \
      MAGI_CP_POLICY_STORE="${DB_DIR}/policies.json" \
      .venv/bin/python -m uvicorn \
        magi_cp.cloud.app:_build_production_app \
        --factory --host 127.0.0.1 --port "${CLOUD_PORT}" \
        > "${LOG_DIR}/cloud.log" 2>&1 &
  )
  for i in 1 2 3 4 5 6 7 8; do
    sleep 0.6
    if curl -fsS "http://127.0.0.1:${CLOUD_PORT}/healthz" >/dev/null 2>&1; then
      ok "backend healthy"
      break
    fi
  done
  curl -fsS "http://127.0.0.1:${CLOUD_PORT}/healthz" >/dev/null || \
    fail "backend never came up — see ${LOG_DIR}/cloud.log"
fi

# ── 2. Dashboard ──────────────────────────────────────────────────
if lsof -nP -iTCP:${DASH_PORT} -sTCP:LISTEN >/dev/null 2>&1; then
  ok "dashboard already up on :${DASH_PORT}"
else
  warn "dashboard not running. Start it in another terminal:"
  warn ""
  warn "  cd ${REPO_ROOT}/web && \\"
  warn "    MAGI_CP_API_KEY=${TEST_KEY} \\"
  warn "    MAGI_CP_HITL_API_KEY=${LLM_KEY} \\"
  warn "    MAGI_CP_ADMIN_API_KEY=${ADMIN_KEY} \\"
  warn "    MAGI_CP_ADMIN_HMAC_SECRET=${HMAC_SECRET} \\"
  warn "    MAGI_CP_CLOUD_URL=http://127.0.0.1:${CLOUD_PORT} \\"
  warn "    npx next dev -p ${DASH_PORT}"
fi

# ── 3. CLI install ────────────────────────────────────────────────
if "${REPO_ROOT}/.venv/bin/python" -c "import magi_cp" 2>/dev/null; then
  ok "magi-cp package importable from .venv"
else
  step "pip install -e ."
  "${REPO_ROOT}/.venv/bin/pip" install -e "${REPO_ROOT}" --quiet
  ok "installed"
fi
GATE_BIN="${REPO_ROOT}/.venv/bin/magi-cp-gate"
[ -x "$GATE_BIN" ] || fail "magi-cp-gate not found at $GATE_BIN — pip install failed?"

# ── 4. Shim binary ───────────────────────────────────────────────
mkdir -p "$HOME/.local/bin"
SHIM="$HOME/.local/bin/magi-cp-gate.sh"
cat > "$SHIM" <<EOF
#!/usr/bin/env bash
# magi-cp PreToolUse shim — sources env then execs the Python entry point.
[ -f "\$HOME/.config/magi-cp/env" ] && . "\$HOME/.config/magi-cp/env"
exec "${GATE_BIN}" "\$@"
EOF
chmod +x "$SHIM"
ok "shim installed at $SHIM"

# ── 5. Persist env ───────────────────────────────────────────────
mkdir -p "$HOME/.config/magi-cp"
cat > "$HOME/.config/magi-cp/env" <<EOF
export MAGI_CP_API_KEY="${TEST_KEY}"
export MAGI_CP_CLOUD_URL="http://127.0.0.1:${CLOUD_PORT}"
EOF
ok "env persisted at ~/.config/magi-cp/env"

# ── 6. managed-settings.json (Claude Code wiring) ────────────────
mkdir -p "$HOME/.claude"
TARGET_SETTINGS="$HOME/.claude/managed-settings.json"
BACKUP=""
if [ -f "$TARGET_SETTINGS" ] && ! grep -q "magi-cp-gate" "$TARGET_SETTINGS" 2>/dev/null; then
  BACKUP="${TARGET_SETTINGS}.before-magi-cp.$(date +%Y%m%d%H%M%S)"
  cp "$TARGET_SETTINGS" "$BACKUP"
  warn "existing managed-settings.json backed up → $BACKUP"
fi
cat > "$TARGET_SETTINGS" <<EOF
{
  "allowManagedHooksOnly": true,
  "permissions": {"defaultMode": "default"},
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{ "type": "command", "command": "${SHIM}" }]
      }
    ]
  }
}
EOF
ok "managed-settings.json wired at $TARGET_SETTINGS"

# ── 7. Summary ───────────────────────────────────────────────────
echo ""
echo "─────────────────────────────────────────────────────────────"
echo "  Local magi-control-plane is ready."
echo "─────────────────────────────────────────────────────────────"
printf "  Cloud:     http://127.0.0.1:%s  (logs: %s/cloud.log)\n" "$CLOUD_PORT" "$LOG_DIR"
printf "  Dashboard: http://127.0.0.1:%s/rules\n" "$DASH_PORT"
printf "  API key:   %s\n" "$TEST_KEY"
echo ""
echo "Next:"
echo ""
echo "  # A — smoke test the inline regex gate"
echo "  bash ${REPO_ROOT}/scripts/local-e2e-smoke.sh"
echo ""
echo "  # B — author a policy through the wizard, then test with Claude Code"
echo "  1. Open http://127.0.0.1:${DASH_PORT}/rules and click \"New policy\""
echo "  2. Walk the wizard. Try condition kind = regex with pattern like"
echo "     \\bSECRET_LEAK\\b on Bash."
echo "  3. In a Claude Code session, ask:"
echo "       \"please run: echo SECRET_LEAK_xyz\""
echo "     The hook should refuse (\"MAGI: …\")."
echo ""
echo "  # C — unset later"
echo "  rm $TARGET_SETTINGS"
[ -n "$BACKUP" ] && echo "  cp $BACKUP $TARGET_SETTINGS  # if you want the old one back"
echo ""
