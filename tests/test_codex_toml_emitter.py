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

from magi_cp.policy.codex_toml_emitter import (
    CODEX_GATE_COMMAND,
    CODEX_HOOK_TIMEOUT_MS,
    compile_to_codex_requirements,
)
from magi_cp.policy.ir import (
    ContextInjectionPolicy,
    EvidencePolicy,
    EvidenceReq,
    SubagentPolicy,
    Trigger,
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


def test_edit_and_write_dedupe_to_single_apply_patch_table():
    # Distinct CC tools that collapse to one Codex tool must emit exactly
    # one hook table, not two identical ``apply_patch`` tables.
    policies = [
        _evidence("e", event="PreToolUse", matcher="Edit"),
        _evidence("w", event="PreToolUse", matcher="Write"),
    ]
    bundle = compile_to_codex_requirements(policies)
    assert bundle.requirements_toml.count("[[hooks.PreToolUse]]") == 1
    assert bundle.requirements_toml.count('matcher = "apply_patch"') == 1
    assert '"Edit"' not in bundle.requirements_toml
    assert '"Write"' not in bundle.requirements_toml


def test_read_family_and_regex_matchers_pass_through_unchanged():
    # Read-family CC tools have no 1:1 Codex tool, and regex/alternation
    # matchers are not translated: both pass through verbatim.
    policies = [
        _evidence("r", event="PostToolUse", matcher="Read"),
        _evidence("g", event="PreToolUse", matcher="Grep"),
        _evidence("x", event="PreToolUse", matcher="Edit|Write"),
    ]
    toml = compile_to_codex_requirements(policies).requirements_toml
    assert 'matcher = "Read"' in toml
    assert 'matcher = "Grep"' in toml
    assert 'matcher = "Edit|Write"' in toml


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
