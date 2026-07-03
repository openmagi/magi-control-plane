"""Verification routes: verifier/preset catalog listing, step-kind dispatch
(/verify/{step}), inline EvidenceReq dispatch (/verify_inline), and the
citation_verify gate that issues or withholds a signed token."""
from __future__ import annotations

import asyncio
import hashlib
import re
import time

from fastapi import Depends, FastAPI, HTTPException, Request

from ..constants import MAX_CORPUS_OVERRIDE_BYTES
from ..deps import require_tenant_auth, _resolve_tenant_id_from_request
from ..presets_catalog import vendor_catalog
from ..middleware import _bounded_regex_search
from ..schemas import VerifyDispatchReq, VerifyInlineReq, VerifyReq
from ..serialization import (
    _citations_summary, _frame_meta_for_ledger, _issue_token,
)
from ...verifier import Citation, score_review_citations, verify_document
from ...verifier.sources import DictResolver


def attach(
    app: FastAPI, engine, *,
    ledger,
    hitl,
    ks,
    kid: str,
    chain_lock,
    verifier_registry,
    custom_verifier_store,
    nli_classifier,
    llm_compiler,
) -> None:
    @app.get("/verifiers")
    @app.get("/presets")  # alias kept for the existing /presets dashboard route
    def get_verifiers(request: Request) -> dict:
        """Merge built-in VerifierRegistry + tenant-scoped custom verifiers
        + vendored magi-agent catalog (preview). Read-only.

        Sort: wired built-ins first, then custom (per-tenant), then vendor
        preview entries (no implementation behind them).

        Auth: optional. If the request carries a valid tenant key, custom
        verifiers for that tenant are merged in (via require_tenant_auth's
        side effect of setting request.state.tenant_id). Without auth we
        return the global view only.
        """
        wired: list[dict] = []
        seen_ids: set[str] = set()
        if verifier_registry is not None:
            for v in verifier_registry.all():
                pid = v.step.replace("_", "-")
                wired.append({
                    "id": pid,
                    "category": v.category,
                    "description": v.description,
                    "enforcement": v.enforcement.value,
                    "step": v.step,
                    "input_schema": getattr(v, "input_schema", None),
                    "name": getattr(v, "name", None),
                })
                seen_ids.add(pid)

        # Tenant-scoped custom verifiers. Auth on this route is optional —
        # require_tenant_auth has NOT run, so we read the api key header
        # directly and resolve the tenant ourselves; missing / invalid
        # falls through to "no custom rows" (consistent with the prior
        # pre-D52b global view).
        custom: list[dict] = []
        try:
            tenant_id = _resolve_tenant_id_from_request(request)
        except Exception:
            tenant_id = None
        if tenant_id is not None:
            for cv in custom_verifier_store.list_for_tenant(tenant_id):
                custom.append({
                    "id": cv.id,
                    "category": None,
                    "description": cv.description,
                    "enforcement": "preview",
                    "step": cv.name,
                    "input_schema": None,
                    "name": cv.name,
                    "source": "custom",
                })

        vendor = sorted(
            (
                {
                    "id": vp.id,
                    "category": vp.category,
                    "description": vp.description,
                    "enforcement": "preview",
                    "step": None,
                    "input_schema": None,
                    "name": None,
                }
                for vp in vendor_catalog()
                if vp.id not in seen_ids   # wired ID shadows vendor entry
            ),
            key=lambda p: p["id"],
        )
        return {"presets": wired + custom + vendor}

    @app.post("/verify/{step}", dependencies=[Depends(require_tenant_auth)])
    async def verify_dispatch(step: str, req: VerifyDispatchReq, request: Request) -> dict:
        # W8b: per-request metric timing.
        from ..observability import get_metric
        _t0 = time.perf_counter()
        result: dict = {"verdict": "error", "token": None}
        tid_for_metric = getattr(request.state, "tenant_id", "default")
        try:
            result = await _verify_dispatch_impl(step, req, request)
            return result
        finally:
            _vt = get_metric("verify_total")
            if _vt is not None:
                try:
                    _vt.labels(step=step, verdict=result.get("verdict", "error"),
                                tenant_id=tid_for_metric).inc()
                except Exception:
                    pass
            _vl = get_metric("verify_latency_seconds")
            if _vl is not None:
                try:
                    _vl.labels(step=step).observe(time.perf_counter() - _t0)
                except Exception:
                    pass

    async def _verify_dispatch_impl(step: str, req: VerifyDispatchReq, request: Request) -> dict:
        """Generic verifier dispatch — any registered verifier other than
        citation_verify (which keeps its specialized NLI+ledger path).

        Pass: signed token + ledger entry.
        Deny: no token, ledger entry records the deny.
        Review: signed token with hitl flag in body so the gate routes to HITL.
        """
        if verifier_registry is None:
            raise HTTPException(503, "verifier registry not configured")
        if step == "citation_verify":
            raise HTTPException(
                409,
                "use POST /citation_verify for citation_verify (specialized path)",
            )
        tenant_id = getattr(request.state, "tenant_id", "default")
        v = verifier_registry.get_by_step(step)
        if v is None:
            raise HTTPException(404, f"no verifier registered for step {step!r}")
        # PR4: subject/payload_hash are the only keys. Legacy mirror
        # fields removed from request validator (extra="forbid") and from
        # ledger bodies below.
        subj, phash = req.subject, req.payload_hash
        # D53b follow-up: frame metadata written to the ledger row body
        # so the offline dry-run replay can scope rows to the proposed
        # policy's (event, matcher) frame. Gates that haven't rolled
        # forward past the runtime-write contract simply omit these
        # fields; the dry-run will exclude such rows so total_records
        # reflects rows the replay COULD scope, not "every tenant row
        # in window."
        frame_meta = _frame_meta_for_ledger(req.hook_event, req.matcher)
        try:
            verdict = v.run(req.payload)
        except Exception as e:
            # Verifier blew up on a malformed payload → treat as deny, record.
            async with chain_lock:
                ledger.append(subject=subj,
                              body={**frame_meta,
                                    "step": step, "verdict": "deny",
                                    "subject": subj, "payload_hash": phash,
                                    "error": str(e)[:200]},
                              token="", tenant_id=tenant_id)
            return {"verdict": "deny", "token": None,
                    "reasons": [f"verifier error: {type(e).__name__}"]}
        if verdict.status == "pass":
            async with chain_lock:
                result = _issue_token(
                    subj, phash, "pass",
                    ledger=ledger, keystore=ks, kid=kid, step=step,
                    tenant_id=tenant_id,
                    ledger_extra=frame_meta or None,
                )
            result["reasons"] = list(verdict.reasons)
            return result
        if verdict.status == "review":
            async with chain_lock:
                result = _issue_token(
                    subj, phash, "review",
                    ledger=ledger, keystore=ks, kid=kid, step=step,
                    tenant_id=tenant_id,
                    ledger_extra=frame_meta or None,
                )
            result["reasons"] = list(verdict.reasons)
            return result
        # deny
        async with chain_lock:
            ledger.append(subject=subj,
                          body={**frame_meta,
                                "step": step, "verdict": "deny",
                                "subject": subj, "payload_hash": phash,
                                "reasons": list(verdict.reasons)},
                          token="", tenant_id=tenant_id)
        return {"verdict": "deny", "token": None,
                "reasons": list(verdict.reasons)}

    # ── D35: inline EvidenceReq dispatch (regex/llm_critic/shacl) ──
    # Path uses an underscore so it doesn't collide with the
    # `/verify/{step}` wildcard registered above (which would otherwise
    # capture "inline" as the step name).
    @app.post("/verify_inline", dependencies=[Depends(require_tenant_auth)])
    async def verify_inline(req: VerifyInlineReq, request: Request) -> dict:
        """Dispatch a non-step EvidenceReq evaluated in-cloud.

        regex      — pure stdlib, fully wired.
        llm_critic — uses MAGI_CP_LLM_COMPILER provider when configured;
                     returns "review" with a preview reason otherwise.
        shacl      — uses pyshacl when installed; otherwise "review"
                     preview with import-failure reason.

        All three paths append to the audit ledger on pass/deny so the
        catalog endpoint and downstream HITL queue see the same shape
        as step-kind dispatch.
        """
        tenant_id = getattr(request.state, "tenant_id", "default")
        kind = req.kind
        step_label = f"inline_{kind}"
        # Pull the text-typed slice of payload for regex / llm_critic;
        # SHACL works on the dict shape directly. Delegated to the
        # shared `payload_projection` module so /verify_inline,
        # `dry_run`, and the synthetic `test_runner` simulator all
        # project the same payload to the same string.
        from magi_cp.policy.payload_projection import (
            FIELD_MISSING,
            project_payload_for_regex,
            resolve_field_for_regex,
        )
        payload_text = project_payload_for_regex(req.payload)

        verdict_status: str = "deny"
        reasons: list[str] = []
        if kind == "regex":
            if not req.pattern:
                raise HTTPException(422, "kind=regex requires pattern")
            try:
                rx = re.compile(req.pattern)
            except re.error as e:
                raise HTTPException(422, f"pattern fails to compile: {e}")
            # D82c fix: when the caller scopes the match to a specific
            # dotted path, resolve the field BEFORE running re.search.
            # Without this, an operator who picks `tool_response.output`
            # with pattern `\bSSN\b` would match an SSN appearing in
            # `tool_input.command` / `tool_input.description` /
            # anywhere else in the payload (overmatch / fail-OPEN).
            if req.field_path:
                resolved = resolve_field_for_regex(
                    req.payload, req.field_path,
                )
                if resolved is FIELD_MISSING:
                    # Field absent on this payload → cannot match. Deny
                    # with a clear reason instead of silently scanning
                    # the whole payload.
                    scoped_text = ""
                    verdict_status = "deny"
                    reasons = [
                        f"pattern did not match: field {req.field_path!r} "
                        f"absent from payload",
                    ]
                else:
                    assert isinstance(resolved, str)
                    scoped_text = resolved
                    if await _bounded_regex_search(rx, scoped_text):
                        verdict_status = "pass"
                        reasons = [
                            f"pattern matched on {req.field_path}: "
                            f"{req.pattern[:80]}",
                        ]
                    else:
                        verdict_status = "deny"
                        reasons = [
                            f"pattern did not match on {req.field_path}: "
                            f"{req.pattern[:80]}",
                        ]
                # Persist the scoped projection so the offline dry-run
                # replay scans the SAME text the runtime scanned.
                payload_text = scoped_text
            else:
                if await _bounded_regex_search(rx, payload_text):
                    verdict_status = "pass"
                    reasons = [f"pattern matched: {req.pattern[:80]}"]
                else:
                    verdict_status = "deny"
                    reasons = [f"pattern did not match: {req.pattern[:80]}"]
        elif kind == "llm_critic":
            if not req.criterion:
                raise HTTPException(422, "kind=llm_critic requires criterion")
            # Q97a: prefer the hot-reloadable singleton on app.state so a
            # /admin/llm-keys PUT-triggered rebuild reaches this path too.
            active_compiler = getattr(request.app.state, "llm_compiler", None) or llm_compiler
            if active_compiler is None:
                verdict_status = "review"
                reasons = [
                    "llm_critic preview: MAGI_CP_LLM_COMPILER not configured — "
                    "policy authored but runtime evaluation deferred to HITL.",
                ]
            else:
                # D82c: substitute `{field.path}` markers in the criterion
                # with values lifted from the live CC stdin payload BEFORE
                # the prompt reaches the LLM. Missing paths render as
                # `(no <field_path> available)` so the prose stays
                # grammatical instead of leaking literal `{...}` braces.
                from magi_cp.policy.payload_schemas import (
                    interpolate_payload_markers,
                )
                resolved_criterion = interpolate_payload_markers(
                    req.criterion, req.payload,
                )
                # Lightweight one-call yes/no critic. The compiler-side
                # provider already handles auth + timeout; we use it for
                # judgment too.
                prompt = (
                    "You are a strict gate. Reply with exactly YES or NO on "
                    "the first line, then a one-sentence rationale.\n\n"
                    f"CRITERION: {resolved_criterion}\n\n"
                    f"PAYLOAD:\n{payload_text[:4000]}"
                )
                try:
                    raw = await asyncio.to_thread(
                        active_compiler.complete, prompt,
                        max_output_tokens=200,
                    )
                except Exception as e:
                    verdict_status = "deny"
                    reasons = [f"llm_critic provider error: {type(e).__name__}"]
                else:
                    head = (raw or "").strip().split("\n", 1)[0].strip().upper()
                    if head.startswith("YES"):
                        verdict_status = "pass"
                        reasons = [f"llm_critic YES — {raw[:200]}"]
                    else:
                        verdict_status = "deny"
                        reasons = [f"llm_critic NO — {raw[:200]}"]
        elif kind == "shacl":
            if not req.shape_ttl:
                raise HTTPException(422, "kind=shacl requires shape_ttl")
            try:
                import pyshacl
                import rdflib  # type: ignore[import-not-found]
            except ImportError:
                verdict_status = "review"
                reasons = [
                    "shacl preview: pyshacl not installed — install the [shacl] "
                    "extra to enable runtime validation.",
                ]
            else:
                try:
                    # P7 (issue #1, P0 #1): lift the CC hook payload
                    # fields the chip menu advertises into RDF triples
                    # BEFORE pyshacl runs. Without this, a shape
                    # targeting `magi:tool_input.command` finds zero
                    # focus nodes at runtime → pyshacl conforms →
                    # silent fail-open. With this lift, a chip-picked
                    # path resolves to exactly one focus node per hook
                    # firing.
                    #
                    # The /verify_inline shape of the payload differs
                    # from the raw CC stdin (callers wrap it under
                    # `tool_input` keys etc.); we accept either shape:
                    #   - direct CC payload  → lifted to triples
                    #   - {"evidence_ttl": "..."} → kept for back-compat
                    #     so existing legal-vertical shapes still work
                    from ...policy.payload_schemas import (
                        lift_payload_to_data_graph,
                    )
                    # The runtime doesn't know which (event, matcher)
                    # this verify-call came from at the /verify_inline
                    # surface — gate.py passes the payload through
                    # verbatim. We accept hints in the payload itself
                    # under reserved keys (`__event__`, `__matcher__`)
                    # so the gate can opt in; without them we lift
                    # under the most permissive (PreToolUse, *) frame.
                    ev_hint = req.payload.get("__event__") if isinstance(req.payload, dict) else None
                    mt_hint = req.payload.get("__matcher__") if isinstance(req.payload, dict) else None
                    payload_for_lift = {
                        k: v for k, v in (req.payload.items() if isinstance(req.payload, dict) else [])
                        if k not in ("__event__", "__matcher__")
                    }
                    data = lift_payload_to_data_graph(
                        payload_for_lift,
                        event=str(ev_hint) if isinstance(ev_hint, str) else "PreToolUse",
                        matcher=str(mt_hint) if isinstance(mt_hint, str) else None,
                    )
                    # Back-compat: callers carrying a legal-vertical
                    # `evidence_ttl` Turtle blob get it merged onto the
                    # same data graph so existing shapes keep working.
                    ev_ttl = req.payload.get("evidence_ttl") if isinstance(req.payload, dict) else None
                    if isinstance(ev_ttl, str):
                        data.parse(data=ev_ttl, format="turtle")
                    conforms, _, results_text = pyshacl.validate(
                        data, shacl_graph=req.shape_ttl,
                        inference="none", advanced=False,
                    )
                    # P0 #1 second half: a shape that finds zero focus
                    # nodes "conforms" per the SHACL spec — vacuous
                    # satisfaction. We re-frame that as deny so a
                    # mis-targeted shape stops failing open silently.
                    # Heuristic: pyshacl's `conforms=True` with zero
                    # focus nodes triggered by the shape graph means
                    # the shape didn't even reach the data; we
                    # confirm this by extracting target IRIs and
                    # checking that AT LEAST ONE is present in the
                    # data graph.
                    if conforms:
                        from ...policy.payload_schemas import (
                            MAGI_HOOK_NS, extract_targets,
                        )
                        targets = extract_targets(req.shape_ttl)
                        # Determine if the shape has ANY focus-node
                        # selector (sh:targetNode / sh:targetClass).
                        # sh:path is a constraint detail, not an
                        # anchor — a shape can include sh:path with
                        # no targets and that's a constraint shape
                        # invoked by something else; we don't treat
                        # paths as anchors for the vacuous check.
                        anchored = bool(targets["targetNode"] or targets["targetClass"])
                        if anchored:
                            ns = rdflib.Namespace(MAGI_HOOK_NS)
                            present = False
                            for ln in targets["targetNode"]:
                                if (ns[ln], None, None) in data or (None, None, ns[ln]) in data:
                                    present = True
                                    break
                            if not present:
                                for ln in targets["targetClass"]:
                                    if (None, rdflib.RDF.type, ns[ln]) in data:
                                        present = True
                                        break
                            if not present:
                                verdict_status = "deny"
                                reasons = [
                                    "shacl vacuous: shape anchored on a "
                                    "node/class the runtime did not "
                                    "materialize (0 focus nodes). Pick "
                                    "a field from the wizard chip menu "
                                    "or sh:targetClass magi:Hook.",
                                ]
                            else:
                                verdict_status = "pass"
                                reasons = ["shacl conforms"]
                        else:
                            verdict_status = "pass"
                            reasons = ["shacl conforms"]
                    else:
                        verdict_status = "deny"
                        reasons = [f"shacl violation: {str(results_text)[:240]}"]
                except Exception as e:
                    verdict_status = "deny"
                    reasons = [f"shacl error: {type(e).__name__}: {str(e)[:200]}"]
        else:
            raise HTTPException(422, f"unsupported kind: {kind!r}")

        # PR4: subject/payload_hash are the only keys (legacy aliases
        # rejected by the pydantic validator with extra="forbid").
        subj, phash = req.subject, req.payload_hash
        # D53b follow-up: frame metadata on the ledger row body so the
        # offline dry-run replay can scope rows to (event, matcher).
        frame_meta = _frame_meta_for_ledger(req.hook_event, req.matcher)
        # D53b follow-up (regex only): write a bounded payload snapshot
        # under a reserved key so the dry-run regex replay can scan the
        # SAME text the runtime regex saw. We only do this for kind=
        # regex because (a) llm_critic and shacl can't be replayed
        # offline anyway, and (b) for regex the runtime ledger body
        # otherwise carries only the verdict envelope - the operator's
        # `\brm -rf\b` pattern would never match `{"verdict":"deny"}`.
        # The snapshot is bounded to 4000 chars (matches the
        # llm_critic prompt slice above) and lives under a reserved
        # `__payload_snapshot__` key so the redactor's projection
        # treats it as opaque payload-data on egress.
        ledger_extra: dict = dict(frame_meta)
        if kind == "regex" and payload_text:
            ledger_extra["__payload_snapshot__"] = payload_text[:4000]
        if verdict_status in ("pass", "review"):
            async with chain_lock:
                result = _issue_token(
                    subj, phash, verdict_status,
                    ledger=ledger, keystore=ks, kid=kid, step=step_label,
                    tenant_id=tenant_id,
                    ledger_extra=ledger_extra or None,
                )
            result["reasons"] = reasons
            return result
        async with chain_lock:
            ledger.append(subject=subj,
                          body={**ledger_extra,
                                "step": step_label, "verdict": "deny",
                                "subject": subj, "payload_hash": phash,
                                "reasons": reasons},
                          token="", tenant_id=tenant_id)
        return {"verdict": "deny", "token": None, "reasons": reasons}

    @app.post("/citation_verify", dependencies=[Depends(require_tenant_auth)])
    async def citation_verify(req: VerifyReq, request: Request) -> dict:
        tenant_id = getattr(request.state, "tenant_id", "default")
        # corpus_override total size cap (defense in depth on top of body limit)
        if req.corpus_override:
            total = sum(len(k) + len(v) for k, v in req.corpus_override.items())
            if total > MAX_CORPUS_OVERRIDE_BYTES:
                raise HTTPException(413, "corpus_override too large")
        resolver = DictResolver(req.corpus_override or {})
        doc = verify_document(
            [Citation(c.quote, c.ref) for c in req.citations], resolver,
        )
        # PR4: subject + payload_hash are the canonical (only) keys.
        subj, phash = req.subject, req.payload_hash
        # payload_hash binding: if a document is supplied, payload_hash MUST
        # match its sha256. If only payload_hash is supplied (no document),
        # it is used as the binding — gate callers can opt in to content-
        # binding by passing the document.
        if req.document:
            content_hash = hashlib.sha256(req.document.encode("utf-8")).hexdigest()[:32]
            if phash != content_hash:
                raise HTTPException(
                    400,
                    "payload_hash must equal sha256(document)[:32] when "
                    "document is supplied",
                )
        if doc.verdict == "pass":
            async with chain_lock:
                return _issue_token(subj, phash, "pass",
                                     ledger=ledger, keystore=ks, kid=kid,
                                     tenant_id=tenant_id)
        if doc.verdict == "review":
            # Score `review` citations with NLI advisory so HITL reviewers see
            # entailment/contradiction signals. Pure advisory — does not change
            # the deterministic verdict.
            review_payload = _citations_summary(doc)
            if nli_classifier is not None:
                scored = score_review_citations(doc, source_resolver=resolver,
                                                  classifier=nli_classifier)
                # Splice nli_* fields into the citation summary in-place by index
                for i, s in enumerate(scored):
                    if s.nli_label is not None:
                        review_payload[i]["nli_label"] = s.nli_label
                        review_payload[i]["nli_score"] = s.nli_score
            # PR4: HitlRepo.enqueue now takes ONLY subject + payload_hash;
            # legacy matter/doc_id columns dropped in the PR4 schema
            # migration.
            item = hitl.enqueue(
                subject=subj, payload_hash=phash,
                reason="citation_review",
                payload={"citations": review_payload},
                tenant_id=tenant_id,
            )
            async with chain_lock:
                ledger.append(subject=subj,
                              body={"step": "citation_verify", "verdict": "review",
                                    "subject": subj, "payload_hash": phash,
                                    "hitl_id": item.id},
                              token="", tenant_id=tenant_id)
            return {"verdict": "review", "token": None, "hitl_id": item.id,
                    "citations": _citations_summary(doc)}
        # deny
        async with chain_lock:
            ledger.append(subject=subj,
                          body={"step": "citation_verify", "verdict": "deny",
                                "subject": subj, "payload_hash": phash},
                          token="", tenant_id=tenant_id)
        return {"verdict": "deny", "token": None,
                "citations": _citations_summary(doc)}

