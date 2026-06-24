"""D53b follow-up: single source of truth for the closed-set ledger
verdict allowlist.

Why this module exists:

  Prior to this file, the verdict allowlist was inlined in three
  places: the `/ledger/samples` projection (`_ALLOWED_VERDICTS` in
  cloud/app.py around the samples builder), the `/policies/dry-run`
  sample_matched projection (a second `_ALLOWED_VERDICTS` literal in
  the dry-run route), and `_VERDICT_BUCKETS` in
  `magi_cp.policy.dry_run`. Adding a new verdict (e.g. `warn`) would
  require touching every copy in lockstep; a missed copy would cause
  the verdict to round-trip as `unknown` on one surface and as `warn`
  on another, producing a silent UX discrepancy.

  Hoisting the set here makes adding a verdict a single-line change.

The constant is a `frozenset` (unordered, immutable). Callers needing
an iteration order (e.g. the dry-run bucket dict that initialises every
key to zero) use `LEDGER_VERDICTS_ORDERED`, which preserves the legacy
deterministic order used by the dashboard pill row.

Anything outside the allowlist must collapse to `unknown` on egress -
the runtime verifier surface validates that producers stay inside the
set, but a malformed body must NEVER cause a novel string to leak out
the public projection.
"""
from __future__ import annotations


# Deterministic order matches the legacy `_VERDICT_BUCKETS` tuple and
# the dashboard's pill row order, so the by_verdict bucket dict in
# `dry_run.py` iterates in the same order operators are used to.
LEDGER_VERDICTS_ORDERED: tuple[str, ...] = (
    "pass", "fail", "deny",
    "review", "needs_review", "not_applicable",
)

# Membership check for projections. Anything outside the set collapses
# to `unknown` on egress (sample rows) or to the `unknown` bucket
# (dry-run by_verdict). Single source of truth - the `/ledger/samples`,
# `/policies/dry-run` sample projection, and `evaluate_dry_run` bucket
# init all import this rather than redeclaring a literal set.
LEDGER_VERDICTS: frozenset[str] = frozenset(LEDGER_VERDICTS_ORDERED)
