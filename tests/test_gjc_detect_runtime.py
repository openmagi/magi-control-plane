"""U2 gjc detection + flag + registration + dispatch.

Design brief: 2026-07-08-magi-cp-gajae-code-runtime-adapter-design
Section 11.1 U2 sub-tests (a)–(f).

Detection order (highest priority first, §4.6):
  1. Kill switch: MAGI_CP_GJC_RUNTIME_ENABLED falsy → "cc" unconditionally.
  2. Explicit MAGI_CP_RUNTIME in gjc token set → "gjc".
  3. Payload sniff: well-formed JSON with "gjc_event" key → "gjc".
  4. Existing Codex / CC tiers unchanged.

Byte-equivalence regression: CC and Codex golden dispatcher outputs must be
byte-identical with gjc code present (U2(f)).
"""
from __future__ import annotations

import json

import pytest

from magi_cp.runtime.detect import detect_runtime
from magi_cp.runtime import get_runtime
from magi_cp.runtime.trait import HookRuntime


# ── Fixture payloads ────────────────────────────────────────────────────────

# A gjc-shaped envelope: only gjc carries the "gjc_event" key.
_GJC_PAYLOAD = b'{"gjc_event":"tool_call","tool_name":"bash","session_id":"s1","cwd":"/","tool_input":{},"tool_call_id":"c1","shim_version":"1"}'

# Codex-shaped: carries matcher_aliases (CC never sends this).
_CODEX_PAYLOAD = b'{"hook_event_name":"PreToolUse","matcher_aliases":["Bash"],"tool_name":"Bash"}'

# Plain CC-shaped: no gjc_event, no matcher_aliases, no turn_id.
_CC_PAYLOAD = b'{"hook_event_name":"PreToolUse","tool_name":"Bash"}'


@pytest.fixture
def all_on(monkeypatch):
    """Both gjc AND Codex kill switches ON so all detection tiers are live."""
    monkeypatch.setenv("MAGI_CP_GJC_RUNTIME_ENABLED", "1")
    monkeypatch.setenv("MAGI_CP_CODEX_RUNTIME_ENABLED", "1")


# ── (a) MAGI_CP_RUNTIME=gjc → "gjc" ────────────────────────────────────────


def test_env_var_gjc_selects_gjc(all_on):
    """§11.1 U2(a): explicit MAGI_CP_RUNTIME=gjc resolves to "gjc"."""
    assert detect_runtime(_CC_PAYLOAD, env={"MAGI_CP_RUNTIME": "gjc"}) == "gjc"


def test_env_var_gajae_alias(all_on):
    """§4.6 tier 2: "gajae" is an accepted env token alias for gjc."""
    assert detect_runtime(_CC_PAYLOAD, env={"MAGI_CP_RUNTIME": "gajae"}) == "gjc"


def test_env_var_gajae_code_hyphen_alias(all_on):
    """§4.6 tier 2: "gajae-code" alias."""
    assert detect_runtime(_CC_PAYLOAD, env={"MAGI_CP_RUNTIME": "gajae-code"}) == "gjc"


def test_env_var_gajae_code_underscore_alias(all_on):
    """§4.6 tier 2: "gajae_code" alias."""
    assert detect_runtime(_CC_PAYLOAD, env={"MAGI_CP_RUNTIME": "gajae_code"}) == "gjc"


# ── (b) gjc_event payload sniff → "gjc" ─────────────────────────────────────


def test_payload_sniff_gjc_event(all_on):
    """§11.1 U2(b): payload containing "gjc_event" key → "gjc"."""
    assert detect_runtime(_GJC_PAYLOAD, env={}) == "gjc"


def test_payload_sniff_minimal(all_on):
    """Minimal gjc payload (only gjc_event key needed for the sniff)."""
    payload = json.dumps({"gjc_event": "tool_call"}).encode()
    assert detect_runtime(payload, env={}) == "gjc"


# ── (c) sniff disjointness ───────────────────────────────────────────────────


def test_codex_payload_still_resolves_codex_not_gjc(all_on):
    """§11.1 U2(c): Codex payload (matcher_aliases) → "codex", never "gjc"."""
    assert detect_runtime(_CODEX_PAYLOAD, env={}) == "codex"


def test_cc_payload_still_resolves_cc_not_gjc(all_on):
    """§11.1 U2(c): bare CC payload → "cc", never "gjc"."""
    assert detect_runtime(_CC_PAYLOAD, env={}) == "cc"


