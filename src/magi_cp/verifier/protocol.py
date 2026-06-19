"""Verifier protocol + Verdict spec + Registry.

v1.1-PA foundation. Every verifier (the 5 in this batch, the 31 future ones,
and anything users register through plugins) conforms to this shape.

The cloud signs each Verdict into a token bound to a specific verifier `step`,
so the registry is the source of truth for step→verifier resolution at the
boundary where /citation_verify and friends dispatch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Iterator, Literal, Protocol, runtime_checkable


# ── Verdict ──────────────────────────────────────────────────────────
_VERDICT_STATUS = ("pass", "review", "deny")


@dataclass(frozen=True)
class Verdict:
    """The terminal output of a verifier run.

    `pass` → token issued, gate allows.
    `review` → token issued with hitl flag, gate routes to HITL queue.
    `deny` → no token, gate blocks.
    """

    status: Literal["pass", "review", "deny"]
    reasons: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.status not in _VERDICT_STATUS:
            raise ValueError(
                f"Verdict.status must be one of {_VERDICT_STATUS}, got {self.status!r}"
            )


# Input alias kept lightweight — verifiers receive raw dicts shaped per their
# own `input_schema`. The type exists for explicit annotation at call sites.
VerifierInput = dict


# ── Enforcement label (4-tier; mirrors magi-agent preset_map.enforcement_for) ─
class Enforcement(str, Enum):
    """How a verifier's verdict participates in the runtime gate.

    Names mirror magi-agent's `enforcement_for` taxonomy so the customize
    catalog and the control-plane catalog can converge labels:

    - enforcing  → verdict directly drives PreToolUse decision
    - always_on  → runs every relevant event, never deny but produces records
    - preview    → declared but not wired to any policy gate yet (honest label)
    - capability → tool-providing verifier (registry surface, not a gate)
    """

    enforcing = "enforcing"
    always_on = "always-on"
    preview = "preview"
    capability = "capability"


# ── Verifier protocol ────────────────────────────────────────────────
@runtime_checkable
class Verifier(Protocol):
    """Structural type every registered verifier must satisfy.

    These attributes are read at registry time to populate Policy IR resolution,
    MCP `tools/list`, and the /presets catalog. A class missing any of these
    cannot register — see VerifierRegistry.register.
    """

    name: str
    step: str
    category: str
    enforcement: Enforcement
    description: str
    input_schema: dict

    def run(self, payload: VerifierInput) -> Verdict: ...


_REQUIRED_ATTRS = ("name", "step", "category", "enforcement", "description", "input_schema", "run")
_STR_ATTRS = ("name", "step", "category")   # must be non-empty strings


def _ensure_protocol_shape(v: object) -> None:
    """Catch shape errors at registration so policies can't bind to a
    half-constructed verifier later. TypeError mirrors duck-typing semantics
    (caller passed the wrong shape) rather than ValueError (data invariant).

    Covers: missing attrs, None/empty string attrs, wrong-typed enforcement.
    `runtime_checkable` Protocol only checks attribute *presence*, not type —
    so this guard adds the type check the Protocol can't enforce.
    """
    missing = [a for a in _REQUIRED_ATTRS if not hasattr(v, a)]
    if missing:
        raise TypeError(
            f"{type(v).__name__} missing required Verifier attrs: {missing}"
        )
    for attr in _STR_ATTRS:
        val = getattr(v, attr)
        if not isinstance(val, str) or not val:
            raise TypeError(
                f"{type(v).__name__}.{attr} must be a non-empty string, got {val!r}"
            )
    enf = getattr(v, "enforcement")
    if not isinstance(enf, Enforcement):
        raise TypeError(
            f"{type(v).__name__}.enforcement must be Enforcement enum, got {type(enf).__name__}"
        )


# ── Registry ─────────────────────────────────────────────────────────
class VerifierRegistry:
    """Name- and step-unique registry of verifiers.

    Policy IR uses `requires[].step` to bind a policy to a verifier. Two
    verifiers cannot share a step (the binding would be ambiguous). Two
    verifiers also cannot share a name (the /presets catalog and MCP
    `tools/list` use name as the canonical identifier).
    """

    def __init__(self) -> None:
        self._by_name: dict[str, Verifier] = {}
        self._by_step: dict[str, Verifier] = {}
        self._order: list[str] = []  # insertion order for deterministic listing

    def register(self, v: Verifier) -> None:
        _ensure_protocol_shape(v)
        if v.name in self._by_name:
            raise ValueError(f"duplicate verifier name: {v.name!r}")
        if v.step in self._by_step:
            existing = self._by_step[v.step].name
            raise ValueError(
                f"duplicate verifier step: {v.step!r} already registered by {existing!r}"
            )
        self._by_name[v.name] = v
        self._by_step[v.step] = v
        self._order.append(v.name)

    def get(self, name: str) -> Verifier | None:
        return self._by_name.get(name)

    def get_by_step(self, step: str) -> Verifier | None:
        return self._by_step.get(step)

    def all(self) -> Iterator[Verifier]:
        for name in self._order:
            yield self._by_name[name]

    def filter_by_enforcement(self, enforcement: Enforcement) -> Iterable[Verifier]:
        return (v for v in self.all() if v.enforcement == enforcement)

    def filter_by_category(self, category: str) -> Iterable[Verifier]:
        return (v for v in self.all() if v.category == category)


__all__ = [
    "Verdict",
    "VerifierInput",
    "Verifier",
    "Enforcement",
    "VerifierRegistry",
]
