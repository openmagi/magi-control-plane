"""P1 Codex adapter: HookRuntime trait + get_runtime dispatch.

Design brief: 2026-06-30-codex-runtime-adapter-design (private planning repo)
Section 3.1 (trait) + Section 10 P1 (tests bullet).
"""
from __future__ import annotations

import pytest

from magi_cp.runtime import (
    CoverageReport,
    HookEvent,
    HookRuntime,
    InstallPaths,
    ManagedConfigBundle,
    Verdict,
    get_runtime,
)
from magi_cp.runtime.cc import CCDriver
from magi_cp.runtime.codex import CodexDriver


# ── structural typing ────────────────────────────────────────────────
def test_cc_driver_satisfies_protocol():
    assert isinstance(CCDriver(), HookRuntime)


def test_codex_driver_satisfies_protocol():
    assert isinstance(CodexDriver(), HookRuntime)


def test_drivers_expose_canonical_runtime_ids():
    assert CCDriver().runtime_id == "claude-code"
    assert CodexDriver().runtime_id == "codex"


# ── get_runtime dispatch ─────────────────────────────────────────────
def test_get_runtime_cc_returns_cc_driver():
    assert isinstance(get_runtime("cc"), CCDriver)


def test_get_runtime_claude_code_alias_returns_cc_driver():
    assert isinstance(get_runtime("claude-code"), CCDriver)


def test_get_runtime_codex_returns_codex_driver():
    assert isinstance(get_runtime("codex"), CodexDriver)


def test_get_runtime_unknown_raises():
    with pytest.raises(ValueError):
        get_runtime("cursor")


# ── round-trippable canonical types are usable through the trait ─────
def test_driver_methods_return_canonical_types():
    driver = get_runtime("cc")
    event = driver.parse_hook_payload(
        b'{"hook_event_name": "PreToolUse", "tool_input": {"command": "ls"}}'
    )
    assert isinstance(event, HookEvent)
    assert event.hook_event_name == "PreToolUse"

    out = driver.emit_verdict(Verdict(decision="allow"))
    assert isinstance(out, bytes)

    bundle = driver.emit_managed_config([])
    assert isinstance(bundle, ManagedConfigBundle)

    report = driver.coverage_report([])
    assert isinstance(report, CoverageReport)
    assert report.runtime_id == "claude-code"

    paths = driver.default_install_paths()
    assert isinstance(paths, InstallPaths)


# ── CC universal side channels (continue / systemMessage) ────────────
def test_cc_plain_allow_stays_silent():
    # Byte-equivalence: a plain allow with no side channels is empty.
    driver = CCDriver()
    assert driver.emit_verdict(Verdict(decision="allow")) == b""


def test_cc_continue_false_layers_onto_silent_allow():
    import json

    driver = CCDriver()
    out = driver.emit_verdict(Verdict(decision="allow", continue_=False))
    assert json.loads(out.decode()) == {"continue": False}


def test_cc_system_message_layers_onto_deny():
    import json

    driver = CCDriver()
    out = driver.emit_verdict(Verdict(
        decision="deny", reason="no", hook_event_name="PreToolUse",
        system_message="stopped",
    ))
    obj = json.loads(out.decode())
    assert obj["systemMessage"] == "stopped"
    # per-event deny channel still present alongside the side channel.
    assert obj["hookSpecificOutput"]["permissionDecision"] == "deny"
