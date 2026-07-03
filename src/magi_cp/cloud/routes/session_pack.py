"""Session-scoped pack-centric runtime routes: /session/{id}/packs (P1)."""
from __future__ import annotations

import asyncio
from typing import Callable

from fastapi import Body, Depends, FastAPI, HTTPException, Request

from ..deps import require_admin_key, require_tenant_auth
from ..pack_store import PackStore
from ..policy_store import PolicyStore
from ..serialization import _serialize_policy_for_api


def attach(
    app: FastAPI, engine,
    *,
    pack_store: "PackStore | None",
    pack_store_lock: asyncio.Lock | None,
    policy_store: "PolicyStore | None" = None,
    policy_group_store=None,
) -> None:
    """P1+P2 pack-centric runtime — session-scoped activation + resolver.

    Endpoints:
      - POST /session/{session_id}/packs/activate   {pack_id}
      - POST /session/{session_id}/packs/deactivate {pack_id}
      - GET  /session/{session_id}/packs
      - GET  /session/{session_id}/resolved          (P2)

    Each endpoint requires tenant auth (X-Api-Key) so the session row
    is keyed on (session_id, tenant_id) — one tenant cannot see or
    mutate another tenant's active-pack list even if they collide on
    the CC session uuid.

    Semantics locked by
    docs/plans/2026-06-30-pack-centric-session-scoped-runtime.md:

      - Activation is one-shot; persists until session end or explicit
        deactivate (decision 5). Endpoints only refresh ``last_seen_at``
        + extend ``expires_at`` (30d GC TTL, NOT activation TTL).
      - The floor pack cannot be deactivated (decision 7).
      - Idempotent activate returns 200 with the current list unchanged.
      - GET creates the floor pack lazily so a fresh session with no
        activations still gets a coherent ``floor_pack_id`` field.

    P2 adds ``GET /session/{id}/resolved`` which returns the pre-folded
    ``policies_by_hook`` map the gate binary caches. The route is a
    read-only projection over the same session-state row + pack
    store; the resolver's flag-OFF branch returns byte-identical
    output to the legacy path so the runtime shim can be switched
    over without a semantic change.
    """
    from ...policy.floor_pack import ensure_floor_pack_async
    from ..db import SessionActivePacksRepo

    # Serialize activate/deactivate on the same (session_id, tenant_id)
    # to keep the read-then-write path atomic under uvicorn's async
    # dispatch inside ONE worker. This lock is process-scoped only —
    # cross-worker safety is delivered by ``SessionActivePacksRepo``
    # itself (``SELECT ... FOR UPDATE`` on Postgres + IntegrityError
    # retry), NOT by this lock. See the ``SessionActivePacks`` docstring
    # in ``db.py`` for the full concurrency contract.
    session_lock = asyncio.Lock()

    def _pack_exists(pack_id: str) -> bool:
        """Return True iff ``pack_id`` names a pack the caller can
        activate. Built-in ids ("pack/…") live in the immutable catalog;
        user ids ("user-pack/…") live in the pack store. Anything else
        is a 404.

        Kept in-process so a client cannot activate a random string and
        strand the gate with an id it will never resolve.

        Tenant scoping note (decision 8 — single-tenant beta):
        ``pack_store`` is currently process-wide, so no ``tenant_id``
        argument is threaded through. When Phase 5 introduces
        per-tenant pack stores, this helper MUST accept ``tenant_id``
        and scope both the builtin visibility check and the store
        lookup accordingly, otherwise tenant A could activate a
        user-pack owned by tenant B by guessing the id.

        TOCTOU: this helper is intentionally called from inside the
        ``session_lock`` critical section in ``session_pack_activate``
        so a pack deleted between the existence check and the repo
        write cannot strand an orphaned id in
        ``session_active_packs.pack_ids``. External callers must
        preserve that invariant.
        """
        from ...policy.pack import builtin_pack_spec_by_id
        if not isinstance(pack_id, str) or not pack_id:
            return False
        if pack_id.startswith("pack/"):
            return builtin_pack_spec_by_id(pack_id) is not None
        if pack_id.startswith("user-pack/"):
            if pack_store is None:
                return False
            for row in pack_store.load():
                if row.id == pack_id:
                    return True
            return False
        return False

    def _floor_pack_id(rows: list) -> str | None:
        for r in rows:
            if getattr(r, "is_floor", False):
                return r.id
        return None

    async def _resolve_floor(tenant_id: str) -> str | None:
        """Return the floor pack id, seeding one lazily. Returns None
        only when the pack store is not wired (self-host misconfig).
        """
        if pack_store is None:
            return None
        return await ensure_floor_pack_async(
            tenant_id, pack_store, pack_store_lock,
        )

    def _envelope(row, floor_pack_id: str | None) -> dict:
        """Wire envelope for GET + write responses. Always returns the
        floor pack id alongside the caller-scoped active list so the
        client can render the "always-on" chip without a second call.
        """
        if row is None:
            return {
                "active_packs": [],
                "floor_pack_id": floor_pack_id,
                "activated_at": None,
                "last_seen_at": None,
            }
        return {
            "active_packs": list(row.pack_ids or []),
            "floor_pack_id": floor_pack_id,
            "activated_at": row.activated_at,
            "last_seen_at": row.last_seen_at,
        }

    @app.post(
        "/session/{session_id}/packs/activate",
        dependencies=[Depends(require_tenant_auth)],
    )
    async def session_pack_activate(
        session_id: str, request: Request,
        body: dict = Body(...),
    ) -> dict:
        tenant_id = request.state.tenant_id
        if not isinstance(body, dict):
            raise HTTPException(422, "body must be a JSON object")
        pack_id = body.get("pack_id")
        if not isinstance(pack_id, str) or not pack_id:
            raise HTTPException(422, "pack_id is required")
        # Decision 7: the floor pack is always-on and server-locked. It
        # is never a session-activatable id. Reject activation
        # symmetrically with the deactivate lock (which returns 400
        # ``floor_pack_locked``) so activate and deactivate present a
        # consistent contract. Without this guard the floor id passes
        # ``_pack_exists`` (it is a real ``user-pack/…`` row), gets
        # appended to ``pack_ids``, and can then never be removed because
        # deactivate rejects it — a one-way door that strands the id in
        # the active list. Resolve the floor BEFORE the lock, matching
        # ``session_pack_deactivate``, so a stray attempt is a clean 400
        # that never touches the session row.
        floor_pack_id = await _resolve_floor(tenant_id)
        if floor_pack_id is not None and pack_id == floor_pack_id:
            raise HTTPException(
                400,
                {
                    "error": "floor_pack_always_on",
                    "message": (
                        "The tenant's floor pack is always active and "
                        "cannot be session-activated. Its policies fire "
                        "on every session regardless; edit its membership "
                        "through the pack detail endpoint instead."
                    ),
                    "floor_pack_id": floor_pack_id,
                },
            )
        repo = SessionActivePacksRepo(engine)
        # TOCTOU: the pack-exists check MUST happen inside the same
        # critical section as ``repo.activate`` so a pack deleted
        # between the check and the write cannot strand an orphaned id
        # in ``session_active_packs.pack_ids``. See ``_pack_exists``.
        async with session_lock:
            if not _pack_exists(pack_id):
                raise HTTPException(404, f"pack {pack_id!r} not found")
            row, _changed = repo.activate(session_id, tenant_id, pack_id)
        envelope = _envelope(row, floor_pack_id)
        envelope["session_id"] = session_id
        return envelope

    @app.post(
        "/session/{session_id}/packs/deactivate",
        dependencies=[Depends(require_tenant_auth)],
    )
    async def session_pack_deactivate(
        session_id: str, request: Request,
        body: dict = Body(...),
    ) -> dict:
        tenant_id = request.state.tenant_id
        if not isinstance(body, dict):
            raise HTTPException(422, "body must be a JSON object")
        pack_id = body.get("pack_id")
        if not isinstance(pack_id, str) or not pack_id:
            raise HTTPException(422, "pack_id is required")
        # Decision 7: floor pack cannot be deactivated. Resolve BEFORE
        # touching the session row so a stray attempt is a clean 400 and
        # leaves ``last_seen_at`` untouched.
        floor_pack_id = await _resolve_floor(tenant_id)
        if floor_pack_id is not None and pack_id == floor_pack_id:
            raise HTTPException(
                400,
                {
                    "error": "floor_pack_locked",
                    "message": (
                        "The tenant's floor pack cannot be deactivated. "
                        "The floor pack's membership is editable "
                        "through the pack detail endpoint but the "
                        "always-on bit is server-locked."
                    ),
                    "floor_pack_id": floor_pack_id,
                },
            )
        repo = SessionActivePacksRepo(engine)
        async with session_lock:
            row, _changed = repo.deactivate(session_id, tenant_id, pack_id)
        envelope = _envelope(row, floor_pack_id)
        envelope["session_id"] = session_id
        return envelope

    @app.get(
        "/session/{session_id}/packs",
        dependencies=[Depends(require_tenant_auth)],
    )
    async def session_pack_get(session_id: str, request: Request) -> dict:
        tenant_id = request.state.tenant_id
        # Lazily seed the floor pack on any read so a fresh tenant sees
        # a coherent envelope on the first GET (per decision 6 the pack
        # ships empty; ``ensure_floor_pack_async`` is idempotent).
        floor_pack_id = await _resolve_floor(tenant_id)
        repo = SessionActivePacksRepo(engine)
        row = repo.touch(session_id, tenant_id)
        envelope = _envelope(row, floor_pack_id)
        envelope["session_id"] = session_id
        return envelope

    # ── P2 gate-cache feeder: fold pack membership → policies_by_hook ──
    def _build_pack_member_lookup() -> Callable[[str], list[str]]:
        """Return a ``pack_id -> [policy_id, ...]`` lookup closure that
        loads ``pack_store`` at most ONCE per request.

        Cost note: the closure is called per pack in the assembled
        active list, per hook coordinate. Under the pre-hoist shape
        ``_pack_members`` re-invoked ``pack_store.load()`` on every
        call, so a moderate-size install (50 policies × 10 packs ×
        N coords) paid a full store load per (coord, pack) pair. Hoisting
        the load into a dict lookup keeps the total store work at
        O(1) per request and the resolution at O(coords × packs) with
        a dict-lookup constant.
        """
        from ...policy.pack import (
            _builtin_member_ids, builtin_pack_spec_by_id,
        )
        from ...policy.pack_membership import (
            build_group_rule_index, expand_pack_member_ids,
        )
        # pack -> policy -> rule: a member id that names a policy-group
        # expands to that policy's rule ids so the gate cache enforces the
        # policy's rules. Loaded ONCE per request alongside the pack store.
        group_index = build_group_rule_index(policy_group_store)
        # Load user packs ONCE per request. Empty index when the store
        # is not wired (self-host misconfig) — matches the pre-hoist
        # "return []" branch. Member ids are expanded through the policy
        # tier at load time so the lookup returns rule ids downstream.
        user_pack_index: dict[str, list[str]] = {}
        if pack_store is not None:
            for row in pack_store.load():
                user_pack_index[row.id] = expand_pack_member_ids(
                    row.policy_ids, group_index)

        def _lookup(pack_id: str) -> list[str]:
            if not isinstance(pack_id, str) or not pack_id:
                return []
            spec = builtin_pack_spec_by_id(pack_id)
            if spec is not None:
                # Built-in packs may also reference a policy-group member.
                return expand_pack_member_ids(
                    _builtin_member_ids(spec), group_index)
            if pack_id.startswith("user-pack/"):
                return list(user_pack_index.get(pack_id, ()))
            return []

        return _lookup

    def _read_only_floor_pack_id() -> str | None:
        """Read the floor pack id WITHOUT triggering a lazy seed write.

        Used by the flag-OFF branch of ``/session/{id}/resolved`` so
        that URL is not a reachable DB write surface under
        pack-centric-runtime=OFF. Returns None when no floor row is
        already present.
        """
        if pack_store is None:
            return None
        for row in pack_store.load():
            if getattr(row, "is_floor", False):
                return row.id
        return None

    @app.get(
        "/session/{session_id}/resolved",
        dependencies=[Depends(require_tenant_auth)],
    )
    async def session_pack_resolved(
        session_id: str, request: Request,
    ) -> dict:
        """P2 gate-cache feeder.

        Return the pre-folded policy map the gate binary caches for a
        single ``(session_id, tenant_id)`` pair. Response shape::

            {
              "session_id": str,
              "tenant_id":  str,   # so a caller can round-trip the row
              "active_packs":  [pack_id, ...],   # activation-order
              "floor_pack_id": str | None,
              "pack_centric_enabled": bool,      # advisory (matches env)
              "policies_by_hook": [
                {"event": str, "matcher": str | None,
                 "policies": [<serialized_policy>, ...]},
                ...
              ]
            }

        Behavior mirrors the resolver library so the flag-OFF path
        returns the SAME set of policies for a given hook that the
        legacy runtime path would return today. That symmetry is what
        makes the runtime cut-over a pure caching change instead of a
        semantic change (see plan doc Phase 2).

        Under flag-OFF: returns every enabled policy grouped by
        (event, matcher), IGNORING active_packs. Fresh gates seeing a
        flag-OFF cloud can consume the same envelope without a
        branchy decode.

        Under flag-ON: only policies whose id belongs to (floor ∪
        activated packs) survive; the per-policy ``enabled`` bit is
        ignored per the plan doc's runtime section. Order is
        deterministic: ``policies_by_hook`` iteration follows the
        (event, matcher) first-seen order over the pack-walk, so a
        floor pack member always precedes an activated pack member on
        the same hook.
        """
        from ...policy.resolver import (
            extract_event_matcher,
            legacy_resolve_policies_for_hook,
            pack_centric_enabled,
            resolve_policies_for_hook,
        )
        tenant_id = request.state.tenant_id
        # Zero-downtime guard (P5 fail-open fix): the global env flag says
        # "pack-centric is the default", but the pack-centric path only
        # fires policies that live in a pack. If the best-effort boot
        # migration never populated THIS tenant's floor (corrupt/locked
        # store, disk error, permanent per-tenant failure), its
        # `pack_centric_migrated_at` stamp is NULL and its floor is empty
        # — resolving under pack-centric would silently return zero
        # policies for every hook, a total governance bypass. So a tenant
        # is treated as pack-centric ONLY when the global flag is on AND
        # its migration is confirmed complete; otherwise we fall back to
        # the legacy per-policy `enabled` resolver so yesterday's enabled
        # set still fires today (fail-closed against silent bypass).
        flag_on = pack_centric_enabled() and _tenant_pack_centric_migrated(
            engine, tenant_id,
        )
        # Flag-neutrality: this endpoint is REGISTERED under both flag
        # settings so smoke probes + dashboards can render envelope
        # shape without a mode flip. But side-effects (floor-pack seed
        # writes, session_active_packs row touches) MUST NOT happen
        # under flag-OFF, otherwise "flag-OFF is byte-identical" only
        # holds on the response body while the DB drifts. Split the
        # code into two branches for read-vs-write clarity.
        if flag_on:
            # Ensure the floor exists so the envelope always carries an
            # id under pack-centric semantics (mirrors GET
            # /session/{id}/packs).
            floor_pack_id = await _resolve_floor(tenant_id)
            repo = SessionActivePacksRepo(engine)
            row = repo.touch(session_id, tenant_id)
            active_packs = list(row.pack_ids) if row is not None else []
        else:
            # Read-only lookup: no lazy seed, no repo.touch. If the
            # floor row already exists we surface its id (helpful for
            # dashboard preview). Otherwise None — the flag-ON branch
            # will materialise it on the first real pack-centric read.
            floor_pack_id = _read_only_floor_pack_id()
            active_packs = []
        overrides = policy_store.load() if policy_store is not None else []

        # Collect the hook coordinates we need to answer for. Two
        # sources:
        #   (a) every event/matcher pair present on any override —
        #       gives the flag-OFF envelope 1:1 parity with today's
        #       linear-scan gate.
        #   (b) every event/matcher pair reachable via the pack union
        #       under flag-ON. Under flag-OFF this is a subset of (a),
        #       so we just take the union without branching.
        coord_seen: set[tuple[str, str | None]] = set()
        coord_order: list[tuple[str, str | None]] = []
        for ov in overrides:
            coord = extract_event_matcher(ov.policy)
            if coord[0] is None:
                continue
            if coord in coord_seen:
                continue
            coord_seen.add(coord)
            coord_order.append(coord)  # type: ignore[arg-type]

        # Hoist the pack-member lookup so pack_store is read at most
        # once per request. See ``_build_pack_member_lookup`` docstring.
        pack_member_lookup = (
            _build_pack_member_lookup() if flag_on else (lambda _pid: [])
        )

        policies_by_hook: list[dict] = []
        for event, matcher in coord_order:
            if flag_on:
                matched = resolve_policies_for_hook(
                    session_id=session_id, tenant_id=tenant_id,
                    event=event, matcher=matcher,
                    overrides=overrides,
                    active_packs=active_packs,
                    floor_pack_id=floor_pack_id,
                    pack_member_lookup=pack_member_lookup,
                )
            else:
                matched = legacy_resolve_policies_for_hook(
                    overrides, event, matcher,
                )
            if not matched:
                # Under flag-ON a coord that no pack covers yields an
                # empty list; drop it from the envelope so the gate
                # cache doesn't grow O(all_events) empty slots per
                # session. Under flag-OFF an empty list means every
                # override on the coord is disabled — same treatment.
                continue
            policies_by_hook.append({
                "event": event,
                "matcher": matcher,
                "policies": [_serialize_policy_for_api(p) for p in matched],
            })

        return {
            "session_id": session_id,
            "tenant_id": tenant_id,
            "active_packs": active_packs,
            "floor_pack_id": floor_pack_id,
            "pack_centric_enabled": flag_on,
            "policies_by_hook": policies_by_hook,
        }

    # ── P4 dashboard feeder: recent sessions + their active packs ─────
    @app.get(
        "/admin/sessions",
        dependencies=[Depends(require_admin_key)],
    )
    async def admin_list_sessions(
        request: Request, limit: int = 100,
    ) -> dict:
        """P4 ``/sessions`` dashboard tab feeder.

        Return the tenant's recent CC sessions with their currently-
        active pack ids so the operator can see "who left which pack on"
        and force-deactivate from the dashboard. Admin-key gated (same
        surface every other dashboard read uses).

        Tenant scoping (decision 8 — single-tenant beta): the admin key
        is not tenant-bound, so the caller selects the tenant via an
        optional ``?tenant_id=`` query, defaulting to the synthetic
        ``default`` tenant that a single-machine docker-compose install
        writes its session rows under. Phase 5's per-tenant admin auth
        will replace the query param with a bound tenant.

        The floor pack id is resolved read-only (no lazy seed write on a
        GET) so this route is not a hidden DB-write surface. Each row's
        ``active_packs`` carries only the session-activated packs; the
        floor pack is surfaced once at the envelope level for the
        "ALWAYS-ON" chip.
        """
        tenant_id = request.query_params.get("tenant_id") or "default"
        floor_pack_id = _read_only_floor_pack_id()
        repo = SessionActivePacksRepo(engine)
        rows = repo.list_by_tenant(tenant_id, limit=limit)
        items = [
            {
                "session_id": r.session_id,
                "tenant_id": r.tenant_id,
                # Codex runtime adapter (P4): which runtime this session
                # belongs to. Defaults to "claude-code" for every
                # pre-adapter row (server default on the column).
                "runtime_id": getattr(r, "runtime_id", None) or "claude-code",
                "active_packs": list(r.pack_ids or []),
                "activated_at": r.activated_at,
                "last_seen_at": r.last_seen_at,
                "expires_at": r.expires_at,
                "floor_pack_id": floor_pack_id,
            }
            for r in rows
        ]
        return {
            "items": items,
            "tenant_id": tenant_id,
            "floor_pack_id": floor_pack_id,
        }

