"""P4 local gate — PreToolUse hook helper.

Verifies the gate's deny/allow logic on synthetic CC hook payloads + WAL state.
The cloud is mocked at the urllib level.

PR4: the gate matches ONLY on canonical (subject, payload_hash) token-body
fields. Pre-PR2 tokens that carried only `matter`/`doc_hash` no longer
match — operators upgrading past PR4 must roll forward gate + cloud
together so the WAL flushes to the new shape.
"""
import json
import os
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


# ── valid token matching subject+payload_hash → allow (silent) ──────
def test_sentinel_with_valid_token_allows(tmp_local, keypair, cached_pubkey, capsys):
    priv, _ = keypair
    now = int(time.time())
    body = {"step": "citation_verify", "subject": "M1", "payload_hash": "DOC1",
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
    body = {"step": "citation_verify", "subject": "M1", "payload_hash": "DOC1",
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
    body = {"step": "citation_verify", "subject": "M1", "payload_hash": "D",
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
    body = {"step": "citation_verify", "subject": "M1", "payload_hash": "D",
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
        "token": sign_token({"step": "citation_verify",
                              "subject": "M1", "payload_hash": "A",
                              "verdict": "pass", "iat": now, "exp": now + 600, "kid": "k"},
                             priv),
    })
    # only M1/A token exists — M2/B sentinel should fail
    out, code = _run_evaluate_capture(
        _payload("FILE_COURT_M1_A ; FILE_COURT_M2_B"), capsys)
    assert code == 0
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_later_fail_invalidates_earlier_pass(tmp_local, keypair, cached_pubkey, capsys):
    """A later citation_verify=review/deny for the same (subject, payload_hash)
    MUST kill an earlier =pass. Latest-iat wins."""
    priv, _ = keypair
    now = int(time.time())
    wal = Wal(path=str(tmp_local / "wal.jsonl"))
    wal.append({"step": "citation_verify", "token": sign_token(
        {"step": "citation_verify", "subject": "M1", "payload_hash": "D",
         "verdict": "pass", "iat": now - 100, "exp": now + 600, "kid": "k"}, priv)})
    wal.append({"step": "citation_verify", "token": sign_token(
        {"step": "citation_verify", "subject": "M1", "payload_hash": "D",
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
    """Two pass tokens for same (subject, payload_hash) with different kid:
    the NEWEST token's kid pins."""
    priv, _ = keypair
    now = int(time.time())
    wal = Wal(path=str(tmp_local / "wal.jsonl"))
    wal.append({"step": "citation_verify", "token": sign_token(
        {"step": "citation_verify", "subject": "M1", "payload_hash": "D",
         "verdict": "pass", "iat": now - 100, "exp": now + 600, "kid": "old"}, priv)})
    wal.append({"step": "citation_verify", "token": sign_token(
        {"step": "citation_verify", "subject": "M1", "payload_hash": "D",
         "verdict": "pass", "iat": now, "exp": now + 600, "kid": "new"}, priv)})
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M1_D x"), capsys)
    assert code == 0
    assert out == ""   # latest-pinned pass ALLOWs


def test_invalid_cloud_url_scheme_denied(tmp_local, monkeypatch, capsys):
    monkeypatch.setenv("MAGI_CP_CLOUD_URL", "file:///etc/passwd")
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M_D x"), capsys)
    assert code == 0
    assert "scheme" in json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]


# ── tamper: WAL token with mid-char flipped → signature breaks → deny
def test_tampered_token_in_wal_denied(tmp_local, keypair, cached_pubkey, capsys):
    priv, _ = keypair
    now = int(time.time())
    body = {"step": "citation_verify", "subject": "M1", "payload_hash": "D",
            "verdict": "pass", "iat": now, "exp": now + 600, "kid": "k"}
    token = sign_token(body, priv)
    mid = len(token) // 2
    bad = token[:mid] + ("X" if token[mid] != "X" else "Y") + token[mid+1:]
    Wal(path=str(tmp_local / "wal.jsonl")).append(
        {"step": "citation_verify", "token": bad})
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M1_D x"), capsys)
    assert code == 0
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


# ── PR4: legacy token shape no longer matches ───────────────────────
def test_legacy_token_with_matter_doc_hash_no_longer_matches(
        tmp_local, keypair, cached_pubkey, capsys):
    """PR4: a WAL token written by a pre-PR2 cloud carries only
    `matter` + `doc_hash`. The post-PR4 gate matches ONLY on canonical
    (subject, payload_hash) fields, so a legacy token is silently
    ignored and the sentinel denies. Operators must roll forward gate +
    cloud together; the PR2 dual-shape compatibility window is over."""
    priv, _ = keypair
    now = int(time.time())
    body = {"step": "citation_verify",
            "matter": "M1", "doc_hash": "D1",
            "verdict": "pass", "iat": now, "exp": now + 600, "kid": "k"}
    Wal(path=str(tmp_local / "wal.jsonl")).append(
        {"step": "citation_verify", "token": sign_token(body, priv)})
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M1_D1 x"), capsys)
    assert code == 0
    body = json.loads(out)
    assert body["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_new_token_with_subject_payload_hash_only_allows(tmp_local, keypair,
                                                           cached_pubkey, capsys):
    """A WAL token signed by a PR4 cloud carries only `subject` +
    `payload_hash` (legacy mirror dropped). The gate accepts it."""
    priv, _ = keypair
    now = int(time.time())
    body = {"step": "citation_verify",
            "subject": "M2", "payload_hash": "D2",
            "verdict": "pass", "iat": now, "exp": now + 600, "kid": "k"}
    Wal(path=str(tmp_local / "wal.jsonl")).append(
        {"step": "citation_verify", "token": sign_token(body, priv)})
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M2_D2 x"), capsys)
    assert code == 0
    assert out == ""


def test_deny_message_uses_subject_payload_hash_vocabulary(tmp_local,
                                                            cached_pubkey, capsys):
    """When no token is found the deny reason mentions the canonical
    vocabulary (subject/payload_hash) so operators searching logs find
    them."""
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_MX_DX motion"),
                                       capsys)
    assert code == 0
    body = json.loads(out)
    reason = body["hookSpecificOutput"]["permissionDecisionReason"]
    assert "subject=" in reason
    assert "payload_hash=" in reason


# ── PR4 FIX: transitional legacy-token acceptance window ─────────────
def test_legacy_token_accepted_when_transition_window_active(
        tmp_local, keypair, cached_pubkey, capsys, monkeypatch):
    """With MAGI_CP_ACCEPT_LEGACY_TOKEN_SHAPE_UNTIL set to a future epoch,
    a token carrying only legacy `matter`/`doc_hash` fields matches the
    sentinel — bridging the deploy window where some pre-PR2 tokens may
    still sit in WAL."""
    priv, _ = keypair
    now = int(time.time())
    monkeypatch.setenv(
        "MAGI_CP_ACCEPT_LEGACY_TOKEN_SHAPE_UNTIL", str(now + 600),
    )
    body = {"step": "citation_verify",
            "matter": "M1", "doc_hash": "D1",
            "verdict": "pass", "iat": now, "exp": now + 600, "kid": "k"}
    Wal(path=str(tmp_local / "wal.jsonl")).append(
        {"step": "citation_verify", "token": sign_token(body, priv)})
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M1_D1 x"), capsys)
    assert code == 0
    assert out == ""   # silent allow


def test_legacy_token_rejected_after_window_expires(
        tmp_local, keypair, cached_pubkey, capsys, monkeypatch):
    """Past-deadline env value → strict canonical (window expired).
    A legacy-only token must NOT match. This is the auto-fail-closed
    behaviour: an operator who forgets to remove the env still gets
    canonical strictness after their chosen epoch."""
    priv, _ = keypair
    now = int(time.time())
    monkeypatch.setenv(
        "MAGI_CP_ACCEPT_LEGACY_TOKEN_SHAPE_UNTIL", str(now - 60),
    )
    body = {"step": "citation_verify",
            "matter": "M1", "doc_hash": "D1",
            "verdict": "pass", "iat": now, "exp": now + 600, "kid": "k"}
    Wal(path=str(tmp_local / "wal.jsonl")).append(
        {"step": "citation_verify", "token": sign_token(body, priv)})
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M1_D1 x"), capsys)
    assert code == 0
    body_out = json.loads(out)
    assert body_out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_legacy_window_does_not_relax_canonical_mismatch(
        tmp_local, keypair, cached_pubkey, capsys, monkeypatch):
    """Defense-in-depth: when a token DOES carry canonical fields but
    those mismatch the sentinel, the legacy alias must NOT silently
    rescue it. This prevents a partial-mix forgery where an attacker
    inserts a token with canonical-mismatch + legacy-match values."""
    priv, _ = keypair
    now = int(time.time())
    monkeypatch.setenv(
        "MAGI_CP_ACCEPT_LEGACY_TOKEN_SHAPE_UNTIL", str(now + 600),
    )
    body = {"step": "citation_verify",
            "subject": "OTHER", "payload_hash": "OTHER",
            "matter": "M1", "doc_hash": "D1",
            "verdict": "pass", "iat": now, "exp": now + 600, "kid": "k"}
    Wal(path=str(tmp_local / "wal.jsonl")).append(
        {"step": "citation_verify", "token": sign_token(body, priv)})
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M1_D1 x"), capsys)
    assert code == 0
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_legacy_window_malformed_env_is_off(
        tmp_local, keypair, cached_pubkey, capsys, monkeypatch):
    """Bad env value (non-integer) → window OFF, default strict canonical."""
    priv, _ = keypair
    now = int(time.time())
    monkeypatch.setenv("MAGI_CP_ACCEPT_LEGACY_TOKEN_SHAPE_UNTIL", "not-a-number")
    body = {"step": "citation_verify",
            "matter": "M1", "doc_hash": "D1",
            "verdict": "pass", "iat": now, "exp": now + 600, "kid": "k"}
    Wal(path=str(tmp_local / "wal.jsonl")).append(
        {"step": "citation_verify", "token": sign_token(body, priv)})
    out, code = _run_evaluate_capture(_payload("echo FILE_COURT_M1_D1 x"), capsys)
    assert code == 0
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"
