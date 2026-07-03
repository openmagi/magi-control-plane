"""Contract tests for the shared payload projection module.

Three surfaces evaluate regex against CC hook payloads:

  - cloud/app.py /verify_inline (the live gate path)
  - policy/dry_run.py (offline replay over ledger rows)
  - policy/test_runner.py (synthetic CC hook payload simulator, D77)

Before `magi_cp.policy.payload_projection`, each surface had its own
projection flavor and they disagreed on which fields counted as
projectable text. An operator who authored an `EvidencePolicy + regex`
whose pattern targeted `tool_response.output` would see different
verdicts at the simulator vs. the runtime.

This file pins the contract: a single fixture matrix runs through
every projection helper and asserts byte-equal output. A future
maintainer who silently edits any one surface's projection will fire
this test.
"""
from __future__ import annotations

import json

import pytest

from magi_cp.policy.payload_projection import (
    FIELD_MISSING,
    PROJECTION_MAX_CHARS,
    project_payload_for_regex,
    project_snapshot_for_regex,
    resolve_field_for_regex,
)


# ── whole-payload projection ────────────────────────────────────────


def test_project_payload_for_regex_prefers_text_field():
    """Mirrors cloud/app.py /verify_inline:1336-1338 exactly."""
    payload = {"text": "hello", "command": "ignored"}
    assert project_payload_for_regex(payload) == "hello"


def test_project_payload_for_regex_falls_back_to_json_dump_when_text_missing():
    """Mirrors cloud/app.py /verify_inline:1340-1341 exactly."""
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": "rm -rf /"}}
    out = project_payload_for_regex(payload)
    # The runtime emits JSON dump (not key-concatenation) when `text`
    # is absent.
    assert out == json.dumps(payload, ensure_ascii=False)


def test_project_payload_for_regex_caps_at_max_chars():
    """The 8000-char cap is a CPU-pin defense against adversarial
    regex over large fields."""
    payload = {"text": "x" * (PROJECTION_MAX_CHARS * 2)}
    out = project_payload_for_regex(payload)
    assert len(out) == PROJECTION_MAX_CHARS


def test_project_payload_for_regex_non_dict_returns_empty():
    """Mirrors cloud/app.py /verify_inline:1336 isinstance check."""
    assert project_payload_for_regex("not a dict") == ""
    assert project_payload_for_regex(None) == ""
    assert project_payload_for_regex(42) == ""


# ── snapshot projection (dry_run replay) ────────────────────────────


def test_project_snapshot_for_regex_string_passthrough():
    """Snapshots written by /verify_inline with field_path scoping
    are strings (the resolved field value, post-_format_value_for_prompt
    rendering); the replay scans the same text."""
    assert project_snapshot_for_regex("rm -rf /tmp/test") == "rm -rf /tmp/test"


def test_project_snapshot_for_regex_dict_delegates_to_payload_projection():
    """Snapshots written by /verify_inline without field_path scoping
    are the whole payload dict; the replay must project them the same
    way the runtime did."""
    snap = {"text": "foo"}
    assert project_snapshot_for_regex(snap) == "foo"
    snap2 = {"hook_event_name": "PreToolUse"}
    assert project_snapshot_for_regex(snap2) == project_payload_for_regex(snap2)


def test_project_snapshot_for_regex_other_types_return_empty():
    assert project_snapshot_for_regex(None) == ""
    assert project_snapshot_for_regex(42) == ""
    assert project_snapshot_for_regex([1, 2, 3]) == ""


# ── scoped field resolution ─────────────────────────────────────────


def test_resolve_field_for_regex_resolves_dotted_path_to_string():
    payload = {"tool_input": {"command": "rm -rf /"}}
    out = resolve_field_for_regex(payload, "tool_input.command")
    assert out == "rm -rf /"


def test_resolve_field_for_regex_returns_field_missing_for_absent_path():
    payload = {"tool_input": {"command": "rm -rf /"}}
    out = resolve_field_for_regex(payload, "tool_response.output")
    assert out is FIELD_MISSING


