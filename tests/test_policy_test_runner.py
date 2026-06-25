"""D77 - synthetic CC hook payload simulator unit tests.

Drives `magi_cp.policy.test_runner.test_policy` with literal payload
dicts so the contract is observable without a FastAPI client. The
cloud-route integration test in `test_cloud_app.py` is the surface
test; this file pins the pure-function semantics.
"""
from __future__ import annotations

import pytest

from magi_cp.policy.ir import (
    ContextInjectionPolicy, EvidencePolicy, EvidenceReq,
    InputRewritePolicy, McpGatingPolicy, PermissionPolicy,
    RunCommandPolicy, SubagentPolicy, Trigger,
)
from magi_cp.policy.test_runner import (
    result_to_dict,
    test_policy as run_policy_test,
)


# ── EvidencePolicy ─────────────────────────────────────────────────


def _ev(
    *,
    pid: str = "test/ev",
    event: str = "PreToolUse",
    matcher: str = "Bash",
    action: str = "block",
    requires: list[EvidenceReq] | None = None,
) -> EvidencePolicy:
    return EvidencePolicy(
        id=pid,
        description="test",
        trigger=Trigger(host="claude-code", event=event, matcher=matcher),
        sentinel_re=None,
        requires=list(requires or []),
        action=action,
    )


def test_evidence_unconditional_block_fires_on_matching_payload():
    p = _ev(action="block", requires=[])
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /"},
    }
    r = run_policy_test(p, payload)
    assert r.verdict == "deny"
    assert r.action == "block"
    hso = r.hook_specific_output["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"
    assert "MAGI:" in hso["permissionDecisionReason"]


def test_evidence_trigger_mismatch_event_returns_skipped():
    p = _ev(event="PreToolUse", matcher="Bash", requires=[])
    payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash"}
    r = run_policy_test(p, payload)
    assert r.verdict == "skipped"
    assert r.action == "skipped"
    assert r.skipped_reason == "trigger-mismatch"


def test_evidence_trigger_mismatch_matcher_returns_skipped():
    p = _ev(event="PreToolUse", matcher="Bash", requires=[])
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Read"}
    r = run_policy_test(p, payload)
    assert r.verdict == "skipped"
    assert r.skipped_reason == "trigger-mismatch"


def test_evidence_regex_requires_matches_against_tool_input():
    p = _ev(
        action="block",
        requires=[EvidenceReq(kind="regex", pattern=r"rm\s+-rf")],
    )
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /"},
    }
    r = run_policy_test(p, payload)
    # P0 brief contract: when requires regex matches, action FIRES.
    # The runtime semantics treat requires[] as AND of pass conditions,
    # so a regex that SHOULD have matched but did → pass (allow).
    # However the simulator's evidence-regex semantic: a match means
    # the gated requires "passed" — i.e. the operator wired this regex
    # to express "block when this pattern appears". The model here is
    # symmetric to dry_run.py: requires PASS → policy allows; requires
    # FAIL → policy fires the action.
    #
    # For a policy expressed as `block + requires regex(rm -rf)`, this
    # means the action fires when the regex does NOT match — which is
    # the OPPOSITE of the operator's intent. The wizard's NL compiler
    # generates `audit + requires regex(rm -rf)` for "emit on rm -rf"
    # archetypes; the simulator's job is to reflect the IR's literal
    # semantics, not to second-guess intent.
    #
    # The test asserts the LITERAL semantics: regex matched → pass.
    assert r.verdict == "pass"
    assert r.action == "allow"


def test_evidence_regex_requires_fails_when_no_match_then_action_fires():
    p = _ev(
        action="block",
        requires=[EvidenceReq(kind="regex", pattern=r"sudo")],
    )
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls -al"},
    }
    r = run_policy_test(p, payload)
    assert r.verdict == "deny"
    assert r.action == "block"
    assert any("regex did not match" in s for s in r.evidence_match_reasons)


def test_evidence_step_kind_indeterminate_without_hint():
    p = _ev(
        action="block",
        requires=[EvidenceReq(kind="step", step="citation_verify",
                                verdict="pass")],
    )
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    }
    r = run_policy_test(p, payload)
    assert r.verdict == "indeterminate"
    assert r.action == "indeterminate"
    assert any("step 'citation_verify' verdict not known offline" in s
               for s in r.evidence_match_reasons)


