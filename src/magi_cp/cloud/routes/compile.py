"""Policy authoring routes: NL->IR compile (+ critic review), the interactive
compile state machine, wizard handoff-context, and offline dry-run replay."""
from __future__ import annotations

import asyncio
import time

from fastapi import Depends, FastAPI, HTTPException, Request

from ..deps import require_admin_key
from ..schemas import (
    CompileReq, DryRunReq, HandoffContextReq, InteractiveCompileReq,
    ReviewPolicyReq,
)
from ..serialization import _deserialize_policy_from_api, _iso_ts
from ...verifier.protocol import VerifierRegistry


def _build_compile_context(policy_group_store, policy_store=None) -> dict:
    """Read-only snapshot of the existing policy landscape for the
    conversational compiler (context-aware compound authoring).

    Returns {"audit_kinds": {kind: [provider_policy_id, ...]}} - every
    producer that RECORDS an evidence `kind` and is actually live, so a
    new evidence-gate for the same kind can reuse it instead of authoring
    a duplicate. Two producer sources:

      1. compound `evidence_gate` policies that emit their own audit
         (`emit_audit != False`);
      2. standalone `evidence_audit` RULES (e.g. REST-authored) - without
         these, review raises a false "orphan gate" for a gate reusing a
         standalone producer (audit CV-09).

    B1 (audit IF-07): "live" is derived from GROUND TRUTH - the enabled
    state of the producer's member RULES in the policy store - NOT the
    write-once `PolicyRecord.enabled`, which a pack cascade flips without
    syncing (and which the pack-centric runtime does not even consult).
    The PATCH cascade keeps a compound's member rules in lockstep, so any
    enabled member means the producer is live. Best-effort: any store
    error yields an empty context so the compiler falls back to the
    non-context-aware path.
    """
    audit_kinds: dict[str, list[str]] = {}

    # Ground-truth enabled rule ids from the policy store.
    enabled_rule_ids: set[str] = set()
    if policy_store is not None:
        try:
            enabled_rule_ids = {
                ov.policy.id for ov in policy_store.load()
                if getattr(ov, "enabled", False)
            }
        except Exception:  # noqa: BLE001 - advisory; never fail the turn
            enabled_rule_ids = set()

    owned_rule_ids: set[str] = set()
    if policy_group_store is not None:
        try:
            records = policy_group_store.load()
        except Exception:  # noqa: BLE001
            records = []
        for r in records:
            rule_ids = list(getattr(r, "rule_ids", []) or [])
            owned_rule_ids.update(rule_ids)
            draft = getattr(r, "draft", None)
            if not isinstance(draft, dict) or draft.get("type") != "evidence_gate":
                continue
            if draft.get("emit_audit") is False:
                continue  # reuses someone else's audit; not a producer
            # Ground truth: live iff a member rule is enabled (the PATCH
            # cascade keeps a compound's members in lockstep). Falls back to
            # PolicyRecord.enabled only when the rule store was not wired.
            if policy_store is not None:
                if not any(rid in enabled_rule_ids for rid in rule_ids):
                    continue
            elif not getattr(r, "enabled", False):
                continue
            kind = str(draft.get("kind") or "source_credibility")
            audit_kinds.setdefault(kind, []).append(r.id)

    # Standalone evidence_audit rules (not owned by a compound) - a REST-
    # authored producer for the same kind (CV-09).
    if policy_store is not None:
        try:
            for ov in policy_store.load():
                pol = ov.policy
                if (getattr(pol, "type", None) == "evidence_audit"
                        and getattr(ov, "enabled", False)
                        and pol.id not in owned_rule_ids):
                    k = getattr(pol, "kind", None)
                    if isinstance(k, str) and k:
                        lst = audit_kinds.setdefault(k, [])
                        if pol.id not in lst:
                            lst.append(pol.id)
        except Exception:  # noqa: BLE001
            pass

    return {"audit_kinds": audit_kinds}


