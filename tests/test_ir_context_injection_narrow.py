"""D59 — `_CONTEXT_EVENT_LITERALS` narrowing for ContextInjectionPolicy.

D58 widened ContextInjectionPolicy.event to all 30 CC hook events
because the bundled CC binary's hookSpecificOutput JSON schema accepts
`additionalContext` on every hook. D59 surfaces an asymmetry the D58
review caught: four hooks have a SPECIALIZED hookSpecificOutput shape
where `additionalContext` is silently ignored at runtime ("Hook JSON
output had unrecognized keys (ignored)" in the CC binary):

  - Elicitation        — uses hookSpecificOutput.elicitationDecision
                         (accept / decline an MCP elicitation request).
  - ElicitationResult  — uses hookSpecificOutput to override the action
                         or content BEFORE the response is sent to the
                         MCP server.
  - WorktreeCreate     — uses hookSpecificOutput.worktreePath (the gate
                         returns a worktree path).
  - MessageDisplay     — display-only; replaces the delta on screen
                         without changing the stored message or
                         feeding back into the model context.

Authoring a ContextInjectionPolicy on any of these would compile and
persist cleanly, then no-op at runtime with zero operator-visible
feedback — the silent fail-open the matrix gate exists to prevent.

EvidencePolicy (audit-only) stays legal on all 30 events because
audit just records the trigger firing — it does not need
`additionalContext` at all.

Tests in this module pin the asymmetry so a future widen-back to 30
is intentional, not incidental, and lock the error message wording so
the dashboard's flash redirect path can surface a useful message.
"""
from __future__ import annotations

import pytest

from magi_cp.policy.ir import (
    ContextInjectionPolicy, EvidencePolicy, Trigger,
    _CONTEXT_EVENT_LITERALS, _CONTEXT_INJECTION_EXCLUDED_EVENTS,
    _CONTEXT_INJECTION_ALTERNATE_CHANNEL, _SUPPORTED_EVENTS,
)


# ── narrowing invariants ─────────────────────────────────────────────


def test_context_event_literals_excludes_specialized_and_end_of_life_hooks():
    """The narrowed set is _SUPPORTED_EVENTS minus eight hooks:
    four whose hookSpecificOutput shape uses a different channel
    (D59 specialized-channel set) plus four whose timing has no
    downstream same-session model turn for additionalContext to land
    in (D70 end-of-life set)."""
    assert _CONTEXT_INJECTION_EXCLUDED_EVENTS == frozenset({
        # D59 — specialized hookSpecificOutput shape
        "Elicitation", "ElicitationResult",
        "WorktreeCreate", "MessageDisplay",
        # D70 — end-of-life events with no downstream same-session turn
        "Stop", "StopFailure", "SessionEnd", "SubagentStop",
    })
    expected = _SUPPORTED_EVENTS - _CONTEXT_INJECTION_EXCLUDED_EVENTS
    assert set(_CONTEXT_EVENT_LITERALS) == expected
    # Sanity: the narrowed surface is 30 - 8 = 22 events.
    assert len(_CONTEXT_EVENT_LITERALS) == 22
    # And the full surface is 30 (locks the matrix asymmetry: 30 / 22).
    assert len(_SUPPORTED_EVENTS) == 30


def test_alternate_channel_message_names_every_excluded_event():
    """Every excluded event has a per-event description of the
    alternate hookSpecificOutput channel so the ValueError tells the
    operator where to look next."""
    assert (
        set(_CONTEXT_INJECTION_ALTERNATE_CHANNEL.keys())
        == _CONTEXT_INJECTION_EXCLUDED_EVENTS
    )
    # Lock the key tokens that should appear in the message so a future
    # rewording can't silently lose the wire-shape signal.
    assert "elicitationDecision" in _CONTEXT_INJECTION_ALTERNATE_CHANNEL[
        "Elicitation"
    ]
    assert "response is sent" in _CONTEXT_INJECTION_ALTERNATE_CHANNEL[
        "ElicitationResult"
    ]
    assert "worktreePath" in _CONTEXT_INJECTION_ALTERNATE_CHANNEL[
        "WorktreeCreate"
    ]
    assert "display-only" in _CONTEXT_INJECTION_ALTERNATE_CHANNEL[
        "MessageDisplay"
    ]
    # D59 follow-up (#3, code-style): no em-dash in any channel
    # description. The strings splice into the user-facing ValueError
    # and reach the dashboard's flash redirect path verbatim.
    for ev, channel in _CONTEXT_INJECTION_ALTERNATE_CHANNEL.items():
        assert "—" not in channel, (
            f"channel description for {ev!r} carries an em-dash: "
            f"{channel!r}"
        )


