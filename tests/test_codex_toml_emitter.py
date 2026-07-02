"""P1 Codex adapter: ``requirements.toml`` emitter byte-stability.

Design brief: docs/plans/2026-06-30-codex-runtime-adapter-design.md
Section 3.2 + Section 6.2. The emitter mirrors
``compile_to_managed_settings``: pure, byte-stable, and order-invariant
(events + matchers sorted). These tests pin a golden requirements.toml +
hooks.json for a fixed policy list, then assert a reordered list produces
byte-identical output.
"""
from __future__ import annotations

import json

import pytest

import tomllib

from magi_cp.policy.codex_toml_emitter import (
    CODEX_GATE_COMMAND,
    CODEX_HOOK_TIMEOUT_MS,
    CODEX_PERMISSION_PROFILE,
    compile_to_codex_requirements,
)
from magi_cp.policy.ir import (
    ContextInjectionPolicy,
    EvidencePolicy,
    EvidenceReq,
    McpGatingPolicy,
    PermissionPolicy,
    SubagentPolicy,
    Trigger,
)


def _perm(pid: str, pattern: str, permission: str) -> PermissionPolicy:
    return PermissionPolicy(
        id=pid, description="t",
        trigger=Trigger(host="claude-code", event="PreToolUse", matcher="Bash"),
        permission=permission, pattern=pattern,
    )


def _evidence(pid: str, *, event="PreToolUse", matcher="Bash") -> EvidencePolicy:
    return EvidencePolicy(
        id=pid, description="t", version="0.1",
        trigger=Trigger(host="claude-code", event=event, matcher=matcher),
        sentinel_re=None,
        requires=[EvidenceReq(kind="step", step="citation_verify",
                              verdict="pass")],
        action="block", on_signature_invalid="deny",
        gate_binary="/usr/local/bin/magi-gate.sh",
    )


# The fixed golden policy set: two hooks on distinct events/matchers.
# The authored (CC) matchers are ``Bash`` + ``Read``; the emitter
# translates CC tool names to Codex tool names (§11.4 F4), so the golden
# output shows ``Bash`` -> ``exec_command`` and ``Read`` passing through
# unchanged (read-family CC tools have no 1:1 Codex tool).
_GOLDEN_POLICIES = [
    _evidence("p1", event="PreToolUse", matcher="Bash"),
    _evidence("p2", event="PostToolUse", matcher="Read"),
]

_GOLDEN_REQUIREMENTS_TOML = (
    "[features]\n"
    "hooks = true\n"
    "\n"
    "[[hooks.PostToolUse]]\n"
    'matcher = "Read"\n'
    "[[hooks.PostToolUse.hooks]]\n"
    'type = "command"\n'
    'command = "/usr/local/bin/magi-cp gate --runtime codex"\n'
    "timeout = 5000\n"
    "\n"
    "[[hooks.PreToolUse]]\n"
    'matcher = "exec_command"\n'
    "[[hooks.PreToolUse.hooks]]\n"
    'type = "command"\n'
    'command = "/usr/local/bin/magi-cp gate --runtime codex"\n'
    "timeout = 5000\n"
)


def _expected_hooks_json() -> str:
    entry = {
        "hooks": [{
            "type": "command",
            "command": CODEX_GATE_COMMAND,
            "timeout": CODEX_HOOK_TIMEOUT_MS,
        }],
    }
    obj = {"hooks": {
        "PostToolUse": [{**entry, "matcher": "Read"}],
        "PreToolUse": [{**entry, "matcher": "exec_command"}],
    }}
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)


# ── golden byte-stability ────────────────────────────────────────────
def test_requirements_toml_golden():
    bundle = compile_to_codex_requirements(list(_GOLDEN_POLICIES))
    assert bundle.requirements_toml == _GOLDEN_REQUIREMENTS_TOML


def test_hooks_json_golden():
    bundle = compile_to_codex_requirements(list(_GOLDEN_POLICIES))
    assert bundle.hooks_json_sidecar == _expected_hooks_json()