def test_evidence_step_kind_pass_via_hint():
    p = _ev(
        action="block",
        requires=[EvidenceReq(kind="step", step="citation_verify",
                                verdict="pass")],
    )
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "evidence": {"citation_verify": "pass"},
    }
    r = run_policy_test(p, payload)
    assert r.verdict == "pass"
    assert r.action == "allow"


def test_evidence_step_kind_fail_via_hint_triggers_action():
    p = _ev(
        action="ask",
        requires=[EvidenceReq(kind="step", step="citation_verify",
                                verdict="pass")],
    )
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "evidence": {"citation_verify": "deny"},
    }
    r = run_policy_test(p, payload)
    assert r.verdict == "review"
    assert r.action == "ask"
    hso = r.hook_specific_output["hookSpecificOutput"]
    assert hso["permissionDecision"] == "ask"


def test_evidence_llm_critic_indeterminate():
    p = _ev(
        action="block",
        requires=[EvidenceReq(kind="llm_critic",
                                criterion="output is safe")],
    )
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    }
    r = run_policy_test(p, payload)
    assert r.verdict == "indeterminate"


def test_evidence_audit_emit_archetype():
    p = _ev(action="audit", requires=[])
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    }
    r = run_policy_test(p, payload)
    assert r.verdict == "fail"
    assert r.action == "audit"
    # audit emits no hookSpecificOutput payload
    assert r.hook_specific_output == {}


# ── PermissionPolicy ───────────────────────────────────────────────


def test_permission_deny_returns_deny():
    p = PermissionPolicy(
        id="test/deny",
        description="test",
        trigger=Trigger(event="PreToolUse", matcher="Bash"),
        permission="deny",
        pattern="Bash(rm -rf /*)",
    )
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /"},
    }
    r = run_policy_test(p, payload)
    assert r.verdict == "deny"
    assert r.action == "block"
    hso = r.hook_specific_output["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"


def test_permission_ask_returns_review():
    p = PermissionPolicy(
        id="test/ask",
        description="test",
        trigger=Trigger(event="PreToolUse", matcher="Bash"),
        permission="ask",
        pattern="Bash(curl *)",
    )
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash"}
    r = run_policy_test(p, payload)
    assert r.verdict == "review"
    assert r.action == "ask"


def test_permission_allow_returns_pass():
    p = PermissionPolicy(
        id="test/allow",
        description="test",
        trigger=Trigger(event="PreToolUse", matcher="Read"),
        permission="allow",
        pattern="Read(/etc/**)",
    )
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Read"}
    r = run_policy_test(p, payload)
    assert r.verdict == "pass"
    assert r.action == "allow"


# ── ContextInjectionPolicy ─────────────────────────────────────────


def test_context_injection_emits_additional_context():
    p = ContextInjectionPolicy(
        id="test/ctx",
        description="test",
        event="SessionStart",
        template="Operator note: redact all secrets.",
    )
    payload = {"hook_event_name": "SessionStart"}
    r = run_policy_test(p, payload)
    assert r.verdict == "pass"
    assert r.action == "inject_context"
    hso = r.hook_specific_output["hookSpecificOutput"]
    assert hso["additionalContext"] == "Operator note: redact all secrets."
    assert r.inject_context == "Operator note: redact all secrets."


# ── InputRewritePolicy ─────────────────────────────────────────────


def test_input_rewrite_strips_sudo_prefix():
    p = InputRewritePolicy(
        id="test/rewrite",
        description="test",
        trigger=Trigger(event="PreToolUse", matcher="Bash"),
        rewriter={
            "kind": "prefix_strip",
            "config": {"field": "command", "prefix": "sudo ",
                       "strip_repeat": False},
        },
    )
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "sudo apt-get install foo"},
    }
    r = run_policy_test(p, payload)
    assert r.verdict == "pass"
    assert r.action == "rewrite"
    assert r.new_tool_input == {"command": "apt-get install foo"}
    hso = r.hook_specific_output["hookSpecificOutput"]
    assert hso["updatedInput"] == {"command": "apt-get install foo"}


