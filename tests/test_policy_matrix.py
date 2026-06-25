"""v1-P1 — Policy IR _LEGAL matrix validation.

Pattern from magi-agent customize/custom_rules.py::_LEGAL: declare a tabular
matrix of allowed (trigger.event × trigger.matcher-class × on_missing) triples
so a future builder UI can dropdown the legal combinations and the IR loader
rejects illegal ones with a clear message.

magi-agent's matrix sat in-loop; ours sits at IR validation time. Same shape,
different home.
"""
import pytest

from magi_cp.policy.matrix import (
    LEGAL_COMBINATIONS, MatcherClass,
    matcher_class_of, validate_combination, supported_events,
)


# ── matcher_class_of: classifies a matcher string into a known class ──
@pytest.mark.parametrize("matcher,kls", [
    ("Bash", MatcherClass.tool),
    ("Read", MatcherClass.tool),
    ("Edit", MatcherClass.tool),
    ("Write", MatcherClass.tool),
    ("mcp__court__file", MatcherClass.mcp_tool),
    ("mcp__magi__verify_citations", MatcherClass.mcp_tool),
    ("*", MatcherClass.wildcard),
    ("Bash|Edit", MatcherClass.tool_alt),
])
def test_matcher_class_known(matcher, kls):
    assert matcher_class_of(matcher) is kls


def test_matcher_class_unknown_raises():
    with pytest.raises(ValueError, match="unknown matcher class"):
        matcher_class_of("FooBar")


# ── supported_events declared explicitly ─────────────────────────────
def test_supported_events_contains_v0_events():
    s = supported_events()
    assert "PreToolUse" in s
    assert "PostToolUse" in s
    assert "Stop" in s


# ── legal combinations enumerated ────────────────────────────────────
def test_legal_combinations_includes_v0_legal_filing():
    """The v0 hard-coded policy (PreToolUse × Bash × block) must be legal."""
    assert ("PreToolUse", MatcherClass.tool, "block") in LEGAL_COMBINATIONS


def test_legal_combinations_includes_mcp_tool():
    assert ("PreToolUse", MatcherClass.mcp_tool, "block") in LEGAL_COMBINATIONS


def test_validate_combination_accepts_legal():
    validate_combination("PreToolUse", "Bash", "block")
    validate_combination("PreToolUse", "mcp__court__file", "block")


def test_validate_combination_rejects_illegal_event_matcher():
    """Stop + tool matcher is meaningless (Stop has no tool context)."""
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination("Stop", "Bash", "audit")


def test_validate_combination_rejects_illegal_action():
    """PostToolUse cannot ask — by the time the tool ran there is no
    interactive surface to interrupt to. (D82d admits PostToolUse +
    block on per-tool matchers as the CC retry-feedback channel, so
    we pin a still-illegal action here.)"""
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination("PostToolUse", "Bash", "ask")


def test_validate_combination_unknown_matcher_clear_error():
    with pytest.raises(ValueError, match="unknown matcher class"):
        validate_combination("PreToolUse", "GhostTool", "block")


# ── pluggable: tests cover the pattern, not the exact contents ───────
def test_legal_combinations_are_tuples_of_three():
    for combo in LEGAL_COMBINATIONS:
        assert len(combo) == 3
        ev, kls, action = combo
        assert isinstance(ev, str)
        assert isinstance(kls, MatcherClass)
        # D57f-2 widened the action vocabulary to include input_rewrite
        # (PreToolUse only). D63 added run_command (legal on every
        # supported event). D69 added inject_context as a legal action
        # on the LEGAL_COMBINATIONS triples (26 of 30 events; the four
        # D59-excluded events route additionalContext through a
        # specialized hookSpecificOutput field instead). Earlier
        # rounds covered block / ask / audit.
        assert action in {
            "block", "ask", "audit", "input_rewrite", "run_command",
            "inject_context",
        }


