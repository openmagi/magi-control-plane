"""NLI advisory for `review` verdicts (B0 design layer).

This module is *purely advisory*. Its only job is to give HITL reviewers a
prioritization signal — "this misquote-looking citation actually entails the
source (probably a legitimate elision)" vs "this one contradicts (likely
hallucination dressed as misquote)". It must NEVER:
  - override a `missing` (hallucination) hard deny
  - flip a citation's deterministic `status`
  - be invoked on the gate path

The classifier is injected as a protocol; production wires a multilingual NLI
model (e.g. xlm-roberta-large-xnli) gated behind the optional `nli` extra so
the core wheel has no torch/transformers dependency.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol

from .citations import CitationVerdict, DocumentVerdict
from .sources import SourceResolver


# ── classifier protocol ──────────────────────────────────────────────
class EntailmentClassifier(Protocol):
    """Maps (quote, source) → ("entailment"|"neutral"|"contradiction", score in [0,1])."""

    def score(self, quote: str, source: str) -> tuple[str, float]: ...


@dataclass
class AdvisoryNli:
    """Thin wrapper around any classifier implementing the protocol."""
    classifier: EntailmentClassifier

    def score(self, quote: str, source: str) -> tuple[str, float]:
        return self.classifier.score(quote, source)


# ── public surface: score review citations ──────────────────────────
@dataclass
class ScoredCitation:
    """Original verdict + advisory NLI fields. Verdict status is NOT mutated."""
    verdict: CitationVerdict
    nli_label: str | None = None   # "entailment"|"neutral"|"contradiction"|"no-source" | None
    nli_score: float | None = None

    @property
    def status(self) -> str:
        return self.verdict.status


def score_review_citations(
    doc: DocumentVerdict,
    *,
    source_resolver: SourceResolver,
    classifier: EntailmentClassifier,
) -> list[ScoredCitation]:
    """Annotate review-status citations with NLI labels.

    - `missing` (hard deny) citations are NOT scored — they're already blocked.
    - `ok` citations are NOT scored — verbatim already passed.
    - Only `review` (existing case, verbatim failed) gets entailment-graded.
    """
    out: list[ScoredCitation] = []
    for v in doc.verdicts:
        sc = ScoredCitation(verdict=v)
        if v.status == "review" and v.case_number is not None:
            src = source_resolver.resolve(v.case_number)
            if src is None:
                sc.nli_label = "no-source"
            else:
                label, score = classifier.score(v.citation.quote, src)
                sc.nli_label, sc.nli_score = label, score
        out.append(sc)
    return out


# ── optional production classifier (lazy import; nli extra) ─────────
class _TransformerClassifier:  # pragma: no cover (requires torch)
    """xlm-roberta-large-xnli wrapper. Lazy-loaded; throws ImportError if
    `transformers`/`torch` not installed (install with `pip install magi-cp[nli]`).
    """
    def __init__(self, model_name: str = "joeddav/xlm-roberta-large-xnli"):
        try:
            from transformers import pipeline  # type: ignore
        except ImportError as e:
            raise ImportError(
                "NLI advisory requires the optional 'nli' extra: "
                "pip install 'magi-cp[nli]'"
            ) from e
        self._pipe = pipeline("zero-shot-classification", model=model_name)

    def score(self, quote: str, source: str) -> tuple[str, float]:
        labels = ["entailment", "neutral", "contradiction"]
        out = self._pipe(source, candidate_labels=labels, hypothesis_template=quote)
        return out["labels"][0], float(out["scores"][0])


def production_classifier() -> EntailmentClassifier:  # pragma: no cover
    """Convenience constructor used by the cloud service. Test paths should
    inject their own classifier (see test_nli.py stubs)."""
    return _TransformerClassifier()
