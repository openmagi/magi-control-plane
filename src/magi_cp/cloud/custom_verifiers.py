"""Custom (user-authored) verifiers — the "verifier" layer.

Built-in verifiers (`magi_cp/verifier/builtins.py`) ship 5 hardcoded
checks. Operators in the hosted instance and self-hosters alike often
need their own — a tenant-specific regex, an LLM judge with the
operator's grading rubric, a SHACL shape over their structured output.
This module is the persistent store for those user-authored verifiers.

Architecture
------------
- Storage: `custom_verifiers` table (SQLAlchemy + same engine as the
  rest of the cloud).
- Authoring: kind-tagged config blob. v1 ships `kind="regex"` only;
  `kind="llm_judge"` and `kind="shacl"` are reserved.
- Runtime integration: `materialise_for_registry()` walks the table and
  returns instances satisfying the `Verifier` protocol. Cloud `/presets`
  endpoint merges these into its response so the dashboard's "Rules"
  surface shows built-ins + custom side by side.
- Auth: HMAC-signed admin routes (same envelope as `/admin/tenants`).
- Tenant scope: every row carries `tenant_id`. The built-in registry is
  global; custom verifiers are per-tenant.

Why this is in the cloud module (not `magi_cp/verifier/`)
---------------------------------------------------------
Built-in verifiers are deployment-time CODE; they live with the runtime
they target. Custom verifiers are runtime-time DATA persisted in the
cloud's DB. The cloud module owns the table + admin endpoints; the
verifier module continues to own the protocol.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Boolean, Column, Index, Integer, String, Text, select
from sqlalchemy.orm import Session

from .db import Base
from ..verifier.protocol import Enforcement, Verdict, VerifierRegistry


_VALID_KIND = ("regex",)
_VALID_CATEGORY = (
    "ANSWER", "FACT", "CODING", "TASK",
    "OUTPUT", "RESEARCH", "MEMORY", "SECURITY",
)
_VALID_ON_MATCH = ("deny", "review")
_STEP_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_NAME_MAX = 128
_DESC_MAX = 1024
_REASON_MAX = 256
_REASONS_LIMIT = 8
_PATTERN_MAX = 1024


# ── ORM table ───────────────────────────────────────────────────────
class CustomVerifier(Base):  # type: ignore[misc]
    """One row per user-authored verifier.

    Primary key is `(tenant_id, step)` — two tenants can use the same
    step name without colliding, but within a tenant the step is the
    binding key from `Policy.requires[].step`.
    """
    __tablename__ = "custom_verifiers"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    step = Column(String(64), nullable=False)
    name = Column(String(128), nullable=False)
    category = Column(String(32), nullable=False)
    description = Column(Text, nullable=False, default="")
    kind = Column(String(32), nullable=False, default="regex")
    config_json = Column(Text, nullable=False, default="{}")
    enabled = Column(Boolean, nullable=False, default=True)
    ts_created = Column(Integer, nullable=False, default=lambda: int(time.time()))
    ts_updated = Column(Integer, nullable=False, default=lambda: int(time.time()))

    __table_args__ = (
        Index(
            "ux_custom_verifiers_tenant_step",
            "tenant_id", "step",
            unique=True,
        ),
    )


# ── DTOs ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class RegexConfig:
    """v1 verifier kind. Pattern must compile under Python `re`; on a
    successful match the verifier returns `on_match` ('deny' or 'review')
    with the supplied reasons; otherwise 'pass'."""
    pattern: str
    on_match: str
    reasons: list[str]

    def validate(self) -> None:
        if not self.pattern or len(self.pattern) > _PATTERN_MAX:
            raise ValueError("regex pattern empty or > 1024 chars")
        try:
            re.compile(self.pattern)
        except re.error as e:
            raise ValueError(f"regex pattern does not compile: {e}") from e
        if self.on_match not in _VALID_ON_MATCH:
            raise ValueError(f"on_match must be one of {_VALID_ON_MATCH}, got {self.on_match!r}")
        if len(self.reasons) > _REASONS_LIMIT:
            raise ValueError(f"too many reasons ({len(self.reasons)} > {_REASONS_LIMIT})")
        for r in self.reasons:
            if not isinstance(r, str) or len(r) > _REASON_MAX:
                raise ValueError(f"reason must be str ≤ {_REASON_MAX} chars")


@dataclass(frozen=True)
class CustomVerifierSpec:
    """Inbound payload from the admin POST/PATCH endpoints. Validated by
    `validate()` before any DB write."""
    step: str
    name: str
    category: str
    description: str
    kind: str
    config: dict
    enabled: bool

    def validate(self) -> None:
        if not _STEP_RE.match(self.step):
            raise ValueError(f"step must match {_STEP_RE.pattern!r}")
        if not self.name or len(self.name) > _NAME_MAX:
            raise ValueError(f"name empty or > {_NAME_MAX} chars")
        if self.category not in _VALID_CATEGORY:
            raise ValueError(f"category must be one of {_VALID_CATEGORY}")
        if len(self.description) > _DESC_MAX:
            raise ValueError(f"description > {_DESC_MAX} chars")
        if self.kind not in _VALID_KIND:
            raise ValueError(f"kind must be one of {_VALID_KIND}, got {self.kind!r}")
        if self.kind == "regex":
            _coerce_regex(self.config).validate()


def _coerce_regex(config: dict) -> RegexConfig:
    pattern = config.get("pattern")
    on_match = config.get("on_match", "deny")
    reasons = config.get("reasons") or []
    if not isinstance(pattern, str):
        raise ValueError("config.pattern must be a string")
    if not isinstance(on_match, str):
        raise ValueError("config.on_match must be a string")
    if not isinstance(reasons, list):
        raise ValueError("config.reasons must be a list of strings")
    return RegexConfig(pattern=pattern, on_match=on_match, reasons=reasons)


# ── Repo ────────────────────────────────────────────────────────────
class CustomVerifierRepo:
    """Thin wrapper around the SQLAlchemy session for the CRUD endpoints
    and the runtime materialise routine."""

    def __init__(self, engine):  # type: ignore[no-untyped-def]
        self._engine = engine

    def list(self, tenant_id: str) -> list[CustomVerifier]:
        with Session(self._engine) as s:
            stmt = (
                select(CustomVerifier)
                .where(CustomVerifier.tenant_id == tenant_id)
                .order_by(CustomVerifier.step)
            )
            return list(s.scalars(stmt).all())

    def get(self, tenant_id: str, step: str) -> CustomVerifier | None:
        with Session(self._engine) as s:
            stmt = (
                select(CustomVerifier)
                .where(
                    CustomVerifier.tenant_id == tenant_id,
                    CustomVerifier.step == step,
                )
            )
            return s.scalars(stmt).one_or_none()

    def upsert(self, tenant_id: str, spec: CustomVerifierSpec) -> CustomVerifier:
        spec.validate()
        now = int(time.time())
        with Session(self._engine) as s:
            existing = s.scalars(
                select(CustomVerifier)
                .where(
                    CustomVerifier.tenant_id == tenant_id,
                    CustomVerifier.step == spec.step,
                )
            ).one_or_none()
            if existing is None:
                row = CustomVerifier(
                    tenant_id=tenant_id,
                    step=spec.step,
                    name=spec.name,
                    category=spec.category,
                    description=spec.description,
                    kind=spec.kind,
                    config_json=json.dumps(spec.config, ensure_ascii=False),
                    enabled=spec.enabled,
                    ts_created=now,
                    ts_updated=now,
                )
                s.add(row)
            else:
                existing.name = spec.name
                existing.category = spec.category
                existing.description = spec.description
                existing.kind = spec.kind
                existing.config_json = json.dumps(spec.config, ensure_ascii=False)
                existing.enabled = spec.enabled
                existing.ts_updated = now
                row = existing
            s.commit()
            s.refresh(row)
            return row

    def set_enabled(self, tenant_id: str, step: str, enabled: bool) -> CustomVerifier:
        now = int(time.time())
        with Session(self._engine) as s:
            row = s.scalars(
                select(CustomVerifier)
                .where(
                    CustomVerifier.tenant_id == tenant_id,
                    CustomVerifier.step == step,
                )
            ).one_or_none()
            if row is None:
                raise KeyError(f"custom verifier {step!r} not found for tenant {tenant_id!r}")
            row.enabled = enabled
            row.ts_updated = now
            s.commit()
            s.refresh(row)
            return row

    def delete(self, tenant_id: str, step: str) -> None:
        with Session(self._engine) as s:
            row = s.scalars(
                select(CustomVerifier)
                .where(
                    CustomVerifier.tenant_id == tenant_id,
                    CustomVerifier.step == step,
                )
            ).one_or_none()
            if row is None:
                raise KeyError(f"custom verifier {step!r} not found for tenant {tenant_id!r}")
            s.delete(row)
            s.commit()


# ── Runtime adapter ────────────────────────────────────────────────
class _RegexCustomVerifier:
    """Adapter that exposes a user-authored regex check as a
    `Verifier`. Constructed per-request; the compiled regex is held on
    the instance so subsequent runs reuse it.

    NOT registered into the global `VerifierRegistry` (which is
    deployment-singleton and tenant-agnostic). The cloud's
    `verify_dispatch` resolves a step name by first asking the registry
    and then falling back to a tenant-scoped lookup that returns one of
    these adapters.
    """

    def __init__(self, row: CustomVerifier):
        cfg = _coerce_regex(json.loads(row.config_json))
        self.name = f"custom_{row.step}"
        self.step = row.step
        self.category = row.category
        # `preview` keeps these distinct from `enforcing` built-ins in
        # the catalog — a deliberate signal that they're user-authored.
        self.enforcement = Enforcement.preview if not row.enabled else Enforcement.enforcing
        self.description = row.description or f"Custom regex check ({row.kind})"
        self.input_schema: dict[str, Any] = {
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string"}},
        }
        self._pattern = re.compile(cfg.pattern)
        self._on_match = cfg.on_match
        self._reasons = cfg.reasons

    def run(self, payload: dict) -> Verdict:
        text = payload.get("text") or ""
        if not isinstance(text, str):
            return Verdict(status="deny", reasons=["payload.text not a string"])
        if self._pattern.search(text):
            reasons = list(self._reasons) or ["custom verifier matched"]
            return Verdict(
                status=self._on_match,  # type: ignore[arg-type]
                reasons=reasons,
            )
        return Verdict(status="pass", reasons=[])


def materialise_for_tenant(repo: CustomVerifierRepo, tenant_id: str) -> list[_RegexCustomVerifier]:
    """Walk the tenant's enabled custom verifiers and return runtime
    adapters. Skips disabled rows + any with a kind we don't yet
    implement (forward-compat for `llm_judge` etc.)."""
    out: list[_RegexCustomVerifier] = []
    for row in repo.list(tenant_id):
        if not row.enabled:
            continue
        if row.kind != "regex":
            continue
        try:
            out.append(_RegexCustomVerifier(row))
        except (ValueError, re.error):
            # Skip rows whose config no longer compiles — log only;
            # don't break the listing endpoint over one bad row.
            continue
    return out


def resolve_step_for_tenant(
    builtin: VerifierRegistry | None,
    repo: CustomVerifierRepo,
    tenant_id: str,
    step: str,
) -> Any:
    """Tenant-aware verifier lookup used by `verify_dispatch`. Tries the
    built-in registry first (deployment globals win), then falls back to
    the tenant's custom rows."""
    if builtin is not None:
        b = builtin.get_by_step(step)
        if b is not None:
            return b
    row = repo.get(tenant_id, step)
    if row is None or not row.enabled:
        return None
    if row.kind != "regex":
        return None
    try:
        return _RegexCustomVerifier(row)
    except (ValueError, re.error):
        return None
