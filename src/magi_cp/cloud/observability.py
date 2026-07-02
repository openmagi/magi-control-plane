"""v2.0-W8b — production observability.

Two surfaces:

  1. structlog JSON logger — `get_logger(name)` returns a structlog logger
     that emits one JSON object per record to stderr. Fields like tenant_id,
     kid, subject, payload_hash are added via bind() at call sites so log
     aggregators can index them.

  2. Prometheus metrics — counters + histograms exposed at GET /metrics.
     Cheap to emit, no auth (operator-facing only; never exposed publicly).

Both surfaces are OPT-IN at build time:
  - structlog: imported only when configure_structlog() is called from
    _build_production_app(). Tests do not load it (so test output stays
    pretty and stderr free of JSON noise).
  - prometheus: the /metrics route is attached only when the operator
    calls attach_metrics(app). Tests don't expose metrics.

If `structlog` or `prometheus_client` aren't installed (the [observability]
extra wasn't picked up), the helpers no-op gracefully — the app still boots.
"""
from __future__ import annotations

from typing import Any

# Module-level so FastAPI's get_type_hints() can resolve the `request: Request`
# annotation on metrics_endpoint (a locally-imported name is invisible to it,
# and the route would then treat `request` as a required query param -> 422).
# fastapi is a base dependency, so this import is always available.
from fastapi import Request


# ── structlog setup ────────────────────────────────────────────────
_STRUCTLOG_CONFIGURED = False


def configure_structlog() -> None:
    """Idempotent global configuration. Safe to call multiple times."""
    global _STRUCTLOG_CONFIGURED
    if _STRUCTLOG_CONFIGURED:
        return
    try:
        import structlog
    except ImportError:
        return   # extra not installed; logging falls back to stdlib
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
    )
    _STRUCTLOG_CONFIGURED = True


def get_logger(name: str | None = None) -> Any:
    """Return a structured logger. Falls back to stdlib logging when the
    `observability` extra isn't installed."""
    try:
        import structlog
        return structlog.get_logger(name)
    except ImportError:
        import logging
        return logging.getLogger(name or "magi_cp")


# ── Prometheus metrics ─────────────────────────────────────────────
_METRICS_REGISTRY = None
_METRICS = {}


def _ensure_metrics():
    """Create the metric objects once, attach to the default registry."""
    global _METRICS_REGISTRY
    if _METRICS_REGISTRY is not None:
        return _METRICS
    try:
        from prometheus_client import (
            CollectorRegistry, Counter, Histogram,
        )
    except ImportError:
        return None
    reg = CollectorRegistry(auto_describe=False)
    _METRICS["verify_total"] = Counter(
        "magi_cp_verify_total",
        "Verifier dispatches by step and verdict.",
        labelnames=("step", "verdict", "tenant_id"),
        registry=reg,
    )
    _METRICS["verify_latency_seconds"] = Histogram(
        "magi_cp_verify_latency_seconds",
        "Per-request /verify/{step} latency.",
        labelnames=("step",),
        registry=reg,
        buckets=(0.005, 0.025, 0.1, 0.5, 1.0, 5.0),
    )
    _METRICS["compile_total"] = Counter(
        "magi_cp_compile_total",
        "/policies/compile invocations by review-ok outcome.",
        labelnames=("review_ok",),
        registry=reg,
    )
    _METRICS["compile_latency_seconds"] = Histogram(
        "magi_cp_compile_latency_seconds",
        "End-to-end compile (compiler + reviewer LLM calls).",
        registry=reg,
        buckets=(0.5, 2.0, 5.0, 10.0, 30.0, 60.0),
    )
    _METRICS["ledger_append_total"] = Counter(
        "magi_cp_ledger_append_total",
        "Ledger entries appended by tenant + verdict.",
        labelnames=("tenant_id", "verdict"),
        registry=reg,
    )
    _METRICS["hitl_enqueue_total"] = Counter(
        "magi_cp_hitl_enqueue_total",
        "HITL items enqueued by tenant.",
        labelnames=("tenant_id",),
        registry=reg,
    )
    _METRICS_REGISTRY = reg
    return _METRICS


def get_metric(name: str):
    """Lookup a metric by name; returns None if observability extra not
    installed. Call sites should guard: `m = get_metric(...); if m: m.inc()`.
    """
    metrics = _ensure_metrics()
    if metrics is None:
        return None
    return metrics.get(name)


def attach_metrics(app) -> None:
    """Add an operator-facing `/metrics` endpoint.

    The counters carry a `tenant_id` label, so on an exposed instance an
    unauthenticated scraper could enumerate tenants + their activity (OBS-1).
    Two defenses, both opt-in:
      - MAGI_CP_METRICS_TOKEN: when set, require `Authorization: Bearer <token>`
        (constant-time compare). Unset keeps the legacy no-auth behavior for
        deployments that rely on network isolation instead.
      - charts/magi-cp NetworkPolicy (networkPolicy.enabled): restrict ingress
        to the monitoring namespace. The chart now ships that template rather
        than only claiming it in a docstring.
    """
    import os

    metrics = _ensure_metrics()
    if metrics is None:
        return   # extra not installed; skip
    try:
        from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
        from fastapi.responses import Response
    except ImportError:
        return

    @app.get("/metrics", include_in_schema=False)
    def metrics_endpoint(request: Request) -> Response:
        token = os.environ.get("MAGI_CP_METRICS_TOKEN")
        if token:
            import hmac as _hmac
            presented = (
                request.headers.get("authorization") or ""
            ).removeprefix("Bearer ").strip()
            if not presented or not _hmac.compare_digest(presented, token):
                return Response("unauthorized", status_code=401)
        return Response(
            generate_latest(_METRICS_REGISTRY),
            media_type=CONTENT_TYPE_LATEST,
        )
