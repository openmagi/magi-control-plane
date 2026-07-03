"""Compound policy expansion: one intent -> several coupled IR policies."""
from __future__ import annotations

import pytest

from magi_cp.policy.compound import (
    COMPOUND_TYPES, expand_compound_draft, is_compound_draft,
)
from magi_cp.policy.ir import policy_from_dict


def _evidence_gate_draft(**over):
    d = {
        "type": "evidence_gate",
        "id": "verified-trade",
        "description": "Require a credible source before trading",
        "kind": "source_credibility",
        "project_scope": "/Users/kevin/trading-mcp",
        "audit": {"event": "PostToolUse", "matcher": "WebFetch|Bash",
                  "extract": "url", "judge": "domain-credibility"},
        "gate": {"event": "PreToolUse", "matcher": "mcp__trading__execute_trade",
                 "action": "block", "verdict": "pass", "reason": "verify a source first"},
    }
    d.update(over)
    return d


def test_is_compound_draft():
    assert is_compound_draft({"type": "evidence_gate"}) is True
    assert is_compound_draft({"type": "evidence_audit"}) is False
    assert is_compound_draft("nope") is False
    assert set(COMPOUND_TYPES) == {"evidence_gate"}


def test_evidence_gate_expands_to_two_valid_ir_policies():
    members = expand_compound_draft(_evidence_gate_draft())
    # audit + gate + 3 ledger-protection denies (Write/Edit/Bash)
    assert [m["type"] for m in members] == [
        "evidence_audit", "evidence_precondition",
        "permission", "permission", "permission",
    ]
    assert members[0]["id"] == "verified-trade-audit"
    assert members[1]["id"] == "verified-trade-gate"
    # joined on kind
    assert members[0]["kind"] == members[1]["require_kind"] == "source_credibility"
    # scope propagated to the audit/gate pair
    assert members[0]["project_scope"] == members[1]["project_scope"] == "/Users/kevin/trading-mcp"
    # the denies protect the ledger dir
    assert all("session-evidence" in m["pattern"] for m in members[2:])
    # each member is valid IR
    for m in members:
        policy_from_dict(m)  # raises on invalid


def test_protect_ledger_can_be_disabled():
    members = expand_compound_draft(_evidence_gate_draft(protect_ledger=False))
    assert [m["type"] for m in members] == ["evidence_audit", "evidence_precondition"]


def test_expand_uses_defaults_for_missing_fields():
    members = expand_compound_draft({"type": "evidence_gate",
                                     "gate": {"matcher": "mcp__x__y"}})
    assert members[0]["kind"] == "source_credibility"      # default kind
    assert members[0]["trigger"]["matcher"] == "WebFetch|Bash"
    assert members[1]["action"] == "block"
    for m in members:
        policy_from_dict(m)


def test_expand_rejects_non_compound():
    with pytest.raises(ValueError, match="not a compound"):
        expand_compound_draft({"type": "evidence_audit"})
    with pytest.raises(ValueError, match="must be an object"):
        expand_compound_draft("x")
