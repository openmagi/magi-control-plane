"""Authoring path for the session-evidence pair: IR + compiler emission."""
from __future__ import annotations

import pytest

from magi_cp.policy.compiler import (
    DEFAULT_SESSION_AUDIT_SHIM, DEFAULT_SESSION_GATE_SHIM,
    compile_to_managed_settings,
)
from magi_cp.policy.ir import policy_from_dict, policy_to_dict


def _audit(**over):
    raw = {"type": "evidence_audit", "id": "aud1", "description": "d",
           "trigger": {"event": "PostToolUse", "matcher": "WebFetch|Bash"},
           "kind": "source_credibility"}
    raw.update(over)
    return policy_from_dict(raw)


def _pre(**over):
    raw = {"type": "evidence_precondition", "id": "pre1", "description": "d",
           "trigger": {"event": "PreToolUse", "matcher": "mcp__trading__execute_trade"},
           "require_kind": "source_credibility"}
    raw.update(over)
    return policy_from_dict(raw)


# ── IR round-trip + validation ───────────────────────────────────────
def test_audit_round_trips():
    d = policy_to_dict(_audit())
    assert d["type"] == "evidence_audit" and d["kind"] == "source_credibility"
    assert policy_to_dict(policy_from_dict(d)) == d  # stable


def test_precondition_round_trips():
    d = policy_to_dict(_pre(reason="verify first", action="ask"))
    assert d["type"] == "evidence_precondition" and d["action"] == "ask"
    assert policy_to_dict(policy_from_dict(d)) == d


def test_audit_rejects_bad_kind():
    with pytest.raises(ValueError, match="kind must match"):
        _audit(kind="Source Credibility!")


def test_audit_rejects_unknown_judge():
    with pytest.raises(ValueError, match="judge must be one of"):
        _audit(judge="gpt-vibes")


def test_precondition_rejects_bad_verdict():
    with pytest.raises(ValueError, match="require_verdict must be"):
        _pre(require_verdict="maybe")


def test_precondition_rejects_non_gate_action():
    with pytest.raises(ValueError, match="action must be block/ask"):
        _pre(action="audit")


def test_precondition_pinned_to_pretooluse():
    # The gate emits a PreToolUse decision; authoring it elsewhere would file a
    # hook whose output CC ignores (silent no-op). Reject at authoring time.
    with pytest.raises(ValueError, match="must be PreToolUse"):
        _pre(trigger={"event": "PostToolUse", "matcher": "mcp__trading__execute_trade"})


# ── compiler emission ────────────────────────────────────────────────
def test_compiles_to_the_two_binaries():
    s = compile_to_managed_settings([_audit(), _pre(reason="verify a source first")])
    post = s["hooks"]["PostToolUse"][0]
    pre = s["hooks"]["PreToolUse"][0]
    assert post["matcher"] == "WebFetch|Bash"
    assert post["hooks"][0]["command"].startswith(DEFAULT_SESSION_AUDIT_SHIM)
    assert "--kind source_credibility" in post["hooks"][0]["command"]
    assert pre["hooks"][0]["command"].startswith(DEFAULT_SESSION_GATE_SHIM)
    assert "--require-kind source_credibility" in pre["hooks"][0]["command"]
    assert "--require-verdict pass" in pre["hooks"][0]["command"]


def test_reason_is_shell_quoted():
    # A reason with shell metacharacters must be safely quoted in the hook cmd.
    s = compile_to_managed_settings([_pre(reason="don't; rm -rf $HOME `x`")])
    cmd = s["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    # the dangerous chars survive only inside a single-quoted string
    assert "rm -rf" in cmd
    import shlex
    parts = shlex.split(cmd)  # must parse without executing anything
    assert "don't; rm -rf $HOME `x`" in parts


def test_meta_lists_the_new_types():
    s = compile_to_managed_settings([_audit(), _pre()])
    types = {m["type"] for m in s["_magi_policies"]}
    assert types == {"evidence_audit", "evidence_precondition"}
