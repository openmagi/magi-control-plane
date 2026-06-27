"""Composio master-key store — single-key JSON-on-disk overlay.

In `platform` credential mode, self-hosted magi-agent runtimes broker Composio
through this control-plane. The broker holds ONE master Composio API key
server-side (never exposed to tenants); operators paste it into the dashboard
/settings page instead of editing env and recreating the pod.

Mirrors `llm_key_store` (0600 file, atomic tempfile+rename, env fallback) but
holds a single key in `<MAGI_CP_KEY_DIR>/composio-key.json`:
  {"composio_api_key": "..."}

`get()` returns the file value only (store contents). `resolve_master_key()`
adds the env fallback (`MAGI_CP_COMPOSIO_MASTER_KEY`, then `COMPOSIO_API_KEY`)
so an env-only deployment is byte-identical to no-store-at-all. The broker
reads `resolve_master_key()`; the dashboard reads `status()`.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Literal, TypedDict

_FILE_NAME = "composio-key.json"
_DEFAULT_DIR = "~/.magi-cp/cloud"
_ENV_PRIMARY = "MAGI_CP_COMPOSIO_MASTER_KEY"
_ENV_FALLBACK = "COMPOSIO_API_KEY"


class StatusMap(TypedDict):
    set: bool
    last4: str | None
    source: Literal["file", "env", None]


def _resolve_dir() -> Path:
    raw = os.environ.get("MAGI_CP_KEY_DIR") or _DEFAULT_DIR
    return Path(os.path.expanduser(raw))


def _path() -> Path:
    return _resolve_dir() / _FILE_NAME


def _last4(value: str | None) -> str | None:
    if not value:
        return None
    return value[-4:] if len(value) >= 4 else value


def _read_raw() -> dict:
    p = _path()
    if not p.exists():
        return {}
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Corrupt file: do NOT silently delete; treat as empty so the env
        # fallback wins and an operator can inspect/repair manually.
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _atomic_write(payload: dict) -> None:
    dir_path = _resolve_dir()
    dir_path.mkdir(parents=True, exist_ok=True)
    p = _path()
    fd, tmp_name = tempfile.mkstemp(
        prefix=".composio-key.", suffix=".tmp", dir=str(dir_path),
    )
    try:
        os.chmod(tmp_name, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, sort_keys=True, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, p)
        os.chmod(p, 0o600)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _env_key() -> str | None:
    for env in (_ENV_PRIMARY, _ENV_FALLBACK):
        value = (os.environ.get(env) or "").strip()
        if value:
            return value
    return None


def get() -> str | None:
    """Return the file-stored master key (no env fallback), or None."""
    raw = _read_raw()
    value = raw.get("composio_api_key")
    return value if isinstance(value, str) and value else None


def resolve_master_key() -> str | None:
    """Effective master key the broker uses: file first, then env fallback."""
    return get() or _env_key()


def set(api_key: str | None) -> None:
    """Overwrite the stored master key.

    None LEAVES the existing entry unchanged. An empty string CLEARS it. A
    non-empty string overwrites. Written 0600 via tempfile + atomic rename.
    """
    raw = _read_raw()
    if api_key is not None:
        if api_key == "":
            raw.pop("composio_api_key", None)
        else:
            raw["composio_api_key"] = api_key
    _atomic_write(raw)


def status() -> StatusMap:
    """Dashboard-safe summary of the EFFECTIVE master key (file or env).

    Never returns the raw value — only `set`, the last 4 characters, and the
    source so the operator can tell a dashboard-pasted key from an env secret.
    """
    file_key = get()
    if file_key:
        return {"set": True, "last4": _last4(file_key), "source": "file"}
    env_key = _env_key()
    if env_key:
        return {"set": True, "last4": _last4(env_key), "source": "env"}
    return {"set": False, "last4": None, "source": None}


def store_path() -> str:
    """Absolute path to the JSON file (for diagnostics / tests)."""
    return str(_path())


__all__ = [
    "get",
    "resolve_master_key",
    "set",
    "status",
    "store_path",
    "StatusMap",
]
