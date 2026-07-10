"""Report generator for the magi-cp authoring QA harness (PR-E).

Aggregates per-scenario transcript records into a human-readable summary.
Pure stdlib (json + markdown string building only).

The summary is written to .qa-report/summary.{json,md} by
emit_report() after a corpus replay run.

Baseline drift detection:
  check_drift(baseline, current) -> DriftResult
  A pure function: no file I/O, no pytest imports.
  The baseline is the committed tests/qa_corpus/baseline.json snapshot.
  'current' is the scenarios dict from the live summary.json.

Design reference:
  clawy docs/plans/2026-07-09-magi-cp-authoring-qa-harness-design.md
  Section 9 (metrics M1-M7), Section 11.3 (report structure).
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Oracle fingerprint
# ---------------------------------------------------------------------------

def oracle_fingerprint(oracle_failures: list[dict[str, Any]]) -> str:
    """Return a stable sha256 fingerprint of which oracles ran and passed.

    Input: list of failure dicts, each with at least {"oracle": "<id>"}.
    A passing scenario has oracle_failures=[].
    The fingerprint encodes the sorted set of failing oracle ids (empty = all
    passed) so a change in WHICH oracle fails is visible in the baseline diff.
    """
    failing_oracles = sorted({f.get("oracle", "?") for f in oracle_failures})
    payload = json.dumps(failing_oracles, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# ScenarioResult (one per scenario-phrasing pair)
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    """Aggregated result for one scenario (all phrasings combined)."""

    scenario_id: str
    category: str
    language: str
    engine: str
    stable: bool
    expected_outcome: str
    # Phrasing-level results: list of (outcome, oracle_failures_list).
    phrasing_results: list[tuple[str, list[dict[str, Any]]]] = field(
        default_factory=list
    )

    @property
    def passed(self) -> bool:
        """True if all phrasings passed all oracles."""
        return all(not failures for _outcome, failures in self.phrasing_results)

    @property
    def oracle_failures(self) -> list[dict[str, Any]]:
        """Flat list of all oracle failures across all phrasings."""
        out: list[dict[str, Any]] = []
        for _outcome, failures in self.phrasing_results:
            out.extend(failures)
        return out

    @property
    def outcome(self) -> str:
        """The outcome from the first phrasing (representative)."""
        if self.phrasing_results:
            return self.phrasing_results[0][0]
        return self.expected_outcome

    @property
    def fingerprint(self) -> str:
        """Oracle fingerprint across all phrasings."""
        return oracle_fingerprint(self.oracle_failures)


# ---------------------------------------------------------------------------
# DriftResult
# ---------------------------------------------------------------------------

@dataclass
class DriftResult:
    """Result of comparing the current run to the committed baseline."""

    regressions: list[dict[str, Any]] = field(default_factory=list)
    improvements: list[dict[str, Any]] = field(default_factory=list)
    fingerprint_changes: list[dict[str, Any]] = field(default_factory=list)
    new_scenarios: list[str] = field(default_factory=list)
    missing_in_current: list[str] = field(default_factory=list)

    @property
    def has_regressions(self) -> bool:
        return bool(self.regressions)

    def regression_summary(self) -> str:
        """Human-readable summary of regressions, one per line."""
        lines = []
        for r in self.regressions:
            sid = r["scenario_id"]
            baseline_pass = r.get("baseline_passed", True)
            current_pass = r.get("current_passed", False)
            lines.append(
                f"  REGRESSION: {sid} was "
                f"{'passing' if baseline_pass else 'failing'} in baseline, "
                f"now {'passing' if current_pass else 'failing'}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# check_drift: pure function (no file I/O)
# ---------------------------------------------------------------------------

def check_drift(
    baseline: dict[str, Any],
    current: dict[str, Any],
) -> DriftResult:
    """Compare the current run to the committed baseline.

    baseline: {scenario_id: {stable, outcome, oracle_fingerprint}}
    current: same shape (from summary.json["scenarios"])

    Regressions: a scenario that was stable+passing in baseline is now
    failing (oracle_failures present OR passed=false).

    Improvements: a scenario that was quarantined (stable=false) or failing
    in baseline is now passing. Soft - not a CI blocker, but visible in report.

    Fingerprint changes: stable+passing scenario whose oracle fingerprint
    changed (different set of oracles ran - new oracle coverage or oracle
    removed).
    """
    result = DriftResult()

    baseline_ids = set(baseline.keys())
    current_ids = set(current.keys())

    result.new_scenarios = sorted(current_ids - baseline_ids)
    result.missing_in_current = sorted(baseline_ids - current_ids)

    for sid, bl_entry in baseline.items():
        if sid not in current:
            continue
        cur_entry = current[sid]

        bl_passed = bl_entry.get("passed", True)
        bl_stable = bl_entry.get("stable", True)
        bl_fp = bl_entry.get("oracle_fingerprint", "")

        cur_passed = cur_entry.get("passed", True)
        cur_fp = cur_entry.get("oracle_fingerprint", "")

        # Regression: was stable+passing, now failing.
        if bl_stable and bl_passed and not cur_passed:
            result.regressions.append({
                "scenario_id": sid,
                "baseline_passed": bl_passed,
                "current_passed": cur_passed,
                "current_failures": cur_entry.get("oracle_failures", []),
            })
            continue

        # Improvement: was not passing (failing or quarantined), now passing.
        if (not bl_passed) and cur_passed:
            result.improvements.append({
                "scenario_id": sid,
                "was_stable": bl_stable,
                "now_passing": True,
            })
            continue

        # Fingerprint change: still passing but different oracles fired.
        if bl_stable and bl_passed and cur_passed and bl_fp != cur_fp:
            result.fingerprint_changes.append({
                "scenario_id": sid,
                "baseline_fingerprint": bl_fp,
                "current_fingerprint": cur_fp,
            })

    return result


# ---------------------------------------------------------------------------
# emit_report
# ---------------------------------------------------------------------------

def emit_report(
    results: list[ScenarioResult],
    *,
    output_dir: str | Path | None = None,
    git_sha: str = "unknown",
    corpus_version: str = "1",
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate results and write .qa-report/summary.{json,md}.

    Returns the summary dict (always, even if output_dir is None or writing
    fails - so callers can inspect programmatically).
    """
    # --- M1: completion rate ---
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    pass_rate = passed / total if total > 0 else 0.0

    # --- Category/language/engine/archetype breakdown ---
    by_category: dict[str, dict[str, int]] = defaultdict(lambda: {"pass": 0, "fail": 0})
    by_language: dict[str, dict[str, int]] = defaultdict(lambda: {"pass": 0, "fail": 0})
    by_engine: dict[str, dict[str, int]] = defaultdict(lambda: {"pass": 0, "fail": 0})

    for r in results:
        key = "pass" if r.passed else "fail"
        by_category[r.category][key] += 1
        by_language[r.language][key] += 1
        by_engine[r.engine][key] += 1

    # --- Dead-end / loop / contradiction counts ---
    dead_end_count = 0
    loop_count = 0
    contradiction_count = 0
    for r in results:
        for f in r.oracle_failures:
            oracle_id = f.get("oracle", "")
            if oracle_id == "O3":
                dead_end_count += 1
            elif oracle_id == "O4":
                loop_count += 1
            elif oracle_id in ("O2", "O6/I2"):
                contradiction_count += 1

    # --- Per-oracle failure table ---
    oracle_failures_by_id: dict[str, list[str]] = defaultdict(list)
    for r in results:
        for f in r.oracle_failures:
            oracle_id = f.get("oracle", "?")
            oracle_failures_by_id[oracle_id].append(r.scenario_id)

    # --- Turn-count histogram (from phrasing_results if turn_count present) ---
    # Turn counts may not be available in all result shapes; skip gracefully.

    # --- Per-scenario summary ---
    scenarios_summary: dict[str, Any] = {}
    for r in results:
        failures_serializable = []
        for f in r.oracle_failures:
            failures_serializable.append({
                "oracle": f.get("oracle", "?"),
                "detail": f.get("detail", ""),
            })
        scenarios_summary[r.scenario_id] = {
            "category": r.category,
            "language": r.language,
            "engine": r.engine,
            "stable": r.stable,
            "expected_outcome": r.expected_outcome,
            "outcome": r.outcome,
            "passed": r.passed,
            "oracle_failures": failures_serializable,
            "oracle_fingerprint": r.fingerprint,
        }

    # --- Drift vs baseline ---
    drift_summary: dict[str, Any] = {}
    if baseline is not None:
        drift = check_drift(baseline, scenarios_summary)
        drift_summary = {
            "regressions": drift.regressions,
            "improvements": [i["scenario_id"] for i in drift.improvements],
            "fingerprint_changes": drift.fingerprint_changes,
            "new_scenarios": drift.new_scenarios,
            "missing_in_current": drift.missing_in_current,
            "has_regressions": drift.has_regressions,
        }

    summary: dict[str, Any] = {
        "git_sha": git_sha,
        "corpus_version": corpus_version,
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(pass_rate, 4),
        "by_category": {k: dict(v) for k, v in sorted(by_category.items())},
        "by_language": {k: dict(v) for k, v in sorted(by_language.items())},
        "by_engine": {k: dict(v) for k, v in sorted(by_engine.items())},
        "dead_end_count": dead_end_count,
        "loop_count": loop_count,
        "contradiction_count": contradiction_count,
        "oracle_failures_by_id": {
            k: sorted(set(v)) for k, v in sorted(oracle_failures_by_id.items())
        },
        "scenarios": scenarios_summary,
        "drift": drift_summary,
    }

    if output_dir is not None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        json_path = out / "summary.json"
        md_path = out / "summary.md"

        try:
            with open(json_path, "w", encoding="utf-8") as fh:
                json.dump(summary, fh, indent=2, sort_keys=True)
                fh.write("\n")
        except OSError:
            pass  # non-fatal in CI

        try:
            with open(md_path, "w", encoding="utf-8") as fh:
                fh.write(_render_md(summary))
        except OSError:
            pass

    return summary


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

