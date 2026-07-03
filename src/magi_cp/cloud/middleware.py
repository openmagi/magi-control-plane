"""HTTP middleware + a couple of tiny response/regex helpers for the cloud
FastAPI app.

Extracted verbatim from ``app.py`` (modularization design
2026-07-03-cloud-app-modularization-design.md). Behavior-preserving: the
classes and helpers are byte-identical to their former in-``app.py`` form;
``app.py`` re-imports them so every existing reference (routes, tests) keeps
working unchanged.
"""
from __future__ import annotations

import asyncio
import re  # noqa: F401  (kept for the _bounded_regex_search type hint)
import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


def _json_response(status: int, payload: dict):
    from fastapi.responses import JSONResponse
    return JSONResponse(payload, status_code=status)


class _BodyTooLarge(Exception):
    pass


class MaxBodyMiddleware(BaseHTTPMiddleware):
    """413 on Content-Length OR by accumulating a streamed/chunked body."""

    def __init__(self, app, limit: int):
        super().__init__(app)
        self.limit = limit

    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > self.limit:
            return _json_response(413, {"detail": "request body too large"})
        # Wrap ASGI receive to count bytes for chunked / unknown-CL bodies
        recv = request._receive
        consumed = 0
        limit = self.limit

        async def capped_receive():
            nonlocal consumed
            msg = await recv()
            if msg["type"] == "http.request":
                body = msg.get("body") or b""
                consumed += len(body)
                if consumed > limit:
                    raise _BodyTooLarge()
            return msg

        request._receive = capped_receive
        try:
            return await call_next(request)
        except _BodyTooLarge:
            return _json_response(413, {"detail": "request body too large"})


class TokenBucketLimiter(BaseHTTPMiddleware):
    """Per-key (or per-IP fallback) token bucket. Tiny, in-process, adequate
    for v0 single-pod. Swap for slowapi/Redis in P5.
    """
    def __init__(self, app, *, capacity: int = 60, refill_per_sec: float = 10.0):
        super().__init__(app)
        self.cap = capacity
        self.refill = refill_per_sec
        self._buckets: dict[str, tuple[float, float]] = {}   # key -> (tokens, last_ts)

    # Evict idle buckets once the map grows past this, so a flood of
    # distinct keys cannot grow _buckets without bound (API-2 memory DoS).
    _EVICT_WHEN_OVER = 10_000
    _EVICT_IDLE_S = 3600

    def _evict_stale(self, now: float) -> None:
        if len(self._buckets) <= self._EVICT_WHEN_OVER:
            return
        cutoff = now - self._EVICT_IDLE_S
        stale = [k for k, (_, last) in self._buckets.items() if last < cutoff]
        for k in stale:
            self._buckets.pop(k, None)

    async def dispatch(self, request: Request, call_next):
        # No throttling on health/pubkey (cheap, public)
        if request.url.path in ("/healthz", "/pubkey"):
            return await call_next(request)
        # Key on the CONNECTION source, never a caller-supplied header.
        # Keying on x-api-key let an attacker mint a fresh bucket per request
        # (rate-limit bypass) and grow _buckets without bound (API-2). Behind
        # a trusted reverse proxy every request shares the proxy IP's bucket;
        # a future improvement is parsing X-Forwarded-For from a configured
        # trusted proxy, but the safe default is the socket peer.
        key = request.client.host if request.client else "anon"
        now = time.time()
        self._evict_stale(now)
        tokens, last = self._buckets.get(key, (self.cap, now))
        tokens = min(self.cap, tokens + (now - last) * self.refill)
        if tokens < 1:
            self._buckets[key] = (tokens, now)
            return _json_response(429, {"detail": "rate limit exceeded"})
        self._buckets[key] = (tokens - 1, now)
        return await call_next(request)


async def _bounded_regex_search(
    rx: "re.Pattern[str]", text: str, *, timeout: float = 2.0,
) -> bool:
    """Run ``rx.search(text)`` in a worker thread with a wall-clock cap.

    A catastrophic-backtracking pattern from an authenticated caller would
    otherwise wedge the event loop and stall every other tenant's request
    (API-1). Running the search off the loop keeps the loop responsive; on
    timeout we return False (deny) rather than block. The pattern is compiled
    on the calling thread first, so a bad pattern still surfaces as a 422 and
    only the (immutable, thread-safe) compiled object crosses into the thread.

    Note: Python's ``re`` cannot be interrupted mid-scan, so a timed-out
    search keeps running in its thread until it finishes; bounding the number
    of concurrent inline regex evaluations is a follow-up. The loop itself is
    never blocked.
    """
    def _run() -> bool:
        return rx.search(text) is not None
    try:
        return await asyncio.wait_for(asyncio.to_thread(_run), timeout=timeout)
    except asyncio.TimeoutError:
        return False


__all__ = [
    "MaxBodyMiddleware",
    "TokenBucketLimiter",
    "_BodyTooLarge",
    "_json_response",
    "_bounded_regex_search",
]