def test_every_hook_entry_uses_the_shared_gate_command():
    bundle = compile_to_codex_requirements(list(_GOLDEN_POLICIES))
    assert bundle.requirements_toml.count(
        f'command = "{CODEX_GATE_COMMAND}"'
    ) == 2
    assert f"timeout = {CODEX_HOOK_TIMEOUT_MS}" in bundle.requirements_toml


# ── CC -> Codex matcher translation (§11.4 F4) ───────────────────────
def test_bash_matcher_translates_to_exec_command():
    bundle = compile_to_codex_requirements([_evidence("p", matcher="Bash")])
    assert 'matcher = "exec_command"' in bundle.requirements_toml
    assert '"Bash"' not in bundle.requirements_toml
    assert '"exec_command"' in bundle.hooks_json_sidecar


def test_file_mutation_tools_dedupe_to_single_apply_patch_table():
    # Every CC file-mutation tool (Edit/Write/MultiEdit/NotebookEdit)
    # collapses to Codex's single ``apply_patch`` tool and must emit
    # exactly one hook table, not four identical ones.
    policies = [
        _evidence("e", event="PreToolUse", matcher="Edit"),
        _evidence("w", event="PreToolUse", matcher="Write"),
        _evidence("m", event="PreToolUse", matcher="MultiEdit"),
        _evidence("n", event="PreToolUse", matcher="NotebookEdit"),
    ]
    bundle = compile_to_codex_requirements(policies)
    assert bundle.requirements_toml.count("[[hooks.PreToolUse]]") == 1
    assert bundle.requirements_toml.count('matcher = "apply_patch"') == 1
    for cc in ('"Edit"', '"Write"', '"MultiEdit"', '"NotebookEdit"'):
        assert cc not in bundle.requirements_toml


def test_read_family_and_mcp_matchers_pass_through_unchanged():
    # Read-family CC tools have no 1:1 Codex tool, and an MCP tool name is
    # identical on both runtimes: both pass through verbatim.
    policies = [
        _evidence("r", event="PostToolUse", matcher="Read"),
        _evidence("g", event="PreToolUse", matcher="Grep"),
        _evidence("m", event="PreToolUse", matcher="mcp__github__create_issue"),
    ]
    toml = compile_to_codex_requirements(policies).requirements_toml
    assert 'matcher = "Read"' in toml
    assert 'matcher = "Grep"' in toml
    assert 'matcher = "mcp__github__create_issue"' in toml


def test_alternation_of_tool_names_translates_and_dedupes():
    # A simple alternation of bare tool names is translated per-token, so a
    # translatable CC tool inside an alternation still binds to its Codex
    # tool instead of firing zero times (the alternation form of the F4
    # false-coverage hole). Tokens are deduped + sorted for byte-stability.
    #   Edit|Write   -> apply_patch (both map to apply_patch, deduped)
    #   Bash|Read    -> Read|exec_command (Bash translated, Read passes; sorted)
    a = compile_to_codex_requirements(
        [_evidence("a", event="PreToolUse", matcher="Edit|Write")]
    ).requirements_toml
    assert 'matcher = "apply_patch"' in a
    assert '"Edit|Write"' not in a

    b = compile_to_codex_requirements(
        [_evidence("b", event="PreToolUse", matcher="Bash|Read")]
    ).requirements_toml
    assert 'matcher = "Read|exec_command"' in b
    assert '"Bash|Read"' not in b

    # Order-invariant: Write|Edit and Edit|Write emit identical output.
    fwd = compile_to_codex_requirements(
        [_evidence("x", event="PreToolUse", matcher="Edit|Write")]
    ).requirements_toml
    rev = compile_to_codex_requirements(
        [_evidence("x", event="PreToolUse", matcher="Write|Edit")]
    ).requirements_toml
    assert fwd == rev


