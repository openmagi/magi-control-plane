"""v1.2-W2 — AnthropicProvider + OpenAIProvider concrete impls.

We don't take an SDK dependency. Each provider hits the HTTP API directly via
httpx so the binary stays small and the wire is auditable. Tests use a
mock httpx Client; no real network, no real keys.
"""
import json
import os

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
        class _MockResp:
            status_code = 429
            text = "rate limited"
            def json(self): return {}
            def raise_for_status(self):
                import httpx
                raise httpx.HTTPStatusError("429", request=None, response=None)

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
