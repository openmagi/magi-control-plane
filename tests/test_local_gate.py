"""P4 local gate — PreToolUse hook helper.

Verifies the gate's deny/allow logic on synthetic CC hook payloads + WAL state.
The cloud is mocked at the urllib level.
"""
import json
import os
import sys
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from magi_cp.evidence import sign_token, Wal


@pytest.fixture
def tmp_local(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_CP_LOCAL_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def keypair():
    priv = Ed25519PrivateKey.generate()
    return priv, priv.public_key()


@pytest.fixture
def cached_pubkey(tmp_local, keypair):
    from cryptography.hazmat.primitives import serialization
    pub_pem = keypair[1].public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    p = tmp_local / "pubkey.pem"
    p.write_text(pub_pem)
    # Match the production gate's 0600-or-reject policy (gate.py _load_pubkey_for_kid).
    os.chmod(p, 0o600)


def _payload(cmd: str) -> dict:
    return {"hook_event_name": "PreToolUse", "tool_input": {"command": cmd}}


def _run_evaluate_capture(payload: dict, capsys):
    """Invoke evaluate(), expecting SystemExit(0). Return (stdout, exit_code)."""
    from magi_cp.local.gate import evaluate
    with pytest.raises(SystemExit) as exc:
        evaluate(payload)
    captured = capsys.readouterr()
    return captured.out, exc.value.code


# ── non-sentinel commands pass through ──────────────────────────────
def test_non_sentinel_allows(tmp_local, cached_pubkey, capsys):
    out, code = _run_evaluate_capture(_payload("ls -la"), capsys)
    assert code == 0
    assert out == ""


# ── sentinel without WAL token → deny ───────────────────────────────
def test_sentinel_no_token_denies(tmp_local, cached_pubkey, capsys):
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M1_DOC1 motion"),
                                       capsys)
    assert code == 0
    body = json.loads(out)
    assert body["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "no signed" in body["hookSpecificOutput"]["permissionDecisionReason"]


# ── valid token matching matter+doc → allow (silent) ────────────────
def test_sentinel_with_valid_token_allows(tmp_local, keypair, cached_pubkey, capsys):
    priv, _ = keypair
    now = int(time.time())
    body = {"step": "citation_verify", "matter": "M1", "doc_hash": "DOC1",
            "verdict": "pass", "iat": now, "exp": now + 600, "kid": "k"}
    token = sign_token(body, priv)
    Wal(path=str(tmp_local / "wal.jsonl")).append(
        {"step": "citation_verify", "token": token})
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M1_DOC1 motion"),
                                       capsys)
    assert code == 0
    assert out == ""


# ── doc swap: token for DOC1 doesn't help DOC2 ──────────────────────
def test_doc_swap_denied(tmp_local, keypair, cached_pubkey, capsys):
    priv, _ = keypair
    now = int(time.time())
    body = {"step": "citation_verify", "matter": "M1", "doc_hash": "DOC1",
            "verdict": "pass", "iat": now, "exp": now + 600, "kid": "k"}
    Wal(path=str(tmp_local / "wal.jsonl")).append(
        {"step": "citation_verify", "token": sign_token(body, priv)})
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M1_DOC2 other"),
                                       capsys)
    assert code == 0
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


# ── expired token → deny ─────────────────────────────────────────────
def test_expired_token_denied(tmp_local, keypair, cached_pubkey, capsys):
    priv, _ = keypair
    body = {"step": "citation_verify", "matter": "M1", "doc_hash": "D",
            "verdict": "pass", "iat": 0, "exp": 1, "kid": "k"}
    Wal(path=str(tmp_local / "wal.jsonl")).append(
        {"step": "citation_verify", "token": sign_token(body, priv)})
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M1_D x"), capsys)
    assert code == 0
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


# ── wrong-key token → deny (forgery attempt) ─────────────────────────
def test_wrong_key_token_denied(tmp_local, cached_pubkey, capsys):
    """A token signed by a different keypair must NOT verify."""
    other_priv = Ed25519PrivateKey.generate()
    now = int(time.time())
    body = {"step": "citation_verify", "matter": "M1", "doc_hash": "D",
            "verdict": "pass", "iat": now, "exp": now + 600, "kid": "k"}
    Wal(path=str(tmp_local / "wal.jsonl")).append(
        {"step": "citation_verify", "token": sign_token(body, other_priv)})
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M1_D x"), capsys)
    assert code == 0
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