def test_task_matcher_translates_to_spawn_agent():
    # CC's single-subagent-spawn tool ``Task`` maps onto Codex's covered
    # ``spawn_agent`` tool. (``spawn_agent`` itself is unauthorable via the
    # IR grammar, so it only reaches the emitter through Shim D internally.)
    bundle = compile_to_codex_requirements(
        [_evidence("s", event="PreToolUse", matcher="Task")]
    )
    assert 'matcher = "spawn_agent"' in bundle.requirements_toml
    assert '"Task"' not in bundle.requirements_toml


# ── order invariance (sort of events + matchers) ─────────────────────
def test_reordered_policy_list_produces_identical_output():
    forward = compile_to_codex_requirements(list(_GOLDEN_POLICIES))
    reversed_ = compile_to_codex_requirements(list(reversed(_GOLDEN_POLICIES)))
    assert reversed_.requirements_toml == forward.requirements_toml
    assert reversed_.hooks_json_sidecar == forward.hooks_json_sidecar


def test_repeat_compile_is_byte_identical():
    a = compile_to_codex_requirements(list(_GOLDEN_POLICIES))
    b = compile_to_codex_requirements(list(_GOLDEN_POLICIES))
    assert a.requirements_toml == b.requirements_toml
    assert a.hooks_json_sidecar == b.hooks_json_sidecar


# ── features toggles ─────────────────────────────────────────────────
def test_multi_agent_absent_without_subagent_policy():
    bundle = compile_to_codex_requirements(list(_GOLDEN_POLICIES))
    assert "multi_agent" not in bundle.requirements_toml


def test_multi_agent_enabled_with_subagent_policy():
    policies = [
        _evidence("p1"),
        SubagentPolicy(id="sub", description="t", subagent_type="reviewer"),
    ]
    bundle = compile_to_codex_requirements(policies)
    assert "multi_agent = true" in bundle.requirements_toml


# ── context templates sidecar ────────────────────────────────────────
def test_context_injection_emits_sha256_template_sidecar():
    import hashlib

    template = "always cite your sources"
    policies = [
        ContextInjectionPolicy(
            id="ctx", description="t", event="UserPromptSubmit",
            template=template, matcher="*",
        ),
    ]
    bundle = compile_to_codex_requirements(policies)
    digest = hashlib.sha256(template.encode("utf-8")).hexdigest()
    assert bundle.context_templates == {digest: template}


# ── validation boundary ──────────────────────────────────────────────
def test_duplicate_policy_id_rejected():
    with pytest.raises(ValueError):
        compile_to_codex_requirements([_evidence("dup"), _evidence("dup")])


def test_empty_policy_list_emits_features_only():
    bundle = compile_to_codex_requirements([])
    assert bundle.requirements_toml == "[features]\nhooks = true\n"
    assert bundle.hooks_json_sidecar == json.dumps(
        {"hooks": {}}, ensure_ascii=False, sort_keys=True, indent=2,
    )


# ── PermissionPolicy native lowering (design 2026-07-01) ─────────────
def test_no_permission_policies_leave_requirements_byte_identical():
    # Evidence-only compile must be unchanged (no default_permissions,
    # no [allowed_permission_profiles], no [rules], empty permissions_toml).
    b = compile_to_codex_requirements([_evidence("e")])
    assert "default_permissions" not in b.requirements_toml
    assert "[allowed_permission_profiles]" not in b.requirements_toml
    assert "[rules]" not in b.requirements_toml
    assert b.permissions_toml == ""


def test_bash_deny_lowers_to_forbidden_prefix_rule():
    b = compile_to_codex_requirements([_perm("p", "Bash(rm -rf *)", "deny")])
    r = tomllib.loads(b.requirements_toml)
    assert r["rules"]["prefix_rules"] == [{
        "pattern": [{"token": "rm"}, {"token": "-rf"}],
        "decision": "forbidden",
        "justification": "Magi policy p",
    }]


def test_bash_ask_lowers_to_prompt_and_strips_colon_star():
    b = compile_to_codex_requirements([_perm("p", "Bash(git push:*)", "ask")])
    rule = tomllib.loads(b.requirements_toml)["rules"]["prefix_rules"][0]
    assert rule["pattern"] == [{"token": "git"}, {"token": "push"}]
    assert rule["decision"] == "prompt"


