"""D53b - policy dry-run replay.

Given a draft PolicyIR and a window of recent ledger records, return how
many of those records WOULD have triggered the policy's action if the
policy had been enabled when each record was produced.

Replays are read-only; nothing is persisted. The endpoint is gated by
the same admin key as the rest of policies CRUD and never writes to
the ledger or HITL queue.

This module is a SEMANTIC mirror of the runtime contract, not an
implementation reuse:

  The runtime distributes the requires[] combination across the
  `gate_binary` shell script the operator deploys; `/verify_inline`
  and `/verify/{step}` only evaluate ONE requires entry at a time and
  write one ledger row per entry. The cloud has no single Python
  evaluator for the AND-of-pass-conditions combination. This file
  re-implements that combination for offline replay against literal
  ledger bodies; if `gate_binary` ever switches its short-circuit
  semantics the dry-run will silently diverge until the contract pin
  test in tests/test_policies_dry_run.py is updated.

Decision model:
  - Only `EvidencePolicy` participates in replay today.
    Permission / Mcp / Subagent / ContextInjection policies are
    declarative and compile to managed-settings (no requires[] to
    re-run); they return matched=0 with a `skipped` reason rather
    than a fake hit count.
  - For each ledger row inside the trigger frame (event + matcher),
    we re-evaluate every `requires[]` entry against `row.body`. A
    requires entry "passes" iff:
        kind=step       : row.body['step'] == req.step
                          AND row.body['verdict'] == req.verdict
        kind=regex      : re.search(req.pattern, payload_snapshot(row))
                          where payload_snapshot is the runtime-written
                          `body['__payload_snapshot__']` snapshot. Rows
                          that lack the snapshot return INDETERMINATE
                          (regex-not-replayable-from-ledger), not a
                          silent fail.
        kind=llm_critic : INDETERMINATE (no LLM round-trip in dry-run;
                          surfaced via `indeterminate` counter and
                          per-policy `skipped_reason` when every
                          requires entry is non-replayable).
        kind=shacl      : INDETERMINATE (pyshacl validation is not
                          cheap enough to fan out across a 24h window;
                          same treatment as llm_critic).
  - The policy's action would fire when at least one requires entry
    fails (matches the runtime gate semantics: requires[] is an AND
    of pass conditions; any fail short-circuits to "action"). An
    indeterminate entry does NOT trigger the action; we count rows
    that had at least one indeterminate entry in the `indeterminate`
    counter so the dashboard can surface them.
  - An empty requires[] is the "unconditional signal" archetype: the
    policy fires on EVERY row that matches the trigger frame.

Multi-requires limitation:

  The runtime fires `gate_binary` once per (subject, payload_hash);
  that one invocation calls `/verify/{step}` (or `/verify_inline`) N
  times for the same payload (once per requires entry) and combines
  the N verdicts inside the shell script. The offline replay does NOT
  reconstruct that fan-out: it replays one row at a time and pretends
  each row carries a complete picture of all requires entries the
  policy needs. For policies with len(requires) > 1, this is
  STRUCTURALLY WRONG — a row may have logged only one of the two
  steps for that payload, and the replay would see "step B did not
  appear in this row → requires B failed → action fires" when in
  reality step B may have passed on a sibling row sharing the same
  payload_hash. Until per-payload row joining lands, policies with
  multiple step-kind requires entries are SKIPPED with a
  `multi-requires-not-replayable` reason rather than silently
  miscounted.

Output schema (mirrors what cloud/app.py wraps in the response):
    {
      total_records: int,        # rows we considered (trigger-matched)
      matched: int,              # rows where action would have fired
      indeterminate: int,        # rows where >=1 requires was offline-
                                 # unevaluable (llm_critic/shacl/regex
                                 # without payload snapshot)
      by_verdict: {              # row.body['verdict'] distribution
        pass: int, fail: int, deny: int,
        review: int, needs_review: int, not_applicable: int,
        unknown: int,            # closed-set; novel strings collapse here
      },
      by_action: {block,ask,audit,strip: int},
      sample_matched_ids: [int], # newest-first ids of matched rows;
                                 # caller redacts the bodies via D50
      skipped_reason: str | None,
      skipped_kinds: [str],      # requires-entry kinds that contributed
                                 # to indeterminate (subset of
                                 # {"llm_critic","shacl","regex"})
    }

`skipped_reason` value enum:
  - archetype-not-dry-runnable         non-evidence archetype
  - no-records-in-trigger-frame        window had no matching rows
  - no-frame-metadata-on-rows          rows in window lack hook_event /
                                       matcher (predates runtime
                                       contract; cannot scope to the
                                       proposed policy)
  - multi-requires-not-replayable      policy.requires has >1 entry;
                                       per-payload join not implemented
  - requires-indeterminate             every requires entry is
                                       llm_critic / shacl / regex
                                       without a payload snapshot

The caller (the FastAPI route) is responsible for:
  - validating the IR (we trust the dataclass __post_init__),
  - querying the ledger window,
  - redacting `sample_matched` payloads via run_redaction.py.

This module is pure logic (no SQLAlchemy, no FastAPI imports) so the
unit test can drive it with literal LedgerEntry-shaped dicts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .ir import AnyPolicy, EvidencePolicy, EvidenceReq
from .payload_projection import project_snapshot_for_regex
from .verdicts import LEDGER_VERDICTS, LEDGER_VERDICTS_ORDERED


# Action archetypes the IR supports. `strip` is reserved for a future
# verifier-protocol mutation channel; we keep the bucket so the
# frontend pill row renders zero for it without a None check.
_ACTION_BUCKETS: tuple[str, ...] = ("block", "ask", "audit", "strip")


# Reserved key on the ledger row body where the runtime stashes the
# original verifier payload text (regex/llm_critic surfaces only). When
# present, dry-run regex requires are evaluated against the snapshot
# (mirroring what /verify_inline's regex saw at runtime). When ABSENT,
# regex requires are marked indeterminate per the docstring's contract
# — silently scanning the verdict envelope JSON would systematically
# fail to match patterns authors wrote against the original payload.
_PAYLOAD_SNAPSHOT_KEY = "__payload_snapshot__"


# Tri-state evaluation result for one (row, requires-entry) pair.
# `PASS` and `FAIL` are the normal short-circuit branches; `INDET` is
# how we mark llm_critic / shacl / regex-without-snapshot so the
# combine step can both (a) refuse to fire the action and (b) bump the
# indeterminate counter.
_PASS = "pass"
_FAIL = "fail"
_INDET = "indeterminate"


@dataclass
class DryRunResult:
    total_records: int
    matched: int
    # Rows whose evaluation included at least one indeterminate
    # requires entry. These rows do NOT contribute to `matched` (we
    # refuse to claim the action would have fired without evidence)
    # but the dashboard surfaces the count so the operator knows the
    # replay was partial.
    indeterminate: int
    by_verdict: dict[str, int]
    by_action: dict[str, int]
    # Newest-first list of ledger row ids whose action would have
    # fired. The route layer turns these into redacted previews.
    sample_matched_ids: list[int]
    # When the policy archetype does not participate in dry-run (or
    # the trigger never matched any window row) the route surfaces
    # this so the dashboard can render a "skipped" pill instead of a
    # misleading "0 of N would have blocked" line.
    skipped_reason: str | None
    # Requires-entry kinds that produced INDETERMINATE results during
    # this replay (subset of {"llm_critic", "shacl", "regex"}). The
    # dashboard renders a "these requires were not evaluated offline:
    # ..." disclosure so the headline number isn't read as gospel.
    skipped_kinds: list[str] = field(default_factory=list)


def evaluate_dry_run(
    policy: AnyPolicy,
    rows: Iterable[object],
    *,
    sample_limit: int = 3,
) -> DryRunResult:
    """Replay `policy` against `rows` (most-recent-first ledger records).

    `rows` is an iterable of objects with attributes `id: int`,
    `ts: int`, `body: dict`. We accept any duck-typed shape so the
    unit test can pass simple namespaces.

    `sample_limit` caps `sample_matched_ids` (the route layer redacts
    the bodies; we return ids so the unit test can assert ordering
    without a redactor dependency).
    """
    by_verdict = {b: 0 for b in LEDGER_VERDICTS_ORDERED}
    by_verdict["unknown"] = 0
    by_action = {a: 0 for a in _ACTION_BUCKETS}

    # Non-evidence archetypes do not have a requires[] to re-run.
    # Declarative policies compile to managed-settings primitives; the
    # only honest dry-run for them is "you would have seen this rule
    # in your settings.json", which the caller already knows because
    # they typed it. Skip with a reason so the dashboard renders an
    # explanation instead of a fake 0%.
    if not isinstance(policy, EvidencePolicy):
        return DryRunResult(
            total_records=0,
            matched=0,
            indeterminate=0,
            by_verdict=by_verdict,
            by_action=by_action,
            sample_matched_ids=[],
            skipped_reason="archetype-not-dry-runnable",
        )

    event = policy.trigger.event
    matcher = policy.trigger.matcher
    action = policy.action

    # P1 #3: multi-requires policies cannot be honestly replayed
    # one row at a time. The runtime fires gate_binary once per
    # (subject, payload_hash) and combines N verdicts inside the
    # shell script; the offline replay does not reconstruct that
    # join. For policies with len(requires) > 1, we refuse to
    # produce a per-row count and surface the limitation instead.
    if len(policy.requires) > 1:
        return DryRunResult(
            total_records=0,
            matched=0,
            indeterminate=0,
            by_verdict=by_verdict,
            by_action=by_action,
            sample_matched_ids=[],
            skipped_reason="multi-requires-not-replayable",
        )

    # Pre-compile the regex pattern for kind=regex entries once per
    # dry-run (the same pattern fans out across every window row).
    requires: list[tuple[EvidenceReq, re.Pattern[str] | None]] = []
    for r in policy.requires:
        compiled: re.Pattern[str] | None = None
        if r.kind == "regex" and r.pattern:
            try:
                compiled = re.compile(r.pattern)
            except re.error:
                # Should never happen because Policy.__post_init__ already
                # validates regex compile, but defense-in-depth: an
                # uncompilable pattern means "always indeterminate" so
                # we don't silently say "would have fired" against an
                # invalid pattern.
                compiled = None
        requires.append((r, compiled))

    total = 0
    matched = 0
    indeterminate_total = 0
    sample_ids: list[int] = []
    # Track which kinds produced INDETERMINATE results across the
    # replay so the dashboard can surface the disclosure. We collect
    # into a dict to preserve first-seen order while deduping.
    indet_kinds: dict[str, None] = {}
    # Defense against rows that DO carry frame metadata vs rows that
    # DON'T. If 100% of admitted rows lack hook_event/matcher we
    # cannot say the proposed policy's frame is well-scoped; we
    # surface that as `no-frame-metadata-on-rows` after the loop so
    # the dashboard knows the count covers the WHOLE tenant ledger
    # window rather than the (event, matcher) slice the operator
    # picked.
    saw_frame_metadata = False

    for row in rows:
        body = getattr(row, "body", None)
        if not isinstance(body, dict):
            continue
        # Trigger frame match. P0 #2: rows produced by /verify_inline
        # and /verify/{step} now write hook_event + matcher into the
        # ledger body (runtime change shipped alongside this file).
        # Rows that PREDATE the runtime change lack the metadata; we
        # exclude them rather than admit them, because admitting
        # systematically inflates total_records into "all tenant rows
        # in window" rather than the (event, matcher) slice the
        # operator targeted.
        frame_outcome = _trigger_matches(body, event, matcher)
        if frame_outcome == "no-metadata":
            continue
        if frame_outcome == "miss":
            continue
        # frame_outcome == "hit"
        saw_frame_metadata = True

        total += 1

        # by_verdict bucketing (closed-set).
        verdict_raw = body.get("verdict")
        if isinstance(verdict_raw, str) and verdict_raw in LEDGER_VERDICTS:
            by_verdict[verdict_raw] += 1
        else:
            by_verdict["unknown"] += 1

        # Re-run requires[] against the row body. Empty requires =
        # unconditional fire (matches the "audit emit signal"
        # archetype's runtime semantics).
        if requires:
            row_status = _PASS
            row_has_indet = False
            for req, compiled in requires:
                status = _requires_holds(req, compiled, body)
                if status == _FAIL:
                    row_status = _FAIL
                    break
                if status == _INDET:
                    row_has_indet = True
                    indet_kinds.setdefault(req.kind, None)
            if row_status == _PASS and row_has_indet:
                row_status = _INDET
        else:
            row_status = _FAIL  # empty requires → always fires

        if row_status == _FAIL:
            matched += 1
            if action in by_action:
                by_action[action] += 1
            if len(sample_ids) < sample_limit:
                # rows arrive newest-first from the LedgerRepo helper,
                # so appending in iteration order preserves
                # newest-first sample ordering.
                row_id = getattr(row, "id", None)
                if isinstance(row_id, int):
                    sample_ids.append(row_id)
        elif row_status == _INDET:
            indeterminate_total += 1

    skipped: str | None = None
    if total == 0:
        # If we never even matched a row to the trigger frame, the
        # window may be empty OR the rows in it predate the runtime
        # change that writes hook_event/matcher. The route layer
        # cannot disambiguate without re-reading the ledger; we pick
        # the friendlier reason ("no records in this trigger frame")
        # because that's what the operator is most likely to act on.
        skipped = "no-records-in-trigger-frame"
    elif not saw_frame_metadata:
        # Defense-in-depth: should be unreachable because the
        # frame-match function now rejects rows without metadata, but
        # if a future change reintroduces admit-on-missing this flips
        # the result so the dashboard never silently over-reports.
        skipped = "no-frame-metadata-on-rows"
    elif (
        requires
        and matched == 0
        and indeterminate_total > 0
        and indeterminate_total == total
    ):
        # Every row we considered was indeterminate (e.g. an llm_critic
        # policy against a window with no LLM round-trip available).
        # The headline count would mislead an operator into thinking
        # the policy is too narrow; surface the limitation instead.
        skipped = "requires-indeterminate"

    return DryRunResult(
        total_records=total,
        matched=matched,
        indeterminate=indeterminate_total,
        by_verdict=by_verdict,
        by_action=by_action,
        sample_matched_ids=sample_ids,
        skipped_reason=skipped,
        skipped_kinds=list(indet_kinds.keys()),
    )


# ── helpers ─────────────────────────────────────────────────────────


def _trigger_matches(body: dict, event: str, matcher: str) -> str:
    """Return one of:

      "hit"         — body's hook_event + matcher both fall under the
                      policy's (event, matcher) frame.
      "miss"        — body's hook_event / matcher are present and do
                      NOT fall under the frame; row is excluded.
      "no-metadata" — neither hook_event nor matcher is present; the
                      row predates the runtime contract that writes
                      these fields. Excluded so the dry-run does not
                      silently inflate total_records into "all
                      tenant rows in window."

    Rows produced by /verify_inline and /verify/{step} (runtime change
    shipped alongside this file) write hook_event + matcher into the
    ledger body. Rows that lack BOTH are predate-runtime; rows that
    have hook_event but not matcher (or vice versa) are still
    considered to have metadata for the field that's present.
    """
    body_event = body.get("hook_event") or body.get("__event__")
    body_matcher = body.get("matcher") or body.get("__matcher__")
    has_event_meta = isinstance(body_event, str) and bool(body_event)
    has_matcher_meta = isinstance(body_matcher, str) and bool(body_matcher)
    if not has_event_meta and not has_matcher_meta:
        return "no-metadata"
    if has_event_meta and body_event != event:
        return "miss"
    if has_matcher_meta:
        # Matcher is a CC permission matcher pattern. We accept the
        # exact-string and the wildcard case; richer alternation
        # (e.g. "Bash|Edit") is collapsed to "any of the alternates
        # matches" so the dry-run does not need to import the CC
        # matcher grammar wholesale.
        if matcher == "*":
            return "hit"
        alternates = [a.strip() for a in matcher.split("|") if a.strip()]
        if body_matcher in alternates:
            return "hit"
        # CC matchers can include argument captures like `Bash(rm -rf)`.
        # A simple startswith() check covers the common tool-name
        # match without pulling the full grammar in.
        for alt in alternates:
            if body_matcher.startswith(alt):
                return "hit"
        return "miss"
    return "hit"


def _requires_holds(
    req: EvidenceReq,
    compiled: "re.Pattern[str] | None",
    body: dict,
) -> str:
    """Tri-state evaluation: returns `_PASS`, `_FAIL`, or `_INDET`."""
    if req.kind == "step":
        # The runtime step gate writes `{step, verdict}` into the
        # ledger body for every verifier emission. The replay match
        # is exact-string on both fields.
        ok = (
            body.get("step") == req.step
            and body.get("verdict") == req.verdict
        )
        return _PASS if ok else _FAIL
    if req.kind == "regex":
        if compiled is None:
            # Uncompilable pattern → indeterminate (never silently
            # claim the action would have fired).
            return _INDET
        # P0 #1: the runtime ledger body does NOT carry the original
        # verifier payload by default (it carries the verdict
        # envelope). Without an explicit `__payload_snapshot__` field
        # written by the runtime, scanning the verdict envelope JSON
        # would systematically fail to match patterns authors wrote
        # against the original payload. Mark indeterminate so the
        # headline number reflects "could not check" rather than
        # "would have fired."
        snapshot = body.get(_PAYLOAD_SNAPSHOT_KEY)
        # D82c fix: the runtime now writes the scoped projection into
        # `__payload_snapshot__` (the same text the live regex saw),
        # so the offline replay scans the SAME text whether or not the
        # policy used field_path scoping. No extra resolution needed —
        # the runtime already did it. Stays back-compat for whole-
        # payload snapshots written by pre-D82c regex rows.
        text = _payload_text(snapshot)
        if not text:
            return _INDET
        try:
            return _PASS if compiled.search(text) is not None else _FAIL
        except re.error:
            return _INDET
    if req.kind in ("llm_critic", "shacl"):
        # Cannot evaluate offline without an LLM round-trip / SHACL
        # validation pass. Mark indeterminate so the dashboard can
        # disclose the gap.
        return _INDET
    # Unknown kind: indeterminate. Forward-compat: a future kind
    # landed in the IR that this replay doesn't recognise should NOT
    # silently claim the action would have fired.
    return _INDET


def _payload_text(snapshot: object) -> str:
    """Project the runtime-written `body['__payload_snapshot__']` to
    a flat string the regex can scan.

    Delegates to the shared `project_snapshot_for_regex` helper so the
    offline replay, the live `/verify_inline` route, and the synthetic
    `test_runner` simulator stay in lockstep on what counts as
    projectable text. See
    `magi_cp/policy/payload_projection.py` for the canonical contract;
    a regression test in
    `tests/test_policy_payload_projection.py` pins byte-equality across
    the three surfaces.
    """
    return project_snapshot_for_regex(snapshot)