def _tenant_pack_centric_migrated(engine, tenant_id: str) -> bool:
    """Return True iff the P5 boot migration has confirmed-populated this
    tenant's floor pack (``tenants.pack_centric_migrated_at IS NOT NULL``).

    This is the per-tenant half of the zero-downtime guarantee. The
    default-ON env flag is global and env-driven, decoupled from whether
    the best-effort boot migration actually seeded a given tenant's
    floor. Gating the pack-centric runtime on the per-tenant stamp makes
    a migration failure fail-CLOSED: an unstamped tenant keeps using the
    legacy per-policy `enabled` resolver (yesterday's set still fires)
    instead of resolving against an empty floor (silent total bypass).

    Any query error also fails closed to legacy — the security-control
    plane must never drop to zero governance because a status read hit a
    transient DB error.
    """
    from ..tenants import Tenant
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    try:
        with Session(engine) as s:
            stamp = s.scalar(
                select(Tenant.pack_centric_migrated_at).where(
                    Tenant.id == tenant_id
                )
            )
        return stamp is not None
    except Exception:  # pragma: no cover - defensive
        import logging
        logging.getLogger(__name__).exception(
            "magi-cp: pack-centric per-tenant migration check failed for "
            "tenant %r; falling back to the legacy per-policy resolver",
            tenant_id,
        )
        return False
