"""Shared pytest configuration for all magi-cp tests.

This module provides fixtures used by the QA harness replay tests.

QA harness fixture: qa_nonce_counter
  Monkeypatches magi_cp.cloud.nl_compiler._make_fence_nonce() (and its
  re-export from magi_cp.policy.nl_compiler_interactive) to return a
  deterministic counter-based nonce ("0000000000000001", "0000000000000002",
  ...)  instead of a cryptographic random hex string.

  Purpose: the cassette key is sha256(canonical-JSON(normalised-messages)).
  Normalisation already replaces UNTRUSTED-<16hex> with UNTRUSTED-N, but
  belt-and-braces pinning ensures the SYSTEM PROMPT text (which interpolates
  the nonce) is also identical between the recording run and the replay run.
  Without this the system prompt text would differ, producing a different sha256
  key, and the cassette lookup would always miss.

  Scope: function (re-set for every test so the counter resets; avoids
  cross-test pollution where an earlier test consuming N nonces shifts the
  sequence for the next test).

  Only active for tests in tests/qa_harness and tests/test_qa_corpus_replay.py.
  Other tests that import nl_compiler should not be affected because the fixture
  is explicit (autouse=False).

QA session collector plugin:
  Hooks pytest_runtest_logreport to collect QA replay results across the
  session.  After the session ends (pytest_sessionfinish), it writes
  .qa-report/summary.{json,md} if any QA replay test results were collected.
  The report is written even on failure so CI artifact upload can inspect it.
"""

from __future__ import annotations

import itertools
import json
import re
import sys
from pathlib import Path
from typing import Generator

import pytest


# ---------------------------------------------------------------------------
# QA session result collector (PR-E)
# ---------------------------------------------------------------------------

# Node id patterns for the two QA replay test functions.
_QA_FAKE_RE = re.compile(
    r"test_qa_corpus_replay\.py::test_fake_empty_scenario\[(?P<sid>[^\]]+)\]"
)
_QA_CASSETTE_RE = re.compile(
    r"test_qa_corpus_replay\.py::test_cassette_scenario\[(?P<sid_raw>[^\]]+)\]"
)

# Module-level store: {scenario_id: {passed, outcome, oracle_failures, ...}}
_qa_collected: dict[str, dict] = {}
_qa_scenarios_meta: dict[str, dict] = {}  # scenario metadata from corpus


