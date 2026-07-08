"""Hermes runtime driver (third ``HookRuntime`` implementation).

Design brief: 2026-07-06-magi-cp-hermes-runtime-adapter-design (private
planning repo). This is the P1 deliverable (Section 10 "P1: driver +
detection + contract tests"): parse / emit / coverage / install-paths,
built against the same ``HookRuntime`` trait the CC and Codex drivers
implement (``trait.py:242``).

Hermes (``NousResearch/hermes-agent``) speaks a declarative shell-hook
wire that is already Claude-Code-compatible on the block channel
(``agent/shell_hooks.py``), so the driver needs no verdict-field remap of
the kind the Codex F4 matcher table needed. What it DOES carry is a
tool-name normalization table over Hermes's ~70-name registry (Section
2.9), of which the CC-mappable core is ~20; every other name passes
through RAW under the K6 unmapped posture (allow + audit + the
``hermes_unmapped_tool`` marker) so nothing escapes the matcher-less gate
silently.

Wire facts this driver depends on (all verified against Hermes HEAD
``18e840469``, cited file:line in the design doc):

  - stdin payload (``shell_hooks.py:527-543``): snake_case
    ``hook_event_name`` (``pre_tool_call`` ...) + ``tool_name`` +
    ``tool_input`` + ``session_id`` + ``cwd`` + ``extra``.
  - stdout block (``shell_hooks.py:589-594``, docstring ~:43-52): the
    driver emits the Hermes-canonical ``{"action":"block","message":...}``
    (the CC-style ``{"decision":"block","reason":...}`` alias is also
    accepted upstream, but emitting canonical is one fewer normalization
    on the hot path, design Section 3.4).
  - allow == empty stdout (the documented silent no-op).
  - context inject (non-``pre_tool_call`` events) == ``{"context":...}``.
  - There is NO ask tier, NO ``updatedInput``, NO ``additionalContext``
    on ``pre_tool_call``, and exit codes NEVER block (only stdout does).
    That drives the coverage downgrades below and the fail-closed
    contract (design Section 8, ``run_hermes_gate``).

``MAGI_CP_HERMES_RUNTIME_ENABLED`` is default-ON (no-default-OFF policy,
design Section 10 P1+), so the Hermes path is reachable by default when a
runtime signal selects it (``MAGI_CP_RUNTIME=hermes`` / snake_case payload
sniff). A genuine Claude Code invocation carries no such signal, so
``detect.detect_runtime`` still returns ``"cc"`` and ``run_hermes_gate``
is never entered on the CC path. Setting the flag to an explicit falsy
value forces ``"cc"`` unconditionally (the kill switch).
"""
from __future__ import annotations

import json
import re
import sys

from ..policy.ir import (
    AnyPolicy,
    ContextInjectionPolicy,
    InputRewritePolicy,
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
)


# ── event map: Hermes snake_case -> canonical (design Section 4 matrix) ──
# Only the canonical events magi-cp's Policy IR consumes are mapped; every
# other Hermes event (``transform_*``, ``post_llm_call``, ``pre_api_*``,
# ...) has no canonical counterpart and passes through with its raw
# snake_case name (observe-only, never a verdict channel). ``pre_verify``
# maps to ``Stop`` (block-the-stop == keep going, ``shell_hooks.py:596-605``)
# and ``pre_llm_call`` to the CC ``UserPromptSubmit`` context event (the
# ``{"context"}`` inject wire, design Section 4).
_HERMES_EVENT_TO_CANONICAL: dict[str, str] = {
    "pre_tool_call": "PreToolUse",
    "post_tool_call": "PostToolUse",
    "pre_llm_call": "UserPromptSubmit",
    "pre_verify": "Stop",
    "on_session_start": "SessionStart",
    "on_session_end": "SessionEnd",
    "on_session_finalize": "SessionEnd",
    "subagent_start": "SubagentStart",
    "subagent_stop": "SubagentStop",
    "pre_approval_request": "PermissionRequest",
}

