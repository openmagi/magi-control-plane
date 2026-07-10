"""LLM-driven phrasing expander for the magi-cp authoring QA harness (PR-F).

For each scenario, calls the LLM to generate additional natural-language
phrasings of its ``target_ir`` intent in KO/EN x styles (terse, verbose,
ambiguous), per Section 5.2 of the design doc.

Output: proposed EDITS to each scenario JSON's ``phrasings`` array.  New
entries carry ``provenance.generated_by="llm-expand"``.  Changes are written
to disk for HUMAN REVIEW as a normal git diff before being committed.

This script is NEVER run in CI.  Run it manually, review the diff, then
re-record cassettes for any newly added phrasing that needs one:

    PYTHONPATH=src python scripts/qa/gen_phrasings.py --only '<id-glob>'
    git diff tests/qa_corpus/scenarios/
    # review, edit as needed, then:
    MAGI_CP_QA_RECORD=1 PYTHONPATH=src python scripts/qa/record.py --only '<id>'

Usage
-----
Expand all scenarios (small budget):

    PYTHONPATH=src python scripts/qa/gen_phrasings.py --budget 5

Expand a subset:

    PYTHONPATH=src python scripts/qa/gen_phrasings.py --only 's47-*'

Dry-run (print what would be generated, do not write):

    PYTHONPATH=src python scripts/qa/gen_phrasings.py --dry-run

Notes
-----
- Each generated phrasing is validated against the corpus schema before
  writing.  Malformed LLM output is dropped with a warning.
- The ``--budget`` cap limits live LLM calls; stop cleanly when hit.
- NEVER auto-commits.  After generating, human reviews the diff, and any
  new cassette-engine phrasings need recording via ``scripts/qa/record.py``
  before they will pass CI.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
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

from qa_harness.corpus import load_scenarios, validate_scenario  # noqa: E402

_CORPUS_DIR = _TESTS_DIR / "qa_corpus"
_SCENARIOS_DIR = _CORPUS_DIR / "scenarios"
_DEFAULT_BUDGET = 5

# Styles to generate per language.
_STYLES = ["terse", "verbose", "ambiguous"]
_LANGUAGES = ["en", "ko"]


# ---------------------------------------------------------------------------
# Budget counter
# ---------------------------------------------------------------------------

class _BudgetExhausted(Exception):
    """Raised when the live LLM call budget is exhausted."""


class _CountingProvider:
    """Wraps a live LLM provider and raises _BudgetExhausted at the cap."""

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
    """Return the live provider for phrasing generation.

    Uses ClaudeCliProvider by default; exits with an actionable message if
    ``claude`` is not available.
    """
    compiler_env = os.environ.get("MAGI_CP_LLM_COMPILER", "")
    if compiler_env:
        try:
            from magi_cp.llm.anthropic_provider import AnthropicProvider  # type: ignore[import]
            return AnthropicProvider()
        except Exception as exc:  # noqa: BLE001
            print(
                f"gen_phrasings: MAGI_CP_LLM_COMPILER set but provider failed: {exc}",
                file=sys.stderr,
            )
            sys.exit(1)

    if not claude_cli_available():
        print(
            "gen_phrasings needs `claude login` or MAGI_CP_LLM_COMPILER; skipping\n"
            "Install the Claude CLI and run `claude login`, or set "
            "MAGI_CP_LLM_COMPILER=anthropic and ANTHROPIC_API_KEY.",
            file=sys.stderr,
        )
        sys.exit(1)

    return ClaudeCliProvider()


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a QA phrasing generator for a policy authoring system.
Given a policy intent (as a JSON object called target_ir), generate alternative
natural-language phrasings that an operator might type to express the same intent.

Rules:
- Generate exactly one phrasing per (language, style) pair requested.
- "terse": very short, uses abbreviations, minimal words.
- "verbose": detailed, uses full sentences, explains the goal.
- "ambiguous": slightly underspecified so the system must ask a clarifying
  question to resolve it.
- Respond ONLY with a JSON array.  Each element must be an object with:
  {"text": "<phrasing>", "language": "<en|ko>", "style": "<terse|verbose|ambiguous>"}
- No prose, no explanation, no markdown fence.  Pure JSON array only.
"""


def _build_prompt(
    scenario: dict[str, Any],
    languages: list[str],
    styles: list[str],
) -> list[dict[str, str]]:
    """Build the messages list for the LLM phrasing request."""
    target_ir = scenario.get("target_ir")
    existing_phrasings = [p.get("text", "") for p in (scenario.get("phrasings") or [])]

    user_content = (
        f"Scenario id: {scenario['id']}\n"
        f"Category: {scenario.get('category', '')}\n"
        f"Language of existing phrasings: {scenario.get('language', 'en')}\n\n"
        f"Target policy intent (target_ir):\n{json.dumps(target_ir, indent=2, ensure_ascii=False)}\n\n"
        f"Existing phrasings (do not duplicate):\n"
        + "\n".join(f"  - {p}" for p in existing_phrasings)
        + "\n\nGenerate phrasings for:\n"
        + "\n".join(f"  - language={lang}, style={style}" for lang in languages for style in styles)
        + "\n\nRespond with a JSON array only."
    )

    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


