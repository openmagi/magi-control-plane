"""P8 migration: stamp the `enforcement` field on legacy policy rows.

Companion to `scripts/migrate_pr3_backfill.py` /
`scripts/migrate_pr4_drop_legacy.py`. P8 added the policy-store's
`enforcement` field, computed at PUT time from the verifier registry +
vendor catalog. Rows authored before P8 have `enforcement=None` on disk;
the REST layer re-resolves on read, which is correct on the steady-state
but means an operator never sees the row's resolved label until they hit
the dashboard.

This script walks the on-disk policies.json and stamps the field for
each unstamped row:

  - All step refs resolve cleanly → stamp `"enforcing"` / `"preview"`.
  - Any step ref no longer resolves against the live registry → stamp
    `"unresolved-legacy"` AND set `enabled=False`, so the compiled
    managed-settings cannot ship a hook for a decommissioned verifier.
  - No step refs at all (regex / llm_critic / shacl) → fall back to the
    legacy (action, event)-derived label. These are inline conditions
    with no registry binding; the legacy label is the only sensible
    answer.

Idempotent: rows already stamped at PUT time are left alone.

Usage:

    # Dry-run is always safe — reports plan without modifying anything.
    python3 scripts/migrate_p8_stamp_enforcement.py --dry-run

    # Real migration. Take a backup of policies.json first.
    python3 scripts/migrate_p8_stamp_enforcement.py --yes

    # Custom path / registry-less hermetic stamp (every row resolves
    # to "enforcing" / legacy fallback; useful only for offline testing).
    python3 scripts/migrate_p8_stamp_enforcement.py \\
        --policy-store /var/lib/magi-cp/policies.json --yes

Rollback: keep the pre-migration policies.json backup. The script does
NOT modify the policy IR shape; only the row-level `enforcement` and
`enabled` fields are touched, both of which are additive metadata. A
pre-P8 cloud reads back the stamped fields as legacy lazy labels (no
breakage) since `_normalize` writes byte-stable JSON.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Path setup so `python scripts/migrate_p8_stamp_enforcement.py` works
# without pip-installing the package.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_REPO_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from magi_cp.cloud.policy_store import PolicyStore  # noqa: E402
from magi_cp.cloud.presets_catalog import vendor_catalog  # noqa: E402
from magi_cp.policy.resolved import PolicyOverride  # noqa: E402
from magi_cp.policy.step_enforcement import (  # noqa: E402
    StepResolutionError,
    resolve_policy_enforcement,
)


log = logging.getLogger("magi_cp.migrate_p8")


def _legacy_label(policy) -> str:
    """Mirror of cloud/app.py::_enforcement_label.

    Kept inline so the script does not depend on the FastAPI app
    construction (which would pull in db/auth deps an offline
    migration shouldn't need).
    """
    if policy.action in ("block", "ask"):
        return "deterministic-gate"
    if policy.trigger.event == "PostToolUse":
        return "observe-only"
    return "log-only"


def stamp_enforcement(
    overrides: list[PolicyOverride],
    *,
    registry,
) -> tuple[list[PolicyOverride], dict[str, int]]:
    """Walk `overrides`, stamping `enforcement` on every row where it
    is None. Returns (new overrides, counts) without mutating the input.

    Counts cover the categories an operator wants to triage:

      - stamped_enforcing       — step refs resolved cleanly.
      - stamped_preview         — `preview:` prefix present.
      - stamped_legacy_label    — no step refs (regex / llm_critic /
                                  shacl); fall back to (action, event).
      - stamped_unresolved      — step ref failed to resolve; row was
                                  also flipped to enabled=False.
      - already_stamped         — row had a non-None enforcement on
                                  disk; left alone.
    """
    counts = {
        "stamped_enforcing": 0,
        "stamped_preview": 0,
        "stamped_legacy_label": 0,
        "stamped_unresolved": 0,
        "already_stamped": 0,
    }
    out: list[PolicyOverride] = []
    for ov in overrides:
        if ov.enforcement is not None:
            counts["already_stamped"] += 1
            out.append(ov)
            continue
        has_step_req = any(r.kind == "step" for r in ov.policy.requires)
        if not has_step_req:
            label = _legacy_label(ov.policy)
            counts["stamped_legacy_label"] += 1
            out.append(PolicyOverride(
                policy=ov.policy, source=ov.source,
                enabled=ov.enabled, enforcement=label,
            ))
            continue
        try:
            label = resolve_policy_enforcement(
                ov.policy, registry=registry,
                vendor_catalog_fn=vendor_catalog,
            )
        except StepResolutionError as e:
            log.warning(
                "policy %r: step %r failed (%s) — stamping "
                "'unresolved-legacy' + enabled=False",
                ov.policy.id, e.step, e.reason,
            )
            counts["stamped_unresolved"] += 1
            out.append(PolicyOverride(
                policy=ov.policy, source=ov.source,
                enabled=False, enforcement="unresolved-legacy",
            ))
            continue
        if label == "preview":
            counts["stamped_preview"] += 1
        else:
            counts["stamped_enforcing"] += 1
        out.append(PolicyOverride(
            policy=ov.policy, source=ov.source,
            enabled=ov.enabled, enforcement=label,
        ))
    return out, counts


def _build_registry():
    """Construct the same registry the production app uses.

    Kept in a thin helper so the test suite can monkeypatch this to
    return a stub registry without dragging in builtins.
    """
    from magi_cp.verifier.builtins import register_builtins
    from magi_cp.verifier.protocol import VerifierRegistry
    reg = VerifierRegistry()
    register_builtins(reg)
    return reg


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Stamp `enforcement` on legacy (pre-P8) policy "
                    "rows. Idempotent; rows already stamped are "
                    "untouched. Real (non-dry-run) execution "
                    "requires --yes.",
    )
    p.add_argument(
        "--policy-store",
        default=os.environ.get(
            "MAGI_CP_POLICY_STORE",
            str(Path.home() / ".magi-cp" / "policies.json"),
        ),
        help="path to policies.json (default: $MAGI_CP_POLICY_STORE "
             "or ~/.magi-cp/policies.json)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="report what would be done without modifying the store",
    )
    p.add_argument(
        "--yes", "--confirm", dest="yes", action="store_true",
        help="confirm the in-place rewrite. REQUIRED for non-dry-run. "
             "Take a backup of policies.json first.",
    )
    p.add_argument(
        "--quiet", action="store_true",
        help="suppress INFO logs (errors / counts only)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not args.dry_run and not args.yes:
        log.error(
            "Refusing to rewrite %s without --yes. Re-run with "
            "--dry-run to preview, or --yes to apply.", args.policy_store,
        )
        return 1
    if not os.path.exists(args.policy_store):
        log.info(
            "policies.json not found at %s — nothing to migrate.",
            args.policy_store,
        )
        return 0
    store = PolicyStore(path=args.policy_store)
    overrides = store.load()
    if not overrides:
        log.info("policy store is empty — nothing to migrate.")
        return 0
    registry = _build_registry()
    stamped, counts = stamp_enforcement(overrides, registry=registry)
    log.info("migration counts: %s", json.dumps(counts, sort_keys=True))
    if args.dry_run:
        log.info("DRY RUN — store not modified.")
        return 0
    if counts["stamped_enforcing"] + counts["stamped_preview"] + \
            counts["stamped_legacy_label"] + counts["stamped_unresolved"] == 0:
        log.info("nothing to stamp — every row already has enforcement.")
        return 0
    store.save(stamped)
    log.info("wrote %d rows to %s", len(stamped), args.policy_store)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
