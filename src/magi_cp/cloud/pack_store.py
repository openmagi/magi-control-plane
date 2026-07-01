"""D75: user-pack registry.

Pack metadata for built-in packs lives in `policy/pack.py` (immutable
catalog). User packs are operator-authored and persist as a JSON file
under `policy_store_dir/packs.json`. Built-in vs user is keyed by id
prefix:

  - `pack/<slug>`       : built-in (read-only membership).
  - `user-pack/<slug>`  : user (POST / PUT / DELETE allowed).

The store handles ONLY user packs. The cloud layer merges built-in
packs (from policy/pack.py) and user packs (from this store) for the
GET surface.

Pattern mirrors `policy_store.py`:

  - load → list of dict rows.
  - save → byte-stable sort-by-id, two-space indent, trailing newline.
  - no incremental writes; rewrite on every save (small file, simple
    semantics, easy to diff in git).
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass


_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")


@dataclass
class UserPackRow:
    id: str
    name: str
    description: str
    policy_ids: list[str]
    # P1 pack-centric runtime: the tenant's floor pack is the "always-on"
    # bundle every session inherits regardless of activation. Exactly one
    # row may carry `is_floor=True`; PackStore.save enforces the
    # invariant. Default False so pre-P1 rows load unchanged.
    is_floor: bool = False


class PackStore:
    """JSON-file backed user-pack store. One file per tenant install.

    Multi-tenant deployments scope this by using a tenant-specific dir
    when constructing the policy_store path; today the install ships
    single-tenant (same shape as policy_store).
    """

    def __init__(self, path: str):
        self.path = path

    def load(self) -> list[UserPackRow]:
        if not os.path.exists(self.path):
            return []
        raw = json.loads(open(self.path, encoding="utf-8").read())
        if not isinstance(raw, list):
            raise ValueError(f"malformed pack store: expected list, got {type(raw)!r}")
        out: list[UserPackRow] = []
        floor_seen = False
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                raise ValueError(f"pack store item {i}: not an object")
            try:
                pid = item["id"]
                name = item["name"]
                description = item.get("description", "")
                policy_ids = item.get("policy_ids", [])
            except KeyError as e:
                raise ValueError(f"pack store item {i}: missing {e}") from e
            if not isinstance(pid, str) or not pid.startswith("user-pack/"):
                raise ValueError(
                    f"pack store item {i}: id must start with user-pack/"
                )
            if not isinstance(policy_ids, list):
                raise ValueError(
                    f"pack store item {i}: policy_ids must be a list"
                )
            raw_is_floor = item.get("is_floor", False)
            if not isinstance(raw_is_floor, bool):
                raise ValueError(
                    f"pack store item {i}: is_floor must be a bool"
                )
            if raw_is_floor:
                if floor_seen:
                    # A corrupt on-disk file with two is_floor rows must
                    # fail loudly — the gate uses the floor pack to
                    # decide what always fires, so two floors would
                    # silently confuse the resolution.
                    raise ValueError(
                        f"pack store item {i}: duplicate is_floor row"
                    )
                floor_seen = True
            out.append(UserPackRow(
                id=pid,
                name=str(name),
                description=str(description),
                policy_ids=[str(x) for x in policy_ids],
                is_floor=raw_is_floor,
            ))
        return out

    def save(self, rows: list[UserPackRow]) -> None:
        # Invariant: at most one row per store may carry is_floor=True.
        # Callers get a hard ValueError so mutation code paths cannot
        # silently persist two floors.
        floor_rows = [r for r in rows if getattr(r, "is_floor", False)]
        if len(floor_rows) > 1:
            raise ValueError(
                "pack store may hold at most one is_floor row; "
                f"found {len(floor_rows)}: {[r.id for r in floor_rows]!r}"
            )
        rows_sorted = sorted(rows, key=lambda r: r.id)
        body = [
            {
                "id": r.id,
                "name": r.name,
                "description": r.description,
                "policy_ids": list(r.policy_ids),
                "is_floor": bool(getattr(r, "is_floor", False)),
            }
            for r in rows_sorted
        ]
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        # Atomic write: P5 elevated packs.json to a governance-load-bearing
        # file (the floor pack decides what always fires, and it is read on
        # every /session/{id}/resolved call AND written by the boot
        # migration). A truncate-in-place write leaves a partially-written
        # file if the process crashes mid-dump, which would break BOTH the
        # boot migration and the live floor read (json parse error / the
        # duplicate-is_floor guard). Write to a sibling temp file, fsync,
        # then os.replace() onto the target so a crash can never expose a
        # torn packs.json.
        fd, tmp_path = tempfile.mkstemp(
            dir=directory, prefix=".packs-", suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(
                    body, f, ensure_ascii=False, indent=2, sort_keys=True,
                )
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def validate_user_slug(slug: str) -> str:
    """Slug regex: `[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?`. Same shape the
    rest of the cloud surface uses for short ids (no whitespace, no
    path separators, no leading/trailing dash). Empty / oversize
    rejected here so the route handler can 422 with one shape.
    """
    if not isinstance(slug, str) or not slug:
        raise ValueError("slug must be a non-empty string")
    if len(slug) > 80:
        raise ValueError("slug too long (max 80)")
    if not _SLUG_RE.match(slug):
        raise ValueError(
            "slug must match [a-z0-9][a-z0-9_-]*[a-z0-9] (lowercase, "
            "no whitespace, no leading/trailing dash)"
        )
    return slug


def slugify_name(name: str) -> str:
    """Best-effort slug from a free-text pack name. Falls back to
    'pack' if the result would be empty. Operators can override by
    passing `slug` explicitly on POST.
    """
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9_-]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    if not s:
        return "pack"
    return s[:80]


__all__ = [
    "PackStore",
    "UserPackRow",
    "validate_user_slug",
    "slugify_name",
]