# ── tool-name map: Hermes CC-mappable core -> canonical CC family ────────
# Design Section 2.9 / 3.3. The ~20 CC-mappable names normalize onto the
# canonical CC tool families so packs authored in CC vocabulary keep
# governing them unchanged. Everything NOT in this table passes through
# RAW (the K6 unmapped posture, ``_normalize_tool_name`` below), so a
# raw-name policy (``computer_use``, ``cronjob``, ``ha_call_service`` ...)
# can be authored directly. ``mcp__*`` is intentionally absent: Hermes uses
# the identical ``mcp__<server>__<tool>`` convention (``mcp_tool.py:4074``),
# so it round-trips through the raw passthrough unchanged.
#
# This is the same CLASS of maintenance as the Codex ``_CC_TO_CODEX_TOOL``
# table, an order of magnitude more surface. The vendored list is generated
# by the ``registry.register(`` scan (design Section 2.9); the P1 driver
# ships the CC-mappable core and lets unlisted names default to the safe,
# visible unmapped posture until the table catches up.
# TODO(P2): wire the repeatable upstream-drift check (re-run the
# ``registry.register(`` scan against a fresh Hermes clone, fail on
# unlisted names) into CI, per design Section 2.9 / 10 P1.
_HERMES_TOOL_TO_CC: dict[str, str] = {
    # Bash family: shell + code execution + terminal management + process.
    "terminal": "Bash",
    "execute_code": "Bash",
    "read_terminal": "Bash",
    "close_terminal": "Bash",
    "process": "Bash",
    # Edit / Write family: file mutation.
    "write_file": "Write",
    "patch": "Edit",
    # Read.
    "read_file": "Read",
    # Glob / Grep family: file search.
    "search_files": "Grep",
    # Web families.
    "web_extract": "WebFetch",
    "web_search": "WebSearch",
    "x_search": "WebSearch",
    # Subagent spawn.
    "delegate_task": "Task",
}


# ``mcp__<server>__<tool>`` is Hermes's identical CC convention
# (``mcp_tool.py:4074``); it round-trips through the raw passthrough but is
# NOT "unmapped" (a CC-vocab ``mcp__*`` policy governs it directly).
_MCP_TOOL_RE = re.compile(r"^mcp__[A-Za-z0-9_]+__[A-Za-z0-9_]+$")

# The CC tool families a Hermes tool actually normalizes ONTO (the values
# of ``_HERMES_TOOL_TO_CC``). A CC-vocab policy matcher IN this set governs
# a real Hermes tool family; a CC matcher NOT in it (``NotebookEdit``,
# ``TodoWrite``, ``BashOutput`` ...) has no Hermes tool behind it, so a pack
# authored on it governs nothing on Hermes — the honest ``hermes_unmapped_tool``
# signal (design Section 6: "a pack authored purely in CC vocabulary governs
# the mapped families ONLY"). ``mcp__*`` is governed via the identical
# convention and handled separately in ``_matcher_reaches_hermes``.
_GOVERNED_CC_FAMILIES: frozenset[str] = frozenset(_HERMES_TOOL_TO_CC.values())


def _matcher_reaches_hermes(matcher: str) -> bool:
    """Whether a CC-vocab policy ``matcher`` governs a real Hermes tool
    family (design Section 6).

    True when the matcher is a governed CC family (``Bash``, ``Read``,
    ``Edit``, ``Write``, ``Grep``, ``WebFetch``, ``WebSearch``, ``Task``),
    an ``mcp__*`` name (identical convention), a simple alternation whose
    every token is a governed family, or empty (an all-tools / non-tool
    policy, which the matcher-less gate covers). False for a CC matcher no
    Hermes tool maps onto — that pack has no reach on Hermes and gets the
    ``hermes_unmapped_tool`` marker rather than a false ``enforced``.
    """
    if not matcher or matcher == "*":
        return True
    if matcher in _GOVERNED_CC_FAMILIES:
        return True
    if _MCP_TOOL_RE.match(matcher):
        return True
    if "|" in matcher:
        tokens = [t.strip() for t in matcher.split("|") if t.strip()]
        return bool(tokens) and all(
            t in _GOVERNED_CC_FAMILIES or _MCP_TOOL_RE.match(t)
            for t in tokens
        )
    return False