def attach(
    app: FastAPI, engine, *,
    ledger,
    verifier_registry: "VerifierRegistry | None",
    llm_compiler,
    llm_reviewer,
    policy_group_store=None,
    policy_store=None,
) -> None:
    @app.post("/policies/compile", dependencies=[Depends(require_admin_key)])
    async def policies_compile(req: "CompileReq", request: Request) -> dict:
        """Authoring gate 1+2 — NL→IR compile + critic review.

        Returns {"ir": {...}, "review": {"ok": bool, "issues": [...]}}.
        NEVER persists. Gate 3 (human approval) is the dashboard editing the
        IR if needed and calling PUT /policies/{id}.

        v2.0-W5: runs via asyncio.to_thread so the sync httpx-based providers
        don't block the FastAPI event loop during the 5–60s LLM call.

        Q97a: providers are resolved from `app.state` first so the
        /admin/llm-keys PUT route's hot-reload takes effect on the very
        next call; the closure vars stay as the construct-time default.
        """
        active_compiler = getattr(request.app.state, "llm_compiler", None) or llm_compiler
        active_reviewer = getattr(request.app.state, "llm_reviewer", None) or llm_reviewer
        if active_compiler is None or active_reviewer is None:
            raise HTTPException(
                503, "LLM providers not configured on this deployment",
            )
        from ..nl_compiler import PrecheckError, compile_with_review
        try:
            result = await asyncio.to_thread(
                compile_with_review,
                compiler=active_compiler,
                reviewer=active_reviewer,
                nl=req.nl,
                prior_turns=[t.model_dump() for t in (req.prior_turns or [])],
                verifier_registry=verifier_registry,
            )
        except PrecheckError as e:
            raise HTTPException(422, f"precheck: {e}") from e
        except ValueError as e:
            # compiler parse error — operator's prompt or model produced
            # something non-JSON. 422 because the input could be reformulated.
            raise HTTPException(422, str(e)) from e
        # D57e P1: surface descriptor lifecycle drift on the compile
        # response so the dashboard's compile preview can flag the
        # mismatch BEFORE the operator clicks Save (which would 422 at
        # PUT anyway). Annotates the existing `schema_issues` list
        # with structured drift records so the existing renderer
        # (`schema_issues: list[str | dict]`) can pick them up.
        try:
            from ...verifier.descriptors import (
                validate_policy_against_descriptors,
            )
            ir = result.get("ir") or {}
            trigger_event = ((ir.get("trigger") or {}).get("event") or "")
            if isinstance(trigger_event, str) and trigger_event:
                step_refs = [
                    r.get("step", "")
                    for r in (ir.get("requires") or [])
                    if isinstance(r, dict)
                    and r.get("kind") == "step"
                    and isinstance(r.get("step"), str)
                ]
                drift_issues = validate_policy_against_descriptors(
                    policy_id=str(ir.get("id") or "compiled-draft"),
                    trigger_event=trigger_event,
                    step_refs=step_refs,
                )
                if drift_issues:
                    existing_issues = list(result.get("schema_issues") or [])
                    for di in drift_issues:
                        existing_issues.append(
                            f"verifier {di['step']!r} does not fire on "
                            f"{di['trigger_event']!r}; allowed: "
                            f"{di['allowed_events']!r}"
                        )
                    result = dict(result)
                    result["schema_issues"] = existing_issues
        except Exception:  # pragma: no cover - defensive only
            pass
        return result

    @app.post("/policies/compile-interactive",
              dependencies=[Depends(require_admin_key)])
    async def policies_compile_interactive(
        req: "InteractiveCompileReq", request: Request,
    ) -> dict:
        """D55a — conversational policy compiler.

        Turn-by-turn variant of /policies/compile. Each call accepts the
        running history + draft + the user's most recent answers and
        returns the next conversational turn (assistant message + at
        most 2 clarifying questions + an updated draft).

        Stateless: every call reconstructs state from the request body.
        The CLIENT does not mutate the draft; only this endpoint writes
        to it (via the library module's `step_compile`).

        Same 503-on-unconfigured-provider shape as /policies/compile so
        the dashboard's existing provider_unconfigured flash mapping
        lights up without a second code path.

        Q97a: provider resolved from `app.state` first so a key change
        via /admin/llm-keys PUT takes effect on the very next call.
        """
        active_compiler = getattr(request.app.state, "llm_compiler", None) or llm_compiler
        if active_compiler is None:
            raise HTTPException(
                503, "LLM providers not configured on this deployment",
            )
        from ...policy.nl_compiler_interactive import (
            InteractiveInputError, step_compile,
        )
        from ..nl_compiler import PrecheckError
        history = [t.model_dump() for t in (req.history or [])]
        # Context-aware compound authoring: hand the compiler a read-only
        # snapshot of existing producers so a new evidence-gate can reuse
        # one instead of duplicating it. Advisory only; never blocks.
        context = _build_compile_context(policy_group_store, policy_store)
        try:
            return await asyncio.to_thread(
                step_compile,
                active_compiler,
                history=history,
                draft_so_far=req.draft_so_far,
                answers=req.answers,
                context=context,
            )
        except InteractiveInputError as e:
            raise HTTPException(422, str(e)) from e
        except PrecheckError as e:
            raise HTTPException(422, f"precheck: {e}") from e
        except ValueError as e:
            # LLM produced something that didn't parse as JSON — same
            # 422 as /policies/compile so the dashboard renders the same
            # actionable banner.
            raise HTTPException(422, str(e)) from e

    @app.post("/policies/review", dependencies=[Depends(require_admin_key)])
    async def policies_review(req: "ReviewPolicyReq", request: Request) -> dict:
        """Policy-integrity review: does this authored policy implement the
        operator's intent?

        Returns {ok, issues: [{severity, message, source}], summary}. Runs
        DETERMINISTIC structural checks always (orphan gate, non-enforcing
        action, join-key mismatch, invalid member IR) and, when a reviewer
        LLM is configured, an advisory SEMANTIC pass (intent vs rules). The
        verdict is advisory: the dashboard shows it before Save but never
        blocks. No provider is required (unlike /compile) so the
        deterministic loop works on a deployment with no LLM keys.
        """
        from ...policy.review import review_policy_draft
        active_reviewer = getattr(request.app.state, "llm_reviewer", None) or llm_reviewer
        context = _build_compile_context(policy_group_store, policy_store)
        return await asyncio.to_thread(
            review_policy_draft,
            req.draft,
            intent=req.intent,
            reviewer=active_reviewer,
            context=context,
        )

    @app.post("/policies/handoff-context",
              dependencies=[Depends(require_admin_key)])
    async def policies_handoff_context(
        req: "HandoffContextReq",
    ) -> dict:
        """D57g — handoff to conversational from any authoring screen.

        Takes a snapshot of the wizard's URL state and / or the raw
        editor's IR draft and returns the same wire shape
        `step_compile` emits. The conversational client mounts the
        response as the first assistant turn instead of the canned
        intro, so the operator picks up where they left off in chat
        form.

        OFFLINE: no LLM call. The first real conversational turn (the
        operator's reply to this seeded summary) runs through
        `step_compile` as usual.
        """
        from ...policy.handoff_context import (
            HandoffContextError, build_handoff_turn,
        )
        try:
            return await asyncio.to_thread(
                build_handoff_turn,
                wizard_state=req.wizard_state,
                draft_ir=req.draft_ir,
                origin=req.origin,
                locale_hint=req.locale,
            )
        except HandoffContextError as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/policies/dry-run", dependencies=[Depends(require_admin_key)])
    async def policies_dry_run(req: "DryRunReq", request: Request) -> dict:
        """D53b: replay a draft IR over the last 24h / 7d of ledger
        rows and report how many would have triggered the policy
        action.

        Read-only. POST is used because the IR body is non-trivial
        (would not fit in a query string), but nothing is persisted -
        no ledger append, no HITL enqueue, no policy write.

        Validation reuses `_deserialize_policy_from_api` so the same
        archetype + matrix checks that gate PUT /policies also gate
        this surface. A draft that fails to validate returns 422 with
        the validation error message - exactly what the authoring
        page already knows how to render.

        Sample payloads in the response pass through D50's
        `redact_payload_preview` (allowlist projection + linear
        masking) - raw evidence bodies never reach the dashboard.

        P1 follow-up: async + asyncio.to_thread so the threadpool
        does not pin on a 10_000-row Python replay (mirrors the
        `policies_compile` route above which already does this for
        the same long-blocking-call reason).
        """
        from ...policy.dry_run import evaluate_dry_run
        from ...policy.run_redaction import (
            DEFAULT_PREVIEW_MAX_CHARS, redact_payload_preview,
        )
        from ...policy.verdicts import LEDGER_VERDICTS
        from ..tenants import Tenant

        # Gate 1: shape check. Reuse the policies CRUD deserializer
        # so an authoring-time validation failure here mirrors the
        # one the operator would have seen on PUT. The Policy
        # dataclass's __post_init__ raises ValueError on any matrix
        # / regex / SHACL lint failure.
        try:
            policy = _deserialize_policy_from_api(req.ir)
        except (ValueError, KeyError) as e:
            raise HTTPException(422, str(e)) from e

        # Gate 2: tenancy resolution. The route is admin-key gated
        # (require_tenant_auth has NOT run), so request.state.tenant_id
        # is never set; falling back to "default" produces a
        # silently-wrong count on every multi-tenant deployment.
        # Accept an explicit `tenant_id` field on the request and
        # validate it. When the tenants table is empty (single-tenant
        # deployment) we accept the "default" synthetic; when the
        # table has rows we 422 on an omitted or unknown id.
        engine = request.app.state.engine
        from sqlalchemy import select as _select
        from sqlalchemy.orm import Session as _Session
        with _Session(engine) as _s:
            has_tenants = _s.scalars(
                _select(Tenant.id).limit(1)
            ).first() is not None
        if req.tenant_id is not None:
            with _Session(engine) as _s:
                exists = _s.scalars(
                    _select(Tenant.id).where(Tenant.id == req.tenant_id)
                ).first() is not None
            if not exists:
                raise HTTPException(
                    422, f"unknown tenant_id: {req.tenant_id!r}",
                )
            tenant_id = req.tenant_id
        elif has_tenants:
            raise HTTPException(
                422,
                "tenant_id is required on multi-tenant deployments "
                "(POST /policies/dry-run is admin-key gated and has "
                "no per-request tenant resolution)",
            )
        else:
            tenant_id = "default"

        # Gate 3: ledger window. `since` is a closed enum to keep
        # the replay's blast radius bounded (a typo cannot widen to
        # 90d). Limit is clamped by pydantic above (1..10_000).
        window_secs = {"24h": 86_400, "7d": 7 * 86_400}[req.since]
        cutoff = int(time.time()) - window_secs
        rows = await asyncio.to_thread(
            ledger.list_recent_window,
            tenant_id, limit=req.limit, since_ts=cutoff,
        )

        # Gate 4: pure replay. Push the per-row Python loop onto the
        # threadpool too - regex compile + payload-text projection
        # across 10_000 rows can run >100ms which would still wedge
        # the event loop.
        result = await asyncio.to_thread(
            evaluate_dry_run, policy, rows, sample_limit=3,
        )

        # Build the redacted sample list. Look the matched rows back
        # up by id from the already-hydrated `rows` window so we do
        # not need a second SQL round-trip. The redactor is
        # fail-closed; an unexpected future body field with a secret
        # cannot leak through this surface. The verdict allowlist is
        # the single-source-of-truth constant in
        # magi_cp.policy.verdicts; widening the closed set is a
        # one-line change there.
        rows_by_id = {r.id: r for r in rows}
        sample_matched: list[dict] = []
        for rid in result.sample_matched_ids:
            r = rows_by_id.get(rid)
            if r is None:
                continue
            body = r.body if isinstance(r.body, dict) else {}
            verdict_raw = body.get("verdict")
            verdict = (
                verdict_raw
                if isinstance(verdict_raw, str)
                and verdict_raw in LEDGER_VERDICTS
                else None
            )
            sample_matched.append({
                "id": r.id,
                "ts": _iso_ts(r.ts),
                "verdict": verdict,
                "redacted_payload_preview": redact_payload_preview(
                    body, max_chars=DEFAULT_PREVIEW_MAX_CHARS,
                ),
            })

        return {
            "total_records": result.total_records,
            "matched": result.matched,
            "indeterminate": result.indeterminate,
            "by_verdict": result.by_verdict,
            "by_action": result.by_action,
            "sample_matched": sample_matched,
            "skipped_reason": result.skipped_reason,
            "skipped_kinds": result.skipped_kinds,
            "since": req.since,
            "limit": req.limit,
            "tenant_id": tenant_id,
        }

