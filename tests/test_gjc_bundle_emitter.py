"""U4 gjc bundle emitter: byte-stable manifest + sha256 + file-key set + schema + install-paths.

Design brief: 2026-07-08-magi-cp-gajae-code-runtime-adapter-design
Section 11.1 U4 sub-tests (a)-(e).

``compile_to_gjc_bundle(ir)`` in ``policy/gjc_bundle_emitter.py`` is the
peer of ``compile_to_codex_requirements`` in ``policy/codex_toml_emitter.py``.
It returns a ``ManagedConfigBundle`` whose ``files`` dict contains:

  "gajae-plugin.json"                  — manifest with real sha256 values
  "hooks/magi-gate-tool-call.ts"       — THE tool_call gate shim (frozen)
  "hooks/magi-gate-session-start.ts"   — session_start observer
  "hooks/magi-gate-session-shutdown.ts"— session_shutdown observer
  "magi-cp-tool-map.json"              — normalization-table sidecar

(§6.1 exact set; context_templates is empty in v1)

Sub-tests:
  (a) byte-stable manifest golden (two calls, identical bytes)
  (b) manifest sha256 values == hashlib.sha256 of the emitted shim bytes
  (c) ManagedConfigBundle.files keys == exact §6.1 set
  (d) manifest parses under a Python re-expression of parseHooks schema
      (schema.ts:125-150 field constraints)
  (e) install-paths golden against §6.2
"""
from __future__ import annotations

import hashlib
import json
import pytest

# Imports under test — expected to exist after U4 GREEN
from magi_cp.policy.gjc_bundle_emitter import compile_to_gjc_bundle  # type: ignore[import]
from magi_cp.runtime.gjc import GjcDriver
from magi_cp.runtime.trait import InstallPaths, ManagedConfigBundle
from magi_cp.policy.ir import (
    PermissionPolicy,
    SubagentPolicy,
    Trigger,
)

# ── Fixed minimal IR for golden tests ──────────────────────────────────


def _perm(pid: str, pattern: str = "Bash(.*)", permission: str = "allow") -> PermissionPolicy:
    return PermissionPolicy(
        id=pid, description="test",
        trigger=Trigger(host="gjc", event="PreToolUse", matcher="Bash"),
        permission=permission, pattern=pattern,
    )


def _subagent(pid: str) -> SubagentPolicy:
    return SubagentPolicy(
        id=pid, description="test",
        subagent_type="all",
        tool_allowlist=[],
    )


# A minimal but non-empty IR that exercises the emitter without
# requiring every IR node type to exist.
_GOLDEN_IR = [_perm("p1", "Bash(.*)", "deny"), _subagent("s1")]


# ── (a) byte-stable manifest golden ────────────────────────────────────


def test_compile_returns_managed_config_bundle() -> None:
    """compile_to_gjc_bundle returns a ManagedConfigBundle."""
    bundle = compile_to_gjc_bundle(_GOLDEN_IR)
    assert isinstance(bundle, ManagedConfigBundle)


def test_manifest_byte_stable_across_two_calls() -> None:
    """(a) identical IR -> identical manifest bytes on two separate calls."""
    b1 = compile_to_gjc_bundle(_GOLDEN_IR)
    b2 = compile_to_gjc_bundle(_GOLDEN_IR)
    assert b1.files["gajae-plugin.json"] == b2.files["gajae-plugin.json"], (
        "Manifest is not byte-stable across two calls with the same IR"
    )


def test_manifest_byte_stable_reordered_ir() -> None:
    """(a) order-invariant: reversed IR produces identical manifest."""
    b1 = compile_to_gjc_bundle(_GOLDEN_IR)
    b2 = compile_to_gjc_bundle(list(reversed(_GOLDEN_IR)))
    assert b1.files["gajae-plugin.json"] == b2.files["gajae-plugin.json"], (
        "Manifest differs when IR is reordered — emitter is not order-invariant"
    )


