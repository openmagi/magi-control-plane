"""Codex CLI runtime driver.

Design brief: docs/plans/2026-06-30-codex-runtime-adapter-design.md
(Section 2 wire format, Section 3 architecture, Section 4 gap shims,
Section 6 managed enforcement). P1 delivered the straight-through
parse / emit / requirements.toml wrap. P2 (this file) adds the four gap
shims:

  - Shim A (Section 4.1): PreToolUse tool-coverage silent-skip. Codex
    only fires ``PreToolUse`` for its shell tool ``exec_command`` (F4;
    ``unified_exec`` alias), ``apply_patch``, and MCP; ``coverage_report``
    marks any policy that targets a silent-skip tool + the emitter adds
    ``PermissionRequest`` + ``PostToolUse`` audit fallbacks.
  - Shim B (Section 4.2): PreToolUse ``additionalContext`` rejection.
    ``emit_verdict`` downgrades a turn-scope context to ``systemMessage``
    and defers a session-scope context to the next ``UserPromptSubmit``
    via a per-session queue file.
  - Shim C (Section 4.3): ``SessionEnd`` absence. ``parse_hook_payload``
    synthesizes a ``SessionEnd`` event from a ``Stop`` payload with a
    truthy ``stop_hook_active``; a cloud sweeper is the fallback.
  - Shim D (Section 4.4): subagent lifecycle fanout gap. ``coverage_report``
    marks subagent-lifecycle policies + the emitter adds belt-and-suspenders
    ``spawn_agent`` PreToolUse + PostToolUse mirror hooks.

``MAGI_CP_CODEX_RUNTIME_ENABLED`` is default-ON (2026-07-01 flip), so the
Codex path is reachable by default when a runtime signal selects it
(``MAGI_CP_RUNTIME=codex`` / payload sniff). A genuine Claude Code
invocation carries no such signal, so ``detect.detect_runtime`` still
returns ``"cc"`` and ``run_codex_gate`` is never entered on the CC path.
Setting the flag to an explicit falsy value forces ``"cc"`` unconditionally
(the kill switch).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import replace

from ..policy.codex_toml_emitter import compile_to_codex_requirements
from ..policy.ir import (
    AnyPolicy,
    ContextInjectionPolicy,
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


# Tools whose ``PreToolUse`` hook Codex silently skips (design doc 4.1;
# OpenAI issue #20204, open). A Magi policy whose PreToolUse matcher
# targets one of these fires ZERO times on Codex, so ``coverage_report``
# downgrades it and the emitter adds PermissionRequest + PostToolUse
# audit fallbacks instead of a false-confident "enforced".
#
# Magi policy matchers are Claude Code tool names — the IR validates them
# against the CC matcher grammar (``matrix.matcher_class_of``), so a
# Codex-native matcher (``list_dir``) is unauthorable today. The deny-list
# is therefore expressed primarily in CC tool names: the read / search /
# planning / todo tools that map onto Codex's silent-skip surface (Codex
# only fires PreToolUse for exec_command / apply_patch / MCP, which
# CC's Bash / Edit / Write / NotebookEdit / mcp__* map onto). The
# Codex-native aliases are kept in the set for forward-compat with any
# future Codex-native authoring path; they simply never appear on a
# policy today.
# NOTE on ``Task``: CC's single-subagent-spawn tool maps onto Codex's
# ``spawn_agent``, which IS a covered PreToolUse tool (design doc 4.4 —
# Shim D is premised on "parent-side PreToolUse hook on spawn_agent (which
# IS covered)"). The silent-skip enumeration (4.1) only lists the BATCH
# spawn (``spawn_agents_on_csv``), never the single spawn, so ``Task`` is
# deliberately NOT in this set: a PreToolUse policy matching ``Task`` fires
# on Codex and must not be shimmed (that would over-fire the fallback and
# contradict Shim D).
#
# TODO(live-test D3): confirm the exact silent-skip tool set against a
# real Codex install / issue #20204 PoC before dropping any fallback.
# matcher translation: the emitted managed-config matchers are authored as
# CC tool names, so they need translating to Codex tool names or the hook
# binds to nothing (the "false sense of coverage" failure mode 4.1 warns
# about). This IS built: ``translate_matcher_cc_to_codex`` /
# ``_CC_TO_CODEX_TOOL`` below, applied at both emit loops. The deny-list
# below stays expressed in CC names because Shim A/D reason in CC names
# BEFORE translation; only the final emitted matcher is translated.
# Read-family CC tools still have no 1:1 Codex tool and pass through inert
# (surfaced as a coverage downgrade, not silently claimed enforced).
# CONFIRMED (2026-07-01 live, §11.4 F4): Codex's shell tool is named
# ``exec_command`` (args ``{cmd, workdir, yield_time_ms}``), NOT ``Bash``.
# So the CC->Codex map must include Bash/Shell -> ``exec_command`` and
# apply_patch/MCP tool names; a ``matcher = "Bash"`` never fires on Codex.
CODEX_SILENT_SKIP_TOOLS: tuple[str, ...] = (
    # CC tool matchers that map onto Codex silent-skip tools.
    "AskUser",
    "BashOutput",
    "ExitPlanMode",
    "Glob",
    "Grep",
    "KillBash",
    "NotebookRead",
    "Read",
    "TodoWrite",
    "WebFetch",
    "WebSearch",
    # Codex-native aliases (forward-compat; unauthorable via the IR today).
    "list_dir",
    "spawn_agents_on_csv",
    "tool_search",
    "tool_suggest",
    "update_plan",
    "view_image",
    "web_search",
)

# The ``PreToolUse`` tool classes Codex DOES fire a hook for (design doc
# 4.1), expressed as the CC tool matchers that map onto them. Kept next
# to the skip set for documentation symmetry; the coverage / emitter
# logic keys off the skip set (deny-list) so an unknown future tool name
# defaults to "assumed covered" rather than a silent fallback explosion.
CODEX_PRETOOLUSE_COVERED_TOOLS: frozenset[str] = frozenset({
    # CC names.
    "Bash", "Edit", "Write", "MultiEdit", "NotebookEdit",
    # Codex-native names. ``exec_command`` is the CONFIRMED shell tool
    # (§11.4 F4, 2855 rollout function_calls); ``unified_exec`` is kept as a
    # documented alias in case a Codex build exposes it under that name.
    "exec_command", "unified_exec", "apply_patch",
})

# CC tool-name -> Codex tool-name translation for EMITTED hook matchers
# (§11.4 F4). A hook table's ``matcher`` must name a Codex tool or it fires
# ZERO times, the "false sense of coverage" failure mode 4.1 warns about.
# Only CONFIRMED mappings live here (identity for everything else):
#   - CONFIRMED live 2026-07-01 from real rollout ``function_call`` names:
#     ``exec_command`` (2855x, the shell tool), ``apply_patch`` (file
#     mutation), ``spawn_agent`` (multi_agent), ``update_plan``,
#     ``write_stdin``. Codex has NO discrete Read/Grep/Glob/Edit/Write
#     tool: reads run as ``exec_command`` sub-actions and edits as
#     ``apply_patch``.
# So CC ``Bash`` -> ``exec_command`` and CC ``Edit``/``Write``/``MultiEdit``
# -> ``apply_patch``. Read-family CC matchers (``Read``/``Grep``/``Glob``/
# ...) have no 1:1 Codex tool and pass through unchanged; they stay inert
# on Codex, which ``coverage_report`` already surfaces as a downgrade
# (design doc §14 "Costs"). ``spawn_agent``/``apply_patch``/``exec_command``
# and any already-Codex name are identity. Regex/alternation matchers
# (``Edit|Write``) and the empty all-tools matcher pass through unchanged;
# translating those is a documented follow-up.
_CC_TO_CODEX_TOOL: dict[str, str] = {
    "Bash": "exec_command",
    # Every CC file-mutation tool lowers to Codex's single ``apply_patch``
    # tool. NotebookEdit is included so it does not stay in
    # CODEX_PRETOOLUSE_COVERED_TOOLS reporting "enforced" while emitting a
    # ``matcher = "NotebookEdit"`` that fires zero times (Codex has no
    # notebook tool; .ipynb edits ride apply_patch).
    "Edit": "apply_patch",
    "Write": "apply_patch",
    "MultiEdit": "apply_patch",
    "NotebookEdit": "apply_patch",
    # CC's single-subagent-spawn tool ``Task`` maps onto Codex's
    # ``spawn_agent`` (design doc 4.4, a covered PreToolUse tool). Without
    # this, a ``matcher = "Task"`` table never fires on Codex (false
    # coverage). ``spawn_agent`` itself is unauthorable via the IR (rejected
    # by the CC matcher grammar); it reaches the emitter only through Shim
    # D's internal mirror, where it translates identity.
    "Task": "spawn_agent",
}


def translate_matcher_cc_to_codex(matcher: str) -> str:
    """Map a Claude Code tool-name matcher to its confirmed Codex tool
    name for hook emission. See ``_CC_TO_CODEX_TOOL``.

    Also handles a SIMPLE ALTERNATION of bare tool names (``Edit|Write``):
    each token is translated and the result deduped + sorted, so a
    translatable CC tool inside an alternation still binds to its Codex
    tool instead of firing zero times (the alternation form of the F4
    "false coverage" hole). Only alternations whose every token is a bare
    identifier are rewritten; a genuine regex (``mcp__.*``, ``Bash.*``,
    ``.*``) or the empty all-tools matcher passes through unchanged, as do
    already-Codex names and read-family CC tools with no 1:1 Codex tool.
    """
    if matcher in _CC_TO_CODEX_TOOL:
        return _CC_TO_CODEX_TOOL[matcher]
    if "|" in matcher:
        tokens = matcher.split("|")
        if all(t.isidentifier() for t in tokens):
            translated = {_CC_TO_CODEX_TOOL.get(t, t) for t in tokens}
            return "|".join(sorted(translated))
    return matcher

# Subagent lifecycle events whose hook fanout may not fire on Codex's
# internal reviewers (design doc 4.4). A policy triggered on one of these
# gets the ``codex_internal_subagent_gap`` marker + the emitter's
# parent-side ``spawn_agent`` mirror hooks.
_SUBAGENT_LIFECYCLE_EVENTS: frozenset[str] = frozenset({
    "SubagentStart", "SubagentStop",
})


# Events whose Codex channel reads top-level ``{"decision": "block",
# "reason": ...}`` (retry-feedback), same split as CC's PostToolUse*
# channel plus UserPromptSubmit (which Codex documents as accepting
# ``decision: "block"``). See design doc Section 2.2.
# TODO(block-channel event set): confirm the exact block-channel event set
# against a real Codex install; PostToolUse post-hoc block is documented.
# (This marker was mislabeled "D5", D5 is transcript_path, now RESOLVED:
# §11.4 F7 = Codex rollout JSONL, separate reader.) Live event set (F1):
# PreToolUse, PreToolUsePermissionRequest, PostToolUse, PreCompact,
# PostCompact, SessionStart, UserPromptSubmit, SubagentStart, SubagentStop,
# Stop, no Notification/SessionEnd, so SessionEnd-hosted logic below must
# ride Stop.
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


# ── Shim B queue helpers (design doc 4.2) ────────────────────────────
import re as _re


def _safe_session_component(session_id: str) -> str:
    """Filesystem-safe slug for a session id so a crafted id can't escape
    the per-session state dir. Codex session ids are uuidv4 in practice,
    but the gate must never trust a payload field with a path. Any char
    outside ``[A-Za-z0-9._-]`` collapses to ``_`` (so no ``/`` survives),
    and a dotted / empty residue falls back to a constant bucket."""
    slug = _re.sub(r"[^A-Za-z0-9._\-]", "_", session_id or "").strip(".")
    return slug or "_unscoped"


def _state_dir(session_id: str) -> str:
    """Per-session state dir root. ``MAGI_CP_STATE_DIR`` overrides the
    ``~/.magi-cp/state`` default (tests point it at a tmp dir)."""
    root = os.environ.get(
        "MAGI_CP_STATE_DIR", os.path.expanduser("~/.magi-cp/state"),
    )
    return os.path.join(root, _safe_session_component(session_id))


def _pending_context_path(session_id: str) -> str:
    return os.path.join(_state_dir(session_id), "pending_context.jsonl")


def _join_context(existing: str | None, drained: list[str]) -> str:
    """Merge a verdict's existing ``additional_context`` with the drained
    deferred entries, newest-appended-last, dropping empties."""
    parts = [p for p in ([existing] if existing else []) + drained if p]
    return "\n".join(parts)


def _join_system_message(existing: str | None, extra: str) -> str:
    """Append the downgraded context onto any existing systemMessage."""
    return f"{existing}\n{extra}" if existing else extra


# ── coverage helpers (design doc 4.1 / 4.2 / 4.3 / 4.4) ──────────────
def _policy_event_matcher(p: AnyPolicy) -> tuple[str, str]:
    """(event, matcher) for a hook-producing policy. Native-surface
    archetypes (Permission / Mcp / Subagent) have no trigger and are
    handled before this is called."""
    if isinstance(p, ContextInjectionPolicy):
        return (p.event, p.matcher)
    trig = getattr(p, "trigger", None)
    if trig is not None:
        return (getattr(trig, "event", ""), getattr(trig, "matcher", ""))
    return ("", "")


def _coverage_status_for(p: AnyPolicy) -> tuple[str, str | None]:
    """Per-policy Codex coverage ``(status, downgrade)``.

    Native-surface archetypes are now lowered (design 2026-07-01):
    ``PermissionPolicy`` routes to a Codex permission profile (filesystem /
    network) or a ``requirements.toml`` ``prefix_rule`` (command), so it
    reports a real status via ``permission_native_status``. ``McpGatingPolicy``
    has no native profile expression (per the permissions docs) and stays on
    the hook path. ``SubagentPolicy`` rides ``features.multi_agent`` + the
    ``spawn_agent`` mirror hook. The gap-shim markers key off (event, matcher).
    """
    if isinstance(p, PermissionPolicy):
        from ..policy.codex_toml_emitter import permission_native_status
        return permission_native_status(p)
    if isinstance(p, McpGatingPolicy):
        # MCP tool gating is not expressible as a permission profile; it
        # stays on the hook path (design 2.3).
        return ("codex_no_native_mcp_profile", "hook PreToolUse on the mcp tool")
    if isinstance(p, SubagentPolicy):
        return ("codex_subagent_multi_agent",
                "features.multi_agent + spawn_agent PreToolUse hook")
    event, matcher = _policy_event_matcher(p)
    # Shim D: subagent lifecycle fanout may miss Codex internal reviewers.
    if event in _SUBAGENT_LIFECYCLE_EVENTS:
        return (
            "codex_internal_subagent_gap",
            "spawn_agent PreToolUse+PostToolUse mirror",
        )
    # Shim C: Codex has no SessionEnd event.
    if event == "SessionEnd":
        return ("codex_no_session_end", "Stop stop_hook_active + cloud sweeper")
    # Shim A: PreToolUse silent-skip tools fire zero hooks on Codex. The
    # PermissionRequest+PostToolUse audit fallbacks are emitted, but their
    # matcher is a read-family CC tool name with no 1:1 Codex tool
    # (§11.4 F4), so the fallback is itself INERT on Codex until that
    # mapping is confirmed. Report the downgrade honestly rather than
    # implying working audit coverage.
    if event == "PreToolUse" and matcher in CODEX_SILENT_SKIP_TOOLS:
        return (
            "codex_silent_skip",
            "PermissionRequest+PostToolUse audit fallback "
            "(inert: no 1:1 Codex tool name)",
        )
    # Shim B: additionalContext on PreToolUse is rejected; a
    # ContextInjection archetype compiles to the weaker systemMessage
    # channel. context_scope (turn vs session) is a runtime Verdict
    # input, so authoring-time coverage reports the turn-scope default.
    # TODO(live-test D2): a session-scope injection downgrades to
    # "deferred_to_prompt" at emit time instead.
    # TODO(live-test/P2, missing producer): this reports ``enforced`` with
    # a ``system_message`` downgrade, but the downgrade is NOT yet
    # exercised at verdict time. ``local.gate.decide`` never emits a
    # ContextInjection ``additional_context`` verdict on either runtime, so
    # ``_apply_context_shim``'s PreToolUse rewrite only runs for
    # directly-constructed Verdicts in the shim tests — there is no
    # production producer feeding it. The status is aspirational until a
    # verdict-time ContextInjection producer is wired into ``decide()``.
    if event == "PreToolUse" and isinstance(p, ContextInjectionPolicy):
        return ("enforced", "system_message")
    return ("enforced", None)


class CodexDriver:
    """Codex CLI ``HookRuntime`` implementation."""

    runtime_id: str = "codex"

    # ── stdin -> canonical event ─────────────────────────────────────
    def parse_hook_payload(self, raw_stdin: bytes) -> HookEvent:
        """Decode Codex's stdin hook JSON into a canonical ``HookEvent``.

        Codex's envelope is near-identical to CC's plus ``turn_id`` and
        ``matcher_aliases`` (design doc Section 2.2). A blank stdin
        decodes to an empty-``raw`` event (pass-through).

        Shim C (design doc 4.3): Codex has no ``SessionEnd`` event. A
        ``Stop`` payload carrying a truthy ``stop_hook_active`` is treated
        as end-of-session and synthesized into a canonical ``SessionEnd``
        event so ``SessionEnd``-hosted policies (evidence flush, ledger
        commit, sticky-pack deactivate) still fire. ``raw`` keeps the
        original ``Stop`` payload verbatim.
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
        event_name = payload.get("hook_event_name") or "PreToolUse"
        # Shim C: Stop + stop_hook_active -> synthetic SessionEnd.
        # TODO(live-test D1): confirm stop_hook_active reliably signals
        # end-of-session; the cloud sweeper is the fallback when it does
        # not fire.
        if event_name == "Stop" and payload.get("stop_hook_active"):
            event_name = "SessionEnd"
        return HookEvent(
            hook_event_name=event_name,
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

        Shim B (design doc 4.2) runs first: any ``additional_context`` on
        a ``PreToolUse`` verdict is downgraded (turn -> systemMessage,
        session -> deferred queue) because Codex rejects
        ``additionalContext`` there (OpenAI issue #19385); a
        ``UserPromptSubmit`` verdict drains the deferred queue and folds
        it back into ``additionalContext`` (which Codex accepts there).
        """
        verdict = self._apply_context_shim(verdict)
        obj = merge_verdict_side_channels(self._verdict_obj(verdict), verdict)
        if obj is None:
            return b""
        return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")

    # ── Shim B: additionalContext channel reconciliation ─────────────
    def _apply_context_shim(self, verdict: Verdict) -> Verdict:
        """Reconcile ``additional_context`` with Codex's channel rules.

        On ``UserPromptSubmit`` (Codex accepts ``additionalContext``)
        drain any context deferred from an earlier session-scope
        ``PreToolUse`` verdict and fold it in. On ``PreToolUse`` (Codex
        rejects ``additionalContext``) downgrade: ``context_scope ==
        "session"`` queues for the next prompt and emits nothing;
        anything else (``"turn"`` / unspecified) rewrites to the
        strictly-weaker ``systemMessage`` channel on the same event.
        """
        event = verdict.hook_event_name or "PreToolUse"

        # Step 2: drain the deferred queue on the next UserPromptSubmit.
        if event == "UserPromptSubmit" and verdict.session_id:
            drained = self._drain_pending_context(verdict.session_id)
            if drained:
                verdict = replace(
                    verdict,
                    additional_context=_join_context(
                        verdict.additional_context, drained,
                    ),
                )

        # Step 1: downgrade additionalContext on PreToolUse.
        if event == "PreToolUse" and verdict.additional_context is not None:
            if verdict.context_scope == "session":
                if verdict.session_id:
                    self._queue_pending_context(
                        verdict.session_id, verdict.additional_context,
                    )
                # Deferred to the next UserPromptSubmit; emit nothing now.
                verdict = replace(verdict, additional_context=None)
            else:
                # turn scope (default): rewrite to systemMessage.
                verdict = replace(
                    verdict,
                    additional_context=None,
                    system_message=_join_system_message(
                        verdict.system_message, verdict.additional_context,
                    ),
                )
        return verdict

    @staticmethod
    def _queue_pending_context(session_id: str, context: str) -> None:
        """Append a session-scope context to the per-session queue file.

        Hardened to the repo's uniform trust-file bar (mirrors
        ``local.session_cache`` / ``gate`` / ``keys``): the per-session dir
        is created at mode 0o700 and the queue is opened
        ``O_CREAT|O_APPEND|O_NOFOLLOW`` at 0o600, so the injected-context
        payload is never group/world readable on a shared host and a
        pre-planted symlink under a misconfigured ``MAGI_CP_STATE_DIR``
        cannot redirect the append. The whole JSON line is written in a
        single ``os.write`` under ``O_APPEND`` so concurrent writers append
        atomically (no interleaved partial lines) rather than through a
        buffered writer that could split the record.

        Best-effort: a queue write failure must not crash the gate."""
        try:
            os.makedirs(_state_dir(session_id), mode=0o700, exist_ok=True)
            payload = (
                json.dumps({"context": context}, ensure_ascii=False) + "\n"
            ).encode("utf-8")
            fd = os.open(
                _pending_context_path(session_id),
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                0o600,
            )
            try:
                os.write(fd, payload)
            finally:
                os.close(fd)
        except OSError:
            pass

    @staticmethod
    def _drain_pending_context(session_id: str) -> list[str]:
        """Atomically claim + drain this session's deferred-context queue.

        Returns the queued context strings oldest-first; ``[]`` when empty
        or unreadable.

        The claim is an ``os.rename`` of the live queue to a unique private
        name BEFORE reading, which closes two races the old read-then-remove
        had: (1) a concurrent ``_queue_pending_context`` append landing
        between a plain read and a separate ``os.remove`` was silently lost;
        (2) two concurrent drains both reading the same lines re-injected
        the same context twice. Rename is atomic, so exactly one caller wins
        the claim (the loser's rename gets ``ENOENT`` -> ``[]``), any append
        after the claim lands on a fresh queue file and is drained next time,
        and the drain stays single-shot. The claimed file is opened
        ``O_NOFOLLOW`` for symlink parity with the writer."""
        path = _pending_context_path(session_id)
        claim = f"{path}.drain-{os.getpid()}-{os.urandom(8).hex()}"
        try:
            os.rename(path, claim)
        except OSError:
            # Nothing queued, or another drain already claimed it.
            return []
        try:
            fd = os.open(claim, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                with os.fdopen(fd, encoding="utf-8") as f:
                    lines = f.readlines()
            except OSError:
                os.close(fd)
                lines = []
        except OSError:
            lines = []
        finally:
            try:
                os.remove(claim)
            except OSError:
                pass
        out: list[str] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ctx = rec.get("context")
            if isinstance(ctx, str) and ctx:
                out.append(ctx)
        return out

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
            if verdict.additional_context is not None:
                # Reachable on an accepting event (UserPromptSubmit /
                # PostToolUse). PreToolUse additionalContext was already
                # downgraded away by ``_apply_context_shim`` above.
                return {
                    "hookSpecificOutput": {
                        "hookEventName": event,
                        "additionalContext": verdict.additional_context,
                    }
                }
            # Silent allow.
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
        """Per-policy Codex coverage, with the four P2 gap-shim markers.

        Hook-producing archetypes (Evidence / InputRewrite / RunCommand /
        ContextInjection) report ``"enforced"`` unless their (event,
        matcher) trips a gap shim:

          - ``codex_silent_skip`` (Shim A): PreToolUse on a silent-skip
            tool; downgraded to a PermissionRequest + PostToolUse audit.
          - ``codex_no_session_end`` (Shim C): SessionEnd-hosted policy;
            downgraded to Stop + stop_hook_active + the cloud sweeper.
          - ``codex_internal_subagent_gap`` (Shim D): subagent-lifecycle
            policy; downgraded to parent-side spawn_agent mirror hooks.
          - an ``enforced`` policy may still carry a ``system_message``
            downgrade (Shim B) when a ContextInjection on PreToolUse loses
            its ``additionalContext`` channel.

        Native-surface archetypes now lower (design 2026-07-01):
        ``PermissionPolicy`` reports a real status via
        ``permission_native_status`` (``enforced`` for command/file/network
        rules; a hook downgrade for fs/net ``ask``). ``McpGatingPolicy`` has
        no native profile expression (hook path). ``SubagentPolicy`` rides
        ``features.multi_agent`` + the ``spawn_agent`` hook. See
        ``_coverage_status_for``.
        """
        policies: list[CoveragePolicyStatus] = []
        for p in ir:
            status, downgrade = _coverage_status_for(p)
            policies.append(CoveragePolicyStatus(
                policy_id=p.id, status=status, downgrade=downgrade,
            ))
        return CoverageReport(
            runtime_id=self.runtime_id, policies=tuple(policies),
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
    path uses (``gate.decide``, one engine two surfaces), and emits the
    Codex verdict envelope. Reachable when ``detect_runtime`` selects
    ``"codex"`` (default-ON flag + a Codex runtime signal); an explicit
    falsy ``MAGI_CP_CODEX_RUNTIME_ENABLED`` forces the CC path and makes
    this unreachable.
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
