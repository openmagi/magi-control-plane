"""Unit tests for tests/qa_harness/oracles.py (PR-C, RED first).

Tests the `canon`/`equivalent` relation and O3/O4 oracle detectors on
synthetic hand-built transcripts.  Does NOT drive HTTP; no FastAPI import.

Design reference:
  clawy docs/plans/2026-07-09-magi-cp-authoring-qa-harness-design.md
  Section 6.3 (equivalence relation), Section 7.1 (O3/O4 oracles).
"""

from __future__ import annotations

import pytest

from qa_harness.oracles import (
    OracleFailure,
    canon,
    equivalent,
    check_o3_dead_end,
    check_o4_loop,
)


# ---------------------------------------------------------------------------
# Helpers: minimal EvidencePolicy dicts for round-trip tests.
# ---------------------------------------------------------------------------

def _ev(
    event: str = "PreToolUse",
    matcher: str = "Bash",
    action: str = "block",
    requires: list | None = None,
) -> dict:
    """Build a minimal EvidencePolicy dict with explicit triple."""
    return {
        "id": "test-policy",
        "description": "",
        "trigger": {"host": "claude-code", "event": event, "matcher": matcher},
        "requires": requires if requires is not None else [
            {"step": "prompt_injection_screen", "verdict": "pass"}
        ],
        "action": action,
    }


# ---------------------------------------------------------------------------
# Section 6.3: canon() and equivalent() unit tests.
# ---------------------------------------------------------------------------

class TestCanon:
    """canon(d) = policy_to_dict(policy_from_dict(d)) applies defaulting."""

    def test_basic_roundtrip(self) -> None:
        """canon of a minimal dict is idempotent."""
        d = _ev()
        c = canon(d)
        assert isinstance(c, dict)
        # Applying canon twice is idempotent.
        assert canon(c) == c

    def test_type_key_omitted_for_evidence(self) -> None:
        """EvidencePolicy serializer omits the type key (ir.py:1387-1427)."""
        d = _ev()
        c = canon(d)
        assert "type" not in c

    def test_action_log_collapses_to_audit_via_on_missing(self) -> None:
        """_coerce_action: legacy on_missing='log' collapses to 'audit'.

        NOTE: 'log'/'allow' are NOT valid values for the 'action' key directly
        (the validator rejects them with 'action 미지원').  Collapse only
        happens when the *legacy* 'on_missing' key is used.
        """
        d = {
            **_ev(),
            "on_missing": "log",  # legacy key, no 'action' key present
        }
        # Remove the 'action' key so _coerce_action uses the on_missing path.
        d.pop("action", None)
        c = canon(d)
        assert c["action"] == "audit"

    def test_action_allow_collapses_to_audit_via_on_missing(self) -> None:
        """_coerce_action: legacy on_missing='allow' collapses to 'audit'."""
        d = {
            **_ev(),
            "on_missing": "allow",
        }
        d.pop("action", None)
        c = canon(d)
        assert c["action"] == "audit"

    def test_step_requires_no_kind_in_output(self) -> None:
        """Step requires serialise as {step, verdict} without 'kind' (ir.py:1399-1400)."""
        d = _ev(requires=[{"step": "citation_verify", "verdict": "pass"}])
        c = canon(d)
        req = c["requires"][0]
        assert "kind" not in req
        assert req["step"] == "citation_verify"
        assert req["verdict"] == "pass"

    def test_step_requires_kind_stripped(self) -> None:
        """Supplying kind='step' explicitly still strips kind from output."""
        d = _ev(requires=[{"kind": "step", "step": "citation_verify", "verdict": "pass"}])
        c = canon(d)
        req = c["requires"][0]
        assert "kind" not in req

    def test_regex_requires_keeps_kind(self) -> None:
        """Regex requires keep kind='regex' in output."""
        d = _ev(requires=[{"kind": "regex", "pattern": r"\d{6}"}])
        c = canon(d)
        req = c["requires"][0]
        assert req.get("kind") == "regex"
        assert req["pattern"] == r"\d{6}"

    def test_regex_field_path_omitted_when_empty(self) -> None:
        """field_path is omitted by serializer when empty (ir.py:1401-1409)."""
        d = _ev(requires=[{"kind": "regex", "pattern": r"\d+", "field_path": ""}])
        c = canon(d)
        req = c["requires"][0]
        assert "field_path" not in req

    def test_default_on_signature_invalid(self) -> None:
        """on_signature_invalid defaults to 'deny'."""
        d = _ev()
        c = canon(d)
        assert c.get("on_signature_invalid") == "deny"

    def test_default_gate_binary(self) -> None:
        """gate_binary defaults to the canonical magi-gate path."""
        d = _ev()
        c = canon(d)
        assert c.get("gate_binary") == "/usr/local/bin/magi-gate.sh"

    def test_default_version(self) -> None:
        """version defaults to '0.1'."""
        d = _ev()
        c = canon(d)
        assert c.get("version") == "0.1"

    def test_sentinel_re_null_by_default(self) -> None:
        """sentinel_re is None / absent when not supplied."""
        d = _ev()
        c = canon(d)
        # Should be null (None) not a non-null value.
        assert c.get("sentinel_re") is None


