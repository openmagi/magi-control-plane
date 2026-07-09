"""SCRIPTED answerer for the magi-cp authoring QA harness (PR-C, L3).

The answerer maps a wire response (from the server) and the scenario's
target_ir to a next-move dict, enabling a fully deterministic conversation
without any LLM in the user role.

Design reference:
  clawy docs/plans/2026-07-09-magi-cp-authoring-qa-harness-design.md
  Section 6.2 (SCRIPTED mode).

Import discipline: only magi_cp.policy.ir and magi_cp.policy.matrix are
imported from the production tree.  No FastAPI / web imports here.
"""

from __future__ import annotations

from typing import Any

# Lifecycle bucket values offered by q_lifecycle (from nl_compiler_interactive.py).
_LIFECYCLE_TO_EVENT: dict[str, str] = {
    "before_tool_use": "PreToolUse",
    "after_tool_use": "PostToolUse",
    "pre_final": "Stop",
}
_EVENT_TO_LIFECYCLE: dict[str, str] = {v: k for k, v in _LIFECYCLE_TO_EVENT.items()}

# The three bucket events - any other event in target_ir is a 'wide event'.
_BUCKET_EVENTS: frozenset[str] = frozenset(_LIFECYCLE_TO_EVENT.values())


class ScriptedAnswerer:
    """Goal-conditioned SCRIPTED answerer for the fake_empty CI lane.

    The answerer holds a reference to the scenario's target_ir and
    knows how to respond to each canonical question type.  It never
    calls an LLM.

    For pill questions (single_select, multi_select): returns an answers
    dict mapping question id to the matching option value.  Also returns
    the option LABEL as a userText history bubble (mirroring
    ConversationalCompose.tsx:673-689).

    For text questions: returns the appropriate value as a userText turn.

    For non-authoring scenarios (no target_ir): returns stop immediately.
    """

    def __init__(
        self,
        target_ir: dict[str, Any] | None,
        *,
        expected_outcome: str,
        compound_gate_matcher: str | None = None,
    ) -> None:
        self._target = target_ir
        self._expected_outcome = expected_outcome
        # For compound (evidence_gate) scenarios the saved policy is expanded
        # member-wise (design Section 6.3) so target_ir is null and the O1
        # round-trip oracle does not apply. The one operator decision the
        # compound wizard still needs is the gated tool (q_matcher). This
        # field supplies that legal gated-tool matcher so the answerer can
        # drive a compound scenario to a member-wise save.
        self._compound_gate_matcher = compound_gate_matcher
        # Track answered question ids to detect repeated asks.
        self._answered: set[str] = set()

    def next_move(
        self,
        wire: dict[str, Any],
    ) -> dict[str, Any]:
        """Return the next answerer move given the current wire response.

        Possible return shapes:
        - ``{"answers": {qid: value}, "label_bubble": label_text}``
          for pill questions (also append label as user bubble in history).
        - ``{"userText": text}`` for free-text questions / corrections.
        - ``{"stop": reason}`` when no action can be taken.
        """
        questions = wire.get("questions") or []
        ready = wire.get("ready_to_save", False)
        feasibility = wire.get("feasibility")

        # Terminal: server says ready (caller will save, not call answerer again).
        if ready:
            return {"stop": "ready_to_save"}

        # Terminal: non-authoring outcome indicated by feasibility CTA or
        # absence of questions with no steer.
        if feasibility is not None:
            return {"stop": f"feasibility:{feasibility.get('code', 'unknown')}"}

        if not questions:
            # Dead-end: O3 oracle will flag this; stop so the runner can
            # report the oracle failure rather than looping forever.
            return {"stop": "no_questions_no_steer"}

        # Compound (evidence_gate) scenario: target_ir is null by design
        # (member-wise oracle), but the compound wizard needs the gated tool.
        # Answer the emitted q_matcher with the scenario's compound gate
        # matcher via the answers path (the same wire the real UI sends).
        if wire.get("compound") and self._compound_gate_matcher:
            for q in questions:
                if q.get("id") == "q_matcher":
                    m = self._compound_gate_matcher
                    return {"answers": {"q_matcher": m}, "label_bubble": m}

        if self._target is None:
            # Non-authoring scenario: no target to script from; stop.
            return {"stop": "no_target_ir"}

        # Answer the first unanswered question we can handle.
        for q in questions:
            qid = q["id"]
            field = q.get("targets_field", "")
            kind = q.get("kind", "text")

            move = self._answer_question(qid, field, kind, q)
            if move is not None:
                return move

        # Fallback: no question we can answer.
        return {"stop": "unhandled_questions"}

    def _answer_question(
        self,
        qid: str,
        field: str,
        kind: str,
        q: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return a move dict for one question, or None if unhandled."""
        target = self._target
        assert target is not None  # caller checks before here

        trigger = target.get("trigger", {})
        target_event = trigger.get("event", "PreToolUse")
        target_matcher = trigger.get("matcher", "Bash")
        target_action = target.get("action", "block")
        target_requires = target.get("requires", [])

        if qid == "q_lifecycle":
            # Map the target event to the q_lifecycle bucket value.
            lifecycle_value = _EVENT_TO_LIFECYCLE.get(target_event)
            if lifecycle_value is None:
                # Wide event: being asked q_lifecycle is an O6-class failure.
                # Return stop so the oracle layer (O6) can flag it.
                return {"stop": f"wide_event_asked_q_lifecycle:{target_event}"}
            # Find the option with this value.
            label = _find_option_label(q, lifecycle_value)
            return {"answers": {qid: lifecycle_value}, "label_bubble": label}

        if qid == "q_matcher":
            # The record-only (audit-only, requires=[]) archetype has no
            # verifier and runs on the fake_empty lane (no cassette, no LLM
            # merge). There, a free-text userText matcher is never applied
            # (the extractor only recognises named tools, not "*"), so route
            # q_matcher through the ANSWERS path (the real UI pill wire,
            # answers={"q_matcher": "*"}) which _apply_answer_to_draft applies
            # deterministically. For verifier-bearing (cassette-lane) targets
            # the recorded cassette expects the matcher as a userText turn, so
            # keep the free-text move there to preserve the cassette key.
            if self._target_is_record_only():
                return {"answers": {qid: target_matcher}, "label_bubble": target_matcher}
            return {"userText": target_matcher}

        if qid == "q_requires":
            # single_select: pick the kind of the first requires entry.
            # Record-only ("emit signal") archetype: an explicit empty
            # requires list plus action=audit means the operator wants the
            # trigger recorded with NO verifier. Pick the "none" option
            # (added by the audit-only production fix) so the draft becomes
            # the record-only archetype and reaches ready_to_save.
            if self._target_is_record_only():
                label = _find_option_label(q, "none")
                return {"answers": {qid: "none"}, "label_bubble": label}
            if not target_requires:
                # No requires specified: pick 'step' as a safe default.
                req_kind = "step"
            else:
                req_kind = target_requires[0].get("kind", "step")
            label = _find_option_label(q, req_kind)
            return {"answers": {qid: req_kind}, "label_bubble": label}

        if qid == "q_requires_body":
            # text question: provide the body content from the first requires.
            if not target_requires:
                return {"userText": ""}
            req = target_requires[0]
            req_kind = req.get("kind", "step")
            if req_kind == "step":
                body = req.get("step", "")
            elif req_kind == "regex":
                body = req.get("pattern", "")
            elif req_kind == "llm_critic":
                body = req.get("criterion", "")
            elif req_kind == "shacl":
                body = req.get("shape_ttl", "")
            else:
                body = ""
            return {"userText": body}

        if qid == "q_on_missing":
            # single_select: pick the option matching the target action.
            label = _find_option_label(q, target_action)
            return {"answers": {qid: target_action}, "label_bubble": label}

        if qid == "q_id":
            # For record-only (fake_empty-lane) targets, route q_id through the
            # ANSWERS path so the id validator in _apply_answer_to_draft lands
            # it deterministically without an LLM merge. For verifier-bearing
            # (cassette-lane) targets keep the free-text move so the recorded
            # cassette key is preserved.
            target_id = target.get("id", "qa-target")
            if self._target_is_record_only():
                return {"answers": {qid: target_id}, "label_bubble": target_id}
            return {"userText": target_id}

        # Unknown question id.
        return None

    def _target_is_record_only(self) -> bool:
        """True iff the target is the audit-only record-only archetype:
        an EXPLICIT empty requires list plus action=audit. These targets
        must be authored via the q_requires "none" (record-only) option.
        """
        target = self._target
        if target is None:
            return False
        requires = target.get("requires")
        if not (isinstance(requires, list) and len(requires) == 0):
            return False
        action = target.get("action") or target.get("on_missing")
        return isinstance(action, str) and action == "audit"


def _find_option_label(q: dict[str, Any], value: str) -> str:
    """Return the label of the option with the given value, or value if not found."""
    for opt in (q.get("options") or []):
        if isinstance(opt, dict) and opt.get("value") == value:
            return opt.get("label", value)
    return value
