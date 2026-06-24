"""D53b - policy dry-run replay.

Given a draft PolicyIR and a window of recent ledger records, return how
many of those records WOULD have triggered the policy's action if the
policy had been enabled when each record was produced.

Replays are read-only; nothing is persisted. The endpoint is gated by
the same admin key as the rest of policies CRUD and never writes to
the ledger or HITL queue.

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
        kind=regex      : re.search(req.pattern, payload_text(row))
        kind=llm_critic : SKIPPED (no LLM round-trip in dry-run;
                          marked indeterminate so the count is
                          honest about what was actually simulated).
        kind=shacl      : SKIPPED (pyshacl pass is not cheap enough
                          to fan out across a 24h window; counted
                          as indeterminate the same as llm_critic).
  - The policy's action would fire when at least one requires entry
    fails (matches the runtime gate semantics: requires[] is an AND
    of pass conditions; any fail short-circuits to "action").
  - An empty requires[] is the "unconditional signal" archetype: the
    policy fires on EVERY row that matches the trigger frame.

Output schema (mirrors what cloud/app.py wraps in the response):
    {
      total_records: int,        # rows we considered (trigger-matched)
      matched: int,              # rows where action would have fired
      by_verdict: {              # row.body['verdict'] distribution
        pass: int, fail: int, deny: int,
        review: int, needs_review: int, not_applicable: int,
        unknown: int,            # closed-set; novel strings collapse here
      },
      by_action: {block,ask,audit,strip: int},
      sample_matched_ids: [int], # newest-first ids of matched rows;
                                 # caller redacts the bodies via D50
      skipped_reason: str | None,
    }

The caller (the FastAPI route) is responsible for:
  - validating the IR (we trust the dataclass __post_init__),
  - querying the ledger window,
  - redacting `sample_matched` payloads via run_redaction.py.

This module is pure logic (no SQLAlchemy, no FastAPI imports) so the
unit test can drive it with literal LedgerEntry-shaped dicts.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable

from .ir import AnyPolicy, EvidencePolicy, EvidenceReq


# Closed-set verdict allowlist. Mirrors the /ledger/samples projection
# (see cloud/app.py). Anything outside collapses to `unknown` so a
# misbehaving producer cannot pollute the by_verdict bucket with novel
# strings.
_VERDICT_BUCKETS: tuple[str, ...] = (
    "pass", "fail", "deny",
    "review", "needs_review", "not_applicable",
)

# Action archetypes the IR supports. `strip` is reserved for a future
# verifier-protocol mutation channel; we keep the bucket so the
# frontend pill row renders zero for it without a None check.
_ACTION_BUCKETS: tuple[str, ...] = ("block", "ask", "audit", "strip")


@dataclass
class DryRunResult:
    total_records: int
    matched: int
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
    by_verdict = {b: 0 for b in _VERDICT_BUCKETS}
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
            by_verdict=by_verdict,
            by_action=by_action,
            sample_matched_ids=[],
            skipped_reason="archetype-not-dry-runnable",
        )

    event = policy.trigger.event
    matcher = policy.trigger.matcher
    action = policy.action

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
                # uncompilable pattern means "always fails to match" so
                # the policy action would always fire. We still keep the
                # None and treat it as a hard fail in the loop.
                compiled = None
        requires.append((r, compiled))

    total = 0
    matched = 0
    sample_ids: list[int] = []

    for row in rows:
        body = getattr(row, "body", None)
        if not isinstance(body, dict):
            continue
        # Trigger frame match. Records emitted by /verify_inline write
        # `body['hook_event']` and `body['matcher']` when the gate
        # opts in; legacy rows omit them. We accept either: missing
        # frame metadata means we cannot narrow the row to a single
        # trigger and we admit it (the operator's policy may still
        # have applied at runtime; the conservative read is "consider
        # it"). When metadata IS present we filter on it strictly.
        if not _trigger_matches(body, event, matcher):
            continue

        total += 1

        # by_verdict bucketing (closed-set).
        verdict_raw = body.get("verdict")
        if isinstance(verdict_raw, str) and verdict_raw in _VERDICT_BUCKETS:
            by_verdict[verdict_raw] += 1
        else:
            by_verdict["unknown"] += 1

        # Re-run requires[] against the row body. Empty requires =
        # unconditional fire (matches the "audit emit signal"
        # archetype's runtime semantics).
        all_pass = True
        if requires:
            for req, compiled in requires:
                if not _requires_holds(req, compiled, body):
                    all_pass = False
                    break
        else:
            all_pass = False  # empty requires → always fires

        if not all_pass:
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

    skipped: str | None = None
    if total == 0:
        skipped = "no-records-in-trigger-frame"

    return DryRunResult(
        total_records=total,
        matched=matched,
        by_verdict=by_verdict,
        by_action=by_action,
        sample_matched_ids=sample_ids,
        skipped_reason=skipped,
    )


# ── helpers ─────────────────────────────────────────────────────────


def _trigger_matches(body: dict, event: str, matcher: str) -> bool:
    """Best-effort match: a ledger row falls in the policy's trigger
    frame iff its recorded hook_event matches (when present) and its
    recorded matcher (tool name / wildcard) matches.

    Rows produced by /verify_inline DO carry the original hook_event +
    matcher under reserved keys (`__event__` / `__matcher__` on the
    inbound payload; the runtime mirrors them onto the ledger body so
    the offline replay can still find them). Older rows omit the
    metadata; we admit those rows because excluding them silently
    would underreport the matched count - the operator would think
    their proposed policy is more selective than it really is.
    """
    body_event = body.get("hook_event") or body.get("__event__")
    if isinstance(body_event, str) and body_event and body_event != event:
        return False
    body_matcher = body.get("matcher") or body.get("__matcher__")
    if isinstance(body_matcher, str) and body_matcher:
        # Matcher is a CC permission matcher pattern. We accept the
        # exact-string and the wildcard case; richer alternation
        # (e.g. "Bash|Edit") is collapsed to "any of the alternates
        # matches" so the dry-run does not need to import the CC
        # matcher grammar wholesale.
        if matcher == "*":
            return True
        alternates = [a.strip() for a in matcher.split("|") if a.strip()]
        if body_matcher in alternates:
            return True
        # CC matchers can include argument captures like `Bash(rm -rf)`.
        # A simple startswith() check covers the common tool-name
        # match without pulling the full grammar in.
        for alt in alternates:
            if body_matcher.startswith(alt):
                return True
        return False
    return True


def _requires_holds(
    req: EvidenceReq,
    compiled: "re.Pattern[str] | None",
    body: dict,
) -> bool:
    """True when this requires entry would have passed against `body`."""
    if req.kind == "step":
        # The runtime step gate writes `{step, verdict}` into the
        # ledger body for every verifier emission. The replay match
        # is exact-string on both fields.
        return (
            body.get("step") == req.step
            and body.get("verdict") == req.verdict
        )
    if req.kind == "regex":
        if compiled is None:
            return False
        text = _payload_text(body)
        try:
            return compiled.search(text) is not None
        except re.error:
            return False
    if req.kind in ("llm_critic", "shacl"):
        # Cannot evaluate offline without an LLM round-trip / SHACL
        # validation pass. We treat indeterminate as "pass" so the
        # dry-run does not overstate the matched count (an offline
        # replay claiming "would have blocked" because we could not
        # actually check the rule is worse than under-reporting).
        return True
    # Unknown kind: indeterminate -> pass. Mirrors the conservative
    # posture above.
    return True


def _payload_text(body: dict) -> str:
    """Concat the payload-text fields a regex requires entry might
    reasonably target. Mirrors the /verify_inline regex slicing
    (`req.payload['text']` first, fall through to JSON dump) so the
    offline replay scores rows the same way the runtime gate would.
    """
    parts: list[str] = []
    text = body.get("text")
    if isinstance(text, str):
        parts.append(text)
    cmd = body.get("command")
    if isinstance(cmd, str):
        parts.append(cmd)
    prompt = body.get("prompt")
    if isinstance(prompt, str):
        parts.append(prompt)
    tool_input = body.get("tool_input")
    if isinstance(tool_input, dict):
        for v in tool_input.values():
            if isinstance(v, str):
                parts.append(v)
    if parts:
        return "\n".join(parts)
    # Fall back to a JSON projection so a regex targeting a less
    # common field still has something to match against. Bounded so
    # an over-long body row doesn't pin the CPU under an adversarial
    # regex (Policy.__post_init__ already caps pattern length).
    try:
        return json.dumps(body, ensure_ascii=False)[:8000]
    except (TypeError, ValueError):
        return ""
