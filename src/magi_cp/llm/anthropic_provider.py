"""AnthropicProvider — LlmProvider impl for the Anthropic Messages API.

Hits POST https://api.anthropic.com/v1/messages via httpx. No SDK dependency.
Anthropic splits "system" out of the messages array; multiple system entries
in our input are concatenated.

Hardened per v2.0-W5 live-API audit:
  - error body's `error.message` extracted into LlmProviderError
  - `stop_reason == "max_tokens"` raises explicit truncation error
  - 429/529 triggers single retry honoring `retry-after`
  - `max_tokens` bumped to 8192 (was 4096) so 60K-char NL doesn't truncate

Operator wires this via env: `MAGI_CP_LLM_COMPILER=magi_cp.llm.anthropic_provider:anthropic_default`.
The factory reads ANTHROPIC_API_KEY (and ANTHROPIC_MODEL if you want to
override the default).
"""
from __future__ import annotations

import os
import time

import httpx

from .provider import LlmMessage, LlmProviderError


_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_MAX_TOKENS = 8192
_API_URL = "https://api.anthropic.com/v1/messages"
# 2023-06-01 is the stable pinned version. Newer beta features require the
# anthropic-beta header instead of a version bump.
_API_VERSION = "2023-06-01"
_RETRYABLE_STATUS = (429, 529)


def _extract_error_message(resp_json: dict) -> str:
    """Pull Anthropic's error.message out of the response body for diagnostics."""
    if not isinstance(resp_json, dict):
        return ""
    err = resp_json.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or "")
    return ""


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
        # Resolution order (Q97a): explicit arg → on-disk overlay → env-var.
        # The store lets self-host operators paste keys into the /settings
        # page; when the store is empty, behaviour is byte-identical to the
        # pre-Q97a env-var-only deployment.
        key = api_key
        if not key:
            try:
                from ..cloud.llm_key_store import get as _store_get
                key = _store_get().get("anthropic") or None
            except Exception:
                # Store import / read failure must NOT mask the env-var
                # fallback. Operators running without a writable key dir
                # (e.g. unit tests in a read-only sandbox) still get the
                # env-var path.
                key = None
        if not key:
            key = os.environ.get("ANTHROPIC_API_KEY")
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

    def _post_once(self, body: dict):
        return self._http.post(
            _API_URL,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": _API_VERSION,
                "content-type": "application/json",
            },
            json=body,
        )

    def complete(self, messages: list[LlmMessage]) -> str:
        system, chat = self._split(messages)
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": chat,
        }
        if system:
            body["system"] = system

        attempts_left = 2   # initial + 1 retry on 429/529
        last_err_msg = ""
        while attempts_left > 0:
            attempts_left -= 1
            try:
                resp = self._post_once(body)
            except httpx.HTTPError as e:
                raise LlmProviderError(f"anthropic network error: {e}") from e

            if resp.status_code in _RETRYABLE_STATUS and attempts_left > 0:
                wait = float(resp.headers.get("retry-after", "1") or "1")
                time.sleep(wait)
                continue

            if resp.status_code >= 400:
                try:
                    detail = _extract_error_message(resp.json())
                except Exception:
                    detail = ""
                msg = detail or f"http {resp.status_code}"
                raise LlmProviderError(f"anthropic http error: {msg}")

            data = resp.json()
            if data.get("stop_reason") == "max_tokens":
                raise LlmProviderError(
                    f"anthropic output truncated at max_tokens={self.max_tokens} "
                    "(downstream JSON parse will fail; raise max_tokens or shrink prompt)"
                )
            content = data.get("content") or []
            text_parts = [b.get("text", "") for b in content if b.get("type") == "text"]
            text = "".join(text_parts).strip()
            if not text:
                raise LlmProviderError("anthropic returned empty content")
            return text

        # Should not reach here; loop always returns or raises.
        raise LlmProviderError(f"anthropic exhausted retries: {last_err_msg}")


def anthropic_default() -> AnthropicProvider:
    """Env-driven factory for `MAGI_CP_LLM_COMPILER` / `MAGI_CP_LLM_REVIEWER`."""
    return AnthropicProvider()


__all__ = ["AnthropicProvider", "anthropic_default"]
