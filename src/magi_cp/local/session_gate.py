"""PreToolUse precondition gate: deny unless required session evidence exists.

Compiled from an ``EvidencePreconditionPolicy``. On the gated event it reads the
session evidence ledger (written by an audit policy earlier in the run) and, if no
record of the required ``kind`` at the required ``verdict`` is present, emits a
Claude Code deny decision. Otherwise it stays silent and the call falls through to
the normal permission rules (e.g. an ``ask`` rule for human approval).

This is the productized form of the demo's hand-written ``verify-gate.py``: keyed
by ``session_id``, config-driven, not hardcoded to any tool or verdict string.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import session_evidence
from .session_scope import cwd_in_scope
from ..runtime.cc import CCDriver


def _deny(reason: str) -> None:
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }) + "\n")


def cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="magi-cp-session-gate")
    p.add_argument("--require-kind", required=True,
                   help="evidence kind that must be on record for this session")
    p.add_argument("--require-verdict", default="pass",
                   choices=session_evidence.VERDICTS)
    p.add_argument("--reason", default="",
                   help="deny reason shown to the agent when evidence is missing")
    p.add_argument("--cwd-prefix", default="",
                   help="only enforce when the session cwd is inside this dir (empty=global)")
    args = p.parse_args(argv)

    try:
        raw = sys.stdin.buffer.read()
    except (OSError, ValueError):
        return 0
    try:
        event = CCDriver().parse_hook_payload(raw)
        session_id = event.session_id
    except Exception:
        return 0  # cannot parse -> fall through to the permission rules
    if not cwd_in_scope(event.cwd, args.cwd_prefix):
        return 0  # out of the policy's project scope -> not our concern
    if not session_id:
        # Cannot identify the session -> cannot enforce a session precondition.
        # Fail open (the permission rules still apply); the audit/gate pair is a
        # defense in depth, not the only control.
        return 0

    if session_evidence.has(session_id, args.require_kind, verdict=args.require_verdict):
        return 0  # evidence on record -> allow through to the permission rules

    reason = args.reason or (
        f"blocked by policy: this run has no '{args.require_kind}' evidence at "
        f"verdict '{args.require_verdict}'. Produce it earlier in the run, then retry."
    )
    _deny(reason)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(cli())
