"""D77 - synthetic CC hook payload simulator unit tests.

Drives `magi_cp.policy.test_runner.test_policy` with literal payload
dicts so the contract is observable without a FastAPI client. The
cloud-route integration test in `test_cloud_app.py` is the surface
test; this file pins the pure-function semantics.
"""
from __future__ import annotations


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
#
# Brief P2 #5 fix: PermissionPolicy compiles to managed-settings
# `permissions.{allow,deny,ask}` and CC's permission engine matches
# the pattern via its internal grammar. The simulator does NOT
# re-implement that grammar; it returns INDETERMINATE with a per-
# archetype explanation (mirroring dry_run.py's
# `archetype-not-dry-runnable` honesty posture).


def test_permission_deny_returns_indeterminate_with_explanation():
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
    assert r.verdict == "indeterminate"
    assert r.action == "indeterminate"
    assert r.skipped_reason == "declarative-archetype-cc-owned"
    # The explanation must surface that CC owns the decision and the
    # pattern that would compile into managed-settings.
    joined = " ".join(r.evidence_match_reasons)
    assert "PermissionPolicy" in joined
    assert "Bash(rm -rf /*)" in joined
    assert "deny" in joined.lower()


def test_permission_ask_returns_indeterminate():
    p = PermissionPolicy(
        id="test/ask",
        description="test",
        trigger=Trigger(event="PreToolUse", matcher="Bash"),
        permission="ask",
        pattern="Bash(curl *)",
    )
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash"}
    r = run_policy_test(p, payload)
    assert r.verdict == "indeterminate"
    assert r.skipped_reason == "declarative-archetype-cc-owned"


def test_permission_allow_returns_indeterminate():
    p = PermissionPolicy(
        id="test/allow",
        description="test",
        trigger=Trigger(event="PreToolUse", matcher="Read"),
        permission="allow",
        pattern="Read(/etc/**)",
    )
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Read"}
    r = run_policy_test(p, payload)
    assert r.verdict == "indeterminate"
    assert r.skipped_reason == "declarative-archetype-cc-owned"


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
#
# Brief P2 #6 fix: SubagentPolicy + McpGatingPolicy also return
# INDETERMINATE because CC's Agent dispatch + MCP server gating happen
# in places the hook payload does not authoritatively cover.


def test_subagent_policy_returns_indeterminate():
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
    assert r.verdict == "indeterminate"
    assert r.action == "indeterminate"
    assert r.skipped_reason == "declarative-archetype-cc-owned"


def test_subagent_policy_returns_indeterminate_for_non_matching_too():
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
    assert r.verdict == "indeterminate"
    assert r.skipped_reason == "declarative-archetype-cc-owned"


# ── McpGatingPolicy ────────────────────────────────────────────────


def test_mcp_gating_returns_indeterminate():
    p = McpGatingPolicy(
        id="test/mcp",
        description="test",
        server="risky-server",
        action="deny",
    )
    payload = {"tool_name": "mcp__risky-server__do_thing"}
    r = run_policy_test(p, payload)
    assert r.verdict == "indeterminate"
    assert r.action == "indeterminate"
    assert r.skipped_reason == "declarative-archetype-cc-owned"


def test_mcp_gating_returns_indeterminate_for_other_server_too():
    p = McpGatingPolicy(
        id="test/mcp",
        description="test",
        server="risky-server",
        action="deny",
    )
    payload = {"tool_name": "mcp__safe-server__do_thing"}
    r = run_policy_test(p, payload)
    assert r.verdict == "indeterminate"
    assert r.skipped_reason == "declarative-archetype-cc-owned"


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


# ── P2 #5 fix: payload's hook_event_name wins over caller event ─────


def test_payload_event_overrides_caller_event_kwarg():
    """An operator hand-edits the JSON to a different event; the
    panel still posts the template's default event. The simulator
    MUST prefer the payload value so the operator's edit is honoured.
    """
    p = _ev(event="PostToolUse", matcher="Bash", action="audit",
              requires=[])
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    }
    # Caller-passed event is the template default; the JSON edit wins.
    r = run_policy_test(p, payload, event="PreToolUse")
    # Trigger frame is PostToolUse, payload is PostToolUse → fires.
    assert r.action == "audit"


# ── P2 #6 fix: trigger frame fail-closed when no event supplied ────


def test_no_event_supplied_returns_skipped_with_clear_reason():
    """Both the caller's event AND the payload lack hook_event_name.
    The runtime gate would never reach this policy without an event;
    emitting a fabricated verdict would lie."""
    p = _ev(event="PreToolUse", matcher="Bash", action="block",
              requires=[])
    payload = {"tool_input": {"command": "rm -rf /"}}  # no hook_event_name
    r = run_policy_test(p, payload)
    assert r.verdict == "skipped"
    assert r.skipped_reason == "no-event-supplied"


# ── P2 #9 fix: tool-context event missing tool_name → indeterminate ─


def test_tool_context_event_missing_tool_name_returns_skipped():
    """The policy targets a tool-context event but the payload omits
    tool_name. CC always populates tool_name on this event family at
    runtime; the simulator surfaces the gap rather than silently
    admit wildcard matchers as a hit. Matcher='*' + action='audit'
    is the only triple legal for PreToolUse wildcard."""
    p = _ev(event="PreToolUse", matcher="*", action="audit", requires=[])
    payload = {"hook_event_name": "PreToolUse"}  # tool_name missing
    r = run_policy_test(p, payload)
    assert r.verdict == "skipped"
    assert r.skipped_reason == "payload-missing-tool-name"


# ── P1 #1 + #3 fix: deny shape is the gate's canonical shape ───────


