"""U1 gjc runtime driver: parse / emit / gate round-trip.

Design brief: 2026-07-08-magi-cp-gajae-code-runtime-adapter-design
Section 11.1 U1 sub-tests (a)–(f).

Wire format (§4.3, owned by magi-cp — the shim is ours):
  stdin  -> gjc envelope with "gjc_event" key
  stdout -> {"block": true, "reason": "MAGI: ..."}\n  (deny/ask)
           b""                                          (allow)
"""
from __future__ import annotations

import json

import pytest

from magi_cp.runtime.gjc import GjcDriver, _GJC_TO_CC_TOOL, run_gjc_gate
from magi_cp.runtime.trait import HookEvent, Verdict


# ── Fixture helpers ──────────────────────────────────────────────────────


def _tool_call_payload(
    tool_name: str = "bash",
    session_id: str = "01923abc-def0-7abc-8def-012345678901",
    cwd: str = "/workspace",
    tool_input: dict | None = None,
    tool_call_id: str = "call_abc",
) -> dict:
    """Canonical gjc tool_call envelope (§4.3)."""
    return {
        "gjc_event": "tool_call",
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "tool_input": tool_input if tool_input is not None else {"command": "rm -rf /"},
        "session_id": session_id,
        "session_file": f"/home/user/.gjc/sessions/{session_id}.json",
        "cwd": cwd,
        "model": "claude-opus-4-5",
        "shim_version": "1",
    }


