"""Static vendor catalog of magi-agent preset IDs.

Source: magi-agent commit @ 2026-06-19, harness/presets.py + customize/preset_map.py.
Purpose: surface label parity with magi-agent's customize tab so an operator
who knows the magi-agent catalog finds the same preset names here. None of
these are "wired" to a runtime gate in magi-control-plane — they are honest
preview entries until/unless a Verifier is registered with a matching step.

Wired entries (our 5 from PB) are sourced from the live VerifierRegistry, not
this file. /presets merges the two.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VendorPreset:
    """Read-only catalog entry vendored from magi-agent for label parity."""

    id: str
    category: str       # ANSWER/FACT/CODING/TASK/OUTPUT/RESEARCH/MEMORY/SECURITY
    description: str


def _title_from_key(key: str) -> str:
    return key.replace("-", " ").title()


# Vendored verbatim from magi-agent/customize/preset_map.py _DESCRIPTIONS
# where present, with the canonical magi-agent intent copy. The categories
# come from magi-agent/harness/presets.py _BUILTIN_PRESETS.
_VENDOR: tuple[VendorPreset, ...] = (
    # ── ANSWER ────────────────────────────────────────────────────
    VendorPreset("answer-quality", "ANSWER",
                 "Verifies the response actually answers the question."),
    VendorPreset("completion-evidence", "ANSWER",
                 "Checks completion claims have actual evidence."),
    VendorPreset("pre-refusal", "ANSWER",
                 "Prevents rushing to refuse tasks it can handle."),
    VendorPreset("output-purity", "ANSWER",
                 "Blocks raw JSON or internal data from appearing in responses."),
    VendorPreset("deferral-blocker", "ANSWER",
                 "Forces completion now instead of promising future delivery."),
    # ── FACT ──────────────────────────────────────────────────────
    VendorPreset("fact-grounding", "FACT",
                 "Block a specific factual value in the answer that isn't grounded in opened sources."),
    VendorPreset("self-claim", "FACT",
                 "Blocks claiming file contents without reading first."),
    VendorPreset("resource-existence", "FACT",
                 "Verifies referenced files actually exist."),
    VendorPreset("claim-citation", "FACT",
                 "Ensures factual claims include sources."),
    VendorPreset("deterministic-evidence", "FACT",
                 "Require recorded git-diff and test-run evidence on coding turns (disable to opt out)."),
    # ── CODING ────────────────────────────────────────────────────
    VendorPreset("coding-verification", "CODING",
                 "Require fresh test-pass evidence before the final answer when code is mutated."),
    VendorPreset("coding-context", "CODING",
                 "Auto-injects repo map and symbols for code tasks."),
    VendorPreset("coding-workspace-lock", "CODING",
                 "Prevents unrelated file changes during coding."),
    VendorPreset("coding-child-review", "CODING",
                 "Adversarial multi-model review of sub-agent output. Capability — enable with MAGI_CROSS_VERIFY_ENABLED."),
    VendorPreset("benchmark-verifier", "CODING",
                 "Detects and blocks performance regressions."),
    # ── TASK ──────────────────────────────────────────────────────
    VendorPreset("task-contract", "TASK",
                 "Enforces a goal to plan to evidence lifecycle."),
    VendorPreset("goal-progress", "TASK",
                 "Blocks completion claims without actual actions."),
    VendorPreset("task-board-completion", "TASK",
                 "Blocks completion when tasks remain incomplete."),
    VendorPreset("autopilot-phase-router", "TASK",
                 "Routes autopilot turns into FSM phases. Default-off."),
    VendorPreset("autopilot-interview-gate", "TASK",
                 "Holds autopilot until interview clarifies ambiguity. Default-off."),
    VendorPreset("autopilot-consensus-gate", "TASK",
                 "Architect-then-critic consensus before autopilot advances. Default-off."),
    VendorPreset("autopilot-review-gate", "TASK",
                 "Adversarial peer review before autopilot commits. Default-off."),
    VendorPreset("autopilot-qa-gate", "TASK",
                 "Adversarial QA before autopilot finishes a turn. Default-off."),
    # ── OUTPUT ────────────────────────────────────────────────────
    VendorPreset("output-delivery", "OUTPUT",
                 "Verifies created files are actually delivered."),
    VendorPreset("artifact-delivery", "OUTPUT",
                 "Require real delivery evidence for promised artifacts before completion."),
    VendorPreset("redaction", "OUTPUT",
                 "Block a final answer that leaks a credential and require the no-production-attachment invariant."),
    VendorPreset("evidence-pack", "OUTPUT",
                 "Require the runtime to have issued at least one evidence record this turn (audit-mode)."),
    VendorPreset("document-authoring-coverage", "OUTPUT",
                 "Checks authored documents cover the requested scope."),
    VendorPreset("response-language", "OUTPUT",
                 "Block a final answer that violates the configured language policy."),
    # ── RESEARCH ──────────────────────────────────────────────────
    VendorPreset("parallel-research", "RESEARCH",
                 "Block a research turn that synthesized from fewer than 2 inspected sources."),
    VendorPreset("source-authority", "RESEARCH",
                 "Require declared citations to point at actually-inspected sources (anti-fab)."),
    # ── MEMORY ────────────────────────────────────────────────────
    VendorPreset("memory-continuity", "MEMORY",
                 "Maintains cross-session memory consistency."),
    # ── SECURITY ──────────────────────────────────────────────────
    # magi-agent ships these as always-on guards in its own runtime. magi-control-plane
    # has no equivalent — claude-code's terminal gate is policy-driven only. These
    # entries are surfaced for label parity; they are NOT enforced here. Authoring
    # a Policy IR that requires a security verifier in this control plane requires
    # registering a Verifier with a matching step (none ship yet).
    VendorPreset("dangerous-patterns", "SECURITY",
                 "Block dangerous shell commands. Surfaced for parity; not enforced in magi-control-plane."),
    VendorPreset("path-escape", "SECURITY",
                 "Block file access outside the workspace. Surfaced for parity; not enforced in magi-control-plane."),
    VendorPreset("secret-exposure", "SECURITY",
                 "Block commands that would expose secrets. Surfaced for parity; not enforced in magi-control-plane."),
    VendorPreset("git-safety", "SECURITY",
                 "Block destructive git operations. Surfaced for parity; not enforced in magi-control-plane."),
    VendorPreset("sealed-files", "SECURITY",
                 "Protect sealed files from modification. Surfaced for parity; not enforced in magi-control-plane."),
    VendorPreset("arity-permission", "SECURITY",
                 "Require permission for high-impact tool actions. Surfaced for parity; not enforced in magi-control-plane."),
)


def vendor_catalog() -> tuple[VendorPreset, ...]:
    """Return the vendored magi-agent preset catalog. Stable across calls."""
    return _VENDOR


__all__ = ["VendorPreset", "vendor_catalog"]
