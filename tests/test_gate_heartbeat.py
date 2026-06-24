"""Gate-side heartbeat helper tests.

`post_heartbeat()` reads endpoint_id from env/config, hashes the
managed-settings file, and POSTs to the cloud. We exercise the helper's
no-op paths (missing endpoint id, missing api key) and the digest hashing
without touching the network."""
from __future__ import annotations
import hashlib


from magi_cp.local import gate


def test_endpoint_id_reads_env(monkeypatch):
    monkeypatch.setenv("MAGI_CP_ENDPOINT_ID", "ep-from-env")
    assert gate._endpoint_id() == "ep-from-env"


def test_endpoint_id_reads_config_file(monkeypatch, tmp_path):
    monkeypatch.delenv("MAGI_CP_ENDPOINT_ID", raising=False)
    cfg = tmp_path / ".config" / "magi-cp" / "env"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        "# comment\n"
        "MAGI_CP_ENDPOINT_ID=ep-from-file\n"
        "OTHER_KEY=other\n"
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    assert gate._endpoint_id() == "ep-from-file"


def test_endpoint_id_handles_quoted_value(monkeypatch, tmp_path):
    monkeypatch.delenv("MAGI_CP_ENDPOINT_ID", raising=False)
    cfg = tmp_path / ".config" / "magi-cp" / "env"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('MAGI_CP_ENDPOINT_ID="quoted-ep"\n')
    monkeypatch.setenv("HOME", str(tmp_path))
    assert gate._endpoint_id() == "quoted-ep"


def test_endpoint_id_none_when_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("MAGI_CP_ENDPOINT_ID", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert gate._endpoint_id() is None


def test_managed_settings_digest_returns_sha256(monkeypatch, tmp_path):
    p = tmp_path / "settings.json"
    body = b'{"hello": "world"}'
    p.write_bytes(body)
    monkeypatch.setenv("MAGI_CP_MANAGED_SETTINGS_PATH", str(p))
    digest = gate._managed_settings_digest()
    assert digest == hashlib.sha256(body).hexdigest()


def test_managed_settings_digest_none_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "MAGI_CP_MANAGED_SETTINGS_PATH", str(tmp_path / "absent.json"),
    )
    assert gate._managed_settings_digest() is None


def test_post_heartbeat_no_op_when_endpoint_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("MAGI_CP_ENDPOINT_ID", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAGI_CP_API_KEY", "k")
    # No network call: helper returns None silently.
    assert gate.post_heartbeat() is None


def test_post_heartbeat_no_op_when_api_key_missing(monkeypatch):
    monkeypatch.setenv("MAGI_CP_ENDPOINT_ID", "ep-1")
    monkeypatch.delenv("MAGI_CP_API_KEY", raising=False)
    assert gate.post_heartbeat() is None


def test_post_heartbeat_posts_when_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_CP_ENDPOINT_ID", "ep-x")
    monkeypatch.setenv("MAGI_CP_API_KEY", "k")
    p = tmp_path / "ms.json"
    p.write_bytes(b'{"hooks": {}}')
    monkeypatch.setenv("MAGI_CP_MANAGED_SETTINGS_PATH", str(p))
    monkeypatch.setenv("MAGI_CP_CLOUD_URL", "http://stub.invalid")
    # Issue #1 P1 (#6): the gate now refuses plain HTTP to a non-loopback
    # host unless the operator opts in. Tests run in opt-in mode.
    monkeypatch.setenv("MAGI_CP_ALLOW_PLAIN_HTTP", "1")
    # Issue #1 P1 (#19): pid-file under HOME; isolate per-test.
    monkeypatch.setenv("MAGI_CP_LOCAL_DIR", str(tmp_path / "local"))

    captured: dict = {}

    class _Resp:
        def __init__(self, body): self._body = body
        def read(self): return self._body
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=5.0):
        captured["url"] = req.full_url
        captured["data"] = req.data
        captured["api_key"] = req.headers.get("X-api-key")
        # echo body
        return _Resp(b'{"endpoint_id": "ep-x"}')

    monkeypatch.setattr(gate.urllib.request, "urlopen", fake_urlopen)

    result = gate.post_heartbeat()
    assert result == {"endpoint_id": "ep-x"}
    assert captured["url"] == "http://stub.invalid/endpoints/ep-x/heartbeat"
    assert captured["api_key"] == "k"
    # Digest field present and correct
    import json as _json
    body = _json.loads(captured["data"])
    assert body["endpoint_id"] == "ep-x"
    assert body["active_policy_digest"] == hashlib.sha256(b'{"hooks": {}}').hexdigest()
    # Issue #1 P0 (#1): replay-resistant fields included.
    assert isinstance(body.get("ts"), int)
    assert isinstance(body.get("nonce"), str) and len(body["nonce"]) >= 8


