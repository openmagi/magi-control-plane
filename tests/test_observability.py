"""v2.0-W8b — observability surfaces.

  - /metrics endpoint produces Prometheus text format
  - Counters increment on /verify dispatch
  - Logger is structlog-shaped when configured, stdlib fallback otherwise
"""
import tempfile

from fastapi.testclient import TestClient


def _tmp_store():
    f = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    f.write("[]")
    f.close()
    return f.name


def _client_production_like(monkeypatch, tmp_path, *, metrics_public=True):
    """Build app via _build_production_app so /metrics is attached.

    /metrics is fail-closed by default now (OBS-1), so tests that scrape it
    default to the explicit MAGI_CP_METRICS_PUBLIC=1 opt-out (the network-
    isolated deployment case). Pass metrics_public=False to exercise the
    fail-closed default.
    """
    monkeypatch.setenv("MAGI_CP_KEY_DIR", str(tmp_path / "kd"))
    monkeypatch.setenv("MAGI_CP_DSN", "sqlite:///:memory:")
    monkeypatch.setenv("MAGI_CP_POLICY_STORE", _tmp_store())
    monkeypatch.setenv("MAGI_CP_API_KEY", "test-key")
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", "test-hitl")
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", "test-admin")
    if metrics_public:
        monkeypatch.setenv("MAGI_CP_METRICS_PUBLIC", "1")
    else:
        monkeypatch.delenv("MAGI_CP_METRICS_PUBLIC", raising=False)
    monkeypatch.delenv("MAGI_CP_METRICS_TOKEN", raising=False)
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


def test_metrics_fail_closed_by_default(monkeypatch, tmp_path):
    """OBS-1: with neither MAGI_CP_METRICS_TOKEN nor MAGI_CP_METRICS_PUBLIC set,
    an unauthenticated scrape is denied (the endpoint shares the API port and
    carries tenant_id labels)."""
    c = _client_production_like(monkeypatch, tmp_path, metrics_public=False)
    assert c.get("/metrics").status_code == 401


def test_metrics_public_opt_out_serves_without_auth(monkeypatch, tmp_path):
    """MAGI_CP_METRICS_PUBLIC=1 is the explicit network-isolated opt-out."""
    c = _client_production_like(monkeypatch, tmp_path)  # public=True by default
    assert c.get("/metrics").status_code == 200


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
