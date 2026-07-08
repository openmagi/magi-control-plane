"""Gajae-Code (gjc) runtime driver.

Design brief: 2026-07-08-magi-cp-gajae-code-runtime-adapter-design
(Section 4 architecture, §4.3 wire, §4.4 parse, §4.5 emit, §4.6 flags).

gjc becomes the fourth ``HookRuntime`` magi-cp governs (after Claude Code,
Codex, and the designed-but-not-yet-built Hermes driver). The driver is an
adapter-only module: zero policy logic ever lives here; the gate binary stays
the single evaluator, and the frozen TypeScript shim (Section 5) dispatches
to it via ``magi-cp gate --runtime gjc``.

Wire (§4.3 — owned by magi-cp, the shim is ours):
  stdin  <- gjc tool_call JSON envelope containing a ``gjc_event`` key
  stdout -> ``{"block": true, "reason": "MAGI: <reason>"}\\n``  (deny / ask)
             ``b""``                                               (allow)

Locked decisions:
  D2 — unmapped tool names pass through raw (ssh stays ssh).
  D3 — ``ask`` downgrade = deny-with-guidance (not allow).
  D5 — ``MAGI_CP_GJC_RUNTIME_ENABLED`` default-ON with explicit-falsy kill.

``MAGI_CP_GJC_RUNTIME_ENABLED`` is default-ON, so the gjc path is reachable
by default when a runtime signal selects it.  Setting it to an explicit falsy
value forces ``"cc"`` unconditionally (the kill switch).
"""
from __future__ import annotations

import json
import sys

from .trait import (
    HookEvent,
    InstallPaths,
    ManagedConfigBundle,
    Verdict,
)

# Policy IR imports are only needed for coverage_report / emit_managed_config
# (U5, U4 — not in scope for U1/U2 PR-1).  Imported lazily inside those
# methods to avoid pulling the heavy policy graph on the gate hot path.


# ── Tool-name normalization table (§4.4, §2.5 source) ───────────────────────
#
# Maps gjc native tool names (lowercase, from BUILTIN_TOOLS tools/index.ts:383-418)
# to Claude Code canonical tool names used inside the Magi policy IR.
# Authoring-surface rationale: CC tool names are the IR's matcher vocabulary,
# so inbound gjc names are normalized here before reaching decide().
#
# D2 locked: anything NOT in this table passes through raw (unmapped names
# allow + audit; the gate sees every call via the target-less hook).
# The enforcement posture for unmapped names is the coverage layer (U5); here
# we simply do NOT drop or deny them.
_GJC_TO_CC_TOOL: dict[str, str] = {
    # Core file / shell operations
    "bash":      "Bash",
    "read":      "Read",
    "write":     "Write",
    "edit":      "Edit",
    "ast_edit":  "Edit",        # AST-aware edit = same CC archetype
    # Search
    "search":    "Grep",
    "ast_grep":  "Grep",
    "find":      "Glob",
    # Web
    "web_search": "WebSearch",
    # Task management / todos
    "todo_write": "TodoWrite",
    # Subagent spawn (both gjc names -> CC's single Task tool; §4.4)
    "task":      "Task",
    "subagent":  "Task",
}

# ── gjc_event -> canonical hook_event_name mapping (§4.4) ───────────────────
_GJC_EVENT_TO_CANONICAL: dict[str, str] = {
    "tool_call":              "PreToolUse",
    "tool_result":            "PostToolUse",
    "session_start":          "SessionStart",
    "session_shutdown":       "SessionEnd",
    "before_agent_start":     "UserPromptSubmit",    # v1.5
    "session_before_compact": "PreCompact",          # v1.5
    "session_compact":        "PostCompact",         # v1.5
}

# Prefix shared with other drivers (cc_shapes / codex._prefixed).
_PREFIX = "MAGI: "


def _prefixed(reason: str) -> str:
    """Stable ``MAGI: `` provenance marker."""
    return f"{_PREFIX}{reason}"


