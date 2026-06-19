"""P6 NLI advisory — review verdict의 의미 검증.

NLI model is an *advisory* score on top of the deterministic 3-way verdict
(B0 design): verbatim-failed citations in existing cases go to `review`; an
NLI scorer can pre-grade those by entailment to help HITL prioritize, but
never overrides hard `missing` (hallucination) deny.
"""
import pytest

from magi_cp.verifier import Citation, DictResolver, verify_document
from magi_cp.verifier.nli import (
    AdvisoryNli, score_review_citations, EntailmentClassifier,
)


# ── Stub classifier for deterministic tests ─────────────────────────
class _StubNli:
    """Maps (quote, source) → (label, score). Tests inject expected results."""
    def __init__(self, mapping):
        self.mapping = mapping

    def score(self, quote: str, source: str):
        for (q_sub, src_sub), (label, score) in self.mapping.items():
            if q_sub in quote and src_sub in source:
                return label, score
        return "neutral", 0.5


SRC = ("형법 제307조 제1항의 명예훼손죄는 공연히 사실을 적시하여 사람의 사회적 평가를 "
       "저하시킬 만한 구체적 사실을 드러내는 것을 말하고, 적시된 사실이 진실인 경우에도 성립할 수 있다.")


def test_advisory_does_not_override_missing():
    """Hard deny on hallucination must NOT be touched by NLI."""
    cites = [Citation("아무거나", "대법원 2099. 1. 1. 선고 2099도99999 판결")]
    doc = verify_document(cites, DictResolver({}))
    assert doc.verdict == "deny"
    scored = score_review_citations(doc, source_resolver=DictResolver({}),
                                     classifier=_StubNli({}))
    # missing citations are NOT scored — they're already hard-blocked.
    assert all(s.nli_label is None for s in scored)


def test_advisory_scores_review_citations():
    """verbatim-failed citations get entailment-graded for HITL prioritization."""
    cite = Citation(
        quote="명예훼손죄는 허위사실인 경우에만 성립한다",       # 실제와 반대
        ref="대법원 2018. 9. 13. 선고 2018도13694 판결",
    )
    doc = verify_document([cite], DictResolver({"2018도13694": SRC}))
    assert doc.verdict == "review"

    stub = _StubNli({("허위사실인 경우에만", "진실인 경우에도"):
                     ("contradiction", 0.92)})
    scored = score_review_citations(doc, source_resolver=DictResolver({"2018도13694": SRC}),
                                     classifier=stub)
    assert len(scored) == 1
    assert scored[0].nli_label == "contradiction"
    assert scored[0].nli_score == pytest.approx(0.92)


def test_advisory_passes_through_ok_citations():
    """Already-`ok` citations are not re-scored."""
    cite = Citation(
        quote="공연히 사실을 적시하여 사람의 사회적 평가를 저하시킬 만한",
        ref="대법원 2018. 9. 13. 선고 2018도13694 판결",
    )
    doc = verify_document([cite], DictResolver({"2018도13694": SRC}))
    assert doc.verdict == "pass"
    scored = score_review_citations(doc, source_resolver=DictResolver({"2018도13694": SRC}),
                                     classifier=_StubNli({}))
    # ok citations: nli_label is None (no scoring needed)
    assert scored[0].nli_label is None


def test_advisory_metadata_doesnt_affect_aggregate_verdict():
    """Adding NLI scores must NEVER change the underlying ok/review/deny."""
    cite = Citation(quote="misquote", ref="2018도13694")
    doc = verify_document([cite], DictResolver({"2018도13694": SRC}))
    original = doc.verdict
    score_review_citations(doc, source_resolver=DictResolver({"2018도13694": SRC}),
                            classifier=_StubNli({}))
    assert doc.verdict == original


def test_advisory_handles_missing_resolver_gracefully():
    """If resolver can't fetch source, NLI returns 'no-source' label, not crash."""
    cite = Citation(quote="x", ref="2018도13694")
    doc = verify_document([cite], DictResolver({"2018도13694": SRC}))
    assert doc.verdict == "review"
    scored = score_review_citations(doc, source_resolver=DictResolver({}),
                                     classifier=_StubNli({}))
    assert scored[0].nli_label == "no-source"


# ── EntailmentClassifier protocol smoke (no torch required for stub) ──
def test_classifier_protocol_accepts_callable():
    class C:
        def score(self, quote, source):
            return "entailment", 0.8
    nli = AdvisoryNli(C())
    label, score = nli.score("a", "b")
    assert label == "entailment"
    assert score == 0.8
