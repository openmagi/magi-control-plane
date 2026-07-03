"""FastAPI auth dependencies for the cloud app.

Extracted verbatim from ``app.py`` (modularization design
2026-07-03-cloud-app-modularization-design.md). Behavior-preserving: the
functions are byte-identical to their former in-``app.py`` form; ``app.py``
re-imports them so every ``Depends(require_*)`` reference in the route groups
keeps working unchanged. Kept in a dependency-free-of-app.py module so the
route modules can import auth deps without a circular import.
"""
from __future__ import annotations

import os

from fastapi import Header, HTTPException, Request


# ── auth deps (constant-time compare; fail-closed if env unset) ──────
def _check_key(env_var: str, header_value: str | None) -> None:
    import hmac
    expected = os.environ.get(env_var)
    if not expected:
        # Don't echo env var name back to anonymous callers (enumeration)
        raise HTTPException(503, "service unavailable: auth not configured")
    if not header_value or not hmac.compare_digest(header_value, expected):
        raise HTTPException(401, "invalid or missing api key")


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    _check_key("MAGI_CP_API_KEY", x_api_key)


def require_hitl_key(x_hitl_api_key: str | None = Header(default=None)) -> None:
    _check_key("MAGI_CP_HITL_API_KEY", x_hitl_api_key)


def require_admin_key(x_admin_api_key: str | None = Header(default=None)) -> None:
    _check_key("MAGI_CP_ADMIN_API_KEY", x_admin_api_key)


def require_tenant_auth(
    request: Request, x_api_key: str | None = Header(default=None),
) -> None:
    """Multi-tenant aware data-plane auth.

    Recognises:
      - Legacy `MAGI_CP_API_KEY` env value -> synthetic `default` tenant.
      - DB-issued `mcp_...` keys hashed in `api_keys` table -> joined tenant.

    Sets `request.state.tenant_id` for downstream endpoints to scope queries.
    """
    from .tenants import authenticate_request
    engine = request.app.state.engine
    auth = authenticate_request(engine, x_api_key)
    if auth is None:
        raise HTTPException(401, "invalid or missing api key")
    request.state.tenant_id = auth.tenant_id
    request.state.api_key_id = auth.api_key_id


def _resolve_tenant_id_from_request(request: Request) -> str | None:
    """Best-effort tenant resolution for routes where auth is OPTIONAL.

    Used by /verifiers + /catalog/evidence-types so that an unauthenticated
    caller gets the global view (legacy behaviour) while an authenticated
    caller transparently sees their tenant's custom verifiers merged in.
    Mirrors the shape of require_tenant_auth but returns None instead of
    raising on missing / invalid auth.
    """
    from .tenants import authenticate_request
    # If require_tenant_auth has already run on this request, reuse its
    # decision. Saves a DB round-trip on the common authed case.
    cached = getattr(request.state, "tenant_id", None)
    if cached is not None:
        return cached
    api_key = request.headers.get("x-api-key")
    if not api_key:
        return None
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        return None
    try:
        auth = authenticate_request(engine, api_key)
    except Exception:
        return None
    if auth is None:
        return None
    return auth.tenant_id


__all__ = [
    "_check_key",
    "require_api_key",
    "require_hitl_key",
    "require_admin_key",
    "require_tenant_auth",
    "_resolve_tenant_id_from_request",
]
