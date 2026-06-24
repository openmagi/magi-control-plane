"""5-tier policy source precedence.

Pattern derived (not ported) from magi-agent's 9-tier `SOURCE_PRECEDENCE`.
The 9-tier model was in-loop and included model-suggested/session-derived
sources that don't apply to an out-of-loop terminal gate. The 5-tier model
keeps only the human-authoring sources:

    platform > org > bot > user > session

- platform : magi-control-plane shipped defaults (e.g. hard safety)
- org      : organization-wide policy (set by IT/compliance)
- bot      : per-bot (Claude Code installation) policy
- user     : end-user-overridable local policy
- session  : ephemeral session-scope override

A higher-precedence source ALWAYS wins on policy-id conflict — there is no
merge semantics in v0 (kept deterministic).
"""
from __future__ import annotations
import fnmatch
from typing import Literal


PolicySource = Literal["platform", "org", "bot", "user", "session"]
SOURCE_PRECEDENCE: tuple[PolicySource, ...] = (
    "platform", "org", "bot", "user", "session",
)


def source_rank(s: str) -> int:
    """Lower rank = higher authority. 0 is platform; 4 is session."""
    try:
        return SOURCE_PRECEDENCE.index(s)   # type: ignore[arg-type]
    except ValueError as e:
        raise ValueError(f"unknown policy source: {s!r}") from e


def more_authoritative(a: PolicySource, b: PolicySource) -> PolicySource:
    return a if source_rank(a) <= source_rank(b) else b


def resolve_by_id(candidates: list[dict]) -> dict[str, dict]:
    """Group `candidates` by `id`; for each id, keep only the highest-precedence
    entry. Stable and deterministic — same input ⇒ same output.
    """
    by_id: dict[str, dict] = {}
    for c in candidates:
        cid = c["id"]
        if cid not in by_id or source_rank(c["source"]) < source_rank(by_id[cid]["source"]):
            by_id[cid] = c
    return by_id


# ── P6: tighten-only floor semantics ─────────────────────────────────
#
# Lower-precedence policies (user/session/bot) can only NARROW what a
# higher-precedence (platform/org) policy allows — never widen it. The
# pattern is ported from magi-agent's spawn capability cap: parent caps
# child, never the other way around.
#
# `tighten_against(parent, child)` returns a new policy expressing the
# intersection of permissions / allowlists, fail-closed on conflicts.
# Callers pre-resolve precedence (parent = closest higher-rank entry,
# child = the lower-rank candidate) and feed pairs in.
#
# Two operating modes:
#   - strict=False (default; back-compat) — silently collapse to the
#     tighter of the two. The caller never sees a rejected child; the
#     loosening attempt is just dropped from the result.
#   - strict=True (used by `resolve_with_tightening`) — raise
#     `LooseningError` when the child attempts to widen the parent's
#     floor. Callers catch it to log a warning and drop the child
#     rather than silently merging.
#
# Discriminator-mismatch policy (issue #1 P6 #2/#3/#4):
# tighten_against is reachable from `resolve_with_tightening`, which
# groups by `policy id` only. Author-chosen id collisions across tiers
# can therefore deliver two policies sharing an id but disagreeing on
# the discriminator fields the merge depends on
# (server / event+matcher / event / subagent_type / etc.). Silently
# coercing the child onto the parent's discriminator drops the child's
# intent AND emits an effective policy nobody authored. Every branch
# below asserts discriminator equality up-front and raises ValueError
# on mismatch; resolve_with_tightening catches that, logs, and drops.
#
# Tool-name vs pattern algebra (issue #1 P6 #12):
# magi-agent's spawn cap operates on TOOL-NAME frozensets — `Bash`
# either is in the cap set or is not. CP operates on author-supplied
# permission patterns like `Bash(*AKIA*)` which carry argument shapes.
# To preserve magi-agent's set-intersection guarantees against cosmetic
# pattern drift (case-only changes in glob args, whitespace, etc.) the
# loosening check splits the comparison into two layers:
#   1. tool-name layer (`Bash` ↔ `Bash`) — required for ANY interaction.
#   2. argument layer — `fnmatch` glob subsumption in either direction;
#      one pattern covers everything the other covers.
# When the tool names disagree the policies are disjoint — a child
# `Bash(*)` allow alongside a parent `Read(...)` deny is additive, not
# a loosening. When the tool names agree but argument globs don't
# intersect (e.g. `Bash(rm *)` vs `Bash(ls *)`), they're also disjoint.
# The verbatim-equality fallback the original implementation used would
# treat `Bash(*akia*)` (lowercase) as disjoint from `Bash(*AKIA*)`
# (uppercase) because fnmatch globs are case-insensitive on macOS — see
# the parity test file for the exact axes covered.
class LooseningError(ValueError):
    """The child policy attempts to LOOSEN the parent's floor.

    Raised by `tighten_against(strict=True)` and caught by
    `resolve_with_tightening` to drop the offending lower-tier override
    with a warning. Subclasses ValueError so existing
    `tighten_against` callers that catch ValueError still work.
    """


