"""HITL (human-in-the-loop) review routes: pending-queue detail/list plus
approve/reject, which issue or withhold a signed verdict token."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException

from ..deps import require_hitl_key
from ..keys import KeyStore
from ..schemas import DecideReq
from ..serialization import _issue_token


def attach(
    app: FastAPI, *,
    hitl,
    ledger,
    ks: KeyStore,
    kid: str,
    chain_lock,
) -> None:
    @app.get("/hitl/{item_id}/detail", dependencies=[Depends(require_hitl_key)])
    def get_hitl_detail(item_id: int) -> dict:
        item = hitl.get(item_id)
        if item is None:
            raise HTTPException(404, f"hitl item {item_id} not found")
        # PR4: legacy matter/doc_id columns dropped; subject + payload_hash
        # are the only keys. (Pre-PR4 rows were backfilled by
        # `scripts/migrate_pr3_backfill.py`; the PR4 schema migration
        # refuses to drop the legacy columns until that backfill is
        # complete, so we never observe NULL subject here.)
        subj = item.subject
        phash = item.payload_hash
        # Pull ledger entries for this subject so reviewers see context (the
        # citation_verify=review entry + neighbors). Body redacted by default
        # for general /ledger; here we include because the reviewer is gated.
        ctx_entries = []
        if subj is not None:
            # Scope by the item's tenant: `subject` is a cross-tenant
            # namespace, so an unscoped read would surface another tenant's
            # ledger bodies here (TENANT-1).
            for e in ledger.list_by_subject(subj, tenant_id=item.tenant_id):
                ctx_entries.append({
                    "id": e.id, "ts": e.ts, "h": e.h, "prev": e.prev,
                    "body": e.body,
                })
        return {
            "id": item.id,
            "subject": subj, "payload_hash": phash,
            "reason": item.reason, "payload": item.payload,
            "status": item.status.value,
            "approver": item.approver, "note": item.note,
            "ts_created": item.ts_created, "ts_decided": item.ts_decided,
            "ledger_context": ctx_entries,
        }

    @app.get("/hitl", dependencies=[Depends(require_hitl_key)])
    def list_hitl() -> dict:
        # PR4: canonical fields only. See get_hitl_detail above.
        return {"items": [
            {"id": i.id,
             "subject": i.subject, "payload_hash": i.payload_hash,
             "reason": i.reason, "payload": i.payload,
             "ts_created": i.ts_created}
            for i in hitl.list_pending()
        ]}

    @app.post("/hitl/{item_id}/approve", dependencies=[Depends(require_hitl_key)])
    async def hitl_approve(item_id: int, body: DecideReq) -> dict:
        item = hitl.get(item_id)
        if item is None:
            raise HTTPException(404, f"hitl item {item_id} not found")
        try:
            hitl.approve(item_id, approver=body.approver, note=body.note)
        except ValueError as e:
            raise HTTPException(409, str(e))
        subj = item.subject
        phash = item.payload_hash
        if subj is None or phash is None:
            # Should be unreachable post-PR4: schema migration refuses to
            # run if any row has NULL subject/payload_hash (would lose data).
            raise HTTPException(500, f"hitl item {item_id} missing key fields")
        async with chain_lock:
            return _issue_token(subj, phash, "pass",
                                ledger=ledger, keystore=ks, kid=kid,
                                tenant_id=item.tenant_id,
                                extra={"hitl_id": item_id, "approver": body.approver})

    @app.post("/hitl/{item_id}/reject", dependencies=[Depends(require_hitl_key)])
    async def hitl_reject(item_id: int, body: DecideReq) -> dict:
        item = hitl.get(item_id)
        if item is None:
            raise HTTPException(404, f"hitl item {item_id} not found")
        try:
            hitl.reject(item_id, approver=body.approver, note=body.note)
        except ValueError as e:
            raise HTTPException(409, str(e))
        subj = item.subject
        phash = item.payload_hash
        async with chain_lock:
            ledger.append(subject=subj or "",
                          body={"step": "hitl_decision", "decision": "rejected",
                                "subject": subj,
                                "payload_hash": phash,
                                "hitl_id": item_id,
                                "approver": body.approver},
                          token="",
                          tenant_id=item.tenant_id)
        return {"verdict": "rejected", "token": None, "hitl_id": item_id}
