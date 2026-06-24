"""v2.0-W7b — Ed25519 multi-key + rotation.

Multi-key model:
  - KeyStore manages N keypairs in `<dir>/<kid>/{private.pem,public.pem}`
  - One kid is `active` (used for signing); the rest are `verifying` (can
    verify but never sign; needed so tokens issued before the rotation
    still verify until they expire)
  - `<dir>/ACTIVE` is a tiny file containing the active kid (one line)
  - Legacy `<dir>/ed25519_private.pem` + `ed25519_public.pem` is migrated
    to the new layout on first ensure_keypair() call (backwards compat)

Rotation flow:
  - rotate() generates a fresh keypair, makes it active, keeps prior keys
    as verifying-only
  - revoke(kid) deletes the keypair files entirely; tokens signed by that
    kid no longer verify (call AFTER the token TTL has passed in production)
"""
import os

import pytest


def _ks(tmp_path):
    from magi_cp.cloud.keys import KeyStore
    return KeyStore(dir=str(tmp_path / "keys"))


# ── single-key backward compat ─────────────────────────────────────
class TestBackwardCompat:
    def test_ensure_keypair_creates_initial(self, tmp_path):
        ks = _ks(tmp_path)
        ks.ensure_keypair()
        # Two flavors of layout: legacy (top-level pem) or new (per-kid dirs).
        # Either way, active_kid() returns a kid + load_private/public work.
        kid = ks.active_kid()
        assert kid and len(kid) >= 8
        priv = ks.load_private()
        pub = ks.load_public(kid)
        assert priv is not None and pub is not None

    def test_legacy_layout_is_migrated_in_place(self, tmp_path):
        """A legacy keypair in <dir>/ed25519_*.pem is adopted as the active
        key on first ensure_keypair() of the new layout."""
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
        legacy_dir = tmp_path / "keys"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        # Write legacy-layout files
        priv = Ed25519PrivateKey.generate()
        priv_pem = priv.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        pub_pem = priv.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        # Use the legacy filenames the old KeyStore used.
        os.umask(0o077)
        fd = os.open(legacy_dir / "ed25519_private.pem",
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, priv_pem)
        finally:
            os.close(fd)
        (legacy_dir / "ed25519_public.pem").write_bytes(pub_pem)

        # ensure_keypair() should adopt this — NOT generate a new one.
        from magi_cp.cloud.keys import KeyStore
        ks = KeyStore(dir=str(legacy_dir))
        ks.ensure_keypair()
        # Active key should be the same one we wrote.
        loaded_pub_pem = ks.public_pem()
        assert loaded_pub_pem.strip() == pub_pem.decode("utf-8").strip()


# ── rotation ───────────────────────────────────────────────────────
class TestRotation:
    def test_rotate_creates_new_active_and_keeps_old(self, tmp_path):
        ks = _ks(tmp_path)
        ks.ensure_keypair()
        old_kid = ks.active_kid()
        old_pub_pem = ks.public_pem()

        new_kid = ks.rotate()
        assert new_kid != old_kid
        # Active flipped
        assert ks.active_kid() == new_kid
        # Old kid still exists as verifying
        assert old_kid in ks.list_kids()
        # Old public still loadable for verification of in-flight tokens
        assert ks.load_public(old_kid) is not None
        # New public is different from old
        assert ks.public_pem() != old_pub_pem

    def test_rotate_signs_with_new_key(self, tmp_path):
        import time as _time
        ks = _ks(tmp_path)
        ks.ensure_keypair()
        old_kid = ks.active_kid()
        new_kid = ks.rotate()

        # Sign a payload (exp required), verify with the new public key
        from magi_cp.evidence import sign_token, verify_token
        token = sign_token({"sub": "test", "exp": int(_time.time()) + 60},
                           ks.load_private())
        body = verify_token(token, ks.load_public(new_kid))
        assert body is not None and body["sub"] == "test"
        # Verifying with old kid's pub key must fail — returns None, not raise
        assert verify_token(token, ks.load_public(old_kid)) is None

    def test_revoke_removes_keypair(self, tmp_path):
        ks = _ks(tmp_path)
        ks.ensure_keypair()
        old_kid = ks.active_kid()
        ks.rotate()
        # revoke old kid
        ks.revoke(old_kid)
        assert old_kid not in ks.list_kids()
        assert ks.load_public(old_kid) is None

    def test_cannot_revoke_active(self, tmp_path):
        ks = _ks(tmp_path)
        ks.ensure_keypair()
        active = ks.active_kid()
        with pytest.raises(ValueError, match="active"):
            ks.revoke(active)


# ── public_pem map for /pubkey endpoint ────────────────────────────
class TestPubkeyMap:
    def test_lists_all_kids_with_their_pems(self, tmp_path):
        ks = _ks(tmp_path)
        ks.ensure_keypair()
        ks.rotate()
        ks.rotate()
        kids = ks.list_kids()
        assert len(kids) == 3
        m = ks.public_pem_map()
        # Every kid has a PEM
        assert set(m.keys()) == set(kids)
        for pem in m.values():
            assert "BEGIN PUBLIC KEY" in pem

    def test_active_pem_matches_active_kid_in_map(self, tmp_path):
        ks = _ks(tmp_path)
        ks.ensure_keypair()
        ks.rotate()
        m = ks.public_pem_map()
        assert m[ks.active_kid()].strip() == ks.public_pem().strip()


# ── mode check (still 0600 on private files) ───────────────────────
class TestSecurityInvariants:
    def test_rotated_private_is_mode_600(self, tmp_path):
        ks = _ks(tmp_path)
        ks.ensure_keypair()
        new_kid = ks.rotate()
        priv_path = ks.private_path_for(new_kid)
        mode = priv_path.stat().st_mode & 0o777
        assert mode == 0o600, f"got {oct(mode)}"

    def test_rejects_wrong_mode_on_load(self, tmp_path):
        ks = _ks(tmp_path)
        ks.ensure_keypair()
        priv_path = ks.private_path_for(ks.active_kid())
        os.chmod(priv_path, 0o644)
        with pytest.raises(PermissionError, match="0600"):
            ks.load_private()