# ── permission-pattern algebra ───────────────────────────────────────


def _split_perm_pattern(pattern: str) -> tuple[str, str]:
    """Split a CC permission pattern into `(tool_name, glob_args)`.

    `Bash(*AKIA*)` → `("Bash", "*AKIA*")`. `Agent` (the bare disable
    rule) → `("Agent", "")`. Anything without parens collapses to
    `(<whole>, "")` so the algebra still discriminates by tool name.
    """
    if "(" not in pattern:
        return (pattern, "")
    head, _, tail = pattern.partition("(")
    if tail.endswith(")"):
        tail = tail[:-1]
    return (head, tail)


def _perm_patterns_intersect(a: str, b: str) -> bool:
    """True when patterns `a` and `b` can be loosening peers — i.e. they
    refer to the same TOOL (issue #1 P6 #12) and their argument globs
    overlap.

    Two args overlap when either is a wildcard cover of the other (the
    most common author drift: parent `Bash(rm -rf /*)` vs child
    `Bash(*)` — child wholly covers parent). `fnmatch.fnmatchcase` runs
    the glob match (no implicit case folding so deny `Bash(*AKIA*)` is
    NOT covered by an additive child allow `Bash(*akia*)` unless the
    author opted into matching case). Disjoint tools and non-intersecting
    arg shapes both yield False — those pairings are legitimately
    additive, not loosening.
    """
    a_tool, a_args = _split_perm_pattern(a)
    b_tool, b_args = _split_perm_pattern(b)
    if a_tool != b_tool:
        return False
    # Tool-name match with no arg shapes (`Agent` ↔ `Agent`, or both
    # carrying empty args) is trivially intersecting.
    if not a_args and not b_args:
        return True
    if a_args == b_args:
        return True
    # `fnmatch.fnmatchcase` checks if `name` matches `pattern`. For
    # intersection in either direction: a covers b OR b covers a.
    try:
        if fnmatch.fnmatchcase(b_args, a_args):
            return True
        if fnmatch.fnmatchcase(a_args, b_args):
            return True
    except Exception:  # pragma: no cover — fnmatch shouldn't raise.
        return False
    return False


_PERM_RANK = {"allow": 0, "ask": 1, "deny": 2}


def _perm_rank(permission: str) -> int:
    """Order permissions by tightness: deny > ask > allow.

    Issue #1 P6 #6: ask is operationally tighter than allow (it adds an
    HITL approval step). A parent allow paired with a child ask on the
    same surface must adopt the child's ask, not silently collapse to
    allow.
    """
    return _PERM_RANK.get(permission, -1)


def _is_permission_loosening(parent, child) -> bool:
    """Issue #1 P6 #1/#5/#6/#12: detect whether `child` widens `parent`
    on the same tool/arg surface area.

    Cases:
      - parent.deny + child.{allow,ask} on intersecting patterns → loosen.
      - parent.allow + child.allow where child's args wholly cover (are
        a strict superset of) parent's → loosen (widening the allow
        surface). Same-pattern allow/allow is a no-op, not loosening.
      - parent.ask + child.allow on intersecting patterns → loosen
        (drops the HITL approval step).

    Disjoint patterns are NOT loosening (additive, separate surfaces).
    See `_perm_patterns_intersect` for the subsumption semantics.
    """
    if not _perm_patterns_intersect(parent.pattern, child.pattern):
        return False
    parent_rank = _perm_rank(parent.permission)
    child_rank = _perm_rank(child.permission)
    if child_rank < parent_rank:
        # Child is strictly looser than parent on an intersecting
        # surface (e.g. parent deny vs child allow/ask, parent ask vs
        # child allow). Always a loosening.
        return True
    if (
        parent.permission == "allow"
        and child.permission == "allow"
        and parent.pattern != child.pattern
    ):
        # Same-rank allow widening: child's args wholly cover parent's
        # (strict superset). `_perm_patterns_intersect` already proved
        # one covers the other; only flag the case where child does the
        # covering — that's the widening direction.
        _, p_args = _split_perm_pattern(parent.pattern)
        _, c_args = _split_perm_pattern(child.pattern)
        if not c_args:
            # Tool-only pattern `Bash` covers any `Bash(...)` instance.
            return bool(p_args)
        if p_args and fnmatch.fnmatchcase(p_args, c_args):
            return True
    return False


