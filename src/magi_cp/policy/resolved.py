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

from .ir import AnyPolicy, EvidencePolicy, Policy
from .precedence import PolicySource, source_rank


@dataclass(frozen=True)
class PolicyOverride:
    """A single (policy, source, enabled) triple as stored in the policy store.

    Issue #1 P0 (#13): the `policy` field is typed as `AnyPolicy`
    (union of all archetypes), not just `EvidencePolicy`. The
    policy-store reader returns whatever `policy_from_dict` produced,
    so accepting only EvidencePolicy here would have crashed every
    P2/P3 archetype at REST list/get time.

    P8: `enforcement` is the resolved authoring-time label
    (`"enforcing"` / `"preview"` / None). Computed at PUT time from the
    `requires[].step` resolution against the live VerifierRegistry +
    vendor catalog (see `policy.step_enforcement`). `None` means
    "unresolved" — legacy on-disk rows from before P8 omit the field
    and the REST layer falls back to the lazy `_enforcement_label`
    computed off the (action, event) triple."""
    policy: AnyPolicy
    source: PolicySource
    enabled: bool = True
    enforcement: str | None = None


@dataclass(frozen=True)
class ResolvedPolicy:
    """Result of resolving overrides for one policy id."""
    policy: AnyPolicy
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

    def enabled_for_event(self, event: str) -> Iterable[AnyPolicy]:
        """Yield enabled policies whose trigger.event matches.

        Issue #1 P0 (#13): only EvidencePolicy and PermissionPolicy
        carry a `trigger`. SubagentPolicy / McpGatingPolicy are not
        event-scoped (managed-settings consumes them globally), so we
        skip them here. ContextInjectionPolicy has an `event` field but
        no `trigger` — handled via `enabled_context_injections()`.
        """
        for e in self.entries:
            if not e.enabled:
                continue
            trig = getattr(e.policy, "trigger", None)
            if trig is None:
                continue
            if getattr(trig, "event", None) == event:
                yield e.policy

    def enabled_by_archetype(self, archetype_type: str) -> Iterable[AnyPolicy]:
        """Yield enabled policies of the given `type` discriminator
        (`"evidence"`, `"permission"`, `"subagent"`, `"mcp_gating"`,
        `"context_injection"`). Lets callers select declarative-only
        policies without isinstance-dispatch in N places."""
        for e in self.entries:
            if e.enabled and getattr(e.policy, "type", None) == archetype_type:
                yield e.policy

    def enabled_evidence_policies(self) -> Iterable[EvidencePolicy]:
        for e in self.entries:
            if e.enabled and isinstance(e.policy, EvidencePolicy):
                yield e.policy
