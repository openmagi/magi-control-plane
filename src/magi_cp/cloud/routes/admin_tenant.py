"""HMAC-signed admin routes for tenant + API-key lifecycle (/admin/tenants*)."""
from __future__ import annotations

import os
import time

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field


def attach(app: FastAPI, engine) -> None:
    """HMAC-authenticated admin routes for tenant/key lifecycle.

    Called by the billing Stripe webhook (on subscription start/cancel/etc)
    and by the operator dashboard's "create API key" button (server action to
    HMAC POST). Auth is HMAC-SHA256 over `method\\npath\\ntimestamp\\nbody`,
    presented as `x-magi-signature` + `x-magi-timestamp`. Caller signs with the
    shared `MAGI_CP_ADMIN_HMAC_SECRET` env var.

    No bearer token: webhooks fire from many IPs, HMAC is the safer surface.
    The signature binds method + path (so a capture cannot be replayed on a
    different admin route) + a timestamp within a 300s window (so a capture
    expires). The reference client signing string lives in the billing repo's
    docs/clawy-integration.md and must match `require_hmac` below.
    """
    from ..tenants import ApiKeyRepo, TenantRepo

    async def require_hmac(request: Request) -> bytes:
        import hmac as _hmac
        import hashlib as _hashlib
        secret = os.environ.get("MAGI_CP_ADMIN_HMAC_SECRET")
        if not secret:
            raise HTTPException(503, "admin hmac not configured")
        body = await request.body()
        presented = request.headers.get("x-magi-signature") or ""
        ts_raw = request.headers.get("x-magi-timestamp") or ""
        # Bind the signature to method + path + timestamp + body. Body-only
        # signing let a captured empty-body signature be replayed across the
        # revoke / reactivate / issue-key routes (they all sign the constant
        # HMAC(secret, b"")), and had no freshness. The timestamp window makes
        # a captured signature expire; the method + path stop cross-route
        # reuse. See docs/clawy-integration.md for the exact signing string.
        try:
            ts = int(ts_raw)
        except ValueError:
            raise HTTPException(401, "missing or invalid x-magi-timestamp")
        if abs(int(time.time()) - ts) > 300:
            raise HTTPException(401, "admin signature timestamp out of window")
        signing = (
            request.method.encode("utf-8") + b"\n"
            + request.url.path.encode("utf-8") + b"\n"
            + ts_raw.encode("utf-8") + b"\n"
            + body
        )
        expected = _hmac.new(
            secret.encode("utf-8"), signing, _hashlib.sha256,
        ).hexdigest()
        if not _hmac.compare_digest(presented, expected):
            raise HTTPException(401, "invalid admin signature")
        return body

    class _CreateTenantIn(BaseModel):
        tenant_id: str = Field(..., min_length=1, max_length=64,
                                pattern=r"^[A-Za-z0-9_\-:]+$")
        plan: str = Field(default="free", max_length=32)
        expires_at: int | None = None

    class _SuspendIn(BaseModel):
        reason: str = Field(..., min_length=1, max_length=128)

    @app.post("/admin/tenants")
    async def admin_create_tenant(request: Request) -> dict:
        await require_hmac(request)
        # Parse body after HMAC verification — guards against any
        # parsing-side timing channel.
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(422, "invalid JSON body")
        try:
            payload = _CreateTenantIn(**data)
        except Exception as e:
            raise HTTPException(422, f"invalid payload: {e}")
        repo = TenantRepo(engine)
        # Idempotent: if tenant exists, return current record. The webhook
        # caller (clawy) might retry on transient failures.
        existing = repo.get(payload.tenant_id)
        if existing is not None:
            return {"id": existing.id, "status": existing.status,
                    "plan": existing.plan, "expires_at": existing.expires_at}
        t = repo.create(
            tenant_id=payload.tenant_id, plan=payload.plan,
            expires_at=payload.expires_at,
        )
        return {"id": t.id, "status": t.status, "plan": t.plan,
                "expires_at": t.expires_at}

    @app.post("/admin/tenants/{tenant_id}/suspend")
    async def admin_suspend_tenant(tenant_id: str, request: Request) -> dict:
        await require_hmac(request)
        try:
            data = await request.json()
        except Exception:
            data = {}
        try:
            payload = _SuspendIn(**data)
        except Exception:
            raise HTTPException(422, "reason is required")
        repo = TenantRepo(engine)
        try:
            repo.suspend(tenant_id, reason=payload.reason)
        except KeyError:
            raise HTTPException(404, f"tenant {tenant_id!r} not found")
        t = repo.get(tenant_id)
        return {"id": t.id, "status": t.status}

    @app.post("/admin/tenants/{tenant_id}/reactivate")
    async def admin_reactivate_tenant(tenant_id: str, request: Request) -> dict:
        await require_hmac(request)
        repo = TenantRepo(engine)
        try:
            repo.reactivate(tenant_id)
        except KeyError:
            raise HTTPException(404, f"tenant {tenant_id!r} not found")
        t = repo.get(tenant_id)
        return {"id": t.id, "status": t.status}

    @app.post("/admin/tenants/{tenant_id}/keys")
    async def admin_issue_key(tenant_id: str, request: Request) -> dict:
        await require_hmac(request)
        tenant_repo = TenantRepo(engine)
        if tenant_repo.get(tenant_id) is None:
            raise HTTPException(404, f"tenant {tenant_id!r} not found")
        issued = ApiKeyRepo(engine).issue(tenant_id=tenant_id)
        # Cleartext returned ONCE — caller (clawy dashboard) shows once.
        return {"id": issued.id, "tenant_id": issued.tenant_id,
                "api_key": issued.cleartext, "prefix": issued.prefix}

    @app.post("/admin/tenants/{tenant_id}/keys/{key_id}/revoke")
    async def admin_revoke_key(tenant_id: str, key_id: int,
                                request: Request) -> dict:
        await require_hmac(request)
        # Scope the revoke to the tenant named in the path so a valid admin
        # signature cannot revoke another tenant's key by guessing its
        # (sequential) id. AUTH-2.
        if not ApiKeyRepo(engine).revoke_for_tenant(key_id, tenant_id):
            raise HTTPException(
                404, f"key {key_id} not found for tenant {tenant_id!r}"
            )
        return {"id": key_id, "revoked": True}
