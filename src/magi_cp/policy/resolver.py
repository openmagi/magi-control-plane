"""P2 pack-centric runtime: gate resolution shift.

Design brief: docs/plans/2026-06-30-pack-centric-session-scoped-runtime.md
(§ "Runtime changes" + Phase 2 rollout row).

The cloud today answers "which policies fire on THIS hook call?" by
walking every enabled ``PolicyOverride`` and matching the trigger
(event, matcher) against the incoming payload. That behaviour is the
"legacy" path.

Under the pack-centric model, "which policies fire" is a function of
(a) the session's active pack list, (b) the tenant's floor pack, and
(c) the hook coordinates. The per-policy ``enabled`` bit is IGNORED
on the pack-centric path per decisions 1 + 6 + 7:

  - 1. Multiple active packs per session. Union of policies. Ordering:
       floor first, then activation order.
  - 6. Floor pack ships empty; ``pack_ids = [floor] + activated``.
  - 7. Floor pack cannot be deactivated. Always included.

This module exposes:

  * :func:`pack_centric_enabled` — the single flag toggle. Reads
    ``MAGI_CP_PACK_CENTRIC_RUNTIME`` first, then an optional cloud-side
    setting the caller supplies (Phase 5 will store this in the cloud
    DB; P2 accepts the argument as a placeholder so wiring lands with
    zero downstream churn).
  * :func:`legacy_resolve_policies_for_hook` — the current behaviour,
    extracted so both paths share the hook-matching primitive.
  * :func:`resolve_policies_for_hook` — the wrapper decided by the flag.

The two ``_extract_event_matcher`` / ``_matches_hook`` helpers factor
the "is this policy's trigger a match for the incoming
(event, matcher)?" predicate so the legacy and pack-centric paths use
the same rules (matcher-class-aware coverage via
``matrix.matcher_covers``, ``ContextInjectionPolicy`` normalised to
its top-level ``event`` + ``matcher`` fields).

Legacy path stays byte-for-byte identical until the flag flips.
"""
from __future__ import annotations

from typing import Callable, Iterable

from .ir import AnyPolicy
from .matrix import matcher_covers
from ..config import pack_centric_runtime_enabled as _env_flag
from .resolved import PolicyOverride


# Type alias for the pack-membership lookup the caller wires in.
# ``pack_id -> list of policy ids``. Empty list for unknown packs so
# the resolver never has to distinguish "no members" from "unknown id"
# on the hot path.
PackMemberLookup = Callable[[str], list[str]]


def pack_centric_enabled(cloud_setting: bool | None = None) -> bool:
    """Return True iff the pack-centric runtime path is active.

    Truthy sources, evaluated in order:

      1. ``MAGI_CP_PACK_CENTRIC_RUNTIME`` env-var (see
         :func:`magi_cp.config.pack_centric_runtime_enabled`). Any
         truthy value flips the runtime on.
      2. ``cloud_setting`` — placeholder for the Phase 5 cloud-side
         global toggle. P2 accepts the argument so wiring the
         cloud-side setting later does not require re-touching every
         call site; the argument stays None for now.

    Default False. Both branches must agree the runtime is off before
    the legacy path applies.
    """
    if _env_flag():
        return True
    if cloud_setting is True:
        return True
    return False


def extract_event_matcher(
    policy: AnyPolicy,
) -> tuple[str | None, str | None]:
    """Return ``(event, matcher)`` for any policy archetype, or
    ``(None, None)`` when the policy is not event-scoped.

    * ``EvidencePolicy`` / ``PermissionPolicy`` / ``InputRewritePolicy``
      / ``RunCommandPolicy`` — carry a ``trigger`` sub-object.
    * ``ContextInjectionPolicy`` — event + matcher live at the top
      level (no ``trigger``).
    * ``SubagentPolicy`` / ``McpGatingPolicy`` — not event-scoped;
      compile straight to managed-settings and do not participate in
      the hook-resolution predicate.

    Public since D80 P2 follow-up: the cloud's ``/session/{id}/resolved``
    handler needs the same extraction rule this module's resolver uses
    to fold policies_by_hook. Keeping two copies (a private one here +
    a hand-rolled one in ``cloud/app.py``) creates a slow-drift risk
    when a future policy archetype gains an event field — the resolver
    starts firing on the new event while the cloud endpoint still
    walks the old shape and drops the policy from its envelope. One
    exported helper closes that gap.
    """
    trig = getattr(policy, "trigger", None)
    if trig is not None:
        return (
            getattr(trig, "event", None),
            getattr(trig, "matcher", None),
        )
    # ContextInjectionPolicy has flat event + matcher fields.
    event = getattr(policy, "event", None)
    if event is not None:
        matcher = getattr(policy, "matcher", None)
        return event, matcher
    return None, None


# Backwards-compatible alias for the pre-D80-P2-follow-up name. Kept
# because internal call sites and tests may import the leading-
# underscore name; the public alias above is what external callers
# (cloud/app.py) should use.
_extract_event_matcher = extract_event_matcher