def test_supported_events_covers_full_cc_surface():
    """D58: matrix supports the full CC hook surface (30 events as of
    CC 2.1.170; the doc still says 23 — the binary's `nV` enum is the
    truth source). Each name MUST appear unchanged; CC validates
    against this enum at managed-settings load time so a misspelled
    event would either be silently dropped (audit policy never fires)
    or reject the whole settings.json (gate goes fail-open until the
    operator notices)."""
    s = supported_events()
    expected = {
        # Tool-context family
        "PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch",
        # Permission gate family
        "PermissionRequest", "PermissionDenied",
        # Content-flow family
        "UserPromptSubmit", "UserPromptExpansion",
        "PreCompact", "PostCompact",
        "Elicitation", "ElicitationResult",
        # Subagent / Stop boundary family
        "SubagentStart", "SubagentStop",
        "Stop", "StopFailure",
        # Lifecycle / observability family
        "Setup", "Notification",
        "SessionStart", "SessionEnd",
        "TeammateIdle", "TaskCreated", "TaskCompleted",
        "ConfigChange",
        "WorktreeCreate", "WorktreeRemove",
        "InstructionsLoaded",
        "CwdChanged", "FileChanged",
        "MessageDisplay",
    }
    assert s == expected
    # And the pre-D58 8 events stay intact (back-compat).
    legacy_eight = {
        "PreToolUse", "PostToolUse",
        "Stop", "SubagentStop",
        "UserPromptSubmit",
        "PreCompact",
        "SessionStart", "SessionEnd",
    }
    assert legacy_eight.issubset(s)


@pytest.mark.parametrize("event,action", [
    ("UserPromptSubmit", "block"),  # block the prompt
    ("UserPromptSubmit", "ask"),    # interrupt for approval
    ("UserPromptSubmit", "audit"),  # log only
    ("PreCompact", "block"),        # protect the evidence chain
    ("PreCompact", "audit"),
    ("SubagentStop", "audit"),
    ("SessionStart", "audit"),
    ("SessionEnd", "audit"),
])
def test_validate_combination_accepts_no_tool_events_with_wildcard(event, action):
    """The 6 no-tool-context events require matcher='*' — wildcard is
    the only meaningful matcher class. validate_combination must
    accept those triples."""
    validate_combination(event, "*", action)


@pytest.mark.parametrize("event", [
    "UserPromptSubmit", "PreCompact",
    "SubagentStop", "SessionStart", "SessionEnd",
])
def test_no_tool_events_reject_tool_matcher(event):
    """A tool matcher on a no-tool-context event is meaningless and
    must be rejected by the IR loader before reaching the gate."""
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination(event, "Bash", "audit")


def test_session_end_cannot_block():
    """SessionEnd is observe-only — blocking a session that has already
    closed has no semantics."""
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination("SessionEnd", "*", "block")


def test_subagent_stop_cannot_block():
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination("SubagentStop", "*", "block")


def test_audit_is_legal_on_every_event():
    """D31: audit is the universal observe-only action."""
    for ev in supported_events():
        # Use the matcher class that the event supports
        matcher = "Bash" if ev in ("PreToolUse", "PostToolUse") else "*"
        validate_combination(ev, matcher, "audit")


# ── D58: full CC hook surface coverage ───────────────────────────────


@pytest.mark.parametrize("event,action", [
    # Gate-style pre-hooks the wizard surfaces with block/ask/audit.
    ("PermissionRequest", "block"),
    ("PermissionRequest", "ask"),
    ("PermissionRequest", "audit"),
    ("Elicitation", "block"),
    ("Elicitation", "ask"),
    ("Elicitation", "audit"),
])
def test_d58_pre_gate_events_accept_block_ask_audit(event, action):
    """PermissionRequest + Elicitation share the PreToolUse
    "override" channel (CC's hook stdout schema returns
    `{decision, updatedInput, additionalContext, continue}` on every
    hook); the wizard surfaces the full block/ask/audit set."""
    validate_combination(event, "*", action)


