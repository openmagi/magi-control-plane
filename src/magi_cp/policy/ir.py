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


@dataclass
class Policy:
    id: str
    description: str
    trigger: Trigger
    sentinel_re: str
    requires: list[EvidenceReq]
    on_missing: Literal["deny", "ask"] = "deny"
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
        if not self.requires:
            raise ValueError(f"policy '{self.id}': requires가 비어 있음 (=강제 의미 없음)")
        # Defense in depth: Literal[...] is not runtime-enforced by Python.
        # v1: matrix now governs which (event, matcher, on_missing) combos are
        # legal — on_missing can be deny/ask/log/allow per event class.
        if self.on_missing not in {"deny", "ask", "log", "allow"}:
            raise ValueError(f"policy '{self.id}': on_missing 미지원: {self.on_missing}")
        if self.on_signature_invalid != "deny":
            raise ValueError(
                f"policy '{self.id}': on_signature_invalid는 'deny'만 허용 (v0)"
            )
        # v1: _LEGAL matrix — reject illegal (event, matcher_class, decision)
        # triples before they reach the compiler. on_missing doubles as the
        # decision label because that's what v0 policies express.
        from .matrix import validate_combination
        try:
            validate_combination(self.trigger.event, self.trigger.matcher,
                                  self.on_missing)
        except ValueError as e:
            raise ValueError(f"policy '{self.id}': {e}") from e


def load_policy(path: str) -> Policy:
    raw = json.loads(open(path, "r", encoding="utf-8").read())
    p = Policy(
        id=raw["id"],
        description=raw.get("description", ""),
        trigger=Trigger(**raw["trigger"]),
        sentinel_re=raw["sentinel_re"],
        requires=[EvidenceReq(**r) for r in raw["requires"]],
        on_missing=raw.get("on_missing", "deny"),
        on_signature_invalid=raw.get("on_signature_invalid", "deny"),
        gate_binary=raw.get("gate_binary", "/usr/local/bin/magi-gate.sh"),
        version=raw.get("version", "0.1"),
    )
    p.validate()
    return p
