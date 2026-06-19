#!/usr/bin/env bash
# magi-control-plane plugin installer.
#
# Layout produced:
#   ~/.claude/plugins/magi-control-plane/   ← plugin bundle (overwrites on reinstall)
#   /usr/local/bin/magi-gate.sh             ← hook shim (root, optional sudo)
#   ~/.magi-cp/local/                       ← runtime WAL + cached pubkey
#   /Library/Application Support/ClaudeCode/managed-settings.json (sudo, optional)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && cd .. && pwd)"
USER_PLUGIN_DIR="$HOME/.claude/plugins/magi-control-plane"

# 1) Ensure magi-cp is installed so `magi-cp-gate`, `magi-cp-mcp`, etc. are on PATH.
if ! command -v magi-cp-gate >/dev/null 2>&1; then
  echo "→ pip install -e ${HERE} (so magi-cp-* entry points appear on PATH)"
  pip install -e "$HERE" >/dev/null
fi

# 2) Copy plugin bundle.
echo "→ Installing plugin bundle"
mkdir -p "$USER_PLUGIN_DIR"
cp -R "$HERE/plugin/." "$USER_PLUGIN_DIR/"
echo "   $USER_PLUGIN_DIR/  (fully replaced on each install)"

# 3) Build managed-settings if not present in bundle.
if [ ! -s "$USER_PLUGIN_DIR/managed-settings.json" ]; then
  echo "→ Compiling Policy IR → managed-settings.json"
  python3 -m magi_cp.policy.compiler \
    "$HERE/policies/legal_filing_v1.json" \
    "$USER_PLUGIN_DIR/managed-settings.json"
fi

# 4) Install gate shim where managed-settings references it.
if [ "${MAGI_CP_MANAGED:-0}" = "1" ] && [ "$(id -u)" = "0" ]; then
  install -m 0755 -o root "$HERE/scripts/magi-gate.sh" /usr/local/bin/magi-gate.sh
  echo "   /usr/local/bin/magi-gate.sh  (root:0755)"
elif [ -w /usr/local/bin ]; then
  install -m 0755 "$HERE/scripts/magi-gate.sh" /usr/local/bin/magi-gate.sh
  echo "   /usr/local/bin/magi-gate.sh"
else
  # User-mode fallback: install under ~/.local/bin and rewrite managed-settings.
  mkdir -p "$HOME/.local/bin"
  install -m 0755 "$HERE/scripts/magi-gate.sh" "$HOME/.local/bin/magi-gate.sh"
  python3 - "$USER_PLUGIN_DIR/managed-settings.json" "$HOME/.local/bin/magi-gate.sh" <<'PY'
import json, sys
p = sys.argv[1]
data = json.load(open(p))
for hooks in data.get("hooks", {}).values():
    for hook_block in hooks:
        for h in hook_block.get("hooks", []):
            if h.get("type") == "command":
                h["command"] = sys.argv[2]
open(p, "w").write(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
PY
  echo "   $HOME/.local/bin/magi-gate.sh  (and managed-settings rewritten to it)"
fi

# 5) Optional org-wide managed-settings (macOS).
if [ "${MAGI_CP_MANAGED:-0}" = "1" ] && [ "$(uname -s)" = "Darwin" ] && [ "$(id -u)" = "0" ]; then
  MANAGED_DIR="/Library/Application Support/ClaudeCode"
  mkdir -p "$MANAGED_DIR"
  install -m 0644 -o root "$USER_PLUGIN_DIR/managed-settings.json" \
    "$MANAGED_DIR/managed-settings.json"
  echo "   $MANAGED_DIR/managed-settings.json  (root:0644 — user cannot disable)"
fi

# 6) Runtime dir.
mkdir -p "${MAGI_CP_LOCAL_DIR:-$HOME/.magi-cp/local}"

cat <<EOF

→ Done.

Next:
  1) Start the cloud:           docker compose up -d cloud   (or: make cloud-dev)
  2) Export envs:               export MAGI_CP_CLOUD_URL=http://127.0.0.1:8787
                                export MAGI_CP_API_KEY=...
  3) Restart Claude Code to load the plugin + managed-settings.

To enforce on every user of this Mac (cannot be disabled), re-run as:
   sudo MAGI_CP_MANAGED=1 ./scripts/install-plugin.sh
EOF
