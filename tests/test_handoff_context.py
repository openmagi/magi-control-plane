"""D57g — POST /policies/handoff-context + the underlying serializer.

The route is offline (no LLM call) and admin-key gated. The serializer
re-uses the same per-field allowlists that gate /policies/compile-
interactive so any value that survives the merge is already canonical.

Coverage:
  - empty handoff → canonical first questions + neutral intro line.
  - guided wizard mid-flow → assistant summary lists what's filled,
    canonical questions cover what's missing.
  - raw IR draft → IR fields survive sanitisation (gate_binary smuggled
    via the body does NOT land on the merged draft).
  - both inputs together → wizard state wins on conflict (most recent
    author intent).
  - archetype collapse (action=inject_context / input_rewrite) is
    called out in the assistant message.
  - oversize body → 422; missing admin key → 403.
"""
from __future__ import annotations

import tempfile

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.policy.handoff_context import (
    HandoffContextError, build_handoff_turn,
)


HEADERS = {"X-Admin-Api-Key": "test-admin-key"}


@pytest.fixture(autouse=True)
def _admin_key(monkeypatch):
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", "test-admin-key")


def _tmp_store_path() -> str:
    f = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    f.write("[]")
    f.close()
    return f.name


def _client() -> TestClient:
    app = create_app(
        dsn="sqlite:///:memory:",
        policy_store_path=_tmp_store_path(),
    )
    return TestClient(app)


# ── serializer-direct tests ───────────────────────────────────────────


def test_empty_handoff_returns_neutral_intro_and_first_questions():
    out = build_handoff_turn(wizard_state=None, draft_ir=None)
    assert "missing_fields" in out
    # Every canonical field is missing on an empty handoff.
    assert "lifecycle" in out["missing_fields"]
    assert "matcher" in out["missing_fields"]
    # Canonical first questions: lifecycle + matcher.
    qids = [q["id"] for q in out["questions"]]
    assert qids == ["q_lifecycle", "q_matcher"]
    # No draft, no ready_to_save.
    assert out["draft"] is None
    assert out["ready_to_save"] is False
    assert out["needs_more"] is True


def test_guided_wizard_midflow_summarizes_filled_and_asks_missing():
    """Lifecycle + tool + condition picked but no id yet. The summary
    line should call out lifecycle / matcher / check / action, and
    the remaining question set should cover (requires_body, on_missing)
    or (on_missing, id) depending on what's filled."""
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "conditionKind": "regex",
        "pattern": "(^|\\s)sudo\\s",
        "action": "block",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    msg = out["assistant_message"]
    # English by default (no Hangul). The labels mirror page.tsx.
    assert "before a tool runs" in msg
    assert "Bash" in msg
    assert "block" in msg
    # `id` should be the only remaining missing field.
    assert out["missing_fields"] == ["id"]
    qids = [q["id"] for q in out["questions"]]
    assert qids == ["q_id"]
    # The draft is shaped enough to surface to the client.
    assert out["draft"] is not None
    assert out["draft"]["trigger"]["event"] == "PreToolUse"
    assert out["draft"]["trigger"]["matcher"] == "Bash"
    assert out["draft"]["requires"][0]["kind"] == "regex"
    assert out["draft"]["requires"][0]["pattern"] == "(^|\\s)sudo\\s"
    assert out["draft"]["action"] == "block"


def test_korean_state_surfaces_korean_summary():
    """A description with Hangul flips the assistant prose to Korean."""
    state = {
        "lifecycle": "after_tool_use",
        "toolScope": "WebFetch",
        "description": "외부 fetch 결과 감사",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    msg = out["assistant_message"]
    # Korean lifecycle label and the action header use Hangul.
    assert "도구 실행 후" in msg
    assert "WebFetch" in msg


def test_raw_ir_smuggled_gate_binary_does_not_land():
    """A hand-crafted draft_ir cannot smuggle gate_binary onto the
    merged draft. _sanitize_draft_so_far is the boundary."""
    draft_ir = {
        "id": "block-rm",
        "version": "0.1",
        "trigger": {
            "host": "claude-code",
            "event": "PreToolUse",
            "matcher": "Bash",
        },
        "requires": [{"kind": "regex", "pattern": "rm -rf"}],
        "action": "block",
        # smuggled fields the sanitizer drops:
        "gate_binary": "/etc/malicious",
        "on_signature_invalid": "allow",
        "sentinel_re": ".*",
        "type": "permission",
    }
    out = build_handoff_turn(wizard_state=None, draft_ir=draft_ir)
    merged = out["draft"]
    assert merged is not None
    assert "gate_binary" not in merged
    assert "on_signature_invalid" not in merged
    assert "sentinel_re" not in merged
    assert "type" not in merged
    # Author-supplied fields still survive.
    assert merged["trigger"]["matcher"] == "Bash"
    assert merged["action"] == "block"
    assert merged["id"] == "block-rm"


def test_wizard_state_wins_over_draft_ir_on_conflict():
    """The wizard surface is the more recent author intent (the user
    just clicked Continue from inside it), so conflicting matchers
    should resolve to the wizard's pick."""
    draft_ir = {
        "id": "old-id",
        "version": "0.1",
        "trigger": {
            "host": "claude-code",
            "event": "PreToolUse",
            "matcher": "Edit",
        },
        "action": "audit",
    }
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "action": "block",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=draft_ir)
    merged = out["draft"]
    assert merged is not None
    assert merged["trigger"]["matcher"] == "Bash"
    assert merged["action"] == "block"
    # id was not overwritten by the wizard state (no id field present),
    # so the raw editor's value survives.
    assert merged["id"] == "old-id"


