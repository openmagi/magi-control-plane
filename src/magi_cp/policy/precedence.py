"""5-tier policy source precedence.

Pattern derived (not ported) from magi-agent's 9-tier `SOURCE_PRECEDENCE`.
The 9-tier model was in-loop and included model-suggested/session-derived
sources that don't apply to an out-of-loop terminal gate. The 5-tier model
keeps only the human-authoring sources:

    platform > org > bot > user > session

- platform : magi-control-plane shipped defaults (e.g. hard safety)
- org      : organization-wide policy (set by IT/compliance)
- bot      : per-bot (Claude Code installation) policy
- user     : end-user-overridable local policy
- session  : ephemeral session-scope override

A higher-precedence source ALWAYS wins on policy-id conflict — there is no
merge semantics in v0 (kept deterministic).
"""
from __future__ import annotations
from typing import Literal


PolicySource = Literal["platform", "org", "bot", "user", "session"]
SOURCE_PRECEDENCE: tuple[PolicySource, ...] = (
    "platform", "org", "bot", "user", "session",
)


def source_rank(s: str) -> int:
    """Lower rank = higher authority. 0 is platform; 4 is session."""
    try:
        return SOURCE_PRECEDENCE.index(s)   # type: ignore[arg-type]
    except ValueError as e:
        raise ValueError(f"unknown policy source: {s!r}") from e


def more_authoritative(a: PolicySource, b: PolicySource) -> PolicySource:
    return a if source_rank(a) <= source_rank(b) else b


def resolve_by_id(candidates: list[dict]) -> dict[str, dict]:
    """Group `candidates` by `id`; for each id, keep only the highest-precedence
    entry. Stable and deterministic — same input ⇒ same output.
    """
    by_id: dict[str, dict] = {}
    for c in candidates:
        cid = c["id"]
        if cid not in by_id or source_rank(c["source"]) < source_rank(by_id[cid]["source"]):
            by_id[cid] = c
    return by_id