def _normalize_tool_name(raw_tool_name: str) -> tuple[str, bool]:
    """Map a Hermes tool name to its canonical CC family.

    Returns ``(canonical_name, is_mapped)``. A name in the CC-mappable
    core normalizes to its CC family (``is_mapped=True``); an ``mcp__*``
    name passes through raw but counts as mapped (identical CC
    convention). Every OTHER name passes through RAW with
    ``is_mapped=False`` — the K6 unmapped posture (design Section 3.3):
    the raw name reaches the gate on every call (the emitted hooks are
    matcher-less, design Section 5.1) so ungoverned surface is visible via
    the ``hermes_unmapped_tool`` marker instead of silent. Deliberately NOT
    done: guessing an approximate CC family for an unmapped name (a
    ``computer_use -> Bash`` guess would let a policy author believe
    coverage they do not have).
    """
    mapped = _HERMES_TOOL_TO_CC.get(raw_tool_name)
    if mapped is not None:
        return (mapped, True)
    if _MCP_TOOL_RE.match(raw_tool_name):
        return (raw_tool_name, True)
    return (raw_tool_name, False)


# ── coverage vocabulary (design Section 6) ───────────────────────────────
# Statuses this driver introduces, all rendered through the existing
# ``coverage_cell`` vocabulary (no dashboard change beyond the third
# runtime column, design Section 6):
#   hermes_no_ask_tier          - Verdict ``ask`` has no Hermes wire tier
#                                 (downgraded to deny-with-guidance, K3).
#   hermes_no_input_rewrite     - ``InputRewritePolicy`` has no shell-wire
#                                 rewrite surface (Branch A, Section 7.1).
#   hermes_stop_edit_turns_only - ``Stop``-hosted policy fires only on
#                                 edit-turns (``conversation_loop.py:5169``).
#   hermes_no_compact_event     - ``PreCompact`` / ``PostCompact`` have no
#                                 Hermes hook (design Section 4).
#   hermes_pre_tool_context_dropped - ``ContextInjection`` scoped to
#                                 ``PreToolUse`` loses its channel (the
#                                 shell wire drops non-block on
#                                 ``pre_tool_call``, ``shell_hooks.py:589``).
#   hermes_prompt_block_plugin_only - ``UserPromptSubmit`` deny needs a
#                                 plugin; the shell wire cannot block a
#                                 prompt (design Section 4).
#   hermes_unmapped_tool        - a policy over an unmapped raw tool name
#                                 (K6 posture marker, design Section 3.3).

# Canonical events for which Hermes fires a hook AT ALL (design Section 4
# matrix). A policy hosted on an event OUTSIDE this set (``PreCompact`` /
# ``PostCompact`` / ``Notification`` / ...) never fires on Hermes and gets
# an honest downgrade rather than a false ``enforced``. Mirrors the Codex
# ``CODEX_LIVE_EVENTS`` discipline (``codex.py:244``).
HERMES_LIVE_EVENTS: frozenset[str] = frozenset({
    "PreToolUse",
    "PostToolUse",
    "UserPromptSubmit",
    "Stop",
    "SubagentStart",
    "SubagentStop",
    "SessionStart",
    "SessionEnd",
    "PermissionRequest",
})

# Subagent lifecycle events (parity with the Codex driver's set; on Hermes
# the single ``delegate_task`` spawn path makes this coverage deterministic,
# so these report ``enforced`` unlike Codex's internal-reviewer caveat).
_SUBAGENT_LIFECYCLE_EVENTS: frozenset[str] = frozenset({
    "SubagentStart", "SubagentStop",
})


def _prefixed(reason: str) -> str:
    """Stable ``MAGI: `` provenance marker, matching ``cc_shapes`` /
    the Codex driver's ``_prefixed``."""
    return f"MAGI: {reason}"


def _policy_event_matcher(p: AnyPolicy) -> tuple[str, str]:
    """(event, matcher) for a hook-producing policy. Native-surface
    archetypes (Permission / Mcp / Subagent) have no trigger and are
    handled before this is called. Mirrors ``codex._policy_event_matcher``.
    """
    if isinstance(p, ContextInjectionPolicy):
        return (p.event, p.matcher)
    trig = getattr(p, "trigger", None)
    if trig is not None:
        return (getattr(trig, "event", ""), getattr(trig, "matcher", ""))
    return ("", "")


