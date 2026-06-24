"""Local PreToolUse hook entry point.

CC pipes the hook event JSON on stdin. We parse `tool_input.command` for the
sentinel `FILE_COURT_<subject>_<payload_hash>`, then ask the cloud for a
verdict.

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

PR4 NOTE — transitional legacy-token acceptance:
  PR4 drops cloud-side `matter` / `doc_hash` mirrors from token bodies. A
  WAL token signed by a pre-PR2 cloud carries ONLY the legacy fields and
  no longer matches by default. For the deploy window where some PR4 gates
  may still see legacy tokens that were cached pre-roll, the operator can
  set `MAGI_CP_ACCEPT_LEGACY_TOKEN_SHAPE_UNTIL=<unix_ts>`: tokens whose
  body has `matter == <subject>` and `doc_hash == <payload_hash>` will
  match through that epoch. Default-OFF (env unset → strict canonical).
  After the epoch elapses the env flips fail-closed automatically; this
  avoids a forgotten always-on bypass. The whole window is bounded by
  TOKEN_TTL_SECONDS=600 because expired tokens still get rejected by
  `verify_token`.

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
    """Resolve the cloud URL.

    Issue #1 P1 (#6): default is `http://127.0.0.1:8787` for the dev
    loop, but any non-loopback `http://` URL is refused at runtime —
    a man-in-the-middle on the first pubkey fetch would otherwise
    pin the attacker's key permanently. Operators wanting plain
    HTTP for an internal lab must set `MAGI_CP_ALLOW_PLAIN_HTTP=1`
    explicitly.
    """
    url = os.environ.get("MAGI_CP_CLOUD_URL", "http://127.0.0.1:8787")
    return url


def _enforce_url_scheme(url: str) -> None:
    """Refuse plain HTTP except for loopback or explicit opt-in.

    Raises ValueError on rejection; callers fail-closed.
    """
    if url.startswith("https://"):
        return
    if url.startswith("http://"):
        if os.environ.get("MAGI_CP_ALLOW_PLAIN_HTTP") == "1":
            return
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
        if host in ("127.0.0.1", "localhost", "::1"):
            return
        raise ValueError(
            f"refusing plain HTTP to non-loopback host ({host!r}); set "
            f"MAGI_CP_CLOUD_URL to https:// or "
            f"MAGI_CP_ALLOW_PLAIN_HTTP=1 to override"
        )
    raise ValueError(f"unsupported MAGI_CP_CLOUD_URL scheme: {url!r}")


def _local_dir() -> str:
    return os.environ.get("MAGI_CP_LOCAL_DIR",
                          os.path.expanduser("~/.magi-cp/local"))


# Anchored sentinel: forbid trailing `_<more>` (e.g. FILE_COURT_S_P_v2) so the
# subject / payload_hash capture cannot silently drop tail bytes that change
# identity.
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
        # Issue #1 P1 (#6): close the TOCTOU window between stat() and
        # open() by opening with O_NOFOLLOW + fstat() on the same
        # descriptor. A process running as the same UID can no longer
        # swap the file between the two syscalls.
        try:
            fd = os.open(cache, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        except OSError:
            # ELOOP (symlink) / EACCES / etc — refuse fail-closed.
            return None
        try:
            st = os.fstat(fd)
            if (st.st_mode & 0o077) != 0:
                # world/group readable → refuse, force re-fetch.
                try:
                    os.remove(cache)
                except OSError:
                    pass
                return None
            data = b""
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                data += chunk
            return serialization.load_pem_public_key(data)
        finally:
            os.close(fd)

    cached = _read_cached()
    if cached is not None:
        return cached

    os.makedirs(local_dir, exist_ok=True)
    url = _cloud_url()
    _enforce_url_scheme(url)
    with urllib.request.urlopen(url + "/pubkey", timeout=5) as r:
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


def _legacy_token_window_active(now: int | None = None) -> bool:
    """True iff `MAGI_CP_ACCEPT_LEGACY_TOKEN_SHAPE_UNTIL` is set and the
    deadline hasn't passed.

    The env var holds a unix epoch (seconds). An unset / blank / malformed
    value → False (default-OFF fail-closed). Past-deadline → False (the
    window naturally expires; an operator who forgets to remove the env
    after the deploy still gets the canonical behaviour). This bounds the
    blast radius of a transitional bypass to the operator's chosen window.
    """
    raw = os.environ.get("MAGI_CP_ACCEPT_LEGACY_TOKEN_SHAPE_UNTIL", "")
    if not raw.strip():
        return False
    try:
        deadline = int(raw)
    except ValueError:
        return False
    import time as _time
    return (now if now is not None else int(_time.time())) < deadline


def _token_matches_keys(body: dict, *, subject: str, payload_hash: str) -> bool:
    """Match a token body against the canonical (subject, payload_hash) pair.

    Strict-canonical match is the default. When the legacy-shape window
    is active (see `_legacy_token_window_active`) we additionally accept
    bodies whose pre-PR2 `matter == subject` AND `doc_hash == payload_hash`
    — exactly mirroring how PR2 would have written the same logical token
    under the legacy schema. The legacy alias is OFF when:

      - the env knob is unset or expired (see helper), OR
      - either canonical field is already populated on the body (we never
        let a body that opts into canonical fields fall back to legacy
        comparison — that's the only way an attacker who controls a
        partial mix could otherwise trick a match).
    """
    if (body.get("subject") == subject
            and body.get("payload_hash") == payload_hash):
        return True
    if not _legacy_token_window_active():
        return False
    if body.get("subject") is not None or body.get("payload_hash") is not None:
        # The token opted into canonical fields but mismatched above —
        # do not silently fall back to legacy comparison.
        return False
    return (body.get("matter") == subject
            and body.get("doc_hash") == payload_hash)


def _find_signed_token(wal: Wal, pub: Ed25519PublicKey, *,
                       subject: str, payload_hash: str) -> dict | None:
    """Scan WAL for the *latest* citation_verify token bound to
    (subject, payload_hash) whose signature verifies under `pub` and which
    is not expired.

    PR4: only the canonical token shape is recognised by default. Tokens
    signed by a pre-PR2 cloud (legacy `matter`/`doc_hash` body fields) no
    longer match — operators upgrading past PR4 must roll forward gate +
    cloud together so the WAL flushes to the new shape. The brief PR2
    transition window where both pairs were mirrored on the same token
    is over.

    Transitional escape hatch: setting
    `MAGI_CP_ACCEPT_LEGACY_TOKEN_SHAPE_UNTIL=<unix_ts>` accepts legacy
    `matter`/`doc_hash` bodies until the given epoch. Default-OFF; see
    `_legacy_token_window_active`. TOKEN_TTL_SECONDS=600 still caps
    individual token lifetime regardless of the knob.

    Why latest, not first: a later `verdict=fail|review|deny` token for the
    same key MUST invalidate an earlier `pass` — otherwise a stale success
    could authorize a re-edited document. The latest decision wins.

    Kid-pinning is decided from the NEWEST verifying token (by iat). Each
    token's signature is independently verified inside `verify_token`, and
    `_load_pubkey_for_kid` already fail-closes on a kid-vs-cloud mismatch
    — dropping older-kid siblings is purely a defense-in-depth pin.
    """
    candidates: list[dict] = []
    for entry in wal.entries():
        if entry.get("step") != "citation_verify":
            continue
        body = verify_token(entry.get("token", ""), pub)
        if not body:
            continue
        if not _token_matches_keys(body, subject=subject,
                                   payload_hash=payload_hash):
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
        _deny("invalid MAGI_CP_CLOUD_URL scheme")
    try:
        pub = _load_pubkey()
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as e:
        _deny(f"cloud unreachable ({type(e).__name__})")

    wal = Wal(path=os.path.join(_local_dir(), "wal.jsonl"))
    # Every sentinel must individually validate. Multi-statement commands like
    # `FILE_COURT_A_X; FILE_COURT_B_Y` would otherwise allow Y to ride on X's
    # token (or vice versa).
    #
    # PR4: the sentinel's two captured groups are (subject, payload_hash).
    # For legal-vertical sentinels that's still (matter, doc_id) semantically
    # at the policy level, but the WAL lookup matches only on the canonical
    # token-body fields.
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


def _managed_settings_path() -> str:
    return os.environ.get(
        "MAGI_CP_MANAGED_SETTINGS_PATH",
        os.path.expanduser("~/.claude/managed-settings.json"),
    )


def _endpoint_id() -> str | None:
    """Read MAGI_CP_ENDPOINT_ID from env or ~/.config/magi-cp/env.

    Operators set this once at install time; it's the stable identifier
    the cloud uses to track this gate's attestation. Returns None when
    unset (heartbeat then silently no-ops — the cloud just sees an
    "authored but not confirmed" entry for the policy fleet)."""
    env = os.environ.get("MAGI_CP_ENDPOINT_ID")
    if env:
        return env.strip() or None
    cfg = os.path.expanduser("~/.config/magi-cp/env")
    if not os.path.exists(cfg):
        return None
    try:
        with open(cfg, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == "MAGI_CP_ENDPOINT_ID":
                    return v.strip().strip("\"'") or None
    except OSError:
        return None
    return None


def _managed_settings_digest() -> str | None:
    """Sha256 of the gate's currently-loaded managed-settings.json.

    Returns None when the file is absent (first boot before /compile
    has populated it) — the heartbeat then carries a null digest, which
    is the canonical "endpoint authored but no settings loaded" signal.
    """
    import hashlib as _hashlib
    path = _managed_settings_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            return _hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _heartbeat_pidfile_path() -> str:
    return os.path.join(_local_dir(), "heartbeat.last")


def _read_last_heartbeat_ts() -> int | None:
    """Issue #1 P1 (#19): record last successful heartbeat ts so the
    helper can debounce frequent invocations without leaning on cron
    granularity."""
    path = _heartbeat_pidfile_path()
    if not os.path.exists(path):
        return None
    try:
        return int(open(path, "r", encoding="utf-8").read().strip())
    except (OSError, ValueError):
        return None


def _write_last_heartbeat_ts(ts: int) -> None:
    path = _heartbeat_pidfile_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Atomic-ish write; we don't care about racing same-PID
        # invocations because each carries its own monotonically
        # advancing ts.
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(ts))
    except OSError:
        # Disk full / permission — silently skip; the next successful
        # write replaces the file.
        pass


def _heartbeat_min_interval_s() -> int:
    """Operator-tunable debounce. Default 60s — cheap enough that a
    cron+launchd race won't hammer the cloud, generous enough that a
    full restart cycle still beats within the window."""
    raw = os.environ.get("MAGI_CP_HEARTBEAT_MIN_INTERVAL", "")
    if not raw.strip():
        return 60
    try:
        v = int(raw)
        return max(0, v)
    except ValueError:
        return 60


def post_heartbeat(*, api_key: str | None = None,
                    endpoint_id: str | None = None,
                    cloud_url: str | None = None,
                    timeout: float = 5.0,
                    force: bool = False) -> dict | None:
    """Best-effort heartbeat POST. Returns the response dict on 200,
    `None` on any error. Caller (cron / startup hook) ignores failures
    — a missed heartbeat is the cloud's signal, not a gate-side error.

    Auth: `api_key` falls back to `MAGI_CP_API_KEY` env. When neither
    is present we still return None (gate isn't tenant-enrolled yet).
    `endpoint_id` falls back to `_endpoint_id()` (env / config file).

    Issue #1 P1 (#6, #19):
      - URL scheme is enforced (`_enforce_url_scheme`) — plain HTTP
        to non-loopback hosts is rejected unless the operator opts
        in via `MAGI_CP_ALLOW_PLAIN_HTTP=1`.
      - Min-interval guard: skips when the previous successful
        heartbeat was less than `MAGI_CP_HEARTBEAT_MIN_INTERVAL`
        seconds ago. `force=True` bypasses the guard (used by the
        startup hook).
      - Replay-resistant payload: includes a fresh nonce + `ts` so
        the cloud's window check / nonce-dedup is meaningful.
    """
    eid = endpoint_id or _endpoint_id()
    if not eid:
        return None
    key = api_key or os.environ.get("MAGI_CP_API_KEY")
    if not key:
        return None
    # Min-interval debounce (#19).
    import time as _time
    now = int(_time.time())
    if not force:
        last = _read_last_heartbeat_ts()
        if last is not None and (now - last) < _heartbeat_min_interval_s():
            return None
    digest = _managed_settings_digest()
    body: dict = {"endpoint_id": eid, "ts": now}
    # Fresh nonce per call so the cloud-side replay check can dedupe
    # silently-replayed payloads.
    import secrets as _secrets
    body["nonce"] = _secrets.token_hex(16)
    if digest is not None:
        body["active_policy_digest"] = digest
    av = os.environ.get("MAGI_CP_AGENT_VERSION")
    if av:
        body["agent_version"] = av
    label = os.environ.get("MAGI_CP_ENDPOINT_LABEL")
    if label:
        body["label"] = label
    base_url = cloud_url or _cloud_url()
    try:
        _enforce_url_scheme(base_url)
    except ValueError:
        return None
    url = base_url + f"/endpoints/{eid}/heartbeat"
    req = urllib.request.Request(
        url, method="POST",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Api-Key": key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            result = json.loads(r.read())
            _write_last_heartbeat_ts(now)
            return result
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None


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


def _context_templates_dir() -> str:
    """Resolve the sidecar directory where the compiler writes
    `<sha256>.txt` template bytes.

    The compiler writes them next to managed-settings.json under
    `context-templates/`. Operators with a custom managed-settings
    layout override the dir explicitly via
    `MAGI_CP_CONTEXT_TEMPLATES_DIR`.

    Compile-to-stage-then-move trap: `compile_files(policy_paths, "/tmp/m.json")`
    writes sidecars to `/tmp/context-templates/<sha>.txt`. When the
    operator later moves `m.json` to `~/.claude/managed-settings.json`
    without also moving the sidecar dir, this resolver falls back to
    `~/.claude/context-templates` and the shim's read fails silently
    (FileNotFoundError → return 0 with empty stdout, CC continues
    with no `additionalContext`). Two safe install patterns:
    (a) compile straight to the install target (`compile_files(paths,
    "~/.claude/managed-settings.json")` — sidecars land in the right
    place by default); (b) set `MAGI_CP_CONTEXT_TEMPLATES_DIR` on the
    runtime to wherever the sidecars actually live.
    """
    env = os.environ.get("MAGI_CP_CONTEXT_TEMPLATES_DIR")
    if env:
        return env
    return os.path.join(
        os.path.dirname(_managed_settings_path()),
        "context-templates",
    )


# Sha256 hex chars only — refuses path-traversal / ELOOP attempts on the
# `--id` argument fed in by the compiler-emitted command line. The
# compiler always passes a 64-hex sha; anything else means a tampered
# managed-settings.json.
_CONTEXT_ID_RE = re.compile(r"^[A-Fa-f0-9]{64}$")
# Shape gate for the `--event` arg. Matches the CC event-name grammar
# (PascalCase identifier-shaped, ≤64 chars). The shape pass is
# necessary but NOT sufficient — `context_write_cli` also cross-checks
# against `_SUPPORTED_EVENTS` so a well-formed name CC won't recognize
# (e.g. "NotARealHook") fails silently instead of emitting a hookSpecificOutput
# JSON keyed on a hook event CC will then drop or refuse. Old name was
# `_CONTEXT_EVENT_HEX_RE`, which was a misnomer (the regex matches
# event-name shapes, not hex).
_CONTEXT_EVENT_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


def _emit_additional_context(event: str, template: str) -> None:
    """Print the CC-canonical hookSpecificOutput JSON carrying the
    template under `additionalContext` and exit 0.

    D57f-1: keyed on the actual `hookEventName` — CC's hook reader
    dispatches the additionalContext field per-event. UserPromptSubmit
    splices it into the next user prompt; SessionStart stitches it
    into the boot context; every other event records it on the
    downstream consumer the runtime exposes for that event.

    Single emission path so each event kind produces byte-identical
    JSON (modulo the event name + template). The compiler-emitted
    command line is the only caller, so we don't need to deal with
    multi-line splits.
    """
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": template,
        }
    }, ensure_ascii=False))
    sys.exit(0)


def context_write_cli() -> int:
    """`magi-cp-context-write` entry point.

    D57f-1: the compiler emits a hook entry of the shape
    `magi-cp-context-write --event <Event> --id <sha256>` for every
    ContextInjectionPolicy. The shim resolves the sha back into the
    template bytes from the sidecar directory and emits the
    additionalContext JSON keyed on the event.

    Failure modes (all exit-0 + empty stdout, so a missing template
    cannot brick CC: empty JSON output means CC continues with no
    injected context):
      - sidecar dir missing
      - sidecar file missing
      - sidecar file empty / unreadable
      - sidecar file is world/group writable (refused)
      - malformed CLI args
      - `--event` value outside `_SUPPORTED_EVENTS`
        (well-formed-but-unknown event names exit silently rather than
        emit a `hookEventName` CC will drop or refuse)

    Args are parsed without argparse so a single missing flag does
    not raise SystemExit-2 (which CC would surface as a hook error).
    """
    argv = sys.argv[1:]
    event = ""
    tpl_id = ""
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--event" and i + 1 < len(argv):
            event = argv[i + 1]
            i += 2
            continue
        if arg == "--id" and i + 1 < len(argv):
            tpl_id = argv[i + 1]
            i += 2
            continue
        i += 1
    if not event or not _CONTEXT_EVENT_NAME_RE.match(event):
        return 0
    # P1 follow-up: the shape regex was the only event-name gate; a
    # well-formed-but-unsupported name (e.g. "NotARealHook") would
    # still emit a `{hookSpecificOutput: {hookEventName: ..., ...}}`
    # JSON that CC then either silently drops (no enforcement; operator
    # sees a green check) or refuses at settings load (cascading
    # fail-open across every policy in the bundle). Cross-check the
    # IR's canonical `_SUPPORTED_EVENTS` so unknown names exit silently
    # (matches the existing fail-open-on-absence contract on missing
    # sidecars and malformed args).
    from ..policy.ir import _SUPPORTED_EVENTS
    if event not in _SUPPORTED_EVENTS:
        return 0
    if not tpl_id or not _CONTEXT_ID_RE.match(tpl_id):
        return 0
    side_dir = _context_templates_dir()
    path = os.path.join(side_dir, f"{tpl_id}.txt")
    try:
        # O_NOFOLLOW so a swapped symlink in the sidecar dir cannot
        # redirect us to a different file. The compiler writes the
        # sidecar files itself; a swap is the only way a different
        # template lands on disk.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except (FileNotFoundError, OSError):
        return 0
    # P2 follow-up: refuse world/group-writable template files. The
    # sidecar's role is "text the model sees" — if anyone other than
    # the operator can rewrite the file, an attacker can inject
    # arbitrary additionalContext into the model's view. Mirrors the
    # `_read_cached` pubkey hygiene we already apply to MAGI_CP_LOCAL_DIR
    # so context templates get the same handling as the heartbeat
    # cache. The compiler writes with the process umask which on a
    # typical operator workstation gives 0o644; reject 0o66x / 0o67x /
    # any world-writable mode regardless.
    try:
        st = os.fstat(fd)
        if st.st_mode & 0o022:
            os.close(fd)
            return 0
    except OSError:
        os.close(fd)
        return 0
    try:
        data = b""
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            data += chunk
    finally:
        os.close(fd)
    if not data:
        return 0
    try:
        template = data.decode("utf-8")
    except UnicodeDecodeError:
        return 0
    _emit_additional_context(event, template)
    return 0   # unreachable; _emit_additional_context exits


def heartbeat_cli() -> int:  # pragma: no cover (CLI entry)
    """Standalone heartbeat poster. Wire into cron / launchd /
    systemd-timer to fire every ~5 minutes. Silent on failure (exit 0
    either way) so a transient cloud blip doesn't page the operator
    via cron error mail."""
    post_heartbeat()
    return 0


# D57f-2 — input-rewrite shim. Compiler emits a PreToolUse hook of shape
# `magi-cp-input-rewrite --policy <id>`. The shim reads the standard
# PreToolUse JSON on stdin, POSTs (policy_id, tool_input) to the cloud,
# and prints whatever updatedInput the cloud returns.
#
# Authoring contract recap (see InputRewritePolicy + rewriters.py):
#   - The shim does NOT interpret a rewriter spec itself. The cloud is
#     the rewriter; the shim is the courier. A compromised endpoint
#     cannot mint a novel rewrite operation locally.
#   - All failure modes degrade to "exit 0, empty stdout" so a missing
#     cloud / malformed reply / oversize payload becomes a transparent
#     no-op (CC runs the tool with the original input). Fail-closed on
#     the rewrite path would block the tool over a config blip — the
#     EvidencePolicy lane is the right surface to refuse.
_INPUT_REWRITE_POLICY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,127}$")


def _emit_updated_input(updated_input: dict) -> None:
    """Print the CC-canonical hookSpecificOutput JSON carrying the
    rewritten tool_input under `updatedInput` and exit 0.

    Single emission path so every PreToolUse rewrite produces
    byte-identical JSON (modulo the dict body). CC consumes the
    `updatedInput` field on PreToolUse only — the matrix + policy
    validator already pin event=PreToolUse.

    P1 follow-up: emit `permissionDecision: "allow"` alongside
    `updatedInput`. The doc'd CC contract is
    `{decision, updatedInput, additionalContext, continue}` (see
    docs/architecture/claude-code-cli/08-coding-harness-internals.md);
    a hookSpecificOutput WITHOUT a permission stance is version-
    dependent — some builds parse the `updatedInput` but leave the
    permission flow to a downstream hook, others ignore the field
    entirely. Pairing the rewrite with an explicit `allow` makes the
    intent unambiguous across CC builds: "apply the rewrite and
    approve this tool call". Downstream EvidencePolicy gates remain
    the place to deny — they fire on their own hook entry and their
    `deny` overrides this `allow`.
    """
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated_input,
        }
    }, ensure_ascii=False))
    sys.exit(0)


def input_rewrite_cli() -> int:
    """`magi-cp-input-rewrite` entry point.

    Reads the PreToolUse hook JSON on stdin, asks the cloud for a
    rewrite verdict against the policy named on `--policy`, and emits
    the updatedInput JSON when the cloud returns a changed dict.

    Failure modes (every one exits 0 with empty stdout):
      - missing / malformed `--policy <id>` argv
      - missing / unparseable stdin payload
      - missing tool_input dict
      - cloud unreachable / non-200 / malformed reply
      - cloud returned `updated_input` identical to the original (no-op)
      - cloud returned a non-dict updated_input (refuse silently)
    """
    argv = sys.argv[1:]
    policy_id = ""
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--policy" and i + 1 < len(argv):
            policy_id = argv[i + 1]
            i += 2
            continue
        i += 1
    if not policy_id or not _INPUT_REWRITE_POLICY_ID_RE.match(policy_id):
        return 0

    raw = sys.stdin.read()
    if not raw or len(raw) > 256_000:
        # Outsized payloads are not the rewrite path's concern.
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        # P2 follow-up: surface an unfamiliar payload shape to stderr so
        # the operator sees the rewriter going silent during early rollout.
        # The matrix + policy validator only enable input_rewrite on
        # PreToolUse, where CC's real payload includes `tool_name` (snake
        # case in the bundled coreTypes.ts; the docstring at the top of
        # this module pins Claude Code 2.1.170). A missing key here means
        # either CC changed its payload shape on this version or a
        # downstream caller fed the shim a non-PreToolUse JSON. We exit 0
        # so the tool call still proceeds (fail-soft per the rewriter
        # contract), but the stderr line is the only signal the operator
        # has that the rewrite never fired.
        sys.stderr.write(
            "magi-cp-input-rewrite: payload missing `tool_name`; "
            "rewrite skipped (no-op). PreToolUse payload shape may have "
            "changed; verify against the CC version in use.\n"
        )
        return 0

    cloud = _cloud_url()
    try:
        _enforce_url_scheme(cloud)
    except ValueError:
        return 0
    body = json.dumps({
        "policy_id": policy_id,
        "tool_name": tool_name,
        "tool_input": tool_input,
    }, ensure_ascii=False).encode("utf-8")
    # P1 follow-up: forward MAGI_CP_API_KEY (the same env the heartbeat
    # path uses at gate.py:462) as `X-Api-Key`. The cloud-side route
    # accepts the call without a header for backwards compatibility
    # (the local-gate loopback dev loop has no tenant credential by
    # default), but when an operator HAS set the env we want to bind
    # this remote rewrite verdict to the gate's identity so a third
    # party can't poll the endpoint to enumerate policy ids / probe
    # rewriter behaviour. The shim doesn't fail if the key is unset;
    # the cloud-side dependency does the matching enforcement decision.
    req_headers = {"Content-Type": "application/json"}
    forwarded_key = os.environ.get("MAGI_CP_API_KEY")
    if forwarded_key:
        req_headers["X-Api-Key"] = forwarded_key
    req = urllib.request.Request(
        cloud + "/policies/input_rewrite",
        method="POST",
        data=body,
        headers=req_headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            reply = json.loads(r.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return 0
    if not isinstance(reply, dict):
        return 0
    if not reply.get("rewrote"):
        return 0
    new_input = reply.get("updated_input")
    if not isinstance(new_input, dict):
        return 0
    if new_input == tool_input:
        return 0
    _emit_updated_input(new_input)
    return 0   # unreachable; _emit_updated_input exits


# D63 — run_command shim. The compiler emits a hook entry of shape
# `magi-cp-run-command --policy <id>`. The shim asks the cloud which
# policy fires, executes the inline command (or attached script) under
# the named runtime with a wall-clock timeout, captures stdout/stderr,
# writes a ledger row, and prints whatever the command emitted to CC
# (subject to the canonical hookSpecificOutput shape).
#
# Self-host model: the command runs as the magi-cp process. The brief
# explicitly accepts this — D63 ships under MAGI_CP_ALLOW_RUN_COMMAND=1
# on the docker compose default, =0 on the hosted image. Equivalent to
# CC's own `{type: "command"}` hook entries: the operator owns the
# machine and the script body.
_RUN_COMMAND_POLICY_ID_RE = _INPUT_REWRITE_POLICY_ID_RE
_RUN_COMMAND_MAX_STDOUT = 64 * 1024
_RUN_COMMAND_MAX_STDERR = 16 * 1024
_RUN_COMMAND_TRUNCATED_TAG = "...[truncated]"


def _ledger_path() -> str:
    """Append-only JSONL ledger the run_command shim writes execution
    receipts to. Operators can tail this file for an audit trail of
    every shell command the gate spawned.
    """
    return os.environ.get(
        "MAGI_CP_RUN_COMMAND_LEDGER",
        os.path.join(_local_dir(), "run_command_ledger.jsonl"),
    )


def _ledger_append(row: dict) -> None:
    """Best-effort JSONL append. Silent on disk-full / permission errors
    so a logging gap never blocks the gate's response to CC."""
    path = _ledger_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _truncate(text: str, cap: int) -> tuple[str, bool]:
    """Cap a string at `cap` UTF-8 chars. Returns (trimmed, truncated?)."""
    if len(text) <= cap:
        return text, False
    return text[: cap - len(_RUN_COMMAND_TRUNCATED_TAG)] + _RUN_COMMAND_TRUNCATED_TAG, True


def execute_run_command(
    *,
    policy_id: str,
    runtime: str,
    command: str = "",
    script_path: str = "",
    args: list[str] | None = None,
    timeout_ms: int = 5_000,
    fail_closed: bool = False,
    working_dir: str | None = None,
) -> dict:
    """Run an inline command or attached script under a runtime.

    Returns the dict that should be emitted on the CC hook stdout
    channel — either the JSON the command itself printed, or the
    canonical allow/deny shape on a soft failure / fail-closed lane.

    Soft failures (default = allow, ledger records the reason):
      - non-zero exit + fail_closed=False
      - stdout parse failure (not valid JSON)
      - timeout (the partial stdout is parsed if possible)

    Fail-closed lane: when `fail_closed=True`, non-zero exit and
    timeouts both emit a deny shape with permissionDecisionReason
    pointing at the run_command policy.
    """
    import subprocess as _sp
    import time as _time
    args = list(args or [])
    if (bool(command) == bool(script_path)):
        # Defensive: validator already enforces this, but the gate is
        # the trust boundary for any policy that slipped past authoring.
        _ledger_append({
            "ts": int(_time.time()),
            "policy_id": policy_id,
            "kind": "run_command_execution",
            "exit_code": None,
            "duration_ms": 0,
            "stdout": "",
            "stderr_summary": (
                "config error: exactly one of command / script_path must be set"
            ),
            "error": "config",
        })
        if fail_closed:
            return _deny_dict(
                f"run_command policy '{policy_id}': config error"
            )
        return _allow_dict()

    if command:
        if runtime == "bash":
            argv = ["bash", "-c", command, "magi-cp-run-command", *args]
        elif runtime == "python3":
            argv = ["python3", "-c", command, "magi-cp-run-command", *args]
        elif runtime == "node":
            argv = ["node", "-e", command, "magi-cp-run-command", *args]
        else:
            _ledger_append({
                "ts": int(_time.time()),
                "policy_id": policy_id,
                "kind": "run_command_execution",
                "exit_code": None,
                "duration_ms": 0,
                "stdout": "",
                "stderr_summary": f"unknown runtime {runtime!r}",
                "error": "runtime",
            })
            if fail_closed:
                return _deny_dict(
                    f"run_command policy '{policy_id}': unknown runtime"
                )
            return _allow_dict()
    else:
        # Attached script: runtime + path + args.
        argv = [runtime, script_path, *args]

    started = _time.monotonic()
    timeout_s = max(0.1, min(30.0, timeout_ms / 1000.0))
    truncated_stdout = False
    truncated_stderr = False
    proc_stdout = ""
    proc_stderr = ""
    exit_code: int | None = None
    timed_out = False
    error: str | None = None
    try:
        proc = _sp.run(
            argv,
            cwd=working_dir,
            input="",
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        proc_stdout = proc.stdout or ""
        proc_stderr = proc.stderr or ""
        exit_code = proc.returncode
    except _sp.TimeoutExpired as e:
        timed_out = True
        proc_stdout = (e.stdout.decode("utf-8", errors="replace")
                       if isinstance(e.stdout, (bytes, bytearray)) else (e.stdout or ""))
        proc_stderr = (e.stderr.decode("utf-8", errors="replace")
                       if isinstance(e.stderr, (bytes, bytearray)) else (e.stderr or ""))
        exit_code = None
        error = "timeout"
    except (FileNotFoundError, OSError) as e:
        error = f"spawn:{type(e).__name__}"

    duration_ms = int((_time.monotonic() - started) * 1000)
    proc_stdout, truncated_stdout = _truncate(proc_stdout, _RUN_COMMAND_MAX_STDOUT)
    proc_stderr, truncated_stderr = _truncate(proc_stderr, _RUN_COMMAND_MAX_STDERR)

    parsed: dict | None = None
    parse_error: str | None = None
    if proc_stdout:
        try:
            decoded = json.loads(proc_stdout)
            if isinstance(decoded, dict):
                parsed = decoded
            else:
                parse_error = "stdout is not a JSON object"
        except json.JSONDecodeError as e:
            parse_error = f"stdout JSON parse: {e}"

    _ledger_append({
        "ts": int(__import__("time").time()),
        "policy_id": policy_id,
        "kind": "run_command_execution",
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "stdout": proc_stdout,
        "stdout_truncated": truncated_stdout,
        "stderr_summary": proc_stderr,
        "stderr_truncated": truncated_stderr,
        "timed_out": timed_out,
        "parse_error": parse_error,
        "error": error,
    })

    # Decide the return shape.
    if error == "timeout":
        if fail_closed:
            return _deny_dict(
                f"run_command policy '{policy_id}': timeout after "
                f"{timeout_ms}ms"
            )
        if parsed is not None:
            return parsed
        return _allow_dict()
    if error is not None:
        if fail_closed:
            return _deny_dict(
                f"run_command policy '{policy_id}': {error}"
            )
        return _allow_dict()
    if exit_code is not None and exit_code != 0:
        if fail_closed:
            return _deny_dict(
                f"run_command policy '{policy_id}': non-zero exit "
                f"({exit_code})"
            )
        # Audit + continue.
        if parsed is not None:
            return parsed
        return _allow_dict()
    if parsed is not None:
        return parsed
    # Empty / unparseable stdout on a 0 exit → allow (the command
    # ran cleanly, just had nothing to say). Brief: "On stdout parse
    # failure, default to {decision: 'allow'} but log the parse error
    # to ledger" — the parse_error already landed above.
    return _allow_dict()


def _allow_dict() -> dict:
    return {
        "hookSpecificOutput": {
            "permissionDecision": "allow",
        }
    }


def _deny_dict(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "permissionDecision": "deny",
            "permissionDecisionReason": f"MAGI: {reason}",
        }
    }


def run_command_cli() -> int:
    """`magi-cp-run-command` entry point.

    Reads the CC hook payload on stdin, asks the cloud for the
    resolved RunCommandPolicy spec, executes it, and prints whatever
    the command emitted as the hookSpecificOutput JSON.

    Failure modes (every one exits 0; the gate is fail-soft):
      - missing / malformed `--policy <id>` argv
      - missing / unparseable stdin payload
      - cloud unreachable / non-200 / unknown policy id
      - subprocess spawn error
      - (under fail_closed=True) timeout / non-zero exit → deny JSON
    """
    argv = sys.argv[1:]
    policy_id = ""
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--policy" and i + 1 < len(argv):
            policy_id = argv[i + 1]
            i += 2
            continue
        i += 1
    if not policy_id or not _RUN_COMMAND_POLICY_ID_RE.match(policy_id):
        return 0

    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    if len(raw) > 256_000:
        return 0
    payload: dict | None = None
    if raw:
        try:
            decoded = json.loads(raw)
            if isinstance(decoded, dict):
                payload = decoded
        except json.JSONDecodeError:
            payload = None

    cloud = _cloud_url()
    try:
        _enforce_url_scheme(cloud)
    except ValueError:
        return 0

    body = json.dumps({
        "policy_id": policy_id,
        "payload": payload or {},
    }, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    forwarded_key = os.environ.get("MAGI_CP_API_KEY")
    if forwarded_key:
        headers["X-Api-Key"] = forwarded_key
    req = urllib.request.Request(
        cloud + "/policies/run_command",
        method="POST",
        data=body,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            reply = json.loads(r.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return 0
    if not isinstance(reply, dict) or not reply.get("matched"):
        return 0
    spec = reply.get("spec") or {}
    if not isinstance(spec, dict):
        return 0

    out = execute_run_command(
        policy_id=policy_id,
        runtime=str(spec.get("runtime", "bash")),
        command=str(spec.get("command", "")),
        script_path=str(spec.get("script_path", "")),
        args=list(spec.get("args", []) or []),
        timeout_ms=int(spec.get("timeout_ms", 5_000)),
        fail_closed=bool(spec.get("fail_closed", False)),
        working_dir=spec.get("working_dir"),
    )
    if out:
        print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(cli())
