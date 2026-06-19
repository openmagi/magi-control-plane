#!/usr/bin/env bash
# Build target: Policy IR(들) → managed-settings.json (M6 컴파일러).
# hand-write 금지. 정책 바꾸면 policies/*.json만 수정 후 ./build.sh.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
python3 "$HERE/policy_ir.py" compile "$HERE/policies/legal_filing_v1.json" "$HERE/managed-settings.json" >/dev/null
echo "✅ built $HERE/managed-settings.json from policies/*.json"
sha256sum "$HERE/managed-settings.json" 2>/dev/null || shasum -a 256 "$HERE/managed-settings.json"
