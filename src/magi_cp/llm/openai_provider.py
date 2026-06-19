"""OpenAIProvider — LlmProvider impl for the OpenAI Chat Completions API.

Hits POST https://api.openai.com/v1/chat/completions via httpx. No SDK dep.
"""
from __future__ import annotations

import os

import httpx

from .provider import LlmMessage, LlmProvider, LlmProviderError


_DEFAULT_MODEL = "gpt-5.5"
_API_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        http: object | None = None,
        timeout: float = 60.0,
    ) -> None:
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LlmProviderError("OPENAI_API_KEY is not set")
        self.api_key = key
        self.model = model or os.environ.get("OPENAI_MODEL") or _DEFAULT_MODEL
        self._http = http if http is not None else httpx.Client(timeout=timeout)

    def complete(self, messages: list[LlmMessage]) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        }
        try:
            resp = self._http.post(
                _API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise LlmProviderError(f"openai http error: {e}") from e
        except httpx.HTTPError as e:
            raise LlmProviderError(f"openai network error: {e}") from e
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise LlmProviderError("openai returned empty choices")
        msg = (choices[0] or {}).get("message") or {}
        text = (msg.get("content") or "").strip()
        if not text:
            raise LlmProviderError("openai returned empty content")
        return text


def openai_default() -> OpenAIProvider:
    """Env-driven factory for `MAGI_CP_LLM_COMPILER` / `MAGI_CP_LLM_REVIEWER`."""
    return OpenAIProvider()


__all__ = ["OpenAIProvider", "openai_default"]
