"""OpenAIProvider — LlmProvider impl for the OpenAI Chat Completions API.

Hits POST https://api.openai.com/v1/chat/completions via httpx. No SDK dep.

Hardened per v2.0-W5 live-API audit:
  - error body's `error.message` extracted into LlmProviderError
  - `finish_reason == "length"` raises explicit truncation error
  - `response_format = {"type": "json_object"}` ALWAYS set (compile/review
    both produce JSON; the system prompts mention "JSON" so OpenAI accepts
    json_object mode)
  - `max_completion_tokens` set (the newer field name; `max_tokens` is
    deprecated for gpt-5-class models)
  - 429 triggers single retry honoring `retry-after`
"""
from __future__ import annotations

import os
import time

import httpx

from .provider import LlmMessage, LlmProviderError


_DEFAULT_MODEL = "gpt-5.5"
_DEFAULT_MAX_TOKENS = 8192
_API_URL = "https://api.openai.com/v1/chat/completions"


def _extract_error_message(resp_json: dict) -> str:
    if not isinstance(resp_json, dict):
        return ""
    err = resp_json.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or "")
    return ""


class OpenAIProvider:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        max_completion_tokens: int = _DEFAULT_MAX_TOKENS,
        http: object | None = None,
        timeout: float = 60.0,
    ) -> None:
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LlmProviderError("OPENAI_API_KEY is not set")
        self.api_key = key
        self.model = model or os.environ.get("OPENAI_MODEL") or _DEFAULT_MODEL
        self.max_completion_tokens = max_completion_tokens
        self._http = http if http is not None else httpx.Client(timeout=timeout)

    def _post_once(self, body: dict):
        return self._http.post(
            _API_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )

    def complete(self, messages: list[LlmMessage]) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
            "max_completion_tokens": self.max_completion_tokens,
            # Compile + review prompts both demand JSON output. response_format
            # enforces it at the API boundary so the model can't wrap in
            # markdown. NOTE: the system prompt must mention "JSON" — caller
            # ensures this in our nl_compiler templates.
            "response_format": {"type": "json_object"},
        }

        attempts_left = 2   # initial + 1 retry on 429
        while attempts_left > 0:
            attempts_left -= 1
            try:
                resp = self._post_once(body)
            except httpx.HTTPError as e:
                raise LlmProviderError(f"openai network error: {e}") from e

            if resp.status_code == 429 and attempts_left > 0:
                wait_raw = resp.headers.get("retry-after-ms") or resp.headers.get("retry-after") or "1"
                try:
                    wait = float(wait_raw) / (1000.0 if "ms" in str(wait_raw).lower() else 1.0)
                except (TypeError, ValueError):
                    wait = 1.0
                time.sleep(wait)
                continue

            if resp.status_code >= 400:
                try:
                    detail = _extract_error_message(resp.json())
                except Exception:
                    detail = ""
                msg = detail or f"http {resp.status_code}"
                raise LlmProviderError(f"openai http error: {msg}")

            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                raise LlmProviderError("openai returned empty choices")
            choice = choices[0] or {}
            finish_reason = choice.get("finish_reason")
            if finish_reason == "length":
                raise LlmProviderError(
                    f"openai output truncated at finish_reason=length "
                    f"(max_completion_tokens={self.max_completion_tokens}; "
                    "raise it or shrink prompt)"
                )
            msg_obj = choice.get("message") or {}
            text = (msg_obj.get("content") or "").strip()
            if not text:
                raise LlmProviderError(
                    f"openai returned empty content (finish_reason={finish_reason!r})"
                )
            return text

        raise LlmProviderError("openai exhausted retries")


def openai_default() -> OpenAIProvider:
    """Env-driven factory for `MAGI_CP_LLM_COMPILER` / `MAGI_CP_LLM_REVIEWER`."""
    return OpenAIProvider()


__all__ = ["OpenAIProvider", "openai_default"]