def _coverage_status_for_hermes(p: AnyPolicy) -> tuple[str, str | None]:
    """Per-policy Hermes coverage ``(status, downgrade)``.

    Mirrors ``runtime.codex._coverage_status_for`` (``codex.py:315``) with
    the Hermes ledger (design Section 6). The Hermes hook path is the
    VERIFIED in-process pipeline (design Section 2.3), so ``enforced``
    (hook) is a strong status here, not a fallback; the downgrades below
    name the cases where a canonical channel has no Hermes wire.

    Native-surface archetypes:
      - ``PermissionPolicy`` rides the hook path on Hermes (its native
        ``approvals.deny`` lowering is a P2 emitter concern; the hook is
        the primary surface). An ``ask`` permission has no Hermes wire
        tier -> ``hermes_no_ask_tier``.
      - ``McpGatingPolicy`` rides the ``mcp__*`` passthrough (identical
        naming) -> ``enforced`` (hook), better than Codex which had NO
        native MCP surface.
      - ``SubagentPolicy`` rides ``pre_tool_call`` on ``delegate_task`` +
        ``subagent_start`` audit -> ``enforced`` (hook).
      - ``InputRewritePolicy`` has no shell-wire rewrite surface (Branch A,
        Section 7.1) -> ``hermes_no_input_rewrite`` (unsupported).
    """
    if isinstance(p, PermissionPolicy):
        if p.permission == "ask":
            return (
                "hermes_no_ask_tier",
                "deny-with-guidance (Hermes hook wire has no ask tier)",
            )
        # deny / allow both ride the (matcher-less) hook path. When the CC
        # matcher targets no Hermes tool family, flag the K6 marker so the
        # dashboard never overstates CC-vocab reach (design Section 6).
        if not _matcher_reaches_hermes(p.trigger.matcher):
            return (
                "hermes_unmapped_tool",
                "no Hermes tool maps onto this CC matcher; author a "
                "raw-name rule for reach",
            )
        return ("enforced", None)
    if isinstance(p, McpGatingPolicy):
        # Identical ``mcp__*`` naming (mcp_tool.py:4074) -> hook passthrough.
        return ("enforced", None)
    if isinstance(p, SubagentPolicy):
        return ("enforced", None)
    if isinstance(p, InputRewritePolicy):
        # Branch A default (design Section 7.1): no shell-wire rewrite.
        return (
            "hermes_no_input_rewrite",
            "convert to a deny policy or enable the Hermes rewrite shim",
        )

    event, matcher = _policy_event_matcher(p)

    # Subagent lifecycle: single deterministic spawn path on Hermes.
    if event in _SUBAGENT_LIFECYCLE_EVENTS:
        return ("enforced", None)
    # Stop policies fire only on edit-turns (conversation_loop.py:5169).
    if event == "Stop":
        return (
            "hermes_stop_edit_turns_only",
            "pre_verify fires only when the turn edited code",
        )
    # UserPromptSubmit deny needs a plugin; the shell wire only injects
    # context, it cannot block a prompt (design Section 4).
    if event == "UserPromptSubmit" and not isinstance(p, ContextInjectionPolicy):
        return (
            "hermes_prompt_block_plugin_only",
            "prompt-block requires a plugin; shell wire is context-only",
        )
    # ContextInjection scoped to PreToolUse loses its channel (the shell
    # wire drops non-block output on pre_tool_call, shell_hooks.py:589).
    if event == "PreToolUse" and isinstance(p, ContextInjectionPolicy):
        return (
            "hermes_pre_tool_context_dropped",
            "deferred to the next pre_llm_call (context channel)",
        )
    # Compaction has no Hermes hook at all.
    if event in ("PreCompact", "PostCompact"):
        return (
            "hermes_no_compact_event",
            "no compaction hook in Hermes VALID_HOOKS",
        )
    # Event-level not-live catch (placed AFTER every downgrade above, same
    # discipline as codex._coverage_status_for). Only a genuinely-dead
    # event (Notification, TaskCreated, ...) falls through here. ``event``
    # is non-empty (native-surface archetypes returned above); the guard is
    # belt-and-suspenders.
    if event and event not in HERMES_LIVE_EVENTS:
        return (
            "hermes_event_not_live",
            "never fires on Hermes; author on a live event or keep for "
            "Claude Code",
        )
    # PreToolUse over a CC matcher no Hermes tool maps onto: K6 marker so
    # an authored pack never silently overstates CC-vocab reach (Section 6).
    if event == "PreToolUse" and not _matcher_reaches_hermes(matcher):
        return (
            "hermes_unmapped_tool",
            "no Hermes tool maps onto this CC matcher; author a "
            "raw-name rule for reach",
        )
    return ("enforced", None)


