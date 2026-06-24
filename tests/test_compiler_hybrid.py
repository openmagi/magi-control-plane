"""P2/P3 — hybrid compilation tests.

Cover each native-surface policy archetype (Permission / Subagent /
McpGating / ContextInjection) emitting to the right managed-settings
key, plus mixed-list composition with the existing EvidencePolicy
(gate_binary route).

Issue #1 P0 (#3, #8, #9, #10, #11) rewires expected outputs to the
shapes CC actually consumes:
  - SubagentPolicy        → permissions.deny: ["Agent(<name>)"]
  - McpGatingPolicy       → allowedMcpServers / deniedMcpServers
  - ContextInjectionPolicy → {type: "command"} hook + sidecar
  - PermissionPolicy      → permissions.{allow,deny,ask} +
                            allowManagedPermissionRulesOnly
"""
from __future__ import annotations
import json

import pytest

from magi_cp.policy import (
    ContextInjectionPolicy, EvidencePolicy, McpGatingPolicy,
    PermissionPolicy, SubagentPolicy, Trigger,
    compile_to_managed_settings, policy_from_dict, policy_to_dict,
)


# ── PermissionPolicy ──────────────────────────────────────────────────


def test_permission_policy_emits_to_deny_bucket():
    p = PermissionPolicy(
        id="block-rm-rf/v1",
        description="block destructive Bash",
        trigger=Trigger(event="PreToolUse", matcher="Bash"),
        permission="deny",
        pattern="Bash(rm -rf /*)",
    )
    ms = compile_to_managed_settings([p])
    assert ms["permissions"]["deny"] == ["Bash(rm -rf /*)"]
    assert ms["permissions"]["allow"] == []
    assert ms["permissions"]["ask"] == []
    # No gate-binary hook emitted for a declarative permission
    assert ms["hooks"] == {}
    # Issue #1 P0 (#11): exclusive=True (default) → managed-only flag.
    assert ms["allowManagedPermissionRulesOnly"] is True


def test_permission_policy_non_exclusive_omits_flag():
    p = PermissionPolicy(
        id="block-rm-rf/v1", description="",
        trigger=Trigger(event="PreToolUse", matcher="Bash"),
        permission="deny", pattern="Bash(rm -rf /*)",
        exclusive=False,
    )
    ms = compile_to_managed_settings([p])
    assert "allowManagedPermissionRulesOnly" not in ms


def test_permission_policy_allow_and_ask_emit_to_their_own_bucket():
    a = PermissionPolicy(
        id="allow-read-home/v1", description="",
        trigger=Trigger(event="PreToolUse", matcher="Read"),
        permission="allow", pattern="Read(/Users/me/**)",
    )
    b = PermissionPolicy(
        id="ask-webfetch/v1", description="",
        trigger=Trigger(event="PreToolUse", matcher="WebFetch"),
        permission="ask", pattern="WebFetch(https://*)",
    )
    ms = compile_to_managed_settings([a, b])
    assert ms["permissions"]["allow"] == ["Read(/Users/me/**)"]
    assert ms["permissions"]["ask"] == ["WebFetch(https://*)"]
    assert ms["permissions"]["deny"] == []


def test_permission_policy_rejects_unknown_permission():
    with pytest.raises(ValueError, match="permission must be one of"):
        PermissionPolicy(
            id="bad/v1", description="",
            trigger=Trigger(event="PreToolUse", matcher="Bash"),
            permission="strip",  # type: ignore[arg-type]
            pattern="Bash(*)",
        )


def test_permission_policy_rejects_empty_pattern():
    with pytest.raises(ValueError, match="pattern required"):
        PermissionPolicy(
            id="bad/v1", description="",
            trigger=Trigger(event="PreToolUse", matcher="Bash"),
            permission="deny", pattern="   ",
        )


def test_permission_policy_rejects_malformed_grammar():
    """Issue #1 P1 (#7): unanchored garbage is refused at construction."""
    with pytest.raises(ValueError, match="CC permission grammar"):
        PermissionPolicy(
            id="bad/v1", description="",
            trigger=Trigger(event="PreToolUse", matcher="Bash"),
            permission="deny", pattern="garbage((( no verb",
        )


def test_permission_policy_accepts_known_verbs():
    for pat in (
        "Bash(rm -rf /*)",
        "Read(/etc/**)",
        "WebFetch(https://api.example.com/*)",
        "mcp__github",
        "mcp__github__create_issue",
        "Agent",
        "Agent(research)",
    ):
        PermissionPolicy(
            id="ok/v1", description="",
            trigger=Trigger(event="PreToolUse", matcher="Bash"),
            permission="deny", pattern=pat,
        )


# ── SubagentPolicy ───────────────────────────────────────────────────