def _is_mcp_loosening(parent, child) -> bool:
    """Issue #1 P6 #2: pre-condition is server equality; mismatched
    servers are a discriminator error, not a loosening event. The
    branch in tighten_against raises ValueError ahead of this so
    this predicate sees same-server pairs only.
    """
    if parent.server != child.server:
        return False
    if parent.action == "deny" and child.action == "allow":
        return True
    return False


# Hoisted (issue #1 P6 #8): shared between `_is_evidence_loosening` and
# the EvidencePolicy tighten branch (dedup of merged requires[]) so both
# agree on what counts as the same requirement.
def _req_key(r):
    """Discriminator + the bytes that actually identify the requirement."""
    if r.kind == "step":
        return ("step", r.step, r.verdict)
    if r.kind == "regex":
        return ("regex", r.pattern)
    if r.kind == "llm_critic":
        return ("llm_critic", r.criterion)
    if r.kind == "shacl":
        return ("shacl", r.shape_ttl)
    return (r.kind,)


_EVIDENCE_ACTION_ORDER = {"block": 0, "ask": 1, "audit": 2}


def _evidence_action_rank(action: str) -> int:
    """Block tightest, audit loosest. Unknown actions raise (issue #1
    fix-cycle non-blocking #5) so the merge path can't quietly accept a
    bypass-validation child.
    """
    if action not in _EVIDENCE_ACTION_ORDER:
        raise ValueError(
            f"EvidencePolicy: unknown action {action!r}; "
            f"expected one of {tuple(_EVIDENCE_ACTION_ORDER)}"
        )
    return _EVIDENCE_ACTION_ORDER[action]


def _is_evidence_loosening(parent, child) -> bool:
    """Child evidence policy loosens if:
      - it weakens the action (block→ask→audit), OR
      - its requires[] is NOT a superset of parent.requires[].

    Superset semantics: every parent requirement must appear (by
    structural equality on its discriminator fields) in child.requires.
    This matches the spec test:
      - 'org requires citation_verify; user adds shacl' → child carries
        BOTH → superset → accepted.
      - 'org requires citation_verify; user changes to evidence_ref shacl
        only' → citation_verify missing from child → NOT superset →
        rejected.
    """
    if _evidence_action_rank(child.action) > _evidence_action_rank(parent.action):
        return True
    parent_keys = {_req_key(r) for r in parent.requires}
    child_keys = {_req_key(r) for r in child.requires}
    return not parent_keys.issubset(child_keys)


def is_loosening(parent, child) -> bool:
    """Pure predicate: would `child` loosen `parent` if both were applied?

    Mirrors the per-archetype rules embedded in `tighten_against`. Used
    by `resolve_with_tightening` to pre-filter candidates without
    constructing intermediate merged policies.
    """
    from .ir import (
        ContextInjectionPolicy, EvidencePolicy, McpGatingPolicy,
        PermissionPolicy, SubagentPolicy,
    )
    if type(parent) is not type(child):
        return False
    if isinstance(parent, PermissionPolicy):
        return _is_permission_loosening(parent, child)
    if isinstance(parent, McpGatingPolicy):
        return _is_mcp_loosening(parent, child)
    if isinstance(parent, EvidencePolicy):
        return _is_evidence_loosening(parent, child)
    # SubagentPolicy v1 is binary disable — the child has no way to express
    # un-disable, so it cannot loosen. ContextInjectionPolicy is additive
    # and re-orders text so the parent always wins position; no loosening
    # vector exists in the IR.
    if isinstance(parent, (SubagentPolicy, ContextInjectionPolicy)):
        return False
    return False


