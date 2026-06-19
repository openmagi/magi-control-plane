"""Text normalization for deterministic verbatim matching.

Idempotent: normalize(normalize(s)) == normalize(s). Tested in test_verifier.
"""
from __future__ import annotations
import re
import unicodedata

_QUOTES = {"“": '"', "”": '"', "‘": "'", "’": "'", "«": '"', "»": '"'}
_PUNCT_WHITESPACE = re.compile(r"\s*([,.·、，。:;])\s*")
_WHITESPACE = re.compile(r"\s+")


def normalize(s: str) -> str:
    """Canonical form: NFC + unify smart quotes + collapse whitespace + strip
    whitespace around common punctuation. Idempotent.
    """
    s = unicodedata.normalize("NFC", s)
    s = "".join(_QUOTES.get(ch, ch) for ch in s)
    s = _WHITESPACE.sub(" ", s)
    s = _PUNCT_WHITESPACE.sub(r"\1", s)
    return s.strip().strip('"\'').strip()
