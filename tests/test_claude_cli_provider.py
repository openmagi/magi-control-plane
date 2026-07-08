"""Hermetic tests for ClaudeCliProvider + the app auto-select fallback.

NEVER spawns the real `claude` binary. subprocess.Popen is monkeypatched at
the module boundary (magi_cp.llm.claude_cli_provider.subprocess) with a fake
process whose communicate() returns canned bytes.
"""
from __future__ import annotations

import importlib
import json
import subprocess

import pytest

from magi_cp.llm import claude_cli_provider as ccp
from magi_cp.llm.provider import LlmProviderError


# --------------------------------------------------------------------------
# Fake subprocess plumbing
# --------------------------------------------------------------------------
class _FakeProc:
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        timeout: bool = False,
        pid: int = 4242,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._timeout = timeout
        self.pid = pid
        self.communicate_input: bytes | None = None
        self.killed = False

    def communicate(self, input=None, timeout=None):  # noqa: A002
        self.communicate_input = input
        if self._timeout:
            raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout)
        return (self._stdout, self._stderr)

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True

    def terminate(self):
        self.killed = True


def _install_fake_popen(monkeypatch, proc: _FakeProc):
    captured: dict = {}

    def _fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(ccp.subprocess, "Popen", _fake_popen)
    # Neutralize the process-group kill so a timeout test does not touch a
    # real pid. Record that a kill was attempted.
    def _fake_killpg(pgid, sig):
        proc.killed = True

    monkeypatch.setattr(ccp.os, "killpg", _fake_killpg, raising=False)
    monkeypatch.setattr(ccp.os, "getpgid", lambda pid: pid, raising=False)
    return captured


_MESSAGES = [
    {"role": "system", "content": "You are a policy compiler."},
    {"role": "user", "content": "compile: allow git status;<script>evil</script>"},
]


# --------------------------------------------------------------------------
# complete() happy path
# --------------------------------------------------------------------------
def test_complete_returns_result_text(monkeypatch):
    payload = json.dumps({"type": "result", "result": '{"ok": true}'}).encode()
    proc = _FakeProc(stdout=payload, returncode=0)
    captured = _install_fake_popen(monkeypatch, proc)

    out = ccp.ClaudeCliProvider().complete(_MESSAGES)
    assert out == '{"ok": true}'

    argv = captured["argv"]
    # argv list, never a shell string.
    assert isinstance(argv, list)
    assert argv[0] == "claude"
    assert "-p" in argv
    # JSON output flag.
    assert "--output-format" in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    # Tools disabled + MCP disabled.
    assert "--allowedTools" in argv
    assert argv[argv.index("--allowedTools") + 1] == ""
    assert "--strict-mcp-config" in argv
    # System text injected.
    assert "--system-prompt" in argv
    assert "You are a policy compiler." in argv
    # No shell anywhere.
    assert captured["kwargs"].get("shell", False) is False
    # The untrusted user text is passed via STDIN, not interpolated into argv.
    joined = " ".join(argv)
    assert "<script>evil</script>" not in joined
    assert proc.communicate_input is not None
    assert b"<script>evil</script>" in proc.communicate_input
    # Own session/process group for group-kill on timeout.
    assert captured["kwargs"].get("start_new_session", False) is True


def test_model_flag_only_when_env_set(monkeypatch):
    payload = json.dumps({"result": "x"}).encode()

    # Unset => no --model (use CLI default).
    monkeypatch.delenv("MAGI_CP_CLAUDE_CLI_MODEL", raising=False)
    cap = _install_fake_popen(monkeypatch, _FakeProc(stdout=payload))
    ccp.ClaudeCliProvider().complete(_MESSAGES)
    assert "--model" not in cap["argv"]

    # Set => --model <value>.
    monkeypatch.setenv("MAGI_CP_CLAUDE_CLI_MODEL", "claude-sonnet-4-6")
    cap2 = _install_fake_popen(monkeypatch, _FakeProc(stdout=payload))
    ccp.ClaudeCliProvider().complete(_MESSAGES)
    assert "--model" in cap2["argv"]
    assert cap2["argv"][cap2["argv"].index("--model") + 1] == "claude-sonnet-4-6"


def test_split_renders_roles_and_joins_systems():
    system, prompt = ccp.ClaudeCliProvider._split([
        {"role": "system", "content": "S1"},
        {"role": "system", "content": "S2"},
        {"role": "user", "content": "U1"},
        {"role": "assistant", "content": "A1"},
        {"role": "user", "content": "U2"},
    ])
    assert system == "S1\n\nS2"
    assert "User:\nU1" in prompt
    assert "Assistant:\nA1" in prompt
    assert prompt.endswith("User:\nU2")


# --------------------------------------------------------------------------
# error paths
# --------------------------------------------------------------------------
def test_nonzero_exit_raises_with_stderr_slice(monkeypatch):
    proc = _FakeProc(stdout=b"", stderr=b"boom: the compiler died", returncode=2)
    _install_fake_popen(monkeypatch, proc)
    with pytest.raises(LlmProviderError) as ei:
        ccp.ClaudeCliProvider().complete(_MESSAGES)
    msg = str(ei.value)
    assert "exited 2" in msg
    assert "boom: the compiler died" in msg


def test_unauthenticated_stderr_actionable(monkeypatch):
    proc = _FakeProc(
        stdout=b"",
        stderr=b"Error: Not logged in. Please run `claude login`.",
        returncode=1,
    )
    _install_fake_popen(monkeypatch, proc)
    with pytest.raises(LlmProviderError) as ei:
        ccp.ClaudeCliProvider().complete(_MESSAGES)
    msg = str(ei.value)
    assert "claude login" in msg
    assert "not authenticated" in msg.lower()