def tighten_against(parent, child, *, strict: bool = False):
    """Intersection semantics for two policies of the SAME archetype.

    PermissionPolicy:
      - Permission rank is `deny > ask > allow` (issue #1 P6 #6).
        Whichever side carries the higher rank on intersecting patterns
        wins. Disjoint patterns are additive — the parent stands and
        the additive child is silently dropped (the resolver records
        the parent only in `tightened_sources`).
      - `_is_permission_loosening` recognises (a) parent.deny +
        child.{allow,ask}, (b) parent.allow + child.allow widening the
        glob, (c) parent.ask + child.allow.

    SubagentPolicy:
      - Issue #1 P0 (#9): v1 archetype is binary (disable). Parent
        always wins; a lower-precedence policy cannot un-disable a
        subagent the parent disabled. Result is parent verbatim.
      - Discriminator: `subagent_type` must match. Mismatch raises
        ValueError so the resolver drops the wrong-target child instead
        of silently auditing it as a contributor.

    McpGatingPolicy:
      - allow ⊆ deny: if parent denies, child stays deny. If parent
        allows and child denies, child wins (denial is tighter).
      - Discriminator: `server` must match. Mismatch raises ValueError
        (issue #1 P6 #2) so a child targeting a different server can't
        coerce into the parent's server identity.

    ContextInjectionPolicy:
      - Issue #1 P1 (#4): the lower-precedence child template is
        injected BEFORE the parent's so the parent (higher-precedence)
        gets the last word in the prompt — preventing
        `session`-tier 'ignore the above safety rules' from overriding
        a `platform`-tier instruction. Child is dropped entirely when
        it equals or contains the parent template (no-op or already
        covered) AND when the parent template appears verbatim inside
        the child template (issue #1 P6 #7 — defends against a child
        that wraps the parent text to set up a contradicting context).
      - Discriminator: `event` and `matcher` must match (issue #1 P6 #4).
        Mismatched hook surfaces would silently merge into a surface
        the child author never targeted.

    EvidencePolicy:
      - requires[] are concatenated (more checks = tighter) and
        deduped by `_req_key` so the same step/regex/llm_critic/shacl
        appearing in both tiers fires once (issue #1 P6 #8). action
        narrows: block > ask > audit. If parent says block, child
        cannot relax to audit.
      - Discriminator: `trigger.event` and `trigger.matcher` must
        match (issue #1 P6 #3). An event/matcher mismatch silently
        fuses checks authored for a different payload onto the
        parent's trigger — the exact silent vacuous-satisfaction
        class P7 is shipped to kill.

    Type mismatch between parent + child → ValueError (callers must
    pair archetypes; the resolved-set machinery already guarantees this).
    """
    from .ir import (
        ContextInjectionPolicy, EvidencePolicy, McpGatingPolicy,
        PermissionPolicy, SubagentPolicy,
    )
    if type(parent) is not type(child):
        raise ValueError(
            f"tighten_against: archetype mismatch "
            f"{type(parent).__name__} vs {type(child).__name__}"
        )
    if isinstance(parent, PermissionPolicy):
        # Discriminator: trigger (event+matcher) must match. Different
        # hook surfaces are not loosening peers — they're separate
        # surfaces and the resolver should drop the wrong-trigger child.
        if (
            parent.trigger.event != child.trigger.event
            or parent.trigger.matcher != child.trigger.matcher
        ):
            raise ValueError(
                f"PermissionPolicy '{child.id}': discriminator mismatch — "
                f"parent trigger {parent.trigger.event}/{parent.trigger.matcher} "
                f"vs child {child.trigger.event}/{child.trigger.matcher}"
            )
        if strict and _is_permission_loosening(parent, child):
            raise LooseningError(
                f"PermissionPolicy '{child.id}': child permission "
                f"{child.permission!r} on pattern {child.pattern!r} "
                f"would loosen parent {parent.permission} on "
                f"pattern {parent.pattern!r}"
            )
        # No loosening — the tighter side wins. On intersecting patterns
        # this is whichever has the higher _perm_rank; on disjoint
        # patterns the parent floor stands (the child is silently
        # absorbed; resolver still records the source via accepted_sources
        # only on a real merge).
        if not _perm_patterns_intersect(parent.pattern, child.pattern):
            return parent
        return parent if _perm_rank(parent.permission) >= _perm_rank(child.permission) else child
    if isinstance(parent, SubagentPolicy):
        # Issue #1 P0 (#9): SubagentPolicy is binary (disable). Parent
        # disable cannot be undone by a lower-precedence child.
        # Lists are rejected at construction time; this branch only
        # ever sees empty allowlists.
        # Issue #1 fix-cycle non-blocking #1: subagent_type discriminator
        # check so a child against a different subagent can't masquerade
        # as a tightening of the parent's subagent.
        if parent.subagent_type != child.subagent_type:
            raise ValueError(
                f"SubagentPolicy '{child.id}': discriminator mismatch — "
                f"parent subagent_type {parent.subagent_type!r} "
                f"vs child {child.subagent_type!r}"
            )
        return parent
    if isinstance(parent, McpGatingPolicy):
        # Issue #1 P6 #2: server discriminator MUST match before any
        # merge / loosening check. Resolver groups only by id, so the
        # author can pair `id=mcp/x` against server=github (parent)
        # with server=slack (child) — without this check the child's
        # slack intent is silently coerced onto github, producing a
        # phantom deny.
        if parent.server != child.server:
            raise ValueError(
                f"McpGatingPolicy '{child.id}': discriminator mismatch — "
                f"parent server {parent.server!r} vs child {child.server!r}"
            )
        # deny always wins.
        if parent.action == "deny" or child.action == "deny":
            if strict and _is_mcp_loosening(parent, child):
                raise LooseningError(
                    f"McpGatingPolicy '{child.id}': child action 'allow' "
                    f"on server {child.server!r} would loosen parent deny"
                )
            return McpGatingPolicy(
                id=child.id, description=child.description,
                server=parent.server, action="deny", version=child.version,
            )
        return parent
    if isinstance(parent, ContextInjectionPolicy):
        # Issue #1 P6 #4: event/matcher discriminator MUST match.
        # Resolver groups by id; without this check a child authored
        # for SessionStart could merge under the parent's
        # UserPromptSubmit trigger, injecting text into a hook the
        # child author never targeted.
        if parent.event != child.event or parent.matcher != child.matcher:
            raise ValueError(
                f"ContextInjectionPolicy '{child.id}': discriminator "
                f"mismatch — parent {parent.event}/{parent.matcher!r} "
                f"vs child {child.event}/{child.matcher!r}"
            )
        # Issue #1 P1 (#4): tighten-only semantics. The lower-precedence
        # child template is prepended so the parent's text appears LAST
        # in the prompt — last-instruction-wins is the assumed model
        # behaviour, so a `session`-tier 'ignore the above' injected
        # below a `platform` rule can't override it.
        # Drop the child if it's redundant (no-op) OR if the parent
        # appears verbatim inside the child (issue #1 P6 #7 — defends
        # against a child that quotes the parent and wraps it with
        # contradicting context). Defence in depth, not a true
        # security boundary; the parent-last position is the primary
        # guarantee.
        if (
            not child.template
            or child.template == parent.template
            or child.template in parent.template
            or parent.template in child.template
        ):
            return parent
        merged_template = child.template + "\n\n" + parent.template
        return ContextInjectionPolicy(
            id=child.id, description=child.description,
            event=parent.event, matcher=parent.matcher,
            template=merged_template, version=child.version,
        )
    if isinstance(parent, EvidencePolicy):
        # Issue #1 P6 #3: trigger.event+matcher discriminator MUST match.
        # An event/matcher-mismatched child silently fuses checks
        # authored for a different payload onto the parent's trigger —
        # the same silent vacuous-satisfaction class the P7 payload
        # schema is built to kill.
        if (
            parent.trigger.event != child.trigger.event
            or parent.trigger.matcher != child.trigger.matcher
        ):
            raise ValueError(
                f"EvidencePolicy '{child.id}': discriminator mismatch — "
                f"parent trigger {parent.trigger.event}/{parent.trigger.matcher} "
                f"vs child {child.trigger.event}/{child.trigger.matcher}"
            )
        if strict and _is_evidence_loosening(parent, child):
            raise LooseningError(
                f"EvidencePolicy '{child.id}': child requires[] is not a "
                f"superset of parent's, or action {child.action!r} weakens "
                f"parent action {parent.action!r}"
            )
        # action narrows: block > ask > audit. The parent floor stays
        # unless the child legitimately tightens (and isn't loosening
        # per the gate above).
        chosen_action = parent.action
        if _evidence_action_rank(child.action) < _evidence_action_rank(parent.action):
            chosen_action = child.action
        # Concatenate with parent-first ordering, deduping by
        # `_req_key` so the same requirement showing up in both tiers
        # fires once at runtime (issue #1 P6 #8). Without dedup,
        # llm_critic / step requires would run twice per event,
        # doubling LLM cost and tripping evidence-ledger uniqueness.
        seen: set = set()
        merged_requires = []
        for r in list(parent.requires) + list(child.requires):
            key = _req_key(r)
            if key in seen:
                continue
            seen.add(key)
            merged_requires.append(r)
        return EvidencePolicy(
            id=child.id, description=child.description,
            trigger=parent.trigger,
            sentinel_re=child.sentinel_re or parent.sentinel_re,
            requires=merged_requires,
            action=chosen_action,
            on_signature_invalid=parent.on_signature_invalid,
            gate_binary=parent.gate_binary,
            version=child.version,
        )
    raise ValueError(f"tighten_against: unsupported type {type(parent).__name__}")
