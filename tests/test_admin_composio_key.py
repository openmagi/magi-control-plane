"""`/admin/composio-key` — platform-broker master key dashboard surface.

Two endpoints, admin-key gated:
  GET  /admin/composio-key   returns {set, last4, source} (never the raw key)
  PUT  /admin/composio-key   writes the 0600 overlay (preserve/clear/overwrite)
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud import composio_key_store
from magi_cp.cloud.app import create_app


HDR_ADMIN = {"X-Admin-Api-Key": "test-admin"}


@pytest.fixture(autouse=True)
def _admin_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", "test-admin")


@pytest.fixture(autouse=True)
def _isolated_key_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "keys"
    target.mkdir()
    monkeypatch.setenv("MAGI_CP_KEY_DIR", str(target))
    monkeypatch.delenv("MAGI_CP_COMPOSIO_MASTER_KEY", raising=False)
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    return target


def _tmp_policy_path() -> str:
    f = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    f.write("[]")
    f.close()
    return f.name


def _client() -> TestClient:
    app = create_app(dsn="sqlite:///:memory:", policy_store_path=_tmp_policy_path())
    return TestClient(app)


def test_get_returns_unset_when_empty() -> None:
    r = _client().get("/admin/composio-key", headers=HDR_ADMIN)
    assert r.status_code == 200, r.text
    assert r.json() == {"set": False, "last4": None, "source": None}


def test_get_requires_admin_key() -> None:
    assert _client().get("/admin/composio-key").status_code == 401


def test_get_rejects_wrong_admin_key() -> None:
    r = _client().get("/admin/composio-key", headers={"X-Admin-Api-Key": "wrong"})
    assert r.status_code == 401


def test_put_persists_and_returns_last4_only() -> None:
    c = _client()
    r = c.put(
        "/admin/composio-key", headers=HDR_ADMIN, json={"api_key": "cp_master_7777"}
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"set": True, "last4": "7777", "source": "file"}
    assert "cp_master_7777" not in r.text
    assert composio_key_store.get() == "cp_master_7777"


def test_put_empty_string_clears() -> None:
    composio_key_store.set("cp_existing_8888")
    c = _client()
    r = c.put("/admin/composio-key", headers=HDR_ADMIN, json={"api_key": ""})
    assert r.status_code == 200
    assert r.json() == {"set": False, "last4": None, "source": None}


def test_put_missing_field_preserves() -> None:
    composio_key_store.set("cp_keep_9999")
    c = _client()
    r = c.put("/admin/composio-key", headers=HDR_ADMIN, json={})
    assert r.status_code == 200
    assert r.json()["last4"] == "9999"


def test_put_requires_admin_key() -> None:
    r = _client().put("/admin/composio-key", json={"api_key": "x"})
    assert r.status_code == 401


def test_put_rejects_unknown_fields() -> None:
    c = _client()
    r = c.put(
        "/admin/composio-key",
        headers=HDR_ADMIN,
        json={"api_key": "x", "secret_backdoor": "y"},
    )
    assert r.status_code == 422


def test_put_oversize_key_rejected() -> None:
    c = _client()
    r = c.put(
        "/admin/composio-key", headers=HDR_ADMIN, json={"api_key": "x" * 10_000}
    )
    assert r.status_code == 422
