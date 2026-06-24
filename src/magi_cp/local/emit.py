"""Local CLI: request a citation_verify from cloud and cache the token in WAL.

Run after the MCP verify_citations tool to materialize evidence into WAL so the
hook can find it. In a real CC plugin this is wired as a PostToolUse hook;
here it's an explicit CLI so it's testable in isolation.

PR2: keying renamed from (matter, doc_id) → (subject, payload_hash). The
legacy flags (`--matter`, `--doc-id`) still work as deprecated aliases so
existing scripts / pipelines don't break overnight. New flags
(`--subject`, `--payload-hash`) win when both are supplied.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.request

from ..evidence import Wal


def request_citation_evidence(*, subject: str | None = None,
                              payload_hash: str | None = None,
                              document: str = "",
                              citations: list[dict] | None = None,
                              corpus: dict[str, str] | None = None,
                              cloud_url: str, api_key: str,
                              matter: str | None = None,
                              doc_id: str | None = None) -> dict:
    """Send a citation_verify request to the cloud.

    `subject` / `payload_hash` are the canonical names; `matter` / `doc_id`
    are kept as deprecated aliases so existing call sites keep working
    during the PR2/PR3 transition. New code should pass subject/payload_hash.
    """
    # PR2 review fix: emit a DeprecationWarning whenever the legacy kwargs
    # are used — including the case where the caller ALSO passes the
    # canonical kwarg for a rollback window. Without this signal,
    # transitional callers lose the only cue that --matter / --doc-id are
    # on their way out.
    import warnings as _warnings
    if matter is not None:
        _warnings.warn(
            "request_citation_evidence(matter=…) is deprecated; "
            "use subject=… instead",
            DeprecationWarning, stacklevel=2,
        )
    if doc_id is not None:
        _warnings.warn(
            "request_citation_evidence(doc_id=…) is deprecated; "
            "use payload_hash=… instead",
            DeprecationWarning, stacklevel=2,
        )
    # Resolve effective keys (new wins; fall back to legacy).
    subj = subject if subject is not None else matter
    phash = payload_hash if payload_hash is not None else doc_id
    if subj is None or phash is None:
        raise ValueError(
            "subject (or legacy `matter`) and payload_hash (or legacy "
            "`doc_id`) are required"
        )
    # The cloud /citation_verify endpoint accepts BOTH naming shapes for the
    # duration of PR2; sending both makes the request robust against the
    # cloud rolling forward or back across the transition.
    body = {
        "subject": subj,
        "payload_hash": phash,
        "matter": subj,        # legacy mirror
        "doc_id": phash,       # legacy mirror
        "document": document,
        "citations": citations or [],
        "corpus_override": corpus or None,
    }
    req = urllib.request.Request(
        cloud_url + "/citation_verify",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Api-Key": api_key},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def cli() -> int:
    p = argparse.ArgumentParser(prog="magi-cp-emit")
    # PR2: canonical names. Old --matter/--doc-id kept as deprecated aliases
    # for back-compat. argparse's `required=True` would clash with mutually
    # exclusive groups, so we enforce presence manually below.
    p.add_argument("--subject", default=None,
                   help="generic subject identifier (replaces --matter)")
    p.add_argument("--payload-hash", default=None,
                   help="sha256 of canonical tool payload (replaces --doc-id)")
    p.add_argument("--matter", default=None,
                   help="DEPRECATED alias for --subject (kept for back-compat)")
    p.add_argument("--doc-id", default=None,
                   help="DEPRECATED alias for --payload-hash (kept for back-compat)")
    p.add_argument("--doc-text", default="")
    p.add_argument("--cite", action="append", default=[], help="quote||ref (repeatable)")
    p.add_argument("--corpus", action="append", default=[], help="case_no=text (repeatable)")
    p.add_argument("--cloud-url",
                   default=os.environ.get("MAGI_CP_CLOUD_URL", "http://127.0.0.1:8787"))
    p.add_argument("--api-key", default=os.environ.get("MAGI_CP_API_KEY", ""))
    p.add_argument("--local-dir",
                   default=os.environ.get("MAGI_CP_LOCAL_DIR",
                                          os.path.expanduser("~/.magi-cp/local")))
    args = p.parse_args()

    if not args.api_key:
        print("error: --api-key or MAGI_CP_API_KEY required", file=sys.stderr)
        return 2

    # Resolve canonical → legacy fallback. New wins.
    subject = args.subject if args.subject is not None else args.matter
    payload_hash = (args.payload_hash if args.payload_hash is not None
                    else args.doc_id)
    if subject is None:
        print("error: --subject (or legacy --matter) required",
              file=sys.stderr)
        return 2
    if payload_hash is None:
        print("error: --payload-hash (or legacy --doc-id) required",
              file=sys.stderr)
        return 2
    # Deprecation warning fires whenever the legacy flag is present, even
    # if the operator also passes the canonical flag for a rollback window —
    # otherwise scripts updated to "--subject + --matter" would lose the
    # only signal that --matter is on its way out.
    if args.matter is not None:
        print("warning: --matter is deprecated; use --subject", file=sys.stderr)
    if args.doc_id is not None:
        print("warning: --doc-id is deprecated; use --payload-hash",
              file=sys.stderr)

    citations = []
    for c in args.cite:
        if "||" not in c:
            print("error: --cite must be 'quote||ref'", file=sys.stderr); return 2
        q, r = c.split("||", 1)
        citations.append({"quote": q, "ref": r})

    corpus = {}
    for c in args.corpus:
        if "=" not in c:
            print(f"error: --corpus must be 'case_no=text': {c!r}", file=sys.stderr); return 2
        k, v = c.split("=", 1)
        corpus[k] = v

    import urllib.error
    try:
        res = request_citation_evidence(
            subject=subject, payload_hash=payload_hash,
            document=args.doc_text,
            citations=citations, corpus=corpus,
            cloud_url=args.cloud_url, api_key=args.api_key,
        )
    except urllib.error.HTTPError as e:
        print(f"cloud refused: HTTP {e.code} {e.reason}", file=sys.stderr); return 1
    except urllib.error.URLError as e:
        print(f"cloud unreachable: {e.reason}", file=sys.stderr); return 1
    if res.get("token") and res.get("verdict") == "pass":
        wal = Wal(path=os.path.join(args.local_dir, "wal.jsonl"))
        wal.append({"step": "citation_verify", "token": res["token"],
                    "verdict": res["verdict"]})
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def await_approval_cli() -> int:
    """Poll the cloud HITL endpoint until our hitl_id is approved (token
    issued) or rejected/timeout. On approval, append the cloud-signed token
    to the local WAL so the gate can find it.

    Closes the money-demo loop for the misquote→review→approve path that
    emit() cannot complete on its own (no token is issued at /citation_verify
    time; the token only exists after a human decides).
    """
    p = argparse.ArgumentParser(prog="magi-cp-await-approval")
    p.add_argument("--hitl-id", type=int, required=True)
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--cloud-url",
                   default=os.environ.get("MAGI_CP_CLOUD_URL", "http://127.0.0.1:8787"))
    p.add_argument("--hitl-api-key",
                   default=os.environ.get("MAGI_CP_HITL_API_KEY", ""))
    p.add_argument("--local-dir",
                   default=os.environ.get("MAGI_CP_LOCAL_DIR",
                                          os.path.expanduser("~/.magi-cp/local")))
    args = p.parse_args()
    if not args.hitl_api_key:
        print("error: --hitl-api-key or MAGI_CP_HITL_API_KEY required", file=sys.stderr)
        return 2

    import time
    deadline = time.time() + args.timeout
    import urllib.error
    while time.time() < deadline:
        req = urllib.request.Request(
            args.cloud_url + "/hitl",
            headers={"X-Hitl-Api-Key": args.hitl_api_key},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                items = json.loads(r.read()).get("items", [])
        except urllib.error.URLError as e:
            print(f"cloud unreachable: {e.reason}", file=sys.stderr); return 1
        still_pending = any(i["id"] == args.hitl_id for i in items)
        if not still_pending:
            # Decision was made; pull the most recent ledger entry for this hitl
            led_req = urllib.request.Request(
                args.cloud_url + "/ledger?limit=20&include_body=true",
                headers={"X-Api-Key": os.environ.get("MAGI_CP_API_KEY", "")},
            )
            try:
                with urllib.request.urlopen(led_req, timeout=10) as r:
                    entries = json.loads(r.read()).get("entries", [])
            except urllib.error.HTTPError as e:
                print(f"need MAGI_CP_API_KEY to fetch ledger: {e.code}", file=sys.stderr); return 1
            for e in reversed(entries):
                body = e.get("body", {})
                if body.get("hitl_id") == args.hitl_id and body.get("verdict") == "pass":
                    token = e["token"]
                    Wal(path=os.path.join(args.local_dir, "wal.jsonl")
                        ).append({"step": "citation_verify", "token": token})
                    print(json.dumps({"verdict": "pass", "token": token,
                                       "hitl_id": args.hitl_id}, ensure_ascii=False))
                    return 0
            print(json.dumps({"verdict": "rejected", "hitl_id": args.hitl_id}))
            return 0
        time.sleep(args.interval)
    print(json.dumps({"verdict": "timeout", "hitl_id": args.hitl_id}), file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(cli())