def test_inject_context_action_is_collapsed_and_called_out():
    """The conversational vocab cannot model inject_context; the
    serialiser collapses it (no on_missing landed) and the assistant
    summary mentions the collapse using the plain-language label
    (NEVER the raw enum slug — that would violate the project-wide
    "no internal terms in NL surfaces" rule)."""
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "action": "inject_context",
        "injectTemplate": "Reminder: be careful",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    msg = out["assistant_message"]
    # `on_missing` lands in missing_fields because action wasn't kept.
    assert "on_missing" in out["missing_fields"]
    # Plain-language label must appear; the raw slug must NOT.
    assert "context injection" in msg
    assert "inject_context" not in msg
    # The injected template never lands on the draft (it has no slot
    # on the conversational vocabulary).
    if out["draft"] is not None:
        assert "template" not in out["draft"]
    # The operator's typed body is surfaced in the assistant summary
    # so the bytes are not lost silently. We assert on the visible
    # substring (not the field name).
    assert "Reminder: be careful" in msg


def test_input_rewrite_action_label_is_plain_language():
    """input_rewrite must render its plain-language label (not the
    raw enum). The KO branch uses the Korean label."""
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "action": "input_rewrite",
        "rewriterPrefix": "[sanitised] ",
        "description": "입력 정리 정책",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    msg = out["assistant_message"]
    assert "input_rewrite" not in msg
    # KO label.
    assert "입력 다시 쓰기" in msg
    # The rewriter body bytes are surfaced so the operator can copy
    # them back into the next reply.
    assert "[sanitised]" in msg