class HermesDriver:
    """Hermes ``HookRuntime`` implementation."""

    runtime_id: str = "hermes"

    # ── stdin -> canonical event ─────────────────────────────────────
    def parse_hook_payload(self, raw_stdin: bytes) -> HookEvent:
        """Decode Hermes's stdin hook JSON into a canonical ``HookEvent``.

        Hermes's envelope (``shell_hooks.py:527-543``) is snake_case
        ``hook_event_name`` + ``tool_name`` + ``tool_input`` +
        ``session_id`` + ``cwd`` + ``extra``. The event name maps to its
        canonical PascalCase counterpart (``_HERMES_EVENT_TO_CANONICAL``);
        an unmapped Hermes event passes through with its raw snake_case
        name. The tool name normalizes the CC-mappable core to its CC
        family and passes every other name through RAW (the K6 unmapped
        posture, ``_normalize_tool_name``).

        ``extra.turn_id`` / ``extra.task_id`` / ``extra.tool_call_id`` map
        onto the canonical event's correlation fields. A blank stdin
        decodes to an empty-``raw`` event (pass-through), mirroring the
        Codex driver.
        """
        text = raw_stdin.decode("utf-8", errors="replace").strip()
        if not text:
            return HookEvent(hook_event_name="PreToolUse", raw={})
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("hermes hook payload is not a JSON object")
        return self._event_from_dict(payload)

    @staticmethod
    def _event_from_dict(payload: dict) -> HookEvent:
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            tool_input = {}
        extra = payload.get("extra")
        if not isinstance(extra, dict):
            extra = {}
        raw_event = payload.get("hook_event_name") or "pre_tool_call"
        canonical_event = _HERMES_EVENT_TO_CANONICAL.get(raw_event, raw_event)
        raw_tool_name = str(payload.get("tool_name") or "")
        canonical_tool_name, _ = _normalize_tool_name(raw_tool_name)
        return HookEvent(
            hook_event_name=canonical_event,
            session_id=str(payload.get("session_id") or ""),
            turn_id=str(extra.get("turn_id") or ""),
            cwd=str(payload.get("cwd") or ""),
            tool_name=canonical_tool_name,
            tool_input=tool_input,
            raw=payload,
        )

    # ── canonical verdict -> stdout ──────────────────────────────────
    def emit_verdict(self, verdict: Verdict) -> bytes:
        """Serialize a canonical ``Verdict`` to Hermes stdout bytes.

        Hermes's shell wire (``_parse_response``, ``shell_hooks.py:557-611``)
        recognizes exactly three shapes: a ``pre_tool_call`` block, a
        ``pre_verify`` continue, and a ``{"context"}`` inject. There is NO
        ask tier, NO ``updatedInput``, NO ``additionalContext`` on
        ``pre_tool_call``. Mapping (design Section 3.4):

          - ``deny`` -> ``{"action":"block","message":"MAGI: <reason>"}``
            (Hermes-canonical shape).
          - ``ask``  -> **downgraded** to a block with an actionable
            message (Hermes has no ask tier, decision K3). The coverage
            report carries the ``hermes_no_ask_tier`` downgrade.
          - ``allow`` -> EMPTY stdout (the documented silent no-op),
            UNLESS the verdict carries an ``additional_context`` on a
            context-injecting event (``UserPromptSubmit`` / ``Stop``),
            which rides the appropriate channel.
          - a ``Stop`` (``pre_verify``) verdict with ``additional_context``
            -> ``{"action":"continue","message": <ctx>}`` (block-the-stop
            keep-going channel).
          - ``additional_context`` on a ``UserPromptSubmit``-mapped event
            (or any non-block event) -> ``{"context": <ctx>}``.

        ``updated_input`` and ``continue_`` have no Hermes shell-wire
        equivalent; both are dropped here (InputRewrite is reported
        ``unsupported`` at coverage time, Branch A / Section 7.1).
        """
        obj = self._verdict_obj(verdict)
        if obj is None:
            return b""
        return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")

    def _verdict_obj(self, verdict: Verdict) -> dict | None:
        event = verdict.hook_event_name or "PreToolUse"

        if verdict.decision in ("deny", "ask"):
            if verdict.decision == "ask":
                # K3: no ask tier -> deny-with-guidance (fail-safe).
                reason = _prefixed(
                    f"{verdict.reason} (blocked pending approval; re-run "
                    f"after operator approves via magi-cp)"
                    if verdict.reason
                    else "blocked pending approval; re-run after operator "
                    "approves via magi-cp"
                )
            else:
                reason = _prefixed(verdict.reason)
            # The Hermes shell wire only honors a block on pre_tool_call
            # (shell_hooks.py:589). On any other event the block string is
            # ignored, but emitting the canonical shape is harmless and
            # keeps the deny observable; the coverage report is where the
            # channel limitation is recorded (design Section 3.4).
            return {"action": "block", "message": reason}

        # allow: silent, unless it carries a context channel.
        if verdict.additional_context is not None:
            if event == "Stop":
                # pre_verify continue channel (block-the-stop keep-going).
                return {
                    "action": "continue",
                    "message": verdict.additional_context,
                }
            # Every other event: the {"context"} inject wire.
            return {"context": verdict.additional_context}
        # Silent allow.
        return None

    # ── managed config ───────────────────────────────────────────────
    def emit_managed_config(self, ir: list[AnyPolicy]) -> ManagedConfigBundle:
        """Minimal P1 managed-config shell.

        The full ``/etc/hermes`` YAML emitter (``approvals.deny`` /
        ``command_allowlist`` lowering, event-trimming, VALID_HOOKS
        validation, per-tenant K6/K7 toggles) is P2
        (``policy/hermes_yaml_emitter.py``, design Section 5). For P1 this
        returns the ``ManagedConfigBundle`` shell with the matcher-less
        gate-hook wiring the gate needs on every governed event, so the
        driver satisfies the trait and the emit path is exercisable.

        TODO(P2): replace this inline stub with
        ``policy.hermes_yaml_emitter.compile_to_hermes_managed(ir)``,
        which produces the byte-stable, event-trimmed ``config.yaml`` +
        ``.env`` + context-template sidecars (design Section 5.1-5.4).
        """
        # Matcher-less gate hooks on the always-on enforcement events, so
        # the gate sees every tool call regardless of the policy vocabulary
        # (design Section 5.1). Event-trimming for policy-hosted events
        # (pre_verify / pre_llm_call) is deferred to the P2 emitter.
        gate_cmd = "/usr/local/bin/magi-cp gate --runtime hermes"
        config_yaml = (
            "# MAGI-CP MANAGED LAYER v1. Generated; do not hand-edit.\n"
            "# TODO(P2): produced by hermes_yaml_emitter, event-trimmed.\n"
            "hooks_auto_accept: true\n"
            "hooks:\n"
            "  pre_tool_call:\n"
            f"    - command: {json.dumps(gate_cmd)}\n"
            "      timeout: 30\n"
            "  post_tool_call:\n"
            f"    - command: {json.dumps(gate_cmd)}\n"
            "      timeout: 30\n"
        )
        env_file = "MAGI_CP_RUNTIME=hermes\nHERMES_ACCEPT_HOOKS=1\n"
        return ManagedConfigBundle(
            files={
                "config.yaml": config_yaml,
                ".env": env_file,
            },
            context_templates={},
        )

    # ── coverage ─────────────────────────────────────────────────────
    def coverage_report(self, ir: list[AnyPolicy]) -> CoverageReport:
        """Per-policy Hermes coverage via ``_coverage_status_for_hermes``.

        Mirrors ``codex.CodexDriver.coverage_report``: each policy reports
        ``enforced`` on the (verified in-process) hook path unless its
        archetype / (event, matcher) trips one of the Hermes ledger
        downgrades (design Section 6): ``hermes_no_ask_tier``,
        ``hermes_no_input_rewrite``, ``hermes_stop_edit_turns_only``,
        ``hermes_no_compact_event``, ``hermes_pre_tool_context_dropped``,
        ``hermes_prompt_block_plugin_only``, ``hermes_unmapped_tool``.
        """
        policies: list[CoveragePolicyStatus] = []
        for p in ir:
            status, downgrade = _coverage_status_for_hermes(p)
            policies.append(CoveragePolicyStatus(
                policy_id=p.id, status=status, downgrade=downgrade,
            ))
        return CoverageReport(
            runtime_id=self.runtime_id, policies=tuple(policies),
        )

    # ── install paths ────────────────────────────────────────────────
    def default_install_paths(self) -> InstallPaths:
        # Design Section 5.4 / 10 P1: /etc/hermes managed config, NO
        # slash-command dir in v1 (the in-Hermes /magi:pack surface is a
        # deferred nice-to-have, design Section 3.6). ``slash_commands_dir``
        # is left empty; the installer materializes context templates under
        # the managed dir.
        return InstallPaths(
            managed_config_dir="/etc/hermes",
            slash_commands_dir="",
            context_templates_dir="/etc/hermes/magi-cp/context-templates",
        )


