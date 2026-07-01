"""P3: ``magi-cp session pack …`` CLI.

Covers the two behavioural contracts the design brief calls out:
  * ``activate`` POSTs to the cloud AND touches the per-session
    cache-invalidation sentinel so the gate refetches.
  * ``sticky`` persists the pack id under the resolved project-root key
    in ``sticky-packs.json``.
"""
import json
import os

import pytest


class _Resp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    """Isolate every CLI env knob so a polluted dev shell (exported
    MAGI_CP_* vars) cannot leak into the assertions."""
    for var in (
        "MAGI_CP_SESSION_ID", "CLAUDE_SESSION_ID", "MAGI_CP_TENANT_ID",
        "MAGI_CP_CLOUD_URL", "MAGI_CP_API_KEY", "MAGI_CP_SESSION_FILE",
    ):
        monkeypatch.delenv(var, raising=False)
    # Keep sentinel + sticky writes inside tmp.
    monkeypatch.setenv("MAGI_CP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv(
        "MAGI_CP_STICKY_PACKS_FILE", str(tmp_path / "sticky-packs.json"),
    )


def test_activate_posts_and_touches_invalidation(monkeypatch):
    from magi_cp.local import cli, session_cache

    captured: dict = {}

    def _fake_urlopen(req, timeout=15):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["api_key"] = req.get_header("X-api-key")
        return _Resp(
            b'{"active_packs":["user-pack/research"],'
            b'"floor_pack_id":"user-pack/floor"}'
        )

    monkeypatch.setattr(cli.urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setenv("MAGI_CP_API_KEY", "mcp_test")
    monkeypatch.setenv("MAGI_CP_SESSION_ID", "sess_abc")
    monkeypatch.setenv("MAGI_CP_CLOUD_URL", "http://cloud.test")

    rc = cli.cli(["pack", "activate", "user-pack/research"])

    assert rc == 0
    assert captured["method"] == "POST"
    assert captured["url"] == (
        "http://cloud.test/session/sess_abc/packs/activate"
    )
    assert captured["body"] == {"pack_id": "user-pack/research"}
    assert captured["api_key"] == "mcp_test"

    # The sentinel for (session, tenant=default) must now exist so the
    # gate serving this session drops its cached pack set.
    sentinel = session_cache.invalidation_file_path("sess_abc", "default")
    assert os.path.exists(sentinel)
    # And it carries a non-empty nonce (the granularity-independent
    # change signal).
    assert open(sentinel, encoding="utf-8").read().strip() != ""


def test_activate_without_session_id_is_error(monkeypatch, capsys):
    from magi_cp.local import cli

    monkeypatch.setenv("MAGI_CP_API_KEY", "mcp_test")
    # No session id anywhere; state file absent under tmp.
    rc = cli.cli(["pack", "activate", "user-pack/research"])
    assert rc == 2
    assert "session id" in capsys.readouterr().err.lower()


def test_sticky_writes_under_project_root_key(monkeypatch, tmp_path):
    from magi_cp.local import cli

    # A project with a .git marker at the root, cwd a nested subdir.
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    sub = proj / "src" / "deep"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)

    rc = cli.cli(["pack", "sticky", "user-pack/research"])
    assert rc == 0

    sticky_path = tmp_path / "sticky-packs.json"
    data = json.loads(sticky_path.read_text(encoding="utf-8"))

    # The key must be the marker root (proj), NOT the deep cwd.
    expected_key = cli.resolve_project_key()
    assert os.path.basename(expected_key) == "proj"
    assert data == {expected_key: ["user-pack/research"]}


def test_sticky_is_idempotent_and_appends(monkeypatch, tmp_path):
    from magi_cp.local import cli

    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    monkeypatch.chdir(proj)

    assert cli.cli(["pack", "sticky", "user-pack/a"]) == 0
    # Re-adding the same id is a no-op (no dupes).
    assert cli.cli(["pack", "sticky", "user-pack/a"]) == 0
    # A second id appends.
    assert cli.cli(["pack", "sticky", "user-pack/b"]) == 0

    data = json.loads((tmp_path / "sticky-packs.json").read_text("utf-8"))
    key = cli.resolve_project_key()
    assert data[key] == ["user-pack/a", "user-pack/b"]


def test_session_id_falls_back_to_state_file(monkeypatch, tmp_path):
    from magi_cp.local import cli

    state = tmp_path / "session.json"
    state.write_text(json.dumps({"session_id": "sess_from_file"}), "utf-8")
    monkeypatch.setenv("MAGI_CP_SESSION_FILE", str(state))
    assert cli.resolve_session_id() == "sess_from_file"


def test_dispatch_routes_session_to_cli(monkeypatch):
    """`magi-cp session …` reaches the session CLI via the dispatcher."""
    from magi_cp.cli import __main__ as entry
    from magi_cp.local import cli

    seen: dict = {}

    def _fake(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(cli, "cli", _fake)
    rc = entry.main(["session", "pack", "status"])
    assert rc == 0
    assert seen["argv"] == ["pack", "status"]
