"""v2.0-W8b — observability surfaces.

  - /metrics endpoint produces Prometheus text format
  - Counters increment on /verify dispatch
  - Logger is structlog-shaped when configured, stdlib fallback otherwise
"""
import os
import tempfile

import pytest
from fastapi.testclient import TestClient


def _tmp_store():
    f = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    f.write("[]"); f.close()
    return f.name


def _client_production_like(monkeypatch, tmp_path):
    """Build app via _build_production_app so /metrics is attached."""
    monkeypatch.setenv("MAGI_CP_KEY_DIR", str(tmp_path / "kd"))
    monkeypatch.setenv("MAGI_CP_DSN", "sqlite:///:memory:")
    monkeypatch.setenv("MAGI_CP_POLICY_STORE", _tmp_store())
    monkeypatch.setenv("MAGI_CP_API_KEY", "test-key")
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", "test-hitl")
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", "test-admin")
    from magi_cp.cloud.app import _build_production_app
    return TestClient(_build_production_app())


def test_metrics_endpoint_exposes_prometheus_format(monkeypatch, tmp_path):
    c = _client_production_like(monkeypatch, tmp_path)
    r = c.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    body = r.text
    # Prometheus exposition starts with `# HELP` for each metric
    assert "# HELP magi_cp_verify_total" in body
    assert "# HELP magi_cp_verify_latency_seconds" in body
    assert "# HELP magi_cp_compile_total" in body


def test_metrics_endpoint_needs_no_auth(monkeypatch, tmp_path):
    """Public on the listener; operator restricts via network policy."""
    c = _client_production_like(monkeypatch, tmp_path)
    r = c.get("/metrics")
    assert r.status_code == 200   # NO 401


def test_verify_dispatch_increments_counter(monkeypatch, tmp_path):
    c = _client_production_like(monkeypatch, tmp_path)
    # Pre-baseline
    pre = c.get("/metrics").text
    # Dispatch one verify
    r = c.post("/verify/privilege_scan",
               headers={"X-Api-Key": "test-key"},
               json={"payload": {"text": "clean text"}})
    assert r.status_code == 200
    post = c.get("/metrics").text
    # The counter should now contain a labeled sample with step=privilege_scan
    assert 'step="privilege_scan"' in post
    assert pre.count('step="privilege_scan"') < post.count('step="privilege_scan"')


def test_get_logger_returns_a_logger():
    """Whether structlog is installed or not, get_logger() returns something
    bind-able / info-callable."""
    from magi_cp.cloud.observability import get_logger
    log = get_logger("test")
    # Either structlog BoundLogger (has bind/info) or stdlib Logger (has info)
    assert hasattr(log, "info")


def test_configure_structlog_is_idempotent():
    from magi_cp.cloud.observability import configure_structlog
    configure_structlog()
    configure_structlog()   # second call must not raise
