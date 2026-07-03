"""Project (cwd) scoping for the session-evidence hooks.

A policy authored with a project scope should only apply to Claude Code sessions
whose working directory is inside that project. The hooks receive ``cwd`` on
stdin; this helper decides whether a policy with ``--cwd-prefix P`` applies to a
given event cwd.

Empty prefix = global (applies everywhere), preserving the pre-scope behavior.
"""
from __future__ import annotations

import os

__all__ = ["cwd_in_scope"]


def _norm(path: str) -> str:
    # Expand ~ and resolve symlinks / .. so /tmp vs /private/tmp (macOS) and
    # trailing slashes compare equal. realpath is pure-ish (no network).
    return os.path.realpath(os.path.expanduser(path or ""))


def cwd_in_scope(cwd: str, prefix: str) -> bool:
    """True if ``cwd`` is the scope dir or a descendant of it.

    Empty ``prefix`` -> always True (global policy). Uses a path-boundary check
    (not a raw string prefix) so ``/a/project`` does not match ``/a/project-x``.
    """
    if not prefix:
        return True
    base = _norm(prefix)
    here = _norm(cwd)
    if not here:
        return False
    if here == base:
        return True
    return here.startswith(base + os.sep)
