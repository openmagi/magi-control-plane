"""composio_key_store — single master-key on-disk overlay (0600) + env fallback."""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from magi_cp.cloud import composio_key_store


@pytest.fixture(autouse=True)
def _isolated_key_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "keys"
    target.mkdir()
    monkeypatch.setenv("MAGI_CP_KEY_DIR", str(target))
    # Clear env keys so file-vs-env tests are deterministic.
    monkeypatch.delenv("MAGI_CP_COMPOSIO_MASTER_KEY", raising=False)
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    return target


def test_get_and_status_empty_when_unset() -> None:
    assert composio_key_store.get() is None
    assert composio_key_store.resolve_master_key() is None
    assert composio_key_store.status() == {"set": False, "last4": None, "source": None}


def test_set_persists_and_status_reports_file_source() -> None:
    composio_key_store.set("cp_master_secret_abcd")
    assert composio_key_store.get() == "cp_master_secret_abcd"
    assert composio_key_store.resolve_master_key() == "cp_master_secret_abcd"
    assert composio_key_store.status() == {
        "set": True,
        "last4": "abcd",
        "source": "file",
    }


def test_status_never_returns_raw_key() -> None:
    composio_key_store.set("cp_master_supersecret_9999")
    payload = composio_key_store.status()
    assert "cp_master_supersecret" not in str(payload)
    assert payload["last4"] == "9999"


def test_set_none_preserves_existing() -> None:
    composio_key_store.set("cp_keep_1111")
    composio_key_store.set(None)
    assert composio_key_store.get() == "cp_keep_1111"


def test_set_empty_string_clears_and_env_fallback_takes_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    composio_key_store.set("cp_file_2222")
    composio_key_store.set("")
    assert composio_key_store.get() is None
    # File cleared, but env fallback now provides the effective key.
    monkeypatch.setenv("MAGI_CP_COMPOSIO_MASTER_KEY", "cp_env_3333")
    assert composio_key_store.resolve_master_key() == "cp_env_3333"
    assert composio_key_store.status() == {
        "set": True,
        "last4": "3333",
        "source": "env",
    }


def test_file_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CP_COMPOSIO_MASTER_KEY", "cp_env_lose")
    composio_key_store.set("cp_file_win")
    assert composio_key_store.resolve_master_key() == "cp_file_win"
    assert composio_key_store.status()["source"] == "file"


def test_composio_api_key_is_secondary_env_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMPOSIO_API_KEY", "cp_legacy_env_4444")
    assert composio_key_store.resolve_master_key() == "cp_legacy_env_4444"
    assert composio_key_store.status() == {
        "set": True,
        "last4": "4444",
        "source": "env",
    }


def test_primary_env_wins_over_secondary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CP_COMPOSIO_MASTER_KEY", "cp_primary")
    monkeypatch.setenv("COMPOSIO_API_KEY", "cp_secondary")
    assert composio_key_store.resolve_master_key() == "cp_primary"


def test_file_is_written_0600() -> None:
    composio_key_store.set("cp_perm_check")
    mode = stat.S_IMODE(os.stat(composio_key_store.store_path()).st_mode)
    assert mode == 0o600


def test_corrupt_file_treated_as_empty_not_deleted() -> None:
    composio_key_store.set("cp_valid")
    Path(composio_key_store.store_path()).write_text("{ not json", encoding="utf-8")
    assert composio_key_store.get() is None  # corrupt → empty
    assert Path(composio_key_store.store_path()).exists()  # not deleted