@pytest.mark.parametrize("event,action", [
    # Mid-process hooks: block is meaningful, ask has no interactive
    # surface to interrupt to (the prompt is already being expanded;
    # the compaction is already running).
    ("UserPromptExpansion", "block"),
    ("UserPromptExpansion", "audit"),
    ("PreCompact",          "block"),
    ("PreCompact",          "audit"),
])
def test_d58_block_audit_only_events(event, action):
    validate_combination(event, "*", action)


@pytest.mark.parametrize("event,action", [
    # ask is not legal on these (no interactive surface).
    ("UserPromptExpansion", "ask"),
    ("PreCompact",          "ask"),
])
def test_d58_block_audit_events_reject_ask(event, action):
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination(event, "*", action)


@pytest.mark.parametrize("event", [
    # The full audit-only wildcard surface.
    "PostToolUseFailure", "PostToolBatch",
    "PermissionDenied",
    "PostCompact", "ElicitationResult",
    "SubagentStart", "SubagentStop",
    "Stop", "StopFailure",
    "Setup", "Notification",
    "SessionStart", "SessionEnd",
    "TeammateIdle", "TaskCreated", "TaskCompleted",
    "ConfigChange",
    "WorktreeCreate", "WorktreeRemove",
    "InstructionsLoaded",
    "CwdChanged", "FileChanged",
    "MessageDisplay",
])
def test_d58_audit_only_events_legal(event):
    validate_combination(event, "*", "audit")


@pytest.mark.parametrize("event,bad_action", [
    # By policy intent, audit-only observability hooks cannot block.
    # The runtime can't refuse a Notification it already fired; it
    # can't undo a WorktreeCreate that already mutated the filesystem.
    ("Notification",       "block"),
    ("SessionStart",       "block"),
    ("WorktreeCreate",     "block"),
    ("FileChanged",        "block"),
    ("InstructionsLoaded", "block"),
    ("ConfigChange",       "ask"),
])
def test_d58_audit_only_events_reject_gate_actions(event, bad_action):
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination(event, "*", bad_action)


def test_d58_extended_events_reject_tool_matcher():
    """Every D58 no-tool-context event must reject a tool matcher.
    Same constraint the pre-D58 6 events carried.

    D70 — PostToolUseFailure / PostToolBatch are tool-context events
    (their payload carries a tool name) so they accept per-tool audit
    matchers. They're excluded from this list and covered by
    `test_d70_post_tool_failure_batch_audit_per_tool_lockstep` below.
    """
    new_events = [
        "PermissionRequest", "PermissionDenied",
        "UserPromptExpansion", "PostCompact",
        "Elicitation", "ElicitationResult",
        "SubagentStart",
        "StopFailure",
        "Setup", "Notification",
        "TeammateIdle", "TaskCreated", "TaskCompleted",
        "ConfigChange",
        "WorktreeCreate", "WorktreeRemove",
        "InstructionsLoaded",
        "CwdChanged", "FileChanged",
        "MessageDisplay",
    ]
    for ev in new_events:
        with pytest.raises(ValueError, match="illegal combination"):
            validate_combination(ev, "Bash", "audit")


# CC version this matrix was last calibrated against. Bump these
# constants in lock-step with any change to the binary candidate list
# (verified or unverified) so future maintainers know exactly which
# refresh they have to redo before adjusting the matrix.
CC_VERSION = "2.1.170"
CC_SHA = "1cda84def004ef3a8f569f8e8284a153a6b98c3a"


def test_d58_supported_events_count_is_30():
    """Bookmark the count + the EXACT expected event set so a future
    refresh has to explicitly name additions / removals. Anchoring on
    a count alone (the pre-D58-followup behavior) was wrong: a future
    maintainer's first instinct would be to bump 30 → 31 without
    re-verifying matrix entries. The expected-set assertion
    `test_supported_events_covers_full_cc_surface` is the strict
    contract; this test pins the magnitude as a sanity check.

    Calibrated against Claude Code CC_VERSION + CC_SHA above. Bump
    those constants when refreshing the matrix off a newer binary."""
    assert len(supported_events()) == 30
    # Witness that the constants exist in this module so reviewers
    # see the binary they came from at the same time they see the
    # count being changed.
    assert CC_VERSION == "2.1.170"
    assert CC_SHA == "1cda84def004ef3a8f569f8e8284a153a6b98c3a"