def test_subagent_policy_compiles_to_agent_deny():
    """Issue #1 P0 (#9): v1 SubagentPolicy is a binary disable
    expressed as `permissions.deny: ["Agent(<name>)"]`."""
    p = SubagentPolicy(
        id="disable-research/v1",
        description="research subagent: disabled fleet-wide",
        subagent_type="research",
    )
    ms = compile_to_managed_settings([p])
    assert "Agent(research)" in ms["permissions"]["deny"]
    # Top-level `agents` key no longer emitted (it isn't in the CC
    # managed-settings schema).
    assert "agents" not in ms
    assert ms["hooks"] == {}


def test_subagent_policy_rejects_tool_allowlist():
    """Issue #1 P0 (#9): no compile target for per-subagent tool
    scoping in v1. A non-empty allowlist is refused at construction."""
    with pytest.raises(ValueError, match="tool_allowlist is not compilable"):
        SubagentPolicy(
            id="bad/v1", description="",
            subagent_type="research",
            tool_allowlist=["Read", "Grep"],
        )


def test_subagent_policy_rejects_bad_subagent_name():
    with pytest.raises(ValueError, match="subagent_type"):
        SubagentPolicy(
            id="bad/v1", description="",
            subagent_type="bad name with spaces",
        )


def test_subagent_policy_rejects_non_list_allowlist():
    with pytest.raises(ValueError, match="tool_allowlist must be a list"):
        SubagentPolicy(
            id="bad/v1", description="",
            subagent_type="research",
            tool_allowlist="Read",  # type: ignore[arg-type]
        )


# ── McpGatingPolicy ──────────────────────────────────────────────────


def test_mcp_gating_policy_emits_to_denied_mcp_servers():
    """Issue #1 P0 (#10): deny compiles to `deniedMcpServers`, not the
    speculative `mcp.<server>` map."""
    p = McpGatingPolicy(
        id="deny-mcp-github/v1",
        description="GitHub MCP off",
        server="github", action="deny",
    )
    ms = compile_to_managed_settings([p])
    assert ms["deniedMcpServers"] == [{"serverName": "github"}]
    assert ms["allowedMcpServers"] == []
    assert "mcp" not in ms


def test_mcp_gating_allow_pairs_with_managed_only_flag():
    p = McpGatingPolicy(
        id="allow-mcp-fs/v1", description="",
        server="filesystem", action="allow",
    )
    ms = compile_to_managed_settings([p])
    assert ms["allowedMcpServers"] == [{"serverName": "filesystem"}]
    # Issue #1 P0 (#11): exclusive=True (default) → managed-only flag.
    assert ms["allowManagedMcpServersOnly"] is True


def test_mcp_gating_non_exclusive_allow_omits_flag():
    p = McpGatingPolicy(
        id="x/v1", description="",
        server="filesystem", action="allow", exclusive=False,
    )
    ms = compile_to_managed_settings([p])
    assert "allowManagedMcpServersOnly" not in ms


def test_mcp_gating_policy_two_servers():
    a = McpGatingPolicy(id="a/v1", description="", server="github", action="allow")
    b = McpGatingPolicy(id="b/v1", description="", server="filesystem", action="deny")
    ms = compile_to_managed_settings([a, b])
    assert ms["allowedMcpServers"] == [{"serverName": "github"}]
    assert ms["deniedMcpServers"] == [{"serverName": "filesystem"}]


def test_mcp_gating_policy_rejects_bad_action():
    with pytest.raises(ValueError, match="action must be allow"):
        McpGatingPolicy(
            id="bad/v1", description="", server="x", action="ask",  # type: ignore[arg-type]
        )


# ── ContextInjectionPolicy ───────────────────────────────────────────


def test_context_injection_emits_command_hook_and_sidecar():
    """Issue #1 P0 (#3, #8): hook entry must be a valid CC hook type.
    `write` is NOT in the CC spec — we emit `command` + a shim that
    materializes the template via `additionalContext`."""
    from magi_cp.policy.compiler import context_template_sidecars
    p = ContextInjectionPolicy(
        id="team-context/v1",
        description="Inject team standards on prompt submit",
        event="UserPromptSubmit",
        template="Follow the team coding standards: TDD, no any types.",
    )
    ms = compile_to_managed_settings([p])
    hooks = ms["hooks"]["UserPromptSubmit"]
    assert len(hooks) == 1
    assert hooks[0]["matcher"] == "*"
    entry = hooks[0]["hooks"][0]
    assert entry["type"] == "command"
    # Shim path + --event + --id arguments
    assert entry["command"].startswith("/usr/local/bin/magi-cp-context-write")
    assert "--event UserPromptSubmit" in entry["command"]
    # Sidecar bytes returned via the dedicated helper (not embedded in
    # the JSON CC consumes).
    sidecars = context_template_sidecars([p])
    assert len(sidecars) == 1
    assert "team coding standards" in next(iter(sidecars.values()))
    # The JSON blob lists the sha but never inlines the bytes.
    assert "_magi_context_templates" in ms
    assert "_magi_context_templates_bytes" not in ms


