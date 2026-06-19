"""Ed25519 sign/verify (JWS-like, no JOSE dep). Payload is canonical JSON.

Why custom (not pyJWT/python-jose): single algorithm, single-purpose, fewer
deps, easier audit. Token format = base64url(payload) + "." + base64url(sig).
"""
from __future__ import annotations
import base64
import json
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64u_d(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)


def _canonical(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")


def sign_token(body: dict, priv: Ed25519PrivateKey) -> str:
    payload = _canonical(body)
    sig = priv.sign(payload)
    return f"{_b64u(payload)}.{_b64u(sig)}"


def verify_token(token: str, pub: Ed25519PublicKey, *, now: int | None = None) -> dict | None:
    """Return parsed body if signature valid AND not expired; else None.

    `exp` is interpreted as a unix-second deadline. `now` lets tests inject time.
    """
    try:
        payload_b64, sig_b64 = token.split(".")
        payload = _b64u_d(payload_b64)
        sig = _b64u_d(sig_b64)
        pub.verify(sig, payload)
    except (InvalidSignature, ValueError):
        return None
    body = json.loads(payload)
    # `exp` is required on every issued token. A body without `exp` is rejected
    # to prevent a buggy/malicious signer from minting forever-valid tokens.
    deadline = body.get("exp")
    if not isinstance(deadline, (int, float)):
        return None
    if (now if now is not None else int(time.time())) >= deadline:
        return None
    return body