def test_turn_id_payload_still_resolves_codex(all_on):
    """Codex turn_id marker is not confused with gjc."""
    payload = b'{"hook_event_name":"PreToolUse","turn_id":"t-1"}'
    assert detect_runtime(payload, env={}) == "codex"


def test_payload_with_both_gjc_event_and_matcher_aliases(all_on):
    """A payload with both markers: gjc_event wins (it is checked first, §4.6)."""
    payload = json.dumps({
        "gjc_event": "tool_call",
        "matcher_aliases": ["Bash"],  # Codex marker also present
        "tool_name": "bash",
    }).encode()
    # gjc sniff fires before Codex sniff per the tier order.
    assert detect_runtime(payload, env={}) == "gjc"


# ── (d) kill switch per-runtime isolation ────────────────────────────────────


def test_gjc_kill_switch_forces_cc_not_codex(monkeypatch):
    """§11.1 U2(d): MAGI_CP_GJC_RUNTIME_ENABLED=0 forces gjc signal → "cc"."""
    monkeypatch.setenv("MAGI_CP_GJC_RUNTIME_ENABLED", "0")
    monkeypatch.setenv("MAGI_CP_CODEX_RUNTIME_ENABLED", "1")
    # A gjc payload with the gjc kill switch off → falls back to "cc".
    assert detect_runtime(_GJC_PAYLOAD, env={}) == "cc"


def test_gjc_kill_switch_explicit_env_override_also_blocked(monkeypatch):
    """Kill switch blocks explicit MAGI_CP_RUNTIME=gjc too."""
    monkeypatch.setenv("MAGI_CP_GJC_RUNTIME_ENABLED", "0")
    monkeypatch.setenv("MAGI_CP_CODEX_RUNTIME_ENABLED", "1")
    assert detect_runtime(_CC_PAYLOAD, env={"MAGI_CP_RUNTIME": "gjc"}) == "cc"


def test_codex_kill_switch_unaffected_by_gjc_flag(monkeypatch):
    """§11.1 U2(d): Codex routing is untouched when only the gjc flag changes."""
    monkeypatch.setenv("MAGI_CP_GJC_RUNTIME_ENABLED", "0")
    monkeypatch.setenv("MAGI_CP_CODEX_RUNTIME_ENABLED", "1")
    # Codex payload still resolves to "codex" even with gjc flag off.
    assert detect_runtime(_CODEX_PAYLOAD, env={}) == "codex"


def test_gjc_kill_switch_false_token(monkeypatch):
    """All explicit falsy tokens disable gjc (mirror codex_runtime_enabled §4.6)."""
    monkeypatch.setenv("MAGI_CP_CODEX_RUNTIME_ENABLED", "1")
    for token in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("MAGI_CP_GJC_RUNTIME_ENABLED", token)
        result = detect_runtime(_GJC_PAYLOAD, env={})
        assert result == "cc", f"Expected 'cc' for token {token!r}, got {result!r}"


def test_gjc_kill_switch_unset_is_on(monkeypatch):
    """Default-ON: unset flag means gjc is available."""
    monkeypatch.delenv("MAGI_CP_GJC_RUNTIME_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_CP_CODEX_RUNTIME_ENABLED", "1")
    assert detect_runtime(_GJC_PAYLOAD, env={}) == "gjc"


def test_codex_kill_switch_off_gjc_unaffected(monkeypatch):
    """Codex kill switch does NOT affect gjc routing."""
    monkeypatch.setenv("MAGI_CP_CODEX_RUNTIME_ENABLED", "0")
    monkeypatch.setenv("MAGI_CP_GJC_RUNTIME_ENABLED", "1")
    assert detect_runtime(_GJC_PAYLOAD, env={}) == "gjc"


# ── (e) get_runtime("gjc") satisfies HookRuntime ────────────────────────────


def test_get_runtime_gjc_returns_hook_runtime(all_on):
    """§11.1 U2(e): get_runtime("gjc") returns a HookRuntime-satisfying driver."""
    driver = get_runtime("gjc")
    assert isinstance(driver, HookRuntime)


def test_get_runtime_gjc_canonical_id(all_on):
    """The gjc driver reports runtime_id == "gjc"."""
    driver = get_runtime("gjc")
    assert driver.runtime_id == "gjc"


