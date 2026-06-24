"""P1 evidence — Ed25519 토큰 + hash-chain ledger + WAL.

F1 보안 모델 검증:
  - private key는 서명만, public key는 검증만
  - 토큰 위조 = 서명 깨짐
  - exp 지나면 fail-closed
  - ledger entry의 hash chain이 prev에 정확히 의존
"""

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from magi_cp.evidence import (
    sign_token, verify_token,
    Ledger, Wal,
)


@pytest.fixture
def keypair():
    priv = Ed25519PrivateKey.generate()
    return priv, priv.public_key()


# ── 토큰 sign/verify ────────────────────────────────────────────────
def test_sign_then_verify_round_trip(keypair):
    priv, pub = keypair
    body = {"step": "citation_verify", "subject": "M1", "payload_hash": "D1",
            "verdict": "pass", "iat": 1000, "exp": 2000}
    token = sign_token(body, priv)
    parsed = verify_token(token, pub, now=1500)
    assert parsed == body


def test_verify_fails_on_tamper(keypair):
    priv, pub = keypair
    body = {"step": "x", "verdict": "pass", "iat": 1, "exp": 9999999999}
    token = sign_token(body, priv)
    # base64 마지막 char의 6 bits 중 4 bits는 padding이라 last-char swap이
    # 1/4 확률로 같은 sig로 디코드됨. 중간 char를 변조해서 항상 깨지게.
    mid = len(token) // 2
    bad = token[:mid] + ("X" if token[mid] != "X" else "Y") + token[mid+1:]
    assert verify_token(bad, pub) is None


def test_verify_fails_on_expired(keypair):
    priv, pub = keypair
    body = {"step": "x", "verdict": "pass", "iat": 0, "exp": 100}
    token = sign_token(body, priv)
    assert verify_token(token, pub, now=200) is None
    assert verify_token(token, pub, now=50) is not None  # not expired


def test_verify_rejects_token_without_exp(keypair):
    """Forever-valid 토큰 방지: body에 exp 없으면 거부."""
    priv, pub = keypair
    body = {"step": "x", "verdict": "pass", "iat": 1}  # no exp
    token = sign_token(body, priv)
    assert verify_token(token, pub) is None


def test_verify_rejects_token_with_non_numeric_exp(keypair):
    priv, pub = keypair
    body = {"step": "x", "verdict": "pass", "iat": 1, "exp": "soon"}
    token = sign_token(body, priv)
    assert verify_token(token, pub) is None


def test_verify_fails_with_wrong_pubkey(keypair):
    priv, _ = keypair
    body = {"step": "x", "verdict": "pass", "iat": 0, "exp": 9999999999}
    token = sign_token(body, priv)
    other_pub = Ed25519PrivateKey.generate().public_key()
    assert verify_token(token, other_pub) is None


# ── Ledger: append-only + hash-chain ─────────────────────────────────
def test_ledger_appends_and_chains(tmp_path):
    led = Ledger(path=str(tmp_path / "ledger.jsonl"))
    e1 = led.append({"step": "x", "subject": "S1"}, token="t1")
    e2 = led.append({"step": "y", "subject": "S1"}, token="t2")
    assert e1["prev"] == ""
    assert e2["prev"] == e1["h"]
    assert e1["h"] != e2["h"]


def test_ledger_persists_to_disk(tmp_path):
    p = tmp_path / "ledger.jsonl"
    led1 = Ledger(path=str(p))
    led1.append({"step": "x"}, token="t1")
    led1.append({"step": "y"}, token="t2")
    led2 = Ledger(path=str(p))
    entries = led2.entries()
    assert len(entries) == 2
    assert entries[1]["prev"] == entries[0]["h"]


def test_ledger_detects_tamper(tmp_path):
    p = tmp_path / "ledger.jsonl"
    led = Ledger(path=str(p))
    led.append({"step": "x"}, token="t1")
    led.append({"step": "y"}, token="t2")
    # 파일에서 첫 줄의 verdict 바꾸기 = tamper
    lines = p.read_text().splitlines()
    import json
    bad = json.loads(lines[0])
    bad["body"]["step"] = "TAMPERED"
    lines[0] = json.dumps(bad)
    p.write_text("\n".join(lines) + "\n")
    led2 = Ledger(path=str(p))
    assert not led2.verify_chain()


def test_ledger_verify_chain_passes_on_clean(tmp_path):
    p = tmp_path / "ledger.jsonl"
    led = Ledger(path=str(p))
    led.append({"step": "x"}, token="t1")
    led.append({"step": "y"}, token="t2")
    assert Ledger(path=str(p)).verify_chain()


# ── WAL (local cache) ────────────────────────────────────────────────
def test_wal_append_and_read(tmp_path):
    wal = Wal(path=str(tmp_path / "wal.jsonl"))
    wal.append({"step": "citation_verify", "token": "abc"})
    wal.append({"step": "citation_verify", "token": "def"})
    entries = wal.entries()
    assert len(entries) == 2
    assert entries[1]["token"] == "def"


def test_wal_empty_when_missing(tmp_path):
    wal = Wal(path=str(tmp_path / "absent.jsonl"))
    assert wal.entries() == []


def test_wal_clear(tmp_path):
    wal = Wal(path=str(tmp_path / "wal.jsonl"))
    wal.append({"step": "x", "token": "t"})
    wal.clear()
    assert wal.entries() == []
