"""PR-J / DETERMINISM-1: regex requirements are keyed by (pattern, field_path).

field_path scopes WHICH field a regex matches, so regex(pattern=P,
field_path="tool_input.command") and regex(pattern=P,
field_path="tool_response.output") are DISTINCT checks. _req_key omitted
field_path, so the tighten merge's dedup collapsed them and silently dropped
one field's enforcement.
"""
from __future__ import annotations

from magi_cp.policy import (
    EvidencePolicy,
    EvidenceReq,
    Trigger,
    tighten_against,
)
from magi_cp.policy.precedence import _req_key


def test_req_key_distinguishes_regex_by_field_path():
    a = EvidenceReq(kind="regex", pattern=r"\bSSN\b", field_path="tool_input.command")
    b = EvidenceReq(kind="regex", pattern=r"\bSSN\b", field_path="tool_response.output")
    same = EvidenceReq(kind="regex", pattern=r"\bSSN\b", field_path="tool_input.command")
    assert _req_key(a) != _req_key(b)      # distinct field -> distinct key
    assert _req_key(a) == _req_key(same)   # same field -> same key


def test_tighten_merge_keeps_both_field_scoped_regex_checks():
    # Parent enforces the pattern on the command field. Child TIGHTENS by
    # additionally enforcing it on the response field (a superset: it re-states
    # the parent's check and adds one). The merge must keep BOTH field checks.
    parent = EvidencePolicy(
        id="x/v1", description="",
        trigger=Trigger(event="PreToolUse", matcher="Bash"),
        requires=[
            EvidenceReq(kind="regex", pattern=r"\bSSN\b",
                        field_path="tool_input.command"),
        ],
        action="block",
    )
    child = EvidencePolicy(
        id="x/v1", description="",
        trigger=Trigger(event="PreToolUse", matcher="Bash"),
        requires=[
            EvidenceReq(kind="regex", pattern=r"\bSSN\b",
                        field_path="tool_input.command"),
            EvidenceReq(kind="regex", pattern=r"\bSSN\b",
                        field_path="tool_response.output"),
        ],
        action="block",
    )
    result = tighten_against(parent, child)
    paths = sorted(
        r.field_path for r in result.requires if r.kind == "regex"
    )
    assert paths == ["tool_input.command", "tool_response.output"]


def test_tighten_merge_still_dedups_identical_regex():
    # Same pattern AND same field_path in both tiers must still dedup to one
    # (no double-firing), so the fix does not over-count.
    req = dict(kind="regex", pattern=r"\bSSN\b", field_path="tool_input.command")
    parent = EvidencePolicy(
        id="x/v1", description="",
        trigger=Trigger(event="PreToolUse", matcher="Bash"),
        requires=[EvidenceReq(**req)], action="block",
    )
    child = EvidencePolicy(
        id="x/v1", description="",
        trigger=Trigger(event="PreToolUse", matcher="Bash"),
        requires=[EvidenceReq(**req)], action="block",
    )
    result = tighten_against(parent, child)
    regexes = [r for r in result.requires if r.kind == "regex"]
    assert len(regexes) == 1
