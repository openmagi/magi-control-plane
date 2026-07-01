"""P1 Codex adapter: CC path byte-equivalence under the dispatcher.

The refactor extracts stdin parsing / verdict emission into
``runtime/cc.py`` and turns ``gate.main`` into a runtime dispatcher. With
``MAGI_CP_CODEX_RUNTIME_ENABLED`` unset (default), the CC path MUST be
byte-identical to the pre-adapter contract:

  - blank stdin            -> silent allow (empty stdout), exit 0
  - non-sentinel command   -> silent allow (empty stdout), exit 0
  - malformed JSON         -> deny "malformed hook payload (json)"
  - sentinel, no WAL token -> deny "no signed citation_verify=pass ..."

We assert the dispatcher (``gate.main`` routing through ``cc.py``) equals
BOTH a golden built from ``policy.cc_shapes`` AND a direct
``gate.evaluate`` call on the same payload.

Design brief: docs/plans/2026-06-30-codex-runtime-adapter-design.md
Section 3.3 + Section 10 P1.
"""
from __future__ import annotations

import io
import json
import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from magi_cp.local import gate
from magi_cp.policy.cc_shapes import emit_deny_payload


@pytest.fixture
def tmp_local(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_CP_LOCAL_DIR", str(tmp_path))
    # Ensure the Codex flag is off so the dispatcher stays on the CC path.
    monkeypatch.delenv("MAGI_CP_CODEX_RUNTIME_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_CP_RUNTIME", raising=False)
    return tmp_path


@pytest.fixture
def cached_pubkey(tmp_local):
    priv = Ed25519PrivateKey.generate()
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    p = tmp_local / "pubkey.pem"
    p.write_text(pub_pem)
    os.chmod(p, 0o600)


def _run_main(monkeypatch, stdin_text: str, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    with pytest.raises(SystemExit) as exc:
        gate.main()
    captured = capsys.readouterr()
    return captured.out, exc.value.code


def _run_evaluate(payload: dict, capsys):
    with pytest.raises(SystemExit) as exc:
        gate.evaluate(payload)
    captured = capsys.readouterr()
    return captured.out, exc.value.code


# ── allow paths (silent, empty stdout) ───────────────────────────────
def test_blank_stdin_allows_silently(tmp_local, monkeypatch, capsys):
    out, code = _run_main(monkeypatch, "   \n", capsys)
    assert out == ""
    assert code == 0


def test_non_sentinel_allows_silently(tmp_local, monkeypatch, capsys):
    payload = {"hook_event_name": "PreToolUse",
               "tool_input": {"command": "ls -la"}}
    out, code = _run_main(monkeypatch, json.dumps(payload), capsys)
    assert out == ""
    assert code == 0
    # Dispatcher output equals a direct evaluate() call, byte-for-byte.
    out2, code2 = _run_evaluate(payload, capsys)
    assert (out, code) == (out2, code2)


# ── malformed JSON -> deny ───────────────────────────────────────────
def test_malformed_json_denies(tmp_local, monkeypatch, capsys):
    out, code = _run_main(monkeypatch, "{not json", capsys)
    assert code == 0
    golden = json.dumps(
        emit_deny_payload("malformed hook payload (json)",
                          hook_event_name="PreToolUse"),
        ensure_ascii=False,
    ) + "\n"
    assert out == golden


# ── sentinel with empty WAL -> deny (byte-equal to evaluate) ─────────
def test_sentinel_no_token_denies_byte_equal(tmp_local, cached_pubkey,
                                              monkeypatch, capsys):
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_input": {"command": "echo FILE_COURT_subj_hash"},
    }
    out, code = _run_main(monkeypatch, json.dumps(payload), capsys)
    assert code == 0
    golden = json.dumps(
        emit_deny_payload(
            "no signed citation_verify=pass for subject=subj "
            "payload_hash=hash",
            hook_event_name="PreToolUse",
        ),
        ensure_ascii=False,
    ) + "\n"
    assert out == golden

    # And byte-identical to a direct evaluate() call.
    out2, code2 = _run_evaluate(payload, capsys)
    assert (out, code) == (out2, code2)


# ── PostToolUse deny uses the retry-feedback channel ─────────────────
def test_posttooluse_deny_channel_byte_equal(tmp_local, cached_pubkey,
                                             monkeypatch, capsys):
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_input": {"command": "echo FILE_COURT_a_b"},
    }
    out, code = _run_main(monkeypatch, json.dumps(payload), capsys)
    golden = json.dumps(
        emit_deny_payload(
            "no signed citation_verify=pass for subject=a payload_hash=b",
            hook_event_name="PostToolUse",
        ),
        ensure_ascii=False,
    ) + "\n"
    assert out == golden
    assert code == 0
