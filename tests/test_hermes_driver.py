"""P1 Hermes adapter: driver parse -> canonical -> emit round-trip,
coverage ledger, unmapped-tool passthrough, and the fail-closed gate
contract.

Design brief: 2026-07-06-magi-cp-hermes-runtime-adapter-design (private
planning repo), Section 10 "P1: driver + detection + contract tests".

The stdin fixtures replicate the EXACT Hermes payload shape captured from
``agent/shell_hooks.py:527-543`` (``_serialize_payload``): snake_case
``hook_event_name`` + ``tool_name`` + ``tool_input`` + ``session_id`` +
``cwd`` + ``extra``. The stdout shapes match ``_parse_response``
(``shell_hooks.py:557-611``): block == ``{"action":"block","message"}``,
context == ``{"context"}``, allow == empty.
"""
from __future__ import annotations

import json

import pytest

from magi_cp.policy.ir import (
    ContextInjectionPolicy,
    EvidencePolicy,
    EvidenceReq,
    InputRewritePolicy,
    McpGatingPolicy,
    PermissionPolicy,
    SubagentPolicy,
    Trigger,
)
from magi_cp.runtime.hermes import (
    HermesDriver,
    run_hermes_gate,
)
from magi_cp.runtime.trait import HookEvent, Verdict


# ── real Hermes stdin fixtures (shell_hooks.py:527-543 shape) ────────────
def _pre_tool_call_terminal() -> dict:
    """A ``pre_tool_call`` on Hermes's ``terminal`` tool — the exact
    envelope ``_serialize_payload`` renders."""
    return {
        "hook_event_name": "pre_tool_call",
        "tool_name": "terminal",
        "tool_input": {"command": "rm -rf /"},
        "session_id": "sess_abc123",
        "cwd": "/home/user/project",
        "extra": {
            "task_id": "task-1",
            "tool_call_id": "tc-1",
            "turn_id": "turn-9",
            "api_request_id": "req-1",
            "middleware_trace": [],
        },
    }


def _pre_tool_call_unmapped() -> dict:
    """A ``pre_tool_call`` on an UNMAPPED Hermes tool (``computer_use``);
    the raw name must survive end-to-end (K6 posture, design 3.3)."""
    return {
        "hook_event_name": "pre_tool_call",
        "tool_name": "computer_use",
        "tool_input": {"action": "screenshot"},
        "session_id": "sess_xyz",
        "cwd": "/repo",
        "extra": {"turn_id": "turn-2"},
    }


def _pre_llm_call() -> dict:
    return {
        "hook_event_name": "pre_llm_call",
        "tool_name": None,
        "tool_input": None,
        "session_id": "sess_ctx",
        "cwd": "/repo",
        "extra": {"turn_id": "turn-3"},
    }


def _pre_verify() -> dict:
    return {
        "hook_event_name": "pre_verify",
        "tool_name": None,
        "tool_input": None,
        "session_id": "sess_stop",
        "cwd": "/repo",
        "extra": {"turn_id": "turn-4"},
    }


def _emit_obj(driver: HermesDriver, verdict: Verdict) -> dict:
    out = driver.emit_verdict(verdict)
    text = out.decode("utf-8")
    assert text.endswith("\n")
    return json.loads(text)


# ── parse: Hermes snake_case -> canonical HookEvent ──────────────────────
def test_parse_pre_tool_call_maps_event_and_tool():
    driver = HermesDriver()
    ev = driver.parse_hook_payload(
        json.dumps(_pre_tool_call_terminal()).encode()
    )
    assert isinstance(ev, HookEvent)
    # snake_case event -> canonical PascalCase.
    assert ev.hook_event_name == "PreToolUse"
    # terminal -> Bash family (CC-mappable core).
    assert ev.tool_name == "Bash"
    assert ev.tool_input == {"command": "rm -rf /"}
    assert ev.session_id == "sess_abc123"
    # extra.turn_id maps onto the canonical correlation field.
    assert ev.turn_id == "turn-9"


def test_parse_carries_raw_verbatim():
    driver = HermesDriver()
    raw = _pre_tool_call_terminal()
    ev = driver.parse_hook_payload(json.dumps(raw).encode())
    assert ev.raw == raw


