"""Derived read-only catalog routes: evidence types + conditions (/catalog/*)."""
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
    """Derived (read-only) catalog: evidence types + conditions.

    Pure-derivation model — there is no separate storage. The catalog
    walks the live state every request:

      Evidence types  = (built-in verifier registry steps) ∪
                        (tenant-scoped custom verifier rows) ∪
                        (step referenced in any policy's requires[])
      Conditions      = (sentinel_re pattern of every policy) ∪
                        (tool matchers from every policy's trigger)

    Both are tenant-scoped because the policy list is. Custom verifier
    rows are merged in per-tenant so the operator who POSTs a row to
    /custom-verifiers and is redirected to /rules?tab=evidence sees their
    new entry on landing (instead of a "green flash but no row" gap).
    Users cannot write to either tab; entries appear/disappear as the
    policies / custom rows that reference them are saved/deleted
    (mirrors the magi-agent customize refactor — Policy is the only
    first-class entity).
    """

    @app.get("/catalog/evidence-types", dependencies=[Depends(require_tenant_auth)])
    def list_evidence_types(request: Request) -> dict:
        builtin: list[dict] = []
        builtin_steps: set[str] = set()
        if verifier_registry is not None:
            for v in verifier_registry.all():
                builtin.append({
                    "step": v.step,
                    "category": v.category,
                    "description": v.description,
                    "enforcement": v.enforcement.value,
                    "name": getattr(v, "name", None),
                    "source": "builtin",
                    "used_by_policies": [],
                })
                builtin_steps.add(v.step)
        used_by: dict[str, list[str]] = {}
        # Track which inline kinds (regex / llm_critic / shacl) appear in
        # any stored policy so we can inject the synthetic catalog rows
        # below. The /verify_inline route writes `inline_<kind>` as the
        # ledger step label, so the chip selector + emissions widget can
        # surface inline kinds via the same machinery as step-kind rows.
        used_by_inline: dict[str, list[str]] = {}
        for entry in policy_store.load():
            for req in entry.policy.requires:
                kind = getattr(req, "kind", "step")
                if kind == "step":
                    # D52c follow-up: skip empty step names. Inline-kind
                    # rows previously fell through to `used_by[""]`
                    # which produced a `step=""` catalog row and a
                    # dead `/ledger?verifier=` chip; explicit kind
                    # check above + this defensive guard keeps the
                    # catalog clean even if loader semantics shift.
                    if req.step:
                        used_by.setdefault(req.step, []).append(entry.policy.id)
                elif kind in ("regex", "llm_critic", "shacl"):
                    used_by_inline.setdefault(
                        f"inline_{kind}", [],
                    ).append(entry.policy.id)
        for row in builtin:
            row["used_by_policies"] = used_by.pop(row["step"], [])

        custom: list[dict] = []
        if custom_verifier_store is not None:
            tenant_id = getattr(request.state, "tenant_id", "default")
            for cv in custom_verifier_store.list_for_tenant(tenant_id):
                # Custom rows shadow nothing — they live in a separate
                # `source` bucket so the operator can tell at a glance
                # which entries came from their own /verifiers/new
                # authoring vs the cloud's built-in registry.
                used_by_this = used_by.pop(cv.name, [])
                # D52d follow-up: surface the author-supplied
                # field_checks the operator typed into /verifiers/new.
                # Without this projection the catalog row could only
                # ever render the "preview mode" placeholder for
                # custom verifiers, defeating the field_checks editor
                # the operator just used. The dashboard's
                # VerifierFieldChecks accepts a descriptorOverride prop
                # off this field for source='custom' rows.
                custom.append({
                    "step": cv.name,
                    "category": None,
                    "description": cv.description,
                    "enforcement": "preview",
                    "name": cv.name,
                    "source": "custom",
                    "used_by_policies": used_by_this,
                    "field_checks": [
                        {
                            "path": fc.path,
                            "check_description": fc.check_description,
                        }
                        for fc in cv.field_checks
                    ],
                })

        derived: list[dict] = []
        for step, policies in sorted(used_by.items()):
            # Defense-in-depth: skip any step that survived to here with
            # a falsy name (would produce a `?verifier=` chip with no
            # body and a React key collision on duplicates).
            if not step:
                continue
            derived.append({
                "step": step,
                "category": None,
                "description": "Referenced by a policy but not bound to "
                               "any built-in verifier — runs will deny "
                               "with no-verifier-registered.",
                "enforcement": "missing",
                "name": None,
                "source": "policy-derived",
                "used_by_policies": policies,
            })

        # D52c follow-up: synthetic catalog rows for inline kinds.
        # /verify_inline writes `body['step'] = inline_<kind>` to the
        # ledger; without these synthetic rows the chip selector +
        # emissions widget have no way to filter or count those
        # entries. We emit at most one row per inline kind (regex /
        # llm_critic / shacl) and only when at least one stored policy
        # uses that kind, so the catalog stays focused.
        _INLINE_KIND_DESCRIPTIONS = {
            "inline_regex": (
                "Inline regex check authored in a policy's requires list. "
                "Emits to the ledger as step=`inline_regex` on every "
                "evaluation; not registerable via /verifiers/new."
            ),
            "inline_llm_critic": (
                "Inline llm_critic check authored in a policy's requires "
                "list. Emits to the ledger as step=`inline_llm_critic`."
            ),
            "inline_shacl": (
                "Inline SHACL shape authored in a policy's requires list. "
                "Emits to the ledger as step=`inline_shacl`."
            ),
        }
        inline_rows: list[dict] = []
        for step in sorted(used_by_inline.keys()):
            inline_rows.append({
                "step": step,
                "category": None,
                "description": _INLINE_KIND_DESCRIPTIONS.get(
                    step,
                    "Inline policy check; emits under this step label.",
                ),
                "enforcement": "enforcing",
                "name": None,
                # `policy-derived` so the UI's per-source visual
                # treatment surfaces these as "not authored at
                # /verifiers/new" (matches the operator's mental model
                # (they live in a policy, not a verifier).
                "source": "policy-derived",
                "used_by_policies": used_by_inline[step],
            })

        return {"items": builtin + custom + inline_rows + derived}

    @app.get("/catalog/conditions", dependencies=[Depends(require_tenant_auth)])
    def list_conditions() -> dict:
        items: list[dict] = []
        for entry in policy_store.load():
            p = entry.policy
            items.append({
                "kind": "sentinel_re",
                "value": p.sentinel_re,
                "policy_id": p.id,
                "trigger_event": p.trigger.event,
                "tool_matcher": p.trigger.matcher,
            })
            items.append({
                "kind": "tool_match",
                "value": p.trigger.matcher,
                "policy_id": p.id,
                "trigger_event": p.trigger.event,
                "tool_matcher": p.trigger.matcher,
            })
            # D35: surface kind=regex / llm_critic / shacl conditions
            # extracted from each policy's requires list. step kind is
            # already surfaced via evidence-types catalog.
            for req in p.requires:
                if req.kind == "regex":
                    items.append({
                        "kind": "regex",
                        "value": req.pattern,
                        "policy_id": p.id,
                        "trigger_event": p.trigger.event,
                        "tool_matcher": p.trigger.matcher,
                    })
                elif req.kind == "llm_critic":
                    items.append({
                        "kind": "llm_critic",
                        "value": req.criterion,
                        "policy_id": p.id,
                        "trigger_event": p.trigger.event,
                        "tool_matcher": p.trigger.matcher,
                    })
                elif req.kind == "shacl":
                    # SHACL shapes can be long — truncate the catalog
                    # value to a preview head so the conditions list
                    # stays readable; the full shape lives in the
                    # policy IR.
                    head = (req.shape_ttl or "").strip()[:200]
                    items.append({
                        "kind": "shacl",
                        "value": head + (" …" if len(req.shape_ttl) > 200 else ""),
                        "policy_id": p.id,
                        "trigger_event": p.trigger.event,
                        "tool_matcher": p.trigger.matcher,
                    })
        items.sort(key=lambda r: (r["kind"], r["value"], r["policy_id"]))
        return {"items": items}
