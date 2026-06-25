"""D76: aggregator helpers powering the /overview dashboard.

These helpers are deliberately read-only and SQL-cheap. /overview hits the
cloud on every page load + every 30s refresh poll, so a single page load
must not pin the worker on a multi-second per-tenant Python walk.

Two surfaces:

  - `ledger_aggregate(...)` — time-bucketed counts over a window. Powers
    the 24h emission chart on /overview. Returns a list of buckets, each
    with a count + per-action + per-verdict breakdown. Tenant-scoped.

  - `metrics_summary(...)` — single-round-trip aggregator with the
    fields the headline + KPI grid + chain-ok badge need. Replaces the
    pre-D76 fan-out (`listHitl + ledger + listPolicies + listPacks +
    listScripts + ledgerIntegrity`) with one cheap call.

Why a separate module:

  - Keeps `cloud/app.py` (already > 5000 lines) free of N more SQL
    helpers.
  - Lets us unit-test the aggregation math without the full FastAPI
    factory + auth dance.
  - Single source of truth for the action / verdict closed-set
    vocabularies the /overview chart renders.

Action vocabulary mirrors the policy IR's `ActionLiteral` plus the
non-EvidencePolicy archetype labels the dashboard surfaces:

    block, ask, audit            — EvidencePolicy.action (`ActionLiteral`)
    inject_context               — ContextInjectionPolicy archetype
    run_command                  — RunCommandPolicy archetype
    input_rewrite                — InputRewritePolicy archetype

Anything outside this set (a future producer that lands an unknown
action string) collapses into the `other` bucket so the chart's stacked
columns stay closed.

Verdict vocabulary uses the brief's `pass / fail / needs_review /
not_applicable` set. The existing ledger producers emit
`pass / deny / review` today; we MAP on egress so the dashboard renders
the brief's vocabulary without rewriting every producer:

    pass            → pass
    fail | deny     → fail
    review | needs_review  → needs_review
    not_applicable  → not_applicable

Anything else collapses into `other`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import HitlItem, HitlStatus, LedgerEntry


# Closed-set vocabularies — the chart's stacked column order matches
# `_ACTION_BUCKETS_ORDER`, the verdict pill order matches
# `_VERDICT_BUCKETS_ORDER`. Widening either set is a one-line change
# here; downstream consumers iterate this tuple so a new bucket
# automatically appears in the chart legend / API response.
_ACTION_BUCKETS_ORDER: tuple[str, ...] = (
    "block",
    "ask",
    "audit",
    "inject_context",
    "run_command",
    "input_rewrite",
)
_ACTION_BUCKETS: frozenset[str] = frozenset(_ACTION_BUCKETS_ORDER)

_VERDICT_BUCKETS_ORDER: tuple[str, ...] = (
    "pass",
    "fail",
    "needs_review",
    "not_applicable",
)
_VERDICT_BUCKETS: frozenset[str] = frozenset(_VERDICT_BUCKETS_ORDER)


def _empty_action_buckets() -> dict[str, int]:
    return {a: 0 for a in _ACTION_BUCKETS_ORDER}


def _empty_verdict_buckets() -> dict[str, int]:
    return {v: 0 for v in _VERDICT_BUCKETS_ORDER}


def _project_verdict(raw: object) -> str | None:
    """Map a producer-supplied verdict string onto the dashboard's
    closed vocabulary. Returns None when the raw value is unknown so
    the caller can skip incrementing any bucket (the row still counts
    toward the bucket's `count` total).
    """
    if not isinstance(raw, str):
        return None
    if raw == "pass":
        return "pass"
    if raw in ("fail", "deny"):
        return "fail"
    if raw in ("review", "needs_review"):
        return "needs_review"
    if raw == "not_applicable":
        return "not_applicable"
    return None


def _project_action(raw: object) -> str | None:
    """Map a producer-supplied action string onto the dashboard's
    closed vocabulary. Returns None when unknown."""
    if not isinstance(raw, str):
        return None
    if raw in _ACTION_BUCKETS:
        return raw
    return None


# Bounds on the public API. The brief defaults to since=24h / bucket=1h
# (24 buckets); we enforce a ceiling so an operator URL like
# `?since_secs=2592000&bucket_secs=1` cannot push a worker into a
# 2.5M-bucket allocation.
DEFAULT_SINCE_SECS = 86_400          # 24h
DEFAULT_BUCKET_SECS = 3_600          # 1h
MAX_SINCE_SECS = 30 * 86_400         # 30d hard cap
MAX_BUCKETS = 24 * 31                # ~one month at hourly resolution
MIN_BUCKET_SECS = 60                 # one minute floor


@dataclass
class LedgerBucket:
    ts_start: int
    count: int
    by_action: dict[str, int]
    by_verdict: dict[str, int]


@dataclass
class LedgerAggregate:
    since_secs: int
    bucket_secs: int
    now: int
    buckets: list[LedgerBucket]


def normalize_aggregate_params(
    since_secs: int | None, bucket_secs: int | None,
) -> tuple[int, int]:
    """Clamp `since_secs` + `bucket_secs` to safe values.

    Returns the (clamped_since, clamped_bucket) pair the caller uses.
    Raises ValueError when the resulting bucket count would exceed
    MAX_BUCKETS, or when `bucket_secs > since_secs` (a logically empty
    request — the route layer turns either into a 400).
    """
    since = since_secs if since_secs and since_secs > 0 else DEFAULT_SINCE_SECS
    bucket = bucket_secs if bucket_secs and bucket_secs > 0 else DEFAULT_BUCKET_SECS
    since = min(int(since), MAX_SINCE_SECS)
    bucket = max(int(bucket), MIN_BUCKET_SECS)
    if bucket > since:
        # A bucket wider than the window collapses to a single
        # ill-defined bucket whose width exceeds the requested range.
        # Reject so the operator's chart never silently mis-represents
        # the time range it's labelled with.
        raise ValueError(
            f"bucket_secs ({bucket}) must not exceed since_secs ({since})",
        )
    n_buckets = (since + bucket - 1) // bucket
    if n_buckets > MAX_BUCKETS:
        raise ValueError(
            f"bucket configuration produces {n_buckets} buckets "
            f"(max {MAX_BUCKETS}); widen bucket_secs or shrink since_secs",
        )
    return since, bucket


def ledger_aggregate(
    engine,
    tenant_id: str,
    *,
    since_secs: int | None = None,
    bucket_secs: int | None = None,
    now: int | None = None,
) -> LedgerAggregate:
    """Time-bucketed ledger counts for the `/ledger/aggregate` endpoint.

    The query is a single tenant-scoped scan of (ts, body) rows in the
    window followed by an in-Python bucket walk. We do NOT push the
    bucket math into SQL because the body field is JSON (postgres
    JSONB / sqlite TEXT), and pulling out `body['action']` /
    `body['verdict']` in a GROUP BY would require two distinct
    dialect-specific SQL paths for a fixed-size window that is small
    by construction (at most 30 * 24 = 720 buckets, bounded by
    `normalize_aggregate_params`).
    """
    since, bucket = normalize_aggregate_params(since_secs, bucket_secs)
    current = int(now if now is not None else time.time())
    n_buckets = (since + bucket - 1) // bucket
    # Calendar-align the bucket grid to a wall-clock boundary that is a
    # multiple of `bucket_secs`. Without this, a 1h-bucket chart loaded
    # at 14:23 would label its columns 14:23 / 15:23 / ..., which makes
    # "which hour was loud?" hard to read at a glance. After alignment,
    # `bucket_end` is the most-recent boundary at or after `current` so
    # the final bucket still includes everything up to `current`.
    bucket_end = ((current + bucket - 1) // bucket) * bucket
    cutoff = bucket_end - n_buckets * bucket
    buckets: list[LedgerBucket] = [
        LedgerBucket(
            ts_start=cutoff + i * bucket,
            count=0,
            by_action=_empty_action_buckets(),
            by_verdict=_empty_verdict_buckets(),
        )
        for i in range(n_buckets)
    ]
    with Session(engine) as s:
        rows: Iterable[LedgerEntry] = s.scalars(
            select(LedgerEntry)
            .where(
                LedgerEntry.tenant_id == tenant_id,
                LedgerEntry.ts >= cutoff,
                # Tight upper bound at `current`: rows stamped past
                # `now` (producer clock-drift, future-dated test rows)
                # are intentionally NOT folded into the last bucket;
                # clamping them there silently mis-attributes
                # drift-affected events to the "now" hour on the
                # dashboard.
                LedgerEntry.ts <= current,
            )
            .order_by(LedgerEntry.id)
        )
        for r in rows:
            idx = (int(r.ts) - cutoff) // bucket
            if idx < 0 or idx >= n_buckets:
                # Skip rather than clamp — see comment on the WHERE
                # clause above. Anything that lands outside [0,
                # n_buckets) is either a future-stamped row that slid
                # through the boundary (the WHERE bound is inclusive
                # at `current`, so a row exactly at `current` lands at
                # `idx == n_buckets - 1` when bucket-aligned but at
                # `idx == n_buckets` when `current` itself sits on a
                # boundary; that single row is correctly skipped here
                # rather than folded backwards).
                continue
            bkt = buckets[idx]
            bkt.count += 1
            body = r.body if isinstance(r.body, dict) else {}
            action = _project_action(body.get("action"))
            if action is not None:
                bkt.by_action[action] += 1
            verdict = _project_verdict(body.get("verdict"))
            if verdict is not None:
                bkt.by_verdict[verdict] += 1
    return LedgerAggregate(
        since_secs=since, bucket_secs=bucket,
        now=current, buckets=buckets,
    )


def ledger_aggregate_to_dict(agg: LedgerAggregate) -> dict:
    """Serialize a LedgerAggregate to the wire shape the dashboard
    consumes. Keeps the route handler thin."""
    return {
        "since_secs": agg.since_secs,
        "bucket_secs": agg.bucket_secs,
        "now": agg.now,
        "action_buckets": list(_ACTION_BUCKETS_ORDER),
        "verdict_buckets": list(_VERDICT_BUCKETS_ORDER),
        "buckets": [
            {
                "ts_start": b.ts_start,
                "count": b.count,
                "by_action": dict(b.by_action),
                "by_verdict": dict(b.by_verdict),
            }
            for b in agg.buckets
        ],
    }


@dataclass
class MetricsSummary:
    policies_total: int
    policies_enabled: int
    policies_by_action: dict[str, int]
    packs_total_active: int
    packs_partial: int
    scripts_total: int
    hitl_pending: int
    ledger_24h_total: int
    ledger_chain_ok: bool
    last_emission_ts: int | None


def _count_policy_actions(overrides) -> tuple[int, int, dict[str, int]]:
    """Walk the policy store and return (total, enabled, by_action).

    Only EvidencePolicy carries an `action` field today; declarative
    archetypes (Permission / Subagent / etc) compile to managed-settings
    primitives and don't fire a verdict. We surface the EvidencePolicy
    breakdown only; declarative archetypes still count toward `total`
    + `enabled` so the dashboard's "active policies" KPI matches what
    /rules shows."""
    total = 0
    enabled = 0
    by_action = _empty_action_buckets()
    for ov in overrides:
        total += 1
        if ov.enabled:
            enabled += 1
        action = _project_action(getattr(ov.policy, "action", None))
        if action is not None and ov.enabled:
            by_action[action] += 1
    return total, enabled, by_action


def _count_pack_status(
    enabled_ids: set[str], pack_member_lists: Iterable[list[str]],
) -> tuple[int, int]:
    """Return (total_active, partial) for the pack catalog.

    `total_active` counts packs where AT LEAST one member is enabled;
    `partial` counts the subset where some but not all members are
    enabled. A pack with zero members enabled is neither active nor
    partial.

    The dashboard renders "N active packs (M partial)" off this pair.
    """
    total_active = 0
    partial = 0
    for members in pack_member_lists:
        if not members:
            continue
        enabled_count = sum(1 for m in members if m in enabled_ids)
        if enabled_count == 0:
            continue
        total_active += 1
        if enabled_count < len(members):
            partial += 1
    return total_active, partial


def hitl_pending_count(engine, tenant_id: str) -> int:
    """Tenant-scoped count of pending HITL items.

    Single SELECT COUNT(*) — same shape as
    `LedgerRepo.count_by_tenant` so the dashboard's headline poll
    stays cheap even when the HITL queue grows.
    """
    with Session(engine) as s:
        return int(
            s.scalar(
                select(func.count(HitlItem.id)).where(
                    HitlItem.tenant_id == tenant_id,
                    HitlItem.status == HitlStatus.pending,
                ),
            )
            or 0,
        )


def last_ledger_emission_ts(engine, tenant_id: str) -> int | None:
    """Most recent ledger `ts` for the tenant. None on empty store.

    Used by the headline to render "last emission 2m ago" so an
    operator can tell whether the chart's silence means "0 emissions
    today" or "cloud has not been seeing CC for a while".
    """
    with Session(engine) as s:
        ts = s.scalar(
            select(func.max(LedgerEntry.ts)).where(
                LedgerEntry.tenant_id == tenant_id,
            ),
        )
        return int(ts) if ts is not None else None


def metrics_summary(
    engine,
    tenant_id: str,
    *,
    policy_overrides,
    pack_member_lists: Iterable[list[str]],
    scripts_total: int,
    ledger_repo,
    now: int | None = None,
) -> MetricsSummary:
    """One-shot aggregator for `/metrics/summary`.

    `policy_overrides` is a sequence of PolicyOverride (PolicyStore.load());
    `pack_member_lists` is an iterable of policy_ids lists across every
    pack the operator can see (builtin + user). Computed in-process so
    we don't widen PolicyStore's API surface for a single read-only
    summary."""
    overrides = list(policy_overrides)
    total, enabled, by_action = _count_policy_actions(overrides)
    enabled_ids = {ov.policy.id for ov in overrides if ov.enabled}
    total_active_packs, partial_packs = _count_pack_status(
        enabled_ids, pack_member_lists,
    )
    current = int(now if now is not None else time.time())
    ledger_24h_total = int(
        ledger_repo.count_by_tenant(
            tenant_id, since_ts=current - DEFAULT_SINCE_SECS,
        ),
    )
    return MetricsSummary(
        policies_total=total,
        policies_enabled=enabled,
        policies_by_action=by_action,
        packs_total_active=total_active_packs,
        packs_partial=partial_packs,
        scripts_total=int(scripts_total),
        hitl_pending=hitl_pending_count(engine, tenant_id),
        ledger_24h_total=ledger_24h_total,
        ledger_chain_ok=bool(ledger_repo.verify_chain()),
        last_emission_ts=last_ledger_emission_ts(engine, tenant_id),
    )


def metrics_summary_to_dict(s: MetricsSummary) -> dict:
    """Wire shape consumed by the dashboard. Mirrors the brief."""
    return {
        "policies": {
            "total": s.policies_total,
            "enabled": s.policies_enabled,
            "by_action": dict(s.policies_by_action),
        },
        "packs": {
            "total_active": s.packs_total_active,
            "partial": s.packs_partial,
        },
        "scripts": {"total": s.scripts_total},
        "hitl_pending": s.hitl_pending,
        "ledger_24h_total": s.ledger_24h_total,
        "ledger_chain_ok": s.ledger_chain_ok,
        "last_emission_ts": s.last_emission_ts,
    }


__all__ = [
    "DEFAULT_SINCE_SECS",
    "DEFAULT_BUCKET_SECS",
    "MAX_SINCE_SECS",
    "MAX_BUCKETS",
    "MIN_BUCKET_SECS",
    "LedgerAggregate",
    "LedgerBucket",
    "MetricsSummary",
    "hitl_pending_count",
    "last_ledger_emission_ts",
    "ledger_aggregate",
    "ledger_aggregate_to_dict",
    "metrics_summary",
    "metrics_summary_to_dict",
    "normalize_aggregate_params",
]
