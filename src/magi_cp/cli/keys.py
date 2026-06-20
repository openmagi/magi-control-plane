"""`magi-cp keys ...` — rotate / list / revoke signing keys.

Operator workflow:
  1. magi-cp keys rotate              → mints a new active key; old keys
                                         stay around to verify in-flight
                                         tokens.
  2. (wait at least TOKEN_TTL_SECONDS — currently 600s = 10min)
  3. magi-cp keys revoke <old-kid>    → deletes the old keypair entirely.

Daily-cron-friendly: rotate+revoke is idempotent across reruns (revoke is
no-op when kid is missing; rotate always mints fresh).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _store():
    from ..cloud.keys import KeyStore
    return KeyStore(
        dir=os.environ.get(
            "MAGI_CP_KEY_DIR",
            str(Path.home() / ".magi-cp" / "cloud"),
        ),
    )


def _cmd_rotate(_args: argparse.Namespace) -> int:
    ks = _store()
    ks.ensure_keypair()
    old_kid = ks.active_kid()
    new_kid = ks.rotate()
    print(f"rotated: {old_kid} -> {new_kid}")
    print(f"active kid: {new_kid}")
    print("note: keep old kid until tokens it signed have expired "
          "(>= TOKEN_TTL_SECONDS = 600s), then `keys revoke {old_kid}`.")
    return 0


def _cmd_list(_args: argparse.Namespace) -> int:
    ks = _store()
    if not ks.dir.exists():
        print("no keys yet — run `magi-cp keys rotate` (or any cloud command "
              "with MAGI_CP_KEY_DIR set) first")
        return 1
    try:
        active = ks.active_kid()
    except RuntimeError:
        print("no active key (KeyStore dir exists but no ACTIVE marker)")
        return 1
    for kid in ks.list_kids():
        marker = "active" if kid == active else "verifying"
        print(f"{kid}\t{marker}")
    return 0


def _cmd_revoke(args: argparse.Namespace) -> int:
    ks = _store()
    ks.ensure_keypair()
    try:
        ks.revoke(args.kid)
    except ValueError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(f"revoked: {args.kid}")
    return 0


def cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="magi-cp-keys",
        description="Ed25519 signing-key lifecycle: rotate, list, revoke",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("rotate", help="mint a new active key; keep prior keys")
    sub.add_parser("list", help="list all keys with active/verifying status")

    rev = sub.add_parser("revoke", help="delete a non-active key")
    rev.add_argument("kid", help="kid to revoke (must NOT be the active kid)")

    args = p.parse_args(argv)
    if args.cmd == "rotate":
        return _cmd_rotate(args)
    if args.cmd == "list":
        return _cmd_list(args)
    if args.cmd == "revoke":
        return _cmd_revoke(args)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(cli())