# ---------------------------------------------------------------------------
# Phrasing validation
# ---------------------------------------------------------------------------

def _validate_generated_phrasing(
    phrasing: Any,
    scenario: dict[str, Any],
) -> str | None:
    """Validate one generated phrasing dict.

    Returns an error string on failure, None on success.
    """
    if not isinstance(phrasing, dict):
        return f"not a dict: {type(phrasing).__name__}"
    text = phrasing.get("text")
    if not isinstance(text, str) or not text.strip():
        return "missing or empty 'text' field"
    language = phrasing.get("language")
    if language not in ("en", "ko"):
        return f"invalid language {language!r}; must be 'en' or 'ko'"
    style = phrasing.get("style")
    if style not in _STYLES:
        return f"invalid style {style!r}; must be one of {_STYLES}"

    # Build a candidate scenario with just this phrasing and validate schema.
    candidate = dict(scenario)
    candidate["phrasings"] = [{"text": text, "note": f"generated style={style}"}]
    candidate["language"] = language
    candidate["style"] = style
    # Remove __file_stem__ so validate_scenario does not check filename match.
    candidate.pop("__file_stem__", None)
    try:
        validate_scenario(candidate)
    except ValueError as exc:
        return f"schema validation failed: {exc}"

    return None


# ---------------------------------------------------------------------------
# Core generation function
# ---------------------------------------------------------------------------

