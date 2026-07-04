"""magi-cp runtime drivers.

The ``HookRuntime`` trait (``trait.py``) is the seam that decouples what a
coding-agent runtime speaks on stdin from what Magi enforces. Two drivers
implement it today: ``cc.py`` (Claude Code, the reference runtime) and
``codex.py`` (Codex CLI, default-off behind
``MAGI_CP_CODEX_RUNTIME_ENABLED``).

Design brief: 2026-06-30-codex-runtime-adapter-design (private planning repo).
"""
from __future__ import annotations

from .detect import detect_runtime
from .trait import (
    COVERAGE_CELLS,
    CoveragePolicyStatus,
    CoverageReport,
    HookEvent,
    HookRuntime,
    InstallPaths,
    ManagedConfigBundle,
    Verdict,
    coverage_cell,
    rollup_cells,
)


def get_runtime(runtime_id: str) -> HookRuntime:
    """Return the ``HookRuntime`` driver for ``runtime_id``.

    Accepts both the short dispatcher token (``"cc"`` / ``"codex"``) and
    the canonical ``runtime_id`` (``"claude-code"`` / ``"codex"``). The
    Codex driver import is lazy so the CC hot path never touches the
    Codex module.
    """
    key = (runtime_id or "").strip().lower()
    if key in ("cc", "claude-code", "claude_code", "claudecode"):
        from .cc import CCDriver
        return CCDriver()
    if key == "codex":
        from .codex import CodexDriver
        return CodexDriver()
    raise ValueError(f"unknown runtime id: {runtime_id!r}")


__all__ = [
    "HookRuntime",
    "HookEvent",
    "Verdict",
    "CoveragePolicyStatus",
    "CoverageReport",
    "COVERAGE_CELLS",
    "coverage_cell",
    "rollup_cells",
    "ManagedConfigBundle",
    "InstallPaths",
    "detect_runtime",
    "get_runtime",
]
