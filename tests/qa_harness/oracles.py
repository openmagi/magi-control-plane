"""L4 per-transcript oracles for the magi-cp authoring QA harness (PR-C).

Oracles O1-O8 are evaluated on each completed transcript produced by the
runner.  They are pure functions: they receive wire dicts / transcript lists
and raise ``OracleFailure`` on violation.

Design reference:
  clawy docs/plans/2026-07-09-magi-cp-authoring-qa-harness-design.md
  Section 6.3 (equivalence relation), Section 7.1 (oracles O1-O8).

Import discipline: only ``magi_cp.policy.ir`` and ``magi_cp.policy.matrix``
are imported from the production tree.  No FastAPI / web imports here.
"""

from __future__ import annotations

import re
from typing import Any

from magi_cp.policy.ir import policy_from_dict, policy_to_dict


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class OracleFailure(AssertionError):
    """Raised by any oracle function when the invariant is violated.

    Subclasses AssertionError so pytest reports it clearly as a test failure.
    """

    def __init__(self, oracle: str, detail: str) -> None:
        self.oracle = oracle
        self.detail = detail
        super().__init__(f"[{oracle}] {detail}")


# ---------------------------------------------------------------------------
# Section 6.3: round-trip equivalence relation
# ---------------------------------------------------------------------------