def test_evidence_deny_shape_byte_equal_to_gate_emit():
    """The simulator's hook_specific_output MUST be byte-equal to the
    runtime gate's `_emit_deny_payload` for the same reason + event
    (P1 review wire-shape drift). We hit the regex-fail path which
    propagates the verifier reason into the deny shape."""
    from magi_cp.local.gate import _emit_deny_payload
    p = _ev(action="block", requires=[
        EvidenceReq(kind="regex", pattern=r"sudo"),
    ])
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls -al"},
    }
    r = run_policy_test(p, payload)
    assert r.verdict == "deny"
    # The reason comes from the first failing requires entry, not a
    # policy-id boilerplate.
    expected_reason_substring = "regex did not match"
    hso_reason = r.hook_specific_output["hookSpecificOutput"][
        "permissionDecisionReason"
    ]
    assert expected_reason_substring in hso_reason
    # Pre-side hook uses hookSpecificOutput.permissionDecision shape.
    expected_shape = _emit_deny_payload(
        hso_reason.removeprefix("MAGI: "),
        hook_event_name="PreToolUse",
    )
    assert r.hook_specific_output == expected_shape


def test_evidence_deny_shape_uses_top_level_decision_for_post_tool():
    """PostToolUse / PostToolUseFailure / PostToolBatch must use the
    top-level `{decision, reason}` shape, NOT
    hookSpecificOutput.permissionDecision (the channel CC reads on
    this event family). Drift would mean the dashboard panel lies."""
    p = _ev(event="PostToolUse", matcher="Bash", action="block",
              requires=[EvidenceReq(kind="regex", pattern=r"never")])
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "tool_response": {"output": "foo"},
    }
    r = run_policy_test(p, payload)
    assert r.verdict == "deny"
    # Top-level decision shape, NOT hookSpecificOutput.
    assert r.hook_specific_output["decision"] == "block"
    assert "reason" in r.hook_specific_output
    assert r.hook_specific_output["reason"].startswith("MAGI:")
    assert "hookSpecificOutput" not in r.hook_specific_output


# ── P1 #2 fix: scoped regex resolves dict leaves via runtime helper ─


def test_evidence_regex_field_path_resolves_dict_leaf_same_as_runtime():
    """Brief P1 #2: scoped regex against a dict leaf (e.g.
    field_path='tool_input') must format the value identically to the
    runtime via `_format_value_for_prompt`. Same policy + same
    payload → same verdict at the simulator and at /verify_inline.
    """
    p = _ev(
        action="block",
        requires=[EvidenceReq(
            kind="regex", pattern=r'"command"\s*:',
            field_path="tool_input",
        )],
    )
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /"},
    }
    r = run_policy_test(p, payload)
    # Regex matches the JSON-formatted dict leaf, requires passes,
    # action does NOT fire (allow).
    assert r.verdict == "pass"
    assert r.action == "allow"


def test_evidence_regex_field_path_missing_fails_with_clear_reason():
    """P1 #2 corollary: field absent on the payload → deny with a
    clear "field absent" reason, byte-equal to /verify_inline's
    "field <path> absent from payload"."""
    p = _ev(
        action="block",
        requires=[EvidenceReq(
            kind="regex", pattern=r"never",
            field_path="tool_response.output",
        )],
    )
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    }
    r = run_policy_test(p, payload)
    assert r.verdict == "deny"
    assert any(
        "absent from payload" in s for s in r.evidence_match_reasons
    )


# ── P1 #4 fix: unscoped regex does NOT see tool_response ───────────


def test_unscoped_regex_mirrors_verify_inline_projection():
    """Brief P1 #4: the simulator MUST mirror /verify_inline's
    unscoped projection (text → JSON dump) byte-for-byte. The runtime
    projects `text` or JSON dump. The fixture has no `text` so
    projection = JSON dump of the whole payload; substring 'passwd'
    appears in the JSON dump (tool_response.output contains it),
    therefore the regex DOES match.

    Pre-D77-fix, the simulator's projection over-included
    tool_response strings via concatenation; an operator authoring an
    unscoped regex would see DIFFERENT verdicts at simulator vs
    runtime. This test pins the byte-equal contract.
    """
    p = _ev(
        event="PostToolUse",
        action="block",
        requires=[EvidenceReq(kind="regex", pattern=r"passwd")],
    )
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls /etc"},
        "tool_response": {"output": "group\nhosts\npasswd\nshadow\n"},
    }
    # Whole-payload projection = JSON dump; 'passwd' appears in it.
    r = run_policy_test(p, payload)
    assert r.verdict == "pass"  # regex matched → requires passed → allow


# ── multi-requires honesty ─────────────────────────────────────────


def test_multi_requires_returns_indeterminate_with_breakdown():
    """Brief P2 #7: multi-requires policies cannot be honestly
    replayed entry-by-entry (mirrors dry_run.py); pin headline to
    indeterminate but keep the per-entry breakdown for the operator."""
    p = _ev(action="block", requires=[
        EvidenceReq(kind="regex", pattern=r"sudo"),
        EvidenceReq(kind="step", step="citation_verify", verdict="pass"),
    ])
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "sudo rm -rf /"},
    }
    r = run_policy_test(p, payload)
    assert r.verdict == "indeterminate"
    assert r.action == "indeterminate"
    assert r.skipped_reason == "multi-requires-not-replayable"
    # Per-entry breakdown is still surfaced so the operator can see
    # how each requires entry would have evaluated.
    assert len(r.requires_results) == 2
    statuses = {rr["status"] for rr in r.requires_results}
    assert "pass" in statuses  # regex match
    assert "indeterminate" in statuses  # step without hint
