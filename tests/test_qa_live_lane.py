"""Unit tests for scripts/qa/run_live.py and scripts/qa/gen_phrasings.py (PR-F).

No real LLM calls are made.  All live providers are replaced with
FakeLlmProvider or error-raising fakes.

Discovered by: PYTHONPATH=src python3 -m pytest tests -q -k "qa"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

# Ensure src/ and tests/ are on path (mirrors script path setup).
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT / "tests"))

# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------


class _AlwaysPassProvider:
    """Returns a valid neutral compiler JSON response."""

    _EMPTY = json.dumps({
        "assistant_message": "",
        "draft_updates": {},
        "questions": [],
    })

    def complete(self, messages: Any) -> str:  # noqa: ANN001
        return self._EMPTY


class _RaisingProvider:
    """Always raises LlmProviderError."""

    def complete(self, messages: Any) -> str:  # noqa: ANN001
        from magi_cp.llm.provider import LlmProviderError
        raise LlmProviderError("simulated auth failure")


class _PhrasingProvider:
    """Returns a fixed JSON array of phrasings."""

    def __init__(self, phrasings: list[dict[str, Any]] | None = None) -> None:
        self._phrasings = phrasings or []

    def complete(self, messages: Any) -> str:  # noqa: ANN001
        return json.dumps(self._phrasings)


class _MalformedProvider:
    """Returns invalid JSON."""

    def complete(self, messages: Any) -> str:  # noqa: ANN001
        return "NOT VALID JSON AT ALL !!!"


# ---------------------------------------------------------------------------
# run_live.py tests
# ---------------------------------------------------------------------------


class TestRunLiveBudgetCap:
    """Budget counter stops after N calls."""

    def test_budget_cap_zero_stops_immediately(self) -> None:
        from scripts.qa.run_live import run_live

        summary = run_live(budget=0, provider=_AlwaysPassProvider(), output_dir=None)
        # With budget=0, no scenarios should be run (or budget hits immediately).
        assert summary["live_calls_used"] == 0

    def test_budget_cap_respected(self) -> None:
        """Budget of 1 stops after 1 LLM call."""
        from scripts.qa.run_live import _CountingProvider, _BudgetExhausted

        provider = _AlwaysPassProvider()
        counter: list[int] = [0]
        counting = _CountingProvider(provider, counter, 1)

        # First call succeeds.
        result = counting.complete([])
        assert counter[0] == 1
        assert "assistant_message" in result

        # Second call raises.
        with pytest.raises(_BudgetExhausted):
            counting.complete([])

    def test_budget_not_exceeded_when_sufficient(self) -> None:
        from scripts.qa.run_live import _CountingProvider

        provider = _AlwaysPassProvider()
        counter: list[int] = [0]
        counting = _CountingProvider(provider, counter, 10)

        for _ in range(5):
            counting.complete([])
        assert counter[0] == 5

    def test_run_live_returns_summary_dict(self) -> None:
        from scripts.qa.run_live import run_live

        summary = run_live(budget=5, provider=_AlwaysPassProvider(), output_dir=None)
        assert isinstance(summary, dict)
        assert "lane" in summary
        assert "live lane (non-deterministic)" in summary["lane"]
        assert "scenarios_run" in summary
        assert "live_calls_used" in summary

    def test_run_live_budget_hit_reflected_in_lane_label(self) -> None:
        from scripts.qa.run_live import run_live

        # Use a very tight budget (0) so it hits immediately.
        summary = run_live(budget=0, provider=_AlwaysPassProvider(), output_dir=None)
        # Lane label should contain budget info.
        assert "budget=0" in summary["lane"]


class TestRunLiveCleanSkip:
    """When claude is absent / unauthenticated, exit with actionable message."""

    def test_resolve_provider_exits_when_no_claude(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_resolve_provider() calls sys.exit(1) when claude is not on PATH."""

        # Monkeypatch claude_cli_available to return False.
        monkeypatch.setattr(
            "scripts.qa.run_live.claude_cli_available",
            lambda: False,
        )
        # Also ensure MAGI_CP_LLM_COMPILER is not set.
        monkeypatch.delenv("MAGI_CP_LLM_COMPILER", raising=False)

        import scripts.qa.run_live as live_module

        with pytest.raises(SystemExit) as exc_info:
            live_module._resolve_provider()
        assert exc_info.value.code == 1

    def test_resolve_provider_exits_with_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            "scripts.qa.run_live.claude_cli_available",
            lambda: False,
        )
        monkeypatch.delenv("MAGI_CP_LLM_COMPILER", raising=False)

        import scripts.qa.run_live as live_module

        with pytest.raises(SystemExit):
            live_module._resolve_provider()

        captured = capsys.readouterr()
        assert "claude login" in captured.err or "claude login" in captured.out

    def test_run_live_with_provider_does_not_call_claude(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Injecting a provider bypasses the claude-available check."""
        monkeypatch.setattr(
            "scripts.qa.run_live.claude_cli_available",
            lambda: False,
        )

        from scripts.qa.run_live import run_live

        # Should not SystemExit because provider is injected.
        summary = run_live(budget=5, provider=_AlwaysPassProvider(), output_dir=None)
        assert isinstance(summary, dict)


class TestRunLiveLlmProviderError:
    """LlmProviderError mid-run is caught and surfaces as early stop."""

    def test_provider_error_stops_run(self) -> None:
        from scripts.qa.run_live import run_live

        summary = run_live(budget=5, provider=_RaisingProvider(), output_dir=None)
        # Should complete without crashing; scenarios_run may be 0 or 1 depending
        # on whether the error fires before or after the first scenario.
        assert isinstance(summary, dict)
        assert "scenarios_run" in summary


# ---------------------------------------------------------------------------
# gen_phrasings.py tests
# ---------------------------------------------------------------------------


class TestGenPhrasingsBudgetCap:
    """Budget counter stops after N calls."""

    def test_budget_cap_respected(self) -> None:
        from scripts.qa.gen_phrasings import _CountingProvider, _BudgetExhausted

        provider = _AlwaysPassProvider()
        counter: list[int] = [0]
        counting = _CountingProvider(provider, counter, 2)

        counting.complete([])
        counting.complete([])
        with pytest.raises(_BudgetExhausted):
            counting.complete([])

    def test_run_gen_budget_zero(self) -> None:
        from scripts.qa.gen_phrasings import run_gen

        summary = run_gen(budget=0, provider=_PhrasingProvider(), only="*")
        # With budget=0 the first call should hit the cap.
        assert summary["calls_used"] == 0

    def test_run_gen_returns_summary(self) -> None:
        from scripts.qa.gen_phrasings import run_gen

        summary = run_gen(budget=100, provider=_PhrasingProvider(), dry_run=True)
        assert isinstance(summary, dict)
        assert "scenarios_attempted" in summary
        assert "phrasings_generated" in summary
        assert "calls_used" in summary
        assert "budget_hit" in summary

    def test_run_gen_dry_run_makes_no_calls(self) -> None:
        from scripts.qa.gen_phrasings import run_gen

        summary = run_gen(budget=100, provider=_PhrasingProvider(), dry_run=True)
        # Dry-run must not make any LLM calls.
        assert summary["calls_used"] == 0
        assert summary["phrasings_generated"] == 0


class TestGenPhrasingsSchemaValidation:
    """Malformed LLM output is dropped; valid output passes."""

    def test_malformed_json_dropped(self) -> None:
        from scripts.qa.gen_phrasings import run_gen

        summary = run_gen(
            budget=100,
            provider=_MalformedProvider(),
        )
        # Malformed responses produce zero validated phrasings.
        assert summary["phrasings_generated"] == 0

    def test_invalid_phrasing_dropped(self) -> None:
        """A phrasing missing required fields is dropped with a warning."""
        from scripts.qa.gen_phrasings import _validate_generated_phrasing

        # Missing 'text' field.
        err = _validate_generated_phrasing(
            {"language": "en", "style": "terse"},
            {},
        )
        assert err is not None
        assert "text" in err

    def test_invalid_language_dropped(self) -> None:
        from scripts.qa.gen_phrasings import _validate_generated_phrasing

        err = _validate_generated_phrasing(
            {"text": "block bash", "language": "fr", "style": "terse"},
            {},
        )
        assert err is not None
        assert "language" in err

    def test_invalid_style_dropped(self) -> None:
        from scripts.qa.gen_phrasings import _validate_generated_phrasing

        err = _validate_generated_phrasing(
            {"text": "block bash", "language": "en", "style": "poetic"},
            {},
        )
        assert err is not None
        assert "style" in err

    def test_valid_phrasing_passes_validation(self) -> None:
        from scripts.qa.gen_phrasings import _validate_generated_phrasing

        err = _validate_generated_phrasing(
            {"text": "Block all bash commands", "language": "en", "style": "terse"},
            # Minimal scenario dict - validate_scenario checks schema_version etc.
            {
                "schema_version": 1,
                "id": "s-test",
                "category": "happy_path",
                "language": "en",
                "style": "canonical",
                "engine": "fake_empty",
                "stable": True,
                "known_limitation": False,
                "target_ir": {
                    "trigger": {
                        "host": "claude-code",
                        "event": "PreToolUse",
                        "matcher": "Bash",
                    },
                    "action": "block",
                    "requires": [],
                    "id": "qa-target",
                },
                "expected": {"outcome": "saved", "feasibility_code": None, "max_turns": 8},
                "phrasings": [{"text": "original phrasing", "note": "seed"}],
                "provenance": {
                    "source": "test",
                    "generated_by": "human",
                    "reviewed": True,
                },
                "compound_gate_matcher": None,
                "runtime_id": None,
            },
        )
        assert err is None

    def test_not_a_dict_dropped(self) -> None:
        from scripts.qa.gen_phrasings import _validate_generated_phrasing

        err = _validate_generated_phrasing("plain string", {})
        assert err is not None
        assert "not a dict" in err


class TestGenPhrasingsCleanSkip:
    """When claude is absent, exit with actionable message."""

    def test_resolve_provider_exits_when_no_claude(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "scripts.qa.gen_phrasings.claude_cli_available",
            lambda: False,
        )
        monkeypatch.delenv("MAGI_CP_LLM_COMPILER", raising=False)

        import scripts.qa.gen_phrasings as gen_module

        with pytest.raises(SystemExit) as exc_info:
            gen_module._resolve_provider()
        assert exc_info.value.code == 1

    def test_run_gen_with_injected_provider_no_exit(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "scripts.qa.gen_phrasings.claude_cli_available",
            lambda: False,
        )
        from scripts.qa.gen_phrasings import run_gen

        # Injected provider: no SystemExit.
        summary = run_gen(budget=5, provider=_PhrasingProvider(), dry_run=True)
        assert isinstance(summary, dict)


class TestRunLiveAndGenImportable:
    """Both scripts must be importable without making any real calls."""

    def test_run_live_importable(self) -> None:
        import scripts.qa.run_live  # noqa: F401

    def test_gen_phrasings_importable(self) -> None:
        import scripts.qa.gen_phrasings  # noqa: F401