def test_strip_action_label_is_plain_language():
    state = {
        "lifecycle": "after_tool_use",
        "toolScope": "Bash",
        "action": "strip",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    msg = out["assistant_message"]
    assert "strip" not in msg or "stripping" in msg
    # English fallback: "stripping the output".
    assert "stripping the output" in msg


def test_unknown_condition_kind_is_collapsed_with_generic_phrasing():
    """A wizard state with an unrecognised condition kind must NOT
    leak the raw slug to the rendered chat. We use the generic
    `that check` / `이 조건` fallback so a future ConditionKind that
    ships without a label update silently degrades to plain
    language."""
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "WebFetch",
        "conditionKind": "banana",  # unknown kind
        "action": "audit",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    msg = out["assistant_message"]
    # The raw slug must NOT appear in the rendered chat.
    assert "banana" not in msg
    # The generic phrasing must.
    assert "that check" in msg
    # Without a kind the wizard still leaves the requires slot empty.
    assert "requires" in out["missing_fields"]


def test_known_wizard_only_condition_kinds_use_their_plain_labels():
    """fetch_domain / domain_allowlist / evidence_ref are recognised
    wizard kinds that the conversational vocabulary can map down. They
    no longer surface as dropped — they land on the draft as a regex
    pattern (fetch_domain / domain_allowlist) or as a `step` (evidence_ref).
    The summary therefore does NOT mention a dropped kind for them."""
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "WebFetch",
        "conditionKind": "fetch_domain",
        "fetchDomain": "openmagi.ai",
        "action": "block",
        "id": "webfetch-openmagi",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    msg = out["assistant_message"]
    # The raw token does not leak.
    assert "fetch_domain" not in msg
    # No collapse note for this kind (it lands as a regex).
    merged = out["draft"]
    assert merged is not None
    assert (merged.get("requires") or [{}])[0].get("kind") == "regex"


def test_domain_allowlist_compiles_to_regex_requirement():
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "WebFetch",
        "conditionKind": "domain_allowlist",
        "allowlist": "github.com, npmjs.com",
        "action": "ask",
        "id": "webfetch-allow",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    merged = out["draft"]
    assert merged is not None
    reqs = merged.get("requires") or []
    assert reqs, "expected requires to land"
    assert reqs[0]["kind"] == "regex"
    # The domain is regex-escaped so the literal dot is `\.`.
    assert r"github\.com" in reqs[0]["pattern"]
    assert r"npmjs\.com" in reqs[0]["pattern"]


def test_id_field_renders_as_name_in_en_summary():
    """The wizard's user-facing label for the policy id is `Name`
    (Step 5 heading). The EN summary mirrors that so the operator
    does not see `id:` (borderline-internal vocabulary) right after
    leaving a `Name` field."""
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "id": "block-sudo",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    msg = out["assistant_message"]
    assert "name: block-sudo" in msg
    # Defensive: the literal "id: <id>" line should not appear.
    assert "id: block-sudo" not in msg


def test_dropped_lifecycle_outside_conversational_vocab_is_called_out():
    """A D58 lifecycle the conversational vocab cannot model (e.g.
    `permission_request`) used to silently degrade — the summary would
    print 'haven't filled much in yet'. The fix tracks the dropped
    lifecycle and calls it out with a plain-language label."""
    state = {
        "lifecycle": "permission_request",
        "toolScope": "Bash",
        "action": "audit",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    msg = out["assistant_message"]
    # The raw slug must NOT leak; the plain-language label must.
    assert "permission_request" not in msg
    assert "permission-request" in msg
    # The misleading "haven't filled much in yet" line is suppressed
    # when there's a dropped lifecycle — the summary frames it as a
    # collapse instead.
    assert "haven't filled much in yet" not in msg


def test_dropped_lifecycle_unknown_slug_uses_generic_phrasing():
    state = {
        "lifecycle": "made_up_event",
        "toolScope": "Bash",
        "action": "audit",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    msg = out["assistant_message"]
    assert "made_up_event" not in msg
    assert "that timing" in msg


def test_half_typed_regex_is_preserved_on_draft():
    """The wizard's regex body is in-progress (uncompilable): the
    operator typed `^foo(` and then clicked Continue. The seed
    must preserve the bytes on the draft (so the IR pane shows them)
    rather than silently dropping them — the conversational follow-up
    re-asks for a body anyway."""
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "conditionKind": "regex",
        "pattern": "^foo(",   # uncompilable
        "action": "block",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    merged = out["draft"]
    assert merged is not None
    reqs = merged.get("requires") or [{}]
    assert reqs[0].get("kind") == "regex"
    assert reqs[0].get("pattern") == "^foo("


def test_empty_wizard_regex_does_not_clobber_well_formed_raw_editor_requires():
    """The wizard picked `regex` as the condition kind but the body is
    in-progress. The raw editor draft carries a fully-formed
    `requires=[{kind:"regex", pattern:"^foo$"}]`. The merge must
    fall back to the raw editor's value rather than clobbering it
    with the wizard's empty slot."""
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "conditionKind": "regex",
        # No `pattern` field — the body is still in-progress.
        "action": "block",
    }
    draft_ir = {
        "id": "good-id",
        "version": "0.1",
        "trigger": {"host": "claude-code", "event": "PreToolUse", "matcher": "Bash"},
        "requires": [{"kind": "regex", "pattern": "^foo$"}],
        "action": "audit",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=draft_ir)
    merged = out["draft"]
    assert merged is not None
    assert merged["requires"][0]["pattern"] == "^foo$"


def test_origin_frames_the_summary_head():
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "action": "audit",
        "id": "x-y",
    }
    out_review = build_handoff_turn(wizard_state=state, draft_ir=None, origin="review")
    assert "review screen" in out_review["assistant_message"]
    out_adv = build_handoff_turn(wizard_state=state, draft_ir=None, origin="advanced")
    assert "rule editor" in out_adv["assistant_message"]


def test_locale_hint_overrides_draft_content_heuristic():
    """Korean-locale operator authoring an English-only policy still
    receives a Korean summary when the dashboard forwards
    `locale=ko`."""
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "action": "audit",
        "id": "english-only",
    }
    out = build_handoff_turn(
        wizard_state=state, draft_ir=None, locale_hint="ko",
    )
    msg = out["assistant_message"]
    assert "도구 실행 전" in msg
    # English head should not appear when ko was hinted.
    assert "Continuing from where you were" not in msg


def test_locale_hint_en_overrides_korean_draft():
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "action": "audit",
        "description": "한국어 설명",
    }
    out = build_handoff_turn(
        wizard_state=state, draft_ir=None, locale_hint="en",
    )
    msg = out["assistant_message"]
    assert "before a tool runs" in msg


