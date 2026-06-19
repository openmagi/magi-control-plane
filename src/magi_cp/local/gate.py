"""Local PreToolUse hook entry point.

CC pipes the hook event JSON on stdin. We parse `tool_input.command` for the
sentinel `FILE_COURT_<matter>_<doc_id>`, then ask the cloud for a verdict.

Output protocol:
  - Allow → exit 0 silently (CC continues normal permission flow).
  - Deny  → print {hookSpecificOutput:{permissionDecision:"deny",...}} JSON + exit 0.
  - Cloud unreachable → fail-closed deny ("license expiry = bundle expiry").
"""
from __future__ import annotations
import json
import os
import re
import sys
import urllib.error
import urllib.request

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ..evidence import verify_token, Wal


def _cloud_url() -> str:
    return os.environ.get("MAGI_CP_CLOUD_URL", "http://127.0.0.1:8787")


def _local_dir() -> str:
    return os.environ.get("MAGI_CP_LOCAL_DIR",
                          os.path.expanduser("~/.magi-cp/local"))


# Anchored sentinel: forbid trailing `_<more>` (e.g. FILE_COURT_M_D_v2) so the
# matter / doc_id capture cannot silently drop tail bytes that change identity.
SENTINEL_RE = re.compile(r"\bFILE_COURT_([A-Za-z0-9]+)_([A-Za-z0-9]+)(?!_)\b")


def _deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"MAGI: {reason}",
        }
    }, ensure_ascii=False))
    sys.exit(0)


def _allow() -> None:
    # silent → CC continues normal permission flow
    sys.exit(0)


def _load_pubkey_for_kid(kid: str | None) -> Ed25519PublicKey:
    """Fetch + cache cloud pubkey, indexed by kid so rotation is supported.

    A token carrying kid=X requires the public key whose sha256 prefix is X.
    Without kid (legacy/test path) we fall back to the active pubkey. Files
    are written 0o600 so a non-root local user cannot overwrite the trust
    anchor; if a cached file has loose mode, refuse and re-fetch.
    """
    local_dir = _local_dir()
    fname = f"pubkey-{kid}.pem" if kid else "pubkey.pem"
    cache = os.path.join(local_dir, fname)

    def _read_cached() -> Ed25519PublicKey | None:
        if not os.path.exists(cache):
            return None
        st = os.stat(cache)
        if (st.st_mode & 0o077) != 0:
            os.remove(cache)   # world/group readable → refuse, force re-fetch
            return None
        with open(cache, "rb") as f:
            return serialization.load_pem_public_key(f.read())

    cached = _read_cached()
    if cached is not None:
        return cached

    os.makedirs(local_dir, exist_ok=True)
    with urllib.request.urlopen(_cloud_url() + "/pubkey", timeout=5) as r:
        data = json.loads(r.read())
        pem = data["pubkey_pem"]
        served_kid = data.get("kid")
    if kid and served_kid and served_kid != kid:
        # Cloud doesn't currently advertise old keys — pin mismatch = fail closed.
        raise ValueError(f"kid mismatch: token wants {kid!r}, cloud serves {served_kid!r}")
    # atomic 0600 write (no readable window)
    fd = os.open(cache, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, pem.encode("utf-8"))
    finally:
        os.close(fd)
    return serialization.load_pem_public_key(pem.encode("utf-8"))


def _load_pubkey() -> Ed25519PublicKey:
    """Legacy entry point: no kid known yet, use active pubkey."""
    return _load_pubkey_for_kid(None)


def _find_signed_token(wal: Wal, pub: Ed25519PublicKey, *, matter: str, doc_id: str) -> dict | None:
    """Scan WAL for the *latest* citation_verify token bound to (matter, doc_id)
    whose signature verifies under `pub` and which is not expired.

    Why latest, not first: a later `verdict=fail|review|deny` token for the same
    (matter, doc_id) MUST invalidate an earlier `pass` — otherwise a stale
    success could authorize a re-edited document. The latest decision wins.
    """
    latest: dict | None = None
    latest_iat = -1
    expected_kid: str | None = None
    for entry in wal.entries():
        if entry.get("step") != "citation_verify":
            continue
        body = verify_token(entry.get("token", ""), pub)
        if not body:
            continue
        if body.get("matter") != matter or body.get("doc_hash") != doc_id:
            continue
        # Kid pinning: every valid token for this (matter, doc_id) must agree
        # on kid. A late-rotated token with a *different* kid means the gate
        # is holding a stale pubkey; treat as suspect and reject.
        kid = body.get("kid")
        if expected_kid is None:
            expected_kid = kid
        elif kid != expected_kid:
            continue
        iat = body.get("iat", 0)
        if iat > latest_iat:
            latest_iat, latest = iat, body
    if latest is None or latest.get("verdict") != "pass":
        return None
    return latest


def evaluate(payload: dict) -> None:
    """Decide allow/deny from a PreToolUse hook payload.

    Exits the process directly (CC reads stdout + exit code).
    """
    cmd = payload.get("tool_input", {}).get("command", "")
    matches = list(SENTINEL_RE.finditer(cmd))
    if not matches:
        _allow()   # not a sentinel; CC continues
    cloud = _cloud_url()
    if not cloud.startswith(("http://", "https://")):
        _deny(f"invalid MAGI_CP_CLOUD_URL scheme")
    try:
        pub = _load_pubkey()
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as e:
        _deny(f"cloud unreachable ({type(e).__name__})")

    wal = Wal(path=os.path.join(_local_dir(), "wal.jsonl"))
    # Every sentinel must individually validate. Multi-statement commands like
    # `FILE_COURT_A_X; FILE_COURT_B_Y` would otherwise allow Y to ride on X's
    # token (or vice versa).
    for m in matches:
        matter, doc_id = m.group(1), m.group(2)
        body = _find_signed_token(wal, pub, matter=matter, doc_id=doc_id)
        if body is None:
            _deny(f"no signed citation_verify=pass for matter={matter} doc={doc_id}")
    _allow()


def cli() -> int:  # pragma: no cover (CLI entry)
    raw = sys.stdin.read().strip()
    if not raw:
        # Started outside a hook context; pass through
        _allow()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        _deny("malformed hook payload (json)")
    evaluate(payload)
    return 0   # unreachable; evaluate exits


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(cli())
