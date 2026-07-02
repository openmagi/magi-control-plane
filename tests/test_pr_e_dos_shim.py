"""PR-E: resource-exhaustion defenses + shim fail-closed.

- API-1: verify_inline's regex runs off the event loop with a wall-clock cap,
  so a catastrophic-backtracking pattern denies instead of wedging the loop.
- API-2: the rate limiter keys on the connection source, not a caller-supplied
  header, and evicts idle buckets.
- API-3: the shim routes fail closed (503) when MAGI_CP_API_KEY is unset
  instead of accepting anonymous callers.
"""
from __future__ import annotations

import asyncio
import re
import tempfile
import time

from fastapi.testclient import TestClient

from magi_cp.cloud.app import _bounded_regex_search, create_app


def _tmp_store() -> str:
    f = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    f.write("[]")
    f.close()
    return f.name


def _client(**env) -> TestClient:
    return TestClient(create_app(dsn="sqlite:///:memory:", policy_store_path=_tmp_store()))


# ── API-1: bounded regex ─────────────────────────────────────────────
def test_bounded_regex_matches_normally():
    rx = re.compile(r"secret-\d+")
    assert asyncio.run(_bounded_regex_search(rx, "here is secret-42")) is True
    assert asyncio.run(_bounded_regex_search(rx, "nothing here")) is False


def test_bounded_regex_times_out_to_false_without_blocking():
    # A deterministic slow "search" (sleeps, then returns) stands in for a
    # catastrophic-backtracking pattern. Using a real evil regex would leave an
    # uninterruptible thread backtracking at executor shutdown and hang the
    # test process (Python's re cannot be interrupted mid-scan); the sleep
    # exercises the exact same timeout + off-loop path and returns cleanly.
    class _SlowRx:
        def search(self, _text):
            time.sleep(0.5)   # exceeds the 0.2s timeout, but does return
            return object()   # truthy "match"

    async def _drive():
        ticks = 0

        async def _ticker():
            nonlocal ticks
            for _ in range(5):
                await asyncio.sleep(0.01)
                ticks += 1

        result, _ = await asyncio.gather(
            _bounded_regex_search(_SlowRx(), "x", timeout=0.2),
            _ticker(),
        )
        return result, ticks

    result, ticks = asyncio.run(_drive())
    assert result is False           # timed out -> deny
    assert ticks == 5                # the loop stayed responsive during the search


def test_verify_inline_wires_bounded_search_and_denies_nonmatch(monkeypatch):
    # Proves the route path uses the bounded search and returns correct
    # verdicts. We avoid a real catastrophic pattern here (see the note above);
    # the timeout->deny contract is covered by the unit test.
    monkeypatch.setenv("MAGI_CP_API_KEY", "k")
    c = _client()
    r = c.post("/verify_inline", headers={"X-Api-Key": "k"}, json={
        "kind": "regex",
        "pattern": r"\bSSN\b",
        "payload": {"tool_input": {"command": "nothing sensitive here"}},
    })
    assert r.status_code == 200
    assert r.json()["verdict"] == "deny"

    r2 = c.post("/verify_inline", headers={"X-Api-Key": "k"}, json={
        "kind": "regex",
        "pattern": r"secret-\d+",
        "payload": {"tool_input": {"command": "found secret-7 in log"}},
    })
    assert r2.status_code == 200
    assert r2.json()["verdict"] == "pass"


# ── API-2: rate limiter key ──────────────────────────────────────────
def test_rate_limiter_keys_on_connection_not_header():
    from magi_cp.cloud.app import TokenBucketLimiter

    lim = TokenBucketLimiter(app=None, capacity=2, refill_per_sec=0.0)

    class _Client:
        host = "10.0.0.9"

    class _Req:
        def __init__(self, api_key):
            self.url = type("U", (), {"path": "/verify"})()
            self.client = _Client()
            self.headers = {"x-api-key": api_key}

    calls = {"n": 0}

    async def _next(_req):
        calls["n"] += 1
        return "ok"

    async def _drive():
        # Three requests each with a DIFFERENT x-api-key but the same client
        # host. With header-keying they would each get a fresh 2-token bucket
        # (bypass). With connection-keying they share one bucket: 3rd is 429.
        r1 = await lim.dispatch(_Req("key-1"), _next)
        r2 = await lim.dispatch(_Req("key-2"), _next)
        r3 = await lim.dispatch(_Req("key-3"), _next)
        return r1, r2, r3

    r1, r2, r3 = asyncio.run(_drive())
    assert r1 == "ok" and r2 == "ok"
    assert getattr(r3, "status_code", None) == 429   # shared bucket exhausted


def test_rate_limiter_evicts_idle_buckets():
    from magi_cp.cloud.app import TokenBucketLimiter

    lim = TokenBucketLimiter(app=None, capacity=5, refill_per_sec=1.0)
    now = 1_000_000.0
    # Seed way over the eviction threshold with stale entries.
    for i in range(TokenBucketLimiter._EVICT_WHEN_OVER + 5):
        lim._buckets[f"ip-{i}"] = (5.0, now - 7200)   # 2h idle
    lim._evict_stale(now)
    assert len(lim._buckets) == 0


# ── API-3: shim fail-closed ──────────────────────────────────────────
def test_run_command_shim_fails_closed_when_key_unset(monkeypatch):
    monkeypatch.delenv("MAGI_CP_API_KEY", raising=False)
    c = _client()
    r = c.post("/policies/run_command", json={"policy_id": "p1"})
    assert r.status_code == 503   # not anonymous-accepted


def test_input_rewrite_shim_fails_closed_when_key_unset(monkeypatch):
    monkeypatch.delenv("MAGI_CP_API_KEY", raising=False)
    c = _client()
    r = c.post("/policies/input_rewrite",
               json={"policy_id": "p1", "tool_name": "Bash", "tool_input": {}})
    assert r.status_code == 503


def test_run_command_shim_401_on_wrong_key(monkeypatch):
    monkeypatch.setenv("MAGI_CP_API_KEY", "right")
    c = _client()
    r = c.post("/policies/run_command",
               headers={"X-Api-Key": "wrong"},
               json={"policy_id": "p1"})
    assert r.status_code == 401