def test_empty_ir_produces_manifest() -> None:
    """(a) empty IR still produces a valid manifest (hooks are static; IR only drives tool-map).

    After the install-blocking bug fix, the manifest carries 36 hooks:
    34 tool_call (one per gjc builtin tool, with target+phase) + 2 session.
    """
    bundle = compile_to_gjc_bundle([])
    assert "gajae-plugin.json" in bundle.files
    manifest = json.loads(bundle.files["gajae-plugin.json"])
    assert "hooks" in manifest and len(manifest["hooks"]) == 36, (
        f"Expected 36 hooks (34 tool_call + 2 session), got {len(manifest['hooks'])}"
    )


# ── (b) manifest sha256 == hash of emitted shim bytes ──────────────────

# After fanout, tool_call entries have names like "magi-gate-tool-call-bash".
# We test session entries by exact name, and tool_call entries as a group.
_SESSION_SHIM_FILE_KEYS = [
    ("magi-gate-session-start", "hooks/magi-gate-session-start.ts"),
    ("magi-gate-session-shutdown", "hooks/magi-gate-session-shutdown.ts"),
]


@pytest.mark.parametrize("hook_name,file_key", _SESSION_SHIM_FILE_KEYS)
def test_manifest_sha256_matches_session_shim_bytes(hook_name: str, file_key: str) -> None:
    """(b) manifest sha256 for each session hook == hashlib.sha256 of the emitted shim bytes."""
    bundle = compile_to_gjc_bundle(_GOLDEN_IR)
    manifest = json.loads(bundle.files["gajae-plugin.json"])
    hook_entry = next(
        (h for h in manifest["hooks"] if h["name"] == hook_name),
        None,
    )
    assert hook_entry is not None, f"Session hook {hook_name!r} not found in manifest"
    declared_sha256: str = hook_entry["sha256"]
    assert declared_sha256 != "<computed>", (
        f"Hook {hook_name!r} sha256 is still the template placeholder"
    )
    shim_bytes: bytes = bundle.files[file_key].encode("utf-8")
    computed = hashlib.sha256(shim_bytes).hexdigest()
    assert computed == declared_sha256, (
        f"Hook {hook_name!r}: manifest sha256={declared_sha256!r} "
        f"but hashlib.sha256(shim_bytes)={computed!r}"
    )


def test_manifest_sha256_matches_tool_call_shim_bytes() -> None:
    """(b) all magi-gate-tool-call-* hooks declare sha256 == hashlib.sha256(tool_call shim bytes).

    After fanout, 34 entries share the same shim file; all must carry the
    same correct hash.
    """
    bundle = compile_to_gjc_bundle(_GOLDEN_IR)
    manifest = json.loads(bundle.files["gajae-plugin.json"])
    shim_key = "hooks/magi-gate-tool-call.ts"
    expected_sha256 = hashlib.sha256(
        bundle.files[shim_key].encode("utf-8")
    ).hexdigest()

    tool_call_hooks = [
        h for h in manifest["hooks"] if h["name"].startswith("magi-gate-tool-call-")
    ]
    assert len(tool_call_hooks) == 34, (
        f"Expected 34 tool_call hooks, got {len(tool_call_hooks)}"
    )
    for hook in tool_call_hooks:
        declared = hook["sha256"]
        assert declared == expected_sha256, (
            f"Hook {hook['name']!r}: sha256={declared!r} does not match "
            f"tool_call shim sha256={expected_sha256!r}"
        )


def test_manifest_sha256_is_hex_string() -> None:
    """(b) each sha256 value is a 64-char lowercase hex string."""
    bundle = compile_to_gjc_bundle(_GOLDEN_IR)
    manifest = json.loads(bundle.files["gajae-plugin.json"])
    for hook in manifest["hooks"]:
        sha = hook["sha256"]
        assert isinstance(sha, str) and len(sha) == 64, (
            f"Hook {hook['name']!r}: sha256={sha!r} is not a 64-char hex string"
        )
        assert sha == sha.lower(), f"sha256 should be lowercase: {sha!r}"
        int(sha, 16)  # raises ValueError if not valid hex


