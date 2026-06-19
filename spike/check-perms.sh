#!/usr/bin/env bash
# Automatable bypass checks (4,5,8) — run as the NORMAL (non-root) user.
# PASS = the user cannot tamper with gate / managed-settings / evidence.
MANAGED="/Library/Application Support/ClaudeCode/managed-settings.json"
GATE="/usr/local/bin/magi-gate.sh"
TOK="/var/magi/evidence/filing.token"

try() { # desc, command-that-should-FAIL
  if eval "$2" 2>/dev/null; then echo "FAIL  $1 (user could modify!)"; else echo "PASS  $1 (write denied)"; fi
}
echo "== M0 file-permission bypass checks (run as non-root) =="
try "4 hook swap"      "echo 'exit 0' > '$GATE'"
try "5 settings edit"  "echo '{}' > '$MANAGED'"
try "8 token forge"    "mkdir -p /var/magi/evidence && printf 'x\ny\n' > '$TOK'"
echo "(1,2,3,6,7 require launching claude — see README matrix)"
