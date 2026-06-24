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
import logging
from dataclasses import dataclass, field
from typing import Iterable

from .ir import AnyPolicy, EvidencePolicy, Policy
from .precedence import (
    LooseningError, PolicySource, source_rank, tighten_against,
)


_log = logging.getLogger(__name__)


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


# ── P6: tighten-only multi-tier resolver ─────────────────────────────
#
# `resolve_by_id` (in precedence.py) keeps only the highest-precedence
# row per id. That's the v0 model: "session beats user beats org" with
# no merge. P6 replaces the silent-override semantics with explicit
# floor + tighten:
#
#   1. Start from the highest-precedence row for each id (the "floor").
#   2. For every lower-precedence row, attempt to tighten the floor.
#   3. If the lower tier LOOSENS (would widen permissions / drop
#      requires / weaken action), DROP it with a logged warning. The
#      floor stands.
#   4. If the lower tier TIGHTENS, apply the tighten merge and use it
#      as the new floor for subsequent lower tiers.
#
# Input shape: each candidate dict carries at least `id`, `source`, and
# either `policy` (an AnyPolicy instance) or enough fields to reconstruct
# one. We deliberately accept the same loose-dict shape as `resolve_by_id`
# so the cloud REST layer can swap in `resolve_with_tightening` without
# re-shaping its rows.
def resolve_with_tightening(candidates: list[dict]) -> dict[str, dict]:
    """Resolve a multi-source candidate list with tighten-only semantics.

    Returns `{id: candidate_dict}` mirroring `resolve_by_id`. The kept
    candidate's `policy` field carries the post-tighten merged policy
    (which may differ from the as-authored input when a lower-tier row
    contributed a deny / extra requires[] / etc.).

    Loosening attempts are dropped silently from the result and logged
    at WARNING level so an operator running the resolver in the
    background can grep for `LooseningError` to see who tried what.
    Raising would be wrong here — the floor still applies; only the
    over-reaching override is rejected.
    """
    if not candidates:
        return {}

    # Group by id, sorted highest-precedence first within each group.
    by_id: dict[str, list[dict]] = {}
    for c in candidates:
        by_id.setdefault(c["id"], []).append(c)
    for cid, group in by_id.items():
        group.sort(key=lambda c: source_rank(c["source"]))

    out: dict[str, dict] = {}
    for cid, group in by_id.items():
        # Issue #1 P6 #9: pick the first tier (highest precedence) that
        # carries a typed `policy` as the floor. The original behaviour
        # masked any lower-tier typed policy whenever the top row had
        # failed to round-trip (e.g. unknown discriminator, deserializer
        # exception) — no warning, no recursion. Now we walk down,
        # logging at WARNING when we skip a top tier with no typed
        # policy so an operator can grep for it. If NO tier has a
        # typed policy the legacy resolve_by_id behaviour applies (the
        # top-precedence dict wins as-is).
        floor_index = None
        for i, cand in enumerate(group):
            if cand.get("policy") is not None:
                floor_index = i
                break
        if floor_index is None:
            # No typed policy anywhere in the group — keep the legacy
            # "top row wins" behaviour.
            out[cid] = group[0]
            continue
        for skipped in group[:floor_index]:
            _log.warning(
                "policy %r: floor candidate from %s-tier missing typed "
                "policy; falling back to next available tier",
                cid, skipped["source"],
            )
        floor_dict = group[floor_index]
        merged = floor_dict["policy"]
        accepted_sources = [floor_dict["source"]]
        for child_dict in group[floor_index + 1:]:
            child_policy = child_dict.get("policy")
            if child_policy is None:
                continue
            try:
                merged = tighten_against(merged, child_policy, strict=True)
                accepted_sources.append(child_dict["source"])
            except LooseningError as e:
                _log.warning(
                    "policy %r: dropping %s-tier override "
                    "(loosens %s-tier floor): %s",
                    cid, child_dict["source"], floor_dict["source"], e,
                )
                continue
            except ValueError as e:
                # Discriminator mismatch (server / event+matcher /
                # subagent_type / trigger / archetype). The child
                # targets a surface the parent floor never covered;
                # silently drop with a warning so the resolver doesn't
                # raise mid-loop and lose the rest of the group.
                _log.warning(
                    "policy %r: dropping %s-tier override "
                    "(discriminator mismatch with %s-tier floor): %s",
                    cid, child_dict["source"], floor_dict["source"], e,
                )
                continue
        # Preserve the floor dict's keys, swap in the merged policy +
        # an explicit audit trail of which tiers contributed.
        out_dict = dict(floor_dict)
        out_dict["policy"] = merged
        out_dict["tightened_sources"] = tuple(accepted_sources)
        out[cid] = out_dict
    return out