def _matches_hook(
    policy: AnyPolicy, event: str, matcher: str | None,
) -> bool:
    """Predicate: does ``policy`` fire on the given hook coordinates?

    ``matcher=None`` means "the incoming hook did not name a tool"
    (e.g. UserPromptSubmit / Stop) — in that case we do not filter on
    the matcher field so a wildcard policy still fires and a
    per-tool policy is skipped. When the caller supplies a matcher
    (typically the incoming ``tool_name``), we defer to
    ``matrix.matcher_covers`` so wildcard / tool_alt / exact all resolve
    identically to the runtime shim's predicate.

    A policy with no event (SubagentPolicy / McpGatingPolicy) is
    silently skipped — those archetypes never participate in hook
    dispatch.
    """
    p_event, p_matcher = _extract_event_matcher(policy)
    if p_event is None:
        return False
    if p_event != event:
        return False
    if p_matcher is None:
        # An event-scoped policy without a matcher (should not happen
        # under authoring rules) matches everything on the event.
        return True
    if matcher is None:
        # Caller did not scope to a tool. A wildcard matcher still
        # covers the hook; anything else does not.
        return matcher_covers(p_matcher, "")
    return matcher_covers(p_matcher, matcher)


def legacy_resolve_policies_for_hook(
    overrides: Iterable[PolicyOverride],
    event: str,
    matcher: str | None = None,
) -> list[AnyPolicy]:
    """Legacy behaviour: enabled overrides whose trigger matches.

    Preserves the pre-P2 semantics so the flag-OFF path stays
    byte-for-byte identical: per-policy ``enabled=True`` gates
    participation; the tighten-only ``ResolvedPolicySet`` pipeline is
    NOT applied here because the runtime shim already ran
    ``PolicyStore.load`` (which returns the persisted override rows,
    not a post-resolution set). This function is the direct extract of
    the ``for ov in store.load(): if ov.enabled and matches`` loop that
    the input_rewrite / run_command shims run inline today.

    Return ordering mirrors ``overrides`` iteration order — the caller
    controls determinism by how it builds the input list (the policy
    store already sorts by id at save time).
    """
    out: list[AnyPolicy] = []
    for ov in overrides:
        if not ov.enabled:
            continue
        if _matches_hook(ov.policy, event, matcher):
            out.append(ov.policy)
    return out


def resolve_policies_for_hook(
    *,
    session_id: str,
    tenant_id: str,
    event: str,
    matcher: str | None,
    overrides: Iterable[PolicyOverride],
    active_packs: list[str],
    floor_pack_id: str | None,
    pack_member_lookup: PackMemberLookup,
    cloud_setting: bool | None = None,
) -> list[AnyPolicy]:
    """Return policies to evaluate for this hook call.

    Flag-OFF: delegates to :func:`legacy_resolve_policies_for_hook`
    unchanged. ``session_id`` / ``tenant_id`` / ``active_packs`` /
    ``floor_pack_id`` / ``pack_member_lookup`` are unused on that path;
    the wrapper accepts them so the call site stays uniform.

    Flag-ON (pack-centric):

      1. Assemble the ordered pack id list. The floor pack leads
         (decision 1), followed by the session's activated packs in
         activation order. The floor pack is prepended EVEN WHEN
         EMPTY — an empty floor contributes zero policies but the id
         still leads so tests keep the ordering guarantee stable.
      2. Collect the union of member policy ids across every pack in
         that list (dedup, preserving first-seen order).
      3. Materialise the member ids to actual ``AnyPolicy`` objects by
         looking them up in ``overrides``. The per-policy ``enabled``
         bit is IGNORED here — pack membership is the authoritative
         gate (per plan doc § "Runtime changes").
      4. Filter by hook coordinates via ``_matches_hook`` so a pack
         that contains a policy targeting a different event/matcher
         does not smear its members onto every hook.

    Return ordering is deterministic: outer loop iterates pack ids in
    floor-first-then-activation-order; inner loop iterates each pack's
    ``policy_ids`` in author-declared order. First-seen wins on
    duplicate policy ids.

    Missing pieces (silent, defensive):

      * ``floor_pack_id=None`` — self-host misconfig where the pack
        store is not wired. Treated as "no floor row", so the caller
        still gets the activated packs (the only failure mode is that
        the always-on bit is not enforced; the fail-closed decision
        belongs at the pack-store construction site).
      * ``pack_member_lookup`` returning an unknown id — the id is
        dropped silently. The dashboard is the auditing surface for
        pack-vs-catalog drift.
      * ``overrides`` missing a member id — the member is skipped.
        This is a normal state during migration: a pack references a
        prebuilt slug that has not yet been enabled.
    """
    if not pack_centric_enabled(cloud_setting=cloud_setting):
        return legacy_resolve_policies_for_hook(overrides, event, matcher)

    # Assemble pack id order: floor first (even if empty), then
    # activated packs (dedup vs the floor).
    pack_ids: list[str] = []
    if floor_pack_id is not None:
        pack_ids.append(floor_pack_id)
    for pid in active_packs or []:
        if pid == floor_pack_id:
            continue
        if pid in pack_ids:
            continue
        pack_ids.append(pid)

    # Index overrides by id so pack member ids resolve in O(1).
    # NOTE: pack-centric mode intentionally ignores the ``enabled`` bit
    # so a policy authored today that is currently enabled=False can
    # STILL fire tomorrow via pack activation.
    by_id: dict[str, AnyPolicy] = {}
    for ov in overrides:
        by_id[ov.policy.id] = ov.policy

    seen: set[str] = set()
    out: list[AnyPolicy] = []
    for pid in pack_ids:
        for mid in pack_member_lookup(pid):
            if not isinstance(mid, str) or not mid:
                continue
            if mid in seen:
                continue
            seen.add(mid)
            p = by_id.get(mid)
            if p is None:
                continue
            if not _matches_hook(p, event, matcher):
                continue
            out.append(p)
    return out


__all__ = [
    "PackMemberLookup",
    "extract_event_matcher",
    "legacy_resolve_policies_for_hook",
    "pack_centric_enabled",
    "resolve_policies_for_hook",
]