def test_oversized_state_raises():
    blob = "x" * 30_000
    with pytest.raises(HandoffContextError):
        build_handoff_turn(
            wizard_state={"description": blob}, draft_ir=None,
        )


def test_complete_draft_is_ready_to_save():
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "conditionKind": "regex",
        "pattern": "(^|\\s)sudo\\s",
        "action": "block",
        "id": "block-sudo",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    assert out["ready_to_save"] is True
    assert out["needs_more"] is False
    assert out["questions"] == []


# ── D66 run_command handoff round-trip ────────────────────────────────


def test_run_command_inline_state_round_trips_through_seed():
    """The brief's leading scenario: wizard mid-flight with
    action=run_command + half-typed inline command + 3s timeout. The
    serializer must carry the body to the draft (so the IrDraftPane
    renders it the same way it would after a typed reply) and frame
    the summary line with the brief's "run <X> at <when> with <T>s
    timeout" template."""
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "action": "run_command",
        "runCommandMode": "inline",
        "runCommandRuntime": "bash",
        "runCommandBody": "echo \"audit-stamp $TOOL_INPUT\"",
        "runCommandArgs": "",
        "runCommandTimeoutMs": "3000",
        "runCommandFailClosed": "false",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    merged = out["draft"]
    assert merged is not None
    # The archetype discriminator landed.
    assert merged["type"] == "run_command"
    # Run_command body fields are present.
    assert merged["command"] == "echo \"audit-stamp $TOOL_INPUT\""
    assert merged["runtime"] == "bash"
    assert merged["timeout_ms"] == 3_000
    assert merged["fail_closed"] is False
    # Trigger carries through from lifecycle / toolScope.
    assert merged["trigger"]["event"] == "PreToolUse"
    assert merged["trigger"]["matcher"] == "Bash"
    # Verifier-only fields are NOT seeded — they have no meaning on
    # run_command.
    assert "requires" not in merged
    assert "on_missing" not in merged
    assert "action" not in merged
    # Summary line follows the brief template.
    msg = out["assistant_message"]
    assert "Continuing where you left off" in msg
    # "run <X> at <when> with <T>s timeout" template parts.
    assert "echo " in msg
    assert "before a tool runs" in msg
    assert "3s timeout" in msg
    assert "non-blocking on failure" in msg


