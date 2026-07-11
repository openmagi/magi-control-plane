"""Deterministic Policy IR -> gjc plugin-bundle emitter.

Sibling of ``codex_toml_emitter.py`` (the Codex CLI emitter). The gjc
bundle is the fourth runtime managed-config artifact; it ships a manifest
(``gajae-plugin.json``) plus the three frozen TypeScript shim modules and
a documentation sidecar.

Guarantees (mirror ``compile_to_codex_requirements``):
  - Pure function: no clock, no randomness, no env reads.
  - Byte-stable: same IR list -> byte-identical manifest; a reordered IR
    list -> same manifest.  The shim bytes are VENDORED and never change
    when policies change — the manifest sha256 values are stable because
    the shim bytes are stable.

Design brief: 2026-07-08-magi-cp-gajae-code-runtime-adapter-design
Section 6.1 (bundle file set), Section 5 (shim contract), Section 4.4
(_GJC_TO_CC_TOOL sidecar).
"""
from __future__ import annotations

import hashlib
import json
import pathlib

from ..runtime.trait import ManagedConfigBundle

# ── Locate vendored shim assets ───────────────────────────────────────
#
# The shim files live next to the driver, in ``runtime/gjc_assets/``.
# They are READ ONCE at module-import time and cached; the emitter is a
# pure function over the IR and these cached bytes.

_ASSETS_DIR = pathlib.Path(__file__).parent.parent / "runtime" / "gjc_assets"

_SHIM_TOOL_CALL_KEY = "hooks/magi-gate-tool-call.ts"
_SHIM_SESSION_START_KEY = "hooks/magi-gate-session-start.ts"
_SHIM_SESSION_SHUTDOWN_KEY = "hooks/magi-gate-session-shutdown.ts"

_SHIM_TOOL_CALL_TEXT: str = (_ASSETS_DIR / "magi-gate-tool-call.ts").read_text("utf-8")
_SHIM_SESSION_START_TEXT: str = (_ASSETS_DIR / "magi-gate-session-start.ts").read_text("utf-8")
_SHIM_SESSION_SHUTDOWN_TEXT: str = (_ASSETS_DIR / "magi-gate-session-shutdown.ts").read_text("utf-8")

# Pre-compute sha256 values (stable because the shim sources are frozen)
_SHA256_TOOL_CALL: str = hashlib.sha256(_SHIM_TOOL_CALL_TEXT.encode("utf-8")).hexdigest()
_SHA256_SESSION_START: str = hashlib.sha256(_SHIM_SESSION_START_TEXT.encode("utf-8")).hexdigest()
_SHA256_SESSION_SHUTDOWN: str = hashlib.sha256(_SHIM_SESSION_SHUTDOWN_TEXT.encode("utf-8")).hexdigest()


def _emit_manifest() -> str:
    """Emit the ``gajae-plugin.json`` manifest as a byte-stable JSON string.

    The manifest is independent of the Policy IR: hooks are static (the gate
    decides).  The sha256 values are the stable hashes of the vendored shim
    bytes (pinned at import time).

    **Fanout:** gjc's plugin compiler (compiler.ts:236-246) REJECTS a
    ``tool_call`` hook that lacks both ``target`` AND ``phase``, raising
    ``invalid_hook``.  A single target-less hook would also silently never
    fire for any tool (the runtime bridge matches ``toolName !== target``
    exactly).  We therefore emit ONE entry per tool in
    ``_GJC_BUILTIN_TOOLS`` with ``"phase":"before"``, sorted for byte
    stability.  The ``session_start`` / ``session_shutdown`` hooks remain
    target-less (the compiler allows that for session events).

    Keys are sorted; separators are pinned; newline-terminated.
    """
    # Import locally to mirror the existing _emit_tool_map() pattern and avoid
    # pulling the driver on every gate hot path.
    from ..runtime.gjc import _GJC_BUILTIN_TOOLS  # noqa: PLC0415

    tool_call_hooks = [
        {
            "event": "tool_call",
            "name": f"magi-gate-tool-call-{tool}",
            "path": _SHIM_TOOL_CALL_KEY,
            "phase": "before",
            "sha256": _SHA256_TOOL_CALL,
            "target": tool,
        }
        for tool in sorted(_GJC_BUILTIN_TOOLS)
    ]
    manifest = {
        "description": "Magi Control Plane enforcement gate (frozen dispatcher; policy lives in magi-cp)",
        "hooks": tool_call_hooks + [
            {
                "event": "session_start",
                "name": "magi-gate-session-start",
                "path": _SHIM_SESSION_START_KEY,
                "sha256": _SHA256_SESSION_START,
            },
            {
                "event": "session_shutdown",
                "name": "magi-gate-session-shutdown",
                "path": _SHIM_SESSION_SHUTDOWN_KEY,
                "sha256": _SHA256_SESSION_SHUTDOWN,
            },
        ],
        "name": "magi-cp-gate",
        "version": "1",
    }
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def _emit_tool_map() -> str:
    """Emit the ``magi-cp-tool-map.json`` documentation sidecar.

    Contains the gjc-native -> CC-canonical tool name mapping from
    ``_GJC_TO_CC_TOOL`` (``runtime/gjc.py``). This sidecar is NOT read by
    gjc at runtime; it documents the normalization table for operators and
    is included in the bundle for transparency.

    Byte-stable: keys sorted, separators pinned.
    """
    # Import locally to avoid a circular import (gjc.py imports from trait.py;
    # this module imports from gjc.py via _GJC_TO_CC_TOOL only; keeping the
    # import here avoids pulling the heavy driver on every gate hot path).
    from ..runtime.gjc import _GJC_TO_CC_TOOL  # noqa: PLC0415

    return json.dumps(_GJC_TO_CC_TOOL, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def compile_to_gjc_bundle(ir: list) -> ManagedConfigBundle:
    """Compile a Policy IR list into the gjc plugin-bundle ``ManagedConfigBundle``.

    Pure, byte-stable, order-invariant.  The Policy IR is consulted only
    for the tool-map sidecar (which is static — the normalization table does
    not depend on IR content); the manifest and shim files are always the
    same frozen bytes.

    Returns a ``ManagedConfigBundle`` with five file keys (§6.1):
      - ``gajae-plugin.json``                   — manifest with sha256 hashes
      - ``hooks/magi-gate-tool-call.ts``         — frozen tool_call gate shim
      - ``hooks/magi-gate-session-start.ts``     — session_start observer
      - ``hooks/magi-gate-session-shutdown.ts``  — session_shutdown observer
      - ``magi-cp-tool-map.json``               — normalization table sidecar

    ``context_templates`` is empty in v1 (no gjc-specific template injection).
    """
    return ManagedConfigBundle(
        files={
            "gajae-plugin.json": _emit_manifest(),
            _SHIM_TOOL_CALL_KEY: _SHIM_TOOL_CALL_TEXT,
            _SHIM_SESSION_START_KEY: _SHIM_SESSION_START_TEXT,
            _SHIM_SESSION_SHUTDOWN_KEY: _SHIM_SESSION_SHUTDOWN_TEXT,
            "magi-cp-tool-map.json": _emit_tool_map(),
        },
        context_templates={},
    )


__all__ = ["compile_to_gjc_bundle"]
