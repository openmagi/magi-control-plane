"""v1.2-W2 — AnthropicProvider + OpenAIProvider concrete impls.

We don't take an SDK dependency. Each provider hits the HTTP API directly via
httpx so the binary stays small and the wire is auditable. Tests use a
mock httpx Client; no real network, no real keys.
"""

import pytest

from magi_cp.llm.provider import LlmProviderError


# ── Anthropic ──────────────────────────────────────────────────────
class TestAnthropicProvider:
    def _make(self, monkeypatch, mock_client):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        from magi_cp.llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider(http=mock_client)

    def test_factory_reads_env(self, monkeypatch):
        """Default factory reads ANTHROPIC_API_KEY (no constructor arg)."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        from magi_cp.llm.anthropic_provider import AnthropicProvider
        p = AnthropicProvider()
        assert p.api_key == "sk-ant-test"

    def test_factory_raises_when_key_missing(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        from magi_cp.llm.anthropic_provider import AnthropicProvider
        with pytest.raises(LlmProviderError, match="ANTHROPIC_API_KEY"):
            AnthropicProvider()

    def test_complete_calls_messages_endpoint(self, monkeypatch):
        captured = {}

        class _MockResp:
            def __init__(self, body, status=200):
                self._body = body
                self.status_code = status
            def json(self): return self._body
            def raise_for_status(self):
                if self.status_code >= 400:
                    raise RuntimeError(f"http {self.status_code}")

        class _Mock:
            def post(self, url, **kw):
                captured["url"] = url
                captured["headers"] = kw.get("headers")
                captured["json"] = kw.get("json")
                return _MockResp({
                    "content": [{"type": "text", "text": "hello world"}],
                    "stop_reason": "end_turn",
                })

        p = self._make(monkeypatch, _Mock())
        out = p.complete([
            {"role": "system", "content": "you are a compiler"},
            {"role": "user", "content": "compile this"},
        ])
        assert out == "hello world"
        assert "/v1/messages" in captured["url"]
        assert captured["headers"]["x-api-key"] == "sk-ant-test"
        assert captured["headers"]["anthropic-version"]
        # system + user messages split per Anthropic API
        body = captured["json"]
        assert body["system"] == "you are a compiler"
        assert body["messages"] == [{"role": "user", "content": "compile this"}]

    def test_complete_concatenates_multiple_system_msgs(self, monkeypatch):
        """Anthropic API takes ONE system string. Our caller could supply
        multiple system messages; we join them."""
        class _Mock:
            def post(self, url, **kw):
                self.body = kw["json"]
                class R:
                    status_code = 200
                    def json(s): return {"content": [{"type": "text", "text": "ok"}]}
                    def raise_for_status(s): pass
                return R()
        m = _Mock()
        p = self._make(monkeypatch, m)
        p.complete([
            {"role": "system", "content": "rule A"},
            {"role": "system", "content": "rule B"},
            {"role": "user", "content": "x"},
        ])
        assert "rule A" in m.body["system"] and "rule B" in m.body["system"]

    def test_complete_raises_on_http_error(self, monkeypatch):
        """Non-retryable 4xx surfaces as LlmProviderError immediately."""
        class _MockResp:
            status_code = 400   # not 429/529 — no retry
            text = "bad request"
            headers = {}
            def json(self): return {"error": {"message": "bad"}}
            def raise_for_status(self):
                pass   # provider checks status_code itself now

        class _Mock:
            def post(self, url, **kw):
                return _MockResp()

        p = self._make(monkeypatch, _Mock())
        with pytest.raises(LlmProviderError, match="anthropic"):
            p.complete([{"role": "user", "content": "x"}])

    def test_complete_raises_on_empty_response(self, monkeypatch):
        class _MockResp:
            status_code = 200
            def json(self): return {"content": []}
            def raise_for_status(self): pass
        class _Mock:
            def post(self, url, **kw): return _MockResp()
        p = self._make(monkeypatch, _Mock())
        with pytest.raises(LlmProviderError, match="empty"):
            p.complete([{"role": "user", "content": "x"}])


# ── OpenAI ─────────────────────────────────────────────────────────
class TestOpenAIProvider:
    def _make(self, monkeypatch, mock_client):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        from magi_cp.llm.openai_provider import OpenAIProvider
        return OpenAIProvider(http=mock_client)

    def test_factory_raises_when_key_missing(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        from magi_cp.llm.openai_provider import OpenAIProvider
        with pytest.raises(LlmProviderError, match="OPENAI_API_KEY"):
            OpenAIProvider()

    def test_complete_calls_chat_endpoint(self, monkeypatch):
        captured = {}
        class _MockResp:
            status_code = 200
            def json(self): return {
                "choices": [{"message": {"role": "assistant", "content": "json out"}}],
            }
            def raise_for_status(self): pass
        class _Mock:
            def post(self, url, **kw):
                captured["url"] = url
                captured["headers"] = kw.get("headers")
                captured["json"] = kw.get("json")
                return _MockResp()
        p = self._make(monkeypatch, _Mock())
        out = p.complete([
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u"},
        ])
        assert out == "json out"
        assert "/v1/chat/completions" in captured["url"]
        assert captured["headers"]["Authorization"].startswith("Bearer sk-test")
        # OpenAI passes system+user verbatim
        assert captured["json"]["messages"][0]["role"] == "system"

    def test_complete_raises_on_missing_choices(self, monkeypatch):
        class _MockResp:
            status_code = 200
            def json(self): return {"choices": []}
            def raise_for_status(self): pass
        class _Mock:
            def post(self, url, **kw): return _MockResp()
        p = self._make(monkeypatch, _Mock())
        with pytest.raises(LlmProviderError, match="empty"):
            p.complete([{"role": "user", "content": "x"}])


# ── env-pointed factory hook ───────────────────────────────────────
def test_default_anthropic_factory_callable(monkeypatch):
    """Factory function consumable from MAGI_CP_LLM_COMPILER=...:anthropic_default"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from magi_cp.llm.anthropic_provider import anthropic_default
    p = anthropic_default()
    from magi_cp.llm.anthropic_provider import AnthropicProvider
    assert isinstance(p, AnthropicProvider)


