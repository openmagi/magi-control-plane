#!/usr/bin/env python3
"""
magi-control-plane M4 — Local gate + WAL cache (분리된 로컬 프로세스).

로컬은:
  - **public key만** fetch (private 접근 0). F1의 답.
  - 클라우드가 발행한 서명 토큰을 받아 WAL에 캐시.
  - file_court 게이트는 캐시 토큰의 서명·doc_hash·만료를 *결정론* 검증.
  - 클라우드와 분리 = 토큰 위조/대칭키 모순 제거.
"""
from __future__ import annotations
import base64, hashlib, json, os, time, urllib.request
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature

LOCAL_DIR = os.path.expanduser("~/.magi-cp/local")
WAL_PATH  = os.path.join(LOCAL_DIR, "wal.jsonl")
PUB_CACHE = os.path.join(LOCAL_DIR, "pubkey.pem")
CLOUD_URL = "http://127.0.0.1:8787"

def b64u_d(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)
def doc_hash(text: str) -> str: return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(CLOUD_URL + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def fetch_pubkey() -> Ed25519PublicKey:
    """클라우드에서 public key fetch + 로컬 캐시."""
    os.makedirs(LOCAL_DIR, exist_ok=True)
    if not os.path.exists(PUB_CACHE):
        with urllib.request.urlopen(CLOUD_URL + "/pubkey", timeout=10) as r:
            pem = json.loads(r.read())["pubkey_pem"]
        open(PUB_CACHE, "w").write(pem)
    return serialization.load_pem_public_key(open(PUB_CACHE, "rb").read())

# ── 클라우드 호출: 증거 발행 (verify→sign→token) ─────────────────────
def request_citation_evidence(matter: str, document: str, citations: list[dict],
                              resolver_corpus: dict[str,str]) -> dict:
    res = _post("/citation_verify", {
        "matter": matter, "document": document,
        "citations": citations, "resolver_corpus": resolver_corpus,
    })
    if res.get("token"):
        # WAL append (로컬 캐시)
        os.makedirs(LOCAL_DIR, exist_ok=True)
        with open(WAL_PATH, "a") as f:
            f.write(json.dumps({"step": "citation_verify", "token": res["token"]},
                               ensure_ascii=False) + "\n")
    return res

# ── 토큰 검증 (public key, 결정론) ───────────────────────────────────
def verify_token(token: str, pub: Ed25519PublicKey) -> dict | None:
    try:
        payload_b64, sig_b64 = token.split(".")
        payload = b64u_d(payload_b64); sig = b64u_d(sig_b64)
        pub.verify(sig, payload)
        body = json.loads(payload)
        if body.get("exp", 0) < int(time.time()):
            return None  # 만료 = fail-closed
        return body
    except (InvalidSignature, ValueError):
        return None

def load_wal() -> list[dict]:
    if not os.path.exists(WAL_PATH): return []
    return [json.loads(l) for l in open(WAL_PATH)]

# ── PreToolUse gate: file_court ──────────────────────────────────────
def file_court_gate(matter: str, document: str, pub: Ed25519PublicKey) -> tuple[bool, str]:
    dh = doc_hash(document)
    for entry in load_wal():
        if entry.get("step") != "citation_verify": continue
        body = verify_token(entry["token"], pub)
        if not body: continue                                   # tamper/만료 → 무시
        if (body.get("matter") == matter and body.get("doc_hash") == dh
                and body.get("verdict") == "pass"):
            return True, f"ALLOW (cloud-signed citation_verify, exp@{body['exp']})"
    return False, f"DENY (이 문서[{dh}] 매칭 토큰 없음)"

# ── 테스트 hook: WAL clear ───────────────────────────────────────────
def wal_reset():
    if os.path.exists(WAL_PATH): os.remove(WAL_PATH)
    if os.path.exists(PUB_CACHE): os.remove(PUB_CACHE)

# ── matter+doc_id 기반 gate (CC hook용; doc text 없이 식별자로 매칭) ─
def file_court_gate_by_id(matter: str, doc_id: str, pub: Ed25519PublicKey) -> tuple[bool, str]:
    for entry in load_wal():
        if entry.get("step") != "citation_verify": continue
        body = verify_token(entry["token"], pub)
        if not body: continue
        if (body.get("matter") == matter and body.get("doc_hash") == doc_id
                and body.get("verdict") == "pass"):
            return True, f"ALLOW (cloud-signed, exp@{body['exp']})"
    return False, f"DENY (matter={matter} doc={doc_id}: 매칭 토큰 없음)"


def _emit_deny(reason: str) -> None:
    """CC PreToolUse 응답 — permissionDecision deny + exit 0."""
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": f"MAGI: {reason}",
    }}))
    raise SystemExit(0)


def cli_gate(matter: str, doc_id: str) -> None:
    """magi-gate.sh가 호출. cloud 도달 불가 = fail-closed."""
    try:
        pub = fetch_pubkey()
    except Exception as ex:
        _emit_deny(f"cloud unreachable ({type(ex).__name__})")
    ok, msg = file_court_gate_by_id(matter, doc_id, pub)
    if not ok:
        _emit_deny(msg)
    # ok: 침묵 = 정상 permission flow


def cli_emit(matter: str, doc_id: str, doc_text: str,
             citations: list[dict], corpus: dict[str, str]) -> dict:
    """사용자/스킬이 호출. cloud /citation_verify → WAL append. doc_id를 doc_hash로 박음."""
    import urllib.request as _u
    req = _u.Request(CLOUD_URL + "/citation_verify",
                     data=json.dumps({"matter": matter, "document": doc_text,
                                      "doc_id": doc_id, "citations": citations,
                                      "resolver_corpus": corpus}).encode(),
                     headers={"Content-Type": "application/json"})
    with _u.urlopen(req, timeout=15) as r:
        res = json.loads(r.read())
    if res.get("token"):
        os.makedirs(LOCAL_DIR, exist_ok=True)
        with open(WAL_PATH, "a") as f:
            f.write(json.dumps({"step": "citation_verify", "token": res["token"]},
                               ensure_ascii=False) + "\n")
    return res


if __name__ == "__main__":
    import argparse, sys
    p = argparse.ArgumentParser(prog="magi-cp-local")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gate"); g.add_argument("--matter", required=True); g.add_argument("--doc-id", required=True)
    e = sub.add_parser("emit"); e.add_argument("--matter", required=True); e.add_argument("--doc-id", required=True)
    e.add_argument("--doc-text", default=""); e.add_argument("--cite", action="append", default=[],
                                                              help="quote||ref (반복)")
    e.add_argument("--corpus", action="append", default=[], help="case_no=text (반복)")
    args = p.parse_args()
    if args.cmd == "gate":
        cli_gate(args.matter, args.doc_id)
    else:
        cites = [{"quote": c.split("||",1)[0], "ref": c.split("||",1)[1]} for c in args.cite]
        corpus = dict(c.split("=", 1) for c in args.corpus)
        out = cli_emit(args.matter, args.doc_id, args.doc_text, cites, corpus)
        print(json.dumps(out, ensure_ascii=False, indent=2))