def test_context_injection_rejects_unknown_event():
    """ContextInjectionPolicy rejects events the IR does not recognize.
    The recognized surface is the full CC hook event set per
    `_SUPPORTED_EVENTS`; only outright bogus names refuse."""
    with pytest.raises(ValueError, match="not a recognized CC hook"):
        ContextInjectionPolicy(
            id="bad/v1", description="",
            event="NotARealHook",  # type: ignore[arg-type]
            template="x",
        )


def test_context_injection_accepts_full_cc_hook_surface():
    """D57f-1: ContextInjectionPolicy accepts every hook event the
    matrix recognizes. The CC hookSpecificOutput JSON schema accepts
    `additionalContext` on every event (per
    docs/architecture/claude-code-cli/08-coding-harness-internals.md:233
    — "JSON stdout returns {decision, updatedInput, additionalContext,
    continue}"), so the wizard's "Inject extra context" archetype
    routes here on every Step-1 lifecycle.

    Every event round-trips through compile_to_managed_settings into
    a hooks.<event>[] command entry naming the magi-cp-context-write
    shim — byte-identical shape across events so the shim path
    works the same for each kind."""
    from magi_cp.policy.ir import _SUPPORTED_EVENTS

    for ev in sorted(_SUPPORTED_EVENTS):
        p = ContextInjectionPolicy(
            id=f"ctx-{ev.lower()}/v1",
            description=f"context on {ev}",
            event=ev,  # type: ignore[arg-type]
            template=f"hello from {ev}",
        )
        ms = compile_to_managed_settings([p])
        hooks = ms["hooks"][ev]
        assert len(hooks) == 1
        entry = hooks[0]["hooks"][0]
        assert entry["type"] == "command"
        assert entry["command"].startswith("/usr/local/bin/magi-cp-context-write")
        assert f"--event {ev}" in entry["command"]


def test_context_injection_rejects_per_tool_matcher_on_no_tool_event():
    """D57f-1 follow-up (P1): the matrix-coherence gate added to
    `ContextInjectionPolicy.validate()` refuses (event, matcher)
    pairs where the event has no per-tool payload (SessionStart,
    SubagentStop, UserPromptSubmit, Notification, etc.). Without
    the gate, a hand-rolled IR (direct PUT, NL-compiled draft, or
    stale persisted dict) would land hooks.<event>=[{matcher: 'Bash',
    ...}] which CC silently drops (no enforcement) or refuses
    settings load (cascading fail-open).
    """
    from magi_cp.policy.ir import _SUPPORTED_EVENTS

    _TOOL_CONTEXT_EVENTS = {
        "PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch",
    }
    bad_matchers = ["Bash", "Read", "mcp__github__create_issue", "Bash|Edit"]
    for ev in sorted(_SUPPORTED_EVENTS - _TOOL_CONTEXT_EVENTS):
        for idx, m in enumerate(bad_matchers):
            with pytest.raises(ValueError, match="no per-tool matcher"):
                ContextInjectionPolicy(
                    id=f"ctx-{ev.lower()}-{idx}/v1",
                    description="bogus pairing",
                    event=ev,  # type: ignore[arg-type]
                    matcher=m,
                    template="x",
                )


def test_context_injection_rejects_garbage_matcher_class():
    """Matcher strings that don't classify (unknown tool names,
    not-mcp-shape) raise via `matcher_class_of` — same gate evidence
    policies use, so authoring a typoed tool name fails fast at
    construction instead of round-tripping into managed-settings."""
    with pytest.raises(ValueError, match="unknown matcher class"):
        ContextInjectionPolicy(
            id="ctx-garbage/v1",
            description="bogus matcher",
            event="PreToolUse",
            matcher="NotARealTool",
            template="x",
        )


def test_context_injection_accepts_tool_matcher_on_tool_context_events():
    """The four tool-context events (Pre/PostToolUse + Failure +
    Batch) still accept the full matcher set (tool / mcp_tool /
    tool_alt / wildcard) — the matrix gate only narrows the
    no-tool-context families."""
    p = ContextInjectionPolicy(
        id="ctx-bash-pre/v1", description="",
        event="PreToolUse", matcher="Bash",
        template="warn before bash",
    )
    ms = compile_to_managed_settings([p])
    assert ms["hooks"]["PreToolUse"][0]["matcher"] == "Bash"

    p2 = ContextInjectionPolicy(
        id="ctx-mcp/v1", description="",
        event="PostToolUse", matcher="mcp__github__create_issue",
        template="audit github writes",
    )
    ms2 = compile_to_managed_settings([p2])
    assert (
        ms2["hooks"]["PostToolUse"][0]["matcher"]
        == "mcp__github__create_issue"
    )


