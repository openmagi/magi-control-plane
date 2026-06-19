"""P3 cloud — Ed25519 keypair lifecycle on disk."""
import os
from pathlib import Path

import pytest

from magi_cp.cloud.keys import KeyStore


def test_generate_creates_keys_with_private_0600(tmp_path: Path):
    ks = KeyStore(dir=str(tmp_path))
    ks.ensure_keypair()
    priv = tmp_path / "ed25519_private.pem"
    pub = tmp_path / "ed25519_public.pem"
    assert priv.exists() and pub.exists()
    assert (priv.stat().st_mode & 0o777) == 0o600, "private key must be 0600"


def test_ensure_keypair_is_idempotent(tmp_path: Path):
    ks = KeyStore(dir=str(tmp_path))
    ks.ensure_keypair()
    orig = (tmp_path / "ed25519_private.pem").read_bytes()
    ks.ensure_keypair()
    again = (tmp_path / "ed25519_private.pem").read_bytes()
    assert orig == again


def test_load_returns_matching_keypair(tmp_path: Path):
    ks = KeyStore(dir=str(tmp_path))
    ks.ensure_keypair()
    priv = ks.load_private()
    pub_loaded = ks.load_public()
    # round-trip: sign with priv, verify with loaded pub
    msg = b"hello"
    sig = priv.sign(msg)
    pub_loaded.verify(sig, msg)   # raises if mismatch


def test_load_private_refuses_world_readable(tmp_path: Path):
    ks = KeyStore(dir=str(tmp_path))
    ks.ensure_keypair()
    priv = tmp_path / "ed25519_private.pem"
    os.chmod(priv, 0o644)         # world-readable
    with pytest.raises(PermissionError, match="0600"):
        ks.load_private()


def test_public_key_pem_returned_for_distribution(tmp_path: Path):
    ks = KeyStore(dir=str(tmp_path))
    ks.ensure_keypair()
    pem = ks.public_pem()
    assert "BEGIN PUBLIC KEY" in pem
    assert "PRIVATE" not in pem