def test_post_heartbeat_refuses_plain_http_to_non_loopback(monkeypatch, tmp_path):
    """Issue #1 P1 (#6): MITM defense — bare http:// to a public host
    is rejected at runtime so a captured first-fetch can't pin an
    attacker pubkey."""
    monkeypatch.setenv("MAGI_CP_ENDPOINT_ID", "ep-x")
    monkeypatch.setenv("MAGI_CP_API_KEY", "k")
    monkeypatch.setenv("MAGI_CP_CLOUD_URL", "http://cloud.example.com")
    monkeypatch.delenv("MAGI_CP_ALLOW_PLAIN_HTTP", raising=False)
    monkeypatch.setenv("MAGI_CP_LOCAL_DIR", str(tmp_path / "local"))
    # No fake urlopen — if the scheme guard fails, we'd attempt a real
    # DNS lookup. With the guard, urlopen never gets called.
    called = {"hit": False}

    def fake_urlopen(req, timeout=5.0):
        called["hit"] = True
        raise gate.urllib.error.URLError("should not be called")

    monkeypatch.setattr(gate.urllib.request, "urlopen", fake_urlopen)
    result = gate.post_heartbeat()
    assert result is None
    assert called["hit"] is False


def test_post_heartbeat_debounces_when_recent(monkeypatch, tmp_path):
    """Issue #1 P1 (#19): the helper skips when the previous successful
    heartbeat is younger than `MAGI_CP_HEARTBEAT_MIN_INTERVAL`."""
    monkeypatch.setenv("MAGI_CP_ENDPOINT_ID", "ep-x")
    monkeypatch.setenv("MAGI_CP_API_KEY", "k")
    monkeypatch.setenv("MAGI_CP_CLOUD_URL", "http://127.0.0.1:8787")
    monkeypatch.setenv("MAGI_CP_LOCAL_DIR", str(tmp_path / "local"))
    monkeypatch.setenv("MAGI_CP_HEARTBEAT_MIN_INTERVAL", "3600")
    # Pre-populate the last-heartbeat marker as "now".
    import time as _time
    (tmp_path / "local").mkdir(parents=True, exist_ok=True)
    (tmp_path / "local" / "heartbeat.last").write_text(str(int(_time.time())))

    called = {"hit": False}

    def fake_urlopen(req, timeout=5.0):
        called["hit"] = True
        raise gate.urllib.error.URLError("should not be called")

    monkeypatch.setattr(gate.urllib.request, "urlopen", fake_urlopen)
    assert gate.post_heartbeat() is None
    assert called["hit"] is False
    # force=True bypasses the debounce
    monkeypatch.setattr(gate.urllib.request, "urlopen",
                         lambda req, timeout=5.0: _StubResp(b'{}'))
    assert gate.post_heartbeat(force=True) == {}


class _StubResp:
    def __init__(self, body): self._body = body
    def read(self): return self._body
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_post_heartbeat_swallows_network_errors(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_CP_ENDPOINT_ID", "ep-x")
    monkeypatch.setenv("MAGI_CP_API_KEY", "k")
    monkeypatch.setenv("MAGI_CP_CLOUD_URL", "http://stub.invalid")
    monkeypatch.setenv(
        "MAGI_CP_MANAGED_SETTINGS_PATH", str(tmp_path / "absent.json"),
    )

    def boom(req, timeout=5.0):
        raise gate.urllib.error.URLError("connection refused")

    monkeypatch.setattr(gate.urllib.request, "urlopen", boom)
    # Helper swallows network errors and returns None
    assert gate.post_heartbeat() is None
