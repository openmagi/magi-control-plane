#!/usr/bin/env python3
"""
M4 — real client/server separation demo.
M3가 단일프로세스 sim이었다면 M4는 *진짜 분리된 서버/클라이언트*:
  - cloud_signer.py serve가 별도 프로세스로 떠 있어야 함 (private key 소유)
  - 로컬은 public key만 fetch
사전준비:  python3 cloud_signer.py serve   (다른 터미널)
실행:     python3 m4_demo.py
"""
import os, time, urllib.request
import local_gate as lg
from verify_citations import normalize  # only for the demo source text shape

CLOUD = lg.CLOUD_URL

def ping_cloud():
    try:
        urllib.request.urlopen(CLOUD + "/pubkey", timeout=2); return True
    except Exception: return False

def step(label, ok, msg=""):
    print(f"  {'✅' if ok else '⛔'} {label}{': ' + msg if msg else ''}")

if __name__ == "__main__":
    if not ping_cloud():
        print("⚠️  cloud_signer 안 떠 있음. 다른 터미널에서:\n   python3 cloud_signer.py serve\n"); raise SystemExit(1)

    print("\n=== M4 데모: 진짜 분리된 cloud↔local ===\n")
    lg.wal_reset()
    pub = lg.fetch_pubkey()
    print(f"[setup] pubkey fetched, WAL empty\n")

    SRC = "사기죄 등 재산범죄에서 동일한 피해자에 대하여 단일하고 계속된 범의하에 동종의 범행을 일정기간 반복하여 행한 경우에는 각 범행은 통틀어 포괄일죄가 될 수 있다."
    REF = "대법원 2016. 10. 27. 선고 2016도11318 판결"
    resolver_corpus = {"2016도11318": SRC}

    valid_cite = {"quote": SRC, "ref": REF}
    fake_cite  = {"quote": "AI가 작성한 서면은 무효", "ref": "대법원 2099. 1. 1. 선고 2099도99999 판결"}

    dirty_doc = "답변서 v1 (가짜 인용 포함)"
    clean_doc = "답변서 v2 (검증된 인용)"
    M = "M_K1"

    print("[1] dirty 문서(가짜 인용) → 클라우드에 verify 요청")
    res = lg.request_citation_evidence(M, dirty_doc, [valid_cite, fake_cite], resolver_corpus)
    step(f"cloud verdict={res['verdict']!r}", res["verdict"] == "deny", "토큰 미발행")
    ok, msg = lg.file_court_gate(M, dirty_doc, pub)
    step("local file_court_gate(dirty)", not ok, msg)

    print("\n[2] swap: clean으로 verify 받고, dirty 제출 시도")
    lg.request_citation_evidence(M, clean_doc, [valid_cite], resolver_corpus)
    ok, msg = lg.file_court_gate(M, dirty_doc, pub)
    step("gate(dirty, clean 토큰으로)", not ok, msg + "  ← doc_hash 불일치")

    print("\n[3] tamper: WAL의 토큰을 한 글자 바꿈")
    lines = open(lg.WAL_PATH).read().splitlines()
    # base64는 짧은 변형도 verify를 깨야 함
    open(lg.WAL_PATH, "w").write(lines[0].replace("citation_verify", "citation_PWNED") + "\n")
    # 변형: payload 자체 변조 (json key 변경) — 서명 깨짐 검증
    raw = open(lg.WAL_PATH).read()
    open(lg.WAL_PATH, "w").write(raw)
    # 더 결정적: 토큰의 마지막 글자 flip
    line = open(lg.WAL_PATH).read().rstrip()
    import json as _j; d = _j.loads(line)
    t = d["token"]; bad = t[:-1] + ("A" if t[-1] != "A" else "B")
    d["token"] = bad
    open(lg.WAL_PATH, "w").write(_j.dumps(d, ensure_ascii=False) + "\n")
    ok, msg = lg.file_court_gate(M, clean_doc, pub)
    step("gate(tampered token)", not ok, "서명 깨져 무시 → " + msg)

    print("\n[4] 깨끗한 재발행 → file_court 통과")
    lg.wal_reset(); pub = lg.fetch_pubkey()
    lg.request_citation_evidence(M, clean_doc, [valid_cite], resolver_corpus)
    ok, msg = lg.file_court_gate(M, clean_doc, pub)
    step("gate(clean)", ok, msg)

    print("\n[5] 만료 시뮬: TTL 짧게 잡혀 있다면 시간 흐른 뒤 만료 검증 (TTL=600s 기본이라 스킵).")
    print("   → production은 TTL 짧게(예: 60s) + 자동 refresh. 라이선스 만료=정책번들 만료=fail-closed (§8.5).")

    print("\n[audit] cloud ledger (외부 read-only, hash-chain):")
    import urllib.request as _u, json as _j
    entries = _j.loads(_u.urlopen(CLOUD + "/ledger", timeout=5).read())["entries"]
    for e in entries:
        print(f"   h={e['h'][:10]}… prev={(e['prev'] or '∅')[:10]}… verdict={e['body']['verdict']} doc={e['body']['doc_hash']}")
    print()
