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
InputAssembly = Literal["cc_stdin", "caller_assembled"]


_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_MAX_NAME_LEN = 64
_MAX_DESCRIPTION_LEN = 500
_ALLOWED_VERDICTS: set[VerdictStatus] = {"pass", "fail", "needs_review", "not_applicable"}
_ALLOWED_BODY_TYPES: set[BodyType] = {"preview"}
# D57c: input-assembly contract for custom verifiers. Same vocabulary
# as the built-in descriptors (see verifier/descriptors.py). The
# `caller_assembly_hint` prose is bounded by the same cell budget as
# the field_check description so the dashboard notice render stays
# predictable.
_ALLOWED_INPUT_ASSEMBLY: set[InputAssembly] = {"cc_stdin", "caller_assembled"}
_MAX_CALLER_ASSEMBLY_HINT_LEN = 500
# Upper bound on triggers per verifier. Mirrors the spirit of
# MAX_CITATIONS_PER_REQUEST in app.py: keep the persisted row bounded so
# list / serialize stay cheap and a misuse (e.g. 10k duplicated triggers
# under the 256KB body cap) cannot silently inflate disk reads.
_MAX_TRIGGERS = 32
# D52d: per-row caps for field_checks. `_MAX_FIELD_CHECKS` mirrors
# `_MAX_TRIGGERS` so a single verifier cannot inflate disk reads
# beyond a few KB; `_MAX_FIELD_CHECK_PATH_LEN` and
# `_MAX_FIELD_CHECK_DESC_LEN` match the dashboard cell budgets so the
# tree render stays predictable.
_MAX_FIELD_CHECKS = 32
_MAX_FIELD_CHECK_PATH_LEN = 128
_MAX_FIELD_CHECK_DESC_LEN = 200


def _allowed_events() -> set[str]:
    """Canonical CC hook event vocabulary, sourced from D47 payload
    schema registry.

    Imported lazily so this module stays free of a top-level import
    cycle (policy/payload_schemas already imports from policy/*; the
    cloud layer drags in the cloud package which depends on policy).
    """
    from ..policy.payload_schemas import PAYLOAD_SCHEMAS_BY_EVENT
    return set(PAYLOAD_SCHEMAS_BY_EVENT.keys())


class CustomVerifierError(ValueError):
    """Raised when a verifier definition fails validation. The REST
    layer maps this to 422; lib callers see a typed exception they can
    catch on a per-field basis if they want to."""


class CustomVerifierConflict(ValueError):
    """Raised when a verifier name collides with an existing row for the
    same tenant. The REST layer maps this to 409 Conflict (mirroring the
    HITL one-shot 409 convention)."""


@dataclass(frozen=True)
class CustomVerifierTrigger:
    """One CC hook event + matcher_class pair this verifier subscribes
    to. matcher_class follows the policy/payload_schemas.py vocabulary
    (`tool` / `no_tool` / `final`)."""

    event: str
    matcher_class: str


@dataclass(frozen=True)
class CustomVerifierFieldCheck:
    """D52d: one (path, check_description) pair documenting what this
    verifier inspects on each fire. Same shape as the catalog
    descriptor `FieldCheck` so the dashboard can reuse a single render
    component across built-in + custom rows.

    `path` is a CC stdin payload path (e.g. `tool_input.url`);
    `check_description` is human-readable prose. Both are persisted as
    plain strings. The runtime does not interpret them today (custom
    verifiers are preview-only), but the catalog surface uses them
    immediately.
    """

    path: str
    check_description: str


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
    # D52d: per-field check rows. Empty default so older on-disk JSON
    # round-trips through deserialize() without crashing; the REST POST
    # path requires >=1 row.
    field_checks: tuple[CustomVerifierFieldCheck, ...] = field(default_factory=tuple)
    # D57c: input-assembly contract. Default `cc_stdin` so older
    # on-disk JSON (written before D57c) deserializes to the same
    # behaviour the dashboard already assumed. New rows POSTed via
    # /custom-verifiers can opt into `caller_assembled` so authors
    # documenting a recipe-driven wrapper get the matching notice.
    input_assembly: InputAssembly = "cc_stdin"
    # D57c: optional prose explaining the caller's role for
    # caller_assembled rows. Blank on cc_stdin rows.
    caller_assembly_hint: str = ""


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
    if len(triggers) > _MAX_TRIGGERS:
        raise CustomVerifierError(
            f"at most {_MAX_TRIGGERS} triggers per verifier"
        )
    allowed_events = _allowed_events()
    seen: set[tuple[str, str]] = set()
    out: list[CustomVerifierTrigger] = []
    for i, raw in enumerate(triggers):
        if not isinstance(raw, dict):
            raise CustomVerifierError(f"triggers[{i}]: must be an object")
        event = raw.get("event")
        matcher_class = raw.get("matcher_class")
        if not isinstance(event, str) or not event.strip():
            raise CustomVerifierError(f"triggers[{i}].event: required string")
        event_clean = event.strip()
        if event_clean not in allowed_events:
            raise CustomVerifierError(
                f"triggers[{i}].event: unknown event {event_clean!r}; "
                f"pick one of: {sorted(allowed_events)}"
            )
        if matcher_class not in ("tool", "no_tool", "final"):
            raise CustomVerifierError(
                f"triggers[{i}].matcher_class: must be tool|no_tool|final",
            )
        key = (event_clean, matcher_class)
        if key in seen:
            # Silently dedupe — author intent of "same trigger twice"
            # is ambiguous but never useful. Keep first occurrence.
            continue
        seen.add(key)
        out.append(CustomVerifierTrigger(event=event_clean, matcher_class=matcher_class))
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


