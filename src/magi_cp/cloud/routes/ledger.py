"""Ledger + metrics-summary routes: per-tenant audit ledger views, integrity,
counts, samples, aggregate, and the Overview /metrics/summary rollup."""
from __future__ import annotations

import time

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from ..constants import _KEY_PATTERN
from ..deps import require_tenant_auth
from ..policy_store import PolicyStore
from ..serialization import _iso_ts


def attach(
    app: FastAPI, engine, *,
    ledger,
    policy_store: PolicyStore,
    pack_store,
    script_store,
    policy_group_store=None,
) -> None:

    # D52c follow-up: cap the repeatable `verifier=` parameter so an
    # authenticated caller cannot amplify a request into an unbounded
    # `IN (...)` clause. 64 covers any realistic catalog size (the
    # built-ins are 5; a tenant's custom-verifier table is bounded
    # by `/verifiers/new` form input).
    _LEDGER_VERIFIER_LIMIT = 64

    def _normalize_verifier_param(values: list[str] | None) -> list[str]:
        wanted = [v for v in (values or []) if v]
        if len(wanted) > _LEDGER_VERIFIER_LIMIT:
            raise HTTPException(
                400,
                f"verifier= accepts at most {_LEDGER_VERIFIER_LIMIT} values; "
                f"got {len(wanted)}",
            )
        return wanted

    @app.get("/ledger", dependencies=[Depends(require_tenant_auth)])
    def list_ledger(request: Request, since_id: int = 0, limit: int = 100,
                     include_body: bool = False,
                     verifier: list[str] | None = Query(default=None)) -> dict:
        """Per-tenant ledger view. chain_ok validates the GLOBAL chain (so
        cross-tenant tampering is still detectable), but `entries` is scoped
        to the requesting tenant.

        D52c: `verifier=<step>` (repeatable) filters entries to those whose
        `body['step']` matches one of the supplied names. The filter is
        applied AFTER tenant scoping and BEFORE pagination so the
        `next_since_id` cursor advances over the filtered view (callers
        paginating by verifier do not have to scan thousands of unrelated
        entries to find the next page).

        D52c follow-up:
          - `since_id` + `verifier` + `limit` are pushed into SQL via
            `list_by_tenant_page` so the database does the skipping
            (was: full-tenant Python scan per request, O(N_tenant)).
          - `chain_ok` is skipped when paginating (`since_id > 0`); a
            caller fetching page 2+ is not auditing the chain, and the
            cost of re-verifying scales with the whole chain not the
            page. Dedicated `/ledger/integrity` endpoint surfaces the
            chain-ok bit on demand. Page 1 still verifies on every
            call (matches the prior shape: the dashboard polls page
            1 and expects the badge).
          - `verifier` count is capped (HTTPException 400 above the
            limit) to bound the SQL `IN (...)` clause.
        """
        limit = max(1, min(int(limit), 1000))
        tenant_id = getattr(request.state, "tenant_id", "default")
        wanted = _normalize_verifier_param(verifier)
        # Over-fetch one to compute `has_more` so the dashboard can
        # hide the Load more affordance when the filtered chain is
        # exhausted (was: the page only knew it had hit the end via
        # `len(entries) < LEDGER_PAGE_SIZE`, which is fragile when
        # the page size happens to equal the remaining count).
        page = ledger.list_by_tenant_page(
            tenant_id,
            since_id=since_id,
            limit=limit + 1,
            verifier=wanted or None,
        )
        has_more = len(page) > limit
        if has_more:
            page = page[:limit]
        # D52c follow-up: skip the global chain re-walk when the caller
        # is paginating. The chain has not changed by the time the
        # operator clicks Next; page 1 (since_id == 0) still verifies
        # so the dashboard's chain-integrity badge stays accurate.
        chain_ok = ledger.verify_chain() if since_id == 0 else True
        return {"chain_ok": chain_ok,
                "next_since_id": page[-1].id if page else since_id,
                "has_more": has_more,
                "entries": [
                    {"id": e.id, "ts": e.ts,
                     "subject": e.matter,
                     "prev": e.prev, "h": e.h,
                     **({"body": e.body, "token": e.token} if include_body else {})}
                    for e in page
                ]}

    @app.get("/ledger/integrity", dependencies=[Depends(require_tenant_auth)])
    def ledger_integrity() -> dict:
        """D52c follow-up: dedicated chain-integrity endpoint.

        The dashboard can poll this at low frequency for the
        chain-ok badge so paginated `/ledger` reads stay cheap. The
        verify_chain implementation is incremental (LedgerRepo caches
        the last verified head + id) so calls after the first one
        only re-hash the appended suffix.
        """
        return {"chain_ok": ledger.verify_chain()}

    @app.get("/ledger/count", dependencies=[Depends(require_tenant_auth)])
    def ledger_count(request: Request,
                      verifier: list[str] | None = Query(default=None),
                      since_secs: int | None = None) -> dict:
        """D52c: count of ledger entries matching the given filter(s).

        Used by the Rules → Verifiers expander to render a "Recent emissions
        (last 24h)" widget without paging through the entire chain. The
        `verifier=<step>` query is repeatable (multi-select on the
        dashboard); `since_secs=<int>` bounds the window to entries with
        `ts >= now - since_secs` (24h = 86400).

        Returns `{count: N}`. Empty case returns 0, no error for an
        unknown verifier name (the chip selector lists names that exist,
        and a typo'd query should not crash the expander).

        D52c follow-up: pushed into SQL via `LedgerRepo.count_by_tenant`
        (was O(N_tenant_rows) hydrate-and-walk per request)."""
        tenant_id = getattr(request.state, "tenant_id", "default")
        wanted = _normalize_verifier_param(verifier)
        cutoff: int | None = None
        if since_secs is not None and since_secs > 0:
            cutoff = int(time.time()) - int(since_secs)
        n = ledger.count_by_tenant(
            tenant_id, verifier=wanted or None, since_ts=cutoff,
        )
        return {"count": int(n)}

    @app.get("/ledger/samples", dependencies=[Depends(require_tenant_auth)])
    def ledger_samples(request: Request,
                        verifier: str = Query(..., min_length=1, max_length=64,
                                              pattern=_KEY_PATTERN),
                        limit: int = Query(default=5, ge=1, le=25),
                        since_secs: int = Query(default=86400, ge=0)) -> dict:
        """D53a: most-recent N redacted samples for a single verifier.

        Powers the inline "Recent emissions" sample list on the verifier
        catalog expander. Each sample is the verdict + a short redacted
        preview of the body (raw payloads never reach the dashboard;
        every preview flows through `run_redaction.redact_payload_preview`
        before the response is built).

        Defaults:
          - `limit=5` (max 25, lower-clamped to 1)
          - `since_secs=86400` (24h window; `0` disables the window)
          - `verifier` is required; unknown verifier names return
            `{samples: []}` (NOT 404; an empty filter view is a valid
            operator-visible state, mirrors the count endpoint's
            "unknown=0" contract).

        Auth: same tenant-scoped key as /ledger.
        """
        from ...policy.run_redaction import (
            DEFAULT_PREVIEW_MAX_CHARS, redact_payload_preview,
        )
        tenant_id = getattr(request.state, "tenant_id", "default")
        cutoff: int | None = None
        if since_secs > 0:
            cutoff = int(time.time()) - int(since_secs)
        rows = ledger.list_recent_by_verifier(
            tenant_id,
            verifier=verifier,
            limit=limit,
            since_ts=cutoff,
        )
        # Closed-set verdict allowlist. Single source of truth in
        # magi_cp.policy.verdicts; widening the closed set is a
        # one-line change there. Anything outside collapses to None
        # at the cloud boundary so a misbehaving producer cannot leak
        # a novel string through this surface.
        from ...policy.verdicts import LEDGER_VERDICTS
        samples: list[dict] = []
        for r in rows:
            # Intentionally drop r.subject / r.matter / r.digest /
            # r.payload_hash from the response — only id, ts, the
            # redacted body summary, and the closed-set verdict reach
            # the client. The body is the ONLY field that can carry
            # producer-supplied content; everything that flows from
            # body must pass through the redactor (`policy_id` is
            # dropped entirely today — fail-closed projection — until
            # a producer + redaction contract is defined for it).
            body = r.body if isinstance(r.body, dict) else {}
            verdict_raw = body.get("verdict")
            verdict = (
                verdict_raw
                if isinstance(verdict_raw, str)
                and verdict_raw in LEDGER_VERDICTS
                else None
            )
            # Defense in depth: every body MUST pass through the
            # redactor before it reaches the response. The preview
            # function is fail-closed (allowlist projection + linear
            # regex masking) so an unexpected future body field with a
            # secret cannot leak through this surface.
            preview = redact_payload_preview(
                body, max_chars=DEFAULT_PREVIEW_MAX_CHARS,
            )
            samples.append({
                "id": r.id,
                "ts": _iso_ts(r.ts),
                "verdict": verdict,
                "redacted_payload_preview": preview,
                # `policy_id` is intentionally NOT projected. There is
                # no producer that records it today, and no redaction
                # contract for the field is defined; fail-closed
                # projection means the frontend type stays nullable
                # but the wire surface drops it entirely. Re-introduce
                # only after the producer schema + a redact_text pass
                # are wired.
            })
        return {"samples": samples}

    @app.get("/ledger/counts", dependencies=[Depends(require_tenant_auth)])
    def ledger_counts(request: Request,
                       verifier: list[str] | None = Query(default=None),
                       since_secs: int | None = None) -> dict:
        """D52c follow-up: batched per-step count.

        Replaces the dashboard fan-out of one `/ledger/count` call per
        catalog row with a single GROUP BY query. The Rules → Verifiers
        tab calls this once per render, regardless of how many
        verifiers the catalog grows to. Returns `{counts: {step: n}}`
        (every step in the request appears in the response: missing
        keys → 0) so the dashboard can render dashes for "no
        emissions" without a follow-up call.

        Capped at `_LEDGER_VERIFIER_LIMIT` steps per request (same
        bound as `/ledger` and `/ledger/count`).
        """
        tenant_id = getattr(request.state, "tenant_id", "default")
        wanted = _normalize_verifier_param(verifier)
        cutoff: int | None = None
        if since_secs is not None and since_secs > 0:
            cutoff = int(time.time()) - int(since_secs)
        counts = ledger.counts_by_step(
            tenant_id, steps=wanted, since_ts=cutoff,
        )
        return {"counts": counts}

    # ── D76: /ledger/aggregate + /metrics/summary — Overview surface ──
    #
    # The `/overview` dashboard polls a single round-trip summary +
    # one time-bucketed aggregate every 30s. Both routes are
    # tenant-scoped (same auth as /ledger) so the polling cost is
    # bounded by the tenant chain size, not the global one.
    from ..metrics import (
        ledger_aggregate as _ledger_aggregate,
        ledger_aggregate_to_dict as _ledger_aggregate_to_dict,
        metrics_summary as _metrics_summary,
        metrics_summary_to_dict as _metrics_summary_to_dict,
    )

    @app.get("/ledger/aggregate", dependencies=[Depends(require_tenant_auth)])
    def ledger_aggregate_route(
        request: Request,
        since_secs: int | None = Query(default=None, ge=1),
        bucket_secs: int | None = Query(default=None, ge=1),
    ) -> dict:
        """D76: time-bucketed counts powering the /overview chart.

        Defaults to a 24h window in 1h buckets (24 buckets). Buckets
        carry `count` + `by_action` (block/ask/audit/inject_context/
        run_command/input_rewrite) + `by_verdict` (pass/fail/
        needs_review/not_applicable). Unknown action/verdict strings
        do NOT increment any bucket but still count toward the
        bucket's `count` total so the chart's stacked columns can be
        compared against the row totals.

        `since_secs` is hard-capped at 30 days; `bucket_secs` is
        clamped to a 60-second floor. A configuration that would
        produce more than `MAX_BUCKETS` buckets returns 400 (cheap
        guard against `?since_secs=2592000&bucket_secs=1`).
        """
        tenant_id = getattr(request.state, "tenant_id", "default")
        try:
            agg = _ledger_aggregate(
                request.app.state.engine, tenant_id,
                since_secs=since_secs, bucket_secs=bucket_secs,
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return _ledger_aggregate_to_dict(agg)

    @app.get("/metrics/summary", dependencies=[Depends(require_tenant_auth)])
    def metrics_summary_route(request: Request) -> dict:
        """D76: single-round-trip aggregator for /overview.

        Returns policy/pack/script/HITL/ledger counts in one call so
        the dashboard's headline + KPI grid can render off a single
        request instead of fanning out to six endpoints. Tenant-scoped
        for the ledger + HITL slices; policy/pack/script counts are
        single-tenant on the self-host install (which ships one
        PolicyStore/PackStore/ScriptStore per cloud) so the figures
        match the /rules + /scripts pages 1:1.
        """
        tenant_id = getattr(request.state, "tenant_id", "default")
        # Pack member lists: builtin specs + user-pack rows. We resolve
        # them inline so the metrics module doesn't take a build-time
        # dependency on the pack catalog import (which pulls the
        # policy IR; we want the metrics module to stay test-cheap).
        from ...policy.pack import all_builtin_packs
        from ...policy.pack_membership import (
            build_group_rule_index, expand_pack_member_ids,
        )
        # pack -> policy -> rule: expand policy-group members to their rule
        # ids so the count matches the rules the pack actually contributes
        # (1:1 with the /rules page), same as every other membership site.
        group_index = build_group_rule_index(policy_group_store)
        pack_member_lists: list[list[str]] = []
        # all_builtin_packs returns dicts with policy_ids; reuse the
        # catalog so we get the same ordering the /policy-packs surface
        # exposes. locale is irrelevant for the count (policy_ids is
        # locale-agnostic) so we pass "en" arbitrarily.
        for p in all_builtin_packs(locale="en", enabled_ids=set()):
            pack_member_lists.append(
                expand_pack_member_ids(p.get("policy_ids", []), group_index))
        if pack_store is not None:
            for row in pack_store.load():
                pack_member_lists.append(
                    expand_pack_member_ids(row.policy_ids, group_index))
        scripts_total = 0
        if script_store is not None:
            try:
                scripts_total = len(script_store.list())
            except Exception:
                # Defense in depth: a malformed scripts index must not
                # take the overview offline. The /scripts page will
                # surface the underlying error if the operator drills in.
                scripts_total = 0
        summary = _metrics_summary(
            request.app.state.engine, tenant_id,
            policy_overrides=policy_store.load(),
            pack_member_lists=pack_member_lists,
            scripts_total=scripts_total,
            ledger_repo=ledger,
        )
        return _metrics_summary_to_dict(summary)