# ── (c) ManagedConfigBundle.files keys == exact §6.1 set ───────────────

_EXPECTED_FILE_KEYS = {
    "gajae-plugin.json",
    "hooks/magi-gate-tool-call.ts",
    "hooks/magi-gate-session-start.ts",
    "hooks/magi-gate-session-shutdown.ts",
    "magi-cp-tool-map.json",
}


def test_bundle_files_keys_exact_set() -> None:
    """(c) bundle.files keys == exact §6.1 set (no more, no less)."""
    bundle = compile_to_gjc_bundle(_GOLDEN_IR)
    actual = set(bundle.files.keys())
    assert actual == _EXPECTED_FILE_KEYS, (
        f"bundle.files key set mismatch.\n"
        f"  Missing: {_EXPECTED_FILE_KEYS - actual}\n"
        f"  Extra:   {actual - _EXPECTED_FILE_KEYS}"
    )


def test_bundle_context_templates_empty_v1() -> None:
    """(c) context_templates is empty in v1 (§6.1)."""
    bundle = compile_to_gjc_bundle(_GOLDEN_IR)
    assert bundle.context_templates == {}, (
        f"context_templates must be empty in v1, got: {list(bundle.context_templates)}"
    )


def test_bundle_files_all_str() -> None:
    """(c) every file value in bundle.files is a str (text bytes)."""
    bundle = compile_to_gjc_bundle(_GOLDEN_IR)
    for key, val in bundle.files.items():
        assert isinstance(val, str), f"bundle.files[{key!r}] is {type(val).__name__}, expected str"


def test_tool_map_sidecar_is_valid_json() -> None:
    """(c) magi-cp-tool-map.json is parseable JSON."""
    bundle = compile_to_gjc_bundle(_GOLDEN_IR)
    tool_map = json.loads(bundle.files["magi-cp-tool-map.json"])
    assert isinstance(tool_map, dict), "tool-map sidecar must be a JSON object"


def test_tool_map_contains_bash() -> None:
    """(c) tool-map sidecar contains the bash -> Bash mapping (sanity check)."""
    bundle = compile_to_gjc_bundle(_GOLDEN_IR)
    tool_map = json.loads(bundle.files["magi-cp-tool-map.json"])
    assert tool_map.get("bash") == "Bash", f"Expected bash->Bash in tool-map: {tool_map}"


# ── (d) manifest schema validation (Python re-expression of schema.ts:125-150) ─


def _validate_manifest(manifest: dict) -> list[str]:
    """Re-expression of gjc parseHooks field constraints (schema.ts:125-150).

    Returns a list of error strings; empty list means valid.
    """
    errors: list[str] = []
    if not isinstance(manifest.get("name"), str) or not manifest["name"]:
        errors.append("manifest.name must be a non-empty string")
    if not isinstance(manifest.get("version"), (str, int)):
        errors.append("manifest.version must be a string or int")
    if not isinstance(manifest.get("description"), str):
        errors.append("manifest.description must be a string")
    hooks = manifest.get("hooks")
    if not isinstance(hooks, list):
        errors.append("manifest.hooks must be an array")
        return errors
    for i, hook in enumerate(hooks):
        if not isinstance(hook.get("name"), str) or not hook["name"]:
            errors.append(f"hooks[{i}].name must be a non-empty string")
        if not isinstance(hook.get("event"), str) or not hook["event"]:
            errors.append(f"hooks[{i}].event must be a non-empty string")
        if not isinstance(hook.get("path"), str) or not hook["path"]:
            errors.append(f"hooks[{i}].path must be a non-empty string")
        # phase is optional; if present must be "before" or "after"
        if "phase" in hook:
            if hook["phase"] not in ("before", "after"):
                errors.append(f"hooks[{i}].phase must be 'before' or 'after' if present")
        # target is optional (no constraint on value)
        # sha256 is optional; if present must be a string
        if "sha256" in hook:
            if not isinstance(hook["sha256"], str):
                errors.append(f"hooks[{i}].sha256 must be a string if present")
    return errors