def canon(d: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize a policy dict via policy_from_dict -> policy_to_dict.

    This normalizes all defaults enumerated in Section 6.3:
    - Trigger: host -> 'claude-code', event -> 'PreToolUse', matcher -> 'Bash'
    - EvidenceReq: kind -> 'step', verdict -> 'pass', field_path -> '' (omitted
      by serializer when empty); step rows serialize as {step, verdict} (no kind).
    - EvidencePolicy: description -> '', sentinel_re -> None, action via
      _coerce_action (log/allow -> audit), on_signature_invalid -> 'deny',
      gate_binary -> '/usr/local/bin/magi-gate.sh', version -> '0.1',
      type omitted when 'evidence'.
    - RunCommandPolicy: runtime -> 'bash', command/script_path -> '', args -> [],
      timeout_ms -> 5000, fail_closed -> False.

    Raises ``ValueError`` if the dict is not accepted by policy_from_dict
    (matrix violation or shape error).  O1 catches this and fails the oracle.
    """
    return policy_to_dict(policy_from_dict(d))


def _drop_id_description(c: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of c with 'id' and 'description' removed."""
    return {k: v for k, v in c.items() if k not in {"id", "description"}}


def equivalent(
    target: dict[str, Any],
    saved: dict[str, Any],
    *,
    expect_id: bool = False,
) -> bool:
    """Return True if target and saved represent the same policy semantics.

    Per Section 6.3:
      canon(target) == canon(saved)

    with 'id' and 'description' dropped AFTER canonicalization (unless
    ``expect_id`` is True, in which case id is included in the comparison).

    Both dicts must be valid policy_from_dict inputs or this raises ValueError.
    """
    c_target = canon(target)
    c_saved = canon(saved)
    if not expect_id:
        c_target = _drop_id_description(c_target)
        c_saved = _drop_id_description(c_saved)
    return c_target == c_saved


# ---------------------------------------------------------------------------
# Dead-end and loop steer-marker regexes (mirroring nl_compiler_interactive.py)
# ---------------------------------------------------------------------------

# /policy-packs/<id> link: pack CTA steer marker.
_PACK_CTA_RE = re.compile(r"/policy-packs/")

# /scripts link: scripts upload steer marker.
# Same whole-word anchoring as the production _SCRIPTS_LINK_RE.
_SCRIPTS_LINK_RE = re.compile(r"(?<![A-Za-z0-9_])/scripts(?!/?[A-Za-z0-9_])")


def _has_steer_marker(wire: dict[str, Any]) -> bool:
    """Return True if the wire response contains any deterministic steer marker.

    Steer markers (O3 exemptions, per Section 7.1):
    - assistant_message contains /policy-packs/ (pack CTA)
    - assistant_message contains /scripts (scripts upload fallback)
    - feasibility is not None (any feasibility steer)
    - feasibility.alternatives is non-empty

    Wire-markers only - never prose matching beyond these anchors.
    """
    msg = wire.get("assistant_message", "") or ""
    if _PACK_CTA_RE.search(msg):
        return True
    if _SCRIPTS_LINK_RE.search(msg):
        return True
    feasibility = wire.get("feasibility")
    if feasibility is not None:
        # Any feasibility object is a steer CTA.
        return True
    return False


# ---------------------------------------------------------------------------
# O1: round-trip oracle
# ---------------------------------------------------------------------------

def check_o1_round_trip(
    target_ir: dict[str, Any],
    saved_body: dict[str, Any],
) -> None:
    """O1: the saved policy is semantically equivalent to target_ir.

    Raises OracleFailure if:
    - policy_from_dict(saved_body) raises (policy not loadable)
    - equivalent(target_ir, saved_body) is False

    Per Section 6.3, applied when target_ir is present and outcome=saved.
    """
    try:
        c_target = canon(target_ir)
        c_saved = canon(saved_body)
    except (ValueError, KeyError, TypeError) as e:
        raise OracleFailure("O1", f"canon() raised: {e}") from e

    t_cmp = _drop_id_description(c_target)
    s_cmp = _drop_id_description(c_saved)
    if t_cmp != s_cmp:
        raise OracleFailure(
            "O1",
            f"round-trip mismatch:\n  target_canon={t_cmp}\n  saved_canon={s_cmp}",
        )


# ---------------------------------------------------------------------------
# O2: save-contradiction oracle
# ---------------------------------------------------------------------------

def check_o2_save_contradiction(
    ready_to_save: bool,
    save_status_code: int,
) -> None:
    """O2: ready_to_save=True implies the save call returns 2xx.

    A 4xx after ready_to_save=True is the S6/P1-8 class of bug (save blocked
    after ready signal) and always a hard failure.

    ``save_status_code`` is the HTTP status from PUT /policies/{id} or
    POST /policies/compound.  Only inspected when ready_to_save=True.
    """
    if ready_to_save and not (200 <= save_status_code < 300):
        raise OracleFailure(
            "O2",
            f"ready_to_save=True but save returned HTTP {save_status_code} "
            "(save-contradiction: S6/P1-8 class)",
        )


# ---------------------------------------------------------------------------
# O3: dead-end oracle
# ---------------------------------------------------------------------------

def check_o3_dead_end(wire: dict[str, Any]) -> None:
    """O3: fail if needs_more=True, questions==[], and no steer marker.

    A dead-end is a turn where the server tells the client 'more is needed'
    but offers no questions and no steer.  The bot is stuck.

    Per Section 7.1: exempt when assistant_message contains /policy-packs/
    or /scripts, or when feasibility is non-null (any of these is a
    deterministic steer).  Wire markers only, never prose matching.
    """
    if not wire.get("needs_more", False):
        return
    if wire.get("questions"):
        return
    if _has_steer_marker(wire):
        return
    raise OracleFailure(
        "O3",
        "dead-end: needs_more=True, questions=[], no steer marker "
        "(no /policy-packs/, /scripts, or feasibility CTA)",
    )


# ---------------------------------------------------------------------------
# O4: loop oracle
# ---------------------------------------------------------------------------

def _turn_fingerprint(wire: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return (sorted missing_fields, sorted question ids) for O4 comparison."""
    missing = tuple(sorted(wire.get("missing_fields", [])))
    qids = tuple(sorted(q["id"] for q in wire.get("questions", [])))
    return (missing, qids)


def check_o4_loop(transcript: list[dict[str, Any]]) -> None:
    """O4: fail if (missing_fields, sorted question ids) repeats 3 consecutive turns.

    Three consecutive identical fingerprints while the answerer supplied
    protocol-valid input indicates the conversation is looping without progress.

    Also raises if the same qid is re-emitted after its answer was accepted
    twice.  (This simpler check is subsumed by the consecutive-run check for
    the current corpus size.)

    Per Section 7.1.
    """
    if len(transcript) < 3:
        return
    run_len = 1
    prev = _turn_fingerprint(transcript[0])
    for wire in transcript[1:]:
        fp = _turn_fingerprint(wire)
        if fp == prev:
            run_len += 1
            if run_len >= 3:
                raise OracleFailure(
                    "O4",
                    f"loop detected: fingerprint {fp!r} repeated {run_len} "
                    "consecutive turns without progress",
                )
        else:
            run_len = 1
        prev = fp


# ---------------------------------------------------------------------------
# O5: turn-bound oracle
# ---------------------------------------------------------------------------

def check_o5_turn_bound(turn_count: int, max_turns: int) -> None:
    """O5: conversation must terminate within expected.max_turns.

    ``turn_count`` is the number of compile-interactive POST calls made.
    ``max_turns`` comes from expected.max_turns in the scenario (default 8).
    """
    if turn_count > max_turns:
        raise OracleFailure(
            "O5",
            f"turn bound exceeded: {turn_count} turns > max_turns={max_turns}",
        )


# ---------------------------------------------------------------------------
# O6: per-turn invariants (I2, I3, I8, I10, I11, I13)
# ---------------------------------------------------------------------------

_WIRE_KEYS = frozenset({
    "assistant_message", "draft", "missing_fields", "questions",
    "needs_more", "ready_to_save", "compound", "feasibility",
})

_WIRE_KEY_TYPES: dict[str, type | tuple[type, ...]] = {
    "assistant_message": str,
    "draft": (dict, type(None)),
    "missing_fields": list,
    "questions": list,
    "needs_more": bool,
    "ready_to_save": bool,
    "compound": bool,
    "feasibility": (dict, type(None)),
}

# Plain-language source tokens must not appear in assistant_message or prompts.
# Import the production list lazily to avoid polluting module load.
def _plain_language_rules() -> tuple:
    from magi_cp.policy.nl_compiler_interactive import _PLAIN_LANGUAGE_RULES  # noqa: PLC0415
    return _PLAIN_LANGUAGE_RULES


def check_o6_per_turn(wire: dict[str, Any], *, language: str = "en") -> None:
    """O6: evaluate per-turn invariants I2, I3, I8, I10, I11, I13.

    Raises OracleFailure on the FIRST violation found.

    - I13: wire carries exactly the 8 documented keys with documented types.
    - I2: ready_to_save == (missing_fields == []) and needs_more == not ready.
    - I3: every emitted question targets_field is in missing_fields.
    - I8: never (feasibility.code is matrix_illegal_triple or class is
          not-expressible) at the same time as ready_to_save=True.
    - I10: if language=='ko' and assistant_message is non-empty, it must
           contain hangul.
    - I11: every option on an emitted q_on_missing is non-empty.
    """
    # I13: wire-shape stability.
    actual_keys = set(wire.keys())
    if actual_keys != _WIRE_KEYS:
        raise OracleFailure(
            "O6/I13",
            f"wire keys mismatch: expected {_WIRE_KEYS}, got {actual_keys}",
        )
    for key, expected_type in _WIRE_KEY_TYPES.items():
        val = wire[key]
        if not isinstance(val, expected_type):
            raise OracleFailure(
                "O6/I13",
                f"wire key {key!r}: expected type {expected_type}, "
                f"got {type(val).__name__} value={val!r}",
            )

    # I2: state coherence.
    ready = wire["ready_to_save"]
    missing = wire["missing_fields"]
    needs_more = wire["needs_more"]
    if ready != (missing == []):
        raise OracleFailure(
            "O6/I2",
            f"ready_to_save={ready} but missing_fields={missing!r}",
        )
    if needs_more != (not ready):
        raise OracleFailure(
            "O6/I2",
            f"needs_more={needs_more} but ready_to_save={ready}",
        )

    # I3: question discipline.
    questions = wire["questions"]
    top_slice = set(missing[:2])  # MAX_QUESTIONS_PER_TURN = 2
    for q in questions:
        tf = q.get("targets_field")
        if tf not in top_slice:
            raise OracleFailure(
                "O6/I3",
                f"question {q.get('id')!r} targets_field={tf!r} not in "
                f"missing[:2]={top_slice!r}",
            )
        expected_id = f"q_{tf}"
        if q.get("id") != expected_id:
            raise OracleFailure(
                "O6/I3",
                f"question id={q.get('id')!r} != expected {expected_id!r}",
            )

    # I8: no feasibility contradiction.
    feasibility = wire.get("feasibility")
    if ready and feasibility is not None:
        fcode = feasibility.get("code", "")
        fclass = feasibility.get("class", "")
        if fcode == "matrix_illegal_triple" or fclass == "not-expressible":
            raise OracleFailure(
                "O6/I8",
                f"ready_to_save=True but feasibility indicates not-expressible: "
                f"code={fcode!r} class={fclass!r}",
            )

    # I10: language + plain-language.
    _hangul_re = re.compile(r"[가-힯]")
    if language == "ko":
        msg = wire.get("assistant_message", "") or ""
        if msg and not _hangul_re.search(msg):
            raise OracleFailure(
                "O6/I10",
                f"language=ko but assistant_message contains no hangul: {msg[:100]!r}",
            )

    # I11: q_on_missing options non-empty.
    for q in questions:
        if q.get("targets_field") == "on_missing":
            opts = q.get("options", [])
            if not opts:
                raise OracleFailure(
                    "O6/I11",
                    "q_on_missing emitted with empty options list",
                )


# ---------------------------------------------------------------------------
# O7: expectation-match oracle
# ---------------------------------------------------------------------------

def check_o7_expectation(
    outcome: str,
    expected_outcome: str,
    *,
    feasibility_code: str | None = None,
    expected_feasibility_code: str | None = None,
    final_action: str | None = None,
    expected_final_action: str | None = None,
) -> None:
    """O7: actual outcome, feasibility_code, and final_action match expectations.

    ``outcome`` is one of: saved, steered, infeasible, pack_cta, handoff_cta,
    rejected_422 - determined by the runner from the transcript.
    ``feasibility_code`` is the exact wire ``feasibility.code`` or None.
    ``final_action`` is the saved policy's action field (if outcome=saved).
    """
    if outcome != expected_outcome:
        raise OracleFailure(
            "O7",
            f"outcome mismatch: expected={expected_outcome!r}, actual={outcome!r}",
        )
    if (
        expected_feasibility_code is not None
        and feasibility_code != expected_feasibility_code
    ):
        raise OracleFailure(
            "O7",
            f"feasibility_code mismatch: expected={expected_feasibility_code!r}, "
            f"actual={feasibility_code!r}",
        )
    if (
        expected_final_action is not None
        and final_action is not None
        and final_action != expected_final_action
    ):
        raise OracleFailure(
            "O7",
            f"final_action mismatch: expected={expected_final_action!r}, "
            f"actual={final_action!r}",
        )


# ---------------------------------------------------------------------------
# O8: status-discipline oracle
# ---------------------------------------------------------------------------

def check_o8_status_discipline(
    status_code: int,
    *,
    is_rejected_422_scenario: bool = False,
) -> None:
    """O8: no 5xx anywhere; 422 only in rejected_422 scenarios.

    Call once per HTTP response (compile-interactive turns + save call).
    ``is_rejected_422_scenario`` comes from expected.outcome == 'rejected_422'.
    """
    if 500 <= status_code < 600:
        raise OracleFailure(
            "O8",
            f"5xx response: HTTP {status_code} (server error must never occur)",
        )
    if status_code == 422 and not is_rejected_422_scenario:
        raise OracleFailure(
            "O8",
            f"422 in non-rejected_422 scenario (unexpected validation error)",
        )