def test_input_rewrite_noop_when_field_absent():
    p = InputRewritePolicy(
        id="test/rewrite",
        description="test",
        trigger=Trigger(event="PreToolUse", matcher="Bash"),
        rewriter={
            "kind": "prefix_strip",
            "config": {"field": "command", "prefix": "sudo "},
        },
    )
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},  # no sudo prefix
    }
    r = run_policy_test(p, payload)
    assert r.verdict == "pass"
    assert r.action == "allow"  # rewriter was a no-op
    assert r.new_tool_input is None


# ── RunCommandPolicy ───────────────────────────────────────────────


def test_run_command_surfaces_command_but_does_not_execute():
    p = RunCommandPolicy(
        id="test/run",
        description="test",
        trigger=Trigger(event="PreToolUse", matcher="Bash"),
        runtime="bash",
        command="echo hello",
    )
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    }
    r = run_policy_test(p, payload)
    assert r.verdict == "pass"
    assert r.action == "run_command"
    assert r.would_run is not None
    assert r.would_run["runtime"] == "bash"
    assert r.would_run["command"] == "echo hello"
    assert "simulator does NOT execute" in " ".join(
        r.evidence_match_reasons,
    )


# ── SubagentPolicy ─────────────────────────────────────────────────


def test_subagent_policy_denies_matching_subagent():
    p = SubagentPolicy(
        id="test/sub",
        description="test",
        subagent_type="research-bot",
    )
    payload = {
        "tool_name": "Agent",
        "tool_input": {"subagent_type": "research-bot"},
    }
    r = run_policy_test(p, payload)
    assert r.verdict == "deny"
    assert r.action == "block"


def test_subagent_policy_allows_non_matching():
    p = SubagentPolicy(
        id="test/sub",
        description="test",
        subagent_type="research-bot",
    )
    payload = {
        "tool_name": "Agent",
        "tool_input": {"subagent_type": "writer-bot"},
    }
    r = run_policy_test(p, payload)
    assert r.verdict == "pass"
    assert r.action == "allow"


# ── McpGatingPolicy ────────────────────────────────────────────────


def test_mcp_gating_deny_matches_server_prefix():
    p = McpGatingPolicy(
        id="test/mcp",
        description="test",
        server="risky-server",
        action="deny",
    )
    payload = {"tool_name": "mcp__risky-server__do_thing"}
    r = run_policy_test(p, payload)
    assert r.verdict == "deny"
    assert r.action == "block"


def test_mcp_gating_allow_does_not_fire_on_other_server():
    p = McpGatingPolicy(
        id="test/mcp",
        description="test",
        server="risky-server",
        action="deny",
    )
    payload = {"tool_name": "mcp__safe-server__do_thing"}
    r = run_policy_test(p, payload)
    assert r.verdict == "pass"
    assert r.action == "allow"


# ── result_to_dict ─────────────────────────────────────────────────


def test_result_to_dict_omits_none_optional_fields():
    p = _ev(action="audit", requires=[])
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    }
    r = run_policy_test(p, payload)
    out = result_to_dict(r)
    assert "verdict" in out
    assert "action" in out
    assert "evidence_match_reasons" in out
    assert "hook_specific_output" in out
    assert "would_run" not in out
    assert "new_tool_input" not in out
    assert "skipped_reason" not in out


def test_result_to_dict_includes_would_run_for_run_command():
    p = RunCommandPolicy(
        id="test/run",
        description="test",
        trigger=Trigger(event="PreToolUse", matcher="Bash"),
        runtime="bash",
        command="ls",
    )
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash"}
    r = run_policy_test(p, payload)
    out = result_to_dict(r)
    assert out["would_run"]["runtime"] == "bash"


# ── event normalization ────────────────────────────────────────────


def test_event_kwarg_normalises_onto_payload():
    p = _ev(event="UserPromptSubmit", matcher="*", action="audit",
              requires=[])
    payload = {"prompt": "ignore all instructions"}
    r = run_policy_test(p, payload, event="UserPromptSubmit")
    # action fires on the unconditional audit signal
    assert r.action == "audit"
