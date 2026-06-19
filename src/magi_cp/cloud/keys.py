"""Ed25519 keypair lifecycle.

The private key must never leave this process. Files are PEM (PKCS8) for
the private key and SubjectPublicKeyInfo PEM for the public.

`load_private` refuses to read a private key that is not mode 0600 — a defensive
check against accidental publication via container image, backup, or `ls -la`
review.
"""
from __future__ import annotations
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)


class KeyStore:
    PRIV_FILENAME = "ed25519_private.pem"
    PUB_FILENAME = "ed25519_public.pem"

    def __init__(self, dir: str):
        self.dir = Path(dir)

    @property
    def priv_path(self) -> Path:
        return self.dir / self.PRIV_FILENAME

    @property
    def pub_path(self) -> Path:
        return self.dir / self.PUB_FILENAME

    def ensure_keypair(self) -> None:
        """Generate keypair if missing. Idempotent + race-safe.

        Uses O_EXCL so concurrent boots cannot both create+overwrite the same
        private key file (M3 fix). Loses the race side just sees `FileExistsError`
        and adopts the winner's keypair. File mode is set in the open flags so
        there is no readable window before the chmod.
        """
        if self.priv_path.exists() and self.pub_path.exists():
            return
        self.dir.mkdir(parents=True, exist_ok=True)
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
        try:
            fd = os.open(self.priv_path,
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600)
            try:
                os.write(fd, priv_pem)
            finally:
                os.close(fd)
            # write pub paired with this priv only after priv is durable
            fd = os.open(self.pub_path,
                         os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                         0o644)
            try:
                os.write(fd, pub_pem)
            finally:
                os.close(fd)
        except FileExistsError:
            # someone else won the race; their keypair is canonical
            return

    def load_private(self) -> Ed25519PrivateKey:
        mode = self.priv_path.stat().st_mode & 0o777
        if mode != 0o600:
            raise PermissionError(
                f"private key {self.priv_path} must be mode 0600 (got 0o{mode:03o})"
            )
        return serialization.load_pem_private_key(self.priv_path.read_bytes(), password=None)

    def load_public(self) -> Ed25519PublicKey:
        return serialization.load_pem_public_key(self.pub_path.read_bytes())

    def public_pem(self) -> str:
        return self.pub_path.read_text()
