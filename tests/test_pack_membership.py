"""pack -> policy -> rule membership expansion (pure resolver)."""
from __future__ import annotations

from magi_cp.policy.pack_membership import (
    build_group_rule_index, expand_pack_member_ids,
)


def test_bare_rule_ids_pass_through_unchanged():
    # No group index -> every member is a bare rule id (back-compat).
    assert expand_pack_member_ids(["a", "b", "c"], {}) == ["a", "b", "c"]
    assert expand_pack_member_ids(["a", "b"], None) == ["a", "b"]


def test_policy_group_expands_to_rule_ids():
    idx = {"verified-trade": ["verified-trade-audit", "verified-trade-gate"]}
    assert expand_pack_member_ids(["verified-trade"], idx) == [
        "verified-trade-audit", "verified-trade-gate",
    ]


def test_mixed_members_expand_and_passthrough_in_order():
    idx = {"pol": ["r1", "r2"]}
    assert expand_pack_member_ids(["x", "pol", "y"], idx) == ["x", "r1", "r2", "y"]


def test_dedup_first_seen_order():
    # r1 reached via the policy AND directly -> appears once, first-seen.
    idx = {"pol": ["r1", "r2"]}
    assert expand_pack_member_ids(["pol", "r1", "r2"], idx) == ["r1", "r2"]
    # a rule shared by two policies appears once.
    idx2 = {"p1": ["r1", "r2"], "p2": ["r2", "r3"]}
    assert expand_pack_member_ids(["p1", "p2"], idx2) == ["r1", "r2", "r3"]


def test_ignores_non_string_and_empty_members():
    idx = {"pol": ["r1"]}
    assert expand_pack_member_ids(["", None, 5, "pol"], idx) == ["r1"]  # type: ignore[list-item]


def test_group_with_empty_rule_ids_yields_nothing_for_that_member():
    idx = {"pol": []}
    assert expand_pack_member_ids(["pol", "r9"], idx) == ["r9"]


# ── index builder ──────────────────────────────────────────────────────

class _Rec:
    def __init__(self, id, rule_ids):
        self.id, self.rule_ids = id, rule_ids


class _Store:
    def __init__(self, recs):
        self._recs = recs
    def load(self):
        return self._recs


def test_build_index_from_store():
    store = _Store([
        _Rec("verified-trade", ["verified-trade-audit", "verified-trade-gate"]),
        _Rec("other", ["other-rule"]),
    ])
    idx = build_group_rule_index(store)
    assert idx == {
        "verified-trade": ["verified-trade-audit", "verified-trade-gate"],
        "other": ["other-rule"],
    }


def test_build_index_none_store_is_empty():
    assert build_group_rule_index(None) == {}


def test_build_index_survives_store_load_error():
    class _Boom:
        def load(self):
            raise RuntimeError("corrupt store")
    assert build_group_rule_index(_Boom()) == {}


def test_build_index_skips_malformed_rows():
    store = _Store([_Rec("", ["r"]), _Rec("ok", "not-a-list"), _Rec("good", ["r1"])])
    assert build_group_rule_index(store) == {"good": ["r1"]}
