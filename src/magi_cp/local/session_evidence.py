"""Session-scoped evidence ledger: a first-class control-plane state primitive.

The gap this closes: the legacy local gate only reads a `(subject, payload_hash)`
sentinel token from the WAL. It cannot answer "did some earlier step in THIS
session produce evidence of kind K?". That cross-step, session-scoped question is
what lets one policy (a gate) depend on what another policy (an audit) recorded
earlier in the run.

This module is that ledger. An `audit` policy writes evidence records here on the
tool calls it matches; a `precondition` gate reads them to decide allow/deny. Both
are keyed by the Claude Code ``session_id`` the hooks receive on stdin.

Records are line-delimited JSON under ``<dir>/<session_id>.jsonl``::

    {"sessionId", "kind", "subject", "verdict", "detail", "toolUseId", "ts"}

``verdict`` is a canonical enum: ``pass`` / ``fail`` / ``review``. The prose reason
lives in ``detail`` (surfaced by the share producer). Pure and defensive: a
malformed record or an unreadable file degrades to "no evidence", never raises.
"""
from __future__ import annotations

import hashlib
import json
import os
import time

__all__ = ["ledger_dir", "record", "entries", "has", "VERDICTS"]

VERDICTS = ("pass", "fail", "review")
_MAX_SUBJECT = 512
_MAX_DETAIL = 2000


def ledger_dir() -> str:
    """Directory holding per-session evidence ledgers (env-overridable).

    Lives under ``~/.magi-cp`` by design: OUT of any agent workspace, so a
    normal run never encounters it. In a governed setup (managed-settings, no
    ``--dangerously-skip-permissions``) a companion policy denies the agent
    read/write to this path, making the audit hook the only writer. Under
    skip-permissions ("fully trusted agent") that guard is off by definition.
    """
    return os.path.expanduser(
        os.environ.get("MAGI_CP_SESSION_EVIDENCE_DIR", "~/.magi-cp/session-evidence")
    )


def _safe_session(session_id: str) -> str:
    """Deterministic, collision-resistant filename stem for a session id.

    A sha256 hex digest: safe as a filename (no path traversal) and, unlike a
    lossy char-substitution, cannot alias two distinct session ids onto one
    ledger (which would leak evidence across sessions).
    """
    return hashlib.sha256((session_id or "unknown").encode("utf-8")).hexdigest()[:32]


def _path(session_id: str) -> str:
    return os.path.join(ledger_dir(), f"{_safe_session(session_id)}.jsonl")


def record(
    session_id: str,
    kind: str,
    *,
    subject: str = "",
    verdict: str = "pass",
    detail: str = "",
    tool_use_id: str | None = None,
    ts: int | None = None,
) -> dict:
    """Append one evidence record to a session's ledger. Returns the record.

    Bounded + canonicalized: unknown verdicts fall back to ``review``; subject /
    detail are length-capped. Best-effort: an unwritable dir is swallowed (the
    audit path must never break the agent), returning the record regardless.
    """
    v = verdict if verdict in VERDICTS else "review"
    rec = {
        "sessionId": session_id,
        "kind": str(kind),
        "subject": str(subject)[:_MAX_SUBJECT],
        "verdict": v,
        "detail": str(detail)[:_MAX_DETAIL],
        "toolUseId": tool_use_id,
        "ts": int(ts if ts is not None else time.time()),
    }
    try:
        os.makedirs(ledger_dir(), exist_ok=True)
        with open(_path(session_id), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return rec


def entries(session_id: str, *, kind: str | None = None) -> list[dict]:
    """All evidence records for a session (optionally filtered by ``kind``)."""
    out: list[dict] = []
    try:
        with open(_path(session_id), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and (kind is None or rec.get("kind") == kind):
                    out.append(rec)
    except OSError:
        return out
    return out


def has(session_id: str, kind: str, *, verdict: str = "pass") -> bool:
    """True if the session has at least one ``kind`` record with ``verdict``.

    The gate primitive: "did an earlier step record kind K at verdict V?".
    """
    for rec in entries(session_id, kind=kind):
        if rec.get("verdict") == verdict:
            return True
    return False
