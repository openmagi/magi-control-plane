"""Rules-page tab routes: /checks + /evidence-types (D56e)."""
from __future__ import annotations

from fastapi import Depends, FastAPI, Request

from ..custom_verifier_store import CustomVerifierStore
from ..deps import require_tenant_auth
from ..policy_store import PolicyStore
from ...verifier.protocol import VerifierRegistry


def attach(
    app: FastAPI,
    policy_store: PolicyStore,
    verifier_registry: VerifierRegistry | None,
    custom_verifier_store: "CustomVerifierStore | None" = None,
) -> None:
    """D56e: the Rules page reorganized into Policies / Checks / Evidence.

    Two new derived endpoints back the new tabs:

      GET /checks
          Merged list of every *check* (pure function) the runtime can
          evaluate: built-in verifiers + tenant-scoped custom verifiers
          + inline regex / llm_critic / shacl bodies pulled from the
          policy store. Read-only; entries change as the underlying
          policies / customs are edited. See policy/check_catalog.py.

      GET /evidence-types
          Catalog of evidence record types — one row per kind of ledger
          record the system can emit. Built-in shapes come from
          verifier descriptors; inline kinds come from a static
          envelope; custom rows are surfaced as preview. See
          policy/evidence_catalog.py.

    Both endpoints are tenant-aware (custom rows are tenant-scoped) and
    require the data-plane API key, matching /catalog/* behaviour.

    The legacy /catalog/* endpoints stay live for back-compat with any
    pinned older dashboard; the new endpoints are siblings, not
    replacements.
    """
    from ...policy.check_catalog import build_check_catalog
    from ...policy.evidence_catalog import build_evidence_catalog

    @app.get("/checks", dependencies=[Depends(require_tenant_auth)])
    def list_checks(request: Request) -> dict:
        tenant_id = getattr(request.state, "tenant_id", "default")
        return {
            "items": build_check_catalog(
                policy_store=policy_store,
                verifier_registry=verifier_registry,
                custom_verifier_store=custom_verifier_store,
                tenant_id=tenant_id,
            ),
        }

    @app.get("/evidence-types", dependencies=[Depends(require_tenant_auth)])
    def list_evidence_types_v2(request: Request) -> dict:
        tenant_id = getattr(request.state, "tenant_id", "default")
        return {
            "items": build_evidence_catalog(
                policy_store=policy_store,
                verifier_registry=verifier_registry,
                custom_verifier_store=custom_verifier_store,
                tenant_id=tenant_id,
            ),
        }
