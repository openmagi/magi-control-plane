"""PR-1: Codex event-level not-live coverage.

Promotes the F1 live-event set into code (``CODEX_LIVE_EVENTS``) and fixes
``_coverage_status_for`` over-reporting: a policy hosted on a lifecycle
event Codex never fires (Notification, TaskCreated, FileChanged, ...) used
to report ``enforced`` while enforcing zero times. It now reports the
``codex_event_not_live`` downgrade. Events that are live, shimmed, or
tool-silent-skipped keep their existing status verbatim.
"""
from __future__ import annotations

import pytest

from magi_cp.policy.ir import EvidencePolicy, EvidenceReq, Trigger
from magi_cp.runtime.codex import (
    CODEX_LIVE_EVENTS,
    _coverage_status_for,
)


def _evidence(pid: str, *, event: str, matcher: str = "*",
              action: str = "audit") -> EvidencePolicy:
    return EvidencePolicy(
        id=pid, description="t", version="0.1",
        trigger=Trigger(host="claude-code", event=event, matcher=matcher),
        sentinel_re=None,
        requires=[EvidenceReq(kind="step", step="privilege_scan",
                              verdict="pass")],
        action=action, on_signature_invalid="deny",
        gate_binary="/usr/local/bin/magi-gate.sh",
    )


# The F1 live-event set is exactly these ten. A regression that adds or
# drops a live event should trip this pin.
def test_codex_live_events_is_the_f1_set() -> None:
    assert CODEX_LIVE_EVENTS == frozenset({
        "PreToolUse", "PermissionRequest", "PostToolUse",
        "PreCompact", "PostCompact", "SessionStart", "UserPromptSubmit",
        "SubagentStart", "SubagentStop", "Stop",
    })


@pytest.mark.parametrize("event", [
    "Notification", "TaskCreated", "TaskCompleted", "FileChanged",
    "InstructionsLoaded", "CwdChanged", "Elicitation", "ElicitationResult",
    "MessageDisplay", "WorktreeCreate", "TeammateIdle",
])
def test_event_not_live_downgrades(event: str) -> None:
    status, downgrade = _coverage_status_for(_evidence("ev/x", event=event))
    assert status == "codex_event_not_live"
    assert downgrade is not None
    assert "never fires on Codex" in downgrade


@pytest.mark.parametrize("event", [
    "PreToolUse", "PostToolUse", "Stop", "PreCompact", "PostCompact",
    "SessionStart", "UserPromptSubmit",
])
def test_live_events_stay_enforced(event: str) -> None:
    # matcher="*" is not in the silent-skip set, so these are pure live
    # events with no shim in play → enforced.
    status, downgrade = _coverage_status_for(_evidence("ev/x", event=event))
    assert status == "enforced"
    assert downgrade is None


def test_existing_shims_unchanged() -> None:
    # SessionEnd rides Stop (Shim C), not the not-live catch.
    s, d = _coverage_status_for(_evidence("ev/se", event="SessionEnd"))
    assert s == "codex_no_session_end"
    # Subagent lifecycle keeps its own downgrade (Shim D), even though
    # SubagentStop IS in the live set.
    s, d = _coverage_status_for(_evidence("ev/ss", event="SubagentStop"))
    assert s == "codex_internal_subagent_gap"
    # PreToolUse on a read-family tool stays silent-skip (Shim A).
    s, d = _coverage_status_for(
        _evidence("ev/read", event="PreToolUse", matcher="Read"))
    assert s == "codex_silent_skip"
    # PreToolUse + Bash still enforced (translates to exec_command).
    s, d = _coverage_status_for(
        _evidence("ev/bash", event="PreToolUse", matcher="Bash",
                  action="block"))
    assert s == "enforced"
