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


def test_kid_drift_across_wal_entries_pins_to_latest(tmp_local, keypair, cached_pubkey, capsys):
    """Two pass tokens for same (matter,doc) with different kid: the NEWEST
    token's kid pins. PR2 review fix (issue #1) — previously the OLDEST
    entry's kid pinned, which let a stale pass win over a rotated newer one
    once PR2's dual-shape lookup widened the candidate pool.

    Both tokens here verify under the same keypair (kid is just a label in
    the body), so the newest-pinned pass still ALLOWs — but the selection
    semantics now match the "latest decision wins" intent."""
    priv, _ = keypair
    now = int(time.time())
    wal = Wal(path=str(tmp_local / "wal.jsonl"))
    wal.append({"step": "citation_verify", "token": sign_token(
        {"step": "citation_verify", "matter": "M1", "doc_hash": "D",
         "verdict": "pass", "iat": now - 100, "exp": now + 600, "kid": "old"}, priv)})
    wal.append({"step": "citation_verify", "token": sign_token(
        {"step": "citation_verify", "matter": "M1", "doc_hash": "D",
         "verdict": "pass", "iat": now, "exp": now + 600, "kid": "new"}, priv)})
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M1_D x"), capsys)
    assert code == 0
    assert out == ""   # latest-pinned pass ALLOWs


def test_pr2_upgrade_boundary_new_token_honored_over_legacy(tmp_local, keypair,
                                                              cached_pubkey, capsys):
    """Mixed-shape WAL: an OLDER legacy-shape (matter/doc_hash) pass token
    signed by `K_OLD`, and a NEWER PR2-shape (subject/payload_hash) pass
    token signed by `K_NEW` for the same logical key. Pre-PR2 the gate
    pinned `expected_kid = K_OLD` from the first match and dropped the
    newer entry — silently letting a stale pass win across the upgrade
    boundary. Post-PR2 the newest entry pins; the gate still ALLOWs (both
    are pass), but selection now matches the "latest decision wins" intent.
    """
    priv, _ = keypair
    now = int(time.time())
    wal = Wal(path=str(tmp_local / "wal.jsonl"))
    # Legacy-shape pass, older iat, kid=K_OLD
    wal.append({"step": "citation_verify", "token": sign_token(
        {"step": "citation_verify",
         "matter": "M1", "doc_hash": "D",
         "verdict": "pass", "iat": now - 100, "exp": now + 600, "kid": "K_OLD"},
        priv)})
    # PR2-shape pass, newer iat, kid=K_NEW (rotated)
    wal.append({"step": "citation_verify", "token": sign_token(
        {"step": "citation_verify",
         "subject": "M1", "payload_hash": "D",
         "verdict": "pass", "iat": now, "exp": now + 600, "kid": "K_NEW"},
        priv)})
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M1_D x"), capsys)
    assert code == 0
    assert out == ""   # newest (K_NEW, PR2-shape) pin → pass → ALLOW


def test_pr2_upgrade_boundary_newer_review_kills_older_legacy_pass(
        tmp_local, keypair, cached_pubkey, capsys):
    """The reverse direction: an older legacy-shape PASS must be
    invalidated by a newer PR2-shape REVIEW for the same logical key. This
    is the load-bearing safety the kid-pin re-ordering preserves — a stale
    cached pass cannot authorise a re-edited document."""
    priv, _ = keypair
    now = int(time.time())
    wal = Wal(path=str(tmp_local / "wal.jsonl"))
    wal.append({"step": "citation_verify", "token": sign_token(
        {"step": "citation_verify",
         "matter": "M1", "doc_hash": "D",
         "verdict": "pass", "iat": now - 100, "exp": now + 600, "kid": "K_OLD"},
        priv)})
    wal.append({"step": "citation_verify", "token": sign_token(
        {"step": "citation_verify",
         "subject": "M1", "payload_hash": "D",
         "verdict": "review", "iat": now, "exp": now + 600, "kid": "K_NEW"},
        priv)})
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M1_D x"), capsys)
    assert code == 0
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


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


# ── PR2: subject/payload_hash dual-shape token back-compat ──────────
def test_legacy_token_with_matter_doc_hash_still_allows(tmp_local, keypair,
                                                         cached_pubkey, capsys):
    """A WAL token written by a pre-PR2 cloud carries only `matter` +
    `doc_hash`. The post-PR2 gate must still recognise it — otherwise
    customers crossing the upgrade boundary lose all cached verdicts."""
    priv, _ = keypair
    now = int(time.time())
    body = {"step": "citation_verify",
            "matter": "M1", "doc_hash": "D1",
            "verdict": "pass", "iat": now, "exp": now + 600, "kid": "k"}
    Wal(path=str(tmp_local / "wal.jsonl")).append(
        {"step": "citation_verify", "token": sign_token(body, priv)})
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M1_D1 x"), capsys)
    assert code == 0
    assert out == ""   # silent allow


def test_new_token_with_subject_payload_hash_only_allows(tmp_local, keypair,
                                                           cached_pubkey, capsys):
    """A WAL token signed by a post-PR4 cloud (legacy fields dropped)
    carries only `subject` + `payload_hash`. Today the cloud still mirrors
    both, but this test asserts the gate's lookup is independent of the
    legacy fields' presence — guard against a future regression where
    someone forgets to handle the new-only shape."""
    priv, _ = keypair
    now = int(time.time())
    body = {"step": "citation_verify",
            # NO matter / doc_hash — emulate post-PR4 cloud
            "subject": "M2", "payload_hash": "D2",
            "verdict": "pass", "iat": now, "exp": now + 600, "kid": "k"}
    Wal(path=str(tmp_local / "wal.jsonl")).append(
        {"step": "citation_verify", "token": sign_token(body, priv)})
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M2_D2 x"), capsys)
    assert code == 0
    assert out == ""


def test_token_with_both_pairs_allows(tmp_local, keypair, cached_pubkey, capsys):
    """The PR2 cloud emits both pairs in the token body — gate accepts."""
    priv, _ = keypair
    now = int(time.time())
    body = {"step": "citation_verify",
            "matter": "M3", "doc_hash": "D3",
            "subject": "M3", "payload_hash": "D3",
            "verdict": "pass", "iat": now, "exp": now + 600, "kid": "k"}
    Wal(path=str(tmp_local / "wal.jsonl")).append(
        {"step": "citation_verify", "token": sign_token(body, priv)})
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M3_D3 x"), capsys)
    assert code == 0
    assert out == ""


def test_deny_message_uses_subject_payload_hash_vocabulary(tmp_local,
                                                            cached_pubkey, capsys):
    """When no token is found the deny reason should mention the new
    vocabulary (subject/payload_hash) so operators searching logs for the
    canonical names find them."""
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_MX_DX motion"),
                                       capsys)
    assert code == 0
    body = json.loads(out)
    reason = body["hookSpecificOutput"]["permissionDecisionReason"]
    assert "subject=" in reason
    assert "payload_hash=" in reason
