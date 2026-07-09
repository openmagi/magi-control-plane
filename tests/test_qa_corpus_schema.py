"""Schema tests for the QA authoring corpus (L2).

Loads every committed scenario fixture and asserts it validates, then
drives one broken-fixture case per validator rule to prove each rule
fires with a specific error message.

Run: PYTHONPATH=src python3 -m pytest tests/test_qa_corpus_schema.py -q
"""

from __future__ import annotations

import copy
from collections import Counter
from pathlib import Path

import pytest

from tests.qa_harness.corpus import (
    CATEGORY_ENUM,
    OUTCOME_ENUM,
    load_scenarios,
    validate_scenario,
)

_CORPUS_ROOT = Path(__file__).parent / "qa_corpus"


def _valid_base() -> dict:
    """A minimal valid scenario dict used to seed broken-fixture cases.

    Carries a synthetic __file_stem__ so the id-vs-filename rule passes.
    """
    return {
        "__file_stem__": "fixture-base",
        "schema_version": 1,
        "id": "fixture-base",
        "category": "happy_path",
        "language": "en",
        "style": "canonical",
        "runtime_id": None,
        "engine": "fake_empty",
        "stable": True,
        "known_limitation": False,
        "target_ir": {
            "id": "qa-target",
            "description": "",
            "trigger": {
                "host": "claude-code",
                "event": "PreToolUse",
                "matcher": "Bash",
            },
            "requires": [{"step": "privilege_scan", "verdict": "pass"}],
            "action": "block",
        },
        "expected": {"outcome": "saved", "feasibility_code": None, "max_turns": 8},
        "phrasings": [{"text": "block bash before it runs", "note": "seed"}],
        "provenance": {"source": "test", "generated_by": "hand", "reviewed": True},
    }


def test_base_fixture_is_valid() -> None:
    # Sanity: the seed for the broken-fixture cases must itself validate.
    validate_scenario(_valid_base())


def test_all_seeds_validate() -> None:
    scenarios = load_scenarios(_CORPUS_ROOT)
    assert scenarios, "no scenarios loaded from the corpus"

    all_ids: set[str] = set()
    for s in scenarios:
        validate_scenario(s, all_ids=all_ids)

    # Report counts per lane (provenance.source prefix) and category.
    by_source: Counter[str] = Counter()
    by_category: Counter[str] = Counter()
    by_engine: Counter[str] = Counter()
    by_outcome: Counter[str] = Counter()
    for s in scenarios:
        src = s["provenance"]["source"]
        # Group audit-S* rows into a single "audit-S*" lane bucket.
        lane = "audit-S*" if src.startswith("audit-S") else src
        by_source[lane] += 1
        by_category[s["category"]] += 1
        by_engine[s["engine"]] += 1
        by_outcome[s["expected"]["outcome"]] += 1

    print("\n=== QA corpus counts ===")
    print(f"total: {len(scenarios)}")
    print(f"by lane:     {dict(sorted(by_source.items()))}")
    print(f"by category: {dict(sorted(by_category.items()))}")
    print(f"by engine:   {dict(sorted(by_engine.items()))}")
    print(f"by outcome:  {dict(sorted(by_outcome.items()))}")

    # Every category and outcome used must be in the enums (belt + braces;
    # validate_scenario already enforces this per-scenario).
    assert set(by_category) <= CATEGORY_ENUM
    assert set(by_outcome) <= OUTCOME_ENUM
    assert len(all_ids) == len(scenarios), "ids are not unique"


def test_broken_duplicate_id() -> None:
    a = _valid_base()
    b = _valid_base()
    all_ids: set[str] = set()
    validate_scenario(a, all_ids=all_ids)
    with pytest.raises(ValueError, match="duplicate"):
        validate_scenario(b, all_ids=all_ids)


def test_broken_illegal_triple() -> None:
    s = _valid_base()
    # Stop/Bash/block is illegal (audit at Stop is wildcard-only; block
    # never legal at Stop).
    s["target_ir"]["trigger"]["event"] = "Stop"
    s["target_ir"]["trigger"]["matcher"] = "Bash"
    s["target_ir"]["action"] = "block"
    with pytest.raises(ValueError, match="LEGAL_COMBINATIONS"):
        validate_scenario(s)


def test_broken_missing_matcher() -> None:
    s = _valid_base()
    del s["target_ir"]["trigger"]["matcher"]
    with pytest.raises(ValueError, match="explicit"):
        validate_scenario(s)


def test_broken_unknown_category() -> None:
    s = _valid_base()
    s["category"] = "not_a_real_category"
    with pytest.raises(ValueError, match="category"):
        validate_scenario(s)


def test_broken_empty_phrasings() -> None:
    s = _valid_base()
    s["phrasings"] = []
    with pytest.raises(ValueError, match="phrasing"):
        validate_scenario(s)


def test_broken_non_loadable_ir() -> None:
    s = _valid_base()
    # Missing required `id` on the policy body makes policy_from_dict raise.
    del s["target_ir"]["id"]
    with pytest.raises(ValueError, match="policy_from_dict"):
        validate_scenario(s)


def test_broken_bad_outcome() -> None:
    s = _valid_base()
    s["expected"]["outcome"] = "definitely_saved"
    with pytest.raises(ValueError, match="outcome"):
        validate_scenario(s)


def test_broken_bad_engine() -> None:
    s = _valid_base()
    s["engine"] = "live_gpt"
    with pytest.raises(ValueError, match="engine"):
        validate_scenario(s)


def test_broken_id_mismatch_filename() -> None:
    s = _valid_base()
    s["id"] = "different-id"
    with pytest.raises(ValueError, match="filename"):
        validate_scenario(s)


def test_target_ir_null_is_allowed() -> None:
    s = _valid_base()
    s["target_ir"] = None
    s["expected"]["outcome"] = "steered"
    # Must not raise: non-authoring outcomes carry no target IR.
    validate_scenario(copy.deepcopy(s))
