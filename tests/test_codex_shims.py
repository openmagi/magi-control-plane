"""P2 Codex adapter: gap shims A-D.

Design brief: docs/plans/2026-06-30-codex-runtime-adapter-design.md
Section 4. Each shim's fixture is drawn from the research recap:

  - Shim A (4.1): PreToolUse tool-coverage silent-skip. coverage_report
    marks a ``list_dir`` policy ``codex_silent_skip``; the emitter adds
    PermissionRequest + PostToolUse fallbacks; a covered tool (Bash) gets
    no fallback.
  - Shim B (4.2): PreToolUse additionalContext rejection. A turn-scope
    verdict downgrades to systemMessage; a session-scope verdict defers
    to the per-session queue and the next UserPromptSubmit drains it;
    session A cannot read session B's queue.
  - Shim C (4.3): SessionEnd absence. A Stop payload with
    stop_hook_active=true parses to a synthetic SessionEnd; false stays
    Stop.
  - Shim D (4.4): subagent hook fanout gap. A subagent-lifecycle policy
    is marked codex_internal_subagent_gap and the emitter adds a
    spawn_agent PreToolUse fallback.
"""
from __future__ import annotations

import json

from magi_cp.policy.codex_toml_emitter import compile_to_codex_requirements
from magi_cp.policy.ir import (
    ContextInjectionPolicy,
    EvidencePolicy,
    EvidenceReq,
    Trigger,
)
from magi_cp.runtime.codex import CODEX_SILENT_SKIP_TOOLS, CodexDriver
from magi_cp.runtime.trait import Verdict


# ── fixtures ─────────────────────────────────────────────────────────
def _evidence(pid: str, *, event="PreToolUse", matcher="Bash",
              action="block") -> EvidencePolicy:
    return EvidencePolicy(
        id=pid, description="t", version="0.1",
        trigger=Trigger(host="claude-code", event=event, matcher=matcher),
        sentinel_re=None,
        requires=[EvidenceReq(kind="step", step="privilege_scan",
                              verdict="pass")],
        action=action, on_signature_invalid="deny",
        gate_binary="/usr/local/bin/magi-gate.sh",
    )


def _emit_obj(driver: CodexDriver, verdict: Verdict) -> dict:
    out = driver.emit_verdict(verdict)
    text = out.decode("utf-8")
    assert text.endswith("\n")
    return json.loads(text)


# ── Shim A: PreToolUse silent-skip coverage + fallbacks ──────────────
# NOTE: the Codex-native silent-skip tool ``list_dir`` is unauthorable as
# a Magi policy (the IR validates matchers against the CC matcher grammar
# — matrix.matcher_class_of), so the end-to-end fixtures use the CC tool
# ``Read``, which maps onto a Codex read tool that silently skips
# PreToolUse. The Codex-native alias still lives in the deny-list for
# forward-compat.
def test_shim_a_list_dir_alias_in_deny_list():
    assert "list_dir" in CODEX_SILENT_SKIP_TOOLS
    assert "Read" in CODEX_SILENT_SKIP_TOOLS


def test_shim_a_coverage_marks_silent_skip_tool():
    driver = CodexDriver()
    report = driver.coverage_report([_evidence("p", matcher="Read")])
    entry = report.policies[0]
    assert entry.status == "codex_silent_skip"
    assert entry.downgrade == "PermissionRequest+PostToolUse audit"


def test_shim_a_emitter_adds_permission_and_posttooluse_fallbacks():
    bundle = compile_to_codex_requirements([_evidence("p", matcher="Read")])
    toml = bundle.requirements_toml
    # primary PreToolUse hook on the silent-skip tool...
    assert '[[hooks.PreToolUse]]\nmatcher = "Read"' in toml
    # ...plus the two fallbacks so the gate still sees the tool.
    assert '[[hooks.PermissionRequest]]\nmatcher = "Read"' in toml
    assert '[[hooks.PostToolUse]]\nmatcher = "Read"' in toml


def test_shim_a_covered_tool_gets_no_fallback():
    bundle = compile_to_codex_requirements([_evidence("p", matcher="Bash")])
    hooks = json.loads(bundle.hooks_json_sidecar)["hooks"]
    # Bash IS covered by Codex PreToolUse — only the primary hook, no
    # PermissionRequest / PostToolUse fallback.
    assert list(hooks.keys()) == ["PreToolUse"]
    assert [e["matcher"] for e in hooks["PreToolUse"]] == ["Bash"]


