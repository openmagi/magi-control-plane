"""v1-P1 — 5-tier policy source precedence.

Pattern from magi-agent harness/policy_state.py::SOURCE_PRECEDENCE (9-tier,
in-loop). Ours is 5-tier (out-of-loop terminal-gate), Literal-typed,
strictly ordered. The rule: a higher-precedence source overrides a lower one
when both define the same policy id.
"""
import pytest

from magi_cp.policy.precedence import (
    SOURCE_PRECEDENCE, source_rank, more_authoritative,
    resolve_by_id,
)


def test_precedence_literal_order_is_explicit():
    # platform > org > bot > user > session
    assert SOURCE_PRECEDENCE == ("platform", "org", "bot", "user", "session")


def test_source_rank_higher_index_is_lower_authority():
    assert source_rank("platform") < source_rank("session")
    assert source_rank("org") < source_rank("user")


def test_source_rank_rejects_unknown():
    with pytest.raises(ValueError, match="unknown policy source"):
        source_rank("model")


def test_more_authoritative_picks_higher_precedence():
    assert more_authoritative("platform", "org") == "platform"
    assert more_authoritative("session", "bot") == "bot"


def test_more_authoritative_self_returns_self():
    assert more_authoritative("user", "user") == "user"


def test_resolve_by_id_picks_highest_precedence_for_each_id():
    candidates = [
        {"id": "A", "source": "session", "verdict": "from-session"},
        {"id": "A", "source": "user",    "verdict": "from-user"},
        {"id": "A", "source": "org",     "verdict": "from-org"},
        {"id": "B", "source": "bot",     "verdict": "from-bot"},
        {"id": "B", "source": "user",    "verdict": "from-user"},
    ]
    resolved = resolve_by_id(candidates)
    assert resolved["A"]["verdict"] == "from-org"
    assert resolved["B"]["verdict"] == "from-bot"


def test_resolve_by_id_empty_input():
    assert resolve_by_id([]) == {}


def test_resolve_by_id_single_source_returns_as_is():
    candidates = [{"id": "X", "source": "user", "verdict": "v"}]
    assert resolve_by_id(candidates) == {"X": candidates[0]}
