"""magi-cp CLI — dispatcher to subcommands.

Subcommands:
  gate           run the PreToolUse gate (reads hook JSON on stdin)
  emit           request a citation_verify token from the cloud
  await-approval poll /hitl until a review item is approved, then write the
                 signed token to local WAL (closes the money-demo loop)
  compile        Policy IR → managed-settings.json
  cloud          run the FastAPI cloud server (dev shortcut)
  mcp            run the stdio MCP server
  keys           rotate / list / revoke Ed25519 signing keys (W7b)
  share          turn a Claude Code run into a public share link
"""
from __future__ import annotations
import sys


def _help(*, explicit: bool) -> int:
    """Print usage. Exit code: 0 when user asked for it, 2 on bad/missing args."""
    print(__doc__, file=sys.stderr)
    return 0 if explicit else 2


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        return _help(explicit=True)
    if not argv:
        return _help(explicit=False)
    cmd, rest = argv[0], argv[1:]
    if cmd == "gate":
        from ..local.gate import cli as gate_cli
        sys.argv = ["magi-cp-gate", *rest]
        return gate_cli()
    if cmd == "emit":
        from ..local.emit import cli as emit_cli
        sys.argv = ["magi-cp-emit", *rest]
        return emit_cli()
    if cmd == "await-approval":
        from ..local.emit import await_approval_cli
        sys.argv = ["magi-cp-await-approval", *rest]
        return await_approval_cli()
    if cmd == "compile":
        from ..policy.compiler import main as compile_main
        sys.argv = ["magi-cp-compile", *rest]
        return compile_main()
    if cmd == "cloud":
        from ..cloud.app import run as cloud_run
        cloud_run()
        return 0
    if cmd == "mcp":
        from ..mcp.server import main as mcp_main
        return mcp_main()
    if cmd == "keys":
        from .keys import cli as keys_cli
        return keys_cli(rest)
    if cmd == "share":
        from .share import cli as share_cli
        return share_cli(rest)
    print(f"unknown subcommand: {cmd!r}", file=sys.stderr)
    return _help(explicit=False)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
