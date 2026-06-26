"""Q97a — JSON-on-disk LLM API key overlay.

The store lives under `${MAGI_CP_KEY_DIR}/llm-keys.json` (default
`~/.magi-cp/cloud/`), shares the directory with the Ed25519 keypair, and
is 0600 because it carries provider secrets. The LLM providers
(`AnthropicProvider`, `OpenAIProvider`) consult this overlay BEFORE
their env-vars so a self-host operator pasting keys into the dashboard
takes effect without restarting the container.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from magi_cp.cloud import llm_key_store


@pytest.fixture(autouse=True)
def _redirect_key_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets a fresh MAGI_CP_KEY_DIR so the real `~/.magi-cp`
    is never touched and tests are hermetic."""
    target = tmp_path / "keys"
    target.mkdir()
    monkeypatch.setenv("MAGI_CP_KEY_DIR", str(target))
    return target


def test_get_returns_none_when_file_missing() -> None:
    out = llm_key_store.get()
    assert out == {"anthropic": None, "openai": None}


def test_set_then_get_roundtrips(_redirect_key_dir: Path) -> None:
    llm_key_store.set(anthropic="sk-ant-aaaa1111", openai="sk-bbbb2222")
    got = llm_key_store.get()
    assert got["anthropic"] == "sk-ant-aaaa1111"
    assert got["openai"] == "sk-bbbb2222"
    # Status surface masks the value but exposes last4 for confirmation.
    st = llm_key_store.status()
    assert st["anthropic_set"] is True
    assert st["anthropic_last4"] == "1111"
    assert st["openai_set"] is True
    assert st["openai_last4"] == "2222"


def test_set_only_anthropic_leaves_openai_unset(_redirect_key_dir: Path) -> None:
    llm_key_store.set(anthropic="sk-ant-x")
    got = llm_key_store.get()
    assert got["anthropic"] == "sk-ant-x"
    assert got["openai"] is None


def test_partial_update_preserves_prior_value(_redirect_key_dir: Path) -> None:
    """A None field on `set()` leaves the existing entry intact."""
    llm_key_store.set(anthropic="sk-ant-old", openai="sk-old")
    llm_key_store.set(anthropic="sk-ant-new")  # openai unchanged
    got = llm_key_store.get()
    assert got["anthropic"] == "sk-ant-new"
    assert got["openai"] == "sk-old"


def test_empty_string_clears_key(_redirect_key_dir: Path) -> None:
    """Empty string is the explicit "clear this key" signal."""
    llm_key_store.set(anthropic="sk-ant-x", openai="sk-y")
    llm_key_store.set(anthropic="")  # clear anthropic, leave openai
    got = llm_key_store.get()
    assert got["anthropic"] is None
    assert got["openai"] == "sk-y"


def test_file_is_mode_0600_after_write(_redirect_key_dir: Path) -> None:
    llm_key_store.set(anthropic="sk-ant-x")
    p = Path(llm_key_store.store_path())
    assert p.exists()
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got 0o{mode:o}"


def test_atomic_write_no_partial_file_on_crash(
    _redirect_key_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If `os.replace` fails mid-write the temp file is cleaned up and
    the prior file content is preserved (atomic guarantee)."""
    llm_key_store.set(anthropic="sk-ant-original")
    original = Path(llm_key_store.store_path()).read_text()

    real_replace = os.replace

    def _boom(*a, **kw):
        raise OSError("simulated crash mid-write")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError, match="simulated crash"):
        llm_key_store.set(anthropic="sk-ant-corrupt")
    # Reset for cleanup
    monkeypatch.setattr(os, "replace", real_replace)

    # Original file untouched.
    assert Path(llm_key_store.store_path()).read_text() == original
    # No stray temp file in the directory.
    leftovers = [
        p for p in _redirect_key_dir.iterdir()
        if p.name.startswith(".llm-keys.") and p.name.endswith(".tmp")
    ]
    assert leftovers == [], f"tempfile not cleaned up: {leftovers}"


def test_corrupt_file_treated_as_empty(_redirect_key_dir: Path) -> None:
    """A malformed JSON file should NOT crash get()/status() — fall back
    to "empty store" so env-var resolution still wins."""
    p = _redirect_key_dir / "llm-keys.json"
    p.write_text("this is not json {", encoding="utf-8")
    os.chmod(p, 0o600)
    got = llm_key_store.get()
    assert got == {"anthropic": None, "openai": None}


def test_status_short_key_returns_value_as_last4(
    _redirect_key_dir: Path,
) -> None:
    """Short / malformed keys (< 4 chars) shouldn't render '' in the
    dashboard; the store returns the whole value as last4."""
    llm_key_store.set(anthropic="ab")
    st = llm_key_store.status()
    assert st["anthropic_last4"] == "ab"


def test_file_layout_is_documented_schema(_redirect_key_dir: Path) -> None:
    """On-disk shape: top-level JSON object with `anthropic_api_key` /
    `openai_api_key`. Missing key = not-set."""
    llm_key_store.set(anthropic="sk-ant-x")
    raw = json.loads(Path(llm_key_store.store_path()).read_text())
    assert raw == {"anthropic_api_key": "sk-ant-x"}


def test_provider_resolution_prefers_store_over_env(
    _redirect_key_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both providers should pick the store value over the env-var."""
    llm_key_store.set(anthropic="sk-ant-from-store",
                      openai="sk-from-store")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    from magi_cp.llm.anthropic_provider import AnthropicProvider
    from magi_cp.llm.openai_provider import OpenAIProvider
    a = AnthropicProvider()
    o = OpenAIProvider()
    assert a.api_key == "sk-ant-from-store"
    assert o.api_key == "sk-from-store"


def test_provider_resolution_falls_back_to_env_when_store_empty(
    _redirect_key_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No store file → env-var path stays byte-equivalent to the
    pre-Q97a deploy shape (docker-compose env-only)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-only")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-only")
    from magi_cp.llm.anthropic_provider import AnthropicProvider
    from magi_cp.llm.openai_provider import OpenAIProvider
    a = AnthropicProvider()
    o = OpenAIProvider()
    assert a.api_key == "sk-ant-env-only"
    assert o.api_key == "sk-env-only"


def test_provider_resolution_raises_when_neither_store_nor_env(
    _redirect_key_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from magi_cp.llm.anthropic_provider import AnthropicProvider
    from magi_cp.llm.openai_provider import OpenAIProvider
    from magi_cp.llm.provider import LlmProviderError
    with pytest.raises(LlmProviderError, match="ANTHROPIC_API_KEY"):
        AnthropicProvider()
    with pytest.raises(LlmProviderError, match="OPENAI_API_KEY"):
        OpenAIProvider()
