"""Core routes: health check, active signing pubkey, and tenant self-identity."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request

from ..deps import require_tenant_auth
from ..keys import KeyStore


def attach(app: FastAPI, engine, *, ks: KeyStore) -> None:
    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/pubkey")
    def get_pubkey() -> dict:
        """v2.0-W7b: multi-key aware. `kid` and `pubkey_pem` describe the
        ACTIVE signing key (back-compat with single-key clients). `keys` is
        a {kid: pubkey_pem} map of every key the cloud will verify against,
        so clients holding a token signed by a prior (rotated-out) key can
        still verify until that key is revoked."""
        return {
            "kid": ks.active_kid(),
            "pubkey_pem": ks.public_pem(),
            "keys": ks.public_pem_map(),
        }

    # ── v2.2: tenant identity (alpha-signup retired; tenants now provisioned
    #         by Clawy Pro+ Stripe webhook hitting /admin/tenants) ───────
    @app.get("/tenants/me", dependencies=[Depends(require_tenant_auth)])
    def get_my_tenant(request: Request) -> dict:
        """Authenticated user fetches their own tenant info — used by the
        /setup wizard. Returns just enough for the dashboard to render
        identity + plan + active status; no other tenants' data."""
        from ..tenants import TenantRepo
        repo = TenantRepo(engine)
        tenant_id = getattr(request.state, "tenant_id", "default")
        if tenant_id == "default":
            # Codex runtime adapter (P4): surface the runtime even for the
            # synthetic default tenant so the dashboard picker reflects a
            # prior switch. ``get_runtime`` returns "claude-code" until a
            # row is materialized by the runtime picker.
            return {"id": "default", "status": "active", "plan": "free",
                    "expires_at": None, "synthetic": True,
                    "runtime_id": repo.get_runtime("default")}
        t = repo.get(tenant_id)
        if t is None:
            raise HTTPException(404, "tenant not found")
        return {
            "id": t.id, "status": t.status, "plan": t.plan,
            "expires_at": t.expires_at, "synthetic": False,
            "runtime_id": repo.get_runtime(tenant_id),
        }
