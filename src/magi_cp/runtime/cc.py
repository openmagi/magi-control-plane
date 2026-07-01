"""Claude Code runtime driver.

Factored out of ``src/magi_cp/local/gate.py`` as part of the P1 Codex
adapter work (design doc Section 3.3). The stdin JSON parsing, verdict
envelope emission, and managed-settings emitter binding live here so the
gate entry point becomes a runtime dispatcher and a second driver
(``codex.py``) stands at the same distance from the entry point.

BYTE-EQUIVALENCE CONTRACT: the CC policy path in ``gate.evaluate`` is
unchanged. This driver's ``parse_hook_payload`` / ``emit_verdict``
produce output byte-identical to the pre-adapter ``gate._deny`` /
``gate._allow`` helpers (both route through ``policy.cc_shapes``), so the
dispatcher wrapping is transparent. ``tests/test_cc_driver_passthrough.py``
pins this.
"""
from __future__ import annotations

import json
import os

from ..policy.cc_shapes import (
    emit_allow_payload,
    emit_ask_payload,
    emit_deny_payload,
)
from ..policy.compiler import (
    compile_to_managed_settings,
    context_template_sidecars,
)
from ..policy.ir import AnyPolicy
from .trait import (
    CoveragePolicyStatus,
    CoverageReport,
    HookEvent,
    InstallPaths,
    ManagedConfigBundle,
    Verdict,
    merge_verdict_side_channels,
)


class CCDriver:
    """Claude Code ``HookRuntime`` implementation."""

    runtime_id: str = "claude-code"

    # ── stdin -> canonical event ─────────────────────────────────────
    def parse_hook_payload(self, raw_stdin: bytes) -> HookEvent:
        """Decode CC's stdin hook JSON into a canonical ``HookEvent``.

        A blank / whitespace-only stdin (started outside a hook context)
        decodes to a ``HookEvent`` with an empty ``raw`` dict — the
        dispatcher treats that as "pass through / allow", matching the
        legacy ``gate.cli`` behaviour.
        """
        text = raw_stdin.decode("utf-8", errors="replace").strip()
        if not text:
            return HookEvent(hook_event_name="PreToolUse", raw={})
        payload = json.loads(text)
        if not isinstance(payload, dict):
            # Non-object JSON is malformed for CC's contract.
            raise ValueError("hook payload is not a JSON object")
        return self._event_from_dict(payload)

    @staticmethod
    def _event_from_dict(payload: dict) -> HookEvent:
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            tool_input = {}
        tool_response = payload.get("tool_response")
        if not isinstance(tool_response, dict):
            tool_response = None
        return HookEvent(
            hook_event_name=payload.get("hook_event_name") or "PreToolUse",
            session_id=str(payload.get("session_id") or ""),
            cwd=str(payload.get("cwd") or ""),
            tool_name=str(payload.get("tool_name") or ""),
            tool_input=tool_input,
            tool_response=tool_response,
            model=str(payload.get("model") or ""),
            permission_mode=str(payload.get("permission_mode") or ""),
            transcript_path=str(payload.get("transcript_path") or ""),
            raw=payload,
        )

    # ── canonical verdict -> stdout ──────────────────────────────────
    def emit_verdict(self, verdict: Verdict) -> bytes:
        """Serialize a canonical ``Verdict`` to CC stdout bytes.

        Allow is silent (empty bytes) — CC continues its normal
        permission flow on a silent gate exit, exactly like the legacy
        ``gate._allow``. Deny / ask route through ``policy.cc_shapes`` so
        the emitted JSON is byte-identical to ``gate._deny``'s output
        (``json.dumps(..., ensure_ascii=False)`` with a trailing
        newline, matching ``print``).

        The universal ``continue`` / ``systemMessage`` side channels
        (design doc Section 2.2) layer on last via
        ``merge_verdict_side_channels``. They are ``None`` on every
        current ``decide()`` path, so this stays byte-identical to the
        legacy ``gate._allow`` / ``gate._deny`` output.
        """
        event = verdict.hook_event_name or "PreToolUse"
        obj = merge_verdict_side_channels(
            self._decision_obj(verdict, event), verdict,
        )
        if obj is None:
            # Silent allow — no stdout (matches gate._allow).
            return b""
        return self._dump(obj)

    @staticmethod
    def _decision_obj(verdict: Verdict, event: str) -> dict | None:
        """The per-decision CC stdout object, before side channels.

        ``None`` means a silent allow (empty stdout)."""
        if verdict.decision == "allow":
            if verdict.updated_input is not None:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow",
                        "updatedInput": verdict.updated_input,
                    }
                }
            if verdict.additional_context is not None:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": event,
                        "additionalContext": verdict.additional_context,
                    }
                }
            return None
        if verdict.decision == "ask":
            return emit_ask_payload(verdict.reason, hook_event_name=event)
        # deny (default / fail-closed)
        return emit_deny_payload(verdict.reason, hook_event_name=event)

    @staticmethod
    def _dump(obj: dict) -> bytes:
        # ``print`` in the legacy path adds a trailing newline; reproduce
        # it so byte-equivalence holds against captured stdout.
        return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")

    # ── managed config ───────────────────────────────────────────────
    def emit_managed_config(self, ir: list[AnyPolicy]) -> ManagedConfigBundle:
        """Wrap the existing CC managed-settings compiler.

        Returns the compiled ``managed-settings.json`` bytes plus the
        context-template sidecar map, in the runtime-neutral
        ``ManagedConfigBundle`` envelope.
        """
        settings = compile_to_managed_settings(ir)
        sidecars = context_template_sidecars(ir)
        return ManagedConfigBundle(
            files={
                "managed-settings.json": json.dumps(
                    settings, ensure_ascii=False, sort_keys=True, indent=2,
                ),
            },
            context_templates=sidecars,
        )

    # ── coverage ─────────────────────────────────────────────────────
    def coverage_report(self, ir: list[AnyPolicy]) -> CoverageReport:
        """CC is the reference runtime: every policy enforces natively."""
        return CoverageReport(
            runtime_id=self.runtime_id,
            policies=tuple(
                CoveragePolicyStatus(policy_id=p.id, status="enforced")
                for p in ir
            ),
        )

    # ── install paths ────────────────────────────────────────────────
    def default_install_paths(self) -> InstallPaths:
        claude_dir = os.path.expanduser("~/.claude")
        return InstallPaths(
            managed_config_dir=claude_dir,
            slash_commands_dir=os.path.join(claude_dir, "commands", "magi"),
            context_templates_dir=os.path.join(
                claude_dir, "context-templates",
            ),
        )


__all__ = ["CCDriver"]
