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
    # P1 follow-up: `permissionDecision: "allow"` must accompany
    # `updatedInput` so the JSON is unambiguous across CC builds.
    # Some builds parse `updatedInput` only when a permission stance
    # is present; others would ignore the field entirely without it.
    # Pairing with `allow` makes the rewrite-and-approve intent
    # explicit; downstream EvidencePolicy hooks still deny via their
    # own entries (deny wins over allow on PreToolUse).
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
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


# ── ReDoS hardening (P1 follow-up) ──────────────────────────────────
def test_validate_rewriter_rejects_nested_quantifier_pattern():
    """ReDoS heuristic: `(a+)+` style patterns must fail at PUT time
    rather than land in prod and pin the event loop on a crafted
    payload. The runtime input-length cap is the actual ceiling; the
    lint surfaces the most obvious case loudly to the operator."""
    with pytest.raises(ValueError, match="nested quantifiers"):
        validate_rewriter_spec({
            "kind": "regex_substitute",
            "config": {
                "field": "command",
                "pattern": r"(a+)+$",
                "replacement": "",
            },
        })


def test_validate_rewriter_rejects_overlapping_quantifier_pattern():
    """Variants of the canonical (a|a)*b / (a*)*b shapes also lint
    out — they trip the same nested-quantifier inner-then-outer
    structure even though the inner construct uses `*` instead of
    `+`."""
    with pytest.raises(ValueError, match="nested quantifiers"):
        validate_rewriter_spec({
            "kind": "regex_substitute",
            "config": {
                "field": "command",
                "pattern": r"(a*)+b",
                "replacement": "",
            },
        })


def test_validate_rewriter_accepts_safe_pattern_with_inner_quantifier_only():
    """The lint must NOT refuse a legitimate pattern that uses
    quantifiers INSIDE a group without an outer quantifier on the
    group. `echo\\s+(\\w+)` is the existing happy-path fixture."""
    validate_rewriter_spec({
        "kind": "regex_substitute",
        "config": {
            "field": "command",
            "pattern": r"echo\s+(\w+)",
            "replacement": r"printf \1",
        },
    })


def test_apply_rewriter_caps_oversize_input_for_regex_substitute():
    """A 64KB+ value in the targeted field must not reach `re.sub` —
    the rewriter degrades to no-op (returns the original tool_input
    unchanged). The cap closes the ReDoS amplification surface
    described in the P1 finding."""
    huge = "x" * (64 * 1024 + 1)
    original = {"command": huge}
    spec = {
        "kind": "regex_substitute",
        "config": {
            "field": "command",
            "pattern": r"^",
            "replacement": "PFX",
        },
    }
    out = apply_rewriter(spec, original)
    # No-op path: same object identity is the cheap "did anything
    # change?" signal callers already rely on.
    assert out is original


def test_apply_rewriter_runs_on_input_at_cap():
    """Boundary check: a value EXACTLY at the cap is allowed
    (the cap is inclusive on the OK side)."""
    at_cap = "y" * (64 * 1024)
    original = {"command": at_cap}
    spec = {
        "kind": "regex_substitute",
        "config": {
            "field": "command",
            "pattern": r"^",
            "replacement": "PFX",
        },
    }
    out = apply_rewriter(spec, original)
    assert out["command"].startswith("PFX")
    assert len(out["command"]) == 64 * 1024 + len("PFX")


# ── permissionDecision: "allow" pairing (P1 follow-up) ───────────────
def test_emit_updated_input_pairs_allow_with_updated_input():
    """The shim's emission must include `permissionDecision: "allow"`
    so the JSON is unambiguous to any CC build that wants both fields
    present. Without this, some CC builds parse the `updatedInput`
    but leave the permission flow to a downstream hook, others
    ignore the field entirely."""
    from magi_cp.local import gate
    buf = io.StringIO()
    with redirect_stdout(buf), pytest.raises(SystemExit):
        gate._emit_updated_input({"command": "apt update"})
    out = json.loads(buf.getvalue())
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "allow"
    assert hso["updatedInput"] == {"command": "apt update"}


