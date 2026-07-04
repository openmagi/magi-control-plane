"""P2 pack-centric runtime — resolver tests.

Design brief: 2026-06-30-pack-centric-session-scoped-runtime (private planning repo)
(§ "Runtime changes" + Phase 2).

Covered here (per implementation-brief "Tests" bullet):

  1. Flag OFF: ``resolve_policies_for_hook`` output ==
     ``legacy_resolve_policies_for_hook`` for the same inputs (byte
     identity of the returned list). Legacy path never touches active
     packs / floor pack.

  2. Flag ON: output equals the intersection of
     (floor ∪ activated pack members) with the hook filter. The
     per-policy ``enabled=False`` bit is IGNORED on the pack-centric
     path (a disabled policy still fires if a pack activates it).

  3. Ordering: floor first, then activated packs in the order they
     appear in ``active_packs`` — regression guard for decision 1.

  4. Duplicate handling: a policy id present in multiple packs
     yields exactly one entry in the output.

  5. Unknown pack id in ``active_packs`` — silently dropped.

  6. ContextInjectionPolicy (event + matcher at top level, no
     ``trigger`` sub-object) resolves against the same hook.
"""
from __future__ import annotations

import pytest

from magi_cp.policy.ir import (
    ContextInjectionPolicy, EvidencePolicy, EvidenceReq, Trigger,
)
from magi_cp.policy.resolved import PolicyOverride
from magi_cp.policy.resolver import (
    legacy_resolve_policies_for_hook,
    pack_centric_enabled,
    resolve_policies_for_hook,
)


# ── shared fixtures ──────────────────────────────────────────────────
def _make_evidence(
    pid: str, *, event: str = "PreToolUse", matcher: str = "Bash",
    action: str = "block",
) -> EvidencePolicy:
    return EvidencePolicy(
        id=pid, description="t", version="0.1",
        trigger=Trigger(host="claude-code", event=event, matcher=matcher),
        sentinel_re=None,
        requires=[EvidenceReq(kind="step", step="citation_verify",
                              verdict="pass")],
        action=action, on_signature_invalid="deny",
        gate_binary="/usr/local/bin/magi-gate.sh",
    )


def _override(policy, *, source="user", enabled=True) -> PolicyOverride:
    return PolicyOverride(policy=policy, source=source, enabled=enabled)


# ── pack_centric_enabled flag semantics ──────────────────────────────
def test_flag_default_on_after_p5(monkeypatch):
    """P5 flipped the default: unset now means pack-centric ON. The boot
    migration moved every enabled policy into the floor pack, so the
    default runtime is the pack-centric path."""
    monkeypatch.delenv("MAGI_CP_PACK_CENTRIC_RUNTIME", raising=False)
    assert pack_centric_enabled() is True


@pytest.mark.parametrize("falsy", ["0", "false", "no", "off", "OFF", ""])
def test_flag_explicit_falsy_rolls_back(monkeypatch, falsy):
    """The only way back to the legacy per-policy path is an explicit
    falsy value. Rollback contract for P5."""
    monkeypatch.setenv("MAGI_CP_PACK_CENTRIC_RUNTIME", falsy)
    assert pack_centric_enabled() is False


@pytest.mark.parametrize("truthy", ["1", "true", "yes", "on", "TRUE"])
def test_flag_env_truthy_flips_on(monkeypatch, truthy):
    monkeypatch.setenv("MAGI_CP_PACK_CENTRIC_RUNTIME", truthy)
    assert pack_centric_enabled() is True


def test_flag_cloud_setting_placeholder_true_flips_on(monkeypatch):
    """Cloud-side global setting placeholder: caller passes True.

    P5 note: the env default is now ON, so to isolate the cloud-side
    placeholder as the deciding source we roll the env back to ``0``.
    With the env off, the cloud setting is the only lever."""
    monkeypatch.setenv("MAGI_CP_PACK_CENTRIC_RUNTIME", "0")
    assert pack_centric_enabled(cloud_setting=True) is True
    # Explicit False stays off (both sources must disagree).
    assert pack_centric_enabled(cloud_setting=False) is False


