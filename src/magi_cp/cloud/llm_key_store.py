"""LLM API key store — JSON-on-disk overlay for ANTHROPIC_API_KEY / OPENAI_API_KEY.

Self-host operators paste keys into the dashboard /settings page instead of
editing `~/.magi-cp/.env` and recreating containers. The store persists those
keys to a 0600 file under `${MAGI_CP_KEY_DIR}` (next to the Ed25519 keypair
the cloud already pins there) and the LiteLLM providers consult it FIRST,
falling back to the env-vars when the store is empty.

Layout on disk (single file, `<dir>/llm-keys.json`):
  {
    "anthropic_api_key": "sk-ant-...",
    "openai_api_key": "sk-..."
  }

A key is omitted from the JSON when unset; the loader treats a missing entry
as None so an empty store is byte-identical to no-store-at-all (env-only
deployments unchanged).

Why a separate file from `keys.py`'s Ed25519 layout:
  - the Ed25519 layout is multi-key with rotation directories; LLM keys are
    single-pair overwrite-in-place
  - keeping LLM keys in their own file lets the operator delete the LLM
    overlay without touching signing keys
  - the file is 0600 (private-key parity) since it carries provider secrets

Concurrency model: the in-process admin endpoint serialises writes with the
existing `policy_lock`-style asyncio pattern (the cloud's PUT route holds a
lock around read-modify-write). Inter-process safety is not promised — there
is exactly one `magi-cp-cloud` worker per pod in the deployed shape.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import TypedDict


_FILE_NAME = "llm-keys.json"
_DEFAULT_DIR = "~/.magi-cp/cloud"


class KeyMap(TypedDict, total=False):
    anthropic: str | None
    openai: str | None


class StatusMap(TypedDict):
    anthropic_set: bool
    anthropic_last4: str | None
    openai_set: bool
    openai_last4: str | None


def _resolve_dir() -> Path:
    raw = os.environ.get("MAGI_CP_KEY_DIR") or _DEFAULT_DIR
    return Path(os.path.expanduser(raw))


def _path() -> Path:
    return _resolve_dir() / _FILE_NAME


def _last4(value: str | None) -> str | None:
    if not value:
        return None
    # Keep the last 4 visible characters. Short keys (<4 chars) return as-is
    # so the dashboard does not silently render "" for malformed entries.
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
        # Corrupt file: do NOT silently delete; treat as empty so env-var
        # fallback wins, and an operator can manually inspect/repair.
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _atomic_write(payload: dict) -> None:
    dir_path = _resolve_dir()
    dir_path.mkdir(parents=True, exist_ok=True)
    p = _path()
    # tempfile in same dir + os.replace = atomic rename within the same
    # filesystem. Pre-chmod the temp file to 0600 BEFORE the rename so the
    # file never exists on disk with broader perms.
    fd, tmp_name = tempfile.mkstemp(
        prefix=".llm-keys.", suffix=".tmp", dir=str(dir_path),
    )
    try:
        os.chmod(tmp_name, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, sort_keys=True, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, p)
        # Ensure the final file is 0600 even if the FS preserved the temp
        # file's mode bits (os.replace is supposed to keep the dest's perms
        # but defensive chmod here closes the gap on FSes that don't).
        os.chmod(p, 0o600)
    except Exception:
        # Clean up the tempfile on any failure path. NOT a finally because
        # the success path consumed it via os.replace.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def get() -> KeyMap:
    """Return {"anthropic": str|None, "openai": str|None}.

    Missing file or missing key returns None for that provider; the LiteLLM
    provider factories then fall back to ANTHROPIC_API_KEY / OPENAI_API_KEY
    env-vars (byte-identical to no-store-at-all deployments).
    """
    raw = _read_raw()
    a = raw.get("anthropic_api_key")
    o = raw.get("openai_api_key")
    return {
        "anthropic": a if isinstance(a, str) and a else None,
        "openai": o if isinstance(o, str) and o else None,
    }


def set(
    anthropic: str | None = None,
    openai: str | None = None,
) -> None:
    """Overwrite the store with the supplied keys.

    A None value LEAVES the existing entry unchanged. An empty string CLEARS
    that key (removes it from the JSON). A non-empty string overwrites.

    File is written 0600 via tempfile + atomic rename so a crash mid-write
    cannot leave a partial file.
    """
    raw = _read_raw()
    if anthropic is not None:
        if anthropic == "":
            raw.pop("anthropic_api_key", None)
        else:
            raw["anthropic_api_key"] = anthropic
    if openai is not None:
        if openai == "":
            raw.pop("openai_api_key", None)
        else:
            raw["openai_api_key"] = openai
    _atomic_write(raw)


def status() -> StatusMap:
    """Return a dashboard-safe summary.

    NEVER returns the raw key value — only a `set: bool` and the last 4
    characters of the stored value (for "is this the key I just pasted?"
    confirmation). The dashboard renders `**** {last4}` when set.
    """
    keys = get()
    return {
        "anthropic_set": bool(keys.get("anthropic")),
        "anthropic_last4": _last4(keys.get("anthropic")),
        "openai_set": bool(keys.get("openai")),
        "openai_last4": _last4(keys.get("openai")),
    }


def store_path() -> str:
    """Absolute path to the JSON file (for diagnostics / tests)."""
    return str(_path())


__all__ = ["get", "set", "status", "store_path", "KeyMap", "StatusMap"]
