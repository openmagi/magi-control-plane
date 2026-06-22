"""Policy IR — declarative spec of *what* the gate enforces.

Compiler in `compiler.py` turns IR → CC managed-settings.json. LLM never sees
runtime. Authoring tools (NL assist / pack picker / structured builder) only
*produce* IR with human review.
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from typing import Literal


_POLICY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,127}$")
_RESERVED_SUFFIXES = ("/compiled", "/enabled")


def _validate_id(s: str) -> None:
    """Cloud-canonical policy id check. Mirrors web/lib/policy-id.ts but is
    the source of truth — the dashboard's check is a UX nicety."""
    if not isinstance(s, str) or not s:
        raise ValueError(f"policy id required (got {s!r})")
    if not _POLICY_ID_RE.match(s):
        raise ValueError(f"policy id must match [A-Za-z0-9][A-Za-z0-9._\\-/]{{0,127}}; got {s!r}")
    if ".." in s:
        raise ValueError(f"policy id must not contain '..': {s!r}")
    for suf in _RESERVED_SUFFIXES:
        if s.endswith(suf):
            raise ValueError(f"policy id must not end with {suf!r}: {s!r}")


EventLiteral = Literal[
    "PreToolUse", "PostToolUse",
    "Stop", "SubagentStop",
    "UserPromptSubmit",
    "PreCompact",
    "SessionStart", "SessionEnd",
]
_SUPPORTED_EVENTS: frozenset[str] = frozenset({
    "PreToolUse", "PostToolUse",
    "Stop", "SubagentStop",
    "UserPromptSubmit",
    "PreCompact",
    "SessionStart", "SessionEnd",
})


@dataclass
class Trigger:
    host: Literal["claude-code"] = "claude-code"
    event: EventLiteral = "PreToolUse"
    matcher: str = "Bash"


@dataclass
class EvidenceReq:
    step: str
    verdict: str = "pass"


# D31: action archetypes. Replaces the prior `on_missing` field which
# conflated "what happens when the verifier fails" with "what the policy
# is fundamentally trying to do." Action is now the primary intent.
#
#   block — when the verifier doesn't all-pass, prevent the host action
#           (tool runs / prompt sends / compaction starts). The strongest
#           pre-event gate.
#   ask   — when the verifier doesn't all-pass, interrupt for human
#           approval (HITL). Used for legal-significant filings, etc.
#   audit — record the verdict to the evidence ledger; never blocks.
#           Combined with `requires=[]` this expresses the "emit signal"
#           archetype (unconditional ledger marker every time the trigger
#           fires).
#
# Reserved for a follow-up cycle (requires verifier-protocol mutation
# support before it can be wired through the runtime gate):
#   strip — intercept tool output and redact / transform it before the
#           agent sees it. PostToolUse-only.
ActionLiteral = Literal["block", "ask", "audit"]


# Legacy → archetype migration. Older JSON fixtures + persisted policies
# still carry the on_missing wording; deserialization accepts the key
# and folds it into `action` so we don't strand existing rows. The
# allow/log distinction collapses to `audit` — at runtime both meant
# "verifier ran, log the verdict, don't gate," so they were
# operationally interchangeable.
_LEGACY_ON_MISSING_TO_ACTION = {
    "deny":  "block",
    "ask":   "ask",
    "log":   "audit",
    "allow": "audit",
}


@dataclass
class Policy:
    id: str
    description: str
    trigger: Trigger
    sentinel_re: str
    requires: list[EvidenceReq]
    action: ActionLiteral = "block"
    on_signature_invalid: Literal["deny"] = "deny"
    gate_binary: str = "/usr/local/bin/magi-gate.sh"
    version: str = "0.1"

    def __post_init__(self) -> None:
        # Fail-fast on construction so REST inputs / on-disk policies can't
        # quietly carry illegal IR past the surface that accepts them.
        self.validate()

    def validate(self) -> None:
        # v1: id format must match the same shape the JS dashboard enforces.
        # The cloud is the *canonical* boundary — a direct admin-key holder
        # bypasses the JS layer, so this check is the real gate.
        _validate_id(self.id)
        rx = re.compile(self.sentinel_re)
        if "matter" not in rx.groupindex or "doc_id" not in rx.groupindex:
            raise ValueError(
                f"policy '{self.id}': sentinel_re는 named groups (?P<matter>) (?P<doc_id>) 필요"
            )
        if self.trigger.event not in _SUPPORTED_EVENTS:
            raise ValueError(f"policy '{self.id}': trigger.event 미지원: {self.trigger.event}")
        # D31: requires CAN be empty — that's the unconditional ("emit
        # signal") archetype. The matrix decides whether the combination
        # makes sense for the chosen action; this validator just gates
        # the shape.
        if self.action not in ("block", "ask", "audit"):
            raise ValueError(f"policy '{self.id}': action 미지원: {self.action}")
        if self.on_signature_invalid != "deny":
            raise ValueError(
                f"policy '{self.id}': on_signature_invalid는 'deny'만 허용 (v0)"
            )
        from .matrix import validate_combination
        try:
            validate_combination(self.trigger.event, self.trigger.matcher,
                                  self.action)
        except ValueError as e:
            raise ValueError(f"policy '{self.id}': {e}") from e


def _coerce_action(raw: dict) -> ActionLiteral:
    """Accept either the new `action` key or the legacy `on_missing`.
    When both are present, `action` wins."""
    if "action" in raw:
        return raw["action"]
    if "on_missing" in raw:
        legacy = raw["on_missing"]
        mapped = _LEGACY_ON_MISSING_TO_ACTION.get(legacy)
        if mapped is None:
            raise ValueError(f"unknown legacy on_missing value: {legacy!r}")
        return mapped  # type: ignore[return-value]
    return "block"


def load_policy(path: str) -> Policy:
    raw = json.loads(open(path, "r", encoding="utf-8").read())
    p = Policy(
        id=raw["id"],
        description=raw.get("description", ""),
        trigger=Trigger(**raw["trigger"]),
        sentinel_re=raw["sentinel_re"],
        requires=[EvidenceReq(**r) for r in raw["requires"]],
        action=_coerce_action(raw),
        on_signature_invalid=raw.get("on_signature_invalid", "deny"),
        gate_binary=raw.get("gate_binary", "/usr/local/bin/magi-gate.sh"),
        version=raw.get("version", "0.1"),
    )
    p.validate()
    return p
