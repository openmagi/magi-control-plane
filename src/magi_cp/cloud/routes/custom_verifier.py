"""Step-only custom verifier authoring routes (tenant-scoped) (D52b)."""
from __future__ import annotations

import asyncio

from fastapi import Depends, FastAPI, HTTPException, Path as FPath, Request

from ..custom_verifier_store import (
    CustomVerifierConflict, CustomVerifierError, CustomVerifierStore,
    build_from_dict as build_custom_verifier_from_dict,
    serialize as serialize_custom_verifier,
)
from ..deps import require_tenant_auth
from ..schemas import CreateCustomVerifierReq
from ...verifier.protocol import VerifierRegistry


def attach(
    app: FastAPI, store: "CustomVerifierStore",
    custom_verifier_lock: asyncio.Lock,
    *,
    verifier_registry: VerifierRegistry | None = None,
) -> None:
    """D52b: step-only authoring of custom verifiers.

    The /verifiers/new dashboard page POSTs here. Body shape (validated by
    custom_verifier_store.build_from_dict):

      {
        "name": "<slug>",
        "description": "<<=500 chars>",
        "triggers": [{"event": "...", "matcher_class": "tool|no_tool|final"}, ...],
        "verdict_set": ["pass", "fail", ...],
        "body_type": "preview"
      }

    Tenant-scoped: each row carries the caller's tenant_id, and GETs
    only resolve rows the caller owns. Real-code bodies (LLM critic / SHACL
    / regex) stay inline in the policy IR per the design lock, so this
    endpoint accepts step-shape only.

    Concurrency: store mutation runs under `custom_verifier_lock` so two
    concurrent POSTs on the same tenant cannot race the load → mutate →
    save sequence and lose a row to overwrite. Mirrors the policy_lock
    pattern in _attach_policy_routes.
    """

    @app.post(
        "/custom-verifiers",
        dependencies=[Depends(require_tenant_auth)],
    )
    async def create_custom_verifier(
        req: CreateCustomVerifierReq, request: Request,
    ) -> dict:
        tenant_id = getattr(request.state, "tenant_id", "default")
        # Hand the Pydantic-validated body to build_from_dict so the
        # store-layer validators (allowed-event vocab from D47, trigger
        # cap + dedupe, verdict allowlist) stay the single source of
        # truth. The model_dump matches build_from_dict's dict shape.
        try:
            verifier = build_custom_verifier_from_dict(
                req.model_dump(), tenant_id=tenant_id,
            )
        except CustomVerifierError as e:
            raise HTTPException(422, str(e))
        # D56e follow-up: reject any custom-verifier name that collides
        # with a registered built-in step. The Checks + Evidence catalog
        # rows are keyed by `id` (verifier step for builtins, name for
        # custom). A duplicate id surfaces as two rows the dashboard
        # de-dupes via React keys, silently dropping one. Fail at write
        # time so the conflict is visible to the operator authoring the
        # custom verifier instead of disappearing on the next list call.
        if verifier_registry is not None:
            try:
                builtin_steps = {v.step for v in verifier_registry.all()}
            except Exception:
                builtin_steps = set()
            if verifier.name in builtin_steps:
                raise HTTPException(
                    409,
                    f"a built-in verifier step named {verifier.name!r} already "
                    f"exists; choose a different name to avoid catalog collision",
                )
        async with custom_verifier_lock:
            try:
                stored = store.add(tenant_id, verifier)
            except CustomVerifierConflict as e:
                raise HTTPException(409, str(e))
        return serialize_custom_verifier(stored)

    @app.get(
        "/custom-verifiers",
        dependencies=[Depends(require_tenant_auth)],
    )
    def list_custom_verifiers(request: Request) -> dict:
        tenant_id = getattr(request.state, "tenant_id", "default")
        items = [serialize_custom_verifier(v) for v in store.list_for_tenant(tenant_id)]
        return {"items": items}

    @app.get(
        "/custom-verifiers/{verifier_id}",
        dependencies=[Depends(require_tenant_auth)],
    )
    def get_custom_verifier(
        request: Request,
        verifier_id: str = FPath(..., pattern=r"^[a-f0-9]{16}$"),
    ) -> dict:
        tenant_id = getattr(request.state, "tenant_id", "default")
        v = store.get(tenant_id, verifier_id)
        if v is None:
            raise HTTPException(404, "custom verifier not found")
        return serialize_custom_verifier(v)