# ── flag-OFF path: identical to legacy ────────────────────────────────
def test_flag_off_output_matches_legacy(monkeypatch):
    """The flag-OFF branch MUST return the same list as
    ``legacy_resolve_policies_for_hook`` for the same inputs. This
    is the semantic-parity guarantee that lets P5 flip the default
    without changing what fires on any given hook.

    P5 note: the default is now ON, so "flag OFF" means an explicit
    rollback value (``0``). This is the legacy path an operator lands on
    after ``MAGI_CP_PACK_CENTRIC_RUNTIME=0``.
    """
    monkeypatch.setenv("MAGI_CP_PACK_CENTRIC_RUNTIME", "0")
    overrides = [
        _override(_make_evidence("a", matcher="Bash")),
        _override(_make_evidence("b", matcher="Bash"), enabled=False),
        _override(_make_evidence("c", matcher="Read")),
    ]
    legacy_out = legacy_resolve_policies_for_hook(
        overrides, event="PreToolUse", matcher="Bash",
    )
    wrapper_out = resolve_policies_for_hook(
        session_id="s", tenant_id="t",
        event="PreToolUse", matcher="Bash",
        overrides=overrides,
        active_packs=["user-pack/x"],  # ignored under flag-OFF
        floor_pack_id="user-pack/floor",  # ignored
        pack_member_lookup=lambda pid: ["a", "b"],  # ignored
    )
    # Byte-identical list. The "b" override is disabled so both paths
    # drop it; "c" is on a different matcher; only "a" survives.
    assert [p.id for p in legacy_out] == ["a"]
    assert [p.id for p in wrapper_out] == ["a"]


def test_legacy_matcher_none_matches_wildcard():
    """``matcher=None`` (hooks without a tool name — e.g. Stop) still
    fires wildcard-matcher policies. Regression guard for
    ``_matches_hook``'s ``matcher=None`` branch.
    """
    overrides = [
        _override(_make_evidence("stopper", event="Stop", matcher="*",
                                 action="audit")),
        _override(_make_evidence("bash", matcher="Bash")),
    ]
    out = legacy_resolve_policies_for_hook(
        overrides, event="Stop", matcher=None,
    )
    assert [p.id for p in out] == ["stopper"]


def test_legacy_disabled_override_skipped():
    """Per-policy ``enabled=False`` blocks participation on legacy path."""
    overrides = [_override(_make_evidence("a"), enabled=False)]
    out = legacy_resolve_policies_for_hook(
        overrides, event="PreToolUse", matcher="Bash",
    )
    assert out == []


# ── flag-ON path: pack-centric ────────────────────────────────────────
def test_flag_on_ignores_per_policy_enabled_bit(monkeypatch):
    """Under pack-centric, membership in an active pack is the gate;
    the policy's own ``enabled=False`` bit does NOT stop it firing.
    """
    monkeypatch.setenv("MAGI_CP_PACK_CENTRIC_RUNTIME", "1")
    overrides = [_override(_make_evidence("a"), enabled=False)]

    def lookup(pid: str) -> list[str]:
        return ["a"] if pid == "user-pack/mine" else []

    out = resolve_policies_for_hook(
        session_id="s", tenant_id="t",
        event="PreToolUse", matcher="Bash",
        overrides=overrides,
        active_packs=["user-pack/mine"],
        floor_pack_id="user-pack/floor",
        pack_member_lookup=lookup,
    )
    assert [p.id for p in out] == ["a"]


def test_flag_on_only_pack_members_fire(monkeypatch):
    """A policy not in any active pack does NOT fire, even when
    enabled=True. This is the model shift the plan doc calls out.
    """
    monkeypatch.setenv("MAGI_CP_PACK_CENTRIC_RUNTIME", "1")
    overrides = [
        _override(_make_evidence("in-pack")),
        _override(_make_evidence("orphan")),
    ]

    def lookup(pid: str) -> list[str]:
        return ["in-pack"] if pid == "user-pack/x" else []

    out = resolve_policies_for_hook(
        session_id="s", tenant_id="t",
        event="PreToolUse", matcher="Bash",
        overrides=overrides,
        active_packs=["user-pack/x"],
        floor_pack_id="user-pack/floor",
        pack_member_lookup=lookup,
    )
    assert [p.id for p in out] == ["in-pack"]


