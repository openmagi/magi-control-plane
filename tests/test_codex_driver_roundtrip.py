"""P1 Codex adapter: Codex driver parse -> canonical -> emit round-trip.

Canned Codex hook JSON payloads (PreToolUse Bash, PermissionRequest,
UserPromptSubmit, Stop) modelled on the DeepWiki wire-format recap in the
design doc Section 2.2. Assert each parses into a canonical HookEvent and
that a canonical Verdict emits a valid Codex verdict envelope per the
event's channel.
"""
from __future__ import annotations

import json

from magi_cp.runtime.codex import CodexDriver
from magi_cp.runtime.trait import HookEvent, Verdict


def _pre_tool_use_bash() -> dict:
    return {
        "session_id": "11111111-1111-4111-8111-111111111111",
        "turn_id": "22222222-2222-4222-8222-222222222222",
        "cwd": "/repo",
        "hook_event_name": "PreToolUse",
        "model": "gpt-5.2-codex",
        "permission_mode": "default",
        "transcript_path": "/tmp/codex/transcript.jsonl",
        "tool_name": "Bash",
        "tool_use_id": "call_abc",
        "tool_input": {"command": "rm -rf /"},
        "matcher_aliases": ["Bash", "unified_exec"],
    }


def _permission_request() -> dict:
    return {
        "session_id": "33333333-3333-4333-8333-333333333333",
        "turn_id": "44444444-4444-4444-8444-444444444444",
        "hook_event_name": "PermissionRequest",
        "tool_name": "apply_patch",
        "tool_input": {"patch": "*** Update File"},
        "matcher_aliases": ["apply_patch"],
    }


def _user_prompt_submit() -> dict:
    return {
        "session_id": "55555555-5555-4555-8555-555555555555",
        "hook_event_name": "UserPromptSubmit",
        "prompt": "delete the database",
        "matcher_aliases": [],
    }


def _stop() -> dict:
    return {
        "session_id": "66666666-6666-4666-8666-666666666666",
        "turn_id": "77777777-7777-4777-8777-777777777777",
        "hook_event_name": "Stop",
        "stop_hook_active": True,
        "matcher_aliases": [],
    }


def _emit_obj(driver: CodexDriver, verdict: Verdict) -> dict:
    out = driver.emit_verdict(verdict)
    text = out.decode("utf-8")
    assert text.endswith("\n")
    return json.loads(text)


# ── parse produces canonical HookEvent ───────────────────────────────
def test_parse_pretooluse_bash():
    driver = CodexDriver()
    ev = driver.parse_hook_payload(json.dumps(_pre_tool_use_bash()).encode())
    assert isinstance(ev, HookEvent)
    assert ev.hook_event_name == "PreToolUse"
    assert ev.tool_name == "Bash"
    assert ev.turn_id == "22222222-2222-4222-8222-222222222222"
    assert ev.matcher_aliases == ("Bash", "unified_exec")
    assert ev.tool_input == {"command": "rm -rf /"}


def test_parse_carries_raw_verbatim():
    driver = CodexDriver()
    raw = _permission_request()
    ev = driver.parse_hook_payload(json.dumps(raw).encode())
    assert ev.raw == raw


# ── emit produces valid per-channel Codex verdict envelopes ──────────
def test_pretooluse_deny_envelope():
    driver = CodexDriver()
    ev = driver.parse_hook_payload(json.dumps(_pre_tool_use_bash()).encode())
    obj = _emit_obj(driver, Verdict(
        decision="deny", reason="blocked", hook_event_name=ev.hook_event_name,
    ))
    hso = obj["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert hso["permissionDecisionReason"] == "MAGI: blocked"


def test_pretooluse_allow_is_silent():
    driver = CodexDriver()
    out = driver.emit_verdict(Verdict(decision="allow",
                                      hook_event_name="PreToolUse"))
    assert out == b""


def test_pretooluse_allow_with_updated_input():
    driver = CodexDriver()
    obj = _emit_obj(driver, Verdict(
        decision="allow", hook_event_name="PreToolUse",
        updated_input={"command": "ls"},
    ))
    hso = obj["hookSpecificOutput"]
    assert hso["permissionDecision"] == "allow"
    assert hso["updatedInput"] == {"command": "ls"}


def test_permission_request_deny_uses_behavior_channel():
    driver = CodexDriver()
    ev = driver.parse_hook_payload(json.dumps(_permission_request()).encode())
    obj = _emit_obj(driver, Verdict(
        decision="deny", reason="nope", hook_event_name=ev.hook_event_name,
    ))
    assert obj["decision"]["behavior"] == "deny"
    assert obj["decision"]["message"] == "MAGI: nope"


def test_user_prompt_submit_deny_uses_block_channel():
    driver = CodexDriver()
    ev = driver.parse_hook_payload(json.dumps(_user_prompt_submit()).encode())
    obj = _emit_obj(driver, Verdict(
        decision="deny", reason="denied prompt",
        hook_event_name=ev.hook_event_name,
    ))
    assert obj["decision"] == "block"
    assert obj["reason"] == "MAGI: denied prompt"


def test_stop_deny_uses_pretooluse_shape_fallback():
    # Stop is neither behavior- nor block-channel in P1, so it falls back
    # to the hookSpecificOutput.permissionDecision shape keyed on Stop.
    driver = CodexDriver()
    ev = driver.parse_hook_payload(json.dumps(_stop()).encode())
    obj = _emit_obj(driver, Verdict(
        decision="deny", reason="turn blocked",
        hook_event_name=ev.hook_event_name,
    ))
    assert obj["hookSpecificOutput"]["hookEventName"] == "Stop"
    assert obj["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_stop_allow_is_silent():
    driver = CodexDriver()
    ev = driver.parse_hook_payload(json.dumps(_stop()).encode())
    out = driver.emit_verdict(Verdict(decision="allow",
                                      hook_event_name=ev.hook_event_name))
    assert out == b""
