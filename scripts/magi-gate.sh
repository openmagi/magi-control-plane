#!/usr/bin/env bash
# CC PreToolUse hook shim. Forwards stdin + invokes the Python helper installed
# by `pip install magi-cp`.
#
# PLUGIN-1: resolve the gate binary to an ABSOLUTE path rather than letting the
# shell search $PATH on every hook invocation, so a PATH-shadowing
# `magi-cp-gate` planted by another user on a shared host cannot silently
# bypass enforcement. install-plugin.sh bakes the resolved absolute path into
# the GATE='...' assignment below (replacing the sentinel placeholder). When
# run unsubstituted (dev / manual copy) we search a couple of fixed install
# prefixes first, then fall back to $PATH. We deliberately do NOT read the path
# from an environment variable, since that would itself be attacker-set.
set -euo pipefail

GATE='@MAGI_CP_GATE_BIN@'
if [ "$GATE" = '@MAGI_CP_GATE_BIN@' ]; then
  GATE=''
  for cand in /usr/local/bin/magi-cp-gate "${HOME:-}/.local/bin/magi-cp-gate"; do
    if [ -x "$cand" ]; then GATE="$cand"; break; fi
  done
  if [ -z "$GATE" ]; then
    GATE="$(command -v magi-cp-gate 2>/dev/null || true)"
  fi
fi

if [ -z "$GATE" ] || [ ! -x "$GATE" ]; then
  # Fail visible, not open: emit a block the hook host understands instead of
  # letting the tool call through unchecked.
  echo '{"decision":"block","reason":"magi-cp-gate not found"}'
  exit 0
fi

exec "$GATE" "$@"
