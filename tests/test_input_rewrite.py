"""D57f-2: InputRewritePolicy + rewriters DSL.

Covers:
  - IR validation (event pin, matcher class refusal, rewriter spec linter)
  - matrix legality
  - apply_rewriter behavior + total fail-soft contract
  - compiler emits a PreToolUse hook command of the right shape
  - cloud /policies/input_rewrite returns the rewritten dict
  - gate input_rewrite_cli emits the canonical hookSpecificOutput JSON
"""
from __future__ import annotations

import io
import json
import os
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from magi_cp.policy import (
    InputRewritePolicy, Trigger, apply_rewriter,
    compile_to_managed_settings, policy_from_dict, policy_to_dict,
    validate_rewriter_spec,
)
from magi_cp.policy.matrix import LEGAL_COMBINATIONS, MatcherClass


# ── matrix coverage ────────────────────────────────────────────────────
def test_matrix_registers_input_rewrite_on_pretooluse_per_tool():
    """input_rewrite is legal on (PreToolUse, tool/mcp_tool/tool_alt).
    Wildcard is intentionally absent."""
    for kls in (MatcherClass.tool, MatcherClass.mcp_tool, MatcherClass.tool_alt):
        assert ("PreToolUse", kls, "input_rewrite") in LEGAL_COMBINATIONS
    assert ("PreToolUse", MatcherClass.wildcard, "input_rewrite") not in LEGAL_COMBINATIONS
    # input_rewrite NEVER appears on any other event.
    other_events = {ev for ev, _, act in LEGAL_COMBINATIONS if act == "input_rewrite"}
    assert other_events == {"PreToolUse"}


# ── IR validation ──────────────────────────────────────────────────────
def _good_spec():
    return {
        "kind": "prefix_strip",
        "config": {"field": "command", "prefix": "sudo "},
    }


def test_input_rewrite_constructs_with_valid_spec():
    p = InputRewritePolicy(
        id="strip-sudo/v1",
        description="strip sudo from bash",
        trigger=Trigger(event="PreToolUse", matcher="Bash"),
        rewriter=_good_spec(),
    )
    assert p.type == "input_rewrite"
    assert p.rewriter["config"]["field"] == "command"


def test_input_rewrite_rejects_non_pretooluse_event():
    with pytest.raises(ValueError, match="must be PreToolUse"):
        InputRewritePolicy(
            id="bad-event/v1",
            description="",
            trigger=Trigger(event="PostToolUse", matcher="Bash"),
            rewriter=_good_spec(),
        )


def test_input_rewrite_rejects_wildcard_matcher():
    with pytest.raises(ValueError, match="matcher='\\*'"):
        InputRewritePolicy(
            id="bad-wc/v1",
            description="",
            trigger=Trigger(event="PreToolUse", matcher="*"),
            rewriter=_good_spec(),
        )


def test_input_rewrite_accepts_mcp_and_alt_matchers():
    p1 = InputRewritePolicy(
        id="alt/v1", description="",
        trigger=Trigger(event="PreToolUse", matcher="Bash|Edit"),
        rewriter=_good_spec(),
    )
    assert p1.trigger.matcher == "Bash|Edit"
    p2 = InputRewritePolicy(
        id="mcp/v1", description="",
        trigger=Trigger(event="PreToolUse", matcher="mcp__court__file"),
        rewriter=_good_spec(),
    )
    assert p2.trigger.matcher == "mcp__court__file"


def test_input_rewrite_rejects_unknown_rewriter_kind():
    with pytest.raises(ValueError, match="rewriter kind"):
        InputRewritePolicy(
            id="bad-kind/v1", description="",
            trigger=Trigger(event="PreToolUse", matcher="Bash"),
            rewriter={"kind": "code_eval", "config": {"field": "command"}},
        )


def test_input_rewrite_rejects_bad_field_name():
    with pytest.raises(ValueError, match="config.field"):
        InputRewritePolicy(
            id="bad-field/v1", description="",
            trigger=Trigger(event="PreToolUse", matcher="Bash"),
            rewriter={"kind": "prefix_strip",
                       "config": {"field": "../etc/passwd", "prefix": "x"}},
        )


