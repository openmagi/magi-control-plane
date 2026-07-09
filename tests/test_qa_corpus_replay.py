"""L3 corpus replay tests for the magi-cp authoring QA harness (PR-C).

This module is parametrized over all fake_empty scenarios in
tests/qa_corpus/scenarios/.  Each scenario-phrasing pair is a separate
pytest node.  Oracle failures are collected by the runner and re-raised
here as AssertionError, making them hard CI failures for stable scenarios.

Engine filter: this file runs ONLY the fake_empty lane (no LLM required).
The cassette lane is PR-D.

Design reference:
  clawy docs/plans/2026-07-09-magi-cp-authoring-qa-harness-design.md
  Section 9 PR-C acceptance criteria.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

# Ensure tests/ is on the path so qa_harness imports work.
_TESTS_DIR = Path(__file__).parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from qa_harness.corpus import load_scenarios, validate_scenario  # noqa: E402
from qa_harness.runner import ADMIN_KEY, run_scenario  # noqa: E402

# ---------------------------------------------------------------------------
# Corpus loading and parametrization
# ---------------------------------------------------------------------------

_CORPUS_DIR = Path(__file__).parent / "qa_corpus"

# Set admin key for the entire module at import time (before parametrize).
os.environ["MAGI_CP_ADMIN_API_KEY"] = ADMIN_KEY


def _load_fake_empty_params() -> list[tuple[dict[str, Any], int]]:
    """Load all fake_empty scenarios and expand per phrasing.

    Returns a list of (scenario, phrasing_idx) pairs.  Each pair becomes
    one pytest node so failures are granular.
    """
    scenarios = load_scenarios(_CORPUS_DIR)
    params = []
    for s in scenarios:
        if s.get("engine") != "fake_empty":
            continue
        # Validate schema at collection time so corpus errors show as
        # collect errors, not as runtime failures.
        validate_scenario(s)
        for idx in range(len(s["phrasings"])):
            params.append((s, idx))
    return params


_PARAMS = _load_fake_empty_params()


def _param_id(param: tuple) -> str:
    """Human-readable test node id: <scenario_id>[phrasing-<idx>]."""
    s, idx = param
    sid = s["id"]
    if len(s["phrasings"]) > 1:
        return f"{sid}[phrasing-{idx}]"
    return sid


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scenario,phrasing_idx", _PARAMS, ids=[
    _param_id(p) for p in _PARAMS
])
def test_fake_empty_scenario(
    scenario: dict[str, Any],
    phrasing_idx: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run one fake_empty scenario-phrasing through the scripted runner.

    Stable scenarios fail on any oracle violation.  (Quarantined scenarios
    with stable=False would xfail; no such scenarios exist in the current
    fake_empty corpus.)
    """
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", ADMIN_KEY)

    record = run_scenario(scenario, phrasing_idx)

    # Collect all oracle failure messages for a single clear assertion.
    if record.oracle_failures:
        msgs = "\n".join(str(f) for f in record.oracle_failures)
        pytest.fail(
            f"scenario={scenario['id']!r} phrasing={phrasing_idx} "
            f"outcome={record.outcome!r}\n"
            f"Oracle failures ({len(record.oracle_failures)}):\n{msgs}",
            pytrace=False,
        )
