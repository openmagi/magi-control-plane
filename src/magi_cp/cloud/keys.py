"""Ed25519 keypair lifecycle — multi-key with rotation.

Layout on disk (`<dir>/`):
  ACTIVE                          one-line: kid of current signing key
  <kid>/private.pem               PKCS8 PEM, mode 0600
  <kid>/public.pem                SubjectPublicKeyInfo PEM, mode 0644

Legacy (v1 single-keypair) layout:
  ed25519_private.pem
  ed25519_public.pem
  → migrated in-place on first ensure_keypair() of the new layout. The
    legacy keypair becomes the active key under its own freshly-computed kid.

Why kid = sha256(pubkey_pem)[:16]:
  - Stable across re-reads (no random component)
  - Short enough to use as a directory name
  - Long enough that collisions are impossible in practice
  - Anyone with the public key can independently compute kid for verify
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)


def _compute_kid(pub_pem: bytes) -> str:
    return hashlib.sha256(pub_pem).hexdigest()[:16]


def _generate_keypair() -> tuple[bytes, bytes]:
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
    return priv_pem, pub_pem


class KeyStore:
    LEGACY_PRIV = "ed25519_private.pem"
    LEGACY_PUB = "ed25519_public.pem"
    ACTIVE_FILE = "ACTIVE"
    PRIV_NAME = "private.pem"
    PUB_NAME = "public.pem"

    def __init__(self, dir: str):
        self.dir = Path(dir)

    # ── paths ───────────────────────────────────────────────────────
    @property
    def active_file(self) -> Path:
        return self.dir / self.ACTIVE_FILE

    @property
    def legacy_priv_path(self) -> Path:
        return self.dir / self.LEGACY_PRIV

    @property
    def legacy_pub_path(self) -> Path:
        return self.dir / self.LEGACY_PUB

    def kid_dir(self, kid: str) -> Path:
        return self.dir / kid

    def private_path_for(self, kid: str) -> Path:
        return self.kid_dir(kid) / self.PRIV_NAME

    def public_path_for(self, kid: str) -> Path:
        return self.kid_dir(kid) / self.PUB_NAME

    # legacy-shim properties kept ONLY for callers that haven't migrated
    # to active_kid()-based access; both still work and return the active
    # key's paths. Avoid using these in new code.
    @property
    def priv_path(self) -> Path:
        return self.private_path_for(self.active_kid())

    @property
    def pub_path(self) -> Path:
        return self.public_path_for(self.active_kid())

    # ── lifecycle ──────────────────────────────────────────────────
    def ensure_keypair(self) -> None:
        """Ensure at least one keypair + an ACTIVE marker exist."""
        self.dir.mkdir(parents=True, exist_ok=True)
        # Legacy migration: if old files exist and no new layout yet, adopt them.
        if (self.legacy_priv_path.exists()
            and self.legacy_pub_path.exists()
            and not self.active_file.exists()):
            self._migrate_legacy()
            return
        if self.active_file.exists():
            # Sanity-check: active points at an existing key.
            kid = self._read_active()
            if kid and self.private_path_for(kid).exists():
                return
        # Fresh generation
        kid = self._write_keypair(*_generate_keypair())
        self._write_active(kid)

    def _migrate_legacy(self) -> None:
        """Move the legacy `ed25519_*.pem` pair into the per-kid layout."""
        pub_pem = self.legacy_pub_path.read_bytes()
        priv_pem = self.legacy_priv_path.read_bytes()
        kid = _compute_kid(pub_pem)
        self._write_keypair(priv_pem, pub_pem, kid_override=kid)
        # Drop the legacy files (they're now under their kid dir)
        try:
            self.legacy_priv_path.unlink()
            self.legacy_pub_path.unlink()
        except OSError:
            pass
        self._write_active(kid)

    def _write_keypair(self, priv_pem: bytes, pub_pem: bytes,
                        kid_override: str | None = None) -> str:
        kid = kid_override or _compute_kid(pub_pem)
        kd = self.kid_dir(kid)
        kd.mkdir(parents=True, exist_ok=True)
        priv_p = self.private_path_for(kid)
        pub_p = self.public_path_for(kid)
        # O_EXCL prevents concurrent writers from clobbering the priv key with
        # a half-written file. Mode set in flags so there's no readable window.
        if not priv_p.exists():
            fd = os.open(priv_p,
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600)
            try:
                os.write(fd, priv_pem)
            finally:
                os.close(fd)
        if not pub_p.exists():
            fd = os.open(pub_p,
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o644)
            try:
                os.write(fd, pub_pem)
            finally:
                os.close(fd)
        return kid

    def _write_active(self, kid: str) -> None:
        # Atomic via .tmp + rename.
        tmp = self.dir / (self.ACTIVE_FILE + ".tmp")
        tmp.write_text(kid + "\n", encoding="utf-8")
        tmp.replace(self.active_file)

    def _read_active(self) -> str | None:
        if not self.active_file.exists():
            return None
        line = self.active_file.read_text(encoding="utf-8").strip()
        return line or None

    # ── public read surface ───────────────────────────────────────
    def active_kid(self) -> str:
        kid = self._read_active()
        if not kid:
            raise RuntimeError("KeyStore has no active key — call ensure_keypair()")
        return kid

    def list_kids(self) -> list[str]:
        """All kids that have both private + public files on disk."""
        if not self.dir.exists():
            return []
        kids: list[str] = []
        for entry in self.dir.iterdir():
            if not entry.is_dir():
                continue
            if (entry / self.PRIV_NAME).exists() and (entry / self.PUB_NAME).exists():
                kids.append(entry.name)
        return sorted(kids)

    def load_private(self, kid: str | None = None) -> Ed25519PrivateKey:
        if kid is None:
            kid = self.active_kid()
        priv_p = self.private_path_for(kid)
        mode = priv_p.stat().st_mode & 0o777
        if mode != 0o600:
            raise PermissionError(
                f"private key {priv_p} must be mode 0600 (got 0o{mode:03o})"
            )
        return serialization.load_pem_private_key(priv_p.read_bytes(), password=None)

    def load_public(self, kid: str | None = None) -> Ed25519PublicKey | None:
        """None if kid is unknown — let the caller decide whether that's a
        verify failure or a 'try the next kid' signal."""
        if kid is None:
            kid = self.active_kid()
        pub_p = self.public_path_for(kid)
        if not pub_p.exists():
            return None
        return serialization.load_pem_public_key(pub_p.read_bytes())

    def public_pem(self, kid: str | None = None) -> str:
        if kid is None:
            kid = self.active_kid()
        return self.public_path_for(kid).read_text(encoding="utf-8")

    def public_pem_map(self) -> dict[str, str]:
        """{kid: public_pem_string} for the /pubkey endpoint to advertise.

        Clients pick the entry matching the kid in the token they're verifying.
        """
        return {kid: self.public_pem(kid) for kid in self.list_kids()}

    # ── rotation ──────────────────────────────────────────────────
    def rotate(self) -> str:
        """Generate a new keypair, set it active, leave prior keys in place."""
        kid = self._write_keypair(*_generate_keypair())
        self._write_active(kid)
        return kid

    def revoke(self, kid: str) -> None:
        """Delete a keypair. Refuses to revoke the active key."""
        if kid == self.active_kid():
            raise ValueError(
                f"cannot revoke active kid {kid!r}; rotate() to a new key first"
            )
        kd = self.kid_dir(kid)
        if not kd.exists():
            return
        for p in (self.private_path_for(kid), self.public_path_for(kid)):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        try:
            kd.rmdir()
        except OSError:
            pass