@pytest.mark.parametrize(
    "hermes_event,canonical",
    [
        ("pre_tool_call", "PreToolUse"),
        ("post_tool_call", "PostToolUse"),
        ("pre_llm_call", "UserPromptSubmit"),
        ("pre_verify", "Stop"),
        ("on_session_start", "SessionStart"),
        ("on_session_end", "SessionEnd"),
        ("on_session_finalize", "SessionEnd"),
        ("subagent_start", "SubagentStart"),
        ("subagent_stop", "SubagentStop"),
        ("pre_approval_request", "PermissionRequest"),
    ],
)
def test_event_name_map_matches_design_matrix(hermes_event, canonical):
    """Section 4 event matrix: every mapped Hermes event resolves to its
    canonical name."""
    driver = HermesDriver()
    ev = driver.parse_hook_payload(
        json.dumps({"hook_event_name": hermes_event, "extra": {}}).encode()
    )
    assert ev.hook_event_name == canonical


def test_unmapped_event_passes_through_raw():
    """A Hermes event with no canonical counterpart keeps its raw
    snake_case name (observe-only)."""
    driver = HermesDriver()
    ev = driver.parse_hook_payload(
        json.dumps({"hook_event_name": "post_llm_call", "extra": {}}).encode()
    )
    assert ev.hook_event_name == "post_llm_call"


@pytest.mark.parametrize(
    "hermes_tool,cc_family",
    [
        ("terminal", "Bash"),
        ("execute_code", "Bash"),
        ("read_terminal", "Bash"),
        ("close_terminal", "Bash"),
        ("process", "Bash"),
        ("write_file", "Write"),
        ("patch", "Edit"),
        ("read_file", "Read"),
        ("search_files", "Grep"),
        ("web_extract", "WebFetch"),
        ("web_search", "WebSearch"),
        ("x_search", "WebSearch"),
        ("delegate_task", "Task"),
    ],
)
def test_tool_name_normalization_table(hermes_tool, cc_family):
    """Section 2.9 / 3.3: the CC-mappable core normalizes to its CC
    family."""
    driver = HermesDriver()
    ev = driver.parse_hook_payload(
        json.dumps({
            "hook_event_name": "pre_tool_call",
            "tool_name": hermes_tool,
            "extra": {},
        }).encode()
    )
    assert ev.tool_name == cc_family


def test_mcp_tool_passes_through_identically():
    """``mcp__*`` uses the identical CC convention -> raw passthrough."""
    driver = HermesDriver()
    ev = driver.parse_hook_payload(
        json.dumps({
            "hook_event_name": "pre_tool_call",
            "tool_name": "mcp__github__create_issue",
            "extra": {},
        }).encode()
    )
    assert ev.tool_name == "mcp__github__create_issue"


def test_blank_stdin_is_passthrough_event():
    driver = HermesDriver()
    ev = driver.parse_hook_payload(b"")
    assert ev.hook_event_name == "PreToolUse"
    assert ev.raw == {}


def test_non_object_payload_raises():
    driver = HermesDriver()
    with pytest.raises(ValueError):
        driver.parse_hook_payload(b"[1, 2, 3]")


# ── emit: canonical Verdict -> Hermes stdout ─────────────────────────────
def test_deny_emits_hermes_canonical_block():
    driver = HermesDriver()
    obj = _emit_obj(driver, Verdict(
        decision="deny", reason="forbidden", hook_event_name="PreToolUse",
    ))
    assert obj == {"action": "block", "message": "MAGI: forbidden"}


def test_allow_is_silent():
    driver = HermesDriver()
    out = driver.emit_verdict(Verdict(
        decision="allow", hook_event_name="PreToolUse",
    ))
    assert out == b""


def test_ask_downgrades_to_deny_with_guidance():
    """K3: Hermes has no ask tier -> deny-with-guidance (fail-safe)."""
    driver = HermesDriver()
    obj = _emit_obj(driver, Verdict(
        decision="ask", reason="needs approval", hook_event_name="PreToolUse",
    ))
    assert obj["action"] == "block"
    assert "needs approval" in obj["message"]
    assert "magi-cp" in obj["message"]


def test_ask_downgrade_without_reason_still_blocks():
    driver = HermesDriver()
    obj = _emit_obj(driver, Verdict(
        decision="ask", hook_event_name="PreToolUse",
    ))
    assert obj["action"] == "block"
    assert obj["message"].startswith("MAGI: ")


def test_context_on_stop_uses_continue_channel():
    """A Stop (pre_verify) allow carrying context -> continue channel."""
    driver = HermesDriver()
    obj = _emit_obj(driver, Verdict(
        decision="allow", hook_event_name="Stop",
        additional_context="keep going: cite your sources",
    ))
    assert obj == {"action": "continue",
                   "message": "keep going: cite your sources"}