# ── ContextInjectionPolicy refusal per excluded event ───────────────


def test_context_injection_refuses_elicitation():
    with pytest.raises(ValueError, match="elicitationDecision") as ei:
        ContextInjectionPolicy(
            id="ctx-elicit/v1",
            description="should be refused",
            event="Elicitation",  # type: ignore[arg-type]
            template="hello",
        )
    # The error must name the event AND the alternate channel so the
    # operator knows where to pivot. EvidencePolicy audit is mentioned
    # as the fallback that still works on this event.
    msg = str(ei.value)
    assert "'Elicitation'" in msg
    assert "EvidencePolicy" in msg
    assert "additionalContext" in msg
    # D59 follow-up (#10): the error must ALSO name the
    # alternate-hook recovery path so a direct REST PUT or persisted-
    # dict load (no dashboard tooltip in the loop) gets both options.
    assert "PreToolUse" in msg
    assert "SessionStart" in msg
    assert "UserPromptSubmit" in msg
    # D59 follow-up (code-style): the user-facing error string must
    # carry no em-dash. Em-dashes are banned per CLAUDE.md hard rule.
    assert "—" not in msg


def test_context_injection_refuses_elicitation_result():
    with pytest.raises(ValueError, match="response is sent") as ei:
        ContextInjectionPolicy(
            id="ctx-elicit-result/v1",
            description="should be refused",
            event="ElicitationResult",  # type: ignore[arg-type]
            template="hello",
        )
    msg = str(ei.value)
    assert "'ElicitationResult'" in msg
    assert "EvidencePolicy" in msg
    # D59 follow-up (#8): the alternate-channel description must read
    # as a noun phrase so the spliced sentence "this hook uses
    # <channel>, not additionalContext" stays grammatical. Pin the
    # current noun-phrase token ("action / content override") so a
    # future rewording can't slip back to a sentence fragment that
    # produces two verbs back-to-back.
    assert "action / content override" in msg
    # No em-dash (CLAUDE.md hard rule).
    assert "—" not in msg


def test_context_injection_refuses_worktree_create():
    with pytest.raises(ValueError, match="worktreePath") as ei:
        ContextInjectionPolicy(
            id="ctx-worktree/v1",
            description="should be refused",
            event="WorktreeCreate",  # type: ignore[arg-type]
            template="hello",
        )
    msg = str(ei.value)
    assert "'WorktreeCreate'" in msg
    assert "EvidencePolicy" in msg
    assert "additionalContext" in msg
    assert "—" not in msg


def test_context_injection_refuses_message_display():
    with pytest.raises(ValueError, match="display-only") as ei:
        ContextInjectionPolicy(
            id="ctx-message-display/v1",
            description="should be refused",
            event="MessageDisplay",  # type: ignore[arg-type]
            template="hello",
        )
    msg = str(ei.value)
    assert "'MessageDisplay'" in msg
    assert "EvidencePolicy" in msg
    # D59 follow-up (#9): the MessageDisplay channel is a positive
    # "no model-context channel" statement, not an adjective. Pin the
    # token so a future rewording cannot revert to a dangling
    # "uses display-only" fragment (the spliced sentence would
    # otherwise read as: "this hook uses display-only ..., not
    # additionalContext" which doesn't tell the operator what to do
    # next).
    assert "no model-context channel" in msg
    # No em-dash (CLAUDE.md hard rule).
    assert "—" not in msg


# ── 22 still-legal events ───────────────────────────────────────────


def test_context_injection_still_accepts_the_other_22_events():
    """Every event in `_CONTEXT_EVENT_LITERALS` must still construct
    cleanly. We pick a benign matcher per event-family because
    `ContextInjectionPolicy.validate()` also runs a matrix-coherence
    gate that forbids per-tool matchers on no-tool-context events.
    """
    tool_context = {"PreToolUse", "PostToolUse",
                    "PostToolUseFailure", "PostToolBatch"}
    constructed = 0
    for ev in _CONTEXT_EVENT_LITERALS:
        matcher = "Bash" if ev in tool_context else "*"
        p = ContextInjectionPolicy(
            id=f"ctx-{ev.lower()}/v1",
            description=f"context on {ev}",
            event=ev,  # type: ignore[arg-type]
            matcher=matcher,
            template=f"hello from {ev}",
        )
        assert p.event == ev
        constructed += 1
    assert constructed == 22