# ── stderr signal on missing tool_name (P2 follow-up) ────────────────
def test_input_rewrite_cli_signals_missing_tool_name_to_stderr(monkeypatch, capsys):
    """A payload without `tool_name` historically exited silently — the
    operator saw a green wizard PUT and never knew the rewrite never
    fired. The shim now writes a one-line message to stderr so the
    payload-shape mismatch surfaces in CLI logs."""
    from magi_cp.local import gate
    monkeypatch.setattr(sys, "argv", ["magi-cp-input-rewrite", "--policy", "p/v1"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        # No `tool_name` — the canonical CC PreToolUse field is missing.
        "tool_input": {"command": "ls"},
    })))
    rc = gate.input_rewrite_cli()
    captured = capsys.readouterr()
    assert rc == 0
    # stdout still empty (fail-soft).
    assert captured.out == ""
    # stderr carries the diagnostic.
    assert "tool_name" in captured.err
    assert "rewrite skipped" in captured.err


# ── shim forwards X-Api-Key from MAGI_CP_API_KEY (P1 follow-up) ──────
def test_input_rewrite_cli_forwards_api_key_header(monkeypatch):
    """When `MAGI_CP_API_KEY` is set on the gate environment, the shim
    must forward it as `X-Api-Key`. Pairs with the cloud-side optional
    auth (test_input_rewrite_endpoint_*)."""
    from magi_cp.local import gate

    class _FakeResp:
        def __init__(self, body): self._b = json.dumps(body).encode("utf-8")
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    captured = {}

    def fake_urlopen(req, timeout=5):
        captured["headers"] = dict(req.header_items())
        return _FakeResp({"rewrote": False})

    monkeypatch.setenv("MAGI_CP_CLOUD_URL", "http://127.0.0.1:8787")
    monkeypatch.setenv("MAGI_CP_API_KEY", "sekret-123")
    monkeypatch.setattr(gate.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sys, "argv", ["magi-cp-input-rewrite", "--policy", "p/v1"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "tool_name": "Bash", "tool_input": {"command": "ls"},
    })))
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = gate.input_rewrite_cli()
    assert rc == 0
    # urllib normalizes header names to Capitalize-Case.
    header_keys = {k.lower(): v for k, v in captured["headers"].items()}
    assert header_keys.get("x-api-key") == "sekret-123"


def test_input_rewrite_cli_omits_api_key_header_when_env_unset(monkeypatch):
    """Symmetric: no env → no `X-Api-Key` header. Keeps the loopback
    dev loop working when the operator has not enrolled the gate."""
    from magi_cp.local import gate

    class _FakeResp:
        def __init__(self, body): self._b = json.dumps(body).encode("utf-8")
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    captured = {}

    def fake_urlopen(req, timeout=5):
        captured["headers"] = dict(req.header_items())
        return _FakeResp({"rewrote": False})

    monkeypatch.delenv("MAGI_CP_API_KEY", raising=False)
    monkeypatch.setenv("MAGI_CP_CLOUD_URL", "http://127.0.0.1:8787")
    monkeypatch.setattr(gate.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sys, "argv", ["magi-cp-input-rewrite", "--policy", "p/v1"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "tool_name": "Bash", "tool_input": {"command": "ls"},
    })))
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = gate.input_rewrite_cli()
    assert rc == 0
    header_keys = {k.lower() for k in captured["headers"]}
    assert "x-api-key" not in header_keys


# ── matcher_covers helper (P2 follow-up) ─────────────────────────────
def test_matcher_covers_uses_matrix_classifier():
    """The runtime matcher comparison goes through the single
    matrix.matcher_covers predicate so any future matcher class lands
    in one place. We pin the v1 semantics on each matcher class."""
    from magi_cp.policy.matrix import matcher_covers
    # tool / mcp_tool — exact equality.
    assert matcher_covers("Bash", "Bash") is True
    assert matcher_covers("Bash", "Edit") is False
    assert matcher_covers("mcp__court__file", "mcp__court__file") is True
    assert matcher_covers("mcp__court__file", "mcp__court__list") is False
    # tool_alt — pipe alternation, any-of.
    assert matcher_covers("Bash|Edit", "Bash") is True
    assert matcher_covers("Bash|Edit", "Edit") is True
    assert matcher_covers("Bash|Edit", "Read") is False
    # wildcard — covers anything (but input_rewrite refuses wildcard
    # at the route layer, defense in depth).
    assert matcher_covers("*", "Bash") is True
    # Unknown matcher shape → False (fail-soft to no-op rather than
    # crashing the request handler).
    assert matcher_covers("not_a_known_shape", "Bash") is False
