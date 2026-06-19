#!/usr/bin/env python3
"""
magi-control-plane M4 — Cloud signer/ledger (실제 분리된 서버 프로세스).

스코프(최소):
  - Ed25519 keypair: private는 *이 프로세스만* 안다 (PRIV_PATH, 0600). public은 공개.
  - POST /events    raw tool_response 받아 → 정책 평가(verify_citations) → 서명 토큰 발행
  - GET  /pubkey    로컬 gate가 verify용으로 가져감
  - GET  /ledger    감사(외부 read-only, 변조 탐지용 hash-chain)
  - 짧은 만료(ttl)  — 만료=fail-closed (라이선스 만료=같은 산출물, §8.5)

로컬과의 분리(F1):
  - private key 파일은 서버측 디렉토리에만 존재. 로컬 gate는 pubkey만 fetch.
  - 토큰 = base64(payload) + "." + base64(signature)  (JWS-like, 단순화)

용법:
  python3 cloud_signer.py serve [--port 8787]   # 서버
  python3 cloud_signer.py keygen                # private/public 생성
"""
from __future__ import annotations
import base64, hashlib, json, os, sys, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature

# 시뮬레이션에서 verifier 코어 재사용 (실제 클라우드면 별도 서비스로 분리)
from verify_citations import Citation, verify_document

CLOUD_DIR  = os.path.expanduser("~/.magi-cp/cloud")
PRIV_PATH  = os.path.join(CLOUD_DIR, "ed25519_private.pem")
PUB_PATH   = os.path.join(CLOUD_DIR, "ed25519_public.pem")
LEDGER     = os.path.join(CLOUD_DIR, "ledger.jsonl")
TOKEN_TTL  = 600   # seconds; 짧은 만료 → 라이선스/정책 만료 모델

def b64u(b: bytes) -> str: return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
def doc_hash(text: str) -> str: return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

# ── keygen ───────────────────────────────────────────────────────────
def keygen():
    os.makedirs(CLOUD_DIR, exist_ok=True)
    priv = Ed25519PrivateKey.generate()
    open(PRIV_PATH, "wb").write(priv.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    os.chmod(PRIV_PATH, 0o600)
    open(PUB_PATH, "wb").write(priv.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    os.chmod(PUB_PATH, 0o644)
    print(f"wrote {PRIV_PATH} (0600, 클라우드만 접근)")
    print(f"wrote {PUB_PATH} (0644, 로컬 gate가 fetch)")

def load_priv() -> Ed25519PrivateKey:
    return serialization.load_pem_private_key(open(PRIV_PATH, "rb").read(), password=None)

# ── 정책 평가 + 서명 토큰 발행 ───────────────────────────────────────
def sign_token(body: dict, priv: Ed25519PrivateKey) -> str:
    payload = json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
    sig = priv.sign(payload)
    return f"{b64u(payload)}.{b64u(sig)}"

def append_ledger(token: str, body: dict):
    os.makedirs(CLOUD_DIR, exist_ok=True)
    prev = ""
    if os.path.exists(LEDGER):
        with open(LEDGER, "rb") as f:
            f.seek(0, 2); size = f.tell()
            if size > 0:
                f.seek(max(0, size - 4096)); tail = f.read().decode().splitlines()
                if tail: prev = json.loads(tail[-1]).get("h", "")
    entry = {"prev": prev, "body": body, "token": token, "ts": int(time.time())}
    entry["h"] = hashlib.sha256((prev + token).encode()).hexdigest()
    with open(LEDGER, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry

def issue_citation_evidence(req: dict) -> dict:
    """req: {matter, document, citations:[{quote,ref}], resolver_corpus:{case_no:text}}"""
    corpus = req.get("resolver_corpus", {})
    resolver = lambda cn: corpus.get(cn)
    cites = [Citation(**c) for c in req.get("citations", [])]
    v = verify_document(cites, resolver=resolver)
    now = int(time.time())
    # doc_id가 명시되면 그걸 doc_hash로 직접 박음(CC sentinel용); 없으면 내용 해시
    dh = req.get("doc_id") or doc_hash(req["document"])
    body = {
        "step": "citation_verify",
        "matter": req["matter"],
        "doc_hash": dh,
        "verdict": v.verdict,            # ok? — verify_document.verdict returns 'pass'/'review'/'deny'
        "iat": now, "exp": now + TOKEN_TTL,
        "issuer": "magi-cloud-dev",
    }
    if v.verdict == "pass":
        token = sign_token(body, load_priv())
        append_ledger(token, body)
        return {"verdict": body["verdict"], "token": token, "exp": body["exp"]}
    # review/deny → 토큰 발행 안 함 (gate가 자동 deny)
    return {"verdict": v.verdict, "token": None,
            "reasons": [r for cv in v.verdicts for r in cv.reasons]}

# ── HTTP server ──────────────────────────────────────────────────────
class H(BaseHTTPRequestHandler):
    def _json(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a, **k): pass
    def do_GET(self):
        if self.path == "/pubkey":
            self._json(200, {"pubkey_pem": open(PUB_PATH).read()})
        elif self.path == "/ledger":
            entries = [json.loads(l) for l in open(LEDGER)] if os.path.exists(LEDGER) else []
            self._json(200, {"entries": entries})
        else:
            self._json(404, {"error":"not found"})
    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        req = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/citation_verify":
            self._json(200, issue_citation_evidence(req))
        else:
            self._json(404, {"error":"not found"})

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    if sys.argv[1] == "keygen":
        keygen()
    elif sys.argv[1] == "serve":
        port = int(sys.argv[sys.argv.index("--port")+1]) if "--port" in sys.argv else 8787
        if not os.path.exists(PRIV_PATH): keygen()
        print(f"magi cloud signer on http://127.0.0.1:{port}  (priv={PRIV_PATH}, ledger={LEDGER})")
        HTTPServer(("127.0.0.1", port), H).serve_forever()
