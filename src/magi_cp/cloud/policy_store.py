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
import logging
import os
from typing import Iterable

from ..policy.ir import EvidenceReq, _CONTEXT_INJECTION_EXCLUDED_EVENTS
from ..policy.resolved import PolicyOverride


_LOG = logging.getLogger(__name__)


def _is_d59_narrowed_context_injection(item: dict) -> bool:
    """D59 follow-up (#1 P1): a ContextInjectionPolicy persisted between
    D58 (event accepted all 30 hooks) and D59 (narrowed to 26) on one of
    the four specialized-channel events now refuses to construct in
    `policy_from_dict` because `validate()` raises. Without recovery the
    whole tenant's policy store goes dark on the next cloud reboot. This
    helper detects that exact shape so `load()` can drop the row with a
    structured warning instead of aborting the loader.
    """
    pol = item.get("policy") if isinstance(item, dict) else None
    if not isinstance(pol, dict):
        return False
    if pol.get("type") != "context_injection":
        return False
    return pol.get("event") in _CONTEXT_INJECTION_EXCLUDED_EVENTS


def _evidence_req_to_dict(r: EvidenceReq) -> dict:
    """D35: kind-aware serialization. Step shape stays the legacy
    `{step, verdict}` so on-disk policy stores from before D35 still
    round-trip byte-stable.

    D82c fix: kind=regex carries an optional `field_path` scoping the
    match to a single dotted path. We only emit the key when it's
    non-empty so pre-D82c regex rows round-trip byte-identical.
    """
    if r.kind == "step":
        return {"step": r.step, "verdict": r.verdict}
    if r.kind == "regex":
        out: dict = {"kind": "regex", "pattern": r.pattern}
        if r.field_path:
            out["field_path"] = r.field_path
        return out
    if r.kind == "llm_critic":
        return {"kind": "llm_critic", "criterion": r.criterion}
    if r.kind == "shacl":
        return {"kind": "shacl", "shape_ttl": r.shape_ttl}
    raise ValueError(f"unsupported evidence kind on serialize: {r.kind!r}")


def _serialize_policy(p) -> dict:
    """Per-archetype serializer. EvidencePolicy keeps the original byte
    layout (no `type` key) for full backward compat. P2/P3 siblings
    always carry `type`."""
    from ..policy.ir import EvidencePolicy, policy_to_dict
    if isinstance(p, EvidencePolicy):
        # Keep the original byte layout so on-disk stores from before
        # P2/P3 round-trip byte-identical.
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
    return policy_to_dict(p)


def _deserialize_policy(d: dict):
    from ..policy.ir import policy_from_dict
    return policy_from_dict(d)


def _normalize(overrides: Iterable[PolicyOverride]) -> list[dict]:
    """Sort by (source-precedence, id) and serialize canonically. Same input ⇒
    byte-identical output (sha256-stable).

    P8: `enforcement` is the authoring-time resolved label stamped at PUT
    time. We OMIT the key entirely when it's None so on-disk policy stores
    from before P8 stay byte-stable through a round-trip (no spurious
    "enforcement": null sprinkled across the file). New PUTs always pass
    a non-None label so they DO write the field — the JSON shape is
    additive."""
    from ..policy.precedence import source_rank
    items = list(overrides)
    items.sort(key=lambda o: (source_rank(o.source), o.policy.id))
    out: list[dict] = []
    for o in items:
        row: dict = {"source": o.source, "enabled": o.enabled,
                      "policy": _serialize_policy(o.policy)}
        if o.enforcement is not None:
            row["enforcement"] = o.enforcement
        out.append(row)
    return out


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
                # D59 follow-up (#1 P1): a ContextInjectionPolicy persisted
                # between D58 and D59 on Elicitation / ElicitationResult /
                # WorktreeCreate / MessageDisplay now fails `validate()`.
                # Without a per-item recovery path the whole tenant's
                # policy file would refuse to load on the next cloud
                # reboot, dropping every OTHER policy in the file too.
                # Drop just the offending row with a structured log so
                # the operator can re-author it (as EvidencePolicy audit
                # or a different hook event); the rest of the store
                # keeps working.
                if isinstance(e, ValueError) and \
                        _is_d59_narrowed_context_injection(item):
                    pol = item.get("policy", {})
                    _LOG.warning(
                        "policy store item %d: dropping ContextInjectionPolicy "
                        "%r on event %r (D59 narrowed additionalContext to 26 "
                        "events; this hook uses a specialized "
                        "hookSpecificOutput channel). Re-author as "
                        "EvidencePolicy audit or pick a hook event that "
                        "supports additionalContext (PreToolUse, SessionStart, "
                        "UserPromptSubmit). Underlying error: %s",
                        i, pol.get("id", "<unknown>"), pol.get("event"), e,
                    )
                    continue
                raise ValueError(f"policy store item {i}: {e}") from e
            out.append(PolicyOverride(
                policy=policy, source=item["source"],
                enabled=bool(item.get("enabled", True)),
                # P8: legacy rows omit "enforcement" — preserve None so
                # the REST layer falls back to the (action,event) label.
                enforcement=item.get("enforcement"),
            ))
        return out

    def save(self, overrides: Iterable[PolicyOverride]) -> None:
        normalized = _normalize(overrides)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(normalized, f, ensure_ascii=False, indent=2,
                       sort_keys=True)
            f.write("\n")