# ── cloud unreachable → fail-closed deny ────────────────────────────
def test_cloud_unreachable_fails_closed(tmp_local, monkeypatch, capsys):
    """No cached pubkey + cloud down → must fail-closed deny (license = bundle = closed)."""
    monkeypatch.setenv("MAGI_CP_CLOUD_URL", "http://127.0.0.1:1")  # closed port
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M1_D x"), capsys)
    assert code == 0
    body = json.loads(out)
    assert body["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "cloud unreachable" in body["hookSpecificOutput"]["permissionDecisionReason"]


# ── P4 review regressions ───────────────────────────────────────────
def test_multi_sentinel_all_must_validate(tmp_local, keypair, cached_pubkey, capsys):
    """Two sentinels in one command — both must individually verify."""
    priv, _ = keypair
    now = int(time.time())
    Wal(path=str(tmp_local / "wal.jsonl")).append({
        "step": "citation_verify",
        "token": sign_token({"step": "citation_verify", "matter": "M1", "doc_hash": "A",
                              "verdict": "pass", "iat": now, "exp": now + 600, "kid": "k"},
                             priv),
    })
    # only M1/A token exists — M2/B sentinel should fail
    out, code = _run_evaluate_capture(
        _payload("FILE_COURT_M1_A ; FILE_COURT_M2_B"), capsys)
    assert code == 0
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_later_fail_invalidates_earlier_pass(tmp_local, keypair, cached_pubkey, capsys):
    """A later citation_verify=review/deny for the same (matter,doc_id) MUST kill an
    earlier =pass. Latest-iat wins."""
    priv, _ = keypair
    now = int(time.time())
    wal = Wal(path=str(tmp_local / "wal.jsonl"))
    wal.append({"step": "citation_verify", "token": sign_token(
        {"step": "citation_verify", "matter": "M1", "doc_hash": "D",
         "verdict": "pass", "iat": now - 100, "exp": now + 600, "kid": "k"}, priv)})
    wal.append({"step": "citation_verify", "token": sign_token(
        {"step": "citation_verify", "matter": "M1", "doc_hash": "D",
         "verdict": "review", "iat": now, "exp": now + 600, "kid": "k"}, priv)})
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M1_D x"), capsys)
    assert code == 0
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_sentinel_with_trailing_suffix_does_not_match(tmp_local, cached_pubkey, capsys):
    """Anchored regex: FILE_COURT_M_D_v2 must NOT silently parse as M/D."""
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M_D_v2 motion"),
                                       capsys)
    # No sentinel matches → pass-through ALLOW
    assert code == 0
    assert out == ""


def test_pubkey_with_loose_mode_is_rejected_and_refetched(tmp_local, monkeypatch, keypair, capsys):
    """Pubkey cache with 0644 (world-readable) must be refused → re-fetch.
    With cloud unreachable, this yields fail-closed deny."""
    from cryptography.hazmat.primitives import serialization
    pem = keypair[1].public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    p = tmp_local / "pubkey.pem"
    p.write_text(pem)
    os.chmod(p, 0o644)   # world-readable: trust anchor poisoning attempt
    monkeypatch.setenv("MAGI_CP_CLOUD_URL", "http://127.0.0.1:1")
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M_D x"), capsys)
    assert code == 0
    assert "cloud unreachable" in json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]


def test_kid_drift_across_wal_entries_rejects_later(tmp_local, keypair, cached_pubkey, capsys):
    """Two tokens for same (matter,doc) with different kid: later kid is dropped."""
    priv, _ = keypair
    now = int(time.time())
    wal = Wal(path=str(tmp_local / "wal.jsonl"))
    wal.append({"step": "citation_verify", "token": sign_token(
        {"step": "citation_verify", "matter": "M1", "doc_hash": "D",
         "verdict": "pass", "iat": now - 100, "exp": now + 600, "kid": "old"}, priv)})
    wal.append({"step": "citation_verify", "token": sign_token(
        {"step": "citation_verify", "matter": "M1", "doc_hash": "D",
         "verdict": "pass", "iat": now, "exp": now + 600, "kid": "new"}, priv)})
    # The later entry has a different kid; latest-iat selection should NOT
    # adopt it because expected_kid was pinned by the earlier valid entry.
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M1_D x"), capsys)
    assert code == 0
    assert out == ""   # the earlier (pinned-kid) pass still ALLOWs


def test_invalid_cloud_url_scheme_denied(tmp_local, monkeypatch, capsys):
    monkeypatch.setenv("MAGI_CP_CLOUD_URL", "file:///etc/passwd")
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M_D x"), capsys)
    assert code == 0
    assert "scheme" in json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]


# ── tamper: WAL token with mid-char flipped → signature breaks → deny
def test_tampered_token_in_wal_denied(tmp_local, keypair, cached_pubkey, capsys):
    priv, _ = keypair
    now = int(time.time())
    body = {"step": "citation_verify", "matter": "M1", "doc_hash": "D",
            "verdict": "pass", "iat": now, "exp": now + 600, "kid": "k"}
    token = sign_token(body, priv)
    mid = len(token) // 2
    bad = token[:mid] + ("X" if token[mid] != "X" else "Y") + token[mid+1:]
    Wal(path=str(tmp_local / "wal.jsonl")).append(
        {"step": "citation_verify", "token": bad})
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M1_D x"), capsys)
    assert code == 0
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"
