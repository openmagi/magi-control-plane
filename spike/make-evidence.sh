#!/usr/bin/env bash
# magi-cp M0 spike — trusted producer: writes a filing evidence token.
# Run with sudo. Token is root-owned & readable (so the gate-as-user can read it)
# but user-unwritable (forge-resistance via file perms). Simulates the PostToolUse
# signer that, in production, observes a real tool_response and ASYMMETRICALLY signs.
set -euo pipefail

DIR="/var/magi/evidence"
mkdir -p "$DIR"
printf '{"step":"filing","verdict":"pass","matter":"M123","ts":"%s"}\n' "$(date -u +%FT%TZ)" > "$DIR/filing.token"
chmod 644 "$DIR/filing.token"   # root-owned, world-readable, user-unwritable
echo "wrote evidence → $DIR/filing.token (root:644)"
