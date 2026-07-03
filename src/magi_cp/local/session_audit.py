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
from collections.abc import Mapping

from . import session_evidence
from .session_scope import cwd_in_scope
from ..runtime.cc import CCDriver

from urllib.parse import urlparse

_URL_RE = re.compile(r"https?://[^\s\"'<>)]+")
# Curated official / regulatory registrable domains. Matched on the PARSED
# hostname (exact or subdomain suffix), never a raw substring, so a hostile
# path like `https://evil.blog/sec.gov/x` or a name containing `ir.` cannot
# score a pass. `.gov` is trusted at the TLD level (US government).
_OFFICIAL_DOMAINS = (
    "sec.gov", "ir.tesla.com", "assets-ir.tesla.com",
    "europa.eu", "federalreserve.gov", "treasury.gov",
)
def _first_url(text: str) -> str:
    m = _URL_RE.search(text or "")
    return m.group(0) if m else ""


# Shell operators that chain commands. A chained command decouples the
# aggregate Bash exit code from the fetch and hides which command actually
# ran, so evidence cannot be bound to it (`curl -sf .../404 ; echo ok` would
# otherwise launder a failed fetch into a pass via echo's exit 0).
_SHELL_OPS = frozenset({"&&", "||", "|", ";", "&"})
# curl/wget options that CONSUME the next token as a URL-shaped value. Their
# value is NOT the fetch target, so it must never be mistaken for it
# (`curl -e https://sec.gov https://example.com` fetches example.com but the
# referer is the allowlisted host). We skip the option AND its value.
_VALUE_OPTS = frozenset({
    "-e", "--referer", "-A", "--user-agent", "-x", "--proxy",
    "-b", "--cookie", "-c", "--cookie-jar", "-H", "--header",
    "-d", "--data", "-o", "--output", "-K", "--config",
    "-u", "--user", "--connect-to", "--resolve", "-U", "--proxy-user",
})


def _fetch_url_from_bash(cmd: str) -> str:
    """The URL actually FETCHED by a standalone curl/wget, or "" otherwise.

    Hardened against self-attest forgery:
    - comments stripped (`echo done # curl https://sec.gov` -> "");
    - a chained command (`;`, `&&`, `||`, `|`, `&`) mints nothing, so a failed
      fetch cannot be laundered by a trailing `echo ok` (the aggregate exit is
      no longer curl's);
    - the URL must be a POSITIONAL argument of curl/wget (or the value of
      `--url`), never the value of an option like `-e`/`-A`/`--proxy`, so a
      URL smuggled into a referer/user-agent/proxy flag cannot mint a pass for
      a host the command never fetched.
    """
    import shlex
    try:
        argv = shlex.split(cmd, comments=True)
    except ValueError:
        return ""
    if not argv or any(tok in _SHELL_OPS for tok in argv):
        return ""
    # Locate the fetch program (allow a leading prefix like `timeout 5 curl`).
    start = next(
        (i for i, tok in enumerate(argv)
         if tok.rsplit("/", 1)[-1] in ("curl", "wget")),
        None,
    )
    if start is None:
        return ""
    i = start + 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--url" and i + 1 < len(argv):
            return argv[i + 1] if _URL_RE.fullmatch(argv[i + 1]) else ""
        if tok.startswith("--url="):
            val = tok[len("--url="):]
            return val if _URL_RE.fullmatch(val) else ""
        if tok in _VALUE_OPTS:
            i += 2  # skip the option AND the value it consumes
            continue
        if tok.startswith("-"):
            i += 1  # boolean/unknown flag; `--opt=value` form self-contains
            continue
        if _URL_RE.fullmatch(tok):  # positional == the fetch target
            return tok
        i += 1
    return ""


def extract_subject(how: str, tool_name: str, tool_input: dict) -> str:
    """Pull the URL to judge out of a tool call. ``how='url'`` for now.

    WebFetch is CC's own server-side fetch, so its url is used directly. For
    Bash, the URL must be an argument of a curl/wget word (comments stripped),
    so neither a bare `echo <url>` nor a URL hidden in a `# comment` records
    anything.
    """
    if how != "url" or not isinstance(tool_input, dict):
        return ""
    if tool_name == "WebFetch":
        u = tool_input.get("url")
        return u if isinstance(u, str) else ""
    if tool_name == "Bash":
        return _fetch_url_from_bash(str(tool_input.get("command", "")))
    return ""


def _host_is_official(host: str) -> bool:
    host = (host or "").lower().strip(".")
    if not host:
        return False
    if host == "gov" or host.endswith(".gov"):
        return True
    return any(host == d or host.endswith("." + d) for d in _OFFICIAL_DOMAINS)


def judge_domain_credibility(subject: str) -> tuple[str, str]:
    """Deterministic parsed-hostname judge -> (verdict, detail). Never hedges."""
    host = urlparse(subject or "").hostname or ""
    if _host_is_official(host):
        return "pass", f"CREDIBLE - {host} is a recognized official / regulatory primary source (hostname check)"
    return "fail", f"NOT_CREDIBLE - {host or subject!r} is not a recognized official primary source (hostname check)"


_JUDGES = {"domain-credibility": judge_domain_credibility}


def _response_ok(tool_response: object) -> bool:
    """True when the tool call actually returned a usable result.

    Binds evidence to a real outcome, not just the request: an errored /
    empty WebFetch (403) or a failed Bash never mints a pass. Lenient about
    shape (CC's tool_response varies) but treats an explicit error or an
    empty body as failure. A missing tool_response (event carried none) is
    allowed through so PreToolUse-less test payloads still exercise the judge.
    """
    if tool_response is None:
        return True
    if isinstance(tool_response, Mapping):
        if tool_response.get("is_error") or tool_response.get("error"):
            return False
        # Bash-style: a non-zero exit code is a failure.
        code = tool_response.get("exit_code")
        if isinstance(code, int) and code != 0:
            return False
        body = (
            tool_response.get("content")
            or tool_response.get("output")
            or tool_response.get("stdout")
            or tool_response.get("result")
        )
        if body is None:
            return True  # shape we don't recognize -> don't over-reject
        return bool(str(body).strip())
    return bool(str(tool_response).strip())


def cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="magi-cp-session-audit")
    p.add_argument("--kind", required=True, help="evidence kind to record under")
    p.add_argument("--extract", default="url", help="how to pull the subject (url)")
    p.add_argument("--judge", default="domain-credibility", choices=sorted(_JUDGES))
    p.add_argument("--cwd-prefix", default="",
                   help="only record when the session cwd is inside this dir (empty=global)")
    args = p.parse_args(argv)

    try:
        raw = sys.stdin.buffer.read()
    except (OSError, ValueError):
        return 0
    try:
        event = CCDriver().parse_hook_payload(raw)
    except (ValueError, UnicodeDecodeError):
        return 0
    if not cwd_in_scope(event.cwd, args.cwd_prefix):
        return 0  # out of the policy's project scope
    session_id = event.session_id
    tool_use_id = event.raw.get("tool_use_id")

    subject = extract_subject(args.extract, event.tool_name, event.tool_input)
    if not subject:
        return 0  # nothing to judge on this call
    if not _response_ok(event.raw.get("tool_response")):
        return 0  # the fetch did not succeed (403 / empty / error) -> no evidence

    verdict, detail = _JUDGES[args.judge](subject)
    session_evidence.record(
        session_id, args.kind,
        subject=subject, verdict=verdict, detail=detail,
        tool_use_id=tool_use_id if isinstance(tool_use_id, str) else None,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(cli())