# ── D69: matrix re-audit ─────────────────────────────────────────────


@pytest.mark.parametrize("event", [
    # Observational hooks that today's matrix narrowed to audit-only +
    # run_command. CC's stdout JSON (additionalContext) is uniform on
    # all 22 inject_context-capable events, so operators authoring
    # "carry context over to the next turn" should be able to attach
    # inject_context to any of these without a runtime refusal.
    #
    # D70 — the inject_context-legal set is now 22 (was 26): the
    # original D59 four (Elicitation / ElicitationResult /
    # WorktreeCreate / MessageDisplay) plus the D70 four end-of-life
    # events (Stop / StopFailure / SessionEnd / SubagentStop) are
    # excluded. Stop / SessionEnd / SubagentStop were removed from
    # this list and moved into
    # `test_d70_inject_context_rejected_on_end_of_life_events`.
    "PreToolUse", "PostToolUse",
    "PostToolUseFailure", "PostToolBatch",
    "PermissionRequest", "PermissionDenied",
    "UserPromptSubmit", "UserPromptExpansion",
    "PreCompact", "PostCompact",
    "SubagentStart",
    "Setup", "Notification",
    "SessionStart",
    "TeammateIdle", "TaskCreated", "TaskCompleted",
    "ConfigChange",
    "WorktreeRemove",
    "InstructionsLoaded",
    "CwdChanged", "FileChanged",
])
def test_d69_inject_context_legal_on_22_events(event):
    """D69: inject_context joins LEGAL_COMBINATIONS as a 6th legal
    action. The 22 events are exactly `_SUPPORTED_EVENTS -
    _CONTEXT_INJECTION_EXCLUDED_EVENTS`. Both wildcard and (for the
    four tool-context events) per-tool matchers are accepted."""
    validate_combination(event, "*", "inject_context")


@pytest.mark.parametrize("event", [
    # D59 excludes these four from additionalContext-bearing hooks.
    # The hookSpecificOutput shape is specialized and CC silently
    # drops additionalContext on them at runtime; the matrix must
    # mirror the runtime gate.
    "Elicitation", "ElicitationResult",
    "WorktreeCreate", "MessageDisplay",
])
def test_d69_inject_context_rejected_on_excluded_events(event):
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination(event, "*", "inject_context")


@pytest.mark.parametrize("event,matcher", [
    # The four tool-context events accept per-tool + mcp_tool
    # matchers on inject_context, same shape they already accept for
    # block / audit / input_rewrite / run_command.
    ("PreToolUse",          "Bash"),
    ("PreToolUse",          "mcp__court__file"),
    ("PostToolUse",         "Bash"),
    ("PostToolUseFailure",  "Bash"),
    ("PostToolBatch",       "mcp__court__file"),
])
def test_d69_inject_context_tool_matcher_on_tool_context_events(event, matcher):
    validate_combination(event, matcher, "inject_context")


@pytest.mark.parametrize("event,action", [
    # D69: widen observational hooks to accept inject_context +
    # run_command beyond the prior audit-only narrowing. Pin a
    # representative slice of the explicit corrections called out
    # in the brief so a future audit cannot silently re-narrow them.
    ("TaskCreated",          "inject_context"),
    ("TaskCreated",          "run_command"),
    ("TaskCompleted",        "inject_context"),
    ("TaskCompleted",        "run_command"),
    ("SubagentStart",        "inject_context"),
    ("SubagentStart",        "run_command"),
    ("PostToolUseFailure",   "inject_context"),
    ("PostToolUseFailure",   "run_command"),
    ("Notification",         "inject_context"),
    ("Notification",         "run_command"),
    ("Setup",                "inject_context"),
    ("Setup",                "run_command"),
    ("TeammateIdle",         "inject_context"),
    ("TeammateIdle",         "run_command"),
    ("ConfigChange",         "inject_context"),
    ("ConfigChange",         "run_command"),
    ("FileChanged",          "inject_context"),
    ("FileChanged",          "run_command"),
    ("CwdChanged",           "inject_context"),
    ("CwdChanged",           "run_command"),
    ("InstructionsLoaded",   "inject_context"),
    ("InstructionsLoaded",   "run_command"),
])
def test_d69_observational_hooks_widened(event, action):
    validate_combination(event, "*", action)