# ── EvidencePolicy backward compat ───────────────────────────────────


def test_evidence_policy_still_emits_gate_command_hook():
    p = EvidencePolicy(
        id="cite/v1", description="",
        trigger=Trigger(event="PreToolUse", matcher="Bash"),
        sentinel_re=None, requires=[], action="audit",
    )
    ms = compile_to_managed_settings([p])
    hooks = ms["hooks"]["PreToolUse"]
    assert len(hooks) == 1
    assert hooks[0]["hooks"][0]["type"] == "command"


# ── mixed-list composition ───────────────────────────────────────────


def test_mixed_list_composes_into_all_native_buckets():
    perm = PermissionPolicy(
        id="perm/v1", description="",
        trigger=Trigger(event="PreToolUse", matcher="Bash"),
        permission="deny", pattern="Bash(rm -rf /*)",
    )
    sub = SubagentPolicy(
        id="sub/v1", description="",
        subagent_type="research",
    )
    mcp = McpGatingPolicy(id="mcp/v1", description="",
                          server="github", action="deny")
    ctx = ContextInjectionPolicy(
        id="ctx/v1", description="",
        event="SessionStart", template="hello",
    )
    ev = EvidencePolicy(
        id="ev/v1", description="",
        trigger=Trigger(event="PreToolUse", matcher="WebFetch"),
        requires=[], action="audit",
    )
    ms = compile_to_managed_settings([perm, sub, mcp, ctx, ev])

    # Permission deny bucket has BOTH the explicit permission and the
    # subagent's Agent(name) auto-deny.
    assert "Bash(rm -rf /*)" in ms["permissions"]["deny"]
    assert "Agent(research)" in ms["permissions"]["deny"]
    assert ms["deniedMcpServers"] == [{"serverName": "github"}]

    # ContextInjection + EvidencePolicy both write to hooks — distinct
    # commands prove they don't clobber each other.
    assert "SessionStart" in ms["hooks"]
    assert ms["hooks"]["SessionStart"][0]["hooks"][0]["command"].startswith(
        "/usr/local/bin/magi-cp-context-write"
    )
    assert ms["hooks"]["PreToolUse"][0]["hooks"][0]["type"] == "command"

    # Meta list carries the type label per row.
    types = [r["type"] for r in ms["_magi_policies"]]
    assert types == ["permission", "subagent", "mcp_gating",
                      "context_injection", "evidence"]


def test_compiler_byte_stable_same_input_same_output():
    perm = PermissionPolicy(
        id="perm/v1", description="",
        trigger=Trigger(event="PreToolUse", matcher="Bash"),
        permission="deny", pattern="Bash(rm -rf /*)",
    )
    a = compile_to_managed_settings([perm])
    b = compile_to_managed_settings([perm])
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_duplicate_id_rejected_across_archetypes():
    perm = PermissionPolicy(
        id="dup/v1", description="",
        trigger=Trigger(event="PreToolUse", matcher="Bash"),
        permission="deny", pattern="Bash(*)",
    )
    sub = SubagentPolicy(
        id="dup/v1", description="",
        subagent_type="x",
    )
    with pytest.raises(ValueError, match="중복"):
        compile_to_managed_settings([perm, sub])


# ── round-trip via policy_from_dict / policy_to_dict ─────────────────


@pytest.mark.parametrize("policy", [
    PermissionPolicy(
        id="perm/v1", description="d",
        trigger=Trigger(event="PreToolUse", matcher="Bash"),
        permission="deny", pattern="Bash(*)",
    ),
    SubagentPolicy(
        id="sub/v1", description="d",
        subagent_type="research",
    ),
    McpGatingPolicy(id="mcp/v1", description="d",
                     server="github", action="deny"),
    ContextInjectionPolicy(
        id="ctx/v1", description="d",
        event="UserPromptSubmit", template="hello"
    ),
])
def test_policy_dict_round_trip(policy):
    serialized = policy_to_dict(policy)
    restored = policy_from_dict(serialized)
    assert policy_to_dict(restored) == serialized


def test_evidence_policy_dict_excludes_type_for_byte_stability():
    """Pre-P2 fixtures must serialize without a `type` discriminator
    so existing on-disk stores diff to zero through a round-trip."""
    ev = EvidencePolicy(
        id="ev/v1", description="",
        trigger=Trigger(event="PreToolUse", matcher="Bash"),
        requires=[], action="audit",
    )
    d = policy_to_dict(ev)
    assert "type" not in d