def validate_field_checks(
    field_checks: list[dict],
) -> tuple[CustomVerifierFieldCheck, ...]:
    """D52d: validate the field_checks rows on a /custom-verifiers POST.

    Requires >=1 row (the dashboard tree needs something to render and
    a verifier that documents nothing is useless to authors); caps row
    count and per-row string lengths to bound disk + render budgets.

    Duplicate (path, check_description) pairs are silently deduped,
    same intent as `validate_triggers` for (event, matcher_class).
    """
    if not isinstance(field_checks, list) or len(field_checks) == 0:
        raise CustomVerifierError(
            "at least one field_check is required",
        )
    if len(field_checks) > _MAX_FIELD_CHECKS:
        raise CustomVerifierError(
            f"at most {_MAX_FIELD_CHECKS} field_checks per verifier",
        )
    seen: set[tuple[str, str]] = set()
    out: list[CustomVerifierFieldCheck] = []
    for i, raw in enumerate(field_checks):
        if not isinstance(raw, dict):
            raise CustomVerifierError(
                f"field_checks[{i}]: must be an object",
            )
        path = raw.get("path")
        desc = raw.get("check_description")
        if not isinstance(path, str) or not path.strip():
            raise CustomVerifierError(
                f"field_checks[{i}].path: required string",
            )
        if len(path) > _MAX_FIELD_CHECK_PATH_LEN:
            raise CustomVerifierError(
                f"field_checks[{i}].path: must be <= "
                f"{_MAX_FIELD_CHECK_PATH_LEN} chars",
            )
        if not isinstance(desc, str) or not desc.strip():
            raise CustomVerifierError(
                f"field_checks[{i}].check_description: required string",
            )
        if len(desc) > _MAX_FIELD_CHECK_DESC_LEN:
            raise CustomVerifierError(
                f"field_checks[{i}].check_description: must be <= "
                f"{_MAX_FIELD_CHECK_DESC_LEN} chars",
            )
        path_clean = path.strip()
        desc_clean = desc.strip()
        key = (path_clean, desc_clean)
        if key in seen:
            continue
        seen.add(key)
        out.append(CustomVerifierFieldCheck(
            path=path_clean, check_description=desc_clean,
        ))
    return tuple(out)