def test_context_on_prompt_uses_context_channel():
    """A UserPromptSubmit allow carrying context -> {"context"} wire."""
    driver = HermesDriver()
    obj = _emit_obj(driver, Verdict(
        decision="allow", hook_event_name="UserPromptSubmit",
        additional_context="today is Friday",
    ))
    assert obj == {"context": "today is Friday"}


def test_updated_input_is_dropped_on_allow():
    """Branch A: no shell-wire rewrite; updated_input has no channel."""
    driver = HermesDriver()
    out = driver.emit_verdict(Verdict(
        decision="allow", hook_event_name="PreToolUse",
        updated_input={"command": "ls"},
    ))
    assert out == b""


# ── coverage ledger (_coverage_status_for_hermes, Section 6) ─────────────
def _perm(permission: str, matcher: str = "Bash") -> PermissionPolicy:
    return PermissionPolicy(
        id=f"perm-{permission}-{matcher}".lower().replace("|", "-"),
        description="p", version="0.1",
        trigger=Trigger(host="claude-code", event="PreToolUse",
                        matcher=matcher),
        permission=permission, pattern=f"{matcher}(x)",
    )


def _mcp() -> McpGatingPolicy:
    return McpGatingPolicy(
        id="mcp1", description="deny server", version="0.1",
        server="github", action="deny",
    )


def _subagent() -> SubagentPolicy:
    return SubagentPolicy(
        id="sub1", description="disable child", version="0.1",
        subagent_type="researcher",
    )


def _input_rewrite() -> InputRewritePolicy:
    return InputRewritePolicy(
        id="ir1", description="strip sudo", version="0.1",
        trigger=Trigger(host="claude-code", event="PreToolUse",
                        matcher="Bash"),
        rewriter={"kind": "prefix_strip",
                  "config": {"field": "command", "prefix": "sudo "}},
    )


def _context(event: str = "UserPromptSubmit") -> ContextInjectionPolicy:
    return ContextInjectionPolicy(
        id=f"ctx-{event}".lower(), description="inject",
        event=event, template="be careful", matcher="*",
    )


def _evidence(
    event: str = "PreToolUse",
    matcher: str = "Bash",
    action: str = "block",
) -> EvidencePolicy:
    return EvidencePolicy(
        id=f"ev-{event}-{matcher}".lower().replace("*", "star"),
        description="audit", version="0.1",
        trigger=Trigger(host="claude-code", event=event, matcher=matcher),
        sentinel_re=None,
        requires=[EvidenceReq(kind="step", step="citation_verify",
                              verdict="pass")],
        action=action, on_signature_invalid="deny",
        gate_binary="/usr/local/bin/magi-gate.sh",
    )


def _status(driver: HermesDriver, policy) -> str:
    report = driver.coverage_report([policy])
    return report.policies[0].status


def test_coverage_permission_deny_enforced():
    driver = HermesDriver()
    assert _status(driver, _perm("deny")) == "enforced"


def test_coverage_permission_ask_no_ask_tier():
    driver = HermesDriver()
    assert _status(driver, _perm("ask")) == "hermes_no_ask_tier"


def test_coverage_permission_unmapped_tool_marker():
    """A deny on a CC tool no Hermes tool maps onto (``NotebookEdit``)
    flags hermes_unmapped_tool: the pack has no reach on Hermes for it."""
    driver = HermesDriver()
    assert _status(driver, _perm("deny", matcher="NotebookEdit")) == \
        "hermes_unmapped_tool"


def test_coverage_mcp_enforced():
    driver = HermesDriver()
    assert _status(driver, _mcp()) == "enforced"


def test_coverage_subagent_enforced():
    driver = HermesDriver()
    assert _status(driver, _subagent()) == "enforced"


def test_coverage_input_rewrite_unsupported():
    driver = HermesDriver()
    assert _status(driver, _input_rewrite()) == "hermes_no_input_rewrite"


def test_coverage_stop_edit_turns_only():
    driver = HermesDriver()
    assert _status(
        driver, _evidence(event="Stop", matcher="*", action="audit")
    ) == "hermes_stop_edit_turns_only"


def test_coverage_pre_tool_context_dropped():
    driver = HermesDriver()
    assert _status(driver, _context(event="PreToolUse")) == \
        "hermes_pre_tool_context_dropped"


def test_coverage_compact_no_event():
    driver = HermesDriver()
    assert _status(
        driver, _evidence(event="PreCompact", matcher="*", action="audit")
    ) == "hermes_no_compact_event"


def test_coverage_pre_tool_evidence_enforced():
    driver = HermesDriver()
    assert _status(driver, _evidence(event="PreToolUse", matcher="Bash")) == \
        "enforced"


