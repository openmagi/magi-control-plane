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


@dataclass
class Trigger:
    host: Literal["claude-code"] = "claude-code"
    event: Literal["PreToolUse", "PostToolUse", "Stop"] = "PreToolUse"
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

    def validate(self) -> None:
        rx = re.compile(self.sentinel_re)
        if "matter" not in rx.groupindex or "doc_id" not in rx.groupindex:
            raise ValueError(
                f"policy '{self.id}': sentinel_re는 named groups (?P<matter>) (?P<doc_id>) 필요"
            )
        if self.trigger.event not in ("PreToolUse", "PostToolUse", "Stop"):
            raise ValueError(f"policy '{self.id}': trigger.event 미지원: {self.trigger.event}")
        if not self.requires:
            raise ValueError(f"policy '{self.id}': requires가 비어 있음 (=강제 의미 없음)")
        # Defense in depth: Literal[...] is not runtime-enforced by Python.
        if self.on_missing not in {"deny", "ask"}:
            raise ValueError(f"policy '{self.id}': on_missing 미지원: {self.on_missing}")
        if self.on_signature_invalid != "deny":
            raise ValueError(
                f"policy '{self.id}': on_signature_invalid는 'deny'만 허용 (v0)"
            )


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
