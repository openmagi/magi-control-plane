"""PR4 migration: drop the legacy `matter` / `doc_id` columns from hitl_item.

Runs after `scripts/migrate_pr3_backfill.py` has populated `subject` and
`payload_hash` on every row. Refuses to run if ANY row still has
`subject IS NULL` — that would silently destroy the only usable
identifier the row carries.

Steps:

  1. SAFETY CHECK — count rows where `subject IS NULL`. Refuse if > 0.
  2. DROP INDEX `ix_hitl_matter_status` (PR3 added the canonical
     `ix_hitl_subject_status` to replace it).
  3. DROP COLUMN `matter` (Postgres) — SQLite gets a table-rebuild.
  4. DROP COLUMN `doc_id` (Postgres) — SQLite gets a table-rebuild.

Usage:

    # Dry-run is always safe — reports plan without modifying anything.
    python scripts/migrate_pr4_drop_legacy.py --dry-run

    # Real cut-over requires explicit irreversibility confirmation.
    python scripts/migrate_pr4_drop_legacy.py --yes
    MAGI_CP_DSN=postgresql+psycopg://… \
        python scripts/migrate_pr4_drop_legacy.py --yes

Or, programmatically:

    from scripts.migrate_pr4_drop_legacy import drop_legacy_columns
    drop_legacy_columns(engine)   # returns dict with rows_kept counts

This is a one-time schema migration. Always back up first; column
removal is irreversible without a restore.

ROLLBACK
--------
Reverting the PR4 application code is NOT sufficient to undo this
migration. The pre-PR4 ORM re-introduces `matter` and `doc_id` columns
and the pydantic VerifyReq/VerifyDispatchReq/VerifyInlineReq models
require them, but `Base.metadata.create_all` is `CREATE TABLE IF NOT
EXISTS`-shaped and will NOT re-add the dropped columns to an existing
table. Every `/hitl` read after such a rollback will crash with
`no such column: matter`.

The only safe rollback is a DB restore from a backup taken BEFORE this
script ran. Take that backup as the first step of the cut-over:

    # Postgres
    pg_dump --no-owner --no-acl "$MAGI_CP_DSN_PG" > pr4-pre-drop.sql

    # SQLite
    cp magi-cp.sqlite magi-cp.sqlite.pr4-pre-drop.bak

The squash commit message for this migration MUST reference the same
caveat so operators reading `git log` (or `helm rollback` runbooks)
discover it without code-diving.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import TYPE_CHECKING

from sqlalchemy import inspect, text

if TYPE_CHECKING:
    from sqlalchemy import Engine

# Path setup so `python scripts/migrate_pr4_drop_legacy.py` works without
# pip-installing the package.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_REPO_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from magi_cp.cloud.db import make_engine  # noqa: E402


log = logging.getLogger("magi_cp.migrate_pr4")


class BackfillIncomplete(RuntimeError):
    """Raised when the PR4 cut-over would discard usable data.

    This is the safety latch: PR3 added subject/payload_hash columns and
    backfilled from matter/doc_id. PR4 drops the legacy columns. If any
    row still has `subject IS NULL` we refuse — the legacy values are
    the only usable identifier and dropping the columns would erase
    the link to the originating call.
    """


def check_backfill_complete(engine: "Engine") -> int:
    """Return number of hitl_item rows where `subject IS NULL`.

    `0` means the PR4 cut-over is safe. Any other value MUST block the
    drop; the operator should re-run `scripts/migrate_pr3_backfill.py`
    and investigate why those rows didn't populate (most commonly: both
    `matter` and `doc_id` were empty strings on legacy rows — issue #4 in
    the backfill cursor design — and the backfill skipped them).
    """
    insp = inspect(engine)
    if "hitl_item" not in set(insp.get_table_names()):
        # No table at all — almost certainly the operator pointed the
        # script at the wrong DSN. Surface a clean, actionable refusal
        # instead of letting SQLAlchemy raise NoSuchTableError with a
        # raw Python traceback.
        raise BackfillIncomplete(
            "hitl_item table does not exist on this DSN — run "
            "init_schema (and scripts/migrate_pr3_backfill.py if "
            "upgrading from a pre-PR3 deploy) first, or re-check the "
            "MAGI_CP_DSN / --dsn target."
        )
    cols = {c["name"] for c in insp.get_columns("hitl_item")}
    if "subject" not in cols:
        # Pre-PR3 schema — backfill cannot have run.
        raise BackfillIncomplete(
            "hitl_item is missing the `subject` column — run init_schema "
            "+ scripts/migrate_pr3_backfill.py first."
        )
    with engine.begin() as conn:
        n = conn.execute(text(
            "SELECT COUNT(*) FROM hitl_item WHERE subject IS NULL"
        )).scalar_one()
    return int(n)


def drop_legacy_columns(engine: "Engine", *, dry_run: bool = False) -> dict:
    """Drop the legacy `matter` / `doc_id` columns and the
    `ix_hitl_matter_status` index from `hitl_item`.

    Safety: refuses to run if `check_backfill_complete()` reports any
    rows with `subject IS NULL`. The refusal is a `BackfillIncomplete`
    exception that the CLI translates into exit-code 1 with a clear
    operator-facing message.

    Idempotent: each step is guarded by an `inspect()` lookup. Calling
    on a fully-migrated DB is a no-op (returns rows_kept=N + steps
    with all entries set to "already_applied").

    SQLite: `ALTER TABLE … DROP COLUMN` was added in 3.35.0 (March 2021)
    — the minimum sqlite shipped with macOS / Linux distros published in
    the last 3 years all satisfy this. We use the bare DROP COLUMN form;
    older SQLite would fail at the ALTER step, which is an operator-
    actionable error (upgrade SQLite) rather than a silent half-migration.
    """
    null_count = check_backfill_complete(engine)
    if null_count > 0:
        raise BackfillIncomplete(
            f"refuse to drop legacy columns — {null_count} hitl_item row(s) "
            f"still have subject IS NULL. Run "
            f"`python scripts/migrate_pr3_backfill.py` and re-check."
        )
    insp = inspect(engine)
    existing_cols = {c["name"] for c in insp.get_columns("hitl_item")}
    existing_idx = {ix["name"] for ix in insp.get_indexes("hitl_item")}
    steps: dict[str, str] = {}

    plan: list[tuple[str, str]] = []
    if "ix_hitl_matter_status" in existing_idx:
        plan.append((
            "drop_index_ix_hitl_matter_status",
            "DROP INDEX IF EXISTS ix_hitl_matter_status",
        ))
    else:
        steps["drop_index_ix_hitl_matter_status"] = "already_applied"
    if "matter" in existing_cols:
        plan.append((
            "drop_column_matter",
            "ALTER TABLE hitl_item DROP COLUMN matter",
        ))
    else:
        steps["drop_column_matter"] = "already_applied"
    if "doc_id" in existing_cols:
        plan.append((
            "drop_column_doc_id",
            "ALTER TABLE hitl_item DROP COLUMN doc_id",
        ))
    else:
        steps["drop_column_doc_id"] = "already_applied"

    if dry_run:
        for name, sql in plan:
            steps[name] = f"would_run: {sql}"
            log.info("DRY RUN: %s -> %s", name, sql)
        return {"rows_kept_null_subject": null_count,
                "steps": steps,
                "dry_run": True}

    with engine.begin() as conn:
        for name, sql in plan:
            log.info("applying: %s", sql)
            conn.execute(text(sql))
            steps[name] = "applied"
    return {"rows_kept_null_subject": null_count,
            "steps": steps,
            "dry_run": False}


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Drop legacy matter/doc_id columns + index from "
                    "hitl_item. Refuses to run with NULL subject rows. "
                    "Real (non-dry-run) execution requires --yes.",
    )
    p.add_argument(
        "--dsn",
        default=os.environ.get("MAGI_CP_DSN", "sqlite:///./magi-cp.sqlite"),
        help="SQLAlchemy DSN (default: $MAGI_CP_DSN or local sqlite)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="report what would be done without modifying the schema",
    )
    p.add_argument(
        "--yes", "--confirm-irreversible", dest="yes", action="store_true",
        help="confirm this irreversible DDL drop. REQUIRED for non-dry-run "
             "execution. Take a DB backup first; see module docstring's "
             "ROLLBACK section.",
    )
    p.add_argument(
        "--quiet", action="store_true",
        help="suppress INFO logs (errors only)",
    )
    return p


def _confirm_irreversible(args: argparse.Namespace, *,
                          stdin=None, stderr=None) -> bool:
    """Gate the non-dry-run path behind an explicit confirmation.

    Three-layer policy:
      1. `--yes` / `--confirm-irreversible` short-circuits to True
         (intended for CI / runbook automation that has already
         taken a DB backup as part of the deploy step).
      2. Interactive TTY: print a clear warning to stderr, then read
         a `yes` / `no` answer from stdin. Anything other than a
         case-insensitive `yes` cancels.
      3. Non-interactive (no `--yes`, no TTY): refuse outright. We
         do not let a CI runner with prod DB creds drop columns by
         accident just because nobody attached stdin.
    """
    stdin = stdin if stdin is not None else sys.stdin
    stderr = stderr if stderr is not None else sys.stderr
    if args.yes:
        return True
    if stdin.isatty():
        print(
            "About to DROP the `matter` / `doc_id` columns + "
            "`ix_hitl_matter_status` index from hitl_item.\n"
            "This is IRREVERSIBLE without a DB restore — make sure "
            "you have a fresh backup.\n"
            "Type 'yes' to continue, anything else to cancel: ",
            end="", file=stderr, flush=True,
        )
        answer = stdin.readline().strip().lower()
        return answer == "yes"
    print(
        "ERROR: refusing to run the irreversible drop without "
        "explicit confirmation. Re-run with --yes (or "
        "--confirm-irreversible) after taking a DB backup. See the "
        "module docstring's ROLLBACK section.",
        file=stderr,
    )
    return False


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not args.dry_run and not _confirm_irreversible(args):
        return 1
    engine = make_engine(args.dsn)
    try:
        result = drop_legacy_columns(engine, dry_run=args.dry_run)
    except BackfillIncomplete as e:
        log.error("backfill incomplete: %s", e)
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.dry_run:
        print("DRY RUN — no changes made.")
    else:
        print("dropped legacy columns + index from hitl_item.")
    for step, outcome in result["steps"].items():
        print(f"  {step}: {outcome}")
    return 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
