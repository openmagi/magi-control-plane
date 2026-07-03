"""Script-store routes: /scripts upload + list + delete for run_command
policies. Extracted verbatim from create_app's _attach_script_store_routes
closure (behavior-preserving)."""
from __future__ import annotations

import asyncio

from fastapi import Depends, FastAPI, HTTPException

from ...config import _run_command_allowed
from ..deps import require_admin_key
from ...policy import RunCommandPolicy
from ..policy_store import PolicyStore
from ..schemas import _ScriptUploadReq
from ..script_store import (
    MAX_SCRIPT_BYTES, ScriptStore, ScriptStoreConflict, ScriptStoreError,
    ScriptStoreInUseError, serialize as serialize_script_entry,
)


def attach(
    app: FastAPI,
    script_store: ScriptStore,
    script_store_lock: asyncio.Lock,
    *,
    policy_store: PolicyStore,
) -> None:
    """D63 — /scripts CRUD for run_command policies.

    Three routes (all admin-keyed):

      POST   /scripts            Upload a script. Idempotent on
                                 (name, sha256). Returns the persisted
                                 ScriptEntry.
      GET    /scripts            List metadata only (no source body).
      DELETE /scripts/{id}       Remove a script. Refuses (409) if any
                                 RunCommandPolicy still references the
                                 id; reports the referencing policy ids.

    Env knob: when `MAGI_CP_ALLOW_RUN_COMMAND=0`, all three routes
    return 403 — the hosted image opts out of the entire surface.
    """
    import base64 as _b64

    def _refuse_if_disabled() -> None:
        if not _run_command_allowed():
            raise HTTPException(
                403,
                "script upload is disabled on this deployment "
                "(MAGI_CP_ALLOW_RUN_COMMAND=0). Self-host docker compose "
                "ships with run_command enabled by default.",
            )

    @app.post("/scripts", dependencies=[Depends(require_admin_key)])
    async def upload_script(req: _ScriptUploadReq) -> dict:
        _refuse_if_disabled()
        try:
            raw = _b64.b64decode(req.body_b64, validate=True)
        except (ValueError, _b64.binascii.Error) as e:  # type: ignore[attr-defined]
            raise HTTPException(422, f"body_b64: {e}") from e
        if not raw:
            raise HTTPException(422, "body_b64: empty body")
        if len(raw) > MAX_SCRIPT_BYTES:
            raise HTTPException(
                422,
                f"script body too large (>{MAX_SCRIPT_BYTES} bytes)",
            )
        async with script_store_lock:
            try:
                entry = script_store.add(
                    name=req.name,
                    runtime=req.runtime,
                    body=raw,
                )
            except ScriptStoreConflict as e:
                raise HTTPException(409, str(e)) from e
            except ScriptStoreError as e:
                raise HTTPException(422, str(e)) from e
        return serialize_script_entry(entry)

    @app.get("/scripts", dependencies=[Depends(require_admin_key)])
    def list_scripts() -> dict:
        # Metadata only; bodies are returned only via the policy gate's
        # local file path (see local/gate.py:_run_command_execute). The
        # dashboard does not need the source to render the table.
        _refuse_if_disabled()
        return {"items": [serialize_script_entry(e) for e in script_store.list()]}

    # The delete path needs the policy lock too — see the inner
    # function. We pull it off app.state because the script-store
    # routes installer doesn't take policy_lock today; we add the
    # closure-captured handle so the brief's TOCTOU window closes.
    @app.delete(
        "/scripts/{script_id}",
        dependencies=[Depends(require_admin_key)],
    )
    async def delete_script(script_id: str) -> dict:
        _refuse_if_disabled()
        # P1 (TOCTOU race against PUT /policies):
        # 1. Acquire BOTH locks, in a fixed order (policy → script).
        # 2. Scan referencing policies INSIDE policy_lock.
        # 3. Delete INSIDE script_store_lock without releasing
        #    policy_lock — a concurrent PUT /policies that wants to
        #    add a new RunCommandPolicy referencing the same script
        #    will block on policy_lock and re-validate against the
        #    deleted script's absence after we return.
        # Brief: "DELETE refuses when a policy references the
        # script" — this ordering closes the gap the previous outside-
        # the-lock scan left open.
        policy_lock = getattr(app.state, "policy_lock", None)
        if policy_lock is None:
            # Fallback for older create_app callers that didn't
            # surface policy_lock on app.state. Best-effort scan;
            # still tighter than the previous "scan outside locks".
            referenced = [
                ov.policy.id
                for ov in policy_store.load()
                if isinstance(ov.policy, RunCommandPolicy)
                and ov.policy.script_path == script_id
            ]
            async with script_store_lock:
                try:
                    removed = script_store.delete(
                        script_id, referenced_by=referenced,
                    )
                except ScriptStoreInUseError as e:
                    raise HTTPException(
                        409,
                        {
                            "message": str(e),
                            "policy_ids": e.policy_ids,
                        },
                    ) from e
        else:
            async with policy_lock:
                referenced = [
                    ov.policy.id
                    for ov in policy_store.load()
                    if isinstance(ov.policy, RunCommandPolicy)
                    and ov.policy.script_path == script_id
                ]
                async with script_store_lock:
                    try:
                        removed = script_store.delete(
                            script_id, referenced_by=referenced,
                        )
                    except ScriptStoreInUseError as e:
                        raise HTTPException(
                            409,
                            {
                                "message": str(e),
                                "policy_ids": e.policy_ids,
                            },
                        ) from e
        if removed is None:
            raise HTTPException(404, f"script {script_id!r} not found")
        return serialize_script_entry(removed)


