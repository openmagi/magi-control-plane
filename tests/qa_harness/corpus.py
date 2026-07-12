"""Corpus loader and validator for the magi-cp authoring QA harness (L2).

This module loads the hand-authored scenario fixtures under
``tests/qa_corpus/scenarios/*.json`` and validates each one against the
schema documented in ``tests/qa_corpus/README.md``.

Design references (clawy docs/plans):
- 2026-07-09-magi-cp-authoring-qa-harness-design.md, Sections 0.3, 5.1, 5.2.
- 2026-07-06-magi-cp-conversational-authoring-coverage-audit.md (S-rows).

Import discipline: only ``magi_cp.policy.ir`` and ``magi_cp.policy.matrix``
are imported. Those two modules are free of the cloud/web import chain, so
this loader never drags FastAPI or the web app into a plain schema test.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict

from magi_cp.policy.ir import policy_from_dict
from magi_cp.policy.matrix import LEGAL_COMBINATIONS, matcher_class_of

# --------------------------------------------------------------------------
# Enums (kept in sync with README.md field semantics).
# --------------------------------------------------------------------------

CATEGORY_ENUM = frozenset(
    {
        "happy_path",
        "wide_event",
        "negated_enforce",
        "enforce_verb",
        "overtrigger_bait",
        "ambiguous",
        "adversarial_injection",
        "malformed",
        "out_of_scope",
        "infeasible_runtime",
        "archetype_run_command",
        "archetype_compound",
        "pack_shaped",
    }
)

OUTCOME_ENUM = frozenset(
    {
        "saved",
        "steered",
        "infeasible",
        "pack_cta",
        "handoff_cta",
        "rejected_422",
    }
)

ENGINE_ENUM = frozenset({"fake_empty", "cassette"})
LANGUAGE_ENUM = frozenset({"ko", "en"})


class Phrasing(TypedDict):
    text: str
    note: str


class Expected(TypedDict):
    outcome: str
    feasibility_code: str | None
    max_turns: int


class Provenance(TypedDict):
    source: str
    generated_by: str
    reviewed: bool


class Scenario(TypedDict):
    schema_version: int
    id: str
    category: str
    language: str
    style: str
    runtime_id: str | None
    engine: str
    stable: bool
    known_limitation: bool
    target_ir: dict[str, Any] | None
    # Optional. For compound (evidence_gate / archetype_compound) scenarios
    # the saved policy is expanded MEMBER-WISE (design Section 6.3) so
    # target_ir stays null and the O1 round-trip oracle does not apply. The
    # one operator decision the compound wizard still needs is the gated tool
    # (q_matcher). This field supplies that legal gated-tool matcher so the
    # scripted answerer can drive a compound scenario to a member-wise save.
    compound_gate_matcher: str | None
    expected: Expected
    phrasings: list[Phrasing]
    provenance: Provenance


def load_scenarios(dir: str | Path) -> list[Scenario]:
    """Load every ``*.json`` scenario under ``<dir>/scenarios/``.

    ``dir`` is the corpus root (the directory that CONTAINS ``scenarios/``).
    Files are returned sorted by filename for deterministic ordering.
    """
    root = Path(dir)
    scen_dir = root / "scenarios"
    if not scen_dir.is_dir():
        raise ValueError(f"scenarios directory not found under {root}")
    scenarios: list[Scenario] = []
    for path in sorted(scen_dir.glob("*.json")):
        with path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
        raw["__file_stem__"] = path.stem
        scenarios.append(raw)  # type: ignore[arg-type]
    return scenarios


def _matcher_class_name(matcher: str) -> str:
    """Return the MatcherClass value string for a concrete matcher."""
    return matcher_class_of(matcher).value


def validate_scenario(
    s: Scenario, all_ids: set[str] | None = None
) -> None:
    """Validate one scenario dict against the schema.

    Raises ``ValueError`` with a specific message on the first rule
    violation. When ``all_ids`` is provided, the scenario id is checked
    for uniqueness and, on success, added to the set.
    """
    file_stem = s.get("__file_stem__")  # type: ignore[assignment]

    # schema_version present.
    if "schema_version" not in s:
        raise ValueError("scenario missing schema_version")

    # id present and matches filename.
    sid = s.get("id")
    if not sid or not isinstance(sid, str):
        raise ValueError("scenario missing a non-empty string id")
    if file_stem is not None and file_stem != sid:
        raise ValueError(
            f"scenario id {sid!r} does not match filename stem {file_stem!r}"
        )

    # category enum.
    category = s.get("category")
    if category not in CATEGORY_ENUM:
        raise ValueError(
            f"scenario {sid!r} has unknown category {category!r}; "
            f"must be one of CATEGORY_ENUM"
        )

    # language enum.
    language = s.get("language")
    if language not in LANGUAGE_ENUM:
        raise ValueError(
            f"scenario {sid!r} language must be 'ko' or 'en', got {language!r}"
        )

    # engine enum.
    engine = s.get("engine")
    if engine not in ENGINE_ENUM:
        raise ValueError(
            f"scenario {sid!r} engine must be 'fake_empty' or 'cassette', "
            f"got {engine!r}"
        )

    # boolean flags.
    if not isinstance(s.get("stable"), bool):
        raise ValueError(f"scenario {sid!r} stable must be a boolean")
    if not isinstance(s.get("known_limitation"), bool):
        raise ValueError(
            f"scenario {sid!r} known_limitation must be a boolean"
        )

    # expected.outcome enum.
    expected = s.get("expected")
    if not isinstance(expected, dict):
        raise ValueError(f"scenario {sid!r} missing expected block")
    outcome = expected.get("outcome")
    if outcome not in OUTCOME_ENUM:
        raise ValueError(
            f"scenario {sid!r} has unknown expected.outcome {outcome!r}; "
            f"must be one of OUTCOME_ENUM"
        )

    # at least one phrasing.
    phrasings = s.get("phrasings")
    if not isinstance(phrasings, list) or len(phrasings) == 0:
        raise ValueError(
            f"scenario {sid!r} must have at least one phrasing"
        )
    for i, ph in enumerate(phrasings):
        if not isinstance(ph, dict) or not ph.get("text"):
            raise ValueError(
                f"scenario {sid!r} phrasing[{i}] must have non-empty text"
            )

    # target_ir rules.
    target_ir = s.get("target_ir")
    if target_ir is not None:
        if not isinstance(target_ir, dict):
            raise ValueError(
                f"scenario {sid!r} target_ir must be null or an object"
            )
        trigger = target_ir.get("trigger")
        if not isinstance(trigger, dict):
            raise ValueError(
                f"scenario {sid!r} target_ir must carry an explicit trigger"
            )
        # Explicit-triple rule (design Section 0.3): host, event AND matcher
        # must all be present. Trigger dataclass defaults would silently
        # canonicalize a missing field into a WRONG valid triple.
        for field in ("host", "event", "matcher"):
            if field not in trigger:
                raise ValueError(
                    f"scenario {sid!r} target_ir trigger is missing the "
                    f"explicit {field!r} field (explicit triple required)"
                )

        # (event, matcher_class, action) must be in LEGAL_COMBINATIONS.
        # Checked BEFORE policy_from_dict so an illegal triple produces the
        # specific legality message (policy_from_dict also gates the matrix
        # inside EvidencePolicy.validate, but its message is generic).
        event = trigger["event"]
        matcher = trigger["matcher"]
        action = target_ir.get("action", "block")
        try:
            mclass = matcher_class_of(matcher)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"scenario {sid!r} target_ir matcher {matcher!r} could not "
                f"be classified: {exc}"
            ) from exc
        if (event, mclass, action) not in LEGAL_COMBINATIONS:
            raise ValueError(
                f"scenario {sid!r} target_ir triple "
                f"({event}, {mclass.value}, {action}) is not in "
                f"LEGAL_COMBINATIONS"
            )

        # policy_from_dict must accept it (loadability + shape).
        try:
            policy_from_dict(target_ir)
        except Exception as exc:  # noqa: BLE001 - re-raise as ValueError
            raise ValueError(
                f"scenario {sid!r} target_ir is not loadable via "
                f"policy_from_dict: {exc}"
            ) from exc

    # compound_gate_matcher (optional): a legal gated-tool matcher the
    # scripted answerer supplies for a compound (member-wise) save. Only
    # meaningful when target_ir is null (compound archetype).
    cgm = s.get("compound_gate_matcher")
    if cgm is not None:
        if not isinstance(cgm, str) or not cgm.strip():
            raise ValueError(
                f"scenario {sid!r} compound_gate_matcher must be a non-empty "
                f"string when present"
            )
        if target_ir is not None:
            raise ValueError(
                f"scenario {sid!r} compound_gate_matcher is only valid with a "
                f"null target_ir (compound member-wise save)"
            )

    # uniqueness.
    if all_ids is not None:
        if sid in all_ids:
            raise ValueError(f"duplicate scenario id {sid!r}")
        all_ids.add(sid)