def validate_input_assembly(
    input_assembly: str, caller_assembly_hint: str,
) -> tuple[InputAssembly, str]:
    """D57c: validate (input_assembly, caller_assembly_hint) pair.

    Mirrors the same invariant the built-in descriptors enforce at
    import time:
      - input_assembly must be one of {cc_stdin, caller_assembled}
      - caller_assembled rows must carry a non-empty hint (otherwise
        the dashboard renders an empty notice block, which is worse
        than no notice at all)
      - cc_stdin rows must leave the hint blank (otherwise the
        dashboard would show a notice on a verifier whose runtime
        actually reads CC stdin directly, misleading the operator)

    The hint is bounded by `_MAX_CALLER_ASSEMBLY_HINT_LEN` so a
    runaway paragraph cannot inflate the persisted row beyond a
    predictable disk + render budget.
    """
    if input_assembly not in _ALLOWED_INPUT_ASSEMBLY:
        raise CustomVerifierError(
            f"input_assembly must be one of "
            f"{sorted(_ALLOWED_INPUT_ASSEMBLY)}",
        )
    if not isinstance(caller_assembly_hint, str):
        raise CustomVerifierError(
            "caller_assembly_hint must be a string",
        )
    # D57c follow-up (validation-consistency): canonicalize the hint
    # via strip() and key BOTH the cc_stdin-must-be-blank and the
    # length check off the same stripped value. The prior wire
    # behaviour was a confusing mix: length check on the raw value
    # (a 501-char string with whitespace at the ends rejected even
    # though the persisted content was <=500), blank check on the
    # stripped value (a whitespace-only "   " hint silently accepted
    # on cc_stdin even though the message said "drop the hint"). The
    # current shape: server normalizes via strip(), and the
    # normalized hint is what we check + persist.
    hint_clean = caller_assembly_hint.strip()
    if len(hint_clean) > _MAX_CALLER_ASSEMBLY_HINT_LEN:
        raise CustomVerifierError(
            f"caller_assembly_hint must be <= "
            f"{_MAX_CALLER_ASSEMBLY_HINT_LEN} chars",
        )
    if input_assembly == "caller_assembled" and not hint_clean:
        raise CustomVerifierError(
            "caller_assembled rows must carry a non-empty "
            "caller_assembly_hint that names the assembler "
            "(recipe / regex / prompt step) and the keys it posts",
        )
    if input_assembly == "cc_stdin" and hint_clean:
        raise CustomVerifierError(
            "cc_stdin rows must leave caller_assembly_hint blank; "
            "drop the hint or switch to caller_assembled",
        )
    return input_assembly, hint_clean  # type: ignore[return-value]


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
    field_checks = raw.get("field_checks") or []
    # D57c: input_assembly is optional on the wire (defaults to
    # cc_stdin) so a pre-D57c client posting an otherwise valid body
    # is not broken. The Pydantic body model still accepts the field
    # so authors who want caller_assembled can opt in.
    input_assembly_raw = raw.get("input_assembly") or "cc_stdin"
    caller_assembly_hint_raw = raw.get("caller_assembly_hint") or ""
    validate_name(name)
    validate_description(description)
    triggers_t = validate_triggers(triggers)
    verdicts_t = validate_verdict_set(verdict_set)
    body_type_t = validate_body_type(body_type)
    field_checks_t = validate_field_checks(field_checks)
    input_assembly_t, caller_assembly_hint_t = validate_input_assembly(
        input_assembly_raw, caller_assembly_hint_raw,
    )
    return CustomVerifier(
        id=_gen_id(),
        name=name,
        description=description.strip(),
        triggers=triggers_t,
        verdict_set=verdicts_t,
        body_type=body_type_t,
        created_at=int(time.time()),
        tenant_id=tenant_id,
        field_checks=field_checks_t,
        input_assembly=input_assembly_t,
        caller_assembly_hint=caller_assembly_hint_t,
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
        "field_checks": [
            {"path": fc.path, "check_description": fc.check_description}
            for fc in v.field_checks
        ],
        # D57c: round-trip the input-assembly pair so the dashboard
        # can render the matching notice on the catalog row.
        "input_assembly": v.input_assembly,
        "caller_assembly_hint": v.caller_assembly_hint,
    }


