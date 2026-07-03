"""Shared request-shape limits + token constants for the cloud app.

Extracted from cloud/app.py so that both the FastAPI app and the request
schemas (cloud/schemas.py) can import them without a circular dependency
(app -> schemas -> app). Values are unchanged.
"""
from __future__ import annotations

# Token lifetime. Short + refreshable; license expiry means fail-closed.
TOKEN_TTL_SECONDS = 600

# Request-shape limits (bytes / counts).
MAX_REQUEST_BYTES = 256 * 1024
MAX_CITATIONS_PER_REQUEST = 50
MAX_QUOTE_LEN = 8_000
MAX_REF_LEN = 1_000
MAX_DOCUMENT_LEN = 200_000
MAX_CORPUS_OVERRIDE_BYTES = 200_000
MAX_VERIFIER_PAYLOAD_BYTES = 20_000

# Canonical id / key patterns.
# `_KEY_PATTERN`: subject / payload_hash wire shape.
_KEY_PATTERN = r"^[A-Za-z0-9_\-]+$"
# `_POLICY_ID_PATTERN`: a policy id (seg," / "-separated, <=128 chars).
_POLICY_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,127}$"
# Policy id suffixes reserved for sibling routes (/compiled, /enabled).
_RESERVED_ID_SUFFIXES = ("/compiled", "/enabled")

# Fields the cloud stamps into a signed token itself; a caller-supplied
# `extra` may not override any of them.
PROTECTED_TOKEN_FIELDS = {
    "step",
    # PR4: canonical keying ONLY. Subject = generic subject identifier
    # (e.g. "session_abc", "req_xyz", or for legal verticals: matter id).
    # payload_hash = sha256 of canonical tool payload (or for legal:
    # doc_id). PR2 had a transition window with legacy `matter`/`doc_hash`
    # mirrored alongside; PR4 drops both legacy names from the protected
    # set and from token bodies entirely. Any deployed gate older than
    # PR2 will no longer find a verifying token — operators upgrading
    # past PR4 must roll forward gate binaries first.
    "subject", "payload_hash",
    "verdict", "iat", "exp", "issuer", "kid",
}

__all__ = [
    "TOKEN_TTL_SECONDS",
    "MAX_REQUEST_BYTES",
    "MAX_CITATIONS_PER_REQUEST",
    "MAX_QUOTE_LEN",
    "MAX_REF_LEN",
    "MAX_DOCUMENT_LEN",
    "MAX_CORPUS_OVERRIDE_BYTES",
    "MAX_VERIFIER_PAYLOAD_BYTES",
    "_KEY_PATTERN",
    "_POLICY_ID_PATTERN",
    "_RESERVED_ID_SUFFIXES",
    "PROTECTED_TOKEN_FIELDS",
]
