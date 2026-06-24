"""Local CLI: request a citation_verify from cloud and cache the token in WAL.

Run after the MCP verify_citations tool to materialize evidence into WAL so the
hook can find it. In a real CC plugin this is wired as a PostToolUse hook;
here it's an explicit CLI so it's testable in isolation.

PR4: legacy `--matter` / `--doc-id` aliases removed. Both flags are still
recognised by argparse so we can return a clear "deprecated, use ..." error
message instead of argparse's generic "unrecognised arguments"; passing
either is a hard exit-2 failure (no silent acceptance, no warning-and-
proceed). Likewise, the `request_citation_evidence()` helper no longer
accepts `matter` / `doc_id` kwargs.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.request

from ..evidence import Wal


def request_citation_evidence(*, subject: str,
                              payload_hash: str,
                              document: str = "",
                              citations: list[dict] | None = None,
                              corpus: dict[str, str] | None = None,
                              cloud_url: str, api_key: str) -> dict:
    """Send a citation_verify request to the cloud.

    PR4: only the canonical (`subject`, `payload_hash`) kwargs are
    accepted. Callers passing the legacy `matter` / `doc_id` kwargs hit
    a TypeError at the Python boundary (intentional — there's no silent
    aliasing left).
    """
    if subject is None or payload_hash is None:
        raise ValueError(
            "subject and payload_hash are required"
        )
    body = {
        "subject": subject,
        "payload_hash": payload_hash,
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
    p.add_argument("--subject", default=None,
                   help="generic subject identifier")
    p.add_argument("--payload-hash", default=None,
                   help="sha256 of canonical tool payload")
    # PR4: legacy flags accepted only so we can return a clean error.
    # Both will hit a hard exit-2 with a clear "deprecated" message
    # rather than argparse's generic "unrecognised arguments".
    p.add_argument("--matter", default=None,
                   help=argparse.SUPPRESS)
    p.add_argument("--doc-id", default=None,
                   help=argparse.SUPPRESS)
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

    # PR4: legacy flags are a hard failure with a remediation hint.
    if args.matter is not None:
        print("error: --matter is deprecated and removed in PR4; use --subject",
              file=sys.stderr)
        return 2
    if args.doc_id is not None:
        print("error: --doc-id is deprecated and removed in PR4; use --payload-hash",
              file=sys.stderr)
        return 2

    if args.subject is None:
        print("error: --subject required", file=sys.stderr)
        return 2
    if args.payload_hash is None:
        print("error: --payload-hash required", file=sys.stderr)
        return 2

    citations = []
    for c in args.cite:
        if "||" not in c:
            print("error: --cite must be 'quote||ref'", file=sys.stderr)
            return 2
        q, r = c.split("||", 1)
        citations.append({"quote": q, "ref": r})

    corpus = {}
    for c in args.corpus:
        if "=" not in c:
            print(f"error: --corpus must be 'case_no=text': {c!r}", file=sys.stderr)
            return 2
        k, v = c.split("=", 1)
        corpus[k] = v

    import urllib.error
    try:
        res = request_citation_evidence(
            subject=args.subject, payload_hash=args.payload_hash,
            document=args.doc_text,
            citations=citations, corpus=corpus,
            cloud_url=args.cloud_url, api_key=args.api_key,
        )
    except urllib.error.HTTPError as e:
        print(f"cloud refused: HTTP {e.code} {e.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"cloud unreachable: {e.reason}", file=sys.stderr)
        return 1
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
            print(f"cloud unreachable: {e.reason}", file=sys.stderr)
            return 1
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
                print(f"need MAGI_CP_API_KEY to fetch ledger: {e.code}", file=sys.stderr)
                return 1
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