def test_flag_on_floor_first_then_activation_order(monkeypatch):
    """Decision 1: ordering = floor first, then activation order."""
    monkeypatch.setenv("MAGI_CP_PACK_CENTRIC_RUNTIME", "1")
    overrides = [
        _override(_make_evidence("floor-1")),
        _override(_make_evidence("pack-a-1")),
        _override(_make_evidence("pack-b-1")),
    ]

    def lookup(pid: str) -> list[str]:
        return {
            "user-pack/floor": ["floor-1"],
            "pack/a": ["pack-a-1"],
            "pack/b": ["pack-b-1"],
        }.get(pid, [])

    # Operator activated b before a — activation order is b, a.
    out = resolve_policies_for_hook(
        session_id="s", tenant_id="t",
        event="PreToolUse", matcher="Bash",
        overrides=overrides,
        active_packs=["pack/b", "pack/a"],
        floor_pack_id="user-pack/floor",
        pack_member_lookup=lookup,
    )
    assert [p.id for p in out] == ["floor-1", "pack-b-1", "pack-a-1"]


def test_flag_on_empty_floor_prepended_but_contributes_zero(monkeypatch):
    """The floor prepends even when empty; the activated packs union
    still emits their members. Regression guard for "empty floor
    contributes zero policies from floor, then activated packs union
    in" per implementation brief.
    """
    monkeypatch.setenv("MAGI_CP_PACK_CENTRIC_RUNTIME", "1")
    overrides = [_override(_make_evidence("a"))]

    def lookup(pid: str) -> list[str]:
        return {"user-pack/floor": [], "user-pack/mine": ["a"]}.get(pid, [])

    out = resolve_policies_for_hook(
        session_id="s", tenant_id="t",
        event="PreToolUse", matcher="Bash",
        overrides=overrides,
        active_packs=["user-pack/mine"],
        floor_pack_id="user-pack/floor",
        pack_member_lookup=lookup,
    )
    assert [p.id for p in out] == ["a"]


def test_flag_on_no_floor_no_active_returns_empty(monkeypatch):
    """A session with no floor + no activation gets zero policies —
    the safety net collapses to "you literally activated nothing".
    """
    monkeypatch.setenv("MAGI_CP_PACK_CENTRIC_RUNTIME", "1")
    overrides = [_override(_make_evidence("a"))]
    out = resolve_policies_for_hook(
        session_id="s", tenant_id="t",
        event="PreToolUse", matcher="Bash",
        overrides=overrides,
        active_packs=[],
        floor_pack_id=None,
        pack_member_lookup=lambda pid: [],
    )
    assert out == []


def test_flag_on_dedupes_policy_across_packs(monkeypatch):
    """A policy id present in multiple packs yields ONE output row."""
    monkeypatch.setenv("MAGI_CP_PACK_CENTRIC_RUNTIME", "1")
    overrides = [_override(_make_evidence("shared"))]

    def lookup(pid: str) -> list[str]:
        return {"pack/a": ["shared"], "pack/b": ["shared"]}.get(pid, [])

    out = resolve_policies_for_hook(
        session_id="s", tenant_id="t",
        event="PreToolUse", matcher="Bash",
        overrides=overrides,
        active_packs=["pack/a", "pack/b"],
        floor_pack_id=None,
        pack_member_lookup=lookup,
    )
    assert [p.id for p in out] == ["shared"]


def test_flag_on_floor_dedup_when_operator_also_activated_it(monkeypatch):
    """Operator explicitly activates the floor pack id — the id
    still appears exactly once (floor position wins; the second
    reference is dropped).
    """
    monkeypatch.setenv("MAGI_CP_PACK_CENTRIC_RUNTIME", "1")
    overrides = [_override(_make_evidence("f1"))]

    def lookup(pid: str) -> list[str]:
        return {"user-pack/floor": ["f1"]}.get(pid, [])

    out = resolve_policies_for_hook(
        session_id="s", tenant_id="t",
        event="PreToolUse", matcher="Bash",
        overrides=overrides,
        active_packs=["user-pack/floor"],
        floor_pack_id="user-pack/floor",
        pack_member_lookup=lookup,
    )
    assert [p.id for p in out] == ["f1"]


def test_flag_on_unknown_pack_id_silently_skipped(monkeypatch):
    """A pack id whose ``pack_member_lookup`` returns empty is
    silently skipped — matches the resolver docstring "unknown id
    dropped silently".
    """
    monkeypatch.setenv("MAGI_CP_PACK_CENTRIC_RUNTIME", "1")
    overrides = [_override(_make_evidence("a"))]

    def lookup(pid: str) -> list[str]:
        return ["a"] if pid == "pack/real" else []

    out = resolve_policies_for_hook(
        session_id="s", tenant_id="t",
        event="PreToolUse", matcher="Bash",
        overrides=overrides,
        active_packs=["pack/does-not-exist", "pack/real"],
        floor_pack_id=None,
        pack_member_lookup=lookup,
    )
    assert [p.id for p in out] == ["a"]