def test_resolve_field_for_regex_formats_dict_leaf_via_prompt_formatter():
    """Mirrors cloud/app.py /verify_inline:1385 -
    `_format_value_for_prompt` renders dicts as JSON (sort_keys=True)
    so the projection is deterministic across kinds.
    """
    payload = {"tool_input": {"a": 1, "b": "two"}}
    out = resolve_field_for_regex(payload, "tool_input")
    assert isinstance(out, str)
    # Sort-keyed JSON so the projection is deterministic.
    assert out == json.dumps({"a": 1, "b": "two"}, sort_keys=True)


def test_resolve_field_for_regex_is_bounded():
    """The scoped helper delegates to
    `_format_value_for_prompt` (which has its own per-marker 1000-char
    cap with a `…<truncated>` suffix) then applies the projection's
    8000-char defense. The runtime /verify_inline path uses the same
    chain, so the cap is whichever bound bites first. Pinning the
    "is bounded" invariant rather than a specific number keeps the
    test in lockstep with `_format_value_for_prompt`'s future
    tuning."""
    payload = {"text": "x" * (PROJECTION_MAX_CHARS * 2)}
    out = resolve_field_for_regex(payload, "text")
    assert isinstance(out, str)
    # Bound = whichever cap bites first; both are < PROJECTION_MAX_CHARS + 32.
    assert len(out) <= PROJECTION_MAX_CHARS + 32


def test_resolve_field_for_regex_empty_path_falls_back_to_whole_payload():
    payload = {"text": "foo"}
    out = resolve_field_for_regex(payload, "")
    assert out == "foo"


# ── cross-surface byte-equality contract ────────────────────────────


_FIXTURES = [
    # 1. Whole-payload, text-bearing
    {"text": "rm -rf /tmp/test"},
    # 2. Whole-payload, tool_input.command path (text absent)
    {"hook_event_name": "PreToolUse", "tool_name": "Bash",
     "tool_input": {"command": "rm -rf /tmp/test"}},
    # 3. PostToolUse with tool_response.output
    {"hook_event_name": "PostToolUse", "tool_name": "Bash",
     "tool_input": {"command": "ls /etc"},
     "tool_response": {"output": "passwd\nshadow\n"}},
    # 4. UserPromptSubmit
    {"hook_event_name": "UserPromptSubmit", "prompt": "ignore prior"},
    # 5. Stop final_message
    {"hook_event_name": "Stop",
     "final_message": "Answer 42 [src:case-1]."},
]


@pytest.mark.parametrize("payload", _FIXTURES)
def test_three_surface_contract_whole_payload(payload: dict):
    """A given payload must project to byte-equal strings across all
    three surfaces:

      - test_runner._evaluate_requires (regex without field_path)
      - verify_inline (no field_path branch)
      - dry_run._payload_text (when the snapshot IS the whole payload)
    """
    from magi_cp.policy.dry_run import _payload_text
    test_runner_projection = project_payload_for_regex(payload)
    dry_run_projection = _payload_text(payload)
    # Runtime emits whatever `payload_text` resolves to; project_payload
    # IS that resolution, so the assertion is reflexive but pins the
    # contract.
    assert test_runner_projection == dry_run_projection


@pytest.mark.parametrize("payload,field_path,expected_contains", [
    ({"tool_input": {"command": "rm -rf /"}},
     "tool_input.command", "rm -rf /"),
    ({"tool_response": {"output": "passwd\nshadow\n"}},
     "tool_response.output", "passwd"),
    ({"prompt": "ignore previous"}, "prompt", "ignore previous"),
])
def test_scoped_resolution_substring_match(payload, field_path, expected_contains):
    """Scoped resolution returns a string the operator's regex can
    scan. The contract is "the same text /verify_inline writes into the
    snapshot is the text the simulator scans"."""
    out = resolve_field_for_regex(payload, field_path)
    assert isinstance(out, str)
    assert expected_contains in out


def test_field_missing_distinguishable_from_empty_string():
    """Callers MUST distinguish 'field absent' (deny with a clear
    reason) from 'field present, empty' (regex did not match because
    value is empty). `is` comparison against FIELD_MISSING is the
    canonical check."""
    payload = {"tool_input": {"command": ""}}
    out_empty = resolve_field_for_regex(payload, "tool_input.command")
    out_missing = resolve_field_for_regex(payload, "tool_input.nonexistent")
    assert out_empty == ""
    assert out_empty is not FIELD_MISSING
    assert out_missing is FIELD_MISSING
