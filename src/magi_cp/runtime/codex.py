"""Codex CLI runtime driver.

Design brief: docs/plans/2026-06-30-codex-runtime-adapter-design.md
(Section 2 wire format, Section 3 architecture, Section 6 managed
enforcement). P1 scope: parse the Codex stdin envelope into a canonical
``HookEvent``, emit a valid Codex verdict envelope from a canonical
``Verdict``, and wrap the Codex ``requirements.toml`` emitter. The four
gap shims (Section 4) are P2 — this driver is the straight-through
translation.

Everything here is dead code with ``MAGI_CP_CODEX_RUNTIME_ENABLED`` unset
(default): ``detect.detect_runtime`` never returns ``"codex"`` with the
flag off, so ``run_codex_gate`` is unreachable on the CC path.
"""
from __future__ import annotations

import json
import os
import sys

from ..policy.codex_toml_emitter import compile_to_codex_requirements
from ..policy.ir import (
    AnyPolicy,
    McpGatingPolicy,
    PermissionPolicy,
    SubagentPolicy,
)
from .trait import (
    CoveragePolicyStatus,
    CoverageReport,
    HookEvent,
    InstallPaths,
    ManagedConfigBundle,
    Verdict,
    merge_verdict_side_channels,
)


# Archetypes CC compiles to a NATIVE managed-config surface
# (``permissions.{allow,deny,ask}`` / ``allowedMcpServers`` /
# ``permissions.deny += Agent(<name>)``) but Codex does NOT yet: the
# ``codex_toml_emitter`` skips Permission / Mcp entirely and
# ``SubagentPolicy`` only flips ``[features].multi_agent`` (never a deny
# rule). Until the out-of-band Codex permission/mcp/subagent emitter
# exists, these compile to ZERO enforceable Codex config, so
# ``coverage_report`` must NOT report them "enforced".
# TODO(live-test P2): land the Codex permission/mcp/subagent-disable
# config emitter, then drop these from the pending set so the status
# flips back to "enforced".
_CODEX_NATIVE_CONFIG_PENDING = (
    PermissionPolicy,
    McpGatingPolicy,
    SubagentPolicy,
)


# Events whose Codex channel reads top-level ``{"decision": "block",
# "reason": ...}`` (retry-feedback), same split as CC's PostToolUse*
# channel plus UserPromptSubmit (which Codex documents as accepting
# ``decision: "block"``). See design doc Section 2.2.
# TODO(live-test D5): confirm the exact block-channel event set against a
# real Codex install; PostToolUse post-hoc block is documented.
_BLOCK_CHANNEL_EVENTS: frozenset[str] = frozenset({
    "PostToolUse",
    "UserPromptSubmit",
})

# Events that carry the ``decision.behavior = allow|deny`` nested shape
# rather than ``hookSpecificOutput.permissionDecision``.
_BEHAVIOR_CHANNEL_EVENTS: frozenset[str] = frozenset({
    "PermissionRequest",
})


def _prefixed(reason: str) -> str:
    """Stable ``MAGI: `` provenance marker, matching ``cc_shapes``."""
    return f"MAGI: {reason}"