def _load_scenarios_meta() -> dict[str, dict]:
    """Load scenario metadata lazily (once per session)."""
    if _qa_scenarios_meta:
        return _qa_scenarios_meta
    try:
        _tests_dir = Path(__file__).parent
        if str(_tests_dir) not in sys.path:
            sys.path.insert(0, str(_tests_dir))
        from qa_harness.corpus import load_scenarios  # noqa: PLC0415
        corpus_dir = _tests_dir / "qa_corpus"
        scenarios = load_scenarios(corpus_dir)
        for s in scenarios:
            _qa_scenarios_meta[s["id"]] = s
    except Exception:  # noqa: BLE001
        pass
    return _qa_scenarios_meta


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Collect QA replay test results as they complete.

    Called by pytest after each test phase (setup/call/teardown).
    We capture on 'call' (the actual test body) or 'xfail' markers.
    """
    if report.when != "call":
        return

    node_id = report.nodeid

    # Match fake_empty or cassette replay tests.
    m = _QA_FAKE_RE.search(node_id) or _QA_CASSETTE_RE.search(node_id)
    if m is None:
        return

    # Extract scenario id (strip [phrasing-N] suffix if present).
    raw_id = m.group(1) if "sid" in m.groupdict() else m.group("sid_raw")
    # Strip phrasing suffix: "some-scenario[phrasing-1]" -> "some-scenario"
    sid = re.sub(r"\[phrasing-\d+\]$", "", raw_id)

    scenarios_meta = _load_scenarios_meta()
    meta = scenarios_meta.get(sid, {})

    # Determine pass/fail from the report.
    passed = report.passed
    outcome_from_meta = meta.get("expected", {}).get("outcome", "unknown")

    # Oracle failures: extract from longreprtext if available.
    oracle_failures: list[dict] = []
    if not passed and not report.skipped:
        longrepr = getattr(report, "longreprtext", None) or ""
        if not longrepr and hasattr(report, "longrepr") and report.longrepr:
            longrepr = str(report.longrepr)
        # Parse "Oracle failures" block from the test failure message.
        for line in longrepr.splitlines():
            line = line.strip()
            # Lines like "[O3] dead-end: ..."
            om = re.match(r"^\[([A-Z0-9/]+)\]\s+(.+)$", line)
            if om:
                oracle_failures.append({
                    "oracle": om.group(1),
                    "detail": om.group(2),
                })

    from qa_harness.report import oracle_fingerprint  # noqa: PLC0415
    fp = oracle_fingerprint(oracle_failures)

    # Accumulate per scenario_id (multiple phrasings may update the same id).
    if sid in _qa_collected:
        existing = _qa_collected[sid]
        # If any phrasing fails, the scenario fails.
        if not passed:
            existing["passed"] = False
            existing["oracle_failures"].extend(oracle_failures)
            existing["oracle_fingerprint"] = oracle_fingerprint(
                existing["oracle_failures"]
            )
    else:
        _qa_collected[sid] = {
            "scenario_id": sid,
            "category": meta.get("category", "unknown"),
            "language": meta.get("language", "en"),
            "engine": meta.get("engine", "fake_empty"),
            "stable": meta.get("stable", True),
            "expected_outcome": outcome_from_meta,
            "outcome": outcome_from_meta,
            "passed": passed,
            "oracle_failures": oracle_failures,
            "oracle_fingerprint": fp,
        }


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Write .qa-report/summary.{json,md} after the session if QA results exist."""
    if not _qa_collected:
        return

    # Load baseline for drift section.
    baseline: dict | None = None
    baseline_path = (
        Path(__file__).parent / "qa_corpus" / "baseline.json"
    )
    if baseline_path.exists():
        try:
            with open(baseline_path, encoding="utf-8") as fh:
                baseline = json.load(fh)
        except Exception:  # noqa: BLE001
            pass

    try:
        _tests_dir = Path(__file__).parent
        if str(_tests_dir) not in sys.path:
            sys.path.insert(0, str(_tests_dir))
        from qa_harness.report import ScenarioResult, emit_report  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return

    results: list[ScenarioResult] = []
    for entry in _qa_collected.values():
        r = ScenarioResult(
            scenario_id=entry["scenario_id"],
            category=entry.get("category", "unknown"),
            language=entry.get("language", "en"),
            engine=entry.get("engine", "fake_empty"),
            stable=entry.get("stable", True),
            expected_outcome=entry.get("expected_outcome", "unknown"),
        )
        outcome = entry.get("outcome", entry.get("expected_outcome", "unknown"))
        r.phrasing_results = [(outcome, entry.get("oracle_failures", []))]
        results.append(r)

    qa_report_dir = Path(session.config.rootdir) / ".qa-report"

    try:
        emit_report(
            results,
            output_dir=qa_report_dir,
            baseline=baseline,
        )
    except Exception:  # noqa: BLE001
        pass  # report writing is never allowed to break the test run


@pytest.fixture()
def qa_nonce_counter(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Patch _make_fence_nonce() to a deterministic counter for QA replay tests.

    The counter produces 16-hex strings: '0000000000000001',
    '0000000000000002', ...  This keeps the nonce-interpolated system prompt
    identical across record and replay, making cassette key lookup stable.
    """
    counter = itertools.count(1)

    def _deterministic_nonce() -> str:
        return f"{next(counter):016x}"

    # Patch both the canonical location and the re-export in nl_compiler_interactive.
    try:
        import magi_cp.cloud.nl_compiler as _nl
        monkeypatch.setattr(_nl, "_make_fence_nonce", _deterministic_nonce)
    except (ImportError, AttributeError):
        pass

    try:
        import magi_cp.policy.nl_compiler_interactive as _ic
        monkeypatch.setattr(_ic, "_make_fence_nonce", _deterministic_nonce)
    except (ImportError, AttributeError):
        pass

    yield
