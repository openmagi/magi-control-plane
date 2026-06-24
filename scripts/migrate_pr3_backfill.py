"""PR3 backfill: populate subject + payload_hash on legacy HITL rows.

For every row in `hitl_item` where `subject IS NULL`, copy `matter` into
`subject` and `doc_id` into `payload_hash`. Idempotent — re-running is
safe (rows that already have subject populated are skipped).

Usage:

    python -m scripts.migrate_pr3_backfill                    # default DSN
    MAGI_CP_DSN=postgresql+psycopg://… python -m scripts.migrate_pr3_backfill

Or, programmatically:

    from scripts.migrate_pr3_backfill import backfill_hitl
    backfill_hitl(engine)   # returns number of rows updated

The migration runs in chunks so a multi-million-row table doesn't pin
a single transaction open. Progress is logged every 1000 rows.

This is a one-time data migration. The schema change (add columns,
add index, make legacy columns nullable) is in `db.py` and applied via
`init_schema(engine)`. PR4 will drop the legacy columns once all
deployments have run this backfill.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import TYPE_CHECKING

from sqlalchemy import select, update
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from sqlalchemy import Engine

# Path setup so `python scripts/migrate_pr3_backfill.py` works without
# pip-installing the package.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_REPO_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from magi_cp.cloud.db import HitlItem, make_engine  # noqa: E402


log = logging.getLogger("magi_cp.migrate_pr3")


def backfill_hitl(engine: "Engine", *,
                  chunk_size: int = 1000,
                  log_every: int = 1000) -> int:
    """Backfill subject/payload_hash from legacy matter/doc_id.

    Returns the total number of rows updated. Idempotent — rows where
    `subject` is already populated are skipped, so re-running on a
    fully-migrated table is a no-op (returns 0).

    Chunking: scan + update in `chunk_size` row batches so we don't
    hold a long-running transaction on big tables. SQLite serialises
    writes anyway; Postgres takes row-locks for the duration of each
    UPDATE, which is fine at this size.
    """
    total = 0
    # Cursor-style watermark: advance past every row we observe (whether we
    # update it or skip it). Without this, a row where both legacy keys are
    # NULL/empty stays NULL on `subject` and the next outer iteration
    # re-fetches the same page, looping forever. Tracking `last_id` makes
    # the scan strictly forward-progressing and bounded by the table size.
    last_id = 0
    with Session(engine) as s:
        while True:
            # Grab a page of rows that still need subject populated.
            # Using id-ordered scan + LIMIT is portable across SQLite +
            # Postgres without locking gymnastics. `id > :last_id` is the
            # safety latch against the skip-row loop described above.
            stmt = (
                select(HitlItem.id, HitlItem.matter, HitlItem.doc_id)
                .where(HitlItem.subject.is_(None), HitlItem.id > last_id)
                .order_by(HitlItem.id)
                .limit(chunk_size)
            )
            rows = list(s.execute(stmt))
            if not rows:
                break
            for row_id, matter, doc_id in rows:
                # Always advance the watermark, even when we skip — that is
                # the cure for the infinite-loop case (issues #2 / #8).
                last_id = row_id
                # Treat empty strings as missing too. A legacy row written
                # under the old NOT NULL + empty-default schema can carry
                # `matter == ''`; the truthy-falsy display helpers in
                # `hitl_display_subject` treat '' as 'no subject' and fall
                # through to the next column. If we copied '' into the
                # canonical column we would silently strand the row with
                # no usable identifier on either side (issue #4).
                if not matter and not doc_id:
                    log.warning(
                        "skip hitl_item id=%s — both matter and doc_id "
                        "are NULL or empty",
                        row_id,
                    )
                    continue
                # Mirror only the non-empty side(s). If one column is empty
                # we leave it empty on the canonical side too — better to
                # carry the single usable key than to mask emptiness with
                # a duplicate.
                s.execute(
                    update(HitlItem)
                    .where(HitlItem.id == row_id)
                    .values(
                        subject=matter or None,
                        payload_hash=doc_id or None,
                    )
                )
                total += 1
                if total % log_every == 0:
                    log.info("backfilled %d hitl rows…", total)
            s.commit()
    log.info("backfill complete — %d hitl rows updated", total)
    return total


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Backfill subject + payload_hash on legacy HITL rows.",
    )
    p.add_argument(
        "--dsn",
        default=os.environ.get("MAGI_CP_DSN", "sqlite:///./magi-cp.sqlite"),
        help="SQLAlchemy DSN (default: $MAGI_CP_DSN or local sqlite)",
    )
    p.add_argument(
        "--chunk-size", type=int, default=1000,
        help="rows updated per transaction chunk (default 1000)",
    )
    p.add_argument(
        "--quiet", action="store_true",
        help="suppress INFO logs (errors only)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    engine = make_engine(args.dsn)
    n = backfill_hitl(engine, chunk_size=args.chunk_size)
    print(f"backfilled {n} rows")
    return 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
