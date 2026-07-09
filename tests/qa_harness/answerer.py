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
    ) -> None:
        self._target = target_ir
        self._expected_outcome = expected_outcome
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
            # text question: provide the matcher directly.
            return {"userText": target_matcher}

        if qid == "q_requires":
            # single_select: pick the kind of the first requires entry.
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
            # text question: provide the target id.
            target_id = target.get("id", "qa-target")
            return {"userText": target_id}

        # Unknown question id.
        return None


def _find_option_label(q: dict[str, Any], value: str) -> str:
    """Return the label of the option with the given value, or value if not found."""
    for opt in (q.get("options") or []):
        if isinstance(opt, dict) and opt.get("value") == value:
            return opt.get("label", value)
    return value
