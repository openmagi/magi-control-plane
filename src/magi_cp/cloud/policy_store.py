"""Policy override store (cloud-side, JSON file).

Pattern from magi-agent customize/store.py: persist as a single JSON file with
a normalize() pass for byte-stable serialization. v0 SQLite path is the cloud
ledger; policy storage stays as a single JSON file so it's easy to:

  - bake into a Docker image / cloud-init payload
  - diff in git
  - hash for change detection ("did the active policy set move?")

Multi-tenant comes later (SECURITY.md §multi-tenant).
"""
from __future__ import annotations
import json
import os
import re
from typing import Iterable

from ..policy.ir import EvidenceReq, Policy, Trigger, _coerce_evidence_req


def _evidence_req_to_dict(r: EvidenceReq) -> dict:
    """D35: kind-aware serialization. Step shape stays the legacy
    `{step, verdict}` so on-disk policy stores from before D35 still
    round-trip byte-stable."""
    if r.kind == "step":
        return {"step": r.step, "verdict": r.verdict}
    if r.kind == "regex":
        return {"kind": "regex", "pattern": r.pattern}
    if r.kind == "llm_critic":
        return {"kind": "llm_critic", "criterion": r.criterion}
    if r.kind == "shacl":
        return {"kind": "shacl", "shape_ttl": r.shape_ttl}
    raise ValueError(f"unsupported evidence kind on serialize: {r.kind!r}")
from ..policy.precedence import PolicySource
from ..policy.resolved import PolicyOverride


def _serialize_policy(p: Policy) -> dict:
    return {
        "id": p.id,
        "description": p.description,
        "version": p.version,
        "trigger": {"host": p.trigger.host, "event": p.trigger.event,
                    "matcher": p.trigger.matcher},
        "sentinel_re": p.sentinel_re,
        "requires": [_evidence_req_to_dict(r) for r in p.requires],
        "action": p.action,
        "on_signature_invalid": p.on_signature_invalid,
        "gate_binary": p.gate_binary,
    }


def _deserialize_policy(d: dict) -> Policy:
    from ..policy.ir import _coerce_action
    return Policy(
        id=d["id"], description=d.get("description", ""),
        version=d.get("version", "0.1"),
        trigger=Trigger(**d["trigger"]),
        sentinel_re=d["sentinel_re"],
        requires=[_coerce_evidence_req(r) for r in d["requires"]],
        action=_coerce_action(d),
        on_signature_invalid=d.get("on_signature_invalid", "deny"),
        gate_binary=d.get("gate_binary", "/usr/local/bin/magi-gate.sh"),
    )


def _normalize(overrides: Iterable[PolicyOverride]) -> list[dict]:
    """Sort by (source-precedence, id) and serialize canonically. Same input ⇒
    byte-identical output (sha256-stable)."""
    from ..policy.precedence import source_rank
    items = list(overrides)
    items.sort(key=lambda o: (source_rank(o.source), o.policy.id))
    return [
        {"source": o.source, "enabled": o.enabled,
         "policy": _serialize_policy(o.policy)}
        for o in items
    ]


class PolicyStore:
    def __init__(self, path: str):
        self.path = path

    def load(self) -> list[PolicyOverride]:
        if not os.path.exists(self.path):
            return []
        try:
            raw = json.loads(open(self.path, encoding="utf-8").read())
        except json.JSONDecodeError as e:
            raise ValueError(f"malformed policy store: {e}") from e
        out: list[PolicyOverride] = []
        for i, item in enumerate(raw):
            try:
                # _deserialize_policy → Policy(...) → __post_init__ → validate()
                # → fail-fast with item index for actionable error messages.
                policy = _deserialize_policy(item["policy"])
            except (ValueError, KeyError) as e:
                raise ValueError(f"policy store item {i}: {e}") from e
            out.append(PolicyOverride(
                policy=policy, source=item["source"],
                enabled=bool(item.get("enabled", True)),
            ))
        return out

    def save(self, overrides: Iterable[PolicyOverride]) -> None:
        normalized = _normalize(overrides)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(normalized, f, ensure_ascii=False, indent=2,
                       sort_keys=True)
            f.write("\n")