class GjcDriver:
    """Gajae-Code ``HookRuntime`` implementation.

    Translates between the gjc plugin-bundle wire (§4.3) and the canonical
    ``HookEvent`` / ``Verdict`` shapes the policy gate speaks.  Zero policy
    logic here — drivers are translators, not evaluators.
    """

    runtime_id: str = "gjc"

    # ── stdin -> canonical event ──────────────────────────────────────────────

    def parse_hook_payload(self, raw_stdin: bytes) -> HookEvent:
        """Decode the gjc stdin envelope into a canonical ``HookEvent``.

        The gjc envelope (§4.3) is a JSON object with a ``gjc_event`` key
        that identifies the event type.  Tool names are normalized through
        ``_GJC_TO_CC_TOOL``; unknown names pass through raw (D2).

        A blank stdin decodes to an empty-``raw`` pass-through event (same
        contract as ``run_codex_gate``; the dispatcher handles the blank case
        before calling this, but the driver must be safe either way).

        Unknown ``gjc_event`` values parse with the raw name preserved and are
        never silently dropped (honesty rule H3, §8.2).
        """
        text = raw_stdin.decode("utf-8", errors="replace").strip()
        if not text:
            return HookEvent(hook_event_name="PreToolUse", raw={})
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("gjc hook payload is not a JSON object")
        return self._event_from_dict(payload)

    @staticmethod
    def _event_from_dict(payload: dict) -> HookEvent:
        gjc_event = str(payload.get("gjc_event") or "")
        # Normalize gjc_event -> canonical hook_event_name (§4.4).
        event_name = _GJC_EVENT_TO_CANONICAL.get(gjc_event, gjc_event or "PreToolUse")

        raw_tool_name = str(payload.get("tool_name") or "")
        # D2: unmapped names pass through; mapped names get the CC canonical.
        tool_name = _GJC_TO_CC_TOOL.get(raw_tool_name, raw_tool_name)

        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            tool_input = {}

        return HookEvent(
            hook_event_name=event_name,
            session_id=str(payload.get("session_id") or ""),
            turn_id="",       # gjc has no turn_id on this wire (§4.4)
            cwd=str(payload.get("cwd") or ""),
            tool_name=tool_name,
            tool_input=tool_input,
            tool_response=None,   # tool_result events: extended in v1.5
            model=str(payload.get("model") or ""),
            permission_mode="",
            transcript_path="",
            matcher_aliases=(),   # gjc has no matcher_aliases
            raw=payload,
        )

    # ── canonical verdict -> stdout bytes ─────────────────────────────────────

    def emit_verdict(self, verdict: Verdict) -> bytes:
        """Serialize a canonical ``Verdict`` to gjc wire bytes (§4.5).

        Locked decisions (§11.1 U1(d), §4.5):
          - deny   -> ``{"block": true, "reason": "MAGI: <reason>"}\\n``
          - allow  -> ``b""`` (silent allow; shim returns undefined)
          - ask    -> deny-with-guidance bytes (D3 downgrade, NOT allow)
          - updated_input on allow  -> ``b""`` (no arg-rewrite channel; D2/§4.5)
          - updated_input on deny   -> deny bytes (side channel dropped but
                                       the deny is preserved; H2)

        Non-PreToolUse events are observe-only in v1: returns ``b""`` unless
        ``decision == "deny"`` on a ``PreToolUse`` event.
        """
        decision = verdict.decision

        if decision == "allow":
            # updated_input is silently dropped (no gjc rewrite channel; §4.5).
            # H2: the coverage ledger (U5) carries gjc_no_input_rewrite marker.
            return b""

        if decision == "deny":
            return self._block_bytes(verdict.reason)

        if decision == "ask":
            # D3: ask downgraded to deny-with-guidance.  gjc has no native
            # ask tier (§3 trait table + §4.5); operator must approve via
            # magi-cp and re-run.
            reason = (
                "blocked pending approval; "
                "re-run after operator approves via magi-cp "
                "(ask-tier unsupported on gjc)"
            )
            return self._block_bytes(reason)

        # Unknown decision: fail-closed (same posture as malformed payload).
        return self._block_bytes(f"unknown verdict decision {decision!r}")

    @staticmethod
    def _block_bytes(reason: str) -> bytes:
        """Emit the gjc deny wire line (§4.3, §4.5)."""
        obj = {"block": True, "reason": _prefixed(reason)}
        return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")

    # ── trait-required stubs (U4, U5 complete these) ─────────────────────────

    def emit_managed_config(self, ir: list) -> ManagedConfigBundle:  # type: ignore[override]
        """Policy IR -> managed config bundle (gjc plugin manifest + shim).

        Full implementation in U4 (gjc_bundle_emitter). Stub raises to keep
        the trait satisfied without silently returning an empty bundle.
        """
        raise NotImplementedError(
            "emit_managed_config not yet implemented for gjc (U4 / PR-2)"
        )

    def coverage_report(self, ir: list):  # type: ignore[override]
        """Per-policy gjc coverage report.

        Full implementation in U5 (gjc coverage golden). Stub raises.
        """
        raise NotImplementedError(
            "coverage_report not yet implemented for gjc (U5 / PR-3)"
        )

    def default_install_paths(self) -> InstallPaths:
        """Where the installer drops the gjc plugin bundle (§6.2)."""
        return InstallPaths(
            managed_config_dir="~/.gjc/agent/gjc-plugins/magi-cp-gate",
            slash_commands_dir=(
                "~/.gjc/agent/gjc-plugins/magi-cp-gate/commands"
            ),
            context_templates_dir=(
                "~/.gjc/agent/gjc-plugins/magi-cp-gate/context-templates"
            ),
        )


# ── Gate entry point (mirrors run_codex_gate, §4.3 / §4.5) ─────────────────


def run_gjc_gate(raw_stripped: str) -> int:
    """gjc runtime path for the gate dispatcher.

    Parses the gjc stdin envelope, runs the SAME policy decision the CC path
    uses (``gate.decide``, one engine N surfaces), and emits the gjc verdict
    wire bytes.  Reachable when ``detect_runtime`` selects ``"gjc"``
    (default-ON flag + a gjc runtime signal); an explicit falsy
    ``MAGI_CP_GJC_RUNTIME_ENABLED`` forces the CC path and makes this
    unreachable.

    Mirrors ``run_codex_gate`` (runtime/codex.py:724-759):
      blank stdin  -> pass-through (exit 0, no output)
      malformed    -> fail-closed block bytes on stdout, exit 0
      well-formed  -> decide() + emit, exit 0
    """
    driver = GjcDriver()
    if not raw_stripped.strip():
        # No hook context — pass through silently, like the CC/Codex paths.
        return 0
    try:
        event = driver.parse_hook_payload(raw_stripped.encode("utf-8"))
    except (json.JSONDecodeError, ValueError):
        # Malformed payload -> fail-closed deny.
        out = driver.emit_verdict(Verdict(
            decision="deny",
            reason="malformed hook payload (json)",
            hook_event_name="PreToolUse",
        ))
        if out:
            sys.stdout.buffer.write(out)
        return 0

    # Reuse the CC decision engine (lazy import avoids import cycle).
    from ..local.gate import decide

    verdict = decide(event.raw)
    out = driver.emit_verdict(verdict)
    if out:
        sys.stdout.buffer.write(out)
    return 0


__all__ = ["GjcDriver", "_GJC_TO_CC_TOOL", "run_gjc_gate"]
