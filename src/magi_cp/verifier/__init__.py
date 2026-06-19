"""Citation verifier — deterministic existence + verbatim, advisory beyond."""
from .normalize import normalize
from .sources import SourceResolver, DictResolver
from .citations import (
    Citation, CitationVerdict, DocumentVerdict,
    extract_case_number, verify_citation, verify_document,
)
from .nli import (
    AdvisoryNli, EntailmentClassifier, ScoredCitation, score_review_citations,
)

__all__ = [
    "normalize", "SourceResolver", "DictResolver",
    "Citation", "CitationVerdict", "DocumentVerdict",
    "extract_case_number", "verify_citation", "verify_document",
    "AdvisoryNli", "EntailmentClassifier", "ScoredCitation", "score_review_citations",
]