# ── Shim B: additionalContext rejection ──────────────────────────────
def test_shim_b_turn_scope_rewrites_to_system_message():
    driver = CodexDriver()
    obj = _emit_obj(driver, Verdict(
        decision="allow", hook_event_name="PreToolUse",
        additional_context="cite your sources", context_scope="turn",
        session_id="sess-turn",
    ))
    # additionalContext is gone; the context rode out on systemMessage.
    assert obj == {"systemMessage": "cite your sources"}
    assert "hookSpecificOutput" not in obj


def test_shim_b_turn_scope_is_default_when_scope_unset():
    driver = CodexDriver()
    obj = _emit_obj(driver, Verdict(
        decision="allow", hook_event_name="PreToolUse",
        additional_context="note", session_id="sess-x",
    ))
    assert obj == {"systemMessage": "note"}


def test_shim_b_session_scope_defers_to_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CP_STATE_DIR", str(tmp_path))
    driver = CodexDriver()
    # session-scope PreToolUse verdict: nothing emitted now, queued.
    out = driver.emit_verdict(Verdict(
        decision="allow", hook_event_name="PreToolUse",
        additional_context="deferred note", context_scope="session",
        session_id="sess-A",
    ))
    assert out == b""  # silent — no additionalContext on the wire
    queue = tmp_path / "sess-A" / "pending_context.jsonl"
    assert queue.exists()

    # next UserPromptSubmit drains the queue into additionalContext.
    obj = _emit_obj(driver, Verdict(
        decision="allow", hook_event_name="UserPromptSubmit",
        session_id="sess-A",
    ))
    hso = obj["hookSpecificOutput"]
    assert hso["hookEventName"] == "UserPromptSubmit"
    assert "deferred note" in hso["additionalContext"]
    # drain is single-shot — the queue file is consumed.
    assert not queue.exists()