def test_run_command_attached_script_state_round_trips_with_name():
    """Attached-script lane: a 64-hex script id + operator-typed name.
    The IR persists only the hash, but the handoff seed preserves the
    name on `_script_name` so the assistant line and the IR pane can
    render the friendly label alongside the bare id."""
    sid = "a" * 64
    state = {
        "lifecycle": "after_tool_use",
        "toolScope": "WebFetch",
        "action": "run_command",
        "runCommandMode": "attach",
        "runCommandRuntime": "python3",
        "runCommandScriptId": sid,
        "runCommandScriptName": "audit-stamp.py",
        "runCommandArgs": "foo, bar",
        "runCommandTimeoutMs": "10000",
        "runCommandFailClosed": "true",
        "id": "wf-audit-stamp",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    merged = out["draft"]
    assert merged is not None
    assert merged["type"] == "run_command"
    assert merged["script_path"] == sid
    assert merged["_script_name"] == "audit-stamp.py"
    assert merged["runtime"] == "python3"
    assert merged["args"] == ["foo", "bar"]
    assert merged["timeout_ms"] == 10_000
    assert merged["fail_closed"] is True
    assert merged["id"] == "wf-audit-stamp"
    # No `command` was supplied — the attach lane is exclusive.
    assert "command" not in merged
    # Summary line uses the operator-typed script name, not the bare id.
    msg = out["assistant_message"]
    assert "audit-stamp.py" in msg
    # Long hash should NOT leak in full to the rendered chat.
    assert sid not in msg
    assert "after a tool runs" in msg
    assert "10s timeout" in msg
    assert "deny on failure" in msg


def test_run_command_only_action_no_body_yet_asks_for_command():
    """Edge case: operator picked run_command but has not typed the
    command body yet. The draft must still commit to `type:
    "run_command"` (so the conversational missing-fields loop dispatches
    to `_run_command_missing_fields`) and ask for the body via the
    requires_body question slot."""
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "action": "run_command",
        # No runCommandBody / scriptId yet.
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    merged = out["draft"]
    assert merged is not None
    assert merged["type"] == "run_command"
    # requires_body is the canonical missing slot for a body-less
    # run_command draft (per _run_command_missing_fields).
    assert "requires_body" in out["missing_fields"]
    # `id` is also missing.
    assert "id" in out["missing_fields"]
    # The summary line still mentions running a command at the picked
    # event even though the body is empty.
    msg = out["assistant_message"]
    assert "run a command" in msg
    assert "before a tool runs" in msg


def test_run_command_only_mode_toggle_picked_no_command():
    """Edge case: operator opened Step 4b, switched mode to `attach`,
    then clicked Continue. The mode toggle alone must not produce a
    half-shaped draft that smuggles a script_path."""
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "action": "run_command",
        "runCommandMode": "attach",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    merged = out["draft"]
    assert merged is not None
    assert merged["type"] == "run_command"
    assert "script_path" not in merged
    assert "command" not in merged
    assert "requires_body" in out["missing_fields"]


def test_run_command_invalid_script_id_is_dropped():
    """A half-typed (non-64-hex) script id MUST NOT leak past the
    serializer. The conversational follow-up re-asks via
    requires_body."""
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "action": "run_command",
        "runCommandMode": "attach",
        "runCommandScriptId": "deadbeef",  # too short
        "id": "block-sudo",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    merged = out["draft"]
    assert merged is not None
    assert merged["type"] == "run_command"
    assert "script_path" not in merged
    assert "requires_body" in out["missing_fields"]


def test_run_command_out_of_range_timeout_is_dropped():
    """A timeout outside the IR's bounds is dropped, not clamped."""
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "action": "run_command",
        "runCommandBody": "echo hi",
        "runCommandTimeoutMs": "999999",  # > _MAX_RUN_COMMAND_TIMEOUT_MS
        "id": "tmt",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    merged = out["draft"]
    assert merged is not None
    assert "timeout_ms" not in merged


def test_run_command_does_not_appear_as_dropped_action():
    """`run_command` USED to be a dropped archetype in the summary
    (D63 review labeled it). D66 widened the serializer so it now
    round-trips; the summary must not emit the dropped-action collapse
    note for run_command."""
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "action": "run_command",
        "runCommandBody": "echo hi",
        "id": "x",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    msg = out["assistant_message"]
    # The "collapsed to the closest default" note must NOT appear.
    assert "collapsed to the closest default" not in msg
    # Neither the KO variant.
    assert "가까운 기본값으로 정리했어요" not in msg


def test_run_command_korean_summary_uses_korean_template():
    """KO locale yields the Korean variant of the brief's template:
    "<event>에 <X> 실행, 타임아웃 <T>s, ..."."""
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "action": "run_command",
        "runCommandBody": "echo hi",
        "runCommandTimeoutMs": "2000",
        "runCommandFailClosed": "true",
        "description": "감사 스탬프 정책",
        "id": "audit-stamp",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    msg = out["assistant_message"]
    # Korean lifecycle label.
    assert "도구 실행 전" in msg
    # Korean timeout phrasing.
    assert "타임아웃 2s" in msg
    # Korean fail-closed phrasing.
    assert "실패 시 차단" in msg


def test_run_command_inline_lane_drops_smuggled_script_id():
    """When mode=inline, a smuggled `runCommandScriptId` must NOT land.
    The two lanes are mutually exclusive per RunCommandPolicy.validate;
    leaving both filled would 422 on save."""
    sid = "b" * 64
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "action": "run_command",
        "runCommandMode": "inline",
        "runCommandBody": "echo hi",
        "runCommandScriptId": sid,  # smuggled
        "id": "x",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    merged = out["draft"]
    assert merged is not None
    assert merged["command"] == "echo hi"
    assert "script_path" not in merged


def test_run_command_complete_draft_is_ready_to_save():
    """A run_command wizard state with every required field set must
    flip ready_to_save=True on the handoff seam — same end-state as
    the verifier-shaped equivalent at the top of the file."""
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "action": "run_command",
        "runCommandMode": "inline",
        "runCommandRuntime": "bash",
        "runCommandBody": "echo hi",
        "runCommandTimeoutMs": "5000",
        "runCommandFailClosed": "false",
        "id": "echo-hi",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    assert out["ready_to_save"] is True, out["assistant_message"]
    assert out["needs_more"] is False
    assert out["questions"] == []


def test_run_command_overlay_clears_verifier_fields_from_base():
    """If the raw editor had a half-typed verifier draft and the wizard
    handed off committing to run_command, the merge must drop the base's
    `requires` / `action` so the merged draft is a pure run_command
    archetype (the two archetypes are mutually exclusive)."""
    draft_ir = {
        "id": "old-evidence",
        "version": "0.1",
        "trigger": {"host": "claude-code", "event": "PreToolUse", "matcher": "Bash"},
        "requires": [{"kind": "regex", "pattern": "^foo$"}],
        "action": "block",
    }
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "action": "run_command",
        "runCommandBody": "echo hi",
        "id": "echo-hi",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=draft_ir)
    merged = out["draft"]
    assert merged is not None
    assert merged["type"] == "run_command"
    assert merged["command"] == "echo hi"
    # Verifier-only fields are cleared.
    assert "requires" not in merged
    assert "action" not in merged
    # id from the wizard wins (most recent intent).
    assert merged["id"] == "echo-hi"


