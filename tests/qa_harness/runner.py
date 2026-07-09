"""L3 simulated-user runner for the magi-cp authoring QA harness (PR-C).

The runner drives a full scenario-phrasing conversation via TestClient,
applying per-turn oracles and saving the resulting policy (or recording a
non-authoring outcome).

Design reference:
  clawy docs/plans/2026-07-09-magi-cp-authoring-qa-harness-design.md
  Section 6.1 (Runner core), Section 6.2 (SCRIPTED answerer), Section 7.1.

Import discipline: FastAPI's TestClient is the only web import in this file.
No other production web/cloud modules are imported here.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app

from .answerer import ScriptedAnswerer
from .oracles import (
    OracleFailure,
    check_o1_round_trip,
    check_o2_save_contradiction,
    check_o3_dead_end,
    check_o4_loop,
    check_o5_turn_bound,
    check_o6_per_turn,
    check_o7_expectation,
    check_o8_status_discipline,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ADMIN_KEY = "test-admin-key-qa"
_HEADERS = {"X-Admin-Api-Key": ADMIN_KEY}

# Source value accepted by the PutPolicyReq / CompoundPolicyReq validators.
_SAVE_SOURCE = "bot"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class _InfiniteFakeLlmProvider:
    """LLM stub that always returns a neutral empty response.

    FakeLlmProvider raises LlmProviderError when its canned list is
    exhausted.  For the fake_empty lane we need a provider that keeps
    returning the neutral response indefinitely.
    """

    _EMPTY = json.dumps({
        "assistant_message": "",
        "draft_updates": {},
        "questions": [],
    })

    def complete(self, messages: Any) -> str:  # noqa: ANN001
        return self._EMPTY


def _make_client() -> TestClient:
    """Build a TestClient with an infinite-empty-response LLM provider."""
    os.environ.setdefault("MAGI_CP_ADMIN_API_KEY", ADMIN_KEY)
    d = tempfile.mkdtemp(prefix="magi-qa-runner-")
    store_path = os.path.join(d, "policies.json")
    with open(store_path, "w") as f:
        f.write("[]")
    app = create_app(
        dsn="sqlite:///:memory:",
        policy_store_path=store_path,
        llm_compiler=_InfiniteFakeLlmProvider(),
    )
    return TestClient(app)


def _post_compile(
    client: TestClient,
    *,
    history: list[dict],
    draft_so_far: dict | None,
    answers: dict | None,
    runtime_id: str | None,
) -> tuple[int, dict[str, Any]]:
    """POST /policies/compile-interactive and return (status, body)."""
    body: dict[str, Any] = {
        "history": list(history),
        "draft_so_far": draft_so_far,
        "answers": answers,
    }
    if runtime_id is not None:
        body["runtime_id"] = runtime_id
    r = client.post("/policies/compile-interactive", headers=_HEADERS, json=body)
    if r.status_code == 200:
        return r.status_code, r.json()
    # Non-200: return status + best-effort body.
    try:
        body_data = r.json()
    except Exception:  # noqa: BLE001
        body_data = {}
    return r.status_code, body_data


def _attempt_save(
    client: TestClient,
    draft: dict[str, Any] | None,
    is_compound: bool,
) -> tuple[int, dict[str, Any] | None]:
    """Attempt to save the draft via PUT /policies/{id} or POST /policies/compound.

    Returns (status_code, response_body_or_None).
    """
    if draft is None:
        return 400, None

    if is_compound:
        r = client.post(
            "/policies/compound",
            headers=_HEADERS,
            json={"draft": draft, "source": _SAVE_SOURCE, "enabled": True},
        )
        try:
            rbody = r.json()
        except Exception:  # noqa: BLE001
            rbody = None
        return r.status_code, rbody
    else:
        policy_id = draft.get("id")
        if not policy_id:
            return 400, None
        r = client.put(
            f"/policies/{policy_id}",
            headers=_HEADERS,
            json={"policy": draft, "source": _SAVE_SOURCE, "enabled": True},
        )
        try:
            rbody = r.json()
        except Exception:  # noqa: BLE001
            rbody = None
        return r.status_code, rbody


def _has_steer_terminal(wire: dict[str, Any]) -> str | None:
    """Return a non-authoring terminal reason from the wire, or None.

    Checks for feasibility CTAs and pack CTAs - markers that the flow
    has reached a non-authoring terminal without ready_to_save.
    """
    feasibility = wire.get("feasibility")
    if feasibility is not None:
        code = feasibility.get("code", "unknown")
        return f"feasibility:{code}"
    msg = wire.get("assistant_message", "") or ""
    if "/policy-packs/" in msg:
        return "pack_cta"
    return None


# ---------------------------------------------------------------------------
# Public: TranscriptRecord + run_scenario
# ---------------------------------------------------------------------------

class TranscriptRecord:
    """Result of running a scenario-phrasing through the runner."""

    def __init__(self, scenario_id: str, phrasing_idx: int) -> None:
        self.scenario_id = scenario_id
        self.phrasing_idx = phrasing_idx
        self.turns: list[tuple[dict, dict]] = []
        self.save_status: int | None = None
        self.save_body: dict | None = None
        self.outcome: str = "steered"
        self.oracle_failures: list[OracleFailure] = []


def run_scenario(
    scenario: dict[str, Any],
    phrasing_idx: int = 0,
    *,
    client: TestClient | None = None,
) -> TranscriptRecord:
    """Run one scenario-phrasing and return a TranscriptRecord.

    All oracle failures are COLLECTED (not raised) and stored in
    ``record.oracle_failures``.  The caller (test parametrize) raises
    if the list is non-empty for stable scenarios.

    Conversation state machine (Section 6.1):
    - Each iteration posts ONE compile-interactive call.
    - After each response the answerer is consulted for the next move.
    - A pill answer (answers dict) is sent as a SEPARATE POST in the same
      iteration (with answers= populated).
    - A userText answer is appended to history; the next iteration sends it.
    - ready_to_save triggers the save call.
    - Oracle failures are collected, not raised.
    """
    sid = scenario["id"]
    phrasings = scenario["phrasings"]
    phrasing_text = phrasings[phrasing_idx]["text"]
    target_ir = scenario.get("target_ir")
    expected = scenario["expected"]
    expected_outcome = expected["outcome"]
    max_turns = expected.get("max_turns", 8)
    language = scenario.get("language", "en")
    runtime_id = scenario.get("runtime_id")
    is_rejected_422 = expected_outcome == "rejected_422"

    record = TranscriptRecord(sid, phrasing_idx)

    def _fail(exc: OracleFailure) -> None:
        record.oracle_failures.append(exc)

    c = client or _make_client()
    answerer = ScriptedAnswerer(target_ir, expected_outcome=expected_outcome)

    # Conversation state.
    history: list[dict[str, str]] = [{"role": "user", "content": phrasing_text}]
    draft_so_far: dict[str, Any] | None = None
    turn_count = 0
    wire_transcript: list[dict[str, Any]] = []
    last_wire: dict[str, Any] | None = None

    # ---------------------------------------------------------------------------
    # For rejected_422 scenarios: the first POST is expected to 422 (from
    # malformed content embedded in the phrasing or answers).  The runner sends
    # an intentionally-invalid answers dict to trigger the 422.
    # ---------------------------------------------------------------------------
    if is_rejected_422:
        # Rejected_422 scenarios test various malformed inputs.
        # Select the answers dict and history based on the phrasing note.
        phrasing_note = (phrasings[phrasing_idx].get("note") or "").lower()

        # Determine the request shape that should trigger 422.
        malformed_answers: dict[str, Any] | None = None
        malformed_history: list[dict[str, str]] = list(history)

        if "answer-coherence" in phrasing_note or "no prior question" in phrasing_note:
            # The coherence guard rejects answers with q_ids not in the prior
            # turn's questions.  On turn 1 with empty history, any valid-format
            # q_id that was never emitted triggers the guard.
            # Use a never-emitted field id to trigger the 422.
            malformed_answers = {"q_random_never_emitted": "value"}
            malformed_history = []
        elif "answer shape" in phrasing_note or "malformed answer" in phrasing_note:
            # Send an answers dict with an invalid key format (doesn't start
            # with q_).  The compiler validates answer key names.
            malformed_answers = {"__not_a_field__": "value"}
        elif "oversized" in phrasing_note or "pre-aggregate" in phrasing_note:
            # Build a history entry that exceeds the per-message limit or the
            # aggregate cap.  MAX_USER_MESSAGE_CHARS = 2000 (per message).
            # Use a 2001-char user turn to trigger the Pydantic boundary 422.
            malformed_history = [{"role": "user", "content": "A" * 2001}]
        # else: oversized phrasing from phrasing text - just send it normally.

        status, body = _post_compile(
            c,
            history=malformed_history,
            draft_so_far=None,
            answers=malformed_answers,
            runtime_id=runtime_id,
        )
        record.turns.append((
            {"history": malformed_history, "answers": malformed_answers},
            body,
        ))
        try:
            check_o8_status_discipline(status, is_rejected_422_scenario=True)
        except OracleFailure as e:
            _fail(e)
        record.outcome = "rejected_422" if status == 422 else "steered"
        try:
            check_o7_expectation(record.outcome, expected_outcome)
        except OracleFailure as e:
            _fail(e)
        return record

    # ---------------------------------------------------------------------------
    # Normal authoring / non-authoring flow.
    # ---------------------------------------------------------------------------
    while True:
        # O5: pre-check turn budget.
        if turn_count >= max_turns:
            try:
                check_o5_turn_bound(turn_count, max_turns)
            except OracleFailure as e:
                _fail(e)
            break

        # Post the compile turn (no answers - answers are sent in a follow-up
        # post below if the answerer returns a pill move).
        status, wire = _post_compile(
            c,
            history=list(history),
            draft_so_far=draft_so_far,
            answers=None,
            runtime_id=runtime_id,
        )
        turn_count += 1

        # O8: status discipline.
        try:
            check_o8_status_discipline(status, is_rejected_422_scenario=is_rejected_422)
        except OracleFailure as e:
            _fail(e)

        if status != 200:
            record.outcome = "rejected_422"
            break

        last_wire = wire
        wire_transcript.append(wire)
        record.turns.append(({
            "history": list(history),
            "draft_so_far": draft_so_far,
            "answers": None,
        }, wire))

        # Append assistant_message to history.
        asst_msg = wire.get("assistant_message", "") or ""
        if asst_msg:
            history.append({"role": "assistant", "content": asst_msg})
        draft_so_far = wire.get("draft")

        # Per-turn oracles.
        try:
            check_o6_per_turn(wire, language=language)
        except OracleFailure as e:
            _fail(e)
        try:
            check_o3_dead_end(wire)
        except OracleFailure as e:
            _fail(e)
            break
        try:
            check_o4_loop(wire_transcript)
        except OracleFailure as e:
            _fail(e)
            break

        # Terminal: ready_to_save.
        if wire.get("ready_to_save"):
            draft = wire.get("draft")
            is_compound = wire.get("compound", False)
            save_status, save_body = _attempt_save(c, draft, is_compound)
            record.save_status = save_status
            record.save_body = save_body
            try:
                check_o2_save_contradiction(True, save_status)
            except OracleFailure as e:
                _fail(e)
            if target_ir is not None and draft is not None and 200 <= save_status < 300:
                try:
                    check_o1_round_trip(target_ir, draft)
                except OracleFailure as e:
                    _fail(e)
            record.outcome = "saved"
            break

        # Non-authoring terminal: feasibility / pack CTA.
        steer_reason = _has_steer_terminal(wire)
        if steer_reason is not None:
            record.outcome = _classify_steer(steer_reason, wire)
            break

        # Ask the answerer what to do next.
        move = answerer.next_move(wire)

        if "stop" in move:
            record.outcome = _classify_steer(move["stop"], wire)
            break

        if "answers" in move:
            # Pill move: send the answers in a separate POST (same turn slot).
            label_bubble = move.get("label_bubble", "")
            if label_bubble:
                history.append({"role": "user", "content": label_bubble})

            if turn_count >= max_turns:
                try:
                    check_o5_turn_bound(turn_count + 1, max_turns)
                except OracleFailure as e:
                    _fail(e)
                break

            status2, wire2 = _post_compile(
                c,
                history=list(history),
                draft_so_far=draft_so_far,
                answers=move["answers"],
                runtime_id=runtime_id,
            )
            turn_count += 1

            try:
                check_o8_status_discipline(status2, is_rejected_422_scenario=is_rejected_422)
            except OracleFailure as e:
                _fail(e)

            if status2 != 200:
                record.outcome = "rejected_422"
                break

            last_wire = wire2
            wire_transcript.append(wire2)
            record.turns.append(({
                "history": list(history),
                "draft_so_far": draft_so_far,
                "answers": move["answers"],
            }, wire2))

            asst_msg2 = wire2.get("assistant_message", "") or ""
            if asst_msg2:
                history.append({"role": "assistant", "content": asst_msg2})
            draft_so_far = wire2.get("draft")

            try:
                check_o6_per_turn(wire2, language=language)
            except OracleFailure as e:
                _fail(e)
            try:
                check_o3_dead_end(wire2)
            except OracleFailure as e:
                _fail(e)
                break
            try:
                check_o4_loop(wire_transcript)
            except OracleFailure as e:
                _fail(e)
                break

            if wire2.get("ready_to_save"):
                draft2 = wire2.get("draft")
                is_compound2 = wire2.get("compound", False)
                save_status2, save_body2 = _attempt_save(c, draft2, is_compound2)
                record.save_status = save_status2
                record.save_body = save_body2
                try:
                    check_o2_save_contradiction(True, save_status2)
                except OracleFailure as e:
                    _fail(e)
                if target_ir is not None and draft2 is not None and 200 <= save_status2 < 300:
                    try:
                        check_o1_round_trip(target_ir, draft2)
                    except OracleFailure as e:
                        _fail(e)
                record.outcome = "saved"
                break

            steer2 = _has_steer_terminal(wire2)
            if steer2 is not None:
                record.outcome = _classify_steer(steer2, wire2)
                break

            # Continue the outer loop with wire2 as the effective last state.
            # The outer loop will now call the answerer on wire2.
            last_wire = wire2
            # Update answerer state check: feed wire2 back to answerer.
            move2 = answerer.next_move(wire2)
            if "stop" in move2:
                record.outcome = _classify_steer(move2["stop"], wire2)
                break
            if "userText" in move2:
                history.append({"role": "user", "content": move2["userText"]})
            # If another pill, the outer loop handles it next iteration.
            # Continue outer loop.

        elif "userText" in move:
            # Free-text move: append to history, outer loop sends next turn.
            history.append({"role": "user", "content": move["userText"]})
            # Continue outer loop.

    # Final O5 check.
    if turn_count > max_turns and not any(
        isinstance(f, OracleFailure) and "O5" in f.oracle
        for f in record.oracle_failures
    ):
        try:
            check_o5_turn_bound(turn_count, max_turns)
        except OracleFailure as e:
            _fail(e)

    # O7: expectation match.
    feasibility_code = None
    if last_wire and last_wire.get("feasibility"):
        feasibility_code = last_wire["feasibility"].get("code")
    try:
        check_o7_expectation(
            record.outcome,
            expected_outcome,
            feasibility_code=feasibility_code,
            expected_feasibility_code=expected.get("feasibility_code"),
        )
    except OracleFailure as e:
        _fail(e)

    return record


def _classify_steer(reason: str, wire: dict[str, Any]) -> str:
    """Map a stop/steer reason to an outcome string."""
    if reason == "pack_cta":
        return "pack_cta"
    if reason.startswith("feasibility:"):
        code = reason.split(":", 1)[1]
        if "infeasible" in code or "not_expressible" in code:
            return "infeasible"
        return "steered"
    msg = wire.get("assistant_message", "") or ""
    if "/policy-packs/" in msg:
        return "pack_cta"
    alternatives = (wire.get("feasibility") or {}).get("alternatives") or []
    if alternatives:
        return "handoff_cta"
    return "steered"
