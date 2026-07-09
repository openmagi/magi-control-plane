"""magi-cp runtime drivers.

The ``HookRuntime`` trait (``trait.py``) is the seam that decouples what a
coding-agent runtime speaks on stdin from what Magi enforces. Three drivers
implement it today: ``cc.py`` (Claude Code, the reference runtime),
``codex.py`` (Codex CLI, behind ``MAGI_CP_CODEX_RUNTIME_ENABLED``),
``hermes.py`` (Hermes CLI, behind ``MAGI_CP_HERMES_RUNTIME_ENABLED``), and
``gjc.py`` (Gajae-Code, behind ``MAGI_CP_GJC_RUNTIME_ENABLED``).

Design briefs:
  2026-06-30-codex-runtime-adapter-design (Codex driver)
  2026-07-06-magi-cp-hermes-runtime-adapter-design (Hermes driver)
  2026-07-08-magi-cp-gajae-code-runtime-adapter-design (gjc driver, §4.6)
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

    Accepts both short dispatcher tokens and canonical ``runtime_id`` values:
      - ``"cc"`` / ``"claude-code"`` / ``"claude_code"`` / ``"claudecode"``
        -> ``CCDriver``
      - ``"codex"`` -> ``CodexDriver``
      - ``"hermes"`` -> ``HermesDriver``
      - ``"gjc"`` / ``"gajae-code"`` / ``"gajae_code"`` -> ``GjcDriver``

    Imports are lazy so the CC hot path never touches the Codex, Hermes,
    or gjc modules.
    """
    key = (runtime_id or "").strip().lower()
    if key in ("cc", "claude-code", "claude_code", "claudecode"):
        from .cc import CCDriver
        return CCDriver()
    if key == "codex":
        from .codex import CodexDriver
        return CodexDriver()
    if key == "hermes":
        from .hermes import HermesDriver
        return HermesDriver()
    if key in ("gjc", "gajae-code", "gajae_code"):
        from .gjc import GjcDriver
        return GjcDriver()
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
