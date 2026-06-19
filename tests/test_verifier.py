"""P1 verifier — spike behavior를 테스트로 lock."""
import pytest
from magi_cp.verifier import (
    Citation, CitationVerdict, DocumentVerdict, normalize,
    extract_case_number, verify_citation, verify_document,
    DictResolver,
)


# ── normalize ───────────────────────────────────────────────────────
@pytest.mark.parametrize("a,b", [
    ("hello  world", "hello world"),
    ("좋은 글  ", "좋은 글"),
    ('"공연히 사실"', "공연히 사실"),                # wrapping quotes 제거
    ("말 “공연히” 적시", '말 "공연히" 적시'),     # smart quotes → ASCII (mid)
    ("a , b", "a,b"),                              # 구두점 주변 공백 제거
    ("a.b", "a.b"),
])
def test_normalize_idempotent_and_canonical(a, b):
    n = normalize(a)
    assert n == b
    assert normalize(n) == n   # 멱등


# ── 사건번호 파서 ────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("대법원 2018. 9. 13. 선고 2018도13694 판결", "2018도13694"),
    ("대법원 1995. 6. 16. 선고 94다35718 판결", "94다35718"),       # pre-2000
    ("대법원 1999. 2. 24. 선고 99도1234 판결", "99도1234"),
    ("헌법재판소 2010. 2. 25. 2008헌가23 결정", "2008헌가23"),
    ("헌재 2011. 6. 30. 2009헌바55", "2009헌바55"),
    ("대법원 2017. 9. 21. 선고 2017도7843 전원합의체 판결", "2017도7843"),
    ("서울고등법원 2014. 11. 20. 선고 2014나2034 판결", "2014나2034"),
])
def test_extract_case_number(text, expected):
    assert extract_case_number(text) == expected


def test_extract_case_number_none():
    assert extract_case_number("이건 판례 인용이 아닙니다") is None


# ── 3-way verdict: ok / missing / review ─────────────────────────────
SRC_307 = (
    "형법 제307조 제1항의 명예훼손죄는 공연히 사실을 적시하여 사람의 사회적 평가를 "
    "저하시킬 만한 구체적 사실을 드러내는 것을 말하고, 적시된 사실이 진실인 경우에도 성립할 수 있다."
)
RESOLVER = DictResolver({"2018도13694": SRC_307})


def test_verdict_ok_exact_quote():
    c = Citation(quote="공연히 사실을 적시하여 사람의 사회적 평가를 저하시킬 만한 구체적 사실을 드러내는 것",
                 ref="대법원 2018. 9. 13. 선고 2018도13694 판결")
    v = verify_citation(c, RESOLVER)
    assert v.status == "ok"
    assert v.exists and v.verbatim


def test_verdict_missing_fake_case():
    """존재하지 않는 사건번호 = 결정론 차단 (할루시네이션 봉쇄, money demo 심장)."""
    c = Citation(quote="아무 내용", ref="대법원 2099. 1. 1. 선고 2099도99999 판결")
    v = verify_citation(c, RESOLVER)
    assert v.status == "missing"
    assert not v.exists


def test_verdict_review_misquote():
    """실재 판례인데 verbatim 실패 = review (NLI/사람 에스컬레이션)."""
    c = Citation(quote="명예훼손죄는 허위사실인 경우에만 성립한다",  # 원문과 반대
                 ref="2018도13694")
    v = verify_citation(c, RESOLVER)
    assert v.status == "review"
    assert v.exists and not v.verbatim


def test_verdict_handles_punctuation_whitespace_variation():
    """구두점 주변 공백 변형은 ok로 통과해야 (B0 false-positive 케이스)."""
    c = Citation(quote="  통상의   손해를 한도로 하며 ,  특별한 사정으로 인한 손해는  ",
                 ref="대법원 2019.5.14. 선고  2019다12345 판결")
    src = ("채무불이행으로 인한 손해배상의 범위는 통상의 손해를 한도로 하며, "
           "특별한 사정으로 인한 손해는 채무자가 그 사정을 알았거나 알 수 있었을 때에 한하여 배상책임이 있다.")
    v = verify_citation(c, DictResolver({"2019다12345": src}))
    assert v.status == "ok"


# ── document-level verdict aggregation ───────────────────────────────
def test_document_verdict_deny_if_any_missing():
    doc = verify_document([
        Citation("공연히 사실을 적시하여 사람의 사회적 평가를 저하시킬 만한 구체적 사실을 드러내는 것",
                 "대법원 2018. 9. 13. 선고 2018도13694 판결"),
        Citation("AI 무효", "대법원 2099. 1. 1. 선고 2099도99999 판결"),
    ], RESOLVER)
    assert doc.verdict == "deny"
    assert len(doc.hard_blocked) == 1


def test_document_verdict_review_if_only_verbatim_fail():
    doc = verify_document([
        Citation("명예훼손죄는 허위사실인 경우에만 성립한다", "2018도13694"),
    ], RESOLVER)
    assert doc.verdict == "review"


def test_document_verdict_pass_if_all_ok():
    doc = verify_document([
        Citation("공연히 사실을 적시하여 사람의 사회적 평가를 저하시킬 만한 구체적 사실을 드러내는 것",
                 "대법원 2018. 9. 13. 선고 2018도13694 판결"),
    ], RESOLVER)
    assert doc.verdict == "pass"


# ── SourceResolver protocol: DictResolver는 한 구현 ──────────────────
def test_dict_resolver_returns_none_for_missing():
    r = DictResolver({"a": "x"})
    assert r.resolve("a") == "x"
    assert r.resolve("b") is None
