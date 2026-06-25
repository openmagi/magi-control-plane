"""D75: Policy Pack feature.

A pack is a NAMED GROUP of policy ids that share an operator context
(research mode, coding session, compliance audit). One toggle on the
pack card cascades to every member.

Why this is its own concept (not just a tag on Policy):

  - Operators commonly want a single switch for an "intent" (e.g. lock
    down a research session). Tagging policies and asking the operator
    to bulk-toggle by tag is the same shape with worse ergonomics.
  - A pack can mix prebuilt ids and user policy ids. Membership is the
    pack's own list, not a query.
  - Built-in packs ship as immutable curated bundles; the operator can
    create user packs through the dashboard for their own contexts.

Pack status (`all` / `partial` / `none`) is computed against the live
policy store at request time. The pack object itself is membership +
metadata; enabled-state is derived, never persisted on the pack.

Membership-vs-state separation matters for the "shared-member"
question: if policy P is in pack A and pack B, disabling A leaves P
enabled iff B is still enabled. The cloud's enable/disable handlers
implement "blunt cascade" — every member's enabled flag is set to the
pack toggle's target. The brief explicitly allows this choice; the PR
notes call it out so the simpler-test trade is visible.

Built-in packs and their member-policy IRs live HERE so a fresh install
ships the strict-block bundle without a separate seed step. The other
four packs reference `prebuilt/...` ids: when the operator enables
those packs, the cloud routes each member through the prebuilt enable
path so the materialized IR + lifecycle endorsement chain matches what
the prebuilt toggle already does.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

from .ir import EvidencePolicy, EvidenceReq, Trigger, policy_to_dict


PackSource = Literal["builtin", "user"]
PackStatus = Literal["all", "partial", "none"]


class PolicyPack(TypedDict, total=False):
    """One pack entry.

    `id`             : stable slug. `pack/<slug>` for built-ins,
                       `user-pack/<slug>` for user packs.
    `name`           : operator-facing label (locale-resolved when KO).
    `description`    : one-sentence "what this bundles in practice".
    `policy_ids`     : ordered list of member policy ids. The dashboard
                       renders members in this order on the expanded
                       view so an operator scanning the pack sees the
                       same ordering every visit.
    `source`         : "builtin" (immutable membership, KO+EN catalog
                       copy) or "user" (operator-authored, mutable
                       through PUT).
    `status`         : derived against the live policy store at request
                       time. Not persisted on disk.
    `member_count`   : len(policy_ids). Convenience field so the
                       dashboard card avoids a second array walk.
    `enabled_count`  : number of members currently enabled in the
                       store. Used to render the "Partial 3/5" badge.
    """

    id: str
    name: str
    description: str
    policy_ids: list[str]
    source: PackSource
    status: PackStatus
    member_count: int
    enabled_count: int


@dataclass(frozen=True)
class _BuiltinPackSpec:
    """Authoring-time tuple for a built-in pack.

    `inline_policies` carries IR dicts the pack OWNS (the strict-block
    bundle is the only case today). `policy_ids` references already-
    materialized ids (prebuilt/... slugs). The two lists are merged into
    `policy_ids` on `all_builtin_packs()`; the inline IRs are exposed
    separately through `inline_policy_for(member_id)` so the cloud
    enable handler can persist them as ordinary EvidencePolicy rows.
    """

    id: str
    name_ko: str
    name_en: str
    description_ko: str
    description_en: str
    prebuilt_refs: tuple[str, ...]
    inline_policies: tuple[tuple[str, EvidencePolicy], ...] = ()


def _strict_block_bash_privilege() -> EvidencePolicy:
    """strict-block: a PreToolUse + Bash + block triple gated by the
    privilege_scan verifier. The prebuilt template defaults to `audit`;
    strict-block ships the IR with `block` so the pack signals intent
    without an operator hand-edit. Matrix-legal (PreToolUse + tool +
    block is in LEGAL_COMBINATIONS).
    """
    return EvidencePolicy(
        id="pack/strict-block/privilege-bash",
        description=(
            "Block Bash invocations whose body trips the privilege-scan "
            "verifier (attorney-client markers, work-product flags, "
            "Korean RRN patterns). Strict-block override of the audit "
            "default prebuilt."
        ),
        trigger=Trigger(host="claude-code", event="PreToolUse", matcher="Bash"),
        sentinel_re=None,
        requires=[EvidenceReq(kind="step", step="privilege_scan",
                              verdict="pass")],
        action="block",
        on_signature_invalid="deny",
        gate_binary="/usr/local/bin/magi-gate.sh",
        version="0.1",
    )


def _strict_block_source_allowlist() -> EvidencePolicy:
    """strict-block: source allowlist already defaults to block, but
    strict-block re-binds it under the pack id so the pack owns the
    enable surface (no cross-pack share with the prebuilt toggle).
    """
    return EvidencePolicy(
        id="pack/strict-block/source-allowlist-webfetch",
        description=(
            "Block WebFetch when the destination host is not in the "
            "configured source allowlist. Strict-block bundle."
        ),
        trigger=Trigger(host="claude-code", event="PreToolUse",
                        matcher="WebFetch"),
        sentinel_re=None,
        requires=[EvidenceReq(kind="step", step="source_allowlist",
                              verdict="pass")],
        action="block",
        on_signature_invalid="deny",
        gate_binary="/usr/local/bin/magi-gate.sh",
        version="0.1",
    )


def _strict_block_user_prompt_injection() -> EvidencePolicy:
    """strict-block: block UserPromptSubmit when prompt-injection
    markers are present. PreToolUse + matcher=WebFetch is restricted
    to PostToolUse for prompt_injection_screen by the prebuilt; the
    strict-block bundle binds to UserPromptSubmit + wildcard + block
    via a step that resolves to `unresolved-legacy` if the descriptor
    surface does not endorse the lifecycle. The pack ships its OWN
    IR (not the prebuilt) so the audit-default cannot leak into the
    strict-block signal.

    UserPromptSubmit + wildcard + block IS in LEGAL_COMBINATIONS (see
    matrix._build_legal()). We bind to `prompt_injection_screen` even
    though the prebuilt fires on PostToolUse — at boot the descriptor
    surface is checked via `_assert_all_validate()` so a future
    descriptor narrowing will fail import-time, not silently ship an
    inert gate.
    """
    return EvidencePolicy(
        id="pack/strict-block/prompt-injection-userprompt",
        description=(
            "Block the user's submitted prompt when prompt-injection "
            "markers are present (override verbs, role-tag injection, "
            "jailbreak phrases). Strict-block bundle."
        ),
        trigger=Trigger(host="claude-code", event="UserPromptSubmit",
                        matcher="*"),
        sentinel_re=None,
        requires=[EvidenceReq(kind="step", step="prompt_injection_screen",
                              verdict="pass")],
        action="block",
        on_signature_invalid="deny",
        gate_binary="/usr/local/bin/magi-gate.sh",
        version="0.1",
    )


_BUILTIN_PACK_SPECS: tuple[_BuiltinPackSpec, ...] = (
    _BuiltinPackSpec(
        id="pack/research-mode",
        name_ko="리서치 모드",
        name_en="Research mode",
        description_ko=(
            "리서치 세션에 어울리는 묶음. 출처 화이트리스트, 인용 검증, "
            "프롬프트 인젝션 차단을 한 번에 활성화합니다."
        ),
        description_en=(
            "Bundle for a research session. Enables citation verification, "
            "source allowlist, and prompt-injection screening together."
        ),
        prebuilt_refs=(
            "prebuilt/citation-verify-at-final",
            "prebuilt/source-allowlist-webfetch",
            "prebuilt/prompt-injection-webfetch",
        ),
    ),
    _BuiltinPackSpec(
        id="pack/coding-safety",
        name_ko="코딩 안전",
        name_en="Coding safety",
        description_ko=(
            "코딩 세션을 위한 묶음. Bash 호출의 권한 스캐닝과 최종 응답의 "
            "구조화 출력 검사를 한 번에 활성화합니다."
        ),
        description_en=(
            "Bundle for a coding session. Enables privilege scanning on "
            "Bash calls and structured-output checks on the final answer."
        ),
        prebuilt_refs=(
            "prebuilt/privilege-scan-bash",
            "prebuilt/structured-output-at-final",
        ),
    ),
    _BuiltinPackSpec(
        id="pack/compliance-audit",
        name_ko="컴플라이언스 감사",
        name_en="Compliance audit",
        description_ko=(
            "감사 전용 묶음. 5 개 프리빌트 모두 audit 모드로 활성화해 "
            "트래픽 가시성을 확보합니다 (차단 없음)."
        ),
        description_en=(
            "Audit-only bundle. Enables all 5 prebuilts in audit mode "
            "for full traffic visibility (no blocking)."
        ),
        prebuilt_refs=(
            "prebuilt/citation-verify-at-final",
            "prebuilt/privilege-scan-bash",
            "prebuilt/source-allowlist-webfetch",
            "prebuilt/structured-output-at-final",
            "prebuilt/prompt-injection-webfetch",
        ),
    ),
    _BuiltinPackSpec(
        id="pack/permissive-observe",
        name_ko="관찰 모드",
        name_en="Permissive observe",
        description_ko=(
            "처음 도입하는 운영자를 위한 가시성 우선 묶음. "
            "감사 묶음과 동일 정책을 켜되, 운영자가 트래픽을 먼저 "
            "관찰하고 싶을 때 추천합니다."
        ),
        description_en=(
            "Visibility-first bundle for first-time operators. Same "
            "members as compliance-audit; recommended when you want "
            "to observe traffic before tightening any rule."
        ),
        prebuilt_refs=(
            "prebuilt/citation-verify-at-final",
            "prebuilt/privilege-scan-bash",
            "prebuilt/source-allowlist-webfetch",
            "prebuilt/structured-output-at-final",
            "prebuilt/prompt-injection-webfetch",
        ),
    ),
    _BuiltinPackSpec(
        id="pack/strict-block",
        name_ko="엄격 차단",
        name_en="Strict block",
        description_ko=(
            "차단 우선의 큐레이션 묶음. 프리빌트가 audit 기본인 부분을 "
            "block 으로 다시 묶어, 권한 스캔/소스 화이트리스트/프롬프트 "
            "인젝션을 모두 차단 모드로 활성화합니다."
        ),
        description_en=(
            "Curated block-first bundle. Re-binds the audit-default "
            "prebuilts as block-mode policies covering privilege scan, "
            "source allowlist, and prompt-injection screening."
        ),
        prebuilt_refs=(),
        inline_policies=(
            ("pack/strict-block/privilege-bash",
             _strict_block_bash_privilege()),
            ("pack/strict-block/source-allowlist-webfetch",
             _strict_block_source_allowlist()),
            ("pack/strict-block/prompt-injection-userprompt",
             _strict_block_user_prompt_injection()),
        ),
    ),
)


def _builtin_member_ids(spec: _BuiltinPackSpec) -> list[str]:
    out: list[str] = list(spec.prebuilt_refs)
    for member_id, _policy in spec.inline_policies:
        out.append(member_id)
    return out


def _spec_to_pack(
    spec: _BuiltinPackSpec, *, locale: str, enabled_ids: set[str],
) -> PolicyPack:
    member_ids = _builtin_member_ids(spec)
    enabled_in_pack = sum(1 for m in member_ids if m in enabled_ids)
    if member_ids and enabled_in_pack == len(member_ids):
        status: PackStatus = "all"
    elif enabled_in_pack == 0:
        status = "none"
    else:
        status = "partial"
    return {
        "id": spec.id,
        "name": spec.name_ko if locale == "ko" else spec.name_en,
        "description": (
            spec.description_ko if locale == "ko" else spec.description_en
        ),
        "policy_ids": member_ids,
        "source": "builtin",
        "status": status,
        "member_count": len(member_ids),
        "enabled_count": enabled_in_pack,
    }


def all_builtin_packs(
    *, locale: str = "en", enabled_ids: set[str] | None = None,
) -> list[PolicyPack]:
    """Return the 5 built-in packs in stable order.

    `enabled_ids` is the set of policy ids currently enabled in the
    tenant policy store. Pass an empty set / None when only the catalog
    metadata matters (e.g. tests checking shape); the cloud route
    passes the live set so each pack's `status` is accurate.
    """
    ids = enabled_ids or set()
    return [_spec_to_pack(spec, locale=locale, enabled_ids=ids)
            for spec in _BUILTIN_PACK_SPECS]


def builtin_pack_spec_by_id(pack_id: str) -> _BuiltinPackSpec | None:
    for spec in _BUILTIN_PACK_SPECS:
        if spec.id == pack_id:
            return spec
    return None


def inline_policy_for(pack_id: str, member_id: str) -> EvidencePolicy | None:
    """Return the EvidencePolicy a built-in pack OWNS for the given
    member id, or None if `member_id` is not pack-owned (e.g. a
    prebuilt reference). The cloud enable handler uses this to
    materialize strict-block's IRs through the same persistence path
    prebuilts use.
    """
    spec = builtin_pack_spec_by_id(pack_id)
    if spec is None:
        return None
    for mid, policy in spec.inline_policies:
        if mid == member_id:
            return policy
    return None


def compute_status(
    member_ids: list[str], enabled_ids: set[str],
) -> tuple[PackStatus, int]:
    """Helper for user-pack status. Returns (status, enabled_count)."""
    if not member_ids:
        return "none", 0
    enabled = sum(1 for m in member_ids if m in enabled_ids)
    if enabled == len(member_ids):
        return "all", enabled
    if enabled == 0:
        return "none", 0
    return "partial", enabled


def user_pack_to_dict(
    pack_id: str,
    name: str,
    description: str,
    policy_ids: list[str],
    enabled_ids: set[str],
) -> PolicyPack:
    status, enabled = compute_status(policy_ids, enabled_ids)
    return {
        "id": pack_id,
        "name": name,
        "description": description,
        "policy_ids": list(policy_ids),
        "source": "user",
        "status": status,
        "member_count": len(policy_ids),
        "enabled_count": enabled,
    }


def _assert_all_inline_validate() -> None:
    """Module-import guard: every inline IR a built-in pack owns must
    construct cleanly + serialize round-trip. Matches the prebuilt
    pattern (boot-time gate keeps a future matrix tweak from silently
    breaking the strict-block bundle).
    """
    for spec in _BUILTIN_PACK_SPECS:
        for _member_id, policy in spec.inline_policies:
            policy_to_dict(policy)


_assert_all_inline_validate()


__all__ = [
    "PolicyPack",
    "PackSource",
    "PackStatus",
    "all_builtin_packs",
    "builtin_pack_spec_by_id",
    "inline_policy_for",
    "compute_status",
    "user_pack_to_dict",
]