def _encode(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8")


# ── (a) tool_call envelope -> canonical HookEvent ────────────────────────


def test_parse_tool_call_to_pretooluse():
    """§11.1 U1(a): tool_call gjc_event -> HookEvent(PreToolUse, tool_name=Bash)."""
    driver = GjcDriver()
    payload = _tool_call_payload(tool_name="bash", session_id="01923abc-def0-7abc-8def-012345678901")
    ev = driver.parse_hook_payload(_encode(payload))
    assert isinstance(ev, HookEvent)
    assert ev.hook_event_name == "PreToolUse"
    assert ev.tool_name == "Bash"  # normalized via _GJC_TO_CC_TOOL
    assert ev.session_id == "01923abc-def0-7abc-8def-012345678901"
    assert ev.raw == payload


def test_parse_carries_raw_verbatim():
    """§4.4: raw keeps the decoded payload verbatim."""
    driver = GjcDriver()
    payload = _tool_call_payload()
    ev = driver.parse_hook_payload(_encode(payload))
    assert ev.raw == payload


def test_parse_cwd_forwarded():
    driver = GjcDriver()
    payload = _tool_call_payload(cwd="/my/project")
    ev = driver.parse_hook_payload(_encode(payload))
    assert ev.cwd == "/my/project"


def test_parse_tool_input_forwarded():
    driver = GjcDriver()
    payload = _tool_call_payload(tool_input={"command": "ls", "flag": "-la"})
    ev = driver.parse_hook_payload(_encode(payload))
    assert ev.tool_input == {"command": "ls", "flag": "-la"}


def test_parse_turn_id_stays_empty():
    """gjc has no turn_id on its wire (§4.4); canonical turn_id stays ''."""
    driver = GjcDriver()
    ev = driver.parse_hook_payload(_encode(_tool_call_payload()))
    assert ev.turn_id == ""


# ── (b) raw-name passthrough (D2) ────────────────────────────────────────


def test_parse_ssh_passthrough():
    """§11.1 U1(b), D2: unmapped tool name 'ssh' stays 'ssh'."""
    driver = GjcDriver()
    payload = _tool_call_payload(tool_name="ssh")
    ev = driver.parse_hook_payload(_encode(payload))
    assert ev.tool_name == "ssh"


def test_parse_computer_passthrough():
    """§2.5 enforcement-relevant raw name: 'computer' passes through."""
    driver = GjcDriver()
    payload = _tool_call_payload(tool_name="computer")
    ev = driver.parse_hook_payload(_encode(payload))
    assert ev.tool_name == "computer"


def test_parse_cron_passthrough():
    driver = GjcDriver()
    ev = driver.parse_hook_payload(_encode(_tool_call_payload(tool_name="cron")))
    assert ev.tool_name == "cron"


def test_parse_telegram_send_passthrough():
    driver = GjcDriver()
    ev = driver.parse_hook_payload(_encode(_tool_call_payload(tool_name="telegram_send")))
    assert ev.tool_name == "telegram_send"


def test_parse_unknown_mcp_tool_passthrough():
    """Custom MCP tools pass through raw (§2.5 D2 posture)."""
    driver = GjcDriver()
    ev = driver.parse_hook_payload(_encode(_tool_call_payload(tool_name="my_custom_mcp_tool")))
    assert ev.tool_name == "my_custom_mcp_tool"


# ── (c) every mapped pair in _GJC_TO_CC_TOOL ────────────────────────────


@pytest.mark.parametrize("gjc_name,cc_name", list(_GJC_TO_CC_TOOL.items()))
def test_tool_normalization_table(gjc_name: str, cc_name: str):
    """§11.1 U1(c): every _GJC_TO_CC_TOOL entry is exercised."""
    driver = GjcDriver()
    ev = driver.parse_hook_payload(_encode(_tool_call_payload(tool_name=gjc_name)))
    assert ev.tool_name == cc_name, (
        f"_GJC_TO_CC_TOOL[{gjc_name!r}] = {cc_name!r} but got {ev.tool_name!r}"
    )


# ── (d) emit_verdict goldens ─────────────────────────────────────────────


def test_emit_deny_block_bytes():
    """§11.1 U1(d): deny -> {"block": true, "reason": "MAGI: ..."} + newline."""
    driver = GjcDriver()
    out = driver.emit_verdict(Verdict(decision="deny", reason="not allowed", hook_event_name="PreToolUse"))
    obj = json.loads(out.decode("utf-8"))
    assert obj["block"] is True
    assert obj["reason"].startswith("MAGI: ")
    assert out.endswith(b"\n")


def test_emit_deny_reason_content():
    driver = GjcDriver()
    out = driver.emit_verdict(Verdict(decision="deny", reason="xyzzy", hook_event_name="PreToolUse"))
    obj = json.loads(out.decode("utf-8"))
    assert "xyzzy" in obj["reason"]


def test_emit_allow_is_empty_bytes():
    """§11.1 U1(d): allow -> b"" (silent allow; §4.3 §4.5)."""
    driver = GjcDriver()
    out = driver.emit_verdict(Verdict(decision="allow", hook_event_name="PreToolUse"))
    assert out == b""


def test_emit_ask_is_deny_with_guidance():
    """§11.1 U1(d), D3: ask -> deny-with-guidance bytes, not allow."""
    driver = GjcDriver()
    out = driver.emit_verdict(Verdict(decision="ask", reason="needs approval", hook_event_name="PreToolUse"))
    assert out != b""
    obj = json.loads(out.decode("utf-8"))
    assert obj["block"] is True
    # Guidance text must reference the ask-tier unsupported on gjc.
    assert "ask" in obj["reason"].lower() or "unsupported" in obj["reason"].lower() or "approval" in obj["reason"].lower()


def test_emit_allow_with_updated_input_stays_empty():
    """§11.1 U1(d): updated_input on allow -> b"" (no arg-rewrite leak, §4.5)."""
    driver = GjcDriver()
    out = driver.emit_verdict(Verdict(
        decision="allow",
        hook_event_name="PreToolUse",
        updated_input={"command": "safe_command"},
    ))
    assert out == b""


def test_emit_deny_with_updated_input_still_denies():
    """§4.5: populated updated_input on a deny keeps the deny (not silently dropped)."""
    driver = GjcDriver()
    out = driver.emit_verdict(Verdict(
        decision="deny",
        reason="blocked",
        hook_event_name="PreToolUse",
        updated_input={"command": "something"},
    ))
    obj = json.loads(out.decode("utf-8"))
    assert obj["block"] is True


# ── (e) malformed stdin -> fail-closed block via run_gjc_gate ────────────


def test_run_gjc_gate_malformed_json_returns_0_and_blocks(capsys):
    """§11.1 U1(e): malformed JSON -> fail-closed block bytes on stdout, exit 0."""
    ret = run_gjc_gate("not valid json at all {{{")
    captured = capsys.readouterr()
    assert ret == 0
    obj = json.loads(captured.out)
    assert obj["block"] is True


def test_run_gjc_gate_non_object_json_blocks(capsys):
    """§4.4: non-object JSON (e.g. a JSON array) -> fail-closed block."""
    ret = run_gjc_gate("[1, 2, 3]")
    captured = capsys.readouterr()
    assert ret == 0
    obj = json.loads(captured.out)
    assert obj["block"] is True


# ── (f) blank stdin -> pass-through (exit 0, no output) ─────────────────


def test_run_gjc_gate_blank_stdin_pass_through(capsys):
    """§11.1 U1(f), §4.3: blank stdin -> pass-through (exit 0, no block output)."""
    ret = run_gjc_gate("")
    captured = capsys.readouterr()
    assert ret == 0
    assert captured.out == ""


def test_run_gjc_gate_whitespace_only_pass_through(capsys):
    """Whitespace-only input is equivalent to blank stdin."""
    ret = run_gjc_gate("   \n  ")
    captured = capsys.readouterr()
    assert ret == 0
    assert captured.out == ""


# ── Additional parse robustness ──────────────────────────────────────────


def test_parse_unknown_gjc_event_preserved():
    """§4.4 / H3: unknown gjc_event values parse with the raw name preserved."""
    driver = GjcDriver()
    payload = {
        "gjc_event": "some_future_event",
        "tool_name": "bash",
        "session_id": "abc",
        "cwd": "/",
        "tool_input": {},
        "tool_call_id": "x",
        "shim_version": "1",
    }
    ev = driver.parse_hook_payload(_encode(payload))
    # Must not raise; raw name preserved (not silently dropped, H3)
    assert ev.hook_event_name  # non-empty
    assert ev.raw == payload


def test_parse_session_start_event():
    """§4.4: session_start -> SessionStart."""
    driver = GjcDriver()
    payload = {
        "gjc_event": "session_start",
        "tool_name": "",
        "tool_call_id": "",
        "tool_input": {},
        "session_id": "sess-1",
        "session_file": "",
        "cwd": "/",
        "model": "",
        "shim_version": "1",
    }
    ev = driver.parse_hook_payload(_encode(payload))
    assert ev.hook_event_name == "SessionStart"


def test_parse_session_shutdown_event():
    """§4.4: session_shutdown -> SessionEnd."""
    driver = GjcDriver()
    payload = {
        "gjc_event": "session_shutdown",
        "tool_name": "",
        "tool_call_id": "",
        "tool_input": {},
        "session_id": "sess-2",
        "session_file": "",
        "cwd": "/",
        "model": "",
        "shim_version": "1",
    }
    ev = driver.parse_hook_payload(_encode(payload))
    assert ev.hook_event_name == "SessionEnd"
