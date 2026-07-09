"""L3 corpus replay tests for the magi-cp authoring QA harness (PR-C + PR-D).

This module is parametrized over two engine lanes:

  fake_empty  -- scenarios that pass with an infinite-empty LLM stub; no LLM
                 calls needed.  (PR-C)

  cassette    -- scenarios whose LLM responses are stored in authored/recorded
                 cassette files.  In CI the CassetteProvider replays from disk;
                 with MAGI_CP_QA_RECORD=1 it records via a real provider.
                 Stable scenarios fail on any oracle violation; scenarios with
                 stable=False are marked xfail.  (PR-D)

Design reference:
  clawy docs/plans/2026-07-09-magi-cp-authoring-qa-harness-design.md
  Section 9 PR-C acceptance criteria, Section 10 PR-D acceptance criteria.
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

from qa_harness.cassette import CassetteProvider  # noqa: E402
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


# ---------------------------------------------------------------------------
# Cassette lane (PR-D)
# ---------------------------------------------------------------------------

def _load_cassette_params() -> list[tuple[dict[str, Any], int]]:
    """Load all cassette scenarios and expand per phrasing.

    Returns a list of (scenario, phrasing_idx) pairs.  Stable=False scenarios
    are included - they are marked xfail at test time.
    """
    scenarios = load_scenarios(_CORPUS_DIR)
    params = []
    for s in scenarios:
        if s.get("engine") != "cassette":
            continue
        validate_scenario(s)
        for idx in range(len(s["phrasings"])):
            params.append((s, idx))
    return params


_CASSETTE_PARAMS = _load_cassette_params()


def _cassette_param_id(param: tuple) -> str:
    """Human-readable test node id: <scenario_id>[phrasing-<idx>]."""
    s, idx = param
    sid = s["id"]
    if len(s["phrasings"]) > 1:
        return f"{sid}[phrasing-{idx}]"
    return sid


@pytest.mark.parametrize("scenario,phrasing_idx", _CASSETTE_PARAMS, ids=[
    _cassette_param_id(p) for p in _CASSETTE_PARAMS
])
def test_cassette_scenario(
    scenario: dict[str, Any],
    phrasing_idx: int,
    monkeypatch: pytest.MonkeyPatch,
    qa_nonce_counter: None,
) -> None:
    """Run one cassette scenario-phrasing through the scripted runner.

    CassetteProvider is used as the LLM provider.  In CI it replays from the
    authored/recorded cassette file.  With MAGI_CP_QA_RECORD=1 it records via
    a real provider.

    Stable=False scenarios are marked xfail with the known_limitation_note as
    the reason.  Stable=True scenarios fail on any oracle violation.
    """
    is_stable = scenario.get("stable", True)
    known_note = scenario.get("provenance", {}).get("known_limitation_note", "")
    if not is_stable:
        pytest.xfail(
            reason=(
                known_note
                or f"scenario {scenario['id']!r} marked stable=false"
            )
        )

    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", ADMIN_KEY)

    provider = CassetteProvider(scenario["id"])
    record = run_scenario(scenario, phrasing_idx, llm_compiler=provider)

    if record.oracle_failures:
        msgs = "\n".join(str(f) for f in record.oracle_failures)
        pytest.fail(
            f"scenario={scenario['id']!r} phrasing={phrasing_idx} "
            f"outcome={record.outcome!r}\n"
            f"Oracle failures ({len(record.oracle_failures)}):\n{msgs}",
            pytrace=False,
        )


# ---------------------------------------------------------------------------
# Cassette unit tests (PR-D)
# ---------------------------------------------------------------------------

def test_cassette_miss_raises_actionable_error() -> None:
    """A cassette miss raises LlmProviderError with re-record instructions."""
    from magi_cp.llm.provider import LlmProviderError
    from qa_harness.cassette import CassetteProvider

    provider = CassetteProvider("__nonexistent_scenario_xyz__")
    with pytest.raises(LlmProviderError, match="MAGI_CP_QA_RECORD=1"):
        provider.complete([{"role": "user", "content": "hello"}])


def test_cassette_nonce_normalisation_same_key() -> None:
    """Two message lists differing only in the nonce produce the same key."""
    from qa_harness.cassette import _make_key

    msgs_a = [{"role": "user", "content": "UNTRUSTED-aabbccddeeff0011 value"}]
    msgs_b = [{"role": "user", "content": "UNTRUSTED-1122334455667788 value"}]
    assert _make_key(msgs_a) == _make_key(msgs_b)


def test_cassette_different_content_different_key() -> None:
    """Two message lists with different content produce different keys."""
    from qa_harness.cassette import _make_key

    msgs_a = [{"role": "user", "content": "block all bash"}]
    msgs_b = [{"role": "user", "content": "audit all bash"}]
    assert _make_key(msgs_a) != _make_key(msgs_b)


def test_cassette_record_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Record mode: a FakeLlmProvider response round-trips through the cassette."""
    import json
    import qa_harness.cassette as _cm
    from qa_harness.cassette import CassetteProvider

    # Redirect cassette storage to a temp directory for the entire test.
    monkeypatch.setattr(_cm, "_CASSETTES_DIR", tmp_path)

    fake_response = json.dumps({"assistant_message": "ok", "draft_updates": {}, "questions": []})

    class _FakeProv:
        def complete(self, messages: Any) -> str:
            return fake_response

    scenario_id = "test-round-trip"
    recorder = CassetteProvider(
        scenario_id,
        record_mode=True,
        underlying_provider=_FakeProv(),
    )

    messages = [{"role": "user", "content": "block all bash"}]
    result = recorder.complete(messages)
    assert result == fake_response

    # Replay the cassette (new provider instance in replay mode).
    # _CASSETTES_DIR is still patched to tmp_path so the file is found.
    replayer = CassetteProvider(scenario_id, record_mode=False)
    result2 = replayer.complete(messages)
    assert result2 == fake_response