def deserialize(raw: dict) -> CustomVerifier:
    """Build a CustomVerifier from one persisted row.

    Defensive against partial writes / hand-edits of the on-disk JSON:
    missing or wrong-shape required keys raise CustomVerifierError so
    callers (list_for_tenant / get) can choose to skip the row rather
    than crash the entire route with KeyError → uncaught 500.
    """
    if not isinstance(raw, dict):
        raise CustomVerifierError("verifier row must be a JSON object")
    try:
        rid = raw["id"]
        rname = raw["name"]
        rdesc = raw["description"]
    except KeyError as e:
        raise CustomVerifierError(f"verifier row missing required key: {e}") from e
    if not isinstance(rid, str) or not isinstance(rname, str) or not isinstance(rdesc, str):
        raise CustomVerifierError(
            "verifier row id/name/description must be strings"
        )
    try:
        triggers = tuple(
            CustomVerifierTrigger(event=t["event"], matcher_class=t["matcher_class"])
            for t in raw.get("triggers", [])
        )
    except (KeyError, TypeError) as e:
        raise CustomVerifierError(f"verifier row has malformed trigger: {e}") from e
    # D52d: tolerate legacy on-disk rows that lack field_checks. The
    # POST path requires >=1; rows persisted before D52d will round-
    # trip with an empty tuple, the dashboard renders the preview note.
    try:
        field_checks = tuple(
            CustomVerifierFieldCheck(
                path=fc["path"],
                check_description=fc["check_description"],
            )
            for fc in raw.get("field_checks", [])
        )
    except (KeyError, TypeError) as e:
        raise CustomVerifierError(
            f"verifier row has malformed field_check: {e}",
        ) from e
    # D57c: tolerate legacy rows that lack input_assembly /
    # caller_assembly_hint. Older rows default to `cc_stdin` + blank
    # hint (the pre-D57c behaviour the dashboard already assumed).
    raw_assembly = raw.get("input_assembly", "cc_stdin")
    if raw_assembly not in _ALLOWED_INPUT_ASSEMBLY:
        # Hand-rolled JSON with an unknown value falls back to
        # cc_stdin rather than refusing to load the row; the catalog
        # surface stays useful while the operator inspects the file.
        raw_assembly = "cc_stdin"
    raw_hint = raw.get("caller_assembly_hint", "")
    if not isinstance(raw_hint, str):
        raw_hint = ""
    return CustomVerifier(
        id=rid,
        name=rname,
        description=rdesc,
        triggers=triggers,
        verdict_set=tuple(raw.get("verdict_set", [])),
        body_type=raw.get("body_type", "preview"),
        created_at=int(raw.get("created_at", 0)),
        tenant_id=raw.get("tenant_id", ""),
        field_checks=field_checks,
        input_assembly=raw_assembly,  # type: ignore[arg-type]
        caller_assembly_hint=raw_hint,
    )


def _safe_deserialize(raw: dict) -> CustomVerifier | None:
    """deserialize() wrapper that returns None instead of raising for
    malformed rows. Used by the list/get paths so one bad row never
    causes the entire route to 500 — the dashboard stays useful while
    the operator inspects the on-disk file."""
    try:
        return deserialize(raw)
    except CustomVerifierError:
        return None


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
        """Persist a new verifier under `tenant_id`.

        Treat (tenant_id, name) as the natural key: re-POSTing the same
        name for the same tenant raises CustomVerifierConflict so the
        REST layer can return 409 instead of silently accumulating
        duplicate rows the operator has no PUT/DELETE route to clean up.
        Server-generated id stays opaque.
        """
        raw = self._load_raw()
        bucket = raw.setdefault(tenant_id, {"verifiers": []})
        for existing in bucket.get("verifiers", []):
            if existing.get("name") == verifier.name:
                raise CustomVerifierConflict(
                    f"a verifier named {verifier.name!r} already exists for this tenant"
                )
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
            field_checks=verifier.field_checks,
            # D57c: carry the input-assembly pair across the stamp so
            # the persisted row + the response body keep the
            # author-supplied values. Dropping these here was the
            # silent-drift bug that made `caller_assembled` POSTs
            # round-trip as `cc_stdin`.
            input_assembly=verifier.input_assembly,
            caller_assembly_hint=verifier.caller_assembly_hint,
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
                return _safe_deserialize(row)
        return None

    def list_for_tenant(self, tenant_id: str) -> list[CustomVerifier]:
        raw = self._load_raw()
        bucket = raw.get(tenant_id)
        if bucket is None:
            return []
        out: list[CustomVerifier] = []
        for row in bucket.get("verifiers", []):
            v = _safe_deserialize(row)
            if v is not None:
                out.append(v)
        return out


__all__ = [
    "CustomVerifier",
    "CustomVerifierConflict",
    "CustomVerifierError",
    "CustomVerifierFieldCheck",
    "CustomVerifierStore",
    "CustomVerifierTrigger",
    "InputAssembly",
    "build_from_dict",
    "deserialize",
    "serialize",
    "validate_body_type",
    "validate_description",
    "validate_field_checks",
    "validate_input_assembly",
    "validate_name",
    "validate_triggers",
    "validate_verdict_set",
]
