"""Script storage for D63 run_command policies.

Persists uploaded script bodies under a single directory next to the
policy store. Each script is keyed by the sha256 of its bytes; uploading
the same content twice yields the same id (idempotent on hash). A
sibling JSON index tracks the friendly name + runtime so the dashboard
can render a table of scripts without having to read every body.

Why a separate store rather than embedding the script body in the policy
JSON:

  - Bodies can be larger than the policy id surface comfortably holds
    on disk (we cap at 64KB per script body but a 4KB JSON file with
    a 64KB string field is awkward to diff / audit).
  - Multiple RunCommandPolicy rows can reference the same script body;
    dedupe-by-hash keeps the on-disk footprint flat.
  - The runtime gate reads the body from a single canonical location,
    independent of which policy fired.

Storage layout (under `dir`):

  scripts/
    <sha256>.<runtime-ext>      bytes (chmod 0644; ownership = process uid)
    index.json                  {"items": [{"id": ..., "name": ..., ...}]}

The index is the authoritative metadata source — `list()` reads it
verbatim. `get(id)` returns the index row + the body bytes path. The
delete path consults the active policy store to refuse removing a
script any RunCommandPolicy still references.

Single-tenant: D63 ships scoped to the same tenant boundary the
PolicyStore uses (one cloud = one operator's policies + scripts). The
hosted multi-tenant version is gated by `MAGI_CP_ALLOW_RUN_COMMAND=0`
in the create_app factory; the store itself has no tenant key.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Iterable, Literal


# Runtime → file extension mapping. The extension is purely cosmetic
# (the runtime is the canonical interpretation; the gate uses
# `runtime <path>` not `./<path>`), but a `.sh` / `.py` / `.js` tail
# makes the operator's sidecar dir self-describing under `ls`.
ScriptRuntime = Literal["bash", "python3", "node"]
_RUNTIME_EXT: dict[str, str] = {
    "bash": "sh",
    "python3": "py",
    "node": "js",
}
_ALLOWED_RUNTIMES: frozenset[str] = frozenset(_RUNTIME_EXT.keys())

# Hard cap on a single script body. Generous enough for any
# operator-authored hook script but small enough that we never store a
# multi-MB binary in this directory.
MAX_SCRIPT_BYTES = 64 * 1024

# Operator-visible name. Lowercase letters / digits / underscore / dash
# / dot, must start with a letter. Same shape the policy id regex uses
# (less the slash), so the same name can become a policy id segment
# unaltered.
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._\-]{0,63}$")


class ScriptStoreError(ValueError):
    """Raised on validation failures the REST layer can map to 422."""


class ScriptStoreConflict(ValueError):
    """Raised when a script name collides with an existing script for a
    DIFFERENT body. Idempotent (same name + same body) is fine — the
    store returns the existing row."""


class ScriptStoreInUseError(ValueError):
    """Raised by delete() when one or more policies still reference the
    script. Carries the policy ids on `.policy_ids` so the REST layer
    can report them back to the operator."""

    def __init__(self, message: str, policy_ids: list[str]):
        super().__init__(message)
        self.policy_ids = policy_ids


@dataclass(frozen=True)
class ScriptEntry:
    """One persisted script row.

    `id` == `hash` == sha256-hex of the body bytes. We keep them as
    separate dataclass fields because the wire surface presents both
    (the id is the URL-safe handle the dashboard uses; the hash is the
    integrity claim the dashboard can verify against a recomputed
    sha256 of a downloaded body).
    """

    id: str
    name: str
    runtime: ScriptRuntime
    size_bytes: int
    hash: str
    created_at: int


def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def validate_name(name: str) -> str:
    if not isinstance(name, str):
        raise ScriptStoreError("name must be a string")
    s = name.strip()
    if not s:
        raise ScriptStoreError("name is required")
    if not _NAME_RE.match(s):
        raise ScriptStoreError(
            "name must match /^[A-Za-z][A-Za-z0-9._\\-]{0,63}$/"
        )
    return s


def validate_runtime(runtime: str) -> ScriptRuntime:
    if runtime not in _ALLOWED_RUNTIMES:
        raise ScriptStoreError(
            f"runtime must be one of {sorted(_ALLOWED_RUNTIMES)}; "
            f"got {runtime!r}"
        )
    return runtime  # type: ignore[return-value]


def validate_body(body: bytes) -> bytes:
    if not isinstance(body, (bytes, bytearray)):
        raise ScriptStoreError("body must be bytes")
    b = bytes(body)
    if not b:
        raise ScriptStoreError("body is empty")
    if len(b) > MAX_SCRIPT_BYTES:
        raise ScriptStoreError(
            f"body too large (>{MAX_SCRIPT_BYTES} bytes; got {len(b)})"
        )
    return b


def serialize(entry: ScriptEntry) -> dict:
    return {
        "id": entry.id,
        "name": entry.name,
        "runtime": entry.runtime,
        "size_bytes": entry.size_bytes,
        "hash": entry.hash,
        "created_at": entry.created_at,
    }


def deserialize(raw: dict) -> ScriptEntry:
    return ScriptEntry(
        id=raw["id"],
        name=raw["name"],
        runtime=raw["runtime"],
        size_bytes=int(raw.get("size_bytes", 0)),
        hash=raw.get("hash", raw["id"]),
        created_at=int(raw.get("created_at", 0)),
    )


class ScriptStore:
    """On-disk script store rooted at `dir`.

    Pattern follows :class:`PolicyStore` / :class:`CustomVerifierStore`:
    a single JSON index + body files alongside. No in-memory cache; every
    call rereads disk so writes are immediately visible to subsequent
    reads inside the same test process.
    """

    def __init__(self, dir: str):
        self.dir = dir
        self.bodies_dir = os.path.join(dir, "scripts")
        self.index_path = os.path.join(self.bodies_dir, "index.json")

    # ── disk plumbing ────────────────────────────────────────────────
    def _ensure_dir(self) -> None:
        os.makedirs(self.bodies_dir, exist_ok=True)

    def _load_index(self) -> list[dict]:
        if not os.path.exists(self.index_path):
            return []
        try:
            data = json.loads(open(self.index_path, encoding="utf-8").read())
        except json.JSONDecodeError as e:
            raise ValueError(f"malformed script index: {e}") from e
        if not isinstance(data, dict):
            return []
        items = data.get("items", [])
        if not isinstance(items, list):
            return []
        return items

    def _save_index(self, items: list[dict]) -> None:
        self._ensure_dir()
        # Sort by (name, id) for stable byte output across writes.
        items_sorted = sorted(items, key=lambda r: (r.get("name", ""), r.get("id", "")))
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump({"items": items_sorted}, f, ensure_ascii=False,
                       indent=2, sort_keys=True)
            f.write("\n")

    def _body_path_for(self, entry: ScriptEntry) -> str:
        ext = _RUNTIME_EXT.get(entry.runtime, "txt")
        return os.path.join(self.bodies_dir, f"{entry.id}.{ext}")

    # ── public API ──────────────────────────────────────────────────
    def add(self, *, name: str, runtime: str, body: bytes) -> ScriptEntry:
        """Persist a script. Idempotent on (sha256, name).

        Re-uploading the SAME body under the SAME name returns the
        existing row. Uploading the SAME body under a DIFFERENT name
        returns a fresh entry pointing at the same body file (we
        keep the existing row too — the dashboard can present both
        names). Uploading a DIFFERENT body under an EXISTING name
        raises :class:`ScriptStoreConflict`.
        """
        clean_name = validate_name(name)
        runtime_t = validate_runtime(runtime)
        body_b = validate_body(body)
        digest = _sha256_hex(body_b)
        items = self._load_index()

        # Conflict: same name, different body.
        for row in items:
            if row.get("name") == clean_name and row.get("id") != digest:
                raise ScriptStoreConflict(
                    f"a script named {clean_name!r} already exists with "
                    f"a different body (id={row.get('id')!r})"
                )

        # Idempotent: same name + same body → return existing.
        for row in items:
            if row.get("name") == clean_name and row.get("id") == digest:
                return deserialize(row)

        entry = ScriptEntry(
            id=digest,
            name=clean_name,
            runtime=runtime_t,
            size_bytes=len(body_b),
            hash=digest,
            created_at=int(time.time()),
        )
        self._ensure_dir()
        # Body write is idempotent: same content overwrites itself.
        body_path = self._body_path_for(entry)
        with open(body_path, "wb") as f:
            f.write(body_b)
        items.append(serialize(entry))
        self._save_index(items)
        return entry

    def list(self) -> list[ScriptEntry]:
        out: list[ScriptEntry] = []
        for row in self._load_index():
            try:
                out.append(deserialize(row))
            except (KeyError, TypeError, ValueError):
                continue
        # Stable order: by (name, id) so the dashboard list is deterministic.
        out.sort(key=lambda e: (e.name, e.id))
        return out

    def get(self, script_id: str) -> ScriptEntry | None:
        for entry in self.list():
            if entry.id == script_id:
                return entry
        return None

    def body_path(self, script_id: str) -> str | None:
        """Absolute path on disk to the body file. None if the script
        is unknown."""
        entry = self.get(script_id)
        if entry is None:
            return None
        return self._body_path_for(entry)

    def delete(
        self,
        script_id: str,
        *,
        referenced_by: Iterable[str] = (),
    ) -> ScriptEntry | None:
        """Remove a script.

        Refuses (raises :class:`ScriptStoreInUseError`) when
        `referenced_by` contains any policy id — the caller resolves
        active references against the policy store and passes the
        ids in. This keeps the script store free of any direct
        dependency on the policy store.
        """
        refs = list(referenced_by)
        if refs:
            raise ScriptStoreInUseError(
                f"script {script_id!r} is referenced by "
                f"{len(refs)} policy(ies)",
                policy_ids=sorted(refs),
            )
        items = self._load_index()
        kept: list[dict] = []
        removed: ScriptEntry | None = None
        for row in items:
            if row.get("id") == script_id:
                try:
                    removed = deserialize(row)
                except (KeyError, ValueError):
                    pass
                continue
            kept.append(row)
        if removed is None:
            return None
        # Only unlink the body file when no other row points at the
        # same id (dedupe-by-hash means two different names could
        # share an id; deleting one name should not remove the body
        # the other name still uses).
        if not any(row.get("id") == script_id for row in kept):
            body_path = self._body_path_for(removed)
            try:
                os.unlink(body_path)
            except FileNotFoundError:
                pass
        self._save_index(kept)
        return removed


__all__ = [
    "MAX_SCRIPT_BYTES",
    "ScriptEntry",
    "ScriptRuntime",
    "ScriptStore",
    "ScriptStoreConflict",
    "ScriptStoreError",
    "ScriptStoreInUseError",
    "deserialize",
    "serialize",
    "validate_body",
    "validate_name",
    "validate_runtime",
]
