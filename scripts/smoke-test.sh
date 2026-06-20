#!/usr/bin/env bash
# magi-control-plane install smoke test.
#
# Confirms that:
#   1) magi-gate.sh is on PATH (or where managed-settings says)
#   2) magi-cp-gate Python CLI is on PATH
#   3) MAGI_CP_API_KEY + MAGI_CP_CLOUD_URL are set
#   4) The cloud is reachable + accepts the key
#   5) The gate evaluates a synthetic PreToolUse JSON correctly:
#      - non-sentinel command → ALLOW (exit 0, no JSON output)
#      - sentinel command w/ no signed token in WAL → DENY (JSON on stdout)
#
# Exit non-zero on any failure with a clear diagnostic.

set -euo pipefail

red()   { printf "\033[1;31m%s\033[0m\n" "$*" >&2; }
green() { printf "\033[1;32m%s\033[0m\n" "$*"; }
yel()   { printf "\033[1;33m%s\033[0m\n" "$*"; }

[ -f "$HOME/.config/magi-cp/env" ] && set -a && . "$HOME/.config/magi-cp/env" && set +a

API_KEY="${MAGI_CP_API_KEY:-}"
CLOUD_URL="${MAGI_CP_CLOUD_URL:-https://cloud.openmagi.ai}"

if [ -z "$API_KEY" ]; then
  red "FAIL: MAGI_CP_API_KEY not set"
  exit 1
fi

# Step 1 — gate shim discoverable.
GATE_PATH=""
for cand in "$HOME/.local/bin/magi-gate.sh" "/usr/local/bin/magi-gate.sh"; do
  [ -x "$cand" ] && { GATE_PATH="$cand"; break; }
done
if [ -z "$GATE_PATH" ]; then
  red "FAIL: magi-gate.sh not found in ~/.local/bin or /usr/local/bin"
  exit 2
fi
green "✓ gate shim: $GATE_PATH"

# Step 2 — gate CLI on PATH.
if ! command -v magi-cp-gate >/dev/null 2>&1; then
  red "FAIL: magi-cp-gate CLI not on PATH (pip install --user may need PATH adjustment)"
  exit 3
fi
green "✓ magi-cp-gate on PATH ($(command -v magi-cp-gate))"

# Step 3 — managed-settings.json present.
MS="$HOME/.claude/managed-settings.json"
if [ ! -s "$MS" ]; then
  red "FAIL: $MS missing or empty"
  exit 4
fi
green "✓ managed-settings.json present"

# Step 4 — cloud reachable.
HTTP_CODE=$(curl -s -o /tmp/magi-cp-smoke-tenants -w "%{http_code}" \
  -H "X-Api-Key: $API_KEY" \
  "$CLOUD_URL/tenants/me" || true)
if [ "$HTTP_CODE" != "200" ]; then
  red "FAIL: cloud unreachable or key rejected — HTTP $HTTP_CODE from $CLOUD_URL/tenants/me"
  cat /tmp/magi-cp-smoke-tenants >&2 2>/dev/null || true
  exit 5
fi
green "✓ cloud reachable + key accepted ($CLOUD_URL)"

# Step 5 — synthetic PreToolUse: non-sentinel command.
NON_SENTINEL_PAYLOAD='{"tool_input":{"command":"ls -la /tmp"}}'
OUT=$(printf '%s' "$NON_SENTINEL_PAYLOAD" | "$GATE_PATH" 2>&1 || true)
EXIT=$?
if [ "$EXIT" != "0" ] || [ -n "$OUT" ]; then
  red "FAIL: non-sentinel command should produce no output + exit 0"
  red "  exit=$EXIT  stdout='$OUT'"
  exit 6
fi
green "✓ non-sentinel command → ALLOW (silent)"

# Step 6 — synthetic PreToolUse: sentinel w/ no signed token.
SENTINEL_PAYLOAD='{"tool_input":{"command":"FILE_COURT_smoke_test123"}}'
OUT=$(printf '%s' "$SENTINEL_PAYLOAD" | "$GATE_PATH" 2>&1 || true)
if echo "$OUT" | grep -q '"permissionDecision":"deny"' && \
   echo "$OUT" | grep -q "no signed citation_verify"; then
  green "✓ sentinel w/o signed token → DENY (correct)"
else
  red "FAIL: sentinel command should DENY when WAL has no signed token"
  red "  output: $OUT"
  exit 7
fi

green ""
green "All smoke tests passed."
green "Gate is correctly enforcing PreToolUse policy."
green ""
yel  "Next: restart Claude Code so it reloads ~/.claude/managed-settings.json"
