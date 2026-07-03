"""PostToolUse audit: record evidence about the tool calls it matches.

Compiled from an ``EvidenceAuditPolicy``. On each matched call it extracts a
subject (e.g. the URL a WebFetch/Bash retrieved), runs a judge, and writes a
canonical evidence record to the session ledger. Passthrough: it never blocks
(PostToolUse cannot anyway).

Judges are pluggable by name so authoring stays declarative. ``domain-credibility``
is a built-in deterministic judge (official/regulatory/IR domains -> pass); it is
hermetic, so a precondition gate can be tested end to end without a model call.

Productized form of the demo's hand-written ``source-score.py``.
"""
from __future__ import annotations

import argparse
import re
import sys

from . import session_evidence
from ..runtime.cc import CCDriver

_URL_RE = re.compile(r"https?://[^\s\"'<>)]+")
_OFFICIAL_HINTS = (
    ".gov/", "sec.gov", "edgar", "investor.", "assets-ir.", "ir.",
    ".europa.eu", ".govt.", "federalreserve", "treasury.gov",
)


def _first_url(text: str) -> str:
    m = _URL_RE.search(text or "")
    return m.group(0) if m else ""


def extract_subject(how: str, tool_name: str, tool_input: dict) -> str:
    """Pull the subject to judge out of a tool call. ``how='url'`` for now."""
    if how != "url" or not isinstance(tool_input, dict):
        return ""
    if tool_name == "WebFetch":
        u = tool_input.get("url")
        return u if isinstance(u, str) else ""
    # Bash / anything with a command string -> first URL in it.
    return _first_url(str(tool_input.get("command", "")))


def judge_domain_credibility(subject: str) -> tuple[str, str]:
    """Deterministic domain judge -> (verdict, detail). Never hedges."""
    u = (subject or "").lower()
    if any(h in u for h in _OFFICIAL_HINTS):
        return "pass", "CREDIBLE - recognized official primary / regulatory source (domain check)"
    return "fail", "NOT_CREDIBLE - not a recognized official primary source (domain check)"


_JUDGES = {"domain-credibility": judge_domain_credibility}


def cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="magi-cp-session-audit")
    p.add_argument("--kind", required=True, help="evidence kind to record under")
    p.add_argument("--extract", default="url", help="how to pull the subject (url)")
    p.add_argument("--judge", default="domain-credibility", choices=sorted(_JUDGES))
    args = p.parse_args(argv)

    try:
        raw = sys.stdin.buffer.read()
    except (OSError, ValueError):
        return 0
    try:
        event = CCDriver().parse_hook_payload(raw)
    except (ValueError, UnicodeDecodeError):
        return 0
    session_id = event.session_id
    tool_use_id = event.raw.get("tool_use_id")

    subject = extract_subject(args.extract, event.tool_name, event.tool_input)
    if not subject:
        return 0  # nothing to judge on this call

    verdict, detail = _JUDGES[args.judge](subject)
    session_evidence.record(
        session_id, args.kind,
        subject=subject, verdict=verdict, detail=detail,
        tool_use_id=tool_use_id if isinstance(tool_use_id, str) else None,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(cli())
