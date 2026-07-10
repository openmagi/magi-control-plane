"""QA harness baseline drift gate (PR-E).

Two concerns:

1. Synthetic-regression unit test: proves that check_drift() fires a
   hard failure when a previously-passing stable scenario regresses.
   This test does NOT run any real scenario -- it uses doctored dicts.
   DoD item 2: "prove the gate is not vacuous."

2. Baseline-drift integration test: asserts no regressions vs the
   committed tests/qa_corpus/baseline.json.

   Data source (in priority order):
   a. conftest._qa_collected: live results from replay tests that ran in
      THIS pytest session (available when replay tests ran before this
      test, regardless of file-ordering).
   b. .qa-report/summary.json: written by conftest.py's
      pytest_sessionfinish hook from a PREVIOUS run -- used when this
      file is run standalone without the replay tests.
   c. Skip with instructions if neither source is available.

   This avoids the alphabetical-ordering trap: conftest._qa_collected is
   populated by pytest_runtest_logreport during the session, so it is
   available even when this file sorts before test_qa_corpus_replay.py.

Design reference:
  clawy docs/plans/2026-07-09-magi-cp-authoring-qa-harness-design.md
  Section 9 PR-E acceptance criteria.

Baseline regeneration:
  MAGI_CP_QA_UPDATE_BASELINE=1 PYTHONPATH=src python3 -m pytest \\
      tests/test_qa_corpus_replay.py tests/test_qa_baseline.py -q

  With MAGI_CP_QA_UPDATE_BASELINE=1, the integration test writes a new
  tests/qa_corpus/baseline.json instead of asserting against the old one.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Ensure tests/ is on the path so qa_harness imports work.
_TESTS_DIR = Path(__file__).parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from qa_harness.report import check_drift  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_BASELINE_PATH = _TESTS_DIR / "qa_corpus" / "baseline.json"
_REPORT_DIR = Path(__file__).parent.parent / ".qa-report"
_SUMMARY_PATH = _REPORT_DIR / "summary.json"


# ---------------------------------------------------------------------------
# Helper: load committed baseline
# ---------------------------------------------------------------------------

def _load_baseline() -> dict:
    if not _BASELINE_PATH.exists():
        pytest.skip(
            f"Committed baseline not found at {_BASELINE_PATH}. "
            "Run: MAGI_CP_QA_UPDATE_BASELINE=1 PYTHONPATH=src python3 -m pytest "
            "tests/test_qa_corpus_replay.py tests/test_qa_baseline.py -q"
        )
    with open(_BASELINE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Helper: get current scenario results
# ---------------------------------------------------------------------------

def _get_current_scenarios() -> dict | None:
    """Return current scenario results dict, or None if unavailable.

    Tries conftest._qa_collected first (live session data, works regardless
    of test ordering).  Falls back to .qa-report/summary.json from a
    previous run.
    """
    # Source A: live conftest data from this session.
    try:
        import conftest as _ct  # noqa: PLC0415
        collected = getattr(_ct, "_qa_collected", None)
        if collected:
            return {
                sid: {
                    "category": e.get("category", "unknown"),
                    "language": e.get("language", "en"),
                    "engine": e.get("engine", "fake_empty"),
                    "stable": e.get("stable", True),
                    "expected_outcome": e.get("expected_outcome", "unknown"),
                    "outcome": e.get("outcome", e.get("expected_outcome", "unknown")),
                    "passed": e.get("passed", False),
                    "oracle_failures": e.get("oracle_failures", []),
                    "oracle_fingerprint": e.get("oracle_fingerprint", ""),
                }
                for sid, e in collected.items()
            }
    except (ImportError, AttributeError):
        pass

    # Source B: summary.json from a previous run.
    if _SUMMARY_PATH.exists():
        try:
            with open(_SUMMARY_PATH, encoding="utf-8") as fh:
                summary = json.load(fh)
            return summary.get("scenarios", {})
        except Exception:  # noqa: BLE001
            pass

    return None


# ---------------------------------------------------------------------------
# Test 1: Synthetic regression proves the gate fires (DoD item 2)
# ---------------------------------------------------------------------------

def test_check_drift_detects_regression() -> None:
    """check_drift() raises a regression when a stable passing scenario fails.

    This test uses doctored dicts only -- no real scenarios run.
    It proves the drift gate is not vacuous: if a stable+passing scenario
    in baseline becomes failing in the current run, has_regressions is True
    and the scenario id appears in regressions.
    """
    baseline = {
        "my-stable-scenario": {
            "stable": True,
            "outcome": "saved",
            "passed": True,
            "oracle_fingerprint": "aabb1122",
        },
        "other-stable-scenario": {
            "stable": True,
            "outcome": "saved",
            "passed": True,
            "oracle_fingerprint": "ccdd3344",
        },
    }
    # Simulate: my-stable-scenario now fails (oracle O2 fired).
    current = {
        "my-stable-scenario": {
            "stable": True,
            "outcome": "saved",
            "passed": False,
            "oracle_fingerprint": "deadbeef",
            "oracle_failures": [{"oracle": "O2", "detail": "save-contradiction"}],
        },
        "other-stable-scenario": {
            "stable": True,
            "outcome": "saved",
            "passed": True,
            "oracle_fingerprint": "ccdd3344",
        },
    }
    result = check_drift(baseline, current)

    assert result.has_regressions, (
        "Expected check_drift() to detect a regression when a stable+passing "
        "scenario goes from passed=True to passed=False, but has_regressions "
        "was False."
    )
    regression_ids = [r["scenario_id"] for r in result.regressions]
    assert "my-stable-scenario" in regression_ids, (
        f"Expected 'my-stable-scenario' in regressions, got: {regression_ids}"
    )
    # The other scenario did not regress.
    assert "other-stable-scenario" not in regression_ids


def test_check_drift_no_regression_when_all_pass() -> None:
    """check_drift() reports no regressions when all stable scenarios still pass."""
    baseline = {
        "scenario-a": {
            "stable": True,
            "outcome": "saved",
            "passed": True,
            "oracle_fingerprint": "aabb",
        },
    }
    current = {
        "scenario-a": {
            "stable": True,
            "outcome": "saved",
            "passed": True,
            "oracle_fingerprint": "aabb",
        },
    }
    result = check_drift(baseline, current)
    assert not result.has_regressions


def test_check_drift_quarantined_fail_is_not_regression() -> None:
    """A stable=False scenario failing in current is not a regression.

    Quarantined scenarios are report-only, non-blocking.
    """
    baseline = {
        "quarantined-scenario": {
            "stable": False,
            "outcome": "saved",
            "passed": False,
            "oracle_fingerprint": "0000",
        },
    }
    current = {
        "quarantined-scenario": {
            "stable": False,
            "outcome": "saved",
            "passed": False,
            "oracle_fingerprint": "0000",
        },
    }
    result = check_drift(baseline, current)
    assert not result.has_regressions


def test_check_drift_improvement_detected() -> None:
    """A previously-failing scenario that now passes is an improvement."""
    baseline = {
        "flaky-scenario": {
            "stable": True,
            "outcome": "saved",
            "passed": False,
            "oracle_fingerprint": "deadbeef",
        },
    }
    current = {
        "flaky-scenario": {
            "stable": True,
            "outcome": "saved",
            "passed": True,
            "oracle_fingerprint": "aabb1122",
        },
    }
    result = check_drift(baseline, current)
    assert not result.has_regressions
    assert len(result.improvements) == 1
    assert result.improvements[0]["scenario_id"] == "flaky-scenario"


def test_check_drift_new_scenario_not_regression() -> None:
    """A scenario in current but not in baseline is 'new', not a regression."""
    baseline: dict = {}
    current = {
        "brand-new-scenario": {
            "stable": True,
            "outcome": "saved",
            "passed": True,
            "oracle_fingerprint": "1234",
        },
    }
    result = check_drift(baseline, current)
    assert not result.has_regressions
    assert "brand-new-scenario" in result.new_scenarios


# ---------------------------------------------------------------------------
# Test 2: Integration -- baseline drift gate against live summary
# ---------------------------------------------------------------------------

def test_baseline_drift_gate() -> None:
    """Assert no regressions vs the committed baseline.json.

    Uses live conftest._qa_collected if replay tests ran in this session,
    otherwise falls back to .qa-report/summary.json from a prior run.
    Skips with instructions if neither source is available.

    With MAGI_CP_QA_UPDATE_BASELINE=1 env var: writes a new baseline.json
    from the current run instead of asserting -- for intentional updates.
    """
    current_scenarios = _get_current_scenarios()
    if not current_scenarios:
        pytest.skip(
            "No QA replay results available. Run the replay tests first:\n"
            "  PYTHONPATH=src python3 -m pytest "
            "tests/test_qa_corpus_replay.py tests/test_qa_baseline.py -q\n"
            "Or to update the baseline:\n"
            "  MAGI_CP_QA_UPDATE_BASELINE=1 PYTHONPATH=src python3 -m pytest "
            "tests/test_qa_corpus_replay.py tests/test_qa_baseline.py -q"
        )

    # Baseline update mode: write new baseline and pass.
    if os.environ.get("MAGI_CP_QA_UPDATE_BASELINE", "").strip("'\"") == "1":
        new_baseline: dict = {}
        for sid, entry in current_scenarios.items():
            new_baseline[sid] = {
                "oracle_fingerprint": entry.get("oracle_fingerprint", ""),
                "outcome": entry.get("outcome", "unknown"),
                "passed": entry.get("passed", False),
                "stable": entry.get("stable", True),
            }
        with open(_BASELINE_PATH, "w", encoding="utf-8") as fh:
            json.dump(new_baseline, fh, indent=2, sort_keys=True)
            fh.write("\n")
        return  # Baseline updated; test passes.

    baseline = _load_baseline()
    drift = check_drift(baseline, current_scenarios)

    if drift.has_regressions:
        regression_lines = [
            f"  - {r['scenario_id']}: was passing in baseline, now FAILING "
            f"(oracle failures: "
            f"{[f['oracle'] for f in r.get('current_failures', [])]})"
            for r in drift.regressions
        ]
        hint = (
            "If this regression is intentional (e.g. a corpus change), "
            "update the baseline:\n"
            "  MAGI_CP_QA_UPDATE_BASELINE=1 PYTHONPATH=src python3 -m pytest "
            "tests/test_qa_corpus_replay.py tests/test_qa_baseline.py -q"
        )
        pytest.fail(
            f"BASELINE DRIFT: {len(drift.regressions)} stable scenario(s) "
            f"regressed vs committed baseline.json:\n"
            + "\n".join(regression_lines)
            + f"\n\n{hint}",
            pytrace=False,
        )

    # Improvements and new scenarios are soft: report but do not fail.
    if drift.improvements or drift.new_scenarios:
        improvements_msg = (
            f"  improvements: {[i['scenario_id'] for i in drift.improvements]}\n"
            if drift.improvements
            else ""
        )
        new_msg = (
            f"  new scenarios: {drift.new_scenarios}\n" if drift.new_scenarios else ""
        )
        # Use warn-level print rather than pytest.warns (avoids coupling to
        # warning infrastructure; the conftest report.md already surfaces these).
        print(
            f"\n[qa-baseline] Non-blocking drift detected:\n"
            f"{improvements_msg}{new_msg}"
            "Run with MAGI_CP_QA_UPDATE_BASELINE=1 to update baseline.json.",
            file=__import__("sys").stderr,
        )
