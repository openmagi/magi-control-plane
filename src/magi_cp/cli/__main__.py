"""magi-cp CLI — dispatcher to subcommands.

Subcommands:
  gate           run the PreToolUse gate (reads hook JSON on stdin)
  emit           request a citation_verify token from the cloud
  await-approval poll /hitl until a review item is approved, then write the
                 signed token to local WAL (closes the money-demo loop)
  compile        Policy IR → managed-settings.json
  cloud          run the FastAPI cloud server (dev shortcut)
  mcp            run the stdio MCP server
"""
from __future__ import annotations
import sys


def _help() -> int:
    print(__doc__, file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        return _help()
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
    print(f"unknown subcommand: {cmd!r}", file=sys.stderr)
    return _help()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