@pytest.mark.parametrize("event,bad_action", [
    # WorktreeCreate stays inject_context-excluded (hookSpecificOutput
    # carries worktreePath); MessageDisplay too (display-only, no
    # model-context channel). They still accept run_command + audit.
    ("WorktreeCreate", "inject_context"),
    ("MessageDisplay", "inject_context"),
    ("Elicitation",    "inject_context"),
    ("ElicitationResult", "inject_context"),
])
def test_d69_inject_context_excluded_events_still_refused(event, bad_action):
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination(event, "*", bad_action)


@pytest.mark.parametrize("event", [
    # Even after D69 widening, observational hooks still cannot block.
    # The runtime cannot retract a Notification, undo a WorktreeCreate,
    # or refuse a TaskCompleted that already fired.
    "Notification", "WorktreeCreate", "TaskCompleted",
    "FileChanged", "InstructionsLoaded", "SessionStart",
])
def test_d69_observational_hooks_still_refuse_block(event):
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination(event, "*", "block")


# ── D70: end-of-life inject_context exclusion + audit lockstep ──────


@pytest.mark.parametrize("event", [
    # D70 — end-of-life events have no downstream same-session model
    # turn for additionalContext to land in. CC's stdout JSON does
    # carry the field uniformly but silently drops it at these four
    # timings. The matrix must refuse the triple at authoring time so
    # the operator does not see a green check and zero enforcement.
    "Stop", "StopFailure", "SessionEnd", "SubagentStop",
])
def test_d70_inject_context_rejected_on_end_of_life_events(event):
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination(event, "*", "inject_context")


@pytest.mark.parametrize("event,matcher", [
    # D70 — PostToolUseFailure / PostToolBatch payloads carry a tool
    # name; the audit archetype now accepts the same matcher set
    # run_command + inject_context already accepted in D63 + D69. The
    # asymmetry prior to D70 ("inject context on Edit-only failures"
    # legal, "audit Edit-only failures" refused) is gone.
    ("PostToolUseFailure",  "Bash"),
    ("PostToolUseFailure",  "Edit"),
    ("PostToolUseFailure",  "Bash|Edit"),
    ("PostToolUseFailure",  "*"),
    ("PostToolUseFailure",  "mcp__court__file"),
    ("PostToolBatch",       "Bash"),
    ("PostToolBatch",       "Bash|Read"),
    ("PostToolBatch",       "*"),
    ("PostToolBatch",       "mcp__court__file"),
])
def test_d70_post_tool_failure_batch_audit_per_tool_lockstep(event, matcher):
    validate_combination(event, matcher, "audit")


@pytest.mark.parametrize("event,matcher,action", [
    # D70 — sibling actions on the same (event, matcher) must all be
    # legal together. Pin a representative slice across audit /
    # run_command / inject_context so a future widening of one loop
    # without the others cannot silently re-introduce the asymmetry.
    ("PostToolUseFailure", "Edit", "audit"),
    ("PostToolUseFailure", "Edit", "run_command"),
    ("PostToolUseFailure", "Edit", "inject_context"),
    ("PostToolBatch",      "Bash|Edit", "audit"),
    ("PostToolBatch",      "Bash|Edit", "run_command"),
    ("PostToolBatch",      "Bash|Edit", "inject_context"),
])
def test_d70_audit_run_command_inject_context_in_lockstep(event, matcher, action):
    validate_combination(event, matcher, action)


