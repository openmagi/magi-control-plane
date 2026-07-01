"""P2 Codex adapter, Shim C cloud fallback: synthetic SessionEnd sweeper.

Design brief: docs/plans/2026-06-30-codex-runtime-adapter-design.md
Section 4.3. The sweeper fires a synthetic SessionEnd for a stale Codex
session (last_seen older than the TTL) whose active packs require a
session-end fanout. A fresh session, a non-Codex session, and a session
with no session-end pack all no-op.
"""
from __future__ import annotations

from dataclasses import dataclass

from magi_cp.cloud.sweepers.codex_session_end import (
    CODEX_SYNTHETIC_SESSION_END_TTL_SECONDS,
    resolve_ttl_seconds,
    sweep_synthetic_session_end,
)


@dataclass
class _Row:
    runtime_id: str
    session_id: str
    tenant_id: str
    last_seen_at: int
    pack_ids: list
    last_synthetic_session_end_at: int | None = None


_NOW = 1_000_000
_TTL = CODEX_SYNTHETIC_SESSION_END_TTL_SECONDS  # 1800s = 30m


def _requires_all(_pid: str) -> bool:
    return True


def test_stale_codex_session_fires_synthetic_session_end():
    # last_seen 31m ago -> stale.
    row = _Row("codex", "s1", "t1", _NOW - 1860, ["pack/x"])
    fired = []
    out = sweep_synthetic_session_end(
        [row], now=_NOW, emit_fanout=fired.append,
        pack_requires_session_end=_requires_all,
    )
    assert len(out) == 1
    assert out[0].session_id == "s1"
    assert out[0].tenant_id == "t1"
    assert out[0].runtime_id == "codex"
    assert fired == out  # the sink saw exactly the fired events


def test_sweep_is_idempotent_over_repeated_passes():
    # A still-stale row must not re-emit on the next sweep pass: the first
    # pass records the synthesis high-water mark, the second excludes it.
    row = _Row("codex", "s1", "t1", _NOW - 1860, ["pack/x"])
    first = []
    out1 = sweep_synthetic_session_end(
        [row], now=_NOW, emit_fanout=first.append,
        pack_requires_session_end=_requires_all,
    )
    assert len(out1) == 1
    # Second pass over the SAME still-stale row -> no re-emit.
    second = []
    out2 = sweep_synthetic_session_end(
        [row], now=_NOW + 300, emit_fanout=second.append,
        pack_requires_session_end=_requires_all,
    )
    assert out2 == []
    assert second == []


def test_sweep_re_arms_after_new_activity():
    # Once synthesized, fresh activity (last_seen_at advancing past the
    # synthesis mark) re-arms the row for a later stale window.
    row = _Row("codex", "s1", "t1", _NOW - 1860, ["pack/x"])
    sweep_synthetic_session_end(
        [row], now=_NOW, emit_fanout=lambda _e: None,
        pack_requires_session_end=_requires_all,
    )
    # New activity: advance last_seen past the synthesis mark, then go
    # stale again relative to a later clock.
    row.last_seen_at = _NOW + 10
    later = _NOW + 10 + _TTL
    out = sweep_synthetic_session_end(
        [row], now=later, emit_fanout=lambda _e: None,
        pack_requires_session_end=_requires_all,
    )
    assert len(out) == 1


def test_sweep_uses_injected_mark_synthesized():
    # Production wiring supplies a persisting mark_synthesized; the sweep
    # calls it once per fired row with (row, now).
    row = _Row("codex", "s1", "t1", _NOW - 1860, ["pack/x"])
    marks = []
    sweep_synthetic_session_end(
        [row], now=_NOW, emit_fanout=lambda _e: None,
        pack_requires_session_end=_requires_all,
        mark_synthesized=lambda r, ts: marks.append((r.session_id, ts)),
    )
    assert marks == [("s1", _NOW)]


def test_fresh_codex_session_does_not_fire():
    # last_seen 10m ago -> under the 30m TTL.
    row = _Row("codex", "s1", "t1", _NOW - 600, ["pack/x"])
    fired = []
    out = sweep_synthetic_session_end(
        [row], now=_NOW, emit_fanout=fired.append,
        pack_requires_session_end=_requires_all,
    )
    assert out == []
    assert fired == []


def test_boundary_exactly_ttl_fires():
    # exactly at the TTL boundary counts as stale (>= ttl).
    row = _Row("codex", "s1", "t1", _NOW - _TTL, ["pack/x"])
    out = sweep_synthetic_session_end(
        [row], now=_NOW, emit_fanout=lambda _e: None,
        pack_requires_session_end=_requires_all,
    )
    assert len(out) == 1


def test_non_codex_session_never_fires():
    row = _Row("claude-code", "s1", "t1", _NOW - 999_999, ["pack/x"])
    out = sweep_synthetic_session_end(
        [row], now=_NOW, emit_fanout=lambda _e: None,
        pack_requires_session_end=_requires_all,
    )
    assert out == []


def test_stale_codex_without_session_end_pack_does_not_fire():
    row = _Row("codex", "s1", "t1", _NOW - 999_999, ["pack/x"])
    out = sweep_synthetic_session_end(
        [row], now=_NOW, emit_fanout=lambda _e: None,
        pack_requires_session_end=lambda _pid: False,
    )
    assert out == []


def test_resolve_ttl_default_and_override():
    assert resolve_ttl_seconds(None) == 1800
    assert resolve_ttl_seconds({}) == 1800
    assert resolve_ttl_seconds(
        {"codex_synthetic_session_end_ttl_seconds": 600}
    ) == 600
    # bool / non-positive / non-int overrides fall back to the default.
    assert resolve_ttl_seconds(
        {"codex_synthetic_session_end_ttl_seconds": True}
    ) == 1800
    assert resolve_ttl_seconds(
        {"codex_synthetic_session_end_ttl_seconds": 0}
    ) == 1800
    assert resolve_ttl_seconds(
        {"codex_synthetic_session_end_ttl_seconds": "900"}
    ) == 1800
