#!/usr/bin/env bash
# Smoke test the local magi-control-plane wiring without going through
# Claude Code. Hits the cloud surfaces the gate would use:
#
#   1. /healthz                          — cloud is up
#   2. /verifiers (X-Api-Key)             — auth wired
#   3. /verify_inline kind=regex pass     — inline regex evaluator
#   4. /verify_inline kind=regex deny     — same, no-match path
#   5. /verify_inline kind=llm_critic     — preview path (no provider)
#   6. /verify_inline kind=shacl          — preview path (no pyshacl)
#   7. /catalog/evidence-types            — built-in verifier catalog
#   8. /catalog/conditions                — should be empty until you save a policy
#
# Exits non-zero on the first unexpected response so you can run this in
# CI / scripts to catch regressions.

set -euo pipefail

# Pull the env the setup script persisted.
[ -f "$HOME/.config/magi-cp/env" ] || {
  echo "✗ run scripts/local-e2e-setup.sh first" >&2
  exit 1
}
# shellcheck disable=SC1091
. "$HOME/.config/magi-cp/env"

CLOUD="${MAGI_CP_CLOUD_URL:-http://127.0.0.1:8787}"
KEY="${MAGI_CP_API_KEY:?MAGI_CP_API_KEY not set}"
HDR=(-H "X-Api-Key: ${KEY}" -H "Content-Type: application/json")

step() { printf "\033[1;34m→\033[0m %s\n" "$1"; }
ok()   { printf "\033[1;32m✓\033[0m %s\n" "$1"; }
fail() { printf "\033[1;31m✗\033[0m %s\n" "$1" >&2; exit 1; }

assert_eq() {
  local got="$1"
  local want="$2"
  local label="$3"
  if [ "$got" = "$want" ]; then
    ok "${label}: ${got}"
  else
    fail "${label}: expected ${want}, got ${got}"
  fi
}

# 1
step "healthz"
out=$(curl -fsS "${CLOUD}/healthz" | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])')
assert_eq "$out" "ok" "healthz"

# 2
step "verifier catalog (auth required)"
status=$(curl -s -o /dev/null -w "%{http_code}" "${HDR[@]}" "${CLOUD}/verifiers")
assert_eq "$status" "200" "verifier catalog auth"

# 3
step "/verify_inline regex pass"
verdict=$(curl -fsS "${HDR[@]}" "${CLOUD}/verify_inline" -d '{
  "kind": "regex",
  "pattern": "\\bAKIA[A-Z0-9]+",
  "payload": {"text": "leaked AKIA12345 in tool output"}
}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["verdict"])')
assert_eq "$verdict" "pass" "regex pass"

# 4
step "/verify_inline regex deny (no match)"
verdict=$(curl -fsS "${HDR[@]}" "${CLOUD}/verify_inline" -d '{
  "kind": "regex",
  "pattern": "\\bAKIA[A-Z0-9]+",
  "payload": {"text": "nothing suspicious here"}
}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["verdict"])')
assert_eq "$verdict" "deny" "regex deny"

# 5
step "/verify_inline llm_critic preview (no provider)"
verdict=$(curl -fsS "${HDR[@]}" "${CLOUD}/verify_inline" -d '{
  "kind": "llm_critic",
  "criterion": "Is this output professional?",
  "payload": {"text": "hello"}
}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["verdict"])')
assert_eq "$verdict" "review" "llm_critic preview"

# 6
step "/verify_inline shacl preview (no pyshacl)"
verdict=$(curl -fsS "${HDR[@]}" "${CLOUD}/verify_inline" -d '{
  "kind": "shacl",
  "shape_ttl": "@prefix sh: <http://www.w3.org/ns/shacl#> .",
  "payload": {}
}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["verdict"])')
# pyshacl may or may not be installed — accept either preview branch.
case "$verdict" in
  pass|deny|review) ok "shacl ${verdict} (acceptable — pyshacl optional)" ;;
  *) fail "shacl unexpected verdict: ${verdict}" ;;
esac

# 7
step "/catalog/evidence-types — builtin steps surfaced"
count=$(curl -fsS "${HDR[@]}" "${CLOUD}/catalog/evidence-types" | \
  python3 -c 'import sys,json;d=json.load(sys.stdin);print(len([i for i in d["items"] if i["source"]=="builtin"]))')
if [ "$count" -ge 5 ]; then
  ok "evidence-types builtin count: ${count}"
else
  fail "evidence-types builtin count: ${count} (expected ≥5)"
fi

# 8
step "/catalog/conditions baseline (may be 0 until you save a policy)"
count=$(curl -fsS "${HDR[@]}" "${CLOUD}/catalog/conditions" | \
  python3 -c 'import sys,json;print(len(json.load(sys.stdin)["items"]))')
ok "conditions count: ${count}"

echo ""
echo "─────────────────────────────────────────────────────────────"
echo "All smoke checks passed. The cloud surfaces the gate calls"
echo "into are working. Next: drive a real run through Claude Code."
echo "─────────────────────────────────────────────────────────────"
