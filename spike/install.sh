#!/usr/bin/env bash
# magi-cp install (M5: cloud-authority model).
#   - magi-gate.sh   → /usr/local/bin (root-owned, user 못 수정)
#   - local_gate.py  → /usr/local/share/magi-cp/ (helper; cryptography는 system python)
#   - managed-settings.json → macOS managed path
# /var/magi/evidence는 더이상 사용 안 함 (cloud-signed 토큰 = ~/.magi-cp/local/wal.jsonl).
set -euo pipefail
[ "$(id -u)" = "0" ] || { echo "run with sudo"; exit 1; }

HERE="$(cd "$(dirname "$0")" && pwd)"
SHARE="/usr/local/share/magi-cp"
MANAGED_DIR="/Library/Application Support/ClaudeCode"

install -m 0755 -o root "$HERE/magi-gate.sh" /usr/local/bin/magi-gate.sh
mkdir -p "$SHARE"
install -m 0644 -o root "$HERE/local_gate.py" "$SHARE/local_gate.py"
mkdir -p "$MANAGED_DIR"
install -m 0644 -o root "$HERE/managed-settings.json" "$MANAGED_DIR/managed-settings.json"

# 이전 spike 잔존물 정리 (대칭 HMAC 시절 흔적)
rm -f /var/magi/secret.key /var/magi/evidence/filing.token 2>/dev/null || true

echo "installed:"
echo "  /usr/local/bin/magi-gate.sh                                (root:0755)"
echo "  /usr/local/share/magi-cp/local_gate.py                     (root:0644)"
echo "  $MANAGED_DIR/managed-settings.json (root:0644)"
echo
echo "NEXT:"
echo "  1) 별도 터미널: cd \$(dirname $0) && python3 cloud_signer.py serve"
echo "  2) 새 claude 세션 시작 (managed-settings 로드)"
echo "  3) 데모: 본 디렉토리의 M5-DEMO.md 참조"
