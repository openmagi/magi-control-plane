"""Codex runtime adapter routes: policy coverage + per-tenant runtime."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request

from ..deps import require_admin_key
from ..pack_store import PackStore
from ..policy_store import PolicyStore


def attach(
    app: FastAPI, engine, *,
    policy_store: "PolicyStore | None",
    pack_store: "PackStore | None",
    policy_group_store=None,
) -> None:
    """Codex runtime adapter (P4): per-runtime coverage + per-tenant
    runtime preference for the dashboard runtime picker.

    Design brief: 2026-06-30-codex-runtime-adapter-design (private planning repo)
    Section 7. Everything here is READ-safe; only the
    ``POST /tenants/{id}/runtime`` switch to ``codex`` is gated on
    ``MAGI_CP_CODEX_RUNTIME_ENABLED`` (default ON; the switch is refused
    only when the flag is set to an explicit falsy value).

    Routes:
      - GET  /policies/{policy_id}/coverage/{runtime_id}  - per-policy strip
      - GET  /packs/{pack_id}/coverage/{runtime_id}       - per-pack rollup
      - GET  /tenants/{tenant_id}/runtime                 - picker state
      - POST /tenants/{tenant_id}/runtime                 - switch runtime

    All coverage reads reuse ``HookRuntime.coverage_report`` (P1) so the
    dashboard never re-derives coverage semantics.
    """
    from ...config import codex_runtime_enabled, gjc_runtime_enabled
    from ...policy.pack import builtin_pack_spec_by_id, _builtin_member_ids
    from ...policy.pack_membership import (
        build_group_rule_index, expand_pack_member_ids,
    )
    from ...policy.prebuilt import build_prebuilt_evidence_policy
    from ...runtime import get_runtime, rollup_cells
    from ...runtime.trait import coverage_cell
    from ..tenants import TenantRepo

    _KNOWN_RUNTIMES = ("claude-code", "codex", "gjc")

    def _canonical_runtime(runtime_id: str) -> str | None:
        """Map a URL runtime token onto a canonical id, or None when
        unknown (so the caller can 404)."""
        key = (runtime_id or "").strip().lower()
        if key in ("cc", "claude-code", "claude_code", "claudecode"):
            return "claude-code"
        if key == "codex":
            return "codex"
        if key in ("gjc", "gajae-code", "gajae_code"):
            return "gjc"
        return None

    def _policy_ir_by_id(policy_id: str):
        """Resolve a policy id to its IR: operator-saved store row first,
        then the prebuilt catalog (so built-in pack members resolve too).
        Returns None when neither knows the id."""
        if policy_store is not None:
            for ov in policy_store.load():
                if ov.policy.id == policy_id:
                    return ov.policy
        return build_prebuilt_evidence_policy(policy_id)

    def _all_store_ir() -> list:
        """The catalog the per-tenant picker rollup measures coverage
        against: operator-saved policies PLUS the members of the
        always-on floor pack.

        A pack-centric tenant can run with an empty ``policy_store``
        while all enforcement flows from the built-in floor pack.
        Counting store rows alone would make the picker under-report
        ("0 policies enforced") even as the per-pack rollup cards show
        the real non-zero counts. Resolving floor-pack members through
        ``_policy_ir_by_id`` (store row first, prebuilt catalog
        fallback) keeps the picker total aligned with those cards.
        Deduped by policy id so a member that is also an operator-saved
        row is counted exactly once."""
        seen: set[str] = set()
        out: list = []
        if policy_store is not None:
            for ov in policy_store.load():
                if ov.policy.id not in seen:
                    seen.add(ov.policy.id)
                    out.append(ov.policy)
        if pack_store is not None:
            group_index = build_group_rule_index(policy_group_store)
            for row in pack_store.load():
                if not getattr(row, "is_floor", False):
                    continue
                # pack -> policy -> rule: expand policy-group members to
                # their rule ids before resolving IR.
                for mid in expand_pack_member_ids(row.policy_ids, group_index):
                    if mid in seen:
                        continue
                    ir = _policy_ir_by_id(mid)
                    if ir is not None:
                        seen.add(mid)
                        out.append(ir)
        return out

    def _resolve_pack_member_ids(pack_id: str) -> list[str] | None:
        """Ordered member policy ids for a pack, or None when unknown.
        Mirrors ``_attach_policy_routes._resolve_pack_members`` (kept
        local so the runtime routes carry no dependency on that closure)."""
        group_index = build_group_rule_index(policy_group_store)
        spec = builtin_pack_spec_by_id(pack_id)
        if spec is not None:
            return expand_pack_member_ids(_builtin_member_ids(spec), group_index)
        if pack_id.startswith("user-pack/") and pack_store is not None:
            for row in pack_store.load():
                if row.id == pack_id:
                    return expand_pack_member_ids(row.policy_ids, group_index)
        return None

    @app.get(
        "/policies/{policy_id:path}/coverage/{runtime_id}",
        dependencies=[Depends(require_admin_key)],
    )
    def policy_coverage(policy_id: str, runtime_id: str) -> dict:
        canonical = _canonical_runtime(runtime_id)
        if canonical is None:
            raise HTTPException(404, f"unknown runtime {runtime_id!r}")
        ir = _policy_ir_by_id(policy_id)
        if ir is None:
            raise HTTPException(404, f"policy {policy_id!r} not found")
        report = get_runtime(canonical).coverage_report([ir])
        status = report.policies[0]
        return {
            "policy_id": policy_id,
            "runtime_id": canonical,
            "status": status.status,
            "downgrade": status.downgrade,
            "coverage": coverage_cell(status.status, status.downgrade),
        }

    @app.get(
        "/packs/{pack_id:path}/coverage/{runtime_id}",
        dependencies=[Depends(require_admin_key)],
    )
    def pack_coverage(pack_id: str, runtime_id: str) -> dict:
        canonical = _canonical_runtime(runtime_id)
        if canonical is None:
            raise HTTPException(404, f"unknown runtime {runtime_id!r}")
        member_ids = _resolve_pack_member_ids(pack_id)
        if member_ids is None:
            raise HTTPException(404, f"pack {pack_id!r} not found")
        ir = [p for p in (_policy_ir_by_id(m) for m in member_ids)
              if p is not None]
        report = get_runtime(canonical).coverage_report(ir)
        rollup = rollup_cells(report)
        rollup["pack_id"] = pack_id
        return rollup

    def _runtime_rollup(canonical: str) -> dict:
        report = get_runtime(canonical).coverage_report(_all_store_ir())
        rollup = rollup_cells(report)
        rollup["id"] = canonical
        # The whole-catalog picker rollup does not need the per-policy
        # detail list; drop it to keep the picker payload small.
        rollup.pop("policies", None)
        return rollup

    @app.get(
        "/tenants/{tenant_id}/runtime",
        dependencies=[Depends(require_admin_key)],
    )
    def get_tenant_runtime(tenant_id: str) -> dict:
        repo = TenantRepo(engine)
        current = repo.get_runtime(tenant_id)
        return {
            "tenant_id": tenant_id,
            "runtime_id": current,
            "codex_enabled": codex_runtime_enabled(),
            "gjc_enabled": gjc_runtime_enabled(),
            "runtimes": [_runtime_rollup(r) for r in _KNOWN_RUNTIMES],
        }

    @app.post(
        "/tenants/{tenant_id}/runtime",
        dependencies=[Depends(require_admin_key)],
    )
    async def set_tenant_runtime(tenant_id: str, request: Request) -> dict:
        # Parse the body manually: a closure-local Pydantic model is not
        # resolvable by FastAPI under `from __future__ import annotations`
        # (the annotation is a string looked up in module globals, where
        # a local class does not live), so it would be mis-read as a
        # query param. Manual parse keeps the route self-contained.
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(400, "invalid json body")
        if not isinstance(payload, dict):
            raise HTTPException(400, "body must be an object")
        requested = payload.get("runtime_id")
        if not isinstance(requested, str) or not requested.strip():
            raise HTTPException(400, "runtime_id required")
        canonical = _canonical_runtime(requested)
        if canonical is None:
            raise HTTPException(400, f"unknown runtime {requested!r}")
        # Feature-flag ladder (Section 9.3): the global kill switch gates
        # any switch TO codex. Switching back to claude-code is always
        # allowed so an operator can revert even on a build where the
        # flag was later turned off.
        if canonical == "codex" and not codex_runtime_enabled():
            raise HTTPException(
                403,
                "codex runtime disabled: MAGI_CP_CODEX_RUNTIME_ENABLED is set "
                "to an explicit falsy value (unset it to re-enable; default ON)",
            )
        if canonical == "gjc" and not gjc_runtime_enabled():
            raise HTTPException(
                403,
                "gjc runtime disabled: MAGI_CP_GJC_RUNTIME_ENABLED is set "
                "to an explicit falsy value (unset it to re-enable; default ON)",
            )
        TenantRepo(engine).set_runtime(tenant_id, runtime_id=canonical)
        return {"tenant_id": tenant_id, "runtime_id": canonical}
