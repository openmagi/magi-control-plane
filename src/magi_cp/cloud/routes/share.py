"""Run-share link routes: create/list/get/edit/revoke a redacted run view
served by an opaque token, plus the public unauthenticated GET."""
from __future__ import annotations

import os
import time

from fastapi import Depends, FastAPI, HTTPException, Request

from ..db import SharedRunRepo
from ..deps import require_tenant_auth


def attach(app: FastAPI, *, share_repo: SharedRunRepo) -> None:
    # ── run-share links ──────────────────────────────────────────────
    # The CLI (`magi-cp share`) uploads an already-redacted openmagi.runView.v1
    # view; we RE-SCRUB on ingest (defense in depth — never trust the client to
    # have redacted) and store it under an opaque token. The public GET serves
    # it without auth (the dashboard fetches it server-side; CORS stays deny-all).
    _SHARE_BASE_URL = os.environ.get(
        "MAGI_CP_SHARE_BASE_URL", "https://cloud.openmagi.ai"
    ).rstrip("/")
    # Default public run-share links to a 30-day TTL (SHARE-1). A leaked share
    # URL is otherwise valid forever. Operators who want permanent links set
    # MAGI_CP_SHARE_TTL_SECONDS=0 (explicit no-expiry opt-in).
    _SHARE_TTL_SECONDS = int(os.environ.get("MAGI_CP_SHARE_TTL_SECONDS", "2592000")) or None

    @app.post("/v1/runs/share", dependencies=[Depends(require_tenant_auth)])
    async def runs_share(request: Request) -> dict:
        from ...share.redaction import build_public_run_view

        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "body must be valid JSON") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        view = body.get("view")
        if not isinstance(view, dict):
            raise HTTPException(400, "missing 'view' object")
        if view.get("schemaVersion") != "openmagi.runView.v1":
            raise HTTPException(400, "unsupported view schemaVersion")
        # Re-scrub: the stored view is always the server's own projection.
        redacted = build_public_run_view(view)
        token = share_repo.create(
            tenant_id=request.state.tenant_id,
            view=redacted,
            ttl_seconds=_SHARE_TTL_SECONDS,
        )
        # Best-effort GC of revoked / expired rows so stored redacted views do
        # not linger forever (SHARE-1). Never fail share creation on a GC error.
        try:
            share_repo.purge_expired()
        except Exception:  # pragma: no cover - defensive
            pass
        return {"token": token, "url": f"{_SHARE_BASE_URL}/r/{token}"}

    @app.get("/share/run/{token}")
    def share_run_get(token: str) -> dict:
        from ...share.edits import apply_share_edits

        row = share_repo.get_active(token)
        if row is None:
            raise HTTPException(404, "not found")
        # Apply the owner's non-destructive edits (range / hide / redact) over
        # the stored full export before serving the public page.
        view = apply_share_edits(row.view, row.edits) if row.edits else row.view
        return {"view": view, "createdAt": row.created_at}

    @app.get("/v1/runs/share/{token_hash}", dependencies=[Depends(require_tenant_auth)])
    def runs_share_get_for_edit(token_hash: str, request: Request) -> dict:
        """Owner-only: the FULL un-edited view + current edits, for the editor."""
        row = share_repo.get_by_hash(token_hash, request.state.tenant_id)
        if row is None:
            raise HTTPException(404, "not found")
        return {"view": row.view, "edits": row.edits or {}, "createdAt": row.created_at}

    @app.patch("/v1/runs/share/{token_hash}/edits", dependencies=[Depends(require_tenant_auth)])
    async def runs_share_set_edits(token_hash: str, request: Request) -> dict:
        """Owner-only: store a normalized edits overlay (range / hidden / redactions)."""
        from ...share.edits import normalize_edits

        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "body must be valid JSON") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        edits = normalize_edits(body.get("edits"))
        ok = share_repo.set_edits(token_hash, request.state.tenant_id, edits or None)
        if not ok:
            raise HTTPException(404, "not found or revoked")
        return {"edits": edits}

    @app.get("/v1/runs/share", dependencies=[Depends(require_tenant_auth)])
    def runs_share_list(request: Request) -> dict:
        """List the caller tenant's share links (manage UI). The cleartext token
        is NOT returned (only its hash is stored); the UI shows metadata +
        revoke, keyed by tokenHash."""
        rows = share_repo.list_by_tenant(request.state.tenant_id)
        now = int(time.time())
        items = []
        for r in rows:
            summary = r.view.get("summary") if isinstance(r.view, dict) else None
            summary = summary if isinstance(summary, dict) else {}
            revoked = r.revoked_at is not None
            expired = r.expires_at is not None and r.expires_at <= now
            items.append({
                "tokenHash": r.token_hash,
                "title": summary.get("title") or summary.get("goal") or None,
                "status": summary.get("status"),
                "createdAt": r.created_at,
                "expiresAt": r.expires_at,
                "revokedAt": r.revoked_at,
                "active": not revoked and not expired,
            })
        return {"items": items}

    @app.post(
        "/v1/runs/share/{token_hash}/revoke",
        dependencies=[Depends(require_tenant_auth)],
    )
    def runs_share_revoke(token_hash: str, request: Request) -> dict:
        ok = share_repo.revoke_by_hash(token_hash, request.state.tenant_id)
        if not ok:
            raise HTTPException(404, "not found or already revoked")
        return {"revoked": True}