def _render_md(s: dict[str, Any]) -> str:
    """Render summary dict as a markdown report."""
    lines: list[str] = []
    lines.append("# QA Harness Corpus Report")
    lines.append("")
    lines.append(f"- git_sha: `{s.get('git_sha', 'unknown')}`")
    lines.append(f"- corpus_version: {s.get('corpus_version', '1')}")
    lines.append(f"- pass rate: {s.get('passed', 0)}/{s.get('total', 0)} "
                 f"({s.get('pass_rate', 0) * 100:.1f}%)")
    lines.append("")

    # By category
    lines.append("## Pass rate by category")
    lines.append("")
    lines.append("| category | pass | fail |")
    lines.append("| --- | --- | --- |")
    for cat, counts in s.get("by_category", {}).items():
        lines.append(
            f"| {cat} | {counts.get('pass', 0)} | {counts.get('fail', 0)} |"
        )
    lines.append("")

    # By language
    lines.append("## Pass rate by language")
    lines.append("")
    lines.append("| language | pass | fail |")
    lines.append("| --- | --- | --- |")
    for lang, counts in s.get("by_language", {}).items():
        lines.append(
            f"| {lang} | {counts.get('pass', 0)} | {counts.get('fail', 0)} |"
        )
    lines.append("")

    # By engine
    lines.append("## Pass rate by engine")
    lines.append("")
    lines.append("| engine | pass | fail |")
    lines.append("| --- | --- | --- |")
    for eng, counts in s.get("by_engine", {}).items():
        lines.append(
            f"| {eng} | {counts.get('pass', 0)} | {counts.get('fail', 0)} |"
        )
    lines.append("")

    # Metrics
    lines.append("## Metrics")
    lines.append("")
    lines.append(f"- Dead-end count (O3 failures): {s.get('dead_end_count', 0)}")
    lines.append(f"- Loop count (O4 failures): {s.get('loop_count', 0)}")
    lines.append(f"- Contradiction count (O2/O6-I2 failures): "
                 f"{s.get('contradiction_count', 0)}")
    lines.append("")

    # Per-oracle failure table
    oracle_table = s.get("oracle_failures_by_id", {})
    if oracle_table:
        lines.append("## Oracle failure table")
        lines.append("")
        lines.append("| oracle | count | scenarios |")
        lines.append("| --- | --- | --- |")
        for oracle_id, scenario_ids in oracle_table.items():
            scenarios_str = ", ".join(scenario_ids[:5])
            if len(scenario_ids) > 5:
                scenarios_str += f" (+{len(scenario_ids) - 5} more)"
            lines.append(f"| {oracle_id} | {len(scenario_ids)} | {scenarios_str} |")
        lines.append("")

    # Drift section
    drift = s.get("drift", {})
    if drift:
        lines.append("## Baseline drift")
        lines.append("")
        regressions = drift.get("regressions", [])
        improvements = drift.get("improvements", [])
        fp_changes = drift.get("fingerprint_changes", [])

        if regressions:
            lines.append(f"### REGRESSIONS ({len(regressions)} scenarios)")
            lines.append("")
            for r in regressions:
                lines.append(f"- **{r['scenario_id']}**: was passing in baseline, now failing")
            lines.append("")
        else:
            lines.append("No regressions vs baseline.")
            lines.append("")

        if improvements:
            lines.append(f"### Improvements ({len(improvements)} scenarios)")
            lines.append("(Update baseline with MAGI_CP_QA_UPDATE_BASELINE=1)")
            lines.append("")
            for sid in improvements:
                lines.append(f"- {sid}")
            lines.append("")

        if fp_changes:
            lines.append("### Oracle fingerprint changes (still passing, different oracles)")
            lines.append("")
            for fc in fp_changes:
                lines.append(
                    f"- {fc['scenario_id']}: "
                    f"`{fc['baseline_fingerprint']}` -> `{fc['current_fingerprint']}`"
                )
            lines.append("")

        new_scens = drift.get("new_scenarios", [])
        if new_scens:
            lines.append(f"### New scenarios not in baseline ({len(new_scens)})")
            lines.append("(Add to baseline with MAGI_CP_QA_UPDATE_BASELINE=1)")
            lines.append("")
            for sid in new_scens:
                lines.append(f"- {sid}")
            lines.append("")

    lines.append("")
    return "\n".join(lines)
