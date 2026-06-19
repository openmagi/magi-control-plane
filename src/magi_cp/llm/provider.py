"""LlmProvider protocol + a deterministic Fake for tests.

The Protocol intentionally matches the chat-completion shape (system + user/
assistant turns) without leaking provider specifics. Anthropic, OpenAI, or
self-hosted models can all conform to it via a small adapter.

Provider implementations are NOT included here. Add them in a separate module
when wiring a live model — that module can take the httpx dependency. This
module stays test-friendly.
"""
from __future__ import annotations

from typing import Protocol, TypedDict


class LlmMessage(TypedDict):
    role: str   # "system" | "user" | "assistant"
    content: str


class LlmProviderError(RuntimeError):
    """A provider failed to produce a response (network, auth, rate limit)."""


class LlmProvider(Protocol):
    """Minimal chat-completion surface.

    Callers send a list of LlmMessage and receive the assistant's text.
    Providers should raise LlmProviderError on failure — never return empty.
    """

    def complete(self, messages: list[LlmMessage]) -> str: ...


class FakeLlmProvider:
    """Deterministic provider for tests.

    Construct with a list of canned responses; each call to complete() returns
    the next one. Exposes `calls` and `last_messages` so tests can assert what
    the caller sent without monkeypatching.
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.last_messages: list[LlmMessage] = []

    def complete(self, messages: list[LlmMessage]) -> str:
        self.calls += 1
        self.last_messages = list(messages)
        if not self._responses:
            raise LlmProviderError(
                "FakeLlmProvider: no canned responses left "
                f"(call #{self.calls})"
            )
        return self._responses.pop(0)
