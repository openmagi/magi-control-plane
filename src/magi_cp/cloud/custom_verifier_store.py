"""Custom verifier definitions store (D52b).

Tenant-scoped JSON file persistence for step-only authoring of custom
verifiers via POST /custom-verifiers. The runtime does NOT yet execute
custom verifier bodies. body_type="preview" means the runtime returns
a `not_applicable` dummy verdict when a policy binds to one of these.

Why a separate store rather than embedding in PolicyStore:

  - Authoring lifecycle is independent. Operators commonly define a
    verifier once and bind multiple policies to it; a single shared
    PolicyStore would force re-writing every policy when the verifier
    changed.
  - Per-tenant isolation is mandatory (a custom verifier authored by
    tenant A must not surface in tenant B's catalog). PolicyStore is
    single-tenant in v1; this store keys by tenant_id from day one.
  - The wire shape is small and stable. JSON-on-disk is the same pattern
    PolicyStore uses (see policy_store.py for rationale).

The on-disk shape is a single JSON document keyed by tenant_id:

  {
    "tenant-a": {
      "verifiers": [
        {"id": "abc12345...", "name": "my-custom-check", ...},
        ...
      ]
    },
    ...
  }
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Literal


VerdictStatus = Literal["pass", "fail", "needs_review", "not_applicable"]
BodyType = Literal["preview"]


_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_MAX_NAME_LEN = 64
_MAX_DESCRIPTION_LEN = 500
_ALLOWED_VERDICTS: set[VerdictStatus] = {"pass", "fail", "needs_review", "not_applicable"}
_ALLOWED_BODY_TYPES: set[BodyType] = {"preview"}


class CustomVerifierError(ValueError):
    """Raised when a verifier definition fails validation. The REST
    layer maps this to 422; lib callers see a typed exception they can
    catch on a per-field basis if they want to."""


@dataclass(frozen=True)
class CustomVerifierTrigger:
    """One CC hook event + matcher_class pair this verifier subscribes
    to. matcher_class follows the policy/payload_schemas.py vocabulary
    (`tool` / `no_tool` / `final`)."""

    event: str
    matcher_class: str


@dataclass(frozen=True)
class CustomVerifier:
    """Persisted custom verifier definition.

    `id` is server-issued (16 hex chars) so two tenants can register the
    same `name` without collision. `name` is the operator-visible slug
    (kebab-cased lowercase, regex `_NAME_RE`).
    """

    id: str
    name: str
    description: str
    triggers: tuple[CustomVerifierTrigger, ...]
    verdict_set: tuple[VerdictStatus, ...]
    body_type: BodyType
    created_at: int
    # Optional tenant binding for cross-tenant audit. Default-empty so the
    # in-memory dataclass round-trips identically for single-tenant tests.
    tenant_id: str = ""


def validate_name(name: str) -> None:
    if not isinstance(name, str):
        raise CustomVerifierError("name must be a string")
    if not name:
        raise CustomVerifierError("name is required")
    if len(name) > _MAX_NAME_LEN:
        raise CustomVerifierError(f"name must be <= {_MAX_NAME_LEN} chars")
    if not _NAME_RE.match(name):
        raise CustomVerifierError(
            "name must match /^[a-z][a-z0-9_]*$/ (lowercase, digits, underscore)",
        )


def validate_description(description: str) -> None:
    if not isinstance(description, str):
        raise CustomVerifierError("description must be a string")
    if not description.strip():
        raise CustomVerifierError("description is required")
    if len(description) > _MAX_DESCRIPTION_LEN:
        raise CustomVerifierError(
            f"description must be <= {_MAX_DESCRIPTION_LEN} chars",
        )


def validate_triggers(triggers: list[dict]) -> tuple[CustomVerifierTrigger, ...]:
    if not isinstance(triggers, list):
        raise CustomVerifierError("triggers must be a list")
    if len(triggers) == 0:
        raise CustomVerifierError("at least one trigger is required")
    out: list[CustomVerifierTrigger] = []
    for i, raw in enumerate(triggers):
        if not isinstance(raw, dict):
            raise CustomVerifierError(f"triggers[{i}]: must be an object")
        event = raw.get("event")
        matcher_class = raw.get("matcher_class")
        if not isinstance(event, str) or not event.strip():
            raise CustomVerifierError(f"triggers[{i}].event: required string")
        if matcher_class not in ("tool", "no_tool", "final"):
            raise CustomVerifierError(
                f"triggers[{i}].matcher_class: must be tool|no_tool|final",
            )
        out.append(CustomVerifierTrigger(event=event.strip(), matcher_class=matcher_class))
    return tuple(out)


def validate_verdict_set(verdict_set: list[str]) -> tuple[VerdictStatus, ...]:
    if not isinstance(verdict_set, list) or len(verdict_set) == 0:
        raise CustomVerifierError("verdict_set must contain at least one verdict")
    seen: list[VerdictStatus] = []
    for v in verdict_set:
        if v not in _ALLOWED_VERDICTS:
            raise CustomVerifierError(
                f"verdict {v!r} not allowed (pick from pass/fail/needs_review/not_applicable)",
            )
        if v not in seen:
            seen.append(v)  # type: ignore[arg-type]
    return tuple(seen)


def validate_body_type(body_type: str) -> BodyType:
    if body_type not in _ALLOWED_BODY_TYPES:
        raise CustomVerifierError(
            "body_type must be 'preview' (real-code bodies are deferred)",
        )
    return body_type  # type: ignore[return-value]


def _gen_id() -> str:
    """16 hex characters of urandom. Long enough that operator-driven
    collisions are not a concern; short enough that the dashboard can
    render the id inline without wrapping."""
    return secrets.token_hex(8)


def build_from_dict(raw: dict, *, tenant_id: str = "") -> CustomVerifier:
    """Validate-and-build path used by POST /custom-verifiers.

    Issues a fresh id; ignores any client-supplied `id` so two tenants
    cannot collide and so re-POSTing the same payload is idempotently
    creating a new row (consistent with PolicyStore.put semantics; the
    operator owns id at the policy layer, not here)."""
    if not isinstance(raw, dict):
        raise CustomVerifierError("body must be a JSON object")
    name = raw.get("name") or ""
    description = raw.get("description") or ""
    triggers = raw.get("triggers") or []
    verdict_set = raw.get("verdict_set") or []
    body_type = raw.get("body_type") or "preview"
    validate_name(name)
    validate_description(description)
    triggers_t = validate_triggers(triggers)
    verdicts_t = validate_verdict_set(verdict_set)
    body_type_t = validate_body_type(body_type)
    return CustomVerifier(
        id=_gen_id(),
        name=name,
        description=description.strip(),
        triggers=triggers_t,
        verdict_set=verdicts_t,
        body_type=body_type_t,
        created_at=int(time.time()),
        tenant_id=tenant_id,
    )


def serialize(v: CustomVerifier) -> dict:
    return {
        "id": v.id,
        "name": v.name,
        "description": v.description,
        "triggers": [
            {"event": t.event, "matcher_class": t.matcher_class}
            for t in v.triggers
        ],
        "verdict_set": list(v.verdict_set),
        "body_type": v.body_type,
        "created_at": v.created_at,
        "tenant_id": v.tenant_id,
    }


def deserialize(raw: dict) -> CustomVerifier:
    triggers = tuple(
        CustomVerifierTrigger(event=t["event"], matcher_class=t["matcher_class"])
        for t in raw.get("triggers", [])
    )
    return CustomVerifier(
        id=raw["id"],
        name=raw["name"],
        description=raw["description"],
        triggers=triggers,
        verdict_set=tuple(raw.get("verdict_set", [])),
        body_type=raw.get("body_type", "preview"),
        created_at=int(raw.get("created_at", 0)),
        tenant_id=raw.get("tenant_id", ""),
    )


class CustomVerifierStore:
    """Tenant-scoped JSON file store. Single file at `path`.

    The store loads on every call (no in-memory cache). This matches
    PolicyStore semantics and keeps the test surface simple. A write
    from one TestClient is visible to the next read without an explicit
    invalidation step.
    """

    def __init__(self, path: str):
        self.path = path

    def _load_raw(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        try:
            return json.loads(open(self.path, encoding="utf-8").read())
        except json.JSONDecodeError as e:
            raise ValueError(f"malformed custom verifier store: {e}") from e

    def _save_raw(self, raw: dict) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")

    def add(self, tenant_id: str, verifier: CustomVerifier) -> CustomVerifier:
        raw = self._load_raw()
        bucket = raw.setdefault(tenant_id, {"verifiers": []})
        # Persist the tenant_id binding so cross-tenant audits / migrations
        # do not lose it if the bucket key is the only place it lives.
        stamped = CustomVerifier(
            id=verifier.id,
            name=verifier.name,
            description=verifier.description,
            triggers=verifier.triggers,
            verdict_set=verifier.verdict_set,
            body_type=verifier.body_type,
            created_at=verifier.created_at,
            tenant_id=tenant_id,
        )
        bucket["verifiers"].append(serialize(stamped))
        self._save_raw(raw)
        return stamped

    def get(self, tenant_id: str, verifier_id: str) -> CustomVerifier | None:
        raw = self._load_raw()
        bucket = raw.get(tenant_id)
        if bucket is None:
            return None
        for row in bucket.get("verifiers", []):
            if row.get("id") == verifier_id:
                return deserialize(row)
        return None

    def list_for_tenant(self, tenant_id: str) -> list[CustomVerifier]:
        raw = self._load_raw()
        bucket = raw.get(tenant_id)
        if bucket is None:
            return []
        return [deserialize(row) for row in bucket.get("verifiers", [])]


__all__ = [
    "CustomVerifier",
    "CustomVerifierError",
    "CustomVerifierStore",
    "CustomVerifierTrigger",
    "build_from_dict",
    "deserialize",
    "serialize",
    "validate_body_type",
    "validate_description",
    "validate_name",
    "validate_triggers",
    "validate_verdict_set",
]