def test_manifest_passes_schema_validation() -> None:
    """(d) emitted manifest parses under a Python re-expression of parseHooks schema."""
    bundle = compile_to_gjc_bundle(_GOLDEN_IR)
    manifest = json.loads(bundle.files["gajae-plugin.json"])
    errors = _validate_manifest(manifest)
    assert not errors, f"Manifest failed schema validation: {errors}"


def test_manifest_tool_call_hooks_have_phase_before() -> None:
    """(d) all tool_call hooks carry phase=='before' (required by gjc compiler.ts:236-246).

    After the install-blocking bug fix, tool_call hooks require both
    target and phase; 'before' is the correct value for pre-execution gates.
    """
    bundle = compile_to_gjc_bundle(_GOLDEN_IR)
    manifest = json.loads(bundle.files["gajae-plugin.json"])
    tool_call_hooks = [h for h in manifest["hooks"] if h["event"] == "tool_call"]
    assert tool_call_hooks, "No tool_call hooks found in manifest"
    for hook in tool_call_hooks:
        assert hook.get("phase") == "before", (
            f"tool_call hook {hook['name']!r} has phase={hook.get('phase')!r}, "
            "expected 'before'"
        )


def test_manifest_session_hooks_have_no_phase_field() -> None:
    """(d) session_start / session_shutdown hooks do NOT have a phase field."""
    bundle = compile_to_gjc_bundle(_GOLDEN_IR)
    manifest = json.loads(bundle.files["gajae-plugin.json"])
    session_hooks = [
        h for h in manifest["hooks"]
        if h["event"] in ("session_start", "session_shutdown")
    ]
    assert len(session_hooks) == 2, f"Expected 2 session hooks, got {len(session_hooks)}"
    for hook in session_hooks:
        assert "phase" not in hook, (
            f"Session hook {hook['name']!r} should not have a 'phase' field: {hook}"
        )


def test_manifest_tool_call_hooks_have_target_field() -> None:
    """(d) all tool_call hooks carry a non-empty 'target' field (gjc install requirement)."""
    bundle = compile_to_gjc_bundle(_GOLDEN_IR)
    manifest = json.loads(bundle.files["gajae-plugin.json"])
    tool_call_hooks = [h for h in manifest["hooks"] if h["event"] == "tool_call"]
    assert tool_call_hooks, "No tool_call hooks found in manifest"
    for hook in tool_call_hooks:
        assert "target" in hook and hook["target"], (
            f"tool_call hook {hook['name']!r} is missing a non-empty 'target' field"
        )


def test_manifest_session_hooks_have_no_target_field() -> None:
    """(d) session_start / session_shutdown hooks do NOT have a 'target' field."""
    bundle = compile_to_gjc_bundle(_GOLDEN_IR)
    manifest = json.loads(bundle.files["gajae-plugin.json"])
    session_hooks = [
        h for h in manifest["hooks"]
        if h["event"] in ("session_start", "session_shutdown")
    ]
    assert len(session_hooks) == 2, f"Expected 2 session hooks, got {len(session_hooks)}"
    for hook in session_hooks:
        assert "target" not in hook, (
            f"Session hook {hook['name']!r} should not have a 'target' field"
        )


def test_manifest_hook_paths_match_bundle_keys() -> None:
    """(d) each hook path (prefixed with 'hooks/') corresponds to a bundle file key."""
    bundle = compile_to_gjc_bundle(_GOLDEN_IR)
    manifest = json.loads(bundle.files["gajae-plugin.json"])
    for hook in manifest["hooks"]:
        path: str = hook["path"]
        assert path in bundle.files, (
            f"Hook {hook['name']!r} declares path={path!r} but it is not in bundle.files"
        )


# ── (e) install-paths golden ────────────────────────────────────────────


def test_install_paths_managed_config_dir() -> None:
    """(e) managed_config_dir == §6.2 value."""
    paths: InstallPaths = GjcDriver().default_install_paths()
    assert paths.managed_config_dir == "~/.gjc/agent/gjc-plugins/magi-cp-gate"