def test_input_rewrite_rejects_regex_that_does_not_compile():
    with pytest.raises(ValueError, match="pattern does not compile"):
        InputRewritePolicy(
            id="bad-rx/v1", description="",
            trigger=Trigger(event="PreToolUse", matcher="Bash"),
            rewriter={"kind": "regex_substitute",
                       "config": {"field": "command", "pattern": "[unclosed",
                                  "replacement": ""}},
        )


# ── policy_from_dict / policy_to_dict round-trip ───────────────────────
def test_input_rewrite_round_trips_through_dict():
    src = {
        "type": "input_rewrite",
        "id": "rt/v1",
        "description": "round trip",
        "version": "0.1",
        "trigger": {"host": "claude-code", "event": "PreToolUse",
                     "matcher": "Bash"},
        "rewriter": _good_spec(),
    }
    p = policy_from_dict(src)
    assert isinstance(p, InputRewritePolicy)
    out = policy_to_dict(p)
    assert out["type"] == "input_rewrite"
    assert out["rewriter"] == src["rewriter"]
    assert out["trigger"] == src["trigger"]


# ── rewriters DSL ──────────────────────────────────────────────────────
def test_prefix_strip_single():
    spec = {"kind": "prefix_strip",
            "config": {"field": "command", "prefix": "sudo "}}
    out = apply_rewriter(spec, {"command": "sudo apt update"})
    assert out == {"command": "apt update"}


def test_prefix_strip_repeat():
    spec = {"kind": "prefix_strip",
            "config": {"field": "command", "prefix": "sudo ",
                       "strip_repeat": True}}
    out = apply_rewriter(spec, {"command": "sudo sudo ls"})
    assert out == {"command": "ls"}


def test_prefix_strip_no_match_returns_original_dict_unchanged():
    original = {"command": "ls -la"}
    spec = {"kind": "prefix_strip",
            "config": {"field": "command", "prefix": "sudo "}}
    out = apply_rewriter(spec, original)
    # Same object identity when no-op (cheap "did anything change?" check
    # callers can use).
    assert out is original


def test_scheme_force_http_to_https():
    spec = {"kind": "scheme_force",
            "config": {"field": "url", "from": "http://", "to": "https://"}}
    out = apply_rewriter(spec, {"url": "http://example.com/path"})
    assert out == {"url": "https://example.com/path"}


def test_scheme_force_no_match_is_noop():
    spec = {"kind": "scheme_force",
            "config": {"field": "url", "from": "http://", "to": "https://"}}
    original = {"url": "ftp://example.com"}
    out = apply_rewriter(spec, original)
    assert out is original


def test_regex_substitute_with_backref():
    spec = {"kind": "regex_substitute",
            "config": {"field": "command",
                        "pattern": r"echo\s+(\w+)",
                        "replacement": r"printf \1\n",
                        "count": 0}}
    out = apply_rewriter(spec, {"command": "echo hello"})
    assert out == {"command": "printf hello\n"}


def test_apply_rewriter_returns_original_on_missing_field():
    original = {"unrelated": "x"}
    out = apply_rewriter(
        {"kind": "prefix_strip",
         "config": {"field": "command", "prefix": "x"}},
        original,
    )
    assert out is original


def test_apply_rewriter_is_total_on_bad_spec():
    """A bad spec must NEVER raise — degrade to no-op."""
    original = {"command": "sudo ls"}
    out = apply_rewriter({"kind": "code_eval", "config": {}}, original)
    assert out is original


def test_apply_rewriter_returns_new_dict_when_changed():
    original = {"command": "sudo ls", "_other": 1}
    out = apply_rewriter(
        {"kind": "prefix_strip",
         "config": {"field": "command", "prefix": "sudo "}},
        original,
    )
    assert out is not original
    assert out == {"command": "ls", "_other": 1}
    # Unrelated fields preserved.
    assert original["_other"] == 1


def test_validate_rewriter_spec_caps_pattern_length():
    with pytest.raises(ValueError, match="pattern too long"):
        validate_rewriter_spec({
            "kind": "regex_substitute",
            "config": {"field": "command",
                        "pattern": "a" * 5000, "replacement": ""},
        })