def test_coverage_report_carries_downgrade_text():
    driver = HermesDriver()
    report = driver.coverage_report([_perm("ask")])
    assert report.policies[0].downgrade is not None
    assert report.downgraded_count == 1


# ── install paths (Section 5.4 / 10 P1) ──────────────────────────────────
def test_install_paths_no_slash_command_dir():
    driver = HermesDriver()
    paths = driver.default_install_paths()
    assert paths.managed_config_dir == "/etc/hermes"
    # No slash-command dir in v1 (design 3.6).
    assert paths.slash_commands_dir == ""


# ── managed config (P1 shell) ────────────────────────────────────────────
def test_emit_managed_config_shell_wires_gate_hook():
    driver = HermesDriver()
    bundle = driver.emit_managed_config([_perm("deny")])
    assert "config.yaml" in bundle.files
    assert ".env" in bundle.files
    assert "--runtime hermes" in bundle.files["config.yaml"]
    assert "MAGI_CP_RUNTIME=hermes" in bundle.files[".env"]


# ── unmapped-tool passthrough end-to-end (K6, design 3.3) ────────────────
def test_unmapped_tool_name_survives_parse():
    driver = HermesDriver()
    ev = driver.parse_hook_payload(
        json.dumps(_pre_tool_call_unmapped()).encode()
    )
    # Raw name preserved so a raw-name policy can match it.
    assert ev.tool_name == "computer_use"
    assert ev.hook_event_name == "PreToolUse"


# ── fail-closed gate contract (Section 8.2) ──────────────────────────────
def test_gate_malformed_payload_fails_closed(capsysbinary):
    """A malformed payload -> block JSON on stdout, exit 0 (Hermes exit
    codes never block, so the verdict MUST ride stdout)."""
    rc = run_hermes_gate("not json at all")
    assert rc == 0
    out = capsysbinary.readouterr().out.decode("utf-8")
    obj = json.loads(out)
    assert obj["action"] == "block"
    assert "malformed" in obj["message"]


def test_gate_raise_inside_evaluate_fails_closed(capsysbinary, monkeypatch):
    """A raise inside the decision engine -> block-on-error JSON on stdout,
    exit 0 (design Section 8.2.2)."""
    import magi_cp.local.gate as gate

    def _boom(payload):
        raise RuntimeError("cloud exploded")

    monkeypatch.setattr(gate, "decide", _boom)
    rc = run_hermes_gate(json.dumps(_pre_tool_call_terminal()))
    assert rc == 0
    out = capsysbinary.readouterr().out.decode("utf-8")
    obj = json.loads(out)
    assert obj["action"] == "block"
    assert "fail-closed" in obj["message"]
    assert "RuntimeError" in obj["message"]


def test_gate_raise_on_observe_only_event_fails_silent(
    capsysbinary, monkeypatch,
):
    """A raise on an observe-only event (post_tool_call -> PostToolUse)
    fails SILENT: a block string there is ignored upstream, so silence
    avoids log spam (design Section 8.2.2)."""
    import magi_cp.local.gate as gate

    def _boom(payload):
        raise RuntimeError("boom")

    monkeypatch.setattr(gate, "decide", _boom)
    payload = dict(_pre_tool_call_terminal())
    payload["hook_event_name"] = "post_tool_call"
    rc = run_hermes_gate(json.dumps(payload))
    assert rc == 0
    assert capsysbinary.readouterr().out == b""


def test_gate_blank_stdin_is_silent(capsysbinary):
    rc = run_hermes_gate("")
    assert rc == 0
    assert capsysbinary.readouterr().out == b""


def test_gate_feeds_canonical_event_to_decide(monkeypatch):
    """One engine, two surfaces: the gate must feed ``decide`` the
    CANONICAL event name + normalized tool the parse step produced (so a
    sentinel that denies on CC denies identically on Hermes)."""
    import magi_cp.local.gate as gate
    seen: dict = {}

    def _capture(payload):
        seen.update(payload)
        from magi_cp.runtime.trait import Verdict
        return Verdict(decision="allow",
                       hook_event_name=payload["hook_event_name"])

    monkeypatch.setattr(gate, "decide", _capture)
    run_hermes_gate(json.dumps(_pre_tool_call_terminal()))
    # decide saw the canonical event + normalized tool, not the snake_case.
    assert seen["hook_event_name"] == "PreToolUse"
    assert seen["tool_name"] == "Bash"
    assert seen["tool_input"] == {"command": "rm -rf /"}
