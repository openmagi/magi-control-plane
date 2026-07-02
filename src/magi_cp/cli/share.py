"""``magi-cp share <run>`` - turn a Claude Code run into a public share link.

Locates the run's Claude Code transcript (``~/.claude/projects/<cwd>/<run>.jsonl``),
builds the ``openmagi.runView.v1`` view, redacts it (allowlist fail-closed), and
uploads it to the cloud, which mints a token and returns the public URL.

Default private: nothing is shared unless the user runs this command. Redaction
is best-effort (see ``share.redaction`` residuals), so the URL is printed with a
"review before sharing publicly" note.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ..share.claude_code_view import transcript_to_run_view
from ..share.redaction import build_public_run_view

_DEFAULT_PROJECTS_DIR = "~/.claude/projects"
_DEFAULT_CLOUD_URL = "http://127.0.0.1:8787"


def find_transcript(run: str, *, projects_dir: str | os.PathLike[str]) -> Path | None:
    """Resolve a run reference to a transcript file.

    ``run`` may be a direct path to a ``.jsonl`` file, or a Claude Code
    sessionId whose transcript lives at ``<projects_dir>/*/<run>.jsonl``. When
    several match (the same session under different cwds), the newest wins.
    """
    direct = Path(run).expanduser()
    if direct.is_file():
        return direct
    base = Path(projects_dir).expanduser()
    if not base.is_dir():
        return None
    matches = sorted(
        base.glob(f"*/{run}.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def load_events(path: Path) -> list[dict]:
    """Parse a transcript JSONL into events. Defensive: skips bad lines."""
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


_LEDGER_DIR = "~/.magi-cp/source-checks"


def load_source_ledger(session_id: str) -> list[dict]:
    """Read the control plane's evidence ledger for a session (best-effort).

    Written by an audit policy (PostToolUse) at
    ``~/.magi-cp/source-checks/<sessionId>.jsonl``. Missing file -> empty.
    """
    path = Path(_LEDGER_DIR).expanduser() / f"{session_id}.jsonl"
    if not path.is_file():
        return []
    out: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    except OSError:
        return []
    return out


def build_redacted_view(run: str, *, projects_dir: str | os.PathLike[str]) -> dict:
    """Locate, build, and redact a run's public view. Raises FileNotFoundError."""
    path = find_transcript(run, projects_dir=projects_dir)
    if path is None:
        raise FileNotFoundError(f"no Claude Code transcript found for run {run!r}")
    # The session id is the transcript filename stem.
    ledger = load_source_ledger(path.stem)
    view = transcript_to_run_view(load_events(path), source_ledger=ledger)
    return build_public_run_view(view)


def upload(view: dict, *, cloud_url: str, api_key: str, timeout: int = 15) -> dict:
    """POST the redacted view to the cloud; return the parsed ``{token, url}``."""
    req = urllib.request.Request(
        cloud_url.rstrip("/") + "/v1/runs/share",
        data=json.dumps({"view": view}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Api-Key": api_key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def cli(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="magi-cp share", description=__doc__)
    p.add_argument("run", help="Claude Code sessionId or a path to a transcript .jsonl")
    p.add_argument(
        "--projects-dir",
        default=os.environ.get("CLAUDE_PROJECTS_DIR", _DEFAULT_PROJECTS_DIR),
        help="Claude Code projects dir (default ~/.claude/projects)",
    )
    p.add_argument(
        "--cloud-url",
        default=os.environ.get("MAGI_CP_CLOUD_URL", _DEFAULT_CLOUD_URL),
    )
    p.add_argument("--api-key", default=os.environ.get("MAGI_CP_API_KEY", ""))
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="build + redact the view and print it; do not upload",
    )
    p.add_argument(
        "--allow-plain-http",
        action="store_true",
        help="permit plain http:// to a non-loopback --cloud-url "
             "(the tenant key + transcript would travel in cleartext)",
    )
    args = p.parse_args(argv)

    try:
        view = build_redacted_view(args.run, projects_dir=args.projects_dir)
    except (FileNotFoundError, OSError, UnicodeDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(json.dumps(view, ensure_ascii=False, indent=2))
        return 0

    if not args.api_key:
        print("error: --api-key or MAGI_CP_API_KEY required", file=sys.stderr)
        return 2

    parsed = urllib.parse.urlsplit(args.cloud_url)
    if parsed.scheme not in ("http", "https"):
        print(f"error: --cloud-url must be http(s), got {args.cloud_url!r}", file=sys.stderr)
        return 2
    # TRANSIT-1: the upload carries the tenant API key + the redacted
    # transcript. Refuse plain http:// to a non-loopback host so those do not
    # travel in cleartext; loopback (the default dev cloud) stays allowed, and
    # --allow-plain-http is the explicit override.
    _loopback = (parsed.hostname or "") in ("127.0.0.1", "localhost", "::1")
    if parsed.scheme == "http" and not _loopback and not args.allow_plain_http:
        print(
            "error: refusing plain http:// to a non-loopback host "
            f"({args.cloud_url!r}); the tenant key would be sent in cleartext. "
            "Use https, or pass --allow-plain-http to override.",
            file=sys.stderr,
        )
        return 2

    try:
        result = upload(view, cloud_url=args.cloud_url, api_key=args.api_key)
    except urllib.error.HTTPError as exc:
        print(f"error: upload failed ({exc.code})", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"error: cannot reach {args.cloud_url}: {exc.reason}", file=sys.stderr)
        return 1
    except ValueError:
        print("error: malformed server response", file=sys.stderr)
        return 1

    url = result.get("url") or result.get("token") if isinstance(result, dict) else None
    if not url:
        print("error: malformed server response (no url/token)", file=sys.stderr)
        return 1
    # URL on stdout (pipeable); human-facing framing on stderr.
    print("Share this run:", file=sys.stderr)
    print(url)
    print(
        "  Redaction is best-effort. Review the page before sharing publicly.",
        file=sys.stderr,
    )
    return 0
