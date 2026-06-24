"""Issue #1 P6 #13 — parity conformance: CP tighten ≡ magi-agent spawn cap.

The CP module-docstring (precedence.py:53-58) claims the tighten-only
pattern is "ported from magi-agent's spawn capability cap." magi-agent
asserts `profile_tools = profile ∩ parent_cap` over tool-name frozensets
at build time (PR #656 / `tests/test_subagent_tighten_only.py`).

This test file exercises a canonical set of `(parent_cap, child_profile)
→ expected_effective_allow` fixtures against BOTH implementations:

  (a) raw tool-name frozenset (magi-agent shape) — implemented inline
      because we don't want to depend on magi-agent at import time.
      The inline implementation is a one-liner (`frozenset.intersection`),
      so the parity assertion is really about the CP side reproducing
      that single-line semantic.
  (b) the same input wrapped as CP PermissionPolicy(permission="allow",
      pattern="Read(*)"), etc.

If the CP implementation ever diverges from magi-agent's pure
set-intersection — e.g. by reverting to verbatim pattern equality and
treating `Read(*)` ≠ `Read(*)` because of formatting drift — at least
one fixture below will catch it. Per issue #1 P6 #12, CP's algebra
operates at the (tool-name, glob-args) layer; magi-agent's operates at
the tool-name layer. The fixtures here exercise tool-name discrimination
only (allow `Read(*)` vs allow `Bash(*)`); the deeper glob fixtures live
in test_precedence_tighten.py.
"""
from __future__ import annotations

import pytest

from magi_cp.policy import (
    LooseningError, PermissionPolicy, Trigger, tighten_against,
)


# ── magi-agent reference implementation (inline) ─────────────────────


def _magi_agent_spawn_cap(parent_cap: frozenset[str],
                          child_profile: frozenset[str]) -> frozenset[str]:
    """One-line port of magi-agent's spawn cap reduction.

    Source: openmagi/magi-agent PR #656 — `profile_tools = profile ∩
    parent_cap`. The cap is enforced at build time so a child profile
    that tries to add a tool the parent doesn't grant is silently
    intersected out. There is no LooseningError analog in magi-agent
    (build-time set intersection has no notion of a 'rejected attempt');
    the equivalent in CP is the strict-mode raise + resolver-level drop.
    """
    return parent_cap & child_profile


# ── canonical parity fixtures ────────────────────────────────────────


@pytest.mark.parametrize(
    "parent_tools, child_tools, expected_effective",
    [
        # 1. Plain intersection: parent grants Read+Grep, child profile
        #    Read+Bash → expected Read only (Bash silently dropped).
        (frozenset({"Read", "Grep"}),
         frozenset({"Read", "Bash"}),
         frozenset({"Read"})),
        # 2. Child ⊂ parent: child is a strict subset → child wins
        #    intact.
        (frozenset({"Read", "Grep", "Bash"}),
         frozenset({"Read"}),
         frozenset({"Read"})),
        # 3. Parent ⊂ child: child tried to widen → parent's cap holds.
        (frozenset({"Read"}),
         frozenset({"Read", "Bash", "Grep"}),
         frozenset({"Read"})),
        # 4. Disjoint sets: child grants nothing the parent permitted →
        #    empty effective allow.
        (frozenset({"Read"}),
         frozenset({"Bash"}),
         frozenset()),
        # 5. Identical sets: no-op intersection.
        (frozenset({"Read", "Bash"}),
         frozenset({"Read", "Bash"}),
         frozenset({"Read", "Bash"})),
    ],
)
def test_parity_magi_agent_vs_cp_tool_name_intersection(
    parent_tools, child_tools, expected_effective,
):
    """Both shapes (raw frozenset, CP PermissionPolicy) must yield the
    same effective allow-set for each tool.

    (a) magi-agent shape:
        `parent_cap & child_profile == expected_effective`.
    (b) CP shape (per-tool): for every tool name in the union of
        parent and child, run tighten_against on
        `allow Tool(*) (parent)` vs `allow Tool(*) (child)` and observe
        whether the resulting policy is `allow Tool(*)` (kept) or
        whether the merge dropped the child (parent-only). The tool
        ends up in the effective allow-set iff BOTH tiers granted it.
    """
    # (a) reference shape
    assert _magi_agent_spawn_cap(parent_tools, child_tools) == expected_effective

    # (b) CP shape — per-tool. CP carries one policy per tool, so the
    # parity reduction is:
    #   effective = {t for t in parent if t in child}
    # which we model below by checking that tightening
    # `parent: allow Tool(*)` against `child: allow Tool(*)` is a
    # no-op (both tiers grant it) and tightening a parent
    # `allow Tool(*)` against `child: deny Tool(*)` (child not in
    # parent's grant set, modeled as deny) collapses to deny.
    effective_cp: set[str] = set()
    for tool in parent_tools | child_tools:
        parent_grants = tool in parent_tools
        child_grants = tool in child_tools
        if not parent_grants:
            # Parent has no rule for this tool — the CP analog is no
            # policy at all on the parent side, so the cap doesn't
            # admit the tool. The tool stays out of effective_cp.
            continue
        parent_policy = PermissionPolicy(
            id=f"tools/{tool}", description="",
            trigger=Trigger(event="PreToolUse", matcher=tool),
            permission="allow", pattern=f"{tool}(*)",
        )
        if child_grants:
            child_policy = PermissionPolicy(
                id=f"tools/{tool}", description="",
                trigger=Trigger(event="PreToolUse", matcher=tool),
                permission="allow", pattern=f"{tool}(*)",
            )
            result = tighten_against(parent_policy, child_policy, strict=True)
            assert result.permission == "allow"
            effective_cp.add(tool)
        else:
            # The child tier omits this tool. magi-agent models that as
            # the tool not being in `child_profile` → it intersects out.
            # CP can model it explicitly by having the child carry an
            # `ask` or `deny` on the same pattern — both are tighter
            # than allow under our rank, so the merge adopts the
            # tighter side. Either way the tool does NOT land in the
            # effective allow-set.
            child_policy = PermissionPolicy(
                id=f"tools/{tool}", description="",
                trigger=Trigger(event="PreToolUse", matcher=tool),
                permission="deny", pattern=f"{tool}(*)",
            )
            result = tighten_against(parent_policy, child_policy, strict=True)
            assert result.permission == "deny"

    assert effective_cp == expected_effective


