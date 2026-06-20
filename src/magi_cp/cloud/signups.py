"""v2.1-D2 — alpha-signup intake.

Free-tier "alpha pilot" signup form. Anyone can submit; the operator
reviews the list, provisions a tenant + key out-of-band, and emails the
new mcp_… key to the applicant. No automatic provisioning here — that
would be a spam vector. The intake just queues the request and rate-
limits per IP.

The schema is intentionally small. We capture only what we need to
qualify the lead:

  - email      : how to send the key
  - firm       : qualification (Korean legal firm = beachhead)
  - role       : decision-maker vs IC
  - use_case   : free-text; what they want to gate
  - referrer   : where they heard about us
  - source_ip  : audit (last seen IP, not used for blocking by itself)

Default-OFF tablespace: lives in the same SQLite/PG store as the rest of
the cloud state. Operator pulls the list with a one-shot CLI (see
`magi-cp signups list`).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import Engine, String, Text, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base, BigInt


# ── table ──────────────────────────────────────────────────────────
class AlphaSignup(Base):
    __tablename__ = "alpha_signups"
    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    ts_created: Mapped[int] = mapped_column(BigInt, nullable=False)
    email: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    firm: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    use_case: Mapped[str] = mapped_column(Text, nullable=False, default="")
    referrer: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    source_ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", index=True,
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")


# ── dataclass + repo ───────────────────────────────────────────────
@dataclass
class SignupRecord:
    id: int
    ts_created: int
    email: str
    firm: str
    role: str
    use_case: str
    referrer: str
    source_ip: str
    status: str
    notes: str


class SignupRepo:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def submit(
        self, *, email: str, firm: str = "", role: str = "",
        use_case: str = "", referrer: str = "", source_ip: str = "",
    ) -> SignupRecord:
        with Session(self.engine) as s:
            row = AlphaSignup(
                ts_created=int(time.time()),
                email=email.strip().lower(),
                firm=firm.strip(),
                role=role.strip(),
                use_case=use_case.strip(),
                referrer=referrer.strip(),
                source_ip=source_ip,
                status="pending",
            )
            s.add(row); s.commit(); s.refresh(row)
            return _as_record(row)

    def list(self, *, status: str | None = None,
             limit: int = 200) -> list[SignupRecord]:
        with Session(self.engine) as s:
            stmt = select(AlphaSignup).order_by(AlphaSignup.id.desc())
            if status:
                stmt = stmt.where(AlphaSignup.status == status)
            rows = s.scalars(stmt.limit(limit)).all()
            return [_as_record(r) for r in rows]

    def update_status(self, id: int, *, status: str, notes: str = "") -> None:
        with Session(self.engine) as s:
            row = s.get(AlphaSignup, id)
            if row is None:
                raise KeyError(id)
            row.status = status
            if notes:
                row.notes = notes
            s.commit()

    def count_recent_by_ip(self, ip: str, since_ts: int) -> int:
        """Used for the cheap per-IP rate limit on /signup. Counts pending +
        approved submissions from this IP within the window."""
        if not ip:
            return 0
        with Session(self.engine) as s:
            return s.scalar(
                select(func.count(AlphaSignup.id))
                .where(AlphaSignup.source_ip == ip)
                .where(AlphaSignup.ts_created >= since_ts)
            ) or 0


def _as_record(row: AlphaSignup) -> SignupRecord:
    return SignupRecord(
        id=row.id, ts_created=row.ts_created,
        email=row.email, firm=row.firm, role=row.role,
        use_case=row.use_case, referrer=row.referrer,
        source_ip=row.source_ip, status=row.status, notes=row.notes,
    )


__all__ = ["AlphaSignup", "SignupRecord", "SignupRepo"]
