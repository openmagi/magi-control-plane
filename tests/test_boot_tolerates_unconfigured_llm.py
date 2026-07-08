"""Boot invariant: an unconfigured LLM provider must not crash the cloud.

The served self-host docker-compose.yml defaults MAGI_CP_LLM_COMPILER to
`magi_cp.llm.anthropic_provider:anthropic_default` and MAGI_CP_LLM_REVIEWER
to `magi_cp.llm.openai_provider:openai_default`. A fresh self-host install
has NO ANTHROPIC_API_KEY / OPENAI_API_KEY, so the eager key check in the
provider __init__ raises LlmProviderError. Before the fix that raise
propagated out of `_build_production_app` (called by `uvicorn --factory`),
so the cloud container crash-looped (restarting / unhealthy) and EVERY
fresh self-host install was dead on arrival.

The LLM compiler/reviewer are OPTIONAL (compile returns 503 when absent),
so a wired-but-unconfigured provider must degrade to None, not take the
whole control plane down. Regression guard for that boot crash.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def _clean_env(monkeypatch, tmp_path):
    # Point every writable store at a tmp dir so create_app can persist.
    for var, val in {
        "MAGI_CP_HITL_API_KEY": "x",
        "MAGI_CP_API_KEY": "x",
        "MAGI_CP_ADMIN_API_KEY": "x",
        "MAGI_CP_ADMIN_HMAC_SECRET": "x",
        "MAGI_CP_KEY_DIR": str(tmp_path / "keys"),
        "MAGI_CP_DSN": f"sqlite:///{tmp_path / 'db.sqlite'}",
        "MAGI_CP_POLICY_STORE": str(tmp_path / "policies.json"),
        "MAGI_CP_PACK_STORE": str(tmp_path / "packs.json"),
        "MAGI_CP_CUSTOM_VERIFIER_STORE": str(tmp_path / "cv.json"),
        "MAGI_CP_POLICY_GROUP_STORE": str(tmp_path / "pg.json"),
        "MAGI_CP_SCRIPT_STORE_DIR": str(tmp_path),
    }.items():
        monkeypatch.setenv(var, val)
    # Ensure NO provider key is present (the fresh-install condition).
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def test_build_production_app_boots_with_compose_defaults_and_no_keys(
    monkeypatch, _clean_env
):
    """Reproduces the served compose defaults + no API keys → must boot."""
    monkeypatch.setenv(
        "MAGI_CP_LLM_COMPILER",
        "magi_cp.llm.anthropic_provider:anthropic_default",
    )
    monkeypatch.setenv(
        "MAGI_CP_LLM_REVIEWER",
        "magi_cp.llm.openai_provider:openai_default",
    )
    app_mod = importlib.import_module("magi_cp.cloud.app")
    # Before the fix this raised LlmProviderError("ANTHROPIC_API_KEY is not set").
    app = app_mod._build_production_app()
    assert app is not None
    # The unconfigured providers degraded to None rather than crashing boot.
    assert app.state.engine is not None


def test_resolve_llm_provider_optional_returns_none_on_factory_raise(
    monkeypatch, _clean_env
):
    monkeypatch.setenv(
        "MAGI_CP_LLM_COMPILER",
        "magi_cp.llm.anthropic_provider:anthropic_default",
    )
    app_mod = importlib.import_module("magi_cp.cloud.app")
    # The claude CLI subscription fallback only fires when `claude` is on PATH.
    # This test asserts the honest-503 path when no fallback is available, so
    # force the CLI absent (CI has no `claude` binary anyway).
    monkeypatch.setattr(app_mod, "_claude_cli_fallback", lambda: None)
    assert app_mod._resolve_llm_provider_optional("MAGI_CP_LLM_COMPILER") is None


def test_resolve_llm_provider_optional_none_when_unset(monkeypatch, _clean_env):
    monkeypatch.delenv("MAGI_CP_LLM_COMPILER", raising=False)
    app_mod = importlib.import_module("magi_cp.cloud.app")
    # Same precondition: no claude CLI => the 503 path is intact.
    monkeypatch.setattr(app_mod, "_claude_cli_fallback", lambda: None)
    assert app_mod._resolve_llm_provider_optional("MAGI_CP_LLM_COMPILER") is None
