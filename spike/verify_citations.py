#!/usr/bin/env python3
"""
magi-control-plane M1 PoC — verify_citations (비치헤드 IP).

핵심: 법적 주장에 붙은 인용이 (a) 실제 존재하는 판례인가(사건번호 존재),
(b) 인용한 판시 텍스트가 원문에 verbatim(정규화 후) 존재하는가 를 *결정론적으로* 검증.
→ 존재하지 않는 판례(할루시네이션) 또는 misquote는 결정론 FAIL → file_court 게이트가 deny.

이 PoC는 합성 corpus(LBox 자리)로 동작. 실제 제품:
  - SOURCE_CORPUS → LBox/대법원 MCP fetch (egress 프록시로 승인 도메인만)
  - NLI supports() → advisory 의미검증(v1, 여기선 stub)
  - 결과 verdict → PostToolUse hook이 서명해 evidence ledger에 기록
"""
from __future__ import annotations
import re
import unicodedata
from dataclasses import dataclass, field

# ── 합성 원본 corpus (실제론 LBox fetch). key = 사건번호 ──
SOURCE_CORPUS: dict[str, str] = {
    "2018도13694": (
        "형법 제307조 제1항의 명예훼손죄는 공연히 사실을 적시하여 사람의 사회적 평가를 "
        "저하시킬 만한 구체적 사실을 드러내는 것을 말하고, 적시된 사실이 진실인 경우에도 성립할 수 있다."
    ),
    "2019다12345": (
        "채무불이행으로 인한 손해배상의 범위는 통상의 손해를 한도로 하며, 특별한 사정으로 인한 "
        "손해는 채무자가 그 사정을 알았거나 알 수 있었을 때에 한하여 배상책임이 있다."
    ),
}

