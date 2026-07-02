"""PR-K: last_used_at write is throttled.

DB-key auth ran a commit on every request to bump last_used_at, taking the
SQLite single-writer lock per request. The write is now throttled to at most
once per interval; the field stays approximately fresh.
"""
from __future__ import annotations

import time

from sqlalchemy.orm import Session

from magi_cp.cloud.db import init_schema, make_engine
from magi_cp.cloud.tenants import (
    ApiKey,
    ApiKeyRepo,
    TenantRepo,
    _LAST_USED_WRITE_INTERVAL_S,
)


def _setup():
    engine = make_engine("sqlite:///:memory:")
    init_schema(engine)
    TenantRepo(engine).create(tenant_id="user_abc", plan="pro")
    return engine, ApiKeyRepo(engine)


def _read_last_used(engine, key_id: int) -> int | None:
    with Session(engine) as s:
        return s.get(ApiKey, key_id).last_used_at


def _set_last_used(engine, key_id: int, value: int) -> None:
    with Session(engine) as s:
        s.get(ApiKey, key_id).last_used_at = value
        s.commit()


def test_first_auth_writes_last_used():
    engine, keys = _setup()
    issued = keys.issue(tenant_id="user_abc")
    assert _read_last_used(engine, issued.id) is None
    assert keys.authenticate(issued.cleartext) is not None
    assert _read_last_used(engine, issued.id) is not None


def test_auth_within_interval_does_not_rewrite():
    engine, keys = _setup()
    issued = keys.issue(tenant_id="user_abc")
    # Seed a distinguishable recent value INSIDE the no-write window. A
    # throttled auth must leave it untouched; an unconditional per-request
    # write (the pre-fix behavior) would clobber it with `now`. Using a
    # distinct sentinel avoids the same-second false pass where `now` equals
    # the previous value.
    sentinel = int(time.time()) - 10   # within _LAST_USED_WRITE_INTERVAL_S
    _set_last_used(engine, issued.id, sentinel)
    keys.authenticate(issued.cleartext)
    assert _read_last_used(engine, issued.id) == sentinel


def test_auth_after_interval_writes_again():
    engine, keys = _setup()
    issued = keys.issue(tenant_id="user_abc")
    keys.authenticate(issued.cleartext)
    first = _read_last_used(engine, issued.id)
    # Age the stored value past the interval, then auth -> refreshes.
    _set_last_used(engine, issued.id, first - _LAST_USED_WRITE_INTERVAL_S - 5)
    keys.authenticate(issued.cleartext)
    refreshed = _read_last_used(engine, issued.id)
    assert refreshed >= first