def test_shim_b_queue_file_and_dir_have_restrictive_perms(tmp_path, monkeypatch):
    import os
    import stat

    monkeypatch.setenv("MAGI_CP_STATE_DIR", str(tmp_path))
    driver = CodexDriver()
    driver.emit_verdict(Verdict(
        decision="allow", hook_event_name="PreToolUse",
        additional_context="secret payload", context_scope="session",
        session_id="sess-perm",
    ))
    session_dir = tmp_path / "sess-perm"
    queue = session_dir / "pending_context.jsonl"
    # dir 0o700, file 0o600 — not group/world readable on a shared host.
    assert stat.S_IMODE(os.stat(session_dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(queue).st_mode) == 0o600


def test_shim_b_drain_is_single_shot_and_atomic_claim(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CP_STATE_DIR", str(tmp_path))
    driver = CodexDriver()
    driver.emit_verdict(Verdict(
        decision="allow", hook_event_name="PreToolUse",
        additional_context="once", context_scope="session",
        session_id="sess-once",
    ))
    # First drain claims + consumes the queue.
    obj = _emit_obj(driver, Verdict(
        decision="allow", hook_event_name="UserPromptSubmit",
        session_id="sess-once",
    ))
    assert "once" in obj["hookSpecificOutput"]["additionalContext"]
    # Second drain finds nothing (no re-injection, no leftover claim file).
    out = driver.emit_verdict(Verdict(
        decision="allow", hook_event_name="UserPromptSubmit",
        session_id="sess-once",
    ))
    assert out == b""
    leftovers = list((tmp_path / "sess-once").glob("*"))
    assert leftovers == []


def test_shim_b_queue_is_scoped_per_session(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CP_STATE_DIR", str(tmp_path))
    driver = CodexDriver()
    driver.emit_verdict(Verdict(
        decision="allow", hook_event_name="PreToolUse",
        additional_context="A secret", context_scope="session",
        session_id="sess-A",
    ))
    # Session B draining its own (empty) queue never sees A's context.
    out = driver.emit_verdict(Verdict(
        decision="allow", hook_event_name="UserPromptSubmit",
        session_id="sess-B",
    ))
    assert out == b""  # B has nothing queued
    # A's queue is untouched by B's drain.
    assert (tmp_path / "sess-A" / "pending_context.jsonl").exists()
    assert not (tmp_path / "sess-B" / "pending_context.jsonl").exists()


def test_shim_b_session_scope_coverage_downgrade_on_context_injection():
    driver = CodexDriver()
    ctx = ContextInjectionPolicy(
        id="ctx", description="t", event="PreToolUse",
        template="always cite", matcher="Bash",
    )
    report = driver.coverage_report([ctx])
    entry = report.policies[0]
    assert entry.status == "enforced"
    assert entry.downgrade == "system_message"


# ── Shim C: SessionEnd absence ───────────────────────────────────────
def test_shim_c_stop_active_synthesizes_session_end():
    driver = CodexDriver()
    payload = {"hook_event_name": "Stop", "stop_hook_active": True,
               "session_id": "s1"}
    ev = driver.parse_hook_payload(json.dumps(payload).encode())
    assert ev.hook_event_name == "SessionEnd"
    # raw stays the original Stop payload verbatim.
    assert ev.raw["hook_event_name"] == "Stop"


def test_shim_c_stop_inactive_stays_stop():
    driver = CodexDriver()
    payload = {"hook_event_name": "Stop", "stop_hook_active": False,
               "session_id": "s1"}
    ev = driver.parse_hook_payload(json.dumps(payload).encode())
    assert ev.hook_event_name == "Stop"


def test_shim_c_session_end_policy_coverage_marker():
    driver = CodexDriver()
    report = driver.coverage_report(
        [_evidence("se", event="SessionEnd", matcher="*", action="audit")]
    )
    entry = report.policies[0]
    assert entry.status == "codex_no_session_end"
    assert entry.downgrade == "Stop stop_hook_active + cloud sweeper"


# ── Shim D: subagent hook fanout gap ─────────────────────────────────
def test_shim_d_subagent_lifecycle_coverage_marker():
    driver = CodexDriver()
    report = driver.coverage_report(
        [_evidence("sa", event="SubagentStop", matcher="*", action="audit")]
    )
    entry = report.policies[0]
    assert entry.status == "codex_internal_subagent_gap"
    assert entry.downgrade == "spawn_agent PreToolUse+PostToolUse mirror"


def test_shim_d_emitter_adds_spawn_agent_fallbacks():
    bundle = compile_to_codex_requirements(
        [_evidence("sa", event="SubagentStop", matcher="*", action="audit")]
    )
    hooks = json.loads(bundle.hooks_json_sidecar)["hooks"]
    # primary SubagentStop hook is present...
    assert "SubagentStop" in hooks
    # ...plus the belt-and-suspenders spawn_agent mirror on the covered
    # PreToolUse + PostToolUse events.
    assert "spawn_agent" in [e["matcher"] for e in hooks["PreToolUse"]]
    assert "spawn_agent" in [e["matcher"] for e in hooks["PostToolUse"]]


def test_shim_d_lifecycle_only_policy_forces_multi_agent():
    # A subagent-LIFECYCLE policy authored WITHOUT an accompanying
    # SubagentPolicy still binds spawn_agent mirror hooks, which Codex only
    # enables under features.multi_agent = true. The emitter must force the
    # toggle on, otherwise the belt-and-suspenders hooks are silently inert.
    bundle = compile_to_codex_requirements(
        [_evidence("sa", event="SubagentStop", matcher="*", action="audit")]
    )
    assert "multi_agent = true" in bundle.requirements_toml


def test_task_single_spawn_is_not_silent_skip_shimmed():
    # CC's Task maps onto Codex spawn_agent, which IS a covered PreToolUse
    # tool (Shim D). A PreToolUse policy on Task must fire, not get a
    # silent-skip fallback: only the primary hook, no PermissionRequest /
    # PostToolUse mirror, and coverage stays "enforced".
    assert "Task" not in CODEX_SILENT_SKIP_TOOLS
    driver = CodexDriver()
    report = driver.coverage_report([_evidence("t", matcher="Task")])
    assert report.policies[0].status == "enforced"
    bundle = compile_to_codex_requirements([_evidence("t", matcher="Task")])
    hooks = json.loads(bundle.hooks_json_sidecar)["hooks"]
    assert list(hooks.keys()) == ["PreToolUse"]
    assert [e["matcher"] for e in hooks["PreToolUse"]] == ["Task"]