def test_flag_on_missing_policy_override_silently_skipped(monkeypatch):
    """A pack member id whose corresponding override is not in the
    ``overrides`` list is silently skipped — a normal state during
    prebuilt-first-then-enable authoring.
    """
    monkeypatch.setenv("MAGI_CP_PACK_CENTRIC_RUNTIME", "1")
    overrides = [_override(_make_evidence("a"))]

    def lookup(pid: str) -> list[str]:
        return ["a", "not-yet-materialised"] if pid == "pack/x" else []

    out = resolve_policies_for_hook(
        session_id="s", tenant_id="t",
        event="PreToolUse", matcher="Bash",
        overrides=overrides,
        active_packs=["pack/x"],
        floor_pack_id=None,
        pack_member_lookup=lookup,
    )
    assert [p.id for p in out] == ["a"]


def test_flag_on_filters_by_hook_event(monkeypatch):
    """A pack containing policies on multiple events emits only those
    matching the incoming ``event`` — the pack does not smear its
    members onto every hook.
    """
    monkeypatch.setenv("MAGI_CP_PACK_CENTRIC_RUNTIME", "1")
    overrides = [
        _override(_make_evidence("pre", event="PreToolUse", matcher="Bash")),
        _override(_make_evidence("post", event="PostToolUse",
                                 matcher="Bash", action="audit")),
    ]

    def lookup(pid: str) -> list[str]:
        return ["pre", "post"] if pid == "pack/x" else []

    pre_out = resolve_policies_for_hook(
        session_id="s", tenant_id="t",
        event="PreToolUse", matcher="Bash",
        overrides=overrides,
        active_packs=["pack/x"],
        floor_pack_id=None,
        pack_member_lookup=lookup,
    )
    assert [p.id for p in pre_out] == ["pre"]

    post_out = resolve_policies_for_hook(
        session_id="s", tenant_id="t",
        event="PostToolUse", matcher="Bash",
        overrides=overrides,
        active_packs=["pack/x"],
        floor_pack_id=None,
        pack_member_lookup=lookup,
    )
    assert [p.id for p in post_out] == ["post"]


def test_flag_on_context_injection_policy_extracts_top_level_event(monkeypatch):
    """ContextInjectionPolicy has ``event``+``matcher`` fields at the
    top level (no ``trigger`` sub-object). The resolver must still
    match it correctly.
    """
    monkeypatch.setenv("MAGI_CP_PACK_CENTRIC_RUNTIME", "1")
    ctx = ContextInjectionPolicy(
        id="ctx", description="t",
        event="UserPromptSubmit", matcher="*",
        template="hello", version="0.1",
    )
    overrides = [_override(ctx)]

    def lookup(pid: str) -> list[str]:
        return ["ctx"] if pid == "pack/x" else []

    out = resolve_policies_for_hook(
        session_id="s", tenant_id="t",
        event="UserPromptSubmit", matcher=None,
        overrides=overrides,
        active_packs=["pack/x"],
        floor_pack_id=None,
        pack_member_lookup=lookup,
    )
    assert [p.id for p in out] == ["ctx"]


def test_flag_on_matcher_narrower_than_pack_dropped(monkeypatch):
    """Under flag-ON a pack including a policy targeting a different
    tool matcher still respects the incoming ``matcher`` (Bash-only
    policy does not fire when the hook is Read).
    """
    monkeypatch.setenv("MAGI_CP_PACK_CENTRIC_RUNTIME", "1")
    overrides = [
        _override(_make_evidence("bash-only", matcher="Bash")),
        _override(_make_evidence("read-only", matcher="Read")),
    ]

    def lookup(pid: str) -> list[str]:
        return ["bash-only", "read-only"] if pid == "pack/x" else []

    out = resolve_policies_for_hook(
        session_id="s", tenant_id="t",
        event="PreToolUse", matcher="Read",
        overrides=overrides,
        active_packs=["pack/x"],
        floor_pack_id=None,
        pack_member_lookup=lookup,
    )
    assert [p.id for p in out] == ["read-only"]
