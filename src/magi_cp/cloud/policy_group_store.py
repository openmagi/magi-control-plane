"""Policy-tier store: the user-facing 'policy' that owns one or more rules.

pack -> policy -> rule. A `rule` is an IR policy (the compile unit, in
`PolicyStore`). A `policy` is the semantic unit a user authors: one intent that
may own several rules. This store persists those groupings so the dashboard and
packs manage at policy granularity while the compiler stays rule-based.

Ownership is the `rule_ids` edge (a policy points at the rules it owns); no IR
change is needed. A rule not owned by any policy is a legacy free-standing rule,
surfaced as a one-rule policy at read time by the caller.

Single JSON file, normalize() for byte-stable diffs, same pattern as PolicyStore.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass
class PolicyRecord:
    id: str
    description: str
    kind: str  # "simple" | "compound"
    draft: dict  # the authored form (compound draft or a single rule dict)
    rule_ids: list[str] = field(default_factory=list)
    source: str = "org"
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "id": self.id, "description": self.description, "kind": self.kind,
            "draft": self.draft, "rule_ids": list(self.rule_ids),
            "source": self.source, "enabled": self.enabled,
        }

    @staticmethod
    def from_dict(d: dict) -> "PolicyRecord":
        return PolicyRecord(
            id=str(d["id"]),
            description=str(d.get("description", "")),
            kind=str(d.get("kind", "simple")),
            draft=d.get("draft") if isinstance(d.get("draft"), dict) else {},
            rule_ids=[str(r) for r in d.get("rule_ids", []) if isinstance(r, (str, int))],
            source=str(d.get("source", "org")),
            enabled=bool(d.get("enabled", True)),
        )


class PolicyGroupStore:
    def __init__(self, path: str):
        self.path = path

    def load(self) -> list[PolicyRecord]:
        if not os.path.exists(self.path):
            return []
        # Fail LOUD on corruption: silently returning [] would dissolve every
        # policy grouping while the rules keep enforcing (the "my policies
        # ungrouped themselves" failure mode). Let the OSError/ValueError raise.
        raw = json.loads(open(self.path, encoding="utf-8").read())
        rows = raw.get("policies") if isinstance(raw, dict) else raw
        out: list[PolicyRecord] = []
        for item in rows if isinstance(rows, list) else []:
            if isinstance(item, dict) and "id" in item:
                try:
                    out.append(PolicyRecord.from_dict(item))
                except (KeyError, TypeError):
                    continue
        return out

    def save(self, records: list[PolicyRecord]) -> None:
        # Atomic write: temp file + os.replace, so a crash mid-write cannot
        # truncate the store (which load() now refuses to swallow).
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        body = {"policies": [r.to_dict() for r in records]}
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True))
        os.replace(tmp, self.path)

    def get(self, policy_id: str) -> PolicyRecord | None:
        for r in self.load():
            if r.id == policy_id:
                return r
        return None
