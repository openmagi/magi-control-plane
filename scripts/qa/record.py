"""CLI to (re-)record QA harness cassettes for engine=cassette scenarios.

Usage
-----
Record all cassette scenarios (uses ClaudeCliProvider by default):

    MAGI_CP_QA_RECORD=1 PYTHONPATH=src python scripts/qa/record.py

Record a specific scenario (glob):

    MAGI_CP_QA_RECORD=1 PYTHONPATH=src python scripts/qa/record.py --only 'ev-pretooluse-bash-block-regex-en'

Record with a budget cap (stop after N live LLM calls):

    MAGI_CP_QA_RECORD=1 PYTHONPATH=src python scripts/qa/record.py --budget 5

Re-record even if a cassette already exists:

    MAGI_CP_QA_RECORD=1 PYTHONPATH=src python scripts/qa/record.py --force

Options
-------
--only <glob>   Only record scenarios whose id matches this glob pattern.
--budget <N>    Stop after N live LLM calls (default: unlimited).
--force         Re-record even if a cassette already exists for a scenario.
--dry-run       Print which scenarios would be recorded without calling anything.

Environment
-----------
MAGI_CP_QA_RECORD=1 enables record mode in CassetteProvider.
MAGI_CP_CLAUDE_CLI_MODEL  Override the Claude CLI model (optional).

Notes
-----
- The script runs each cassette scenario through the L3 runner with
  CassetteProvider wrapping ClaudeCliProvider.  The cassette is written
  (or updated) after each scenario.
- If a scenario fails oracles on replay immediately after recording it is
  quarantined (stable -> note) and reported.
- A budget counter tracks live LLM calls; when the budget is exhausted the
  script stops cleanly.  Re-run to continue.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import sys
from pathlib import Path
from typing import Any

# Ensure src/ is on the path so magi_cp imports work.
_REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from magi_cp.llm.provider import LlmProviderError  # noqa: E402

# QA harness imports (tests/ on path).
_TESTS_DIR = _REPO_ROOT / "tests"
sys.path.insert(0, str(_TESTS_DIR))

from qa_harness.cassette import CassetteProvider  # noqa: E402
from qa_harness.corpus import load_scenarios  # noqa: E402
from qa_harness.runner import run_scenario  # noqa: E402

_CORPUS_DIR = _TESTS_DIR / "qa_corpus"


class _BudgetExhausted(Exception):
    pass


class _CountingProvider:
    """Wraps CassetteProvider and increments a shared call counter."""

    def __init__(
        self,
        inner: CassetteProvider,
        counter: list[int],
        budget: int | None,
    ) -> None:
        self._inner = inner
        self._counter = counter
        self._budget = budget

    def complete(self, messages: Any) -> str:
        if self._budget is not None and self._counter[0] >= self._budget:
            raise _BudgetExhausted(
                f"budget of {self._budget} live LLM calls exhausted"
            )
        result = self._inner.complete(messages)
        self._counter[0] += 1
        return result


def _should_record(scenario: dict[str, Any], force: bool) -> bool:
    """True iff this scenario should be recorded (or re-recorded)."""
    if scenario.get("engine") != "cassette":
        return False
    if force:
        return True
    # Skip if cassette already exists and has entries.
    from qa_harness.cassette import _cassette_path, _load_cassette
    p = _cassette_path(scenario["id"])
    if not p.exists():
        return True
    cassette = _load_cassette(scenario["id"])
    return len(cassette.get("compiler", [])) == 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record QA harness cassettes for engine=cassette scenarios."
    )
    parser.add_argument("--only", metavar="GLOB", default=None,
                        help="Record only scenarios matching this id glob.")
    parser.add_argument("--budget", type=int, default=None,
                        help="Maximum number of live LLM calls (safety cap).")
    parser.add_argument("--force", action="store_true",
                        help="Re-record even if a cassette already exists.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print which scenarios would be recorded without calling.")
    args = parser.parse_args()

    os.environ["MAGI_CP_QA_RECORD"] = "1"

    scenarios = load_scenarios(_CORPUS_DIR)
    cassette_scenarios = [
        s for s in scenarios if s.get("engine") == "cassette"
    ]

    if args.only:
        cassette_scenarios = [
            s for s in cassette_scenarios
            if fnmatch.fnmatch(s["id"], args.only)
        ]

    to_record = [s for s in cassette_scenarios if _should_record(s, args.force)]

    print(f"Scenarios to record: {len(to_record)} / {len(cassette_scenarios)}")

    if args.dry_run:
        for s in to_record:
            print(f"  {s['id']} ({len(s['phrasings'])} phrasings)")
        return

    call_counter: list[int] = [0]
    recorded = 0
    failed: list[str] = []
    oracle_failed: list[str] = []
    budget_hit = False

    for scenario in to_record:
        sid = scenario["id"]
        print(f"\n--- Recording: {sid} ---")

        try:
            from magi_cp.llm.claude_cli_provider import ClaudeCliProvider
            underlying = ClaudeCliProvider()
        except Exception as e:
            print(f"  ERROR: cannot build ClaudeCliProvider: {e}")
            failed.append(sid)
            continue

        cassette_provider = CassetteProvider(
            sid,
            record_mode=True,
            underlying_provider=underlying,
        )
        counting = _CountingProvider(cassette_provider, call_counter, args.budget)

        scenario_ok = True
        for phrasing_idx, phrasing in enumerate(scenario["phrasings"]):
            print(f"  phrasing[{phrasing_idx}]: {phrasing['text'][:60]!r}")
            try:
                record = run_scenario(scenario, phrasing_idx, llm_compiler=counting)
            except _BudgetExhausted as e:
                print(f"  BUDGET EXHAUSTED: {e}")
                budget_hit = True
                break
            except LlmProviderError as e:
                print(f"  LLM ERROR: {e}")
                failed.append(f"{sid}[{phrasing_idx}]")
                scenario_ok = False
                continue
            except Exception as e:
                print(f"  RUNNER ERROR: {type(e).__name__}: {e}")
                failed.append(f"{sid}[{phrasing_idx}]")
                scenario_ok = False
                continue

            if record.oracle_failures:
                msgs = "; ".join(str(f) for f in record.oracle_failures)
                print(f"  ORACLE FAILURE (outcome={record.outcome}): {msgs}")
                oracle_failed.append(f"{sid}[{phrasing_idx}]")
                scenario_ok = False
            else:
                print(f"  OK (outcome={record.outcome}, turns={len(record.turns)})")

        if budget_hit:
            break
        if scenario_ok:
            recorded += 1

    print(f"\n=== Record run complete ===")
    print(f"  Recorded (oracle-clean): {recorded}")
    print(f"  LLM errors:              {len(failed)}")
    print(f"  Oracle failures:         {len(oracle_failed)}")
    print(f"  Live LLM calls used:     {call_counter[0]}")
    if budget_hit:
        print(f"  Budget hit - resume with --only or --budget to continue")
    if failed:
        print(f"  Failed scenarios: {failed}")
    if oracle_failed:
        print(f"  Oracle-failed (quarantine candidates): {oracle_failed}")


if __name__ == "__main__":
    main()
