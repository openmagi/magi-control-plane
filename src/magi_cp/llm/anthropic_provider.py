"""AnthropicProvider — LlmProvider impl for the Anthropic Messages API.

Hits POST https://api.anthropic.com/v1/messages via httpx. No SDK dependency.
Anthropic splits "system" out of the messages array; multiple system entries
in our input are concatenated.

Operator wires this via env: `MAGI_CP_LLM_COMPILER=magi_cp.llm.anthropic_provider:anthropic_default`.
The factory reads ANTHROPIC_API_KEY (and ANTHROPIC_MODEL if you want to
override the default).
"""
from __future__ import annotations

import os
from typing import Iterable

import httpx

from .provider import LlmMessage, LlmProvider, LlmProviderError


_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_MAX_TOKENS = 4096
_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"


class AnthropicProvider:
    """LlmProvider for Anthropic Messages API.

    Pass `http=httpx.Client()` to share connection pooling; a default httpx
    client is created if not provided. Test code injects a mock for both
    paths.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        http: object | None = None,
        timeout: float = 60.0,
    ) -> None:
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise LlmProviderError("ANTHROPIC_API_KEY is not set")
        self.api_key = key
        self.model = model or os.environ.get("ANTHROPIC_MODEL") or _DEFAULT_MODEL
        self.max_tokens = max_tokens
        self._http = http if http is not None else httpx.Client(timeout=timeout)

    @staticmethod
    def _split(messages: list[LlmMessage]) -> tuple[str, list[dict]]:
        """Anthropic expects a top-level `system` string and a `messages`
        array of user/assistant turns. Multiple system entries from our
        compiler are joined newline-double."""
        systems = [m["content"] for m in messages if m["role"] == "system"]
        chat = [
            {"role": m["role"], "content": m["content"]}
            for m in messages if m["role"] in ("user", "assistant")
        ]
        return ("\n\n".join(systems), chat)

    def complete(self, messages: list[LlmMessage]) -> str:
        system, chat = self._split(messages)
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": chat,
        }
        if system:
            body["system"] = system
        try:
            resp = self._http.post(
                _API_URL,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": _API_VERSION,
                    "content-type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise LlmProviderError(f"anthropic http error: {e}") from e
        except httpx.HTTPError as e:
            raise LlmProviderError(f"anthropic network error: {e}") from e
        data = resp.json()
        content = data.get("content") or []
        # Concatenate any text blocks; ignore tool-use blocks (we don't ask for them).
        text_parts = [b.get("text", "") for b in content if b.get("type") == "text"]
        text = "".join(text_parts).strip()
        if not text:
            raise LlmProviderError("anthropic returned empty content")
        return text


def anthropic_default() -> AnthropicProvider:
    """Env-driven factory for `MAGI_CP_LLM_COMPILER` / `MAGI_CP_LLM_REVIEWER`."""
    return AnthropicProvider()


__all__ = ["AnthropicProvider", "anthropic_default"]