# ── compiler ──────────────────────────────────────────────────────────
def test_compiler_emits_input_rewrite_hook():
    p = InputRewritePolicy(
        id="rewrite-aa/v1", description="",
        trigger=Trigger(event="PreToolUse", matcher="Bash"),
        rewriter=_good_spec(),
    )
    ms = compile_to_managed_settings([p])
    hooks = ms["hooks"]["PreToolUse"]
    assert len(hooks) == 1
    entry = hooks[0]
    assert entry["matcher"] == "Bash"
    cmd = entry["hooks"][0]
    assert cmd["type"] == "command"
    # The rewriter config does NOT leak into the command line — only the
    # policy id rides through (the cloud is the source of truth).
    assert "magi-cp-input-rewrite" in cmd["command"]
    assert "--policy rewrite-aa/v1" in cmd["command"]
    # The rewriter literal `prefix` value MUST NOT leak into the hook
    # command line — the cloud applies the rewriter spec server-side.
    assert "sudo " not in cmd["command"]
    assert "\"prefix\"" not in cmd["command"]
    assert "prefix_strip" not in cmd["command"]


# ── gate input_rewrite_cli ─────────────────────────────────────────────
def test_input_rewrite_cli_emits_updated_input(monkeypatch, tmp_path):
    """Happy path: stdin carries a PreToolUse payload, cloud returns a
    rewritten dict, shim prints the canonical JSON."""
    from magi_cp.local import gate

    # Stub the cloud HTTP call.
    class _FakeResp:
        def __init__(self, body): self._b = json.dumps(body).encode("utf-8")
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    captured = {}

    def fake_urlopen(req, timeout=5):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp({
            "rewrote": True,
            "updated_input": {"command": "apt update"},
        })

    monkeypatch.setenv("MAGI_CP_CLOUD_URL", "http://127.0.0.1:8787")
    monkeypatch.setattr(gate.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sys, "argv", ["magi-cp-input-rewrite", "--policy",
                                       "strip-sudo/v1"])
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "sudo apt update"},
    }
    monkeypatch.setattr(sys, "stdin",
                         io.StringIO(json.dumps(payload)))
    buf = io.StringIO()
    with redirect_stdout(buf), pytest.raises(SystemExit) as exc:
        gate.input_rewrite_cli()
    assert exc.value.code == 0
    out = json.loads(buf.getvalue())
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert out["hookSpecificOutput"]["updatedInput"] == {"command": "apt update"}
    assert captured["body"]["policy_id"] == "strip-sudo/v1"
    assert captured["body"]["tool_name"] == "Bash"
    assert captured["body"]["tool_input"]["command"] == "sudo apt update"


def test_input_rewrite_cli_silent_on_noop_reply(monkeypatch):
    """Cloud says `rewrote=false` → shim exits 0 with EMPTY stdout."""
    from magi_cp.local import gate

    class _FakeResp:
        def __init__(self, body): self._b = json.dumps(body).encode("utf-8")
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setenv("MAGI_CP_CLOUD_URL", "http://127.0.0.1:8787")
    monkeypatch.setattr(
        gate.urllib.request, "urlopen",
        lambda req, timeout=5: _FakeResp({"rewrote": False}),
    )
    monkeypatch.setattr(sys, "argv", ["magi-cp-input-rewrite", "--policy", "p/v1"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "tool_name": "Bash", "tool_input": {"command": "ls"},
    })))
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = gate.input_rewrite_cli()
    assert rc == 0
    assert buf.getvalue() == ""


def test_input_rewrite_cli_refuses_bad_policy_id(monkeypatch):
    from magi_cp.local import gate
    monkeypatch.setattr(sys, "argv",
                         ["magi-cp-input-rewrite", "--policy", "../etc/passwd"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = gate.input_rewrite_cli()
    assert rc == 0
    assert buf.getvalue() == ""


def test_input_rewrite_cli_handles_malformed_stdin(monkeypatch):
    from magi_cp.local import gate
    monkeypatch.setattr(sys, "argv", ["magi-cp-input-rewrite", "--policy", "p/v1"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("not-json"))
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = gate.input_rewrite_cli()
    assert rc == 0
    assert buf.getvalue() == ""