def test_bash_allow_emits_no_rule():
    # allow is the default absent a deny; no prefix_rule, no profile.
    b = compile_to_codex_requirements([_perm("p", "Bash(ls *)", "allow")])
    assert "[rules]" not in b.requirements_toml
    assert b.permissions_toml == ""


def test_read_deny_lowers_to_filesystem_deny():
    b = compile_to_codex_requirements([_perm("p", "Read(**/*.env)", "deny")])
    prof = tomllib.loads(b.permissions_toml)["permissions"][CODEX_PERMISSION_PROFILE]
    assert prof["filesystem"][":workspace_roots"]["**/*.env"] == "deny"
    # profile is forced + allowlisted in requirements.toml.
    r = tomllib.loads(b.requirements_toml)
    assert r["default_permissions"] == CODEX_PERMISSION_PROFILE
    assert r["allowed_permission_profiles"] == {CODEX_PERMISSION_PROFILE: True}


def test_write_allow_is_write_tier_read_allow_is_read_tier():
    b = compile_to_codex_requirements([
        _perm("w", "Edit(src/**)", "allow"),
        _perm("r", "Read(docs/**)", "allow"),
    ])
    fs = tomllib.loads(b.permissions_toml)["permissions"][CODEX_PERMISSION_PROFILE]["filesystem"][":workspace_roots"]
    assert fs["src/**"] == "write"
    assert fs["docs/**"] == "read"


def test_webfetch_deny_lowers_to_network_domain_and_strips_domain_prefix():
    b = compile_to_codex_requirements([
        _perm("p", "WebFetch(domain:tracking.example.com)", "deny"),
    ])
    net = tomllib.loads(b.permissions_toml)["permissions"][CODEX_PERMISSION_PROFILE]["network"]
    assert net["enabled"] is True
    assert net["domains"]["tracking.example.com"] == "deny"


def test_filesystem_most_restrictive_wins_on_shared_glob():
    # allow (read) + deny on the same glob -> deny (most restrictive).
    b = compile_to_codex_requirements([
        _perm("a", "Read(secret/**)", "allow"),
        _perm("d", "Edit(secret/**)", "deny"),
    ])
    fs = tomllib.loads(b.permissions_toml)["permissions"][CODEX_PERMISSION_PROFILE]["filesystem"][":workspace_roots"]
    assert fs["secret/**"] == "deny"


def test_ask_on_file_or_host_is_hook_residual_not_native():
    # fs/net have no prompt tier; ask falls to the hook path (no native rule).
    b = compile_to_codex_requirements([
        _perm("f", "Read(x/**)", "ask"),
        _perm("h", "WebFetch(domain:x.com)", "ask"),
    ])
    assert b.permissions_toml == ""  # no fs/net rule emitted


def test_mcp_gating_is_hook_residual_not_native():
    b = compile_to_codex_requirements([
        McpGatingPolicy(id="m", description="t", server="evil", action="deny"),
    ])
    # MCP is not profile-expressible -> no profile, no rule.
    assert b.permissions_toml == ""
    assert "[allowed_permission_profiles]" not in b.requirements_toml


def test_permission_lowering_is_order_invariant_and_valid_toml():
    pols = [
        _perm("p1", "Bash(rm -rf *)", "deny"),
        _perm("p2", "Read(**/*.env)", "deny"),
        _perm("p3", "Edit(src/**)", "allow"),
        _perm("p4", "WebFetch(domain:a.com)", "deny"),
    ]
    fwd = compile_to_codex_requirements(pols)
    rev = compile_to_codex_requirements(list(reversed(pols)))
    assert fwd.requirements_toml == rev.requirements_toml
    assert fwd.permissions_toml == rev.permissions_toml
    # both artifacts are valid TOML
    tomllib.loads(fwd.requirements_toml)
    tomllib.loads(fwd.permissions_toml)
