"""Compound policies: one authored intent -> several coupled IR policies.

Some governance intents a user expresses as a single rule ("require a verified
source before this tool runs") are implemented by more than one primitive policy
(an audit that records evidence + a precondition that requires it). A *compound
draft* carries the whole intent under one ``type``; :func:`expand_compound_draft`
deterministically produces the concrete IR policy dicts it stands for.

This is the single source of truth for the expansion, reused by the conversational
compiler (validation + finalize), the compound save endpoint, and any authoring
surface. Pure: no I/O, raises ValueError on an unknown/invalid compound.
"""
from __future__ import annotations

from collections.abc import Mapping

__all__ = ["COMPOUND_TYPES", "is_compound_draft", "expand_compound_draft"]

# Compound archetype `type` values and the member policy count they expand to.
COMPOUND_TYPES: dict[str, int] = {"evidence_gate": 2}


def is_compound_draft(draft: object) -> bool:
    return isinstance(draft, Mapping) and draft.get("type") in COMPOUND_TYPES


def _stem(draft: Mapping) -> str:
    raw = draft.get("id") or draft.get("id_stem") or "evidence-gate"
    return str(raw).strip() or "evidence-gate"


def _expand_evidence_gate(draft: Mapping) -> list[dict]:
    """`evidence_gate` -> [evidence_audit, evidence_precondition] joined on kind.

    Draft fields (all optional except where noted):
      kind (join key + evidence label), description, project_scope,
      audit: {event, matcher, extract, judge}
      gate:  {event, matcher, action, verdict, reason}
    """
    kind = str(draft.get("kind") or "source_credibility")
    stem = _stem(draft)
    description = str(draft.get("description") or "")
    scope = str(draft.get("project_scope") or "")
    audit = draft.get("audit") if isinstance(draft.get("audit"), Mapping) else {}
    gate = draft.get("gate") if isinstance(draft.get("gate"), Mapping) else {}

    audit_policy = {
        "type": "evidence_audit",
        "id": f"{stem}-audit",
        "description": (f"{description} (audit)" if description else "Record evidence"),
        "trigger": {
            "host": "claude-code",
            "event": str(audit.get("event") or "PostToolUse"),
            "matcher": str(audit.get("matcher") or "WebFetch|Bash"),
        },
        "kind": kind,
        "extract": str(audit.get("extract") or "url"),
        "judge": str(audit.get("judge") or "domain-credibility"),
        "project_scope": scope,
    }
    gate_policy = {
        "type": "evidence_precondition",
        "id": f"{stem}-gate",
        "description": description or "Require verified evidence before the gated tool",
        "trigger": {
            "host": "claude-code",
            "event": str(gate.get("event") or "PreToolUse"),
            "matcher": str(gate.get("matcher") or ""),
        },
        "require_kind": kind,
        "require_verdict": str(gate.get("verdict") or "pass"),
        "reason": str(gate.get("reason") or ""),
        "action": str(gate.get("action") or "block"),
        "project_scope": scope,
    }
    return [audit_policy, gate_policy]


_EXPANDERS = {"evidence_gate": _expand_evidence_gate}


def expand_compound_draft(draft: Mapping) -> list[dict]:
    """Expand a compound draft into its concrete IR policy dicts.

    Raises ValueError if ``draft`` is not a recognized compound.
    """
    if not isinstance(draft, Mapping):
        raise ValueError("compound draft must be an object")
    t = draft.get("type")
    if t not in _EXPANDERS:
        raise ValueError(f"not a compound policy type: {t!r}")
    return _EXPANDERS[t](draft)
