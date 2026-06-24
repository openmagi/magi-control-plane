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
