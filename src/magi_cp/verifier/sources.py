"""SourceResolver protocol: case_number → source text or None.

Why a protocol (not dict default): B1-live revealed that hard-coding a static
corpus dict is wrong — verification must consult a live resolver (law.go.kr,
LBox, internal corpus). A None return is "case does not exist" (= hard deny).
"""
from __future__ import annotations
from typing import Protocol


class SourceResolver(Protocol):
    """Resolve a case number to its source text. Return None if not found."""

    def resolve(self, case_number: str) -> str | None: ...


class DictResolver:
    """In-memory resolver. Useful for tests, defaults, hot caches."""

    def __init__(self, corpus: dict[str, str]):
        self._corpus = corpus

    def resolve(self, case_number: str) -> str | None:
        return self._corpus.get(case_number)