class CodexDriver:
    """Codex CLI ``HookRuntime`` implementation."""

    runtime_id: str = "codex"

    # ── stdin -> canonical event ─────────────────────────────────────
    def parse_hook_payload(self, raw_stdin: bytes) -> HookEvent:
        """Decode Codex's stdin hook JSON into a canonical ``HookEvent``.

        Codex's envelope is near-identical to CC's plus ``turn_id`` and
        ``matcher_aliases`` (design doc Section 2.2). A blank stdin
        decodes to an empty-``raw`` event (pass-through).
        """
        text = raw_stdin.decode("utf-8", errors="replace").strip()
        if not text:
            return HookEvent(hook_event_name="PreToolUse", raw={})
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("codex hook payload is not a JSON object")
        return self._event_from_dict(payload)

    @staticmethod
    def _event_from_dict(payload: dict) -> HookEvent:
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            tool_input = {}
        tool_response = payload.get("tool_response")
        if not isinstance(tool_response, dict):
            tool_response = None
        aliases_raw = payload.get("matcher_aliases")
        if isinstance(aliases_raw, (list, tuple)):
            matcher_aliases = tuple(str(a) for a in aliases_raw)
        else:
            matcher_aliases = ()
        return HookEvent(
            hook_event_name=payload.get("hook_event_name") or "PreToolUse",
            session_id=str(payload.get("session_id") or ""),
            turn_id=str(payload.get("turn_id") or ""),
            cwd=str(payload.get("cwd") or ""),
            tool_name=str(payload.get("tool_name") or ""),
            tool_input=tool_input,
            tool_response=tool_response,
            model=str(payload.get("model") or ""),
            permission_mode=str(payload.get("permission_mode") or ""),
            transcript_path=str(payload.get("transcript_path") or ""),
            matcher_aliases=matcher_aliases,
            raw=payload,
        )

    # ── canonical verdict -> stdout ──────────────────────────────────
    def emit_verdict(self, verdict: Verdict) -> bytes:
        """Serialize a canonical ``Verdict`` to Codex stdout bytes.

        Allow is silent (empty bytes) unless it carries an
        ``updatedInput`` rewrite; Codex continues its permission flow on
        a silent gate exit like CC. Deny / ask route to the per-event
        channel: ``PermissionRequest`` uses the nested
        ``decision.behavior`` shape, the block-channel events use
        top-level ``decision``/``reason``, and everything else uses
        ``hookSpecificOutput.permissionDecision``.

        The universal ``continue`` / ``systemMessage`` side channels
        (design doc Section 2.2 — accepted on every Codex event) layer on
        last via ``merge_verdict_side_channels``, including on an
        otherwise-silent allow so a populated side channel is never
        dropped.
        """
        obj = merge_verdict_side_channels(self._verdict_obj(verdict), verdict)
        if obj is None:
            return b""
        return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")

    def _verdict_obj(self, verdict: Verdict) -> dict | None:
        event = verdict.hook_event_name or "PreToolUse"

        if verdict.decision == "allow":
            if verdict.updated_input is not None:
                # Codex accepts updatedInput on PreToolUse (Section 2.2).
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow",
                        "updatedInput": verdict.updated_input,
                    }
                }
            # Silent allow. NOTE: additionalContext on PreToolUse is
            # rejected by Codex (OpenAI issue #19385) — the downgrade to
            # systemMessage / deferred-prompt is P2 shim B, so P1 does
            # not attach context on an allow.
            # TODO(live-test D2): PreToolUse additionalContext rejection.
            return None

        reason = _prefixed(verdict.reason)

        if event in _BEHAVIOR_CHANNEL_EVENTS:
            # PermissionRequest nested behavior shape.
            behavior = "deny" if verdict.decision == "deny" else "ask"
            return {"decision": {"behavior": behavior, "message": reason}}

        if event in _BLOCK_CHANNEL_EVENTS:
            # Top-level block channel (post-hoc / prompt). ask has no verb
            # here — fall back to block so the operator sees feedback.
            return {"decision": "block", "reason": reason}

        # PreToolUse + the rest: hookSpecificOutput.permissionDecision.
        decision = "ask" if verdict.decision == "ask" else "deny"
        return {
            "hookSpecificOutput": {
                "hookEventName": event,
                "permissionDecision": decision,
                "permissionDecisionReason": reason,
            }
        }

    # ── managed config ───────────────────────────────────────────────
    def emit_managed_config(self, ir: list[AnyPolicy]) -> ManagedConfigBundle:
        """Wrap the Codex ``requirements.toml`` emitter into the
        runtime-neutral ``ManagedConfigBundle`` envelope."""
        bundle = compile_to_codex_requirements(ir)
        return ManagedConfigBundle(
            files={
                "requirements.toml": bundle.requirements_toml,
                "hooks.json": bundle.hooks_json_sidecar,
            },
            context_templates=bundle.context_templates,
        )

    # ── coverage ─────────────────────────────────────────────────────
    def coverage_report(self, ir: list[AnyPolicy]) -> CoverageReport:
        """Per-policy Codex coverage.

        Hook-producing archetypes (Evidence / InputRewrite / RunCommand /
        ContextInjection) report ``"enforced"`` in P1. The native-surface
        archetypes CC compiles to ``permissions`` / ``allowedMcpServers``
        / ``Agent(<name>)`` deny (Permission / Mcp / Subagent) have NO
        Codex managed-config emitter yet, so they report
        ``"codex_native_config_pending"`` rather than a false
        ``"enforced"`` (see ``_CODEX_NATIVE_CONFIG_PENDING``). The
        gap-shim markers (silent-skip, no-session-end,
        internal-subagent) land in P2 alongside the shim implementations.
        """
        # TODO(live-test P2): PreToolUse tool-coverage silent-skip markers
        # (D3) arrive with shim A; the permission/mcp/subagent-disable
        # emitter (native_config_pending) flips those back to "enforced".
        return CoverageReport(
            runtime_id=self.runtime_id,
            policies=tuple(
                CoveragePolicyStatus(
                    policy_id=p.id,
                    status=(
                        "codex_native_config_pending"
                        if isinstance(p, _CODEX_NATIVE_CONFIG_PENDING)
                        else "enforced"
                    ),
                )
                for p in ir
            ),
        )

    # ── install paths ────────────────────────────────────────────────
    def default_install_paths(self) -> InstallPaths:
        # Section 6.1: /etc/codex managed config, ~/.codex/skills/magi
        # slash-command (skills) surface, /etc/codex/magi-cp sidecars.
        return InstallPaths(
            managed_config_dir="/etc/codex",
            slash_commands_dir=os.path.expanduser("~/.codex/skills/magi"),
            context_templates_dir="/etc/codex/magi-cp/context-templates",
        )


def run_codex_gate(raw_stripped: str) -> int:
    """Codex runtime path for the gate dispatcher.

    Parses the Codex stdin envelope, runs the SAME policy decision the CC
    path uses (``gate.decide`` — one engine, two surfaces), and emits the
    Codex verdict envelope. Only reachable with
    ``MAGI_CP_CODEX_RUNTIME_ENABLED`` on, so it is dead code by default.
    """
    driver = CodexDriver()
    if not raw_stripped:
        # No hook context — pass through silently, like the CC path.
        return 0
    try:
        event = driver.parse_hook_payload(raw_stripped.encode("utf-8"))
    except (json.JSONDecodeError, ValueError):
        # Malformed payload → fail-closed deny on the default channel.
        out = driver.emit_verdict(Verdict(
            decision="deny",
            reason="malformed hook payload (json)",
            hook_event_name="PreToolUse",
        ))
        if out:
            sys.stdout.buffer.write(out)
        return 0

    # Reuse the CC decision engine (lazy import avoids an import cycle:
    # gate imports runtime only inside its dispatcher).
    from ..local.gate import decide

    verdict = decide(event.raw)
    out = driver.emit_verdict(verdict)
    if out:
        sys.stdout.buffer.write(out)
    return 0


__all__ = ["CodexDriver", "run_codex_gate"]
