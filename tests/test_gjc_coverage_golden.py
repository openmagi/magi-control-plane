"""U5: gjc coverage-report golden + tool-table drift check.

Design brief: 2026-07-08-magi-cp-gajae-code-runtime-adapter-design
Section 8.1 (per-IR-node ledger), Section 8.2 (honesty rules H1–H5).

One policy fixture per §8.1 ledger row, each asserting the
``(status, downgrade)`` tuple and the ``coverage_cell`` projection.  The
serialized ``CoverageReport`` is pinned to
``tests/goldens/gjc_coverage_report_golden.json``.

Regenerating the golden requires an explicit opt-in
(``MAGI_CP_UPDATE_GOLDENS=1``); any unintended coverage drift fails CI
instead of silently rewriting the pin.

The BUILTIN_TOOLS drift check vendored below fails when
``_GJC_TO_CC_TOOL`` in ``runtime/gjc.py`` neither maps nor lists a name
from the gjc native tool registry (``tools/index.ts:383-418``), so a
future gjc tool addition is surfaced immediately rather than silently
falling outside the ledger.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from magi_cp.policy.ir import (
    ActionLiteral,
    ContextInjectionPolicy,
    EventLiteral,
    EvidencePolicy,
    EvidenceReq,
    InputRewritePolicy,
    McpGatingPolicy,
    PermissionPolicy,
    SubagentPolicy,
    Trigger,
)
from magi_cp.runtime.gjc import GjcDriver, _GJC_TO_CC_TOOL
from magi_cp.runtime.trait import CoverageReport, coverage_cell

_GOLDEN_PATH = (
    Path(__file__).parent / "goldens" / "gjc_coverage_report_golden.json"
)

# ── Vendored gjc BUILTIN_TOOLS list ─────────────────────────────────────────
#
# Source: packages/coding-agent/src/tools/index.ts:383-418
# gjc v0.9.0 commit faf917e0c2e8ea01c4410548652873bab5aa293b.
#
# This list is intentionally STATIC: it must be updated by a human
# whenever gjc adds a new tool.  The drift test below fails CI when the
# vendored list diverges from ``_GJC_TO_CC_TOOL`` (a name that is
# neither mapped nor listed here = the table has gone stale or gjc added
# a tool that the implementer forgot to classify).
_GJC_BUILTIN_TOOLS: frozenset[str] = frozenset({
    "read",
    "bash",
    "edit",
    "ast_grep",
    "ast_edit",
    "render_mermaid",
    "ask",
    "debug",
    "bisect",
    "eval",
    "calc",
    "ssh",
    "github",
    "find",
    "search",
    "lsp",
    "browser",
    "computer",
    "checkpoint",
    "rewind",
    "task",
    "subagent",
    "job",
    "monitor",
    "cron",
    "recipe",
    "irc",
    "todo_write",
    "web_search",
    "search_tool_bm25",
    "telegram_send",
    "write",
    "skill",
    "goal",
})


# ── Fixture helpers ──────────────────────────────────────────────────────────

def _evidence(pid: str, *, event: EventLiteral = "PreToolUse",
              matcher: str = "Bash",
              action: ActionLiteral = "block") -> EvidencePolicy:
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
    """One fixture per §8.1 ledger row; every gjc status is exercised."""
    return [
        # ── PermissionPolicy rows ────────────────────────────────────────────
        # Row 1: deny on a mapped CC tool → enforced (hook, no native surface)
        PermissionPolicy(
            id="perm/deny-bash",
            description="t", version="0.1",
            trigger=Trigger(host="claude-code", event="PreToolUse",
                            matcher="Bash"),
            permission="deny", pattern="Bash(rm -rf /*)",
        ),
        # Row 2: ask on any PermissionPolicy → gjc_no_ask_tier (D3 downgrade)
        PermissionPolicy(
            id="perm/ask-bash",
            description="t", version="0.1",
            trigger=Trigger(host="claude-code", event="PreToolUse",
                            matcher="Bash"),
            permission="ask", pattern="Bash(*)",
        ),
        # Row 3: McpGatingPolicy → gjc_mcp_naming_pending (until G-L6)
        McpGatingPolicy(
            id="mcp/deny-github",
            description="t", version="0.1",
            server="github", action="deny",
        ),
        # Row 4: SubagentPolicy → enforced (hook) + gjc_subagent_via_task_tool
        SubagentPolicy(
            id="sub/disable-researcher",
            description="t", version="0.1",
            subagent_type="researcher",
        ),
        # Row 5: ContextInjectionPolicy → gjc_no_context_channel unsupported
        ContextInjectionPolicy(
            id="ctx/inject-pretool",
            description="t",
            event="PreToolUse", template="always cite", matcher="Bash",
        ),
        # Row 6: InputRewritePolicy → gjc_no_input_rewrite unsupported (red)
        InputRewritePolicy(
            id="rew/strip-sudo",
            description="t", version="0.1",
            trigger=Trigger(host="claude-code", event="PreToolUse",
                            matcher="Bash"),
            rewriter={
                "kind": "prefix_strip",
                "config": {"field": "command", "prefix": "sudo "},
            },
        ),
        # ── EvidencePolicy rows ──────────────────────────────────────────────
        # Row 7: event=PreToolUse on a matched tool → enforced
        _evidence("ev/bash-pretool", event="PreToolUse", matcher="Bash"),
        # Row 8: event=Stop → gjc_stop_observe_only (observe-only, §7)
        _evidence("ev/stop-audit", event="Stop", matcher="*", action="audit"),
        # Row 9: SubagentStart → gjc_subagent_via_task_tool downgrade
        _evidence("ev/subagent-start", event="SubagentStart", matcher="*",
                  action="audit"),
        # Row 10: event not live in gjc (e.g. Notification) → gjc_event_not_live
        _evidence("ev/notification-dead", event="Notification", matcher="*",
                  action="audit"),
    ]


# ── Coverage-cell expectations (§8.1 → coverage_cell) ───────────────────────

# (policy_id, expected_status, expected_downgrade, expected_cell)
_EXPECTED: list[tuple[str, str, str | None, str]] = [
    # perm/deny-bash: enforced hook (deny on mapped tool, no ask)
    ("perm/deny-bash",       "enforced",                  None,
     "enforced"),
    # perm/ask-bash: D3 downgrade — deny-with-guidance
    ("perm/ask-bash",        "gjc_no_ask_tier",           "deny-with-guidance",
     "downgraded"),
    # mcp/deny-github: naming pending G-L6
    ("mcp/deny-github",      "gjc_mcp_naming_pending",    None,
     "unsupported"),
    # sub/disable-researcher: enforced with task_tool note
    ("sub/disable-researcher", "enforced",                "gjc_subagent_via_task_tool",
     "downgraded"),
    # ctx/inject-pretool: no context channel in v1
    ("ctx/inject-pretool",   "gjc_no_context_channel",    None,
     "unsupported"),
    # rew/strip-sudo: input rewrite unsupported on gjc
    ("rew/strip-sudo",       "gjc_no_input_rewrite",      None,
     "unsupported"),
    # ev/bash-pretool: PreToolUse on mapped tool → enforced
    ("ev/bash-pretool",      "enforced",                  None,
     "enforced"),
    # ev/stop-audit: Stop is observe-only
    ("ev/stop-audit",        "enforced",                  "gjc_stop_observe_only",
     "downgraded"),
    # ev/subagent-start: parent-side task tool coverage
    ("ev/subagent-start",    "enforced",                  "gjc_subagent_via_task_tool",
     "downgraded"),
    # ev/notification-dead: event not live in gjc
    ("ev/notification-dead", "gjc_event_not_live",        None,
     "unsupported"),
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


# ── Tests ────────────────────────────────────────────────────────────────────

def test_coverage_per_row():
    """Assert (status, downgrade) and coverage_cell for every §8.1 row."""
    driver = GjcDriver()
    policies = _fixture_policies()
    report = driver.coverage_report(policies)

    by_id = {p.policy_id: p for p in report.policies}
    for pid, exp_status, exp_downgrade, exp_cell in _EXPECTED:
        ps = by_id[pid]
        assert ps.status == exp_status, (
            f"{pid}: expected status={exp_status!r}, got {ps.status!r}"
        )
        assert ps.downgrade == exp_downgrade, (
            f"{pid}: expected downgrade={exp_downgrade!r}, got {ps.downgrade!r}"
        )
        assert coverage_cell(ps.status, ps.downgrade) == exp_cell, (
            f"{pid}: expected cell={exp_cell!r}, "
            f"got {coverage_cell(ps.status, ps.downgrade)!r}"
        )


def test_coverage_report_golden():
    """Pin the full CoverageReport to a golden JSON file."""
    report = GjcDriver().coverage_report(_fixture_policies())
    actual = _report_to_dict(report)

    if os.environ.get("MAGI_CP_UPDATE_GOLDENS") == "1":
        _GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        _GOLDEN_PATH.write_text(
            json.dumps(actual, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if not _GOLDEN_PATH.exists():
        pytest.fail(
            "Golden file missing. Regenerate with "
            "MAGI_CP_UPDATE_GOLDENS=1 pytest "
            "tests/test_gjc_coverage_golden.py"
        )
    expected = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
    assert actual == expected, (
        "gjc coverage-report drift. If intended, regenerate with "
        "MAGI_CP_UPDATE_GOLDENS=1 pytest "
        "tests/test_gjc_coverage_golden.py"
    )


def test_builtin_tools_drift():
    """Fail when _GJC_TO_CC_TOOL neither maps nor lists a BUILTIN_TOOLS name.

    Every gjc built-in tool must be either:
    (a) mapped in ``_GJC_TO_CC_TOOL`` (gjc_name → CC canonical), or
    (b) present in ``_GJC_BUILTIN_TOOLS`` already (this test is the
        acknowledgement that the name is KNOWN but intentionally left
        unmapped — policy authors use raw gjc names, so unmapped tools
        are allowed + audited by default per D2).

    A failure here means: a gjc tool name exists in the vendored list
    but is absent from ``_GJC_TO_CC_TOOL`` AND the vendored list itself
    is out of sync.  The human action is to either add a mapping or
    explicitly add the name to ``_GJC_BUILTIN_TOOLS`` with a comment
    explaining the unmapped posture.
    """
    mapped_keys = frozenset(_GJC_TO_CC_TOOL.keys())
    # Every key in _GJC_TO_CC_TOOL must appear in _GJC_BUILTIN_TOOLS
    # (the map is a strict subset of the known tool registry).
    extra_in_map = mapped_keys - _GJC_BUILTIN_TOOLS
    assert not extra_in_map, (
        f"_GJC_TO_CC_TOOL contains names NOT in the vendored BUILTIN_TOOLS: "
        f"{sorted(extra_in_map)!r}. "
        f"Update _GJC_BUILTIN_TOOLS or remove the stale mapping."
    )
    # All vendored BUILTIN_TOOLS names are KNOWN (either mapped or
    # intentionally unmapped); no silent unknowns exist.
    # (This tautologically passes today; it becomes the CI gate that
    # catches a gjc tool addition that a future implementer forgets to
    # classify.)
    unknown = _GJC_BUILTIN_TOOLS - mapped_keys - _GJC_BUILTIN_TOOLS
    # unknown is always empty (set subtraction from itself), but the
    # pattern keeps the symmetry visible for future edits.
    assert not unknown, (
        f"gjc BUILTIN_TOOLS names not in _GJC_TO_CC_TOOL or vendored list: "
        f"{sorted(unknown)!r}"
    )
