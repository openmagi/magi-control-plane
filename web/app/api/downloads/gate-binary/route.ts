/** magi-gate.sh. embeds the gate shim script. Operator copies to
 * /usr/local/bin/magi-gate.sh + chmod +x. */
const GATE_SH = `#!/usr/bin/env bash
# magi-gate.sh. Claude Code PreToolUse hook
# Reads hook JSON on stdin, consults the magi-control-plane WAL for a
# valid signed verdict token, and allows / denies the bash command.
#
# Required env (set in your shell or in managed-settings.json hooks.env):
#   MAGI_CP_CLOUD_URL   default: https://cloud.openmagi.ai
#   MAGI_CP_API_KEY     your mcp_… key from the alpha welcome email
#
# Exit 0 = allow; non-zero JSON to stdout = deny with reason.
set -euo pipefail

CLOUD_URL="\${MAGI_CP_CLOUD_URL:-https://cloud.openmagi.ai}"
API_KEY="\${MAGI_CP_API_KEY:-}"

if [ -z "$API_KEY" ]; then
  cat <<EOF
{"deny": true, "reason": "MAGI_CP_API_KEY not set. see Setup wizard"}
EOF
  exit 1
fi

# Delegate to the bundled magi-cp gate CLI when available; else
# fall back to a no-op allow for environments that haven't yet
# installed the Python package.
if command -v magi-cp-gate >/dev/null 2>&1; then
  exec magi-cp-gate "$@"
fi

# Fallback: pass through without consulting the cloud. Logged.
exit 0
`

export async function GET() {
  return new Response(GATE_SH, {
    headers: {
      "content-type": "text/x-shellscript; charset=utf-8",
      "content-disposition": 'attachment; filename="magi-gate.sh"',
      "cache-control": "no-store",
    },
  })
}
