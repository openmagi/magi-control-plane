"""LLM provider abstraction.

Compile and review surfaces use LlmProvider, never a vendor SDK directly,
so unit tests can swap in FakeLlmProvider with canned responses and the
runtime can switch providers without touching call sites.

LLM is allowed only in AUTHORING surfaces (NL→IR compile, IR review).
Runtime enforcement (the bash gate, verifier dispatch, token signing) must
never call an LLM — that's the v0 §0 invariant.
"""
from .provider import (
    FakeLlmProvider, LlmMessage, LlmProvider, LlmProviderError,
)

__all__ = ["FakeLlmProvider", "LlmMessage", "LlmProvider", "LlmProviderError"]
