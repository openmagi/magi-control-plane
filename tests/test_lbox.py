"""P2 law.go.kr adapter — fetch precedent text by case number.

Network tests are marked; default `make test-quick` skips them.
"""
import re
import pytest

from magi_cp.mcp.lbox import clean_html, extract_case_holding, normalize_case_no


def test_clean_html_strips_br():
    assert clean_html("<br/>법조문<br>본문<br />") == "법조문 본문"


def test_clean_html_strips_tags_and_unescapes():
    assert clean_html("<p>&quot;공연히&quot;</p>") == '"공연히"'


def test_clean_html_collapses_whitespace():
    assert clean_html("  여러  공백   섞  ") == "여러 공백 섞"


def test_normalize_case_no_strips_spaces():
    assert normalize_case_no("2018 도 13694") == "2018도13694"
    assert normalize_case_no("  2008헌가23 ") == "2008헌가23"


def test_extract_case_holding_picks_a_sentence():
    judgment = ("<br/> [1] 어떤 원칙은 다음과 같이 정한다. "
                "구체적인 경우에 합리적으로 판단하여야 한다.<br/><br/>"
                " [2] 두 번째 항목입니다.")
    sent = extract_case_holding(judgment)
    assert "원칙" in sent or "정한다" in sent
    assert "[1]" not in sent
    assert "<br" not in sent


@pytest.mark.network
def test_search_precedent_live():
    """live: OC=clawy로 명예훼손 검색 → 결과 ≥1, 사건번호 형식 정상."""
    from magi_cp.mcp.lbox import search_precedent
    results = search_precedent("명예훼손", display=3)
    assert len(results) >= 1
    for r in results:
        assert re.match(r"\d{2,4}[가-힣]{1,3}\d+", r["case_no"])
        assert r["title"]


@pytest.mark.network
def test_fetch_precedent_live():
    """live: 알려진 판례번호로 fetch → 판시사항/판결요지 있음."""
    from magi_cp.mcp.lbox import fetch_precedent
    prec = fetch_precedent("612979")   # 2022도10369, B1-live에서 사용
    assert prec["case_no"] == "2022도10369"
    assert prec["holding"]               # 판시사항
    assert prec["judgment_summary"]      # 판결요지
    assert "<br" not in prec["judgment_summary"]
