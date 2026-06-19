#!/usr/bin/env bash
# magi-cp PreToolUse gate (M5: cloud-authority model).
# 호출되는 시점: CC PreToolUse Bash. sentinel "FILE_COURT_<matter>_<doc_id>" 발견 시
# user-mode python helper(/usr/local/share/magi-cp/local_gate.py)에게 위임.
# helper는 ~/.magi-cp/local/wal.jsonl 의 cloud-signed 토큰을 public-key로 검증.
# private key는 cloud에만, F1 해소.
set -euo pipefail

DEBUG_LOG="/tmp/magi-gate.log"
HELPER="/usr/local/share/magi-cp/local_gate.py"

input="$(cat)"
echo "[$(date '+%H:%M:%S')] gate fired" >> "$DEBUG_LOG"

cmd="$(printf '%s' "$input" | /usr/bin/python3 -c \
  'import sys,json;print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null || true)"

# sentinel: FILE_COURT_<matter>_<doc_id>  (영숫자만)
if [[ "$cmd" =~ FILE_COURT_([A-Za-z0-9]+)_([A-Za-z0-9]+) ]]; then
  matter="${BASH_REMATCH[1]}"
  doc_id="${BASH_REMATCH[2]}"
  echo "[$(date '+%H:%M:%S')] gate consult matter=$matter doc=$doc_id" >> "$DEBUG_LOG"
  # helper는 deny면 JSON+exit 0, allow면 침묵+exit 0
  /usr/bin/python3 "$HELPER" gate --matter "$matter" --doc-id "$doc_id"
  exit $?
fi

# 비-sentinel Bash는 통과
exit 0
