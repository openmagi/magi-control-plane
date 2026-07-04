"""B1: _build_compile_context derives producer liveness from GROUND TRUTH
(member-rule enabled state), not the stale PolicyRecord.enabled, and indexes
standalone evidence_audit rules (IF-07, IF-09, CV-09)."""
from __future__ import annotations

from magi_cp.cloud.routes.compile import _build_compile_context


class _Rec:
    def __init__(self, id, draft, rule_ids, enabled=True):
        self.id, self.draft, self.rule_ids, self.enabled = id, draft, rule_ids, enabled


class _Pol:
    def __init__(self, id, type=None, kind=None):
        self.id, self.type, self.kind = id, type, kind


class _Ov:
    def __init__(self, pol, enabled=True):
        self.policy, self.enabled = pol, enabled


class _Store:
    def __init__(self, rows):
        self._rows = rows
    def load(self):
        return self._rows


def _gate_rec(id="verified-trade", kind="source_credibility", **kw):
    return _Rec(id, {"type": "evidence_gate", "kind": kind}, [f"{id}-audit", f"{id}-gate"], **kw)


def test_producer_live_when_member_rule_enabled():
    groups = _Store([_gate_rec(enabled=True)])
    rules = _Store([_Ov(_Pol("verified-trade-audit"), enabled=True),
                    _Ov(_Pol("verified-trade-gate"), enabled=True)])
    ctx = _build_compile_context(groups, rules)
    assert ctx["audit_kinds"]["source_credibility"] == ["verified-trade"]


def test_producer_not_offered_when_member_rules_disabled_despite_stale_record():
    # PolicyRecord.enabled is STALE True (pack cascade never synced it), but
    # the member rules are disabled -> not a live producer (IF-07).
    groups = _Store([_gate_rec(enabled=True)])
    rules = _Store([_Ov(_Pol("verified-trade-audit"), enabled=False),
                    _Ov(_Pol("verified-trade-gate"), enabled=False)])
    ctx = _build_compile_context(groups, rules)
    assert ctx["audit_kinds"] == {}


def test_reuser_policy_is_not_a_producer():
    rec = _Rec("reuser", {"type": "evidence_gate", "kind": "source_credibility",
                          "emit_audit": False}, ["reuser-gate"])
    groups = _Store([rec])
    rules = _Store([_Ov(_Pol("reuser-gate"), enabled=True)])
    assert _build_compile_context(groups, rules)["audit_kinds"] == {}


def test_standalone_evidence_audit_rule_is_a_producer():
    # A REST-authored standalone audit rule providing the kind (CV-09).
    groups = _Store([])
    rules = _Store([_Ov(_Pol("my-audit", type="evidence_audit",
                             kind="source_credibility"), enabled=True)])
    ctx = _build_compile_context(groups, rules)
    assert ctx["audit_kinds"]["source_credibility"] == ["my-audit"]


def test_standalone_audit_owned_by_group_not_double_counted():
    # The compound's own audit member must not also appear as a standalone.
    groups = _Store([_gate_rec()])
    rules = _Store([
        _Ov(_Pol("verified-trade-audit", type="evidence_audit",
                 kind="source_credibility"), enabled=True),
        _Ov(_Pol("verified-trade-gate"), enabled=True),
    ])
    ctx = _build_compile_context(groups, rules)
    # only the group id, not the member rule id
    assert ctx["audit_kinds"]["source_credibility"] == ["verified-trade"]


def test_disabled_standalone_audit_not_a_producer():
    groups = _Store([])
    rules = _Store([_Ov(_Pol("my-audit", type="evidence_audit",
                             kind="source_credibility"), enabled=False)])
    assert _build_compile_context(groups, rules)["audit_kinds"] == {}


def test_none_stores_yield_empty_context():
    assert _build_compile_context(None, None) == {"audit_kinds": {}}


def test_falls_back_to_record_enabled_without_rule_store():
    # Back-compat: with no rule store, fall back to PolicyRecord.enabled.
    groups = _Store([_gate_rec(enabled=True)])
    assert _build_compile_context(groups, None)["audit_kinds"]["source_credibility"] \
        == ["verified-trade"]
    groups_off = _Store([_gate_rec(enabled=False)])
    assert _build_compile_context(groups_off, None)["audit_kinds"] == {}
