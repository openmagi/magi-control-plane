"""P2 Codex adapter: coverage-report golden.

Design brief: 2026-06-30-codex-runtime-adapter-design (private planning repo)
Section 4 + Section 7.2. A fixed fixture policy list exercises all four
shims plus the native-config-pending archetypes, and its serialized
CoverageReport is pinned to
``tests/goldens/codex_coverage_report_golden.json``.

Regenerating the golden requires an explicit opt-in
(``MAGI_CP_UPDATE_GOLDENS=1``); any unintended coverage drift fails CI
instead of silently rewriting the pin.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from magi_cp.policy.ir import (
    ContextInjectionPolicy,
    EvidencePolicy,
    EvidenceReq,
    McpGatingPolicy,
    PermissionPolicy,
    SubagentPolicy,
    Trigger,
)
from magi_cp.runtime.codex import CodexDriver
from magi_cp.runtime.trait import CoverageReport

_GOLDEN_PATH = Path(__file__).parent / "goldens" / "codex_coverage_report_golden.json"


def _evidence(pid: str, *, event="PreToolUse", matcher="Bash",
              action="block") -> EvidencePolicy:
    return EvidencePolicy(
        id=pid, description="t", version="0.1",
        trigger=Trigger(host="claude-code", event=event, matcher=matcher),
        sentinel_re=None,
        requires=[EvidenceReq(kind="step", step="privilege_scan",
                              verdict="pass")],
        action=action, on_signature_invalid="deny",
        gate_binary="/usr/local/bin/magi-gate.sh",
    )


def _fixture_policies():
    """A stable list that hits every coverage status the shims produce."""
    return [
        _evidence("ev/bash-enforced", matcher="Bash"),
        # Read maps onto a Codex silent-skip tool (list_dir is the
        # Codex-native alias, unauthorable via the CC matcher grammar).
        _evidence("ev/read-silent-skip", matcher="Read"),
        ContextInjectionPolicy(
            id="ctx/pretool-downgrade", description="t",
            event="PreToolUse", template="always cite", matcher="Bash",
        ),
        _evidence("ev/session-end", event="SessionEnd", matcher="*",
                  action="audit"),
        _evidence("ev/subagent-stop", event="SubagentStop", matcher="*",
                  action="audit"),
        PermissionPolicy(
            id="perm/deny-rm", description="t", version="0.1",
            trigger=Trigger(host="claude-code", event="PreToolUse",
                            matcher="Bash"),
            permission="deny", pattern="Bash(rm -rf /*)",
        ),
        McpGatingPolicy(
            id="mcp/deny-github", description="t", version="0.1",
            server="github", action="deny",
        ),
        SubagentPolicy(
            id="sub/disable-researcher", description="t", version="0.1",
            subagent_type="researcher",
        ),
    ]


def _report_to_dict(report: CoverageReport) -> dict:
    return {
        "runtime_id": report.runtime_id,
        "enforced_count": report.enforced_count,
        "downgraded_count": report.downgraded_count,
        "policies": [
            {
                "policy_id": p.policy_id,
                "status": p.status,
                "downgrade": p.downgrade,
            }
            for p in report.policies
        ],
    }


def test_coverage_report_golden():
    report = CodexDriver().coverage_report(_fixture_policies())
    actual = _report_to_dict(report)

    if os.environ.get("MAGI_CP_UPDATE_GOLDENS") == "1":
        _GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        _GOLDEN_PATH.write_text(
            json.dumps(actual, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    expected = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
    assert actual == expected, (
        "Codex coverage-report drift. If intended, regenerate with "
        "MAGI_CP_UPDATE_GOLDENS=1 pytest "
        "tests/test_codex_coverage_golden.py"
    )