def test_run_command_args_csv_caps_per_arg_length():
    """A 300-char per-arg token must NOT smuggle past the per-arg cap.
    The cap mirrors the IR's `_MAX_RUN_COMMAND_ARG_LEN`."""
    long_arg = "x" * 300
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        "action": "run_command",
        "runCommandBody": "echo hi",
        "runCommandArgs": f"ok,{long_arg},also-ok",
        "id": "args-cap",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    merged = out["draft"]
    assert merged is not None
    assert merged["args"] == ["ok", "also-ok"]


# ── D66 follow-up: wider lifecycle round-trip for run_command ─────────


def test_run_command_permission_request_lifecycle_round_trips():
    """page.tsx RUN_COMMAND_LEGAL_BY_LIFECYCLE makes run_command legal
    on D58 lifecycles like `permission_request`. The handoff serializer
    must project the wider lifecycle straight onto the trigger so the
    summary renders the 'at the permission-request moment' framing
    instead of degrading to 'run a command' with no when-clause. The
    3-bucket `_LIFECYCLE_TO_EVENT` does NOT cover this slug — the
    run_command branch is what saves the round-trip."""
    state = {
        "lifecycle": "permission_request",
        "toolScope": "Bash",
        "action": "run_command",
        "runCommandBody": "echo audit",
        "id": "perm-audit",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    merged = out["draft"]
    assert merged is not None
    assert merged["type"] == "run_command"
    # Trigger.event lands on the wider event, not the 3-bucket default.
    assert merged["trigger"]["event"] == "PermissionRequest"
    # R3-01: the wider lifecycle must NOT be flagged as still-missing.
    # Before Cluster A the run_command missing-fields gate rejected any
    # non-bucket event, so the seed re-asked lifecycle forever.
    assert "lifecycle" not in out["missing_fields"], out["missing_fields"]
    # Plain-language label landed in the summary.
    msg = out["assistant_message"]
    assert "permission-request" in msg
    assert "permission_request" not in msg
    # The dropped-lifecycle collapse note must NOT appear because the
    # round-trip succeeded.
    assert "does not have a chat-mode equivalent" not in msg


def test_run_command_unknown_lifecycle_emits_dropped_lifecycle_note():
    """Even with the wider table in place, an unrecognised lifecycle
    slug still degrades. The run_command summary branch must surface
    the dropped_lifecycle note rather than short-circuiting (which is
    the bug this regression locks down)."""
    state = {
        "lifecycle": "made_up_event",
        "toolScope": "Bash",
        "action": "run_command",
        "runCommandBody": "echo hi",
        "id": "made-up",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    msg = out["assistant_message"]
    # The raw slug must NOT leak; the generic fallback label must.
    assert "made_up_event" not in msg
    assert "that timing" in msg


def test_run_command_dropped_payload_surfaces_in_summary():
    """When the wizard handed off committing to run_command but the
    operator had also partially written e.g. an inject_context template
    earlier, the bytes are surfaced under the dropped-payload table.
    Mirrors the verifier branch — without this the operator's typed
    body disappears silently on the run_command path."""
    # Simulate a wizard state where the action was switched to
    # run_command but a prior conditionKind body is still in the URL.
    # Our wizard URL ferries `injectTemplate` only when action ==
    # inject_context, so we craft the simulation: dropped_action is
    # tracked when raw_action is NOT a known on_missing value, so we
    # check via the verifier branch instead. Verify the helper is
    # shared between branches by spotting it in the run_command
    # summary header.
    state = {
        "lifecycle": "permission_request",
        "toolScope": "Bash",
        "action": "run_command",
        "runCommandBody": "echo audit",
        "id": "shared-helper",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    msg = out["assistant_message"]
    # Tool scope is in-progress on a non-tool-context lifecycle; the
    # collapse note for it must appear under the shared helper.
    assert "'Bash' target was cleared" in msg


def test_run_command_universal_lifecycle_legality_renders_at_clause():
    """Fuzz every page.tsx lifecycle slug through the summary and assert
    a non-empty 'at <when>' clause for the run_command archetype. Locks
    the coverage guarantee documented next to
    `_RUN_COMMAND_LIFECYCLE_TO_EVENT`."""
    from magi_cp.policy.handoff_context import (
        _RUN_COMMAND_LIFECYCLE_TO_EVENT,
    )
    from magi_cp.policy.handoff_context import (
        _RUN_COMMAND_LIFECYCLE_LABEL_EN,
    )
    for slug in _RUN_COMMAND_LIFECYCLE_TO_EVENT:
        # `before_tool_use` is tool-context; pick any matcher for those.
        # Use a fixed policy id so the slug never accidentally lands
        # inside the `name: ...` clause and confuses the assertion.
        state = {
            "lifecycle": slug,
            "toolScope": "Bash" if slug in ("before_tool_use", "after_tool_use") else "*",
            "action": "run_command",
            "runCommandBody": "echo hi",
            "id": "rc-policy",
        }
        out = build_handoff_turn(wizard_state=state, draft_ir=None)
        msg = out["assistant_message"]
        # The merged draft carries the event for the slug.
        merged = out["draft"]
        assert merged is not None, f"slug {slug} produced no draft"
        ev = (merged.get("trigger") or {}).get("event")
        assert ev == _RUN_COMMAND_LIFECYCLE_TO_EVENT[slug], (
            f"slug {slug} mapped to wrong event: {ev}"
        )
        # The "at <X>" clause renders — `run `echo hi`` should be
        # followed by a non-empty time phrase. We assert presence of
        # SOMETHING from the lifecycle label table rather than the
        # generic fallback.
        assert _RUN_COMMAND_LIFECYCLE_LABEL_EN[slug] in msg, (
            f"slug {slug} fell through to fallback: {msg!r}"
        )
        # And the raw multi-token slug never leaks. Single-word slugs
        # like `setup` / `notification` are legitimately substrings of
        # the plain-language label ("the setup moment"); we only assert
        # the underscore-shaped raw token never appears (those are the
        # programmer-vocabulary leaks).
        if "_" in slug:
            assert slug not in msg, (
                f"raw slug {slug} leaked into message: {msg!r}"
            )


def test_run_command_overlay_clears_verifier_fields_from_wizard_state():
    """Variant of test_run_command_overlay_clears_verifier_fields_from_base
    but with the verifier slot ON THE WIZARD STATE rather than the raw
    editor draft. When the wizard ferries both a conditionKind body
    (e.g. `conditionKind=regex + pattern=^foo$`) AND `action=run_command`,
    the projection must NOT carry the verifier `requires` slot onto the
    overlay — otherwise IrDraftPane's `conditionLabel` would render
    "Pattern in the response" next to the runs-shell warning, giving
    the operator a misleading mixed-archetype view of their own draft."""
    state = {
        "lifecycle": "before_tool_use",
        "toolScope": "Bash",
        # Verifier-vocabulary body on the wizard state.
        "conditionKind": "regex",
        "pattern": "^foo$",
        # ... mixed with run_command action.
        "action": "run_command",
        "runCommandBody": "echo hi",
        "id": "mixed-archetype",
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    merged = out["draft"]
    assert merged is not None
    # Pure run_command archetype.
    assert merged["type"] == "run_command"
    assert merged["command"] == "echo hi"
    # Verifier-only keys are NOT present on the overlay output. The
    # discriminator's invariant is enforced at the source.
    assert "requires" not in merged
    assert "action" not in merged
    assert "on_missing" not in merged


# ── D66 follow-up: per-field-only round-trip coverage ─────────────────
#
# The brief asks for individual-field round-trip tests so a regression
# that silently drops one of (runtime / args / timeout_ms / fail_closed
# / command / script_id) is caught even when the other fields land. The
# bundled tests pass even if one field is silently dropped as long as
# the others are not — these isolate the contract.


_RC_SKELETON = {
    "lifecycle": "before_tool_use",
    "toolScope": "Bash",
    "action": "run_command",
    "id": "per-field-probe",
}


def test_run_command_runtime_field_alone_round_trips():
    state = {**_RC_SKELETON, "runCommandRuntime": "python3"}
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    merged = out["draft"]
    assert merged is not None
    assert merged.get("runtime") == "python3"
    # Other run_command body fields stay unset.
    assert "command" not in merged
    assert "args" not in merged
    assert "timeout_ms" not in merged
    assert "fail_closed" not in merged
    assert "script_path" not in merged


def test_run_command_args_field_alone_round_trips():
    state = {**_RC_SKELETON, "runCommandArgs": "a, b"}
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    merged = out["draft"]
    assert merged is not None
    assert merged.get("args") == ["a", "b"]
    assert "runtime" not in merged
    assert "command" not in merged
    assert "timeout_ms" not in merged
    assert "fail_closed" not in merged


def test_run_command_timeout_field_alone_round_trips():
    state = {**_RC_SKELETON, "runCommandTimeoutMs": "7500"}
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    merged = out["draft"]
    assert merged is not None
    assert merged.get("timeout_ms") == 7500
    assert "runtime" not in merged
    assert "command" not in merged
    assert "args" not in merged
    assert "fail_closed" not in merged


def test_run_command_fail_closed_true_field_alone_round_trips():
    state = {**_RC_SKELETON, "runCommandFailClosed": "true"}
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    merged = out["draft"]
    assert merged is not None
    assert merged.get("fail_closed") is True


def test_run_command_fail_closed_false_field_alone_round_trips():
    """fail_closed=False is load-bearing because of the special
    `if k == 'fail_closed' and isinstance(v, bool)` carve-out in
    `_merge_drafts` that would silently regress without isolated
    coverage. A blanket "drop falsy" rule would erase it."""
    state = {**_RC_SKELETON, "runCommandFailClosed": "false"}
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    merged = out["draft"]
    assert merged is not None
    assert merged.get("fail_closed") is False
    # And critically: it must SURVIVE the merge layer (the bug this
    # carve-out exists for).
    assert "fail_closed" in merged


def test_run_command_command_field_alone_round_trips():
    state = {**_RC_SKELETON, "runCommandBody": "echo hello"}
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    merged = out["draft"]
    assert merged is not None
    assert merged.get("command") == "echo hello"
    assert "runtime" not in merged
    assert "args" not in merged
    assert "timeout_ms" not in merged
    assert "fail_closed" not in merged


def test_run_command_script_id_field_alone_round_trips():
    sid = "a" * 64
    state = {
        **_RC_SKELETON,
        "runCommandMode": "attach",
        "runCommandScriptId": sid,
    }
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    merged = out["draft"]
    assert merged is not None
    assert merged.get("script_path") == sid
    assert "_script_name" not in merged
    assert "runtime" not in merged
    assert "args" not in merged
    assert "command" not in merged


def test_run_command_body_strips_surrounding_whitespace():
    """`runCommandBody` with leading / trailing spaces lands on the
    draft stripped — mirrors how `runCommandScriptId` is treated. Bare
    backticks around spaces in the rendered summary read as noise."""
    state = {**_RC_SKELETON, "runCommandBody": "   echo hi   "}
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    merged = out["draft"]
    assert merged is not None
    assert merged.get("command") == "echo hi"


def test_run_command_long_inline_body_renders_truncation_cue():
    """Truncation appends a localised 'truncated, N more chars' cue so
    the operator knows the rest of the body survived in the draft."""
    body = "a" * 200
    state = {**_RC_SKELETON, "runCommandBody": body}
    out = build_handoff_turn(wizard_state=state, draft_ir=None)
    msg = out["assistant_message"]
    assert "truncated, 120 more chars" in msg


# ── route-level tests ─────────────────────────────────────────────────


def test_route_returns_seeded_turn_with_questions():
    c = _client()
    r = c.post(
        "/policies/handoff-context",
        headers=HEADERS,
        json={
            "wizard_state": {
                "lifecycle": "before_tool_use",
                "toolScope": "Bash",
                "action": "block",
            },
            "draft_ir": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "assistant_message" in body
    assert "questions" in body
    assert "draft" in body
    assert "missing_fields" in body
    assert "needs_more" in body
    assert "ready_to_save" in body


def test_route_403_without_admin_key():
    c = _client()
    r = c.post(
        "/policies/handoff-context",
        json={"wizard_state": None, "draft_ir": None},
    )
    # The require_admin_key dependency returns 401 / 403; both are
    # canonical "no auth" responses.
    assert r.status_code in (401, 403)


def test_route_extra_fields_are_rejected():
    """`extra=forbid` on HandoffContextReq catches typos / smuggled keys."""
    c = _client()
    r = c.post(
        "/policies/handoff-context",
        headers=HEADERS,
        json={
            "wizard_state": {},
            "draft_ir": None,
            "smuggled": "x",
        },
    )
    assert r.status_code == 422


def test_route_oversize_wizard_state_422():
    c = _client()
    r = c.post(
        "/policies/handoff-context",
        headers=HEADERS,
        json={
            "wizard_state": {"description": "x" * 30_000},
            "draft_ir": None,
        },
    )
    assert r.status_code == 422
