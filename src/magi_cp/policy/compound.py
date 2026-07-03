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
# Compound archetype `type` values. Member count is not fixed (an evidence_gate
# expands to 2 rules, or 5 with the default ledger-protection denies).
COMPOUND_TYPES: frozenset[str] = frozenset({"evidence_gate"})


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
    # Ledger-integrity: deny the agent the write vectors into the evidence
    # ledger dir, so under governance (managed-settings, no --skip-permissions)
    # the audit hook is the only writer. Without these, a Bash/Write/Edit can
    # forge `{"verdict":"pass"}` and unlock the gate. Read is not a forgery
    # vector, so it's left allowed. Opt out with `protect_ledger: false`.
    rules = [audit_policy, gate_policy]
    if draft.get("protect_ledger", True):
        for i, (tool, pat) in enumerate((
            ("Write", "Write(~/.magi-cp/session-evidence/**)"),
            ("Edit", "Edit(~/.magi-cp/session-evidence/**)"),
            ("Bash", "Bash(*session-evidence*)"),
        )):
            rules.append({
                "type": "permission",
                "id": f"{stem}-ledger-deny-{i}",
                "description": "Protect the evidence ledger from agent writes",
                "trigger": {"host": "claude-code", "event": "PreToolUse", "matcher": tool},
                "permission": "deny",
                "pattern": pat,
            })
    return rules


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
