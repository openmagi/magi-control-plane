"""``magi-cp session pack ...`` : session-scoped pack activation CLI (P3).

Design brief: docs/plans/2026-06-30-pack-centric-session-scoped-runtime.md
(§ "Slash-command surface", Phase 3).

This is the operator-facing surface behind the Claude Code slash commands
``/magi:pack-activate``, ``/magi:pack-deactivate`` and
``/magi:pack-status`` (the markdown command files the installer drops
under ``~/.claude/commands/magi/`` just shell out to these subcommands
and relay stdout).

Subcommands
===========
  magi-cp session pack activate <pack_id>
      POST ``/session/{session_id}/packs/activate`` on the cloud, then
      touch the ``(session_id, tenant_id)`` cache-invalidation sentinel
      so the gate serving THIS session drops its cached policy map and
      refetches on the next hook. Prints a human confirmation.

  magi-cp session pack deactivate <pack_id>
      POST ``/session/{session_id}/packs/deactivate`` + touch sentinel.

  magi-cp session pack status
      GET the active-pack envelope + a short "N policies will fire"
      summary derived from ``/session/{session_id}/resolved``.

  magi-cp session pack sticky <pack_id>
      Persist ``pack_id`` under the current project's key in
      ``~/.magi-cp/sticky-packs.json`` so it auto-reactivates on the
      next session boot (decision 3, CC-restart persistence). This is
      a purely local write; no cloud round-trip.

Session id resolution
=====================
Every activate/deactivate/status call needs the CC session id. Order:
  1. ``--session-id`` flag (explicit override).
  2. ``MAGI_CP_SESSION_ID`` env (what a slash-command env passthrough or
     an operator sets).
  3. ``CLAUDE_SESSION_ID`` env (in case a future CC build exports it).
  4. ``~/.magi-cp/state/session.json`` (the last session id the gate
     saw on a hook call (``{"session_id": "..."}``). The gate is the
     writer; this CLI only reads).

Tenant id
=========
The cloud derives the tenant from the api key server-side; the local
cache sentinel is keyed ``(session_id, tenant_id)`` so the CLI must know
the tenant to touch the RIGHT sentinel. In the single-tenant self-host
beta (decision 8) a legacy ``MAGI_CP_API_KEY`` maps to the synthetic
``default`` tenant, so ``--tenant-id`` / ``MAGI_CP_TENANT_ID`` defaults
to ``default``. Multi-tenant installs pass the real tenant id.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from . import session_cache


_DEFAULT_CLOUD_URL = "http://127.0.0.1:8787"


# ── transport ─────────────────────────────────────────────────────────
def _post(cloud_url: str, path: str, api_key: str, body: dict) -> dict:
    req = urllib.request.Request(
        cloud_url + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Api-Key": api_key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _get(cloud_url: str, path: str, api_key: str) -> dict:
    req = urllib.request.Request(
        cloud_url + path,
        headers={"X-Api-Key": api_key},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


# ── session id + project resolution ───────────────────────────────────
def _session_state_file() -> str:
    """Path the gate writes the last-seen session id to. Overridable so
    tests never touch a real ``~/.magi-cp`` tree.

    Delegates to :func:`session_cache.session_state_file_path` so the
    reader (this CLI) and the writer (``gate.persist_session_id``) can
    never resolve to different files.
    """
    return session_cache.session_state_file_path()


def resolve_session_id(explicit: str | None = None) -> str | None:
    """Best-effort current CC session id (see module docstring)."""
    if explicit:
        return explicit
    for var in ("MAGI_CP_SESSION_ID", "CLAUDE_SESSION_ID"):
        val = os.environ.get(var)
        if val:
            return val
    try:
        with open(_session_state_file(), encoding="utf-8") as handle:
            data = json.load(handle)
        sid = data.get("session_id") if isinstance(data, dict) else None
        if isinstance(sid, str) and sid:
            return sid
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return None


_PROJECT_MARKERS = (".git", ".claude")


def resolve_project_key(start: str | None = None) -> str:
    """Return the sticky-pack key for the current project.

    Walk up from ``start`` (default cwd) looking for a project-root
    marker (``.git`` / ``.claude``). The nearest directory that contains
    one is the key. When no marker is found we fall back to the absolute
    cwd (design doc: "default to using the absolute cwd as the key"). The
    gate reads the SAME key via
    ``session_cache.load_sticky_packs_for_project`` so both sides must
    agree; keep this the single source of that convention.
    """
    cur = os.path.abspath(start or os.getcwd())
    while True:
        for marker in _PROJECT_MARKERS:
            if os.path.exists(os.path.join(cur, marker)):
                return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            # reached filesystem root without a marker
            return os.path.abspath(start or os.getcwd())
        cur = parent


# ── command handlers ──────────────────────────────────────────────────
def _require_session(args) -> str | None:
    sid = resolve_session_id(args.session_id)
    if not sid:
        print(
            "error: no session id. Pass --session-id, set "
            "MAGI_CP_SESSION_ID, or run inside a Claude Code session "
            "the gate has already touched.",
            file=sys.stderr,
        )
        return None
    return sid


def _require_api_key(args) -> bool:
    if not args.api_key:
        print(
            "error: --api-key or MAGI_CP_API_KEY required",
            file=sys.stderr,
        )
        return False
    return True


def _http_guard(fn):
    """Run a transport closure, mapping urllib errors to a clean rc=1."""
    try:
        return 0, fn()
    except urllib.error.HTTPError as exc:
        print(f"cloud refused: HTTP {exc.code} {exc.reason}", file=sys.stderr)
        return 1, None
    except urllib.error.URLError as exc:
        print(f"cloud unreachable: {exc.reason}", file=sys.stderr)
        return 1, None


def _cmd_activate(args) -> int:
    sid = _require_session(args)
    if sid is None:
        return 2
    if not _require_api_key(args):
        return 2
    rc, res = _http_guard(
        lambda: _post(
            args.cloud_url, f"/session/{sid}/packs/activate",
            args.api_key, {"pack_id": args.pack_id},
        )
    )
    if rc != 0:
        return rc
    # Force the gate serving THIS session to refetch on its next hook.
    touched = session_cache.touch_invalidation_file(sid, args.tenant_id)
    active = res.get("active_packs", []) if isinstance(res, dict) else []
    print(
        f"activated {args.pack_id} for this session ({sid[:12]}). "
        f"Active packs: {', '.join(active) or '(none)'}."
    )
    print(f"Deactivate with: magi-cp session pack deactivate {args.pack_id}")
    if not touched:
        print(
            "warning: could not touch the cache-invalidation signal; the "
            "gate may keep serving the previous pack set until the "
            "session ends. Check permissions on "
            f"{session_cache._state_dir()}.",
            file=sys.stderr,
        )
    return 0


def _cmd_deactivate(args) -> int:
    sid = _require_session(args)
    if sid is None:
        return 2
    if not _require_api_key(args):
        return 2
    rc, res = _http_guard(
        lambda: _post(
            args.cloud_url, f"/session/{sid}/packs/deactivate",
            args.api_key, {"pack_id": args.pack_id},
        )
    )
    if rc != 0:
        return rc
    touched = session_cache.touch_invalidation_file(sid, args.tenant_id)
    active = res.get("active_packs", []) if isinstance(res, dict) else []
    print(
        f"deactivated {args.pack_id}. Active packs: "
        f"{', '.join(active) or '(none)'}. Floor pack remains always-on."
    )
    if not touched:
        print(
            "warning: could not touch the cache-invalidation signal; the "
            "gate may keep serving the previous pack set until the "
            "session ends.",
            file=sys.stderr,
        )
    return 0


def _count_policies(resolved: dict) -> int:
    """Count DISTINCT policies across every hook coordinate.

    A policy that fires on multiple hooks is one policy, so we dedupe by
    id (falling back to a stable json rendering when a serialized policy
    carries no id field)."""
    seen: set[str] = set()
    by_hook = resolved.get("policies_by_hook") if isinstance(resolved, dict) else None
    if not isinstance(by_hook, list):
        return 0
    for row in by_hook:
        if not isinstance(row, dict):
            continue
        for pol in row.get("policies", []) or []:
            if not isinstance(pol, dict):
                continue
            pid = pol.get("id") or pol.get("policy_id")
            if not isinstance(pid, str) or not pid:
                pid = json.dumps(pol, sort_keys=True, ensure_ascii=False)
            seen.add(pid)
    return len(seen)


def _cmd_status(args) -> int:
    sid = _require_session(args)
    if sid is None:
        return 2
    if not _require_api_key(args):
        return 2
    rc, envelope = _http_guard(
        lambda: _get(args.cloud_url, f"/session/{sid}/packs", args.api_key)
    )
    if rc != 0:
        return rc
    active = envelope.get("active_packs", []) if isinstance(envelope, dict) else []
    floor = envelope.get("floor_pack_id") if isinstance(envelope, dict) else None

    # Policy count is best-effort: an older cloud (pre-P2) or a flag-OFF
    # cloud still answers /resolved, but if anything goes wrong we still
    # render the pack list rather than failing the whole status call.
    n_policies: int | None = None
    try:
        resolved = _get(
            args.cloud_url, f"/session/{sid}/resolved", args.api_key,
        )
        n_policies = _count_policies(resolved)
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError):
        n_policies = None

    print(f"Active packs (session {sid[:12]}):")
    if floor:
        print(f"  - {floor}  (floor, always-on)")
    if active:
        for pid in active:
            print(f"  - {pid}")
    elif not floor:
        print("  (none; only the floor pack fires, if seeded)")
    if n_policies is not None:
        print(f"{n_policies} policies will fire on matching hooks.")
    return 0


def _cmd_sticky(args) -> int:
    project = args.project or resolve_project_key()
    path = session_cache.sticky_packs_file_path()
    data: dict = {}
    try:
        with open(path, encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            data = loaded
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError):
        data = {}
    existing = data.get(project)
    ids = [p for p in existing if isinstance(p, str)] if isinstance(existing, list) else []
    if args.pack_id not in ids:
        ids.append(args.pack_id)
    data[project] = ids
    # Atomic write so a crash mid-write cannot corrupt the sticky file
    # (the gate reads it on every fresh session boot). Hardened like the
    # gate's trust files: dir tree 0700, and the tmp written via os.open
    # with O_NOFOLLOW at 0o600 so a pre-planted symlink cannot redirect
    # the write and the sticky list is not world/group readable.
    session_cache._make_secure_dir(os.path.dirname(path) or ".")
    tmp = f"{path}.tmp.{os.getpid()}"
    fd = os.open(
        tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600,
    )
    try:
        os.write(
            fd,
            json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"),
        )
    finally:
        os.close(fd)
    os.replace(tmp, path)
    print(
        f"sticky: {args.pack_id} will auto-activate for project {project}. "
        f"Now sticky here: {', '.join(ids)}."
    )
    return 0


# ── argument wiring ───────────────────────────────────────────────────
def _add_cloud_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--cloud-url",
        default=os.environ.get("MAGI_CP_CLOUD_URL", _DEFAULT_CLOUD_URL),
    )
    p.add_argument(
        "--api-key", default=os.environ.get("MAGI_CP_API_KEY", ""),
    )
    p.add_argument(
        "--session-id", default=None,
        help="override the resolved CC session id",
    )
    p.add_argument(
        "--tenant-id",
        default=os.environ.get("MAGI_CP_TENANT_ID", "default"),
        help="tenant the local cache sentinel is keyed on (default: default)",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="magi-cp session")
    groups = parser.add_subparsers(dest="group", required=True)

    pack = groups.add_parser("pack", help="session-scoped pack activation")
    actions = pack.add_subparsers(dest="action", required=True)

    p_act = actions.add_parser("activate", help="activate a pack for this session")
    p_act.add_argument("pack_id")
    _add_cloud_opts(p_act)
    p_act.set_defaults(handler=_cmd_activate)

    p_deact = actions.add_parser("deactivate", help="deactivate a pack")
    p_deact.add_argument("pack_id")
    _add_cloud_opts(p_deact)
    p_deact.set_defaults(handler=_cmd_deactivate)

    p_status = actions.add_parser("status", help="show active packs")
    _add_cloud_opts(p_status)
    p_status.set_defaults(handler=_cmd_status)

    p_sticky = actions.add_parser(
        "sticky", help="auto-activate a pack for this project on boot",
    )
    p_sticky.add_argument("pack_id")
    p_sticky.add_argument(
        "--project", default=None,
        help="override the project key (default: nearest .git/.claude root)",
    )
    p_sticky.set_defaults(handler=_cmd_sticky)

    return parser


def cli(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse exits 2 on bad args
        return int(exc.code or 0)
    return args.handler(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(cli())
