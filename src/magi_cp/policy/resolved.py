"""ResolvedPolicySet — frozen typed accessor over multi-source policy overrides.

Pattern from magi-agent customize/verification_policy.py: load overrides from
persistence into a frozen dataclass + typed accessors. Two upgrades for
control-plane:

  - source tracking via PolicySource literal so precedence resolution is
    embedded in the type system (no string typos in the gate / API)
  - enabled_for_event() accessor that the gate / managed-settings compiler
    consume directly
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Iterable

from .ir import Policy
from .precedence import PolicySource, source_rank


@dataclass(frozen=True)
class PolicyOverride:
    """A single (policy, source, enabled) triple as stored in the policy store."""
    policy: Policy
    source: PolicySource
    enabled: bool = True


@dataclass(frozen=True)
class ResolvedPolicy:
    """Result of resolving overrides for one policy id."""
    policy: Policy
    source: PolicySource
    enabled: bool

    @property
    def id(self) -> str:
        return self.policy.id


@dataclass(frozen=True)
class ResolvedPolicySet:
    """Immutable post-resolution view; the gate / compiler iterate over this."""
    entries: tuple[ResolvedPolicy, ...] = field(default_factory=tuple)

    # ── factories ─────────────────────────────────────────────────
    @classmethod
    def from_overrides(cls, overrides: Iterable[PolicyOverride]) -> "ResolvedPolicySet":
        by_id: dict[str, PolicyOverride] = {}
        for ov in overrides:
            cid = ov.policy.id
            if cid not in by_id or source_rank(ov.source) < source_rank(by_id[cid].source):
                by_id[cid] = ov
        # stable ordering by precedence then id for deterministic compilation
        ordered = sorted(by_id.values(),
                         key=lambda ov: (source_rank(ov.source), ov.policy.id))
        return cls(entries=tuple(
            ResolvedPolicy(policy=ov.policy, source=ov.source, enabled=ov.enabled)
            for ov in ordered
        ))

    # ── accessors ────────────────────────────────────────────────
    def get(self, policy_id: str) -> ResolvedPolicy | None:
        for e in self.entries:
            if e.policy.id == policy_id:
                return e
        return None

    def all(self) -> tuple[ResolvedPolicy, ...]:
        return self.entries

    def enabled_for_event(self, event: str) -> Iterable[Policy]:
        for e in self.entries:
            if e.enabled and e.policy.trigger.event == event:
                yield e.policy
