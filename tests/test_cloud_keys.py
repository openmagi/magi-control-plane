"""Ed25519 keypair lifecycle on disk — multi-key layout (W7b)."""
import os
from pathlib import Path

import pytest

from magi_cp.cloud.keys import KeyStore


def test_generate_creates_keys_with_private_0600(tmp_path: Path):
    """ensure_keypair() creates an active keypair with the private file 0600."""
    ks = KeyStore(dir=str(tmp_path))
    ks.ensure_keypair()
    kid = ks.active_kid()
    priv = ks.private_path_for(kid)
    pub = ks.public_path_for(kid)
    assert priv.exists() and pub.exists()
    assert (priv.stat().st_mode & 0o777) == 0o600, "private key must be 0600"


def test_ensure_keypair_is_idempotent(tmp_path: Path):
    ks = KeyStore(dir=str(tmp_path))
    ks.ensure_keypair()
    kid = ks.active_kid()
    orig = ks.private_path_for(kid).read_bytes()
    ks.ensure_keypair()
    again = ks.private_path_for(ks.active_kid()).read_bytes()
    assert orig == again
    assert kid == ks.active_kid()


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
    priv = ks.private_path_for(ks.active_kid())
    os.chmod(priv, 0o644)         # world-readable
    with pytest.raises(PermissionError, match="0600"):
        ks.load_private()


def test_public_key_pem_returned_for_distribution(tmp_path: Path):
    ks = KeyStore(dir=str(tmp_path))
    ks.ensure_keypair()
    pem = ks.public_pem()
    assert "BEGIN PUBLIC KEY" in pem
    assert "PRIVATE" not in pem
