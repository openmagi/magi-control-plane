"""SessionStart auto pack-activation (gate side).

The gate's SessionStart handler reads MAGI_CP_AUTO_ACTIVATE_PACKS
(env / config file), then best-effort POSTs an activate for each pack.
It must NEVER fail a session: unset config = no-op, a failing POST is
swallowed, and the verdict is always allow. Design:
2026-07-03-sessionstart-auto-pack-activation-design (private planning repo).
"""
from __future__ import annotations

from magi_cp.local import gate


# ── config reader ────────────────────────────────────────────────────
def test_auto_activate_packs_reads_env(monkeypatch):
    monkeypatch.setenv("MAGI_CP_AUTO_ACTIVATE_PACKS", "pack/a, pack/b ,pack/c")
    assert gate._auto_activate_packs() == ["pack/a", "pack/b", "pack/c"]


def test_auto_activate_packs_reads_config_file(monkeypatch, tmp_path):
    monkeypatch.delenv("MAGI_CP_AUTO_ACTIVATE_PACKS", raising=False)
    cfg = tmp_path / ".config" / "magi-cp" / "env"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        "# comment\n"
        'MAGI_CP_AUTO_ACTIVATE_PACKS="pack/x,pack/y"\n'
        "OTHER=z\n"
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    assert gate._auto_activate_packs() == ["pack/x", "pack/y"]


def test_auto_activate_packs_empty_when_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("MAGI_CP_AUTO_ACTIVATE_PACKS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert gate._auto_activate_packs() == []


def test_auto_activate_packs_empty_string_is_no_packs(monkeypatch):
    monkeypatch.setenv("MAGI_CP_AUTO_ACTIVATE_PACKS", "  , ,")
    assert gate._auto_activate_packs() == []


# ── activate POST helper ─────────────────────────────────────────────
def test_post_activate_no_op_without_key(monkeypatch):
    monkeypatch.delenv("MAGI_CP_API_KEY", raising=False)
    assert gate.post_session_pack_activate("sess-1", "pack/a") is False


def test_post_activate_no_op_on_empty_ids(monkeypatch):
    monkeypatch.setenv("MAGI_CP_API_KEY", "k")
    assert gate.post_session_pack_activate("", "pack/a") is False
    assert gate.post_session_pack_activate("sess-1", "") is False


def test_post_activate_posts_when_configured(monkeypatch):
    monkeypatch.setenv("MAGI_CP_API_KEY", "k")
    monkeypatch.setenv("MAGI_CP_CLOUD_URL", "http://stub.invalid")
    monkeypatch.setenv("MAGI_CP_ALLOW_PLAIN_HTTP", "1")
    captured: dict = {}

    class _Resp:
        status = 200
        def read(self): return b"{}"
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["key"] = req.headers.get("X-api-key")
        captured["body"] = req.data
        return _Resp()

    monkeypatch.setattr(gate.urllib.request, "urlopen", _fake_urlopen)
    ok = gate.post_session_pack_activate("sess-42", "pack/a")
    assert ok is True
    assert captured["url"].endswith("/session/sess-42/packs/activate")
    assert captured["method"] == "POST"
    assert captured["key"] == "k"
    assert b"pack/a" in captured["body"]


# ── decide(SessionStart) end to end ──────────────────────────────────
def test_sessionstart_activates_each_configured_pack(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_CP_API_KEY", "k")
    monkeypatch.setenv("MAGI_CP_CLOUD_URL", "http://stub.invalid")
    monkeypatch.setenv("MAGI_CP_ALLOW_PLAIN_HTTP", "1")
    monkeypatch.setenv("MAGI_CP_AUTO_ACTIVATE_PACKS", "pack/a,pack/b")
    monkeypatch.setenv("MAGI_CP_LOCAL_DIR", str(tmp_path / "local"))

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        gate, "post_session_pack_activate",
        lambda sid, pid, **kw: calls.append((sid, pid)) or True,
    )
    verdict = gate.decide({
        "hook_event_name": "SessionStart",
        "session_id": "sess-9",
    })
    assert verdict.decision == "allow"
    assert verdict.hook_event_name == "SessionStart"
    assert calls == [("sess-9", "pack/a"), ("sess-9", "pack/b")]


def test_sessionstart_no_activate_when_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("MAGI_CP_AUTO_ACTIVATE_PACKS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAGI_CP_LOCAL_DIR", str(tmp_path / "local"))
    calls: list = []
    monkeypatch.setattr(
        gate, "post_session_pack_activate",
        lambda *a, **kw: calls.append(a) or True,
    )
    verdict = gate.decide({
        "hook_event_name": "SessionStart", "session_id": "sess-9",
    })
    assert verdict.decision == "allow"
    assert calls == []


def test_sessionstart_allows_even_when_activate_raises(monkeypatch, tmp_path):
    # Fail-open: a throwing activate must never deny / crash the session.
    monkeypatch.setenv("MAGI_CP_AUTO_ACTIVATE_PACKS", "pack/a")
    monkeypatch.setenv("MAGI_CP_API_KEY", "k")
    monkeypatch.setenv("MAGI_CP_LOCAL_DIR", str(tmp_path / "local"))

    def _boom(*a, **kw):
        raise RuntimeError("cloud down")

    monkeypatch.setattr(gate, "post_session_pack_activate", _boom)
    verdict = gate.decide({
        "hook_event_name": "SessionStart", "session_id": "sess-9",
    })
    assert verdict.decision == "allow"


def test_sessionstart_allows_on_non_utf8_config(monkeypatch, tmp_path):
    # A config file with invalid UTF-8 bytes must not crash the session:
    # _auto_activate_packs swallows the UnicodeDecodeError (a ValueError),
    # and the whole side-effect is guarded, so SessionStart still allows.
    monkeypatch.delenv("MAGI_CP_AUTO_ACTIVATE_PACKS", raising=False)
    cfg = tmp_path / ".config" / "magi-cp" / "env"
    cfg.parent.mkdir(parents=True)
    cfg.write_bytes(b"MAGI_CP_AUTO_ACTIVATE_PACKS=\xff\xfe\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAGI_CP_LOCAL_DIR", str(tmp_path / "local"))
    # reader itself is fail-safe
    assert gate._auto_activate_packs() == []
    verdict = gate.decide({
        "hook_event_name": "SessionStart", "session_id": "sess-9",
    })
    assert verdict.decision == "allow"


def test_sessionstart_quotes_session_id_in_url(monkeypatch):
    # A session_id with path metacharacters must be percent-encoded so it
    # cannot reshape the request path against the API.
    monkeypatch.setenv("MAGI_CP_API_KEY", "k")
    monkeypatch.setenv("MAGI_CP_CLOUD_URL", "http://stub.invalid")
    monkeypatch.setenv("MAGI_CP_ALLOW_PLAIN_HTTP", "1")
    captured: dict = {}

    class _Resp:
        status = 200
        def read(self): return b"{}"
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _Resp()

    monkeypatch.setattr(gate.urllib.request, "urlopen", _fake_urlopen)
    gate.post_session_pack_activate("a/../b", "pack/a")
    assert "/session/a%2F..%2Fb/packs/activate" in captured["url"]