@pytest.mark.parametrize("tool", [
    # D70 — _BUILTIN_TOOLS expansion. The D69 Common-tier TaskCompleted
    # promo implicitly tells operators "Task is a thing"; the matcher
    # registry must classify it as a tool matcher (not raise unknown
    # matcher class). The other CC 2.1.170 binary-string tools are
    # added at the same time so the audit is symmetric across events
    # and tools.
    "Task", "MultiEdit", "BashOutput", "KillBash",
    "NotebookRead", "ExitPlanMode", "AskUser",
])
def test_d70_builtin_tools_expanded(tool):
    assert matcher_class_of(tool) is MatcherClass.tool


def test_d70_task_tool_legal_on_pretooluse():
    """An operator following the D69 Common-tier copy "inject results
    back when the Task tool finishes" and authoring PreToolUse + Task
    + inject_context must reach a legal triple, not the prior unknown-
    matcher refusal at IR load time."""
    validate_combination("PreToolUse", "Task", "inject_context")
    validate_combination("PreToolUse", "Task", "audit")
    validate_combination("PreToolUse", "Task", "block")


# ── D82d: PostToolUse / PostToolUseFailure / PostToolBatch admit ─────
#         block as retry-feedback channel ───────────────────────────


@pytest.mark.parametrize("event,matcher", [
    # CC's PostToolUse hook stdout JSON accepts
    # {"decision":"block","reason":"…"} and surfaces the reason as a
    # retry-feedback message to the model. The retry-feedback action
    # surface is real; D82d registers it on the three PostToolUse*
    # events whose payloads carry a tool name (PostToolUse +
    # PostToolUseFailure) or whose batch shape covers the whole turn
    # (PostToolBatch).
    ("PostToolUse",        "Bash"),
    ("PostToolUse",        "Edit"),
    ("PostToolUse",        "Bash|Edit"),
    ("PostToolUse",        "mcp__court__file"),
    ("PostToolUseFailure", "Bash"),
    ("PostToolUseFailure", "Edit"),
    ("PostToolUseFailure", "mcp__court__file"),
    ("PostToolBatch",      "*"),
])
def test_d82d_block_legal_on_post_tool_events(event, matcher):
    validate_combination(event, matcher, "block")


@pytest.mark.parametrize("event", [
    # block stays illegal on these end-of-life events. The
    # retry-feedback message has no downstream session turn to land
    # in: Stop / SessionEnd fire at end-of-execution / teardown,
    # SubagentStop fires after the child returned, TaskCompleted
    # fires once the parent has accepted the child's result.
    "Stop", "SessionEnd", "SubagentStop", "TaskCompleted",
])
def test_d82d_block_still_illegal_on_end_of_life_events(event):
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination(event, "*", "block")


@pytest.mark.parametrize("event,matcher", [
    # D82d narrowing: ask is intentionally NOT registered on
    # PostToolUse* events even though block now is — the timing makes
    # the "ask a human" affordance confusing (the tool already ran
    # and the model is mid-turn). Pinning ask refusal here keeps a
    # future widening from silently coupling block + ask.
    ("PostToolUse",        "Bash"),
    ("PostToolUseFailure", "Bash"),
    ("PostToolBatch",      "*"),
])
def test_d82d_ask_still_illegal_on_post_tool_events(event, matcher):
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination(event, matcher, "ask")


def test_d82d_post_tool_batch_block_wildcard_only():
    """PostToolBatch + per-tool matcher + block stays illegal — the
    batch event covers the whole turn's tool calls, no single named
    tool to scope to. Authoring per-tool block on the batch event
    would silently fire on every tool in the batch."""
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination("PostToolBatch", "Bash", "block")


def test_d82d_post_tool_use_failure_block_excludes_tool_alt():
    """PostToolUseFailure + tool_alt + block stays illegal — the
    failure surfaces a single tool name, so authoring "retry on
    failure of any of A | B | C" should route through PostToolBatch
    instead. Pin the exclusion so future widening lands here."""
    with pytest.raises(ValueError, match="illegal combination"):
        validate_combination("PostToolUseFailure", "Bash|Edit", "block")