class TestEquivalent:
    """equivalent(target, saved) drops id/description before comparing canon."""

    def test_same_dict_is_equivalent(self) -> None:
        d = _ev()
        assert equivalent(d, d)

    def test_different_ids_still_equivalent(self) -> None:
        """id is excluded from equivalence; different ids still match."""
        target = _ev()
        saved = {**_ev(), "id": "auto-generated-id-from-flow"}
        assert equivalent(target, saved)

    def test_different_descriptions_still_equivalent(self) -> None:
        """description is excluded from equivalence."""
        target = _ev()
        saved = {**_ev(), "description": "Auto-generated description"}
        assert equivalent(target, saved)

    def test_different_action_not_equivalent(self) -> None:
        """Different actions yield non-equivalence."""
        target = _ev(action="block")
        saved = _ev(action="ask")
        assert not equivalent(target, saved)

    def test_different_event_not_equivalent(self) -> None:
        """Different trigger events yield non-equivalence."""
        target = _ev(event="PreToolUse")
        saved = _ev(event="PostToolUse")
        assert not equivalent(target, saved)

    def test_different_matcher_not_equivalent(self) -> None:
        """Different matchers yield non-equivalence."""
        target = _ev(matcher="Bash")
        saved = _ev(matcher="WebFetch")
        assert not equivalent(target, saved)

    def test_action_log_normalises_same_as_audit_via_on_missing(self) -> None:
        """target with on_missing='log' (legacy) is equivalent to saved action='audit'."""
        target = {**_ev(), "on_missing": "log"}
        target.pop("action", None)
        saved = _ev(action="audit")
        assert equivalent(target, saved)