# ── 정규화: NFC, 공백 압축, 인용부호/구두점 통일 ──
_QUOTES = {"“": '"', "”": '"', "‘": "'", "’": "'", "«": '"', "»": '"'}
def normalize(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    s = "".join(_QUOTES.get(ch, ch) for ch in s)
    s = re.sub(r"\s+", " ", s)             # 모든 공백 → 단일 스페이스
    s = re.sub(r"\s*([,.·、，。:;])\s*", r"\1", s)  # 구두점 주변 공백 제거(겉모양 차이 흡수)
    s = s.strip().strip('"\'').strip()
    return s

# ── 한국 사건번호 추출: 4자리연도 + 사건부호(한글) + 일련번호 (예: 2018도13694, 2019다12345) ──
CASE_NO = re.compile(r"\b(\d{2,4}[가-힣]{1,3}\d+)\b")  # 2자리(pre-2000)·4자리 연도, 헌가/헌바 등 포함
def extract_case_number(ref: str) -> str | None:
    m = CASE_NO.search(normalize(ref))
    return m.group(1) if m else None

@dataclass
class Citation:
    quote: str          # 인용한 판시 텍스트
    ref: str            # 출처 표기 (예: "대법원 2018. 9. 13. 선고 2018도13694 판결")

@dataclass
class CitationVerdict:
    citation: Citation
    case_number: str | None
    exists: bool                 # 결정론: 사건번호가 corpus에 존재
    verbatim: bool               # 결정론: 인용 텍스트가 원문에 정규화-존재
    supports: bool | None = None # advisory(NLI), v1 — 여기선 None
    reasons: list[str] = field(default_factory=list)
    @property
    def status(self) -> str:
        # B0 인사이트: 하드 블록은 *존재*에만. 존재하나 verbatim 실패 = review(생략/삽입/
        # paraphrase/misquote를 결정론으론 구분 불가 → NLI+사람). 둘 다 OK = ok.
        if not self.exists:
            return "missing"   # 하드 deny — 날조(존재하지 않는 판례)
        if self.verbatim:
            return "ok"        # fast-path: 직접인용 일치
        return "review"        # advisory 에스컬레이션 (NLI + 사람 확인)

# Source resolver: case_number → 원문 텍스트 또는 None(존재X).
# 기본=정적 corpus, 라이브=law.go.kr fetch 콜백. B1-live가 노출한 결함의 해법.
SourceResolver = "callable[[str], str | None]"
def default_resolver(case_no: str) -> str | None:
    return SOURCE_CORPUS.get(case_no)

def verify_citation(c: Citation, resolver=default_resolver) -> CitationVerdict:
    v = CitationVerdict(citation=c, case_number=extract_case_number(c.ref),
                        exists=False, verbatim=False)
    if v.case_number is None:
        v.reasons.append("사건번호 파싱 실패")
        return v
    src = resolver(v.case_number)
    if src is None:
        v.reasons.append(f"존재하지 않는 판례: {v.case_number} (할루시네이션 의심)")
        return v
    v.exists = True
    if normalize(c.quote) in normalize(src):
        v.verbatim = True
    else:
        v.reasons.append("인용 텍스트가 원문과 불일치(misquote)")
    # supports = NLI(c.quote, src)  # v1 advisory
    return v

@dataclass
class DocumentVerdict:
    verdicts: list[CitationVerdict]
    @property
    def verdict(self) -> str:    # 게이트가 읽는 값: deny(하드) / review(HITL) / pass
        if any(v.status == "missing" for v in self.verdicts):
            return "deny"        # 날조 1건이라도 → 결정론 제출 차단
        if any(v.status == "review" for v in self.verdicts):
            return "review"      # 직접인용 verbatim 실패 → 사람/NLI 확인 필요
        return "pass"
    @property
    def hard_blocked(self) -> list[CitationVerdict]:
        return [v for v in self.verdicts if v.status == "missing"]
    @property
    def needs_review(self) -> list[CitationVerdict]:
        return [v for v in self.verdicts if v.status == "review"]

def verify_document(citations: list[Citation], resolver=default_resolver) -> DocumentVerdict:
    return DocumentVerdict([verify_citation(c, resolver) for c in citations])


if __name__ == "__main__":
    cases = {
        "① 유효 인용": Citation(
            quote="공연히 사실을 적시하여 사람의 사회적 평가를 저하시킬 만한 구체적 사실을 드러내는 것",
            ref="대법원 2018. 9. 13. 선고 2018도13694 판결"),
        "② 가짜 판례(존재X)": Citation(
            quote="인공지능이 작성한 서면은 무효이다",
            ref="대법원 2021. 1. 1. 선고 2021도99999 판결"),
        "③ 실재 판례·misquote": Citation(
            quote="명예훼손죄는 허위사실인 경우에만 성립한다",   # 원문과 반대
            ref="2018도13694"),
        "④ 형식변형(정규화로 통과)": Citation(
            quote='  통상의   손해를 한도로 하며 ,  특별한 사정으로 인한 손해는  ',  # 공백/구두점 변형
            ref="대법원 2019.5.14. 선고  2019다12345 판결"),
    }
    print(f"{'케이스':<22} {'사건번호':<12} exists verbatim → status")
    print("-" * 72)
    for label, c in cases.items():
        v = verify_citation(c)
        print(f"{label:<22} {str(v.case_number):<12} {str(v.exists):<6} {str(v.verbatim):<8} → {v.status}"
              + (f"  ({'; '.join(v.reasons)})" if v.reasons else ""))
    print("-" * 72)
    doc = verify_document(list(cases.values()))
    gate = {"deny": "file_court DENY (결정론 차단)",
            "review": "file_court HOLD → 사람/NLI 확인 필요",
            "pass": "file_court ALLOW"}[doc.verdict]
    print(f"\n문서 verdict = {doc.verdict!r}  → {gate}")
    if doc.hard_blocked:
        print("  하드차단(날조, 존재X): " + ", ".join(f"{v.case_number or '?'}" for v in doc.hard_blocked))
    if doc.needs_review:
        print("  검토필요(실재하나 verbatim 실패 — misquote/생략/삽입 구분 불가): "
              + ", ".join(f"{v.case_number}" for v in doc.needs_review))