def test_d79_verified_vs_unverified_event_split():
    """D79: every previously-unverified candidate has been promoted to
    `_VERIFIED_EVENTS` after the CC 2.1.170 binary audit confirmed
    three independent signals per event:

      1. a literal ``hook_event_name:"<Event>"`` JSON property
         emitted by the runtime on the gate stdin payload;
      2. a matching ``execute<Event>Hooks`` exported runner (CC ships
         one per authorable hook event);
      3. an explicit payload-field shape captured from the binary
         constructor literal and pinned in
         ``src/magi_cp/policy/payload_schemas.py``.

    `_UNVERIFIED_EVENTS` is preserved (empty) so downstream callers
    stay source-compatible and a future cask refresh that drops a
    runner / `hook_event_name` literal has a stable home to land in.
    """
    from magi_cp.policy.matrix import _VERIFIED_EVENTS, _UNVERIFIED_EVENTS
    # Sanity: the two sets still partition supported_events() exactly.
    assert _VERIFIED_EVENTS.isdisjoint(_UNVERIFIED_EVENTS)
    assert _VERIFIED_EVENTS | _UNVERIFIED_EVENTS == supported_events()
    # D79: verified set is the full 30-event surface.
    assert _VERIFIED_EVENTS == supported_events()
    assert len(_VERIFIED_EVENTS) == 30
    assert len(_UNVERIFIED_EVENTS) == 0


def test_d79_verified_includes_pre_d58_floor():
    """The pre-D58 verified floor must remain in `_VERIFIED_EVENTS`
    so a future re-narrow cannot accidentally drop a name covered by
    the existing test fixtures + docs/architecture/claude-code-cli."""
    from magi_cp.policy.matrix import _VERIFIED_EVENTS
    pre_d58_floor = {
        "PreToolUse", "PostToolUse",
        "Stop", "SubagentStop",
        "UserPromptSubmit",
        "PreCompact",
        "SessionStart", "SessionEnd",
    }
    assert pre_d58_floor <= _VERIFIED_EVENTS


def test_d79_verified_includes_d58_candidates_promoted():
    """Pin the 22 D58 candidates as members of `_VERIFIED_EVENTS`. A
    future cask refresh that loses any of these (CC dropped a
    `hook_event_name` literal or an `execute<Event>Hooks` runner) must
    move the name back to `_UNVERIFIED_EVENTS` AND update the matching
    `UNVERIFIED_LIFECYCLE_SLUGS` entry in
    `web/app/(console)/policies/new/_components/step1-lifecycle-groups.ts`
    so card label / chip menu / matrix do not drift.

    Honesty caveat: this is a *Python-side pin*. The TS partition test
    (`Step1LifecyclePicker.test.ts`) pins the TS side independently.
    Together they fail loudly if either side moves, but the two
    assertions are NOT cross-language — a partial Python-only revert
    will fail this test while the TS file keeps rendering the old
    badge state until the Python failure is fixed. A future build-time
    JSON handshake (Python → generated `.unverified-events.generated.json`
    → consumed by `step1-lifecycle-groups.ts`) would close this gap.
    """
    from magi_cp.policy.matrix import _VERIFIED_EVENTS
    promoted_in_d79 = {
        # Tool-context observability variants
        "PostToolUseFailure", "PostToolBatch",
        # Permission gate family
        "PermissionRequest", "PermissionDenied",
        # Content-flow extensions
        "UserPromptExpansion", "PostCompact",
        "Elicitation", "ElicitationResult",
        # Subagent / Stop boundary
        "SubagentStart", "StopFailure",
        # Lifecycle / observability surface
        "Setup", "Notification",
        "TeammateIdle", "TaskCreated", "TaskCompleted",
        "ConfigChange",
        "WorktreeCreate", "WorktreeRemove",
        "InstructionsLoaded",
        "CwdChanged", "FileChanged",
        "MessageDisplay",
    }
    assert promoted_in_d79 <= _VERIFIED_EVENTS