class TestTriggerDefaultsTrap:
    """Section 6.3: under-specified target canonicalizes to wrong triple.

    This is the 'trigger-defaults trap' mentioned in the spec: if target_ir
    omits the event or matcher, policy_from_dict will fill in dataclass
    defaults (event='PreToolUse', matcher='Bash'), silently making an
    under-specified target look equal to a PreToolUse/Bash policy.

    The equivalence function itself handles any dict; it is the CORPUS
    SCHEMA that rejects under-specified scenarios.  This test PROVES the
    trap exists (to confirm the schema guard is not vacuous) and shows
    that explicitly-specified targets compare correctly.
    """

    def test_trap_exists__under_specified_looks_like_pretooluse_bash(self) -> None:
        """An under-specified trigger canonicalizes to the PreToolUse/Bash default.

        ir.py Trigger dataclass defaults: event='PreToolUse', matcher='Bash'.
        A target dict with only 'host' in its trigger omits event+matcher;
        policy_from_dict fills them in silently, making two semantically
        DIFFERENT policies compare as EQUAL under equivalent().

        The corpus schema validator rejects such under-specified targets
        (corpus.py validate_scenario explicit-triple check).  This test proves
        the trap is real (the guard is not vacuous).
        """
        _STEP = "prompt_injection_screen"
        under_specified = {
            "id": "trap-test",
            "description": "",
            "trigger": {"host": "claude-code"},  # missing event + matcher
            "requires": [{"step": _STEP, "verdict": "pass"}],
            "action": "block",
        }
        # Explicitly spelled-out PreToolUse/Bash with the same requires.
        pretooluse_bash = _ev(
            event="PreToolUse",
            matcher="Bash",
            action="block",
            requires=[{"step": _STEP, "verdict": "pass"}],
        )
        # They canonicalize to the same thing: the trap.
        assert equivalent(under_specified, pretooluse_bash), (
            "Under-specified target SHOULD canonicalize to PreToolUse/Bash default "
            "triple - this proves the trap exists and the corpus schema guard is needed"
        )

    def test_explicit_stop_not_equivalent_to_pretooluse(self) -> None:
        """Explicit Stop target is NOT equivalent to the PreToolUse default."""
        stop_target = _ev(event="Stop", matcher="*", action="audit")
        pretooluse = _ev(event="PreToolUse", matcher="*", action="audit")
        assert not equivalent(stop_target, pretooluse), (
            "Stop and PreToolUse must not be equivalent"
        )

    def test_explicit_triple_preserves_wide_event(self) -> None:
        """Wide events (e.g. Stop) survive the round-trip when spelled out."""
        stop_target = _ev(event="Stop", matcher="*", action="audit")
        assert equivalent(stop_target, stop_target)


# ---------------------------------------------------------------------------
# Section 7.1 O3: dead-end detector (synthetic transcripts).
# ---------------------------------------------------------------------------

class TestO3DeadEnd:
    """O3: fail on needs_more=True, questions==[], feasibility==null,
    and no deterministic steer markers in assistant_message.
    """

    def _make_turn(
        self,
        needs_more: bool = True,
        questions: list | None = None,
        feasibility: dict | None = None,
        assistant_message: str = "",
    ) -> dict:
        return {
            "assistant_message": assistant_message,
            "draft": None,
            "missing_fields": ["trigger"],
            "questions": questions if questions is not None else [],
            "needs_more": needs_more,
            "ready_to_save": False,
            "compound": False,
            "feasibility": feasibility,
        }

    def test_raises_on_dead_end(self) -> None:
        """A turn with needs_more=True and empty questions and no steer raises."""
        wire = self._make_turn(needs_more=True, questions=[], feasibility=None)
        with pytest.raises(OracleFailure, match="[Dd]ead.end|O3"):
            check_o3_dead_end(wire)

    def test_no_raise_when_questions_present(self) -> None:
        """No dead-end when questions are present."""
        wire = self._make_turn(
            needs_more=True,
            questions=[{"id": "q_lifecycle", "prompt": "When?", "kind": "single_select",
                        "options": ["before"], "targets_field": "lifecycle"}],
            feasibility=None,
        )
        check_o3_dead_end(wire)  # must not raise

    def test_no_raise_when_needs_more_false(self) -> None:
        """Ready-to-save turns (needs_more=False) are not dead-ends."""
        wire = self._make_turn(needs_more=False, questions=[], feasibility=None)
        check_o3_dead_end(wire)

    def test_no_raise_when_feasibility_present(self) -> None:
        """A feasibility steer CTA is not a dead-end."""
        wire = self._make_turn(
            needs_more=True,
            questions=[],
            feasibility={"runtime_id": "codex", "class": "not-expressible",
                         "code": "not_expressible", "explanation": "...",
                         "alternatives": []},
        )
        check_o3_dead_end(wire)

    def test_no_raise_when_pack_cta_in_message(self) -> None:
        """A /policy-packs/ link in assistant_message is a deterministic steer."""
        wire = self._make_turn(
            needs_more=True,
            questions=[],
            feasibility=None,
            assistant_message="Open /policy-packs/floor to add this rule.",
        )
        check_o3_dead_end(wire)

    def test_no_raise_when_scripts_link_in_message(self) -> None:
        """A /scripts link in assistant_message is a deterministic steer."""
        wire = self._make_turn(
            needs_more=True,
            questions=[],
            feasibility=None,
            assistant_message="Upload your script at /scripts first.",
        )
        check_o3_dead_end(wire)

    def test_no_raise_when_alternatives_nonempty(self) -> None:
        """Non-empty alternatives in feasibility is a deterministic steer."""
        wire = self._make_turn(
            needs_more=True,
            questions=[],
            feasibility={"runtime_id": "codex", "class": "degraded",
                         "code": "degraded", "explanation": "...",
                         "alternatives": ["Use a step verifier instead"]},
        )
        check_o3_dead_end(wire)