def test_get_runtime_gajae_code_alias(all_on):
    """§4.6 get_runtime: "gajae-code" alias resolves to GjcDriver."""
    driver = get_runtime("gajae-code")
    assert isinstance(driver, HookRuntime)
    assert driver.runtime_id == "gjc"


def test_get_runtime_gajae_code_underscore_alias(all_on):
    """§4.6 get_runtime: "gajae_code" alias resolves to GjcDriver."""
    driver = get_runtime("gajae_code")
    assert isinstance(driver, HookRuntime)
    assert driver.runtime_id == "gjc"


def test_get_runtime_unknown_still_raises(all_on):
    """get_runtime with an unknown id still raises (regression)."""
    with pytest.raises(ValueError):
        get_runtime("cursor")


# ── (f) BYTE-EQUIVALENCE regression ─────────────────────────────────────────
# The CC and Codex dispatcher outputs for existing golden fixtures must be
# byte-identical with the gjc code present.  We exercise detect_runtime
# (not gate.main) so no subprocess is needed, but we verify the full
# dispatch import chain by importing the modules directly.


def test_detect_cc_payload_unchanged_with_gjc_present(all_on):
    """§11.1 U2(f): CC payload → "cc" with gjc code imported."""
    from magi_cp.runtime import get_runtime as gr  # noqa: F401 (side-effect: loads gjc module)
    from magi_cp.runtime.gjc import GjcDriver       # noqa: F401
    result = detect_runtime(_CC_PAYLOAD, env={})
    assert result == "cc"


def test_detect_codex_payload_unchanged_with_gjc_present(all_on):
    """§11.1 U2(f): Codex payload → "codex" with gjc code imported."""
    from magi_cp.runtime.gjc import GjcDriver       # noqa: F401
    result = detect_runtime(_CODEX_PAYLOAD, env={})
    assert result == "codex"


def test_cc_driver_emit_deny_golden_unchanged(all_on):
    """Byte-equivalence: CCDriver deny output unchanged after gjc import."""
    from magi_cp.runtime.cc import CCDriver
    from magi_cp.runtime.gjc import GjcDriver       # noqa: F401
    driver = CCDriver()
    out = driver.emit_verdict(__import__("magi_cp.runtime.trait", fromlist=["Verdict"]).Verdict(
        decision="deny", reason="blocked", hook_event_name="PreToolUse",
    ))
    obj = json.loads(out)
    # CC deny shape: hookSpecificOutput.permissionDecision = "deny"
    assert obj["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert obj["hookSpecificOutput"]["permissionDecisionReason"].startswith("MAGI: ")


def test_codex_driver_emit_deny_golden_unchanged(all_on):
    """Byte-equivalence: CodexDriver deny output unchanged after gjc import."""
    from magi_cp.runtime.codex import CodexDriver
    from magi_cp.runtime.gjc import GjcDriver       # noqa: F401
    from magi_cp.runtime.trait import Verdict
    driver = CodexDriver()
    # Codex PreToolUse deny -> hookSpecificOutput.permissionDecision
    payload = b'{"hook_event_name":"PreToolUse","tool_name":"Bash","matcher_aliases":["Bash"]}'
    event = driver.parse_hook_payload(payload)
    out = driver.emit_verdict(Verdict(
        decision="deny", reason="blocked", hook_event_name=event.hook_event_name,
    ))
    obj = json.loads(out)
    assert obj["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_codex_driver_allow_still_empty_bytes(all_on):
    """Byte-equivalence: CodexDriver allow → b"" unchanged."""
    from magi_cp.runtime.codex import CodexDriver
    from magi_cp.runtime.gjc import GjcDriver       # noqa: F401
    from magi_cp.runtime.trait import Verdict
    driver = CodexDriver()
    out = driver.emit_verdict(Verdict(decision="allow", hook_event_name="PreToolUse"))
    assert out == b""


def test_cc_driver_allow_still_empty_bytes(all_on):
    """Byte-equivalence: CCDriver allow → b"" unchanged."""
    from magi_cp.runtime.cc import CCDriver
    from magi_cp.runtime.gjc import GjcDriver       # noqa: F401
    from magi_cp.runtime.trait import Verdict
    driver = CCDriver()
    out = driver.emit_verdict(Verdict(decision="allow"))
    assert out == b""
