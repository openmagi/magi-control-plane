#!/usr/bin/env python3
"""
magi-control-plane M3 PoC — end-to-end money demo (단일 프로세스 시뮬레이션).

루프:  [verify_citations] → [trusted producer가 서명한 evidence] → [ledger] → [file_court 게이트]

실제 배선 대응:
  - emit_citation_evidence  ≈ PostToolUse hook (verify 툴의 *실제 출력*을 관측해 서명)
  - file_court_gate         ≈ PreToolUse hook (mcp__court__file 직전, ledger 증거 검사)
  - sign/verify             : PoC=HMAC(단일프로세스). 실제=Ed25519 비대칭(private=cloud/trusted
                              producer, public=gate) — M0 발견 F1: gate-as-user가 위조 없이 검증.
  - doc_hash 바인딩         : "문서 A 검증 후 문서 B 제출"(swap) 차단.
"""
from __future__ import annotations
import hashlib, hmac, json, time
from dataclasses import dataclass, field
from verify_citations import Citation, verify_document

# 모델/유저가 접근 불가한 trusted-producer 키 (실제론 Ed25519 private, cloud-side)
_KEY = b"poc-trusted-producer-key-NOT-reachable-by-model"

def doc_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

def sign(body: dict) -> str:
    return hmac.new(_KEY, json.dumps(body, sort_keys=True, ensure_ascii=False).encode(), hashlib.sha256).hexdigest()


@dataclass
class Document:
    matter: str
    text: str
    citations: list[Citation]

@dataclass
class Ledger:
    tokens: list[dict] = field(default_factory=list)
    def append(self, tok: dict) -> None:        # append-only
        self.tokens.append(tok)
    def find(self, matter: str, step: str) -> list[dict]:
        return [t for t in self.tokens if t["matter"] == matter and t["step"] == step]


def emit_citation_evidence(doc: Document, ledger: Ledger) -> str:
    """신뢰 producer: verify의 *실제* verdict를 관측해 doc에 바인딩된 서명 토큰 기록."""
    v = verify_document(doc.citations)
    body = {"matter": doc.matter, "step": "citation_verify",
            "verdict": v.verdict, "doc_hash": doc_hash(doc.text), "ts": int(time.time())}
    token = {**body, "sig": sign(body)}
    ledger.append(token)
    return v.verdict


def file_court_gate(matter: str, document_text: str, ledger: Ledger) -> tuple[bool, str]:
    """PreToolUse 게이트: 이 문서에 바인딩된 citation_verify=pass 서명토큰이 있어야 ALLOW."""
    dh = doc_hash(document_text)
    for t in ledger.find(matter, "citation_verify"):
        body = {k: t[k] for k in t if k != "sig"}
        if t["sig"] != sign(body):
            continue                                   # tamper → 무시
        if t["verdict"] == "pass" and t["doc_hash"] == dh:
            return True, "ALLOW (검증된 인용 + 문서 바인딩 일치)"
    return False, f"DENY (이 문서[{dh}]에 바인딩된 citation_verify=pass 증거 없음)"


def step(label, ok, msg):
    print(f"  {'✅' if ok else '⛔'} {label}: {msg}")


if __name__ == "__main__":
    led = Ledger()
    # 인용 4종 (verify_citations 데모와 동일 소스)
    valid_a = Citation("공연히 사실을 적시하여 사람의 사회적 평가를 저하시킬 만한 구체적 사실을 드러내는 것",
                       "대법원 2018. 9. 13. 선고 2018도13694 판결")
    valid_b = Citation("통상의 손해를 한도로 하며, 특별한 사정으로 인한 손해는",
                       "대법원 2019. 5. 14. 선고 2019다12345 판결")
    fake    = Citation("인공지능이 작성한 서면은 무효이다", "대법원 2021. 1. 1. 선고 2021도99999 판결")
    misquote= Citation("명예훼손죄는 허위사실인 경우에만 성립한다", "2018도13694")

    dirty = Document("M123", "답변서 v1: ...본문... [가짜+misquote 인용 포함]", [valid_a, fake, misquote])
    clean = Document("M123", "답변서 v2: ...본문... [전부 검증된 인용]", [valid_a, valid_b])

    print("\n=== M3 money demo: 가짜 인용 → 제출 차단 → 수정 → 통과 ===\n")

    print("[1] 어소가 답변서 v1(가짜+misquote 인용)으로 제출 시도")
    vd = emit_citation_evidence(dirty, led)
    step("verify_citations", vd == "pass", f"verdict={vd!r}")
    ok, msg = file_court_gate("M123", dirty.text, led)
    step("file_court 게이트", ok, msg)

    print("\n[2] swap 공격: clean 문서로 증거 만든 뒤 dirty 문서를 제출")
    emit_citation_evidence(clean, led)
    ok, msg = file_court_gate("M123", dirty.text, led)
    step("file_court(dirty, clean 증거로)", ok, msg + " ← doc_hash 불일치로 차단")

    print("\n[3] tamper: 누군가 dirty 토큰의 verdict를 'pass'로 위조")
    led.tokens[0]["verdict"] = "pass"        # 서명은 안 고침
    ok, msg = file_court_gate("M123", dirty.text, led)
    step("file_court(위조 토큰)", ok, "서명 불일치로 무시됨 → " + msg)

    print("\n[4] 어소가 인용 수정 → 답변서 v2(전부 검증)로 재제출")
    vd = emit_citation_evidence(clean, led)
    step("verify_citations", vd == "pass", f"verdict={vd!r}")
    ok, msg = file_court_gate("M123", clean.text, led)
    step("file_court 게이트", ok, msg)

    print("\n[audit] ledger 전체 (서명된 append-only 기록):")
    for t in led.tokens:
        print(f"    {t['step']} matter={t['matter']} doc={t['doc_hash']} verdict={t['verdict']} sig={t['sig'][:12]}…")
    print()
