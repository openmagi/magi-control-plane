"""pack -> policy -> rule: expand pack membership through the policy tier.

A pack's `policy_ids` list historically held RULE ids (compile units in the
PolicyStore). With the policy tier (pack -> policy -> rule), a pack member id
may ALSO be a POLICY-GROUP id (a user-authored policy that owns >=1 rule in the
PolicyGroupStore). When it is, it expands to that policy's `rule_ids` so the
pack enforces / lists / counts the policy's rules.

This is the single source of truth for that expansion, applied at EVERY site
that resolves pack membership (the runtime gate-cache feeder, the dashboard
pack status, coverage, and metrics counts) so behavior is consistent across
surfaces. Pure: no I/O.

Back-compat: a member id that is NOT a known policy-group id passes through
unchanged, so every existing pack (whose members are bare rule ids) resolves
byte-identically.
"""
from __future__ import annotations

from collections.abc import Mapping

__all__ = ["expand_pack_member_ids", "build_group_rule_index"]


def build_group_rule_index(policy_group_store) -> dict[str, list[str]]:
    """Build a {policy_group_id: [rule_id, ...]} index from the store.

    Best-effort: a None store or a load error yields an empty index, so
    membership resolution degrades to the pre-tier (bare-rule) behavior
    rather than failing the request. Loaded ONCE per request by callers,
    then handed to `expand_pack_member_ids`.
    """
    if policy_group_store is None:
        return {}
    try:
        records = policy_group_store.load()
    except Exception:  # noqa: BLE001 - membership must never fail on store IO
        return {}
    index: dict[str, list[str]] = {}
    for r in records:
        rid = getattr(r, "id", None)
        rule_ids = getattr(r, "rule_ids", None)
        if isinstance(rid, str) and rid and isinstance(rule_ids, list):
            index[rid] = [x for x in rule_ids if isinstance(x, str) and x]
    return index


def expand_pack_member_ids(
    member_ids, group_rule_index: Mapping[str, list[str]] | None,
) -> list[str]:
    """Expand pack member ids through the policy tier to rule ids.

    For each member id: if it is a key in `group_rule_index` (a policy-group
    id), emit that policy's rule ids; otherwise emit the member id unchanged
    (a bare rule id). Order-preserving and de-duplicated, so a rule reached
    via two policies (or a policy + a direct rule) appears once, in
    first-seen order.
    """
    idx = group_rule_index or {}
    out: list[str] = []
    seen: set[str] = set()
    for mid in (member_ids or []):
        if not isinstance(mid, str) or not mid:
            continue
        expanded = idx.get(mid)
        ids = expanded if expanded is not None else [mid]
        for rid in ids:
            if isinstance(rid, str) and rid and rid not in seen:
                seen.add(rid)
                out.append(rid)
    return out
