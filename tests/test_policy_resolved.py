"""v1-P1 — ResolvedPolicySet: frozen accessor over multi-source overrides.

Pattern from magi-agent customize/verification_policy.py: load overrides
from persistence into a frozen dataclass with typed accessors. Two upgrades
for control-plane: (a) source tracking via PolicySource literal so precedence
resolution is part of the type, (b) enabled_for_event() accessor that the
gate consumes.
"""
import pytest

from magi_cp.policy.ir import Policy, Trigger, EvidenceReq
from magi_cp.policy.resolved import (
    PolicyOverride, ResolvedPolicySet,
)


def _make(id: str, *, event: str = "PreToolUse", matcher: str = "Bash",
          action: str = "block",
          sentinel: str = r"FILE_COURT_(?P<matter>[A-Za-z0-9]+)_(?P<doc_id>[A-Za-z0-9]+)"
          ) -> Policy:
    # Auto-pick a legal action for non-PreToolUse events (fail-fast now enforces matrix)
    if event == "PostToolUse" and action == "block":
        action = "audit"
    if event == "Stop":
        matcher, action = "*", "audit"
    return Policy(
        id=id, description="t", version="0.1",
        trigger=Trigger(host="claude-code", event=event, matcher=matcher),
        sentinel_re=sentinel,
        requires=[EvidenceReq(step="citation_verify", verdict="pass")],
        action=action, on_signature_invalid="deny",  # type: ignore[arg-type]
        gate_binary="/usr/local/bin/magi-gate.sh",
    )


# ── from_overrides resolves by precedence ───────────────────────────
def test_from_overrides_picks_highest_precedence():
    p_org = _make("legal-filing/v1")
    p_user = _make("legal-filing/v1", matcher="Bash")  # different content
    resolved = ResolvedPolicySet.from_overrides([
        PolicyOverride(policy=p_user, source="user", enabled=True),
        PolicyOverride(policy=p_org, source="org", enabled=True),
    ])
    assert resolved.get("legal-filing/v1").source == "org"


def test_from_overrides_enabled_flag_per_source():
    p = _make("x")
    resolved = ResolvedPolicySet.from_overrides([
        PolicyOverride(policy=p, source="org", enabled=False),   # disabled at org
        PolicyOverride(policy=p, source="user", enabled=True),   # enabled at user
    ])
    # org wins by precedence even though user wanted it enabled
    item = resolved.get("x")
    assert item.source == "org"
    assert item.enabled is False


def test_enabled_for_event_filters_by_trigger_event():
    a = _make("a", event="PreToolUse")
    b = _make("b", event="PostToolUse")
    resolved = ResolvedPolicySet.from_overrides([
        PolicyOverride(policy=a, source="org", enabled=True),
        PolicyOverride(policy=b, source="org", enabled=True),
    ])
    pre = list(resolved.enabled_for_event("PreToolUse"))
    post = list(resolved.enabled_for_event("PostToolUse"))
    assert [p.id for p in pre] == ["a"]
    assert [p.id for p in post] == ["b"]


def test_enabled_for_event_skips_disabled():
    a = _make("a", event="PreToolUse")
    resolved = ResolvedPolicySet.from_overrides([
        PolicyOverride(policy=a, source="org", enabled=False),
    ])
    assert list(resolved.enabled_for_event("PreToolUse")) == []


def test_resolved_is_frozen():
    """ResolvedPolicySet must be immutable so accessors are referentially stable."""
    a = _make("a")
    resolved = ResolvedPolicySet.from_overrides(
        [PolicyOverride(policy=a, source="org", enabled=True)]
    )
    with pytest.raises((AttributeError, TypeError)):
        resolved.entries = ()    # type: ignore[misc]


def test_get_unknown_id_returns_none():
    resolved = ResolvedPolicySet.from_overrides([])
    assert resolved.get("ghost") is None


def test_all_returns_in_precedence_order():
    pa = _make("A"); pb = _make("B")
    resolved = ResolvedPolicySet.from_overrides([
        PolicyOverride(policy=pb, source="user", enabled=True),
        PolicyOverride(policy=pa, source="platform", enabled=True),
    ])
    # platform first; user later
    sources = [e.source for e in resolved.all()]
    assert sources == ["platform", "user"]
