"""5-tier policy source precedence.

Pattern derived (not ported) from magi-agent's 9-tier `SOURCE_PRECEDENCE`.
The 9-tier model was in-loop and included model-suggested/session-derived
sources that don't apply to an out-of-loop terminal gate. The 5-tier model
keeps only the human-authoring sources:

    platform > org > bot > user > session

- platform : magi-control-plane shipped defaults (e.g. hard safety)
- org      : organization-wide policy (set by IT/compliance)
- bot      : per-bot (Claude Code installation) policy
- user     : end-user-overridable local policy
- session  : ephemeral session-scope override

A higher-precedence source ALWAYS wins on policy-id conflict — there is no
merge semantics in v0 (kept deterministic).
"""
from __future__ import annotations
from typing import Literal


PolicySource = Literal["platform", "org", "bot", "user", "session"]
SOURCE_PRECEDENCE: tuple[PolicySource, ...] = (
    "platform", "org", "bot", "user", "session",
)


def source_rank(s: str) -> int:
    """Lower rank = higher authority. 0 is platform; 4 is session."""
    try:
        return SOURCE_PRECEDENCE.index(s)   # type: ignore[arg-type]
    except ValueError as e:
        raise ValueError(f"unknown policy source: {s!r}") from e


def more_authoritative(a: PolicySource, b: PolicySource) -> PolicySource:
    return a if source_rank(a) <= source_rank(b) else b


def resolve_by_id(candidates: list[dict]) -> dict[str, dict]:
    """Group `candidates` by `id`; for each id, keep only the highest-precedence
    entry. Stable and deterministic — same input ⇒ same output.
    """
    by_id: dict[str, dict] = {}
    for c in candidates:
        cid = c["id"]
        if cid not in by_id or source_rank(c["source"]) < source_rank(by_id[cid]["source"]):
            by_id[cid] = c
    return by_id


# ── P6: tighten-only floor semantics ─────────────────────────────────
#
# Lower-precedence policies (user/session/bot) can only NARROW what a
# higher-precedence (platform/org) policy allows — never widen it. The
# pattern is ported from magi-agent's spawn capability cap: parent caps
# child, never the other way around.
#
# `tighten_against(parent, child)` returns a new policy expressing the
# intersection of permissions / allowlists, fail-closed on conflicts.
# Callers pre-resolve precedence (parent = closest higher-rank entry,
# child = the lower-rank candidate) and feed pairs in.
def tighten_against(parent, child):
    """Intersection semantics for two policies of the SAME archetype.

    PermissionPolicy:
      - `allow` only narrows: child's allow must be a subset of parent's
        allow (verbatim string match), or the child is dropped.
      - `deny` always widens (more deny is "tighter"): result = union.
      - `ask` is treated like allow (asking is weaker than denying;
        a child that asks can't loosen a parent's deny).
      For mixed-permission pairs (one allow + one deny), the deny wins
      and the child collapses to that.

    SubagentPolicy:
      - Issue #1 P0 (#9): v1 archetype is binary (disable). Parent
        always wins; a lower-precedence policy cannot un-disable a
        subagent the parent disabled. Result is parent verbatim.

    McpGatingPolicy:
      - allow ⊆ deny: if parent denies, child stays deny. If parent
        allows and child denies, child wins (denial is tighter).

    ContextInjectionPolicy:
      - Issue #1 P1 (#4): the lower-precedence child template is
        injected BEFORE the parent's so the parent (higher-precedence)
        gets the last word in the prompt — preventing
        `session`-tier 'ignore the above safety rules' from overriding
        a `platform`-tier instruction. Child is dropped entirely when
        it equals or contains the parent template (no-op or already
        covered).

    EvidencePolicy:
      - requires[] are concatenated (more checks = tighter). action
        narrows: block > ask > audit. If parent says block, child
        cannot relax to audit.

    Type mismatch between parent + child → ValueError (callers must
    pair archetypes; the resolved-set machinery already guarantees this).
    """
    from .ir import (
        ContextInjectionPolicy, EvidencePolicy, McpGatingPolicy,
        PermissionPolicy, SubagentPolicy,
    )
    if type(parent) is not type(child):
        raise ValueError(
            f"tighten_against: archetype mismatch "
            f"{type(parent).__name__} vs {type(child).__name__}"
        )
    if isinstance(parent, PermissionPolicy):
        # If either side is deny on the same pattern → child must accept deny.
        # If parent allows X and child denies X, child wins (tighter).
        if parent.permission == "deny":
            return parent
        if child.permission == "deny":
            return child
        # Both allow/ask. Allow stays as parent.
        return parent
    if isinstance(parent, SubagentPolicy):
        # Issue #1 P0 (#9): SubagentPolicy is binary (disable). Parent
        # disable cannot be undone by a lower-precedence child.
        # Lists are rejected at construction time; this branch only
        # ever sees empty allowlists.
        return parent
    if isinstance(parent, McpGatingPolicy):
        # deny always wins.
        if parent.action == "deny" or child.action == "deny":
            return McpGatingPolicy(
                id=child.id, description=child.description,
                server=parent.server, action="deny", version=child.version,
            )
        return parent
    if isinstance(parent, ContextInjectionPolicy):
        # Issue #1 P1 (#4): tighten-only semantics. The lower-precedence
        # child template is prepended so the parent's text appears LAST
        # in the prompt — last-instruction-wins is the assumed model
        # behaviour, so a `session`-tier 'ignore the above' injected
        # below a `platform` rule can't override it. We also drop the
        # child if it's redundant (no-op).
        if (
            not child.template
            or child.template == parent.template
            or child.template in parent.template
        ):
            return parent
        merged_template = child.template + "\n\n" + parent.template
        return ContextInjectionPolicy(
            id=child.id, description=child.description,
            event=parent.event, matcher=parent.matcher,
            template=merged_template, version=child.version,
        )
    if isinstance(parent, EvidencePolicy):
        # action narrows: block > ask > audit. The parent floor stays.
        action_order = {"block": 0, "ask": 1, "audit": 2}
        chosen_action = parent.action
        if action_order.get(child.action, 99) < action_order.get(parent.action, 99):
            chosen_action = child.action
        merged_requires = list(parent.requires) + list(child.requires)
        return EvidencePolicy(
            id=child.id, description=child.description,
            trigger=parent.trigger,
            sentinel_re=child.sentinel_re or parent.sentinel_re,
            requires=merged_requires,
            action=chosen_action,
            on_signature_invalid=parent.on_signature_invalid,
            gate_binary=parent.gate_binary,
            version=child.version,
        )
    raise ValueError(f"tighten_against: unsupported type {type(parent).__name__}")
