"""Local PreToolUse hook entry point.

CC pipes the hook event JSON on stdin. We parse `tool_input.command` for the
sentinel `FILE_COURT_<subject>_<payload_hash>` (legacy: `<matter>_<doc_id>`),
then ask the cloud for a verdict.

PR2 NOTE — sentinel-less policies (`sentinel_re=None`):
  As of D43/D44, `Policy.sentinel_re` is Optional and the wizard no longer
  auto-emits a default sentinel. A policy authored with `sentinel_re=None`
  produces NO local-gate hook trigger in this v2.x line — every PreToolUse
  silently `_allow()`s before any policy logic runs. The cloud-side runtime
  surfaces (e.g. `/verify_inline`, /verify/{step}) still enforce such policies
  end-to-end, but the local CC PreToolUse path requires a literal sentinel
  match in `tool_input.command`. Surfacing sentinel-less policies on the
  local gate is tracked as the "missing CC native surfaces" bullet of
  tracking-issue #1 and is intended for a future surface (out of scope for
  PR2/PR3). Until then, authors who need local CC enforcement MUST keep a
  `sentinel_re` on the policy.

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


def _find_signed_token(wal: Wal, pub: Ed25519PublicKey, *,
                       subject: str, payload_hash: str) -> dict | None:
    """Scan WAL for the *latest* citation_verify token bound to
    (subject, payload_hash) whose signature verifies under `pub` and which
    is not expired.

    PR2: keying renamed from (matter, doc_id/doc_hash) → (subject, payload_hash).
    For back-compat the lookup accepts EITHER set of token-body fields: a
    token written by a pre-PR2 cloud carries `matter`/`doc_hash` only; a
    post-PR2 cloud carries both legacy mirror fields AND `subject`/
    `payload_hash`. We accept a hit if EITHER pair matches the requested
    keys. This lets a gate cross the upgrade boundary without flushing its
    WAL.

    Why latest, not first: a later `verdict=fail|review|deny` token for the
    same key MUST invalidate an earlier `pass` — otherwise a stale success
    could authorize a re-edited document. The latest decision wins.

    PR2 review fix (issue #1 follow-up):
      Kid-pinning is decided from the NEWEST verifying token (by iat),
      not the oldest-encountered one. Pre-PR2 the loop visited WAL entries
      in append (oldest-first) order, pinned `expected_kid` to the first
      match, and dropped every later entry whose `kid` differed. PR2's
      dual-shape lookup expanded the pool of matched entries, so a gate
      crossing the upgrade boundary could end up pinning to a stale,
      older-kid pass token and discarding the newer, rotated-kid pass
      token for the same logical key — letting a stale pass win.

      Two-pass approach: first collect ALL verifying tokens that match by
      either shape, then pick the highest-iat entry, then drop any sibling
      whose kid disagrees with that newest entry's kid. This matches the
      "latest decision wins" intent. Each token's signature is still
      independently verified inside `verify_token`, and
      `_load_pubkey_for_kid` already fail-closes on a kid-vs-cloud
      mismatch — so dropping the older-kid sibling here is purely a
      defense-in-depth pin, not the load-bearing trust check.
    """
    candidates: list[dict] = []
    for entry in wal.entries():
        if entry.get("step") != "citation_verify":
            continue
        body = verify_token(entry.get("token", ""), pub)
        if not body:
            continue
        new_match = (body.get("subject") == subject
                     and body.get("payload_hash") == payload_hash)
        legacy_match = (body.get("matter") == subject
                        and body.get("doc_hash") == payload_hash)
        if not (new_match or legacy_match):
            continue
        candidates.append(body)

    if not candidates:
        return None

    # Newest first by iat. Stable sort preserves WAL order for ties so we
    # still get deterministic behaviour when two tokens share an iat.
    candidates.sort(key=lambda b: b.get("iat", 0), reverse=True)
    newest = candidates[0]
    expected_kid = newest.get("kid")

    # Walk newest-first, taking the first kid-matching pass — any later
    # `review|deny` token for the same key already short-circuits the
    # selection because it sits above an earlier `pass` in the sorted list.
    for body in candidates:
        if body.get("kid") != expected_kid:
            continue
        if body.get("verdict") != "pass":
            # Latest decision is not a pass; stale earlier pass cannot win.
            return None
        return body
    return None


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
    #
    # PR2: the sentinel's two captured groups are now treated as
    # (subject, payload_hash). For legal-vertical sentinels that's still
    # (matter, doc_id) semantically — the token-body match handles both
    # naming schemes so legacy and new tokens both find a match.
    for m in matches:
        subject, payload_hash = m.group(1), m.group(2)
        body = _find_signed_token(wal, pub, subject=subject,
                                  payload_hash=payload_hash)
        if body is None:
            _deny(
                f"no signed citation_verify=pass for subject={subject} "
                f"payload_hash={payload_hash}"
            )
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
