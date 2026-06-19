#!/usr/bin/env bash
# Shim: CC PreToolUse hook entry point. Forwards stdin + invokes the Python
# helper installed by `pip install magi-cp` (resolved on PATH).
exec magi-cp-gate "$@"