# ---------------------------------------------------------------------------
# Section 7.1 O4: loop detector (synthetic transcripts).
# ---------------------------------------------------------------------------

class TestO4Loop:
    """O4: identical (missing_fields, sorted question ids) on 3 consecutive turns
    while the answerer supplied protocol-valid input.
    """

    def _make_state(
        self,
        missing: list[str] | None = None,
        question_ids: list[str] | None = None,
    ) -> dict:
        """Minimal wire response snapshot for loop detection."""
        missing = missing or ["trigger"]
        question_ids = question_ids or ["q_lifecycle"]
        questions = [
            {"id": qid, "prompt": "?", "kind": "single_select",
             "options": ["opt"], "targets_field": qid.removeprefix("q_")}
            for qid in question_ids
        ]
        return {
            "assistant_message": "",
            "draft": None,
            "missing_fields": list(missing),
            "questions": questions,
            "needs_more": True,
            "ready_to_save": False,
            "compound": False,
            "feasibility": None,
        }

    def test_raises_after_three_identical_turns(self) -> None:
        """Three identical (missing, question_ids) tuples trigger O4."""
        state = self._make_state(["trigger"], ["q_lifecycle"])
        transcript = [state, state, state]
        with pytest.raises(OracleFailure, match="[Ll]oop|O4"):
            check_o4_loop(transcript)

    def test_no_raise_on_two_identical_turns(self) -> None:
        """Two identical turns are allowed (progress lag is normal)."""
        state = self._make_state(["trigger"], ["q_lifecycle"])
        transcript = [state, state]
        check_o4_loop(transcript)  # must not raise

    def test_no_raise_when_different_missing_fields(self) -> None:
        """Changing missing_fields resets the loop counter."""
        s1 = self._make_state(["trigger"], ["q_lifecycle"])
        s2 = self._make_state(["requires"], ["q_requires"])
        s3 = self._make_state(["trigger"], ["q_lifecycle"])
        check_o4_loop([s1, s2, s3])

    def test_no_raise_on_single_turn(self) -> None:
        """Single turn never triggers loop detection."""
        check_o4_loop([self._make_state()])

    def test_no_raise_on_empty_transcript(self) -> None:
        """Empty transcript never triggers loop detection."""
        check_o4_loop([])

    def test_raises_on_four_identical_turns(self) -> None:
        """Four identical turns also triggers (3 in a row suffices)."""
        state = self._make_state(["requires"], ["q_requires"])
        with pytest.raises(OracleFailure, match="[Ll]oop|O4"):
            check_o4_loop([state, state, state, state])

    def test_different_question_ids_breaks_loop(self) -> None:
        """Same missing but different question ids breaks the run."""
        s1 = self._make_state(["trigger"], ["q_lifecycle"])
        s2 = self._make_state(["trigger"], ["q_matcher"])
        s3 = self._make_state(["trigger"], ["q_lifecycle"])
        check_o4_loop([s1, s2, s3])
