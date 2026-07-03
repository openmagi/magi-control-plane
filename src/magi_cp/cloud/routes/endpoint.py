"""Endpoint attestation routes: /endpoints heartbeat + list (P10)."""
from __future__ import annotations

import time

from fastapi import Depends, FastAPI, HTTPException, Request

from ..db import EndpointHeartbeatRepo, is_stale
from ..deps import require_tenant_auth
from ..policy_store import PolicyStore
from ..schemas import HeartbeatReq
from ..serialization import _compile_set_with_sha


HEARTBEAT_REPLAY_WINDOW_SECONDS = 300


def attach(app: FastAPI, engine, *,
                              policy_store: "PolicyStore | None" = None) -> None:
    """P10 — endpoint attestation routes.

    Two routes:
      POST /endpoints/{endpoint_id}/heartbeat   — gate POSTs every N min
      GET  /endpoints                           — dashboard reads

    Auth on both: tenant-scoped via require_tenant_auth.

    Issue #1 P0 / P1 (#1, #2, #5, #18):
      - Heartbeats include optional `ts` + `nonce` so the cloud can
        reject replays of older payloads (5min window).
      - The GET response computes a per-tenant `cloud_active_digest`
        from the currently-enabled policy set and classifies each
        endpoint as `confirmed` / `stale-policy` / `unknown` / `not-loaded`
        so operators no longer guess by comparing 12 hex chars.
      - The list response surfaces `stale_threshold_s` so the dashboard
        copy stays in sync with the server-side threshold.
    """
    from ..db import (
        CompiledPolicySnapshotRepo, stale_endpoint_threshold_seconds,
    )

    repo = EndpointHeartbeatRepo(engine)
    snap_repo = CompiledPolicySnapshotRepo(engine)

    def _cloud_active_for_tenant(tenant_id: str) -> tuple[str | None, list[str]]:
        """Compile the currently-enabled policy set for `tenant_id` and
        return (sha256, [policy_ids]).

        Today the policy store is single-tenant (multi-tenant store is
        SECURITY.md §multi-tenant follow-up). When a store is wired we
        compile its enabled set; otherwise the active digest is None
        and every endpoint renders as `unknown`.
        """
        if policy_store is None:
            return None, []
        try:
            overrides = policy_store.load()
        except (ValueError, OSError):
            return None, []
        enabled = [ov.policy for ov in overrides if ov.enabled]
        if not enabled:
            # No policies in force — the gate's empty managed-settings
            # is the cloud-active surface; we hash an empty compile so
            # confirmation still works.
            _, sha = _compile_set_with_sha([])
            snap_repo.record(digest=sha, tenant_id=tenant_id,
                              policy_ids=[])
            return sha, []
        _, sha = _compile_set_with_sha(enabled)
        ids = [p.id for p in enabled]
        # Persist the snapshot so future-rolled gates that report this
        # digest still classify as confirmed-historical instead of
        # unknown.
        snap_repo.record(digest=sha, tenant_id=tenant_id,
                          policy_ids=ids)
        return sha, ids

    def _classify_endpoint(
        digest: str | None,
        cloud_active: str | None,
        known_digests: set[str],
    ) -> str:
        """Issue #1 P0 (#2): map gate-reported digest to a status."""
        if digest is None:
            return "not-loaded"
        if cloud_active is not None and digest == cloud_active:
            return "confirmed"
        if digest in known_digests:
            return "stale-policy"
        return "unknown"

    @app.post("/endpoints/{endpoint_id}/heartbeat",
              dependencies=[Depends(require_tenant_auth)])
    async def post_heartbeat(endpoint_id: str, body: HeartbeatReq,
                              request: Request) -> dict:
        # URL `endpoint_id` is authoritative; body's field must match or
        # we reject. This avoids one tenant's key being misused to write
        # under another endpoint_id (the key already binds the tenant
        # via require_tenant_auth, this binds the row).
        if body.endpoint_id != endpoint_id:
            raise HTTPException(400, "endpoint_id mismatch url vs body")
        tenant_id = getattr(request.state, "tenant_id", "default")
        # Issue #1 P0 (#1): ts + nonce replay window. When the gate
        # opts into the signed-attestation flow it MUST include both;
        # without `ts` we still accept (legacy gates) but record
        # nothing replay-resistant. The window is generous (±5min)
        # because gates run on consumer laptops with skewed clocks.
        if body.ts is not None:
            now = int(time.time())
            if abs(now - body.ts) > HEARTBEAT_REPLAY_WINDOW_SECONDS:
                raise HTTPException(
                    400,
                    f"heartbeat ts out of window "
                    f"(|now-ts|>{HEARTBEAT_REPLAY_WINDOW_SECONDS}s)",
                )
        # nonce reuse check: the previous heartbeat's nonce is stored;
        # an identical resubmit looks like a replay. Different nonces
        # are accepted unconditionally — the per-endpoint key (not
        # wired yet) is the real anti-replay anchor.
        if body.nonce:
            prev = repo.get(endpoint_id, tenant_id)
            if prev is not None and prev.last_nonce == body.nonce:
                raise HTTPException(409, "nonce reused")
        hb = repo.beat(
            endpoint_id=endpoint_id,
            tenant_id=tenant_id,
            active_policy_digest=body.active_policy_digest,
            agent_version=body.agent_version,
            label=body.label,
            signed_attestation=body.signed_attestation,
            nonce=body.nonce,
        )
        return {
            "endpoint_id": hb.endpoint_id,
            "tenant_id": hb.tenant_id,
            "last_seen": hb.last_seen,
            "active_policy_digest": hb.active_policy_digest,
            "agent_version": hb.agent_version,
            "label": hb.label,
            "attested": hb.signed_attestation is not None,
        }

    @app.get("/endpoints", dependencies=[Depends(require_tenant_auth)])
    def list_endpoints(request: Request) -> dict:
        tenant_id = getattr(request.state, "tenant_id", "default")
        rows = repo.list_by_tenant(tenant_id)
        cloud_active, _ids = _cloud_active_for_tenant(tenant_id)
        known = snap_repo.known_digests_for_tenant(tenant_id)
        threshold = stale_endpoint_threshold_seconds()
        items = []
        for r in rows:
            status = _classify_endpoint(
                r.active_policy_digest, cloud_active, known,
            )
            items.append({
                "endpoint_id": r.endpoint_id,
                "tenant_id": r.tenant_id,
                "last_seen": r.last_seen,
                "active_policy_digest": r.active_policy_digest,
                "agent_version": r.agent_version,
                "label": r.label,
                "stale": is_stale(r, threshold_s=threshold),
                "policy_status": status,
                "attested": r.signed_attestation is not None,
            })
        return {
            "items": items,
            "cloud_active_digest": cloud_active,
            "stale_threshold_s": threshold,
            "recommended_heartbeat_interval_s": max(60, threshold // 4),
        }