def generate_phrasings_for_scenario(
    scenario: dict[str, Any],
    provider: object,
    counter: list[int],
    budget: int,
    *,
    languages: list[str] | None = None,
    styles: list[str] | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Generate and validate new phrasings for one scenario.

    Returns a list of validated phrasing dicts (with provenance).
    Raises _BudgetExhausted when cap is hit.
    Drops malformed LLM output with a warning.
    """
    if languages is None:
        languages = _LANGUAGES
    if styles is None:
        styles = _STYLES

    # Only expand scenarios that have a target_ir (non-authoring scenarios have
    # no IR to paraphrase).
    if scenario.get("target_ir") is None:
        return []

    if dry_run:
        count = len(languages) * len(styles)
        print(f"  [dry-run] would generate {count} phrasings for {scenario['id']}")
        return []

    if counter[0] >= budget:
        raise _BudgetExhausted(f"budget of {budget} calls exhausted")

    messages = _build_prompt(scenario, languages, styles)

    # Use _CountingProvider indirectly: increment manually here since we are
    # making exactly one call per scenario.
    if counter[0] >= budget:
        raise _BudgetExhausted(f"budget of {budget} calls exhausted")

    raw = provider.complete(messages)  # type: ignore[attr-defined]
    counter[0] += 1

    # Parse response.
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON array from prose response.
        stripped = raw.strip()
        start = stripped.find("[")
        end = stripped.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(stripped[start:end + 1])
            except json.JSONDecodeError:
                print(
                    f"  WARNING: could not parse LLM response for {scenario['id']}; "
                    f"dropping.  Raw: {raw[:200]!r}",
                    file=sys.stderr,
                )
                return []
        else:
            print(
                f"  WARNING: LLM response for {scenario['id']} is not valid JSON; "
                f"dropping.  Raw: {raw[:200]!r}",
                file=sys.stderr,
            )
            return []

    if not isinstance(parsed, list):
        print(
            f"  WARNING: LLM response for {scenario['id']} is not a JSON array; "
            f"dropping.  Got: {type(parsed).__name__}",
            file=sys.stderr,
        )
        return []

    validated: list[dict[str, Any]] = []
    for item in parsed:
        err = _validate_generated_phrasing(item, scenario)
        if err:
            print(
                f"  WARNING: dropping malformed phrasing for {scenario['id']}: {err}",
                file=sys.stderr,
            )
            continue
        validated.append({
            "text": item["text"],
            "note": f"generated style={item['style']} lang={item['language']}",
            "provenance": {
                "generated_by": "llm-expand",
                "style": item["style"],
                "language": item["language"],
                "reviewed": False,
            },
        })

    return validated


# ---------------------------------------------------------------------------
# Write back to scenario file
# ---------------------------------------------------------------------------

def _write_scenario_phrasings(
    scenario: dict[str, Any],
    new_phrasings: list[dict[str, Any]],
) -> None:
    """Append new phrasings to the scenario file on disk."""
    sid = scenario["id"]
    scenario_path = _SCENARIOS_DIR / f"{sid}.json"
    if not scenario_path.exists():
        print(
            f"  WARNING: scenario file not found: {scenario_path}; skipping write.",
            file=sys.stderr,
        )
        return

    with open(scenario_path) as f:
        on_disk = json.load(f)

    existing_texts = {p.get("text", "") for p in (on_disk.get("phrasings") or [])}
    added = 0
    for p in new_phrasings:
        if p["text"] not in existing_texts:
            on_disk.setdefault("phrasings", []).append(p)
            existing_texts.add(p["text"])
            added += 1

    if added == 0:
        print(f"  {sid}: no new phrasings to add (all duplicates).")
        return

    with open(scenario_path, "w") as f:
        json.dump(on_disk, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"  {sid}: added {added} new phrasing(s).  Review with: git diff {scenario_path}")


# ---------------------------------------------------------------------------
# Public entry for tests
# ---------------------------------------------------------------------------

def run_gen(
    *,
    budget: int = _DEFAULT_BUDGET,
    only: str | None = None,
    dry_run: bool = False,
    provider: object | None = None,
) -> dict[str, Any]:
    """Generate phrasings for matching scenarios.

    Parameters
    ----------
    budget:
        Maximum live LLM calls.
    only:
        Optional fnmatch glob to filter scenario ids.
    dry_run:
        Print what would be generated without calling the LLM or writing files.
    provider:
        Inject a fake provider for tests.  When None, _resolve_provider() is
        called (may SystemExit if claude is unavailable).

    Returns
    -------
    A summary dict with keys: scenarios_attempted, phrasings_generated,
    calls_used, budget_hit.
    """
    if provider is None:
        provider = _resolve_provider()

    scenarios = load_scenarios(_CORPUS_DIR)
    if only:
        scenarios = [s for s in scenarios if fnmatch.fnmatch(s["id"], only)]

    counter: list[int] = [0]
    scenarios_attempted = 0
    phrasings_generated = 0
    budget_hit = False

    for scenario in scenarios:
        sid = scenario["id"]
        try:
            new_phrasings = generate_phrasings_for_scenario(
                scenario,
                provider,
                counter,
                budget,
                dry_run=dry_run,
            )
        except _BudgetExhausted:
            print(
                f"\ngen_phrasings: budget of {budget} calls exhausted "
                f"after {scenarios_attempted} scenario(s).  "
                "Re-run with a larger --budget to continue.",
                file=sys.stderr,
            )
            budget_hit = True
            break
        except LlmProviderError as exc:
            print(
                f"\ngen_phrasings: provider error on {sid}: {exc}",
                file=sys.stderr,
            )
            budget_hit = True
            break

        scenarios_attempted += 1
        if new_phrasings:
            phrasings_generated += len(new_phrasings)
            if not dry_run:
                _write_scenario_phrasings(scenario, new_phrasings)

    return {
        "scenarios_attempted": scenarios_attempted,
        "phrasings_generated": phrasings_generated,
        "calls_used": counter[0],
        "budget_hit": budget_hit,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate LLM phrasing expansions for corpus scenarios. "
            "Output is written to scenario files for human review - NEVER auto-committed."
        )
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=_DEFAULT_BUDGET,
        help=f"Max live LLM calls (default: {_DEFAULT_BUDGET}).  Stop cleanly when hit.",
    )
    parser.add_argument(
        "--only",
        metavar="GLOB",
        default=None,
        help="Expand only scenarios whose id matches this fnmatch glob.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be generated without calling the LLM or writing files.",
    )
    args = parser.parse_args()

    summary = run_gen(
        budget=args.budget,
        only=args.only,
        dry_run=args.dry_run,
    )

    print(
        f"\n[gen_phrasings summary]"
        f"\n  scenarios attempted : {summary['scenarios_attempted']}"
        f"\n  phrasings generated : {summary['phrasings_generated']}"
        f"\n  live calls used     : {summary['calls_used']}"
    )
    if summary["budget_hit"]:
        print("  budget hit: increase --budget and re-run to continue.")
    if not args.dry_run and summary["phrasings_generated"] > 0:
        print(
            "\nNext steps:"
            "\n  1. Review the diff: git diff tests/qa_corpus/scenarios/"
            "\n  2. Edit phrasings as needed."
            "\n  3. Re-record cassettes for any new cassette-engine phrasings:"
            "\n     MAGI_CP_QA_RECORD=1 PYTHONPATH=src python scripts/qa/record.py"
            "\n  4. Run CI to verify: PYTHONPATH=src python3 -m pytest tests -q"
        )


if __name__ == "__main__":
    main()
