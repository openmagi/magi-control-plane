"""Live-lane runner for the magi-cp authoring QA harness (PR-F).

Runs scenarios against the REAL compiler LLM (ClaudeCliProvider or
AnthropicProvider) and writes a report in the same format as the CI lane.

Usage
-----
Run all scenarios with a budget of 5 live LLM calls (default):

    PYTHONPATH=src python scripts/qa/run_live.py

Run a subset:

    PYTHONPATH=src python scripts/qa/run_live.py --only 's47-*'

Larger budget:

    PYTHONPATH=src python scripts/qa/run_live.py --budget 20

Override the compiler provider (uses AnthropicProvider when set):

    MAGI_CP_LLM_COMPILER=anthropic PYTHONPATH=src python scripts/qa/run_live.py

Notes
-----
- Live failures are SIGNALS to investigate, not hard gates.  The report
  is written and the script exits 0 even when scenarios fail.
- When ``claude`` is absent / unauthenticated, the script exits with an
  actionable message and exit code 1 (non-zero so CI workflow can surface
  the skip clearly, but per-spec still "skips cleanly" - the workflow
  catches this and skips cleanly).
- The report is labelled "live lane (non-deterministic)".
- Uses SCRIPTED answerer (ScriptedAnswerer) for the user-sim role - the
  same deterministic driver used by the CI lane.  The live difference is
  the compiler role: a real LLM resolves free-text phrasings and merges
  answers into the draft rather than a cassette.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import sys
from pathlib import Path
from typing import Any

# Ensure src/ is on the path.
_REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from magi_cp.llm.claude_cli_provider import (  # noqa: E402
    ClaudeCliProvider,
    claude_cli_available,
)
from magi_cp.llm.provider import LlmProviderError  # noqa: E402

# QA harness imports.
_TESTS_DIR = _REPO_ROOT / "tests"
sys.path.insert(0, str(_TESTS_DIR))

from qa_harness.corpus import load_scenarios  # noqa: E402
from qa_harness.report import ScenarioResult, emit_report  # noqa: E402
from qa_harness.runner import run_scenario  # noqa: E402

_CORPUS_DIR = _TESTS_DIR / "qa_corpus"
_DEFAULT_BUDGET = 5


# ---------------------------------------------------------------------------
# Budget-counting provider wrapper
# ---------------------------------------------------------------------------

class _BudgetExhausted(Exception):
    """Raised when the live LLM call budget is exhausted."""


class _CountingProvider:
    """Wraps a live LLM provider and increments a shared call counter.

    Raises _BudgetExhausted when the cap is reached BEFORE delegating to
    the inner provider, so no paid call is made after the cap.
    """

    def __init__(
        self,
        inner: object,
        counter: list[int],
        budget: int,
    ) -> None:
        self._inner = inner
        self._counter = counter
        self._budget = budget

    def complete(self, messages: Any) -> str:  # noqa: ANN001
        if self._counter[0] >= self._budget:
            raise _BudgetExhausted(
                f"budget of {self._budget} live LLM calls exhausted"
            )
        result = self._inner.complete(messages)  # type: ignore[attr-defined]
        self._counter[0] += 1
        return result


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------

def _resolve_provider() -> object:
    """Return the live compiler provider according to env / availability.

    Priority:
    1. MAGI_CP_LLM_COMPILER env var present: use AnthropicProvider.
    2. ``claude`` binary on PATH: use ClaudeCliProvider.

    Raises SystemExit with an actionable message if neither is available.
    """
    compiler_env = os.environ.get("MAGI_CP_LLM_COMPILER", "")
    if compiler_env:
        # Import lazily - AnthropicProvider requires ANTHROPIC_API_KEY at
        # construction time; let it raise naturally if the key is absent.
        try:
            from magi_cp.llm.anthropic_provider import AnthropicProvider  # type: ignore[import]
            return AnthropicProvider()
        except Exception as exc:  # noqa: BLE001
            print(
                f"live lane: MAGI_CP_LLM_COMPILER is set but AnthropicProvider "
                f"failed to initialise: {exc}\n"
                "Set ANTHROPIC_API_KEY or unset MAGI_CP_LLM_COMPILER to use "
                "ClaudeCliProvider instead.",
                file=sys.stderr,
            )
            sys.exit(1)

    if not claude_cli_available():
        print(
            "live lane needs `claude login` or MAGI_CP_LLM_COMPILER; skipping\n"
            "Install the Claude CLI (https://claude.ai/download) and run "
            "`claude login`, or set MAGI_CP_LLM_COMPILER=anthropic and "
            "ANTHROPIC_API_KEY to use the Anthropic API directly.",
            file=sys.stderr,
        )
        sys.exit(1)

    return ClaudeCliProvider()


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def run_live(
    *,
    budget: int = _DEFAULT_BUDGET,
    only: str | None = None,
    output_dir: str | Path | None = None,
    provider: object | None = None,
) -> dict[str, Any]:
    """Run the live lane and return the summary dict.

    Parameters
    ----------
    budget:
        Maximum number of live LLM compiler calls.  Stop cleanly when hit.
    only:
        Optional fnmatch glob to filter scenario ids.
    output_dir:
        Where to write summary.{json,md}.  Defaults to ``.qa-report-live/``.
    provider:
        Override the live compiler provider (used by tests to inject a fake).
        When None, _resolve_provider() is called (which may SystemExit).

    Returns
    -------
    The summary dict (same shape as emit_report output).
    """
    if output_dir is None:
        output_dir = _REPO_ROOT / ".qa-report-live"

    if provider is None:
        provider = _resolve_provider()

    scenarios = load_scenarios(_CORPUS_DIR)
    if only:
        scenarios = [s for s in scenarios if fnmatch.fnmatch(s["id"], only)]

    counter: list[int] = [0]
    counting = _CountingProvider(provider, counter, budget)

    results: list[ScenarioResult] = []
    scenarios_run = 0
    budget_hit = False

    for scenario in scenarios:
        sid = scenario["id"]
        phrasings = scenario.get("phrasings") or []
        phrasing_results: list[tuple[str, list[Any]]] = []

        for pidx in range(len(phrasings)):
            try:
                record = run_scenario(scenario, pidx, llm_compiler=counting)
            except _BudgetExhausted:
                budget_hit = True
                print(
                    f"\nlive lane: budget of {budget} calls exhausted "
                    f"after {scenarios_run} scenario(s).  "
                    "Re-run with a larger --budget to continue.",
                    file=sys.stderr,
                )
                break
            except LlmProviderError as exc:
                # Auth or network failure mid-run: surface and stop.
                print(
                    f"\nlive lane: LLM provider error on {sid}[{pidx}]: {exc}",
                    file=sys.stderr,
                )
                budget_hit = True
                break

            failures_raw = [
                {"oracle": f.oracle, "detail": f.detail}
                for f in record.oracle_failures
            ]
            phrasing_results.append((record.outcome, failures_raw))

        if budget_hit:
            break

        if phrasing_results:
            result = ScenarioResult(
                scenario_id=sid,
                category=scenario.get("category", ""),
                language=scenario.get("language", "en"),
                engine=scenario.get("engine", ""),
                stable=bool(scenario.get("stable", True)),
                expected_outcome=scenario.get("expected", {}).get("outcome", ""),
                phrasing_results=phrasing_results,
            )
            results.append(result)
            scenarios_run += 1

    lane_label = "live lane (non-deterministic)"
    if budget_hit:
        lane_label += f" [budget={budget}, stopped early]"
    else:
        lane_label += f" [budget={budget}, calls used={counter[0]}]"

    summary = emit_report(
        results,
        output_dir=output_dir,
        git_sha=os.environ.get("GIT_SHA", "live"),
        corpus_version="1",
        baseline=None,
    )
    summary["lane"] = lane_label
    summary["live_calls_used"] = counter[0]
    summary["scenarios_run"] = scenarios_run

    # Print a brief human summary.
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    print(
        f"\n[{lane_label}]"
        f"\n  scenarios run : {total}"
        f"\n  passed        : {passed}"
        f"\n  failed        : {total - passed}"
        f"\n  live calls    : {counter[0]}"
    )
    if output_dir:
        print(f"  report        : {output_dir}/summary.{{json,md}}")

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Live-lane QA runner: exercises real LLM against corpus scenarios. "
            "Report-only; never a hard gate."
        )
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=_DEFAULT_BUDGET,
        help=f"Max live LLM calls (default: {_DEFAULT_BUDGET}).  "
             "Stop cleanly when hit.",
    )
    parser.add_argument(
        "--only",
        metavar="GLOB",
        default=None,
        help="Run only scenarios whose id matches this fnmatch glob.",
    )
    parser.add_argument(
        "--output-dir",
        metavar="DIR",
        default=None,
        help="Directory for report output (default: .qa-report-live/).",
    )
    args = parser.parse_args()

    run_live(
        budget=args.budget,
        only=args.only,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    main()