# ── asymmetry: EvidencePolicy still works on all 30 events ──────────


def test_evidence_policy_audit_still_legal_on_elicitation():
    """The asymmetry test: ContextInjectionPolicy is narrowed to 22,
    but EvidencePolicy (audit-only) keeps all 30 events. Audit just
    records the trigger firing, so it does not need
    `additionalContext` at all — the matrix.LEGAL_COMBINATIONS table
    keeps the eight excluded events for the audit archetype.
    """
    p = EvidencePolicy(
        id="elicit-audit/v1",
        description="record every elicitation",
        trigger=Trigger(event="Elicitation", matcher="*"),
        sentinel_re=None,
        requires=[],
        action="audit",
    )
    assert p.trigger.event == "Elicitation"
    assert p.action == "audit"


def test_evidence_policy_audit_still_legal_on_the_other_seven_excluded():
    """ElicitationResult / WorktreeCreate / MessageDisplay (D59) plus
    Stop / StopFailure / SessionEnd / SubagentStop (D70) all keep the
    audit archetype legal — the narrowing is per-archetype, not
    per-event."""
    excluded_minus_elicitation = (
        "ElicitationResult", "WorktreeCreate", "MessageDisplay",
        "Stop", "StopFailure", "SessionEnd", "SubagentStop",
    )
    for ev in excluded_minus_elicitation:
        p = EvidencePolicy(
            id=f"{ev.lower()}-audit/v1",
            description=f"record every {ev}",
            trigger=Trigger(event=ev, matcher="*"),
            sentinel_re=None,
            requires=[],
            action="audit",
        )
        assert p.trigger.event == ev
        assert p.action == "audit"


# ── D70: end-of-life events refused with the alternate-channel error ─


@pytest.mark.parametrize("event,expected_token", [
    ("Stop",         "no downstream same-session model turn"),
    ("StopFailure",  "no downstream same-session model turn"),
    ("SessionEnd",   "no downstream same-session model turn"),
    ("SubagentStop", "no downstream same-session model turn"),
])
def test_d70_end_of_life_events_refused_with_named_reason(event, expected_token):
    """D70: end-of-life events take the alternate-channel branch with
    a reason-named token so the operator knows why the channel does
    not apply (different from the D59 specialized-channel branch,
    which names hookSpecificOutput fields)."""
    with pytest.raises(ValueError) as ei:
        ContextInjectionPolicy(
            id=f"ctx-{event.lower()}/v1",
            description="should be refused at end-of-life",
            event=event,  # type: ignore[arg-type]
            template="hello",
        )
    msg = str(ei.value)
    assert f"'{event}'" in msg
    assert expected_token in msg
    # EvidencePolicy audit is still the operator's fallback for these.
    assert "EvidencePolicy" in msg
    # No em-dash per CLAUDE.md hard rule.
    assert "—" not in msg


def test_d70_subagent_stop_message_points_at_subagent_start():
    """SubagentStop's reason copy specifically recommends SubagentStart
    for parent-side carry-over so the operator has a concrete pivot
    without re-reading the docs."""
    with pytest.raises(ValueError) as ei:
        ContextInjectionPolicy(
            id="ctx-subagent-stop/v1",
            description="should redirect to SubagentStart",
            event="SubagentStop",  # type: ignore[arg-type]
            template="hello",
        )
    msg = str(ei.value)
    assert "SubagentStart" in msg


# ── unknown event name still raises the original error ─────────────


def test_unknown_event_raises_original_unrecognized_error():
    """An outright-bogus event name takes the original
    "not a recognized CC hook" branch, NOT the D59 alternate-channel
    branch — so the dashboard's flash mapping for invalid hook names
    stays byte-identical."""
    with pytest.raises(ValueError, match="not a recognized CC hook"):
        ContextInjectionPolicy(
            id="ctx-bogus/v1",
            description="bogus event",
            event="NotARealHook",  # type: ignore[arg-type]
            template="hello",
        )