def test_install_paths_slash_commands_dir() -> None:
    """(e) slash_commands_dir == §6.2 value."""
    paths: InstallPaths = GjcDriver().default_install_paths()
    assert paths.slash_commands_dir == "~/.gjc/agent/gjc-plugins/magi-cp-gate/commands"


def test_install_paths_context_templates_dir() -> None:
    """(e) context_templates_dir == §6.2 value."""
    paths: InstallPaths = GjcDriver().default_install_paths()
    assert paths.context_templates_dir == "~/.gjc/agent/gjc-plugins/magi-cp-gate/context-templates"


def test_install_paths_returns_install_paths_type() -> None:
    """(e) default_install_paths returns an InstallPaths instance."""
    result = GjcDriver().default_install_paths()
    assert isinstance(result, InstallPaths)


# ── GjcDriver.emit_managed_config wiring ───────────────────────────────


def test_emit_managed_config_no_longer_raises() -> None:
    """U4 wiring: GjcDriver.emit_managed_config(ir) delegates to compile_to_gjc_bundle.

    The PR-1 stub raised NotImplementedError; after U4 GREEN it must
    return a ManagedConfigBundle without raising.
    """
    driver = GjcDriver()
    result = driver.emit_managed_config(_GOLDEN_IR)
    assert isinstance(result, ManagedConfigBundle)


def test_emit_managed_config_returns_same_as_compile() -> None:
    """U4 wiring: GjcDriver.emit_managed_config delegates to compile_to_gjc_bundle."""
    driver = GjcDriver()
    direct = compile_to_gjc_bundle(_GOLDEN_IR)
    via_driver = driver.emit_managed_config(_GOLDEN_IR)
    assert via_driver.files == direct.files
    assert via_driver.context_templates == direct.context_templates


# ── Fanout completeness (install-blocking bug regression guard) ─────────


def test_fanout_completeness_all_builtin_tools_have_hook() -> None:
    """Every tool in _GJC_BUILTIN_TOOLS has a corresponding tool_call hook with
    target==tool and phase=='before'.  No target-less tool_call entries allowed.

    This is the regression guard for the install-blocking bug:
    gjc compiler.ts:236-246 rejects target-less tool_call hooks.  If _GJC_BUILTIN_TOOLS
    gains a new entry, the emitter must emit a hook for it automatically, and this
    test will pass without modification.  If the emitter stops fanning out, this test
    fails immediately.
    """
    from magi_cp.runtime.gjc import _GJC_BUILTIN_TOOLS

    bundle = compile_to_gjc_bundle(_GOLDEN_IR)
    manifest = json.loads(bundle.files["gajae-plugin.json"])
    tool_call_hooks = [h for h in manifest["hooks"] if h["event"] == "tool_call"]

    # Index by target value for easy lookup
    by_target: dict[str, dict] = {}
    for hook in tool_call_hooks:
        # Assert no target-less tool_call entries exist
        assert "target" in hook, (
            f"Found a target-less tool_call hook: {hook['name']!r} — "
            "install-blocking bug regression detected"
        )
        by_target[hook["target"]] = hook

    # Every tool in the SSOT must have exactly one hook
    missing = _GJC_BUILTIN_TOOLS - set(by_target.keys())
    assert not missing, (
        f"Missing tool_call hooks for tools: {sorted(missing)!r}. "
        "The emitter must fan out a hook per _GJC_BUILTIN_TOOLS entry."
    )

    # No extra hooks beyond the SSOT
    extra = set(by_target.keys()) - _GJC_BUILTIN_TOOLS
    assert not extra, (
        f"tool_call hooks for unknown tools (not in _GJC_BUILTIN_TOOLS): {sorted(extra)!r}"
    )

    # All tool_call hooks have phase=="before"
    wrong_phase = [
        hook["name"] for hook in tool_call_hooks
        if hook.get("phase") != "before"
    ]
    assert not wrong_phase, (
        f"tool_call hooks with wrong/missing phase (expected 'before'): {wrong_phase!r}"
    )