def test_timeout_raises_and_kills_group(monkeypatch):
    proc = _FakeProc(timeout=True)
    _install_fake_popen(monkeypatch, proc)
    with pytest.raises(LlmProviderError) as ei:
        ccp.ClaudeCliProvider(timeout=0.01).complete(_MESSAGES)
    assert "timed out" in str(ei.value)
    assert proc.killed is True


def test_stdout_not_json_raises(monkeypatch):
    proc = _FakeProc(stdout=b"this is not json at all", returncode=0)
    _install_fake_popen(monkeypatch, proc)
    with pytest.raises(LlmProviderError) as ei:
        ccp.ClaudeCliProvider().complete(_MESSAGES)
    assert "did not return JSON" in str(ei.value)


def test_json_missing_result_raises(monkeypatch):
    proc = _FakeProc(stdout=json.dumps({"type": "result"}).encode(), returncode=0)
    _install_fake_popen(monkeypatch, proc)
    with pytest.raises(LlmProviderError) as ei:
        ccp.ClaudeCliProvider().complete(_MESSAGES)
    assert "no `result`" in str(ei.value)


def test_empty_result_raises(monkeypatch):
    proc = _FakeProc(stdout=json.dumps({"result": "   "}).encode(), returncode=0)
    _install_fake_popen(monkeypatch, proc)
    with pytest.raises(LlmProviderError):
        ccp.ClaudeCliProvider().complete(_MESSAGES)


def test_is_error_result_raises(monkeypatch):
    proc = _FakeProc(
        stdout=json.dumps({"result": "rate limited", "is_error": True}).encode(),
        returncode=0,
    )
    _install_fake_popen(monkeypatch, proc)
    with pytest.raises(LlmProviderError) as ei:
        ccp.ClaudeCliProvider().complete(_MESSAGES)
    assert "error result" in str(ei.value)


def test_binary_missing_raises(monkeypatch):
    def _boom(argv, **kwargs):
        raise FileNotFoundError("no claude")

    monkeypatch.setattr(ccp.subprocess, "Popen", _boom)
    with pytest.raises(LlmProviderError) as ei:
        ccp.ClaudeCliProvider().complete(_MESSAGES)
    assert "not found on PATH" in str(ei.value)


# --------------------------------------------------------------------------
# availability + factory
# --------------------------------------------------------------------------
def test_claude_cli_available_true(monkeypatch):
    monkeypatch.setattr(ccp.shutil, "which", lambda name: "/usr/local/bin/claude")
    assert ccp.claude_cli_available() is True


def test_claude_cli_available_false(monkeypatch):
    monkeypatch.setattr(ccp.shutil, "which", lambda name: None)
    assert ccp.claude_cli_available() is False


def test_factory_returns_provider():
    p = ccp.claude_cli_default()
    assert isinstance(p, ccp.ClaudeCliProvider)


# --------------------------------------------------------------------------
# app auto-select wiring / precedence
# --------------------------------------------------------------------------
@pytest.fixture()
def app_mod():
    return importlib.import_module("magi_cp.cloud.app")


def test_wiring_unset_var_with_cli_available_returns_provider(monkeypatch, app_mod):
    monkeypatch.delenv("MAGI_CP_LLM_COMPILER", raising=False)
    monkeypatch.setattr(ccp, "claude_cli_available", lambda: True)
    resolved = app_mod._resolve_llm_provider_optional("MAGI_CP_LLM_COMPILER")
    assert isinstance(resolved, ccp.ClaudeCliProvider)


def test_wiring_cli_unavailable_returns_none(monkeypatch, app_mod):
    monkeypatch.delenv("MAGI_CP_LLM_COMPILER", raising=False)
    monkeypatch.setattr(ccp, "claude_cli_available", lambda: False)
    resolved = app_mod._resolve_llm_provider_optional("MAGI_CP_LLM_COMPILER")
    assert resolved is None


def test_wiring_key_missing_factory_raise_falls_back(monkeypatch, app_mod):
    # Wired to anthropic_default but no ANTHROPIC_API_KEY => factory raises
    # => resolution None => CLI fallback should fire when claude is present.
    monkeypatch.setenv(
        "MAGI_CP_LLM_COMPILER",
        "magi_cp.llm.anthropic_provider:anthropic_default",
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Keep the anthropic key store from supplying a key in a dev environment.
    import magi_cp.cloud.llm_key_store as _ks
    monkeypatch.setattr(_ks, "get", lambda: {}, raising=False)
    monkeypatch.setattr(ccp, "claude_cli_available", lambda: True)
    resolved = app_mod._resolve_llm_provider_optional("MAGI_CP_LLM_COMPILER")
    assert isinstance(resolved, ccp.ClaudeCliProvider)


def test_wiring_working_api_key_provider_wins(monkeypatch, app_mod):
    # A working API-key provider must win; the CLI fallback must NOT be used.
    monkeypatch.setenv(
        "MAGI_CP_LLM_COMPILER",
        "magi_cp.llm.anthropic_provider:anthropic_default",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    # If precedence were wrong, this True would win instead.
    monkeypatch.setattr(ccp, "claude_cli_available", lambda: True)
    called = {"fallback": False}
    orig = app_mod._claude_cli_fallback

    def _spy():
        called["fallback"] = True
        return orig()

    monkeypatch.setattr(app_mod, "_claude_cli_fallback", _spy)
    resolved = app_mod._resolve_llm_provider_optional("MAGI_CP_LLM_COMPILER")
    from magi_cp.llm.anthropic_provider import AnthropicProvider
    assert isinstance(resolved, AnthropicProvider)
    assert called["fallback"] is False
