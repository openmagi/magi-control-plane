"""Citation verifier — deterministic existence + verbatim, advisory beyond.

Design (locked by B0 stress test): existence is the hard gate (deterministic
hallucination block, FN=0). Verbatim match is the fast path. Verbatim failure
in an *existing* case is *not* a hard block — it escalates to review (NLI /
human) because we cannot deterministically tell misquote from legitimate
elision/insertion/rearrangement.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field

from .normalize import normalize
from .sources import SourceResolver

# Korean case-number regex. `\d{2,4}` covers pre-2000 (e.g. 94다35718) and
# constitutional court (헌가/헌바) and full-bench/lower-court formats.
# Verified by test_extract_case_number.
_CASE_NO = re.compile(r"\b(\d{2,4}[가-힣]{1,3}\d+)\b")


def extract_case_number(text: str) -> str | None:
    m = _CASE_NO.search(normalize(text))
    return m.group(1) if m else None


@dataclass(frozen=True)
class Citation:
    quote: str
    ref: str


@dataclass
class CitationVerdict:
    citation: Citation
    case_number: str | None
    exists: bool
    verbatim: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if not self.exists:
            return "missing"      # hard deny — hallucination block
        if self.verbatim:
            return "ok"
        return "review"           # escalate (NLI / human)


def verify_citation(c: Citation, resolver: SourceResolver) -> CitationVerdict:
    v = CitationVerdict(citation=c, case_number=extract_case_number(c.ref),
                        exists=False, verbatim=False)
    if v.case_number is None:
        v.reasons.append("사건번호 파싱 실패")
        return v
    src = resolver.resolve(v.case_number)
    if src is None:
        v.reasons.append(f"존재하지 않는 판례: {v.case_number} (할루시네이션 의심)")
        return v
    v.exists = True
    if normalize(c.quote) in normalize(src):
        v.verbatim = True
    else:
        v.reasons.append("인용 텍스트가 원문과 불일치(misquote 또는 정당한 변형)")
    return v


@dataclass
class DocumentVerdict:
    verdicts: list[CitationVerdict]

    @property
    def verdict(self) -> str:
        """Aggregate: deny if any missing > review if any review > pass."""
        if any(v.status == "missing" for v in self.verdicts):
            return "deny"
        if any(v.status == "review" for v in self.verdicts):
            return "review"
        return "pass"

    @property
    def hard_blocked(self) -> list[CitationVerdict]:
        return [v for v in self.verdicts if v.status == "missing"]

    @property
    def needs_review(self) -> list[CitationVerdict]:
        return [v for v in self.verdicts if v.status == "review"]


def verify_document(citations: list[Citation], resolver: SourceResolver) -> DocumentVerdict:
    return DocumentVerdict([verify_citation(c, resolver) for c in citations])