def test_default_openai_factory_callable(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from magi_cp.llm.openai_provider import openai_default
    p = openai_default()
    from magi_cp.llm.openai_provider import OpenAIProvider
    assert isinstance(p, OpenAIProvider)


# ── v2.0-W5 live-API hardening ──────────────────────────────────────
class TestAnthropicHardening:
    """Bugs caught by live-API audit (not present in v1.2)."""

    def _make(self, monkeypatch, mock_client):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        from magi_cp.llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider(http=mock_client)

    def test_error_response_body_message_surfaces(self, monkeypatch):
        """Anthropic returns {error: {message, type}} on 4xx — must reach the
        caller via LlmProviderError, not be swallowed by raise_for_status."""
        class _MockResp:
            status_code = 400
            text = ""
            def json(self):
                return {"type": "error",
                        "error": {"type": "invalid_request_error",
                                  "message": "prompt is too long: 250000 tokens > 200000"}}
            def raise_for_status(self):
                import httpx
                req = httpx.Request("POST", "http://test")
                resp = httpx.Response(400, request=req)
                raise httpx.HTTPStatusError("400", request=req, response=resp)
        class _Mock:
            def post(self, url, **kw): return _MockResp()
        p = self._make(monkeypatch, _Mock())
        with pytest.raises(LlmProviderError, match="prompt is too long"):
            p.complete([{"role": "user", "content": "x"}])

    def test_max_tokens_truncation_raises_specific_error(self, monkeypatch):
        """stop_reason='max_tokens' means output was cut — parser would crash
        on truncated JSON. Detect and surface explicitly."""
        class _MockResp:
            status_code = 200
            def json(self):
                return {"content": [{"type": "text", "text": '{"id": "partial'}],
                        "stop_reason": "max_tokens"}
            def raise_for_status(self): pass
        class _Mock:
            def post(self, url, **kw): return _MockResp()
        p = self._make(monkeypatch, _Mock())
        with pytest.raises(LlmProviderError, match="truncated|max_tokens"):
            p.complete([{"role": "user", "content": "x"}])

    def test_429_retry_after_with_single_retry(self, monkeypatch):
        """Anthropic returns 429 with retry-after header — single retry then
        success. (Don't retry forever; one shot.)"""
        sleeps: list[float] = []
        monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
        calls = [0]
        class _RetryResp:
            status_code = 429
            headers = {"retry-after": "1"}
            text = ""
            def json(self):
                return {"type": "error",
                        "error": {"type": "rate_limit_error", "message": "rl"}}
            def raise_for_status(self):
                import httpx
                req = httpx.Request("POST", "http://test")
                resp = httpx.Response(429, request=req)
                raise httpx.HTTPStatusError("429", request=req, response=resp)
        class _OkResp:
            status_code = 200
            def json(self): return {"content": [{"type": "text", "text": "ok"}],
                                     "stop_reason": "end_turn"}
            def raise_for_status(self): pass
        class _Mock:
            def post(self, url, **kw):
                calls[0] += 1
                return _RetryResp() if calls[0] == 1 else _OkResp()
        p = self._make(monkeypatch, _Mock())
        assert p.complete([{"role": "user", "content": "x"}]) == "ok"
        assert calls[0] == 2
        assert sleeps == [1.0]


class TestOpenAIHardening:
    def _make(self, monkeypatch, mock_client):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        from magi_cp.llm.openai_provider import OpenAIProvider
        return OpenAIProvider(http=mock_client)

    def test_error_body_message_surfaces(self, monkeypatch):
        class _MockResp:
            status_code = 400
            text = ""
            def json(self):
                return {"error": {"message": "model gpt-x does not exist",
                                  "type": "invalid_request_error"}}
            def raise_for_status(self):
                import httpx
                req = httpx.Request("POST", "http://test")
                resp = httpx.Response(400, request=req)
                raise httpx.HTTPStatusError("400", request=req, response=resp)
        class _Mock:
            def post(self, url, **kw): return _MockResp()
        p = self._make(monkeypatch, _Mock())
        with pytest.raises(LlmProviderError, match="does not exist"):
            p.complete([{"role": "user", "content": "x"}])

    def test_length_finish_reason_surfaces_as_truncation(self, monkeypatch):
        """finish_reason='length' means truncate — must NOT slip through as
        'ok content with empty message' or similar generic error."""
        class _MockResp:
            status_code = 200
            def json(self):
                return {"choices": [{
                    "finish_reason": "length",
                    "message": {"role": "assistant",
                                "content": '{"id":"part'},
                }]}
            def raise_for_status(self): pass
        class _Mock:
            def post(self, url, **kw): return _MockResp()
        p = self._make(monkeypatch, _Mock())
        with pytest.raises(LlmProviderError, match="truncated|length"):
            p.complete([{"role": "user", "content": "x"}])

    def test_json_object_response_format_sent(self, monkeypatch):
        """Always request JSON object so the model can't wrap in markdown."""
        captured = {}
        class _MockResp:
            status_code = 200
            def json(self): return {"choices": [{
                "finish_reason": "stop",
                "message": {"content": '{"ok": true}'},
            }]}
            def raise_for_status(self): pass
        class _Mock:
            def post(self, url, **kw):
                captured["body"] = kw["json"]
                return _MockResp()
        p = self._make(monkeypatch, _Mock())
        p.complete([
            {"role": "system", "content": "you produce JSON"},
            {"role": "user", "content": "x"},
        ])
        assert captured["body"]["response_format"] == {"type": "json_object"}

    def test_json_mode_requires_json_in_prompt(self, monkeypatch):
        """OpenAI rejects json_object mode if the prompt doesn't mention JSON.
        Our default system instruction MUST include the word JSON; the call
        should still go through without us silently dropping json_object."""
        captured = {}
        class _MockResp:
            status_code = 200
            def json(self): return {"choices": [{
                "finish_reason": "stop",
                "message": {"content": '{}'},
            }]}
            def raise_for_status(self): pass
        class _Mock:
            def post(self, url, **kw):
                captured["body"] = kw["json"]
                return _MockResp()
        p = self._make(monkeypatch, _Mock())
        # System prompt does include JSON (caller responsibility)
        p.complete([{"role": "system", "content": "output JSON only"},
                    {"role": "user", "content": "x"}])
        assert captured["body"]["response_format"] == {"type": "json_object"}


# ── async surface (FastAPI loop-friendly) ──────────────────────────
class TestAsyncSurface:
    """compile_with_review runs in async routes — calling sync httpx blocks
    the event loop. Providers must expose acomplete() OR the route must
    use asyncio.to_thread. We pick the latter for simplicity but want a
    test asserting the helper exists and works."""

    def test_acomplete_helper_runs_in_thread(self, monkeypatch):
        import asyncio
        from magi_cp.llm.async_helper import acomplete
        from magi_cp.llm.provider import FakeLlmProvider
        p = FakeLlmProvider(["hello async"])
        out = asyncio.run(acomplete(p, [{"role": "user", "content": "x"}]))
        assert out == "hello async"