def run_hermes_gate(raw_stripped: str) -> int:
    """Hermes runtime path for the gate dispatcher.

    Parses the Hermes stdin envelope, runs the SAME policy decision the CC
    path uses (``gate.decide``, one engine two surfaces), and emits the
    Hermes verdict envelope. Reachable when ``detect_runtime`` selects
    ``"hermes"`` (default-ON flag + a Hermes runtime signal); an explicit
    falsy ``MAGI_CP_HERMES_RUNTIME_ENABLED`` forces the CC path and makes
    this unreachable.

    Fail-closed contract (design Section 8.2): a malformed payload, or ANY
    exception raised inside policy evaluation, becomes a block verdict on
    stdout for the enforcement-scoped ``pre_tool_call`` event (Hermes exit
    codes never block, ``shell_hooks.py:513-520``, so the verdict MUST
    travel on stdout and the process always exits 0). Observe-only events
    fail silent (a block string on ``post_tool_call`` / lifecycle is
    ignored upstream, so silence avoids log noise).
    """
    driver = HermesDriver()
    if not raw_stripped:
        # No hook context — pass through silently, like the CC path.
        return 0
    try:
        event = driver.parse_hook_payload(raw_stripped.encode("utf-8"))
    except (json.JSONDecodeError, ValueError):
        # Malformed payload → fail-closed deny on the enforcement channel.
        out = driver.emit_verdict(Verdict(
            decision="deny",
            reason="malformed hook payload (json)",
            hook_event_name="PreToolUse",
        ))
        if out:
            sys.stdout.buffer.write(out)
        return 0

    # Reuse the CC decision engine (lazy import avoids an import cycle:
    # gate imports runtime only inside its dispatcher). ``decide`` keys on
    # the payload's ``hook_event_name`` / ``tool_input``, so it is fed the
    # CANONICAL event name + normalized tool the parse step produced (one
    # engine, two surfaces: the Hermes surface presents canonical shapes to
    # the engine, exactly as the Codex surface does).
    from ..local.gate import decide

    decide_payload = dict(event.raw)
    decide_payload["hook_event_name"] = event.hook_event_name
    decide_payload["tool_name"] = event.tool_name
    decide_payload["tool_input"] = event.tool_input
    decide_payload["session_id"] = event.session_id

    try:
        verdict = decide(decide_payload)
    except Exception as exc:  # noqa: BLE001 — fail-closed block-on-error.
        # Block-on-error at the outermost frame (design Section 8.2.2):
        # a policy-evaluation failure becomes a block on the enforcement
        # channel, silence elsewhere.
        if event.hook_event_name == "PreToolUse":
            out = driver.emit_verdict(Verdict(
                decision="deny",
                reason=f"magi-cp gate error (fail-closed): {type(exc).__name__}",
                hook_event_name="PreToolUse",
            ))
            if out:
                sys.stdout.buffer.write(out)
        return 0

    out = driver.emit_verdict(verdict)
    if out:
        sys.stdout.buffer.write(out)
    return 0


__all__ = ["HermesDriver", "run_hermes_gate"]