def test_parity_strict_mode_models_magi_agent_silent_drop():
    """magi-agent's spawn cap silently drops out-of-cap tools (set
    intersection). CP's strict mode raises LooseningError when the
    child tries to widen the parent — the resolver catches and drops,
    which is the multi-tier analog of magi-agent's silent intersection.

    Parity assertion: a CP child that grants a tool the parent denies
    raises LooseningError under strict mode (and the resolver would
    drop it), which yields the same effective allow-set as the
    magi-agent reduction `parent_cap & child_profile = empty`."""
    parent = PermissionPolicy(
        id="bash/safe", description="",
        trigger=Trigger(event="PreToolUse", matcher="Bash"),
        permission="deny", pattern="Bash(*)",
    )
    child = PermissionPolicy(
        id="bash/safe", description="",
        trigger=Trigger(event="PreToolUse", matcher="Bash"),
        permission="allow", pattern="Bash(*)",
    )
    # Reference: cap={empty Bash allow}, profile={Bash} → intersection
    # is empty (parent doesn't grant Bash).
    assert _magi_agent_spawn_cap(frozenset(), frozenset({"Bash"})) == frozenset()
    # CP strict mode: child's attempt to grant Bash where parent denies
    # raises. The resolver catches this and the floor stands.
    with pytest.raises(LooseningError):
        tighten_against(parent, child, strict=True)


def test_parity_disjoint_tool_names_are_additive_not_loosening():
    """magi-agent's per-tool intersection is disjoint-safe: a child
    `Read` profile against a parent `Bash` cap reduces to empty
    intersection (the child tool isn't in the parent's grant). CP
    handles disjoint-tool pairings under a single policy id by
    raising on the trigger/matcher discriminator gate (different
    matcher = different surface; the resolver drops the child). Both
    paths leave the parent's tool-only floor intact — no widening, no
    silent merge across surfaces. The two algebras are equivalent
    over disjoint-tool inputs even though the rejection mechanism
    differs (silent intersection vs explicit discriminator raise)."""
    assert _magi_agent_spawn_cap(
        frozenset({"Bash"}), frozenset({"Read"})
    ) == frozenset()
    parent = PermissionPolicy(
        id="tools/x", description="",
        trigger=Trigger(event="PreToolUse", matcher="Bash"),
        permission="allow", pattern="Bash(*)",
    )
    child = PermissionPolicy(
        id="tools/x", description="",
        trigger=Trigger(event="PreToolUse", matcher="Read"),
        permission="allow", pattern="Read(*)",
    )
    with pytest.raises(ValueError, match="discriminator mismatch"):
        tighten_against(parent, child, strict=True)
