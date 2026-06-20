"""v2.0-W7b — `magi-cp keys` CLI + /pubkey returns multi-key map."""
import os
import tempfile

import pytest
from fastapi.testclient import TestClient


# ── /pubkey returns active + keys map ──────────────────────────────
def test_pubkey_returns_active_and_keys_map(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CP_KEY_DIR", str(tmp_path / "kd"))
    from magi_cp.cloud.app import create_app
    f = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    f.write("[]"); f.close()
    app = create_app(dsn="sqlite:///:memory:", policy_store_path=f.name)
    c = TestClient(app)
    r = c.get("/pubkey")
    assert r.status_code == 200
    body = r.json()
    assert body["kid"]
    assert "BEGIN PUBLIC KEY" in body["pubkey_pem"]
    # multi-key map present
    assert "keys" in body and isinstance(body["keys"], dict)
    assert body["kid"] in body["keys"]
    assert body["keys"][body["kid"]] == body["pubkey_pem"]


def test_pubkey_after_rotation_returns_two_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CP_KEY_DIR", str(tmp_path / "kd"))
    from magi_cp.cloud.app import create_app
    from magi_cp.cloud.keys import KeyStore
    f = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    f.write("[]"); f.close()
    # build app once → ensure_keypair() runs
    app = create_app(dsn="sqlite:///:memory:", policy_store_path=f.name)
    # rotate via KeyStore directly (cron would do this out-of-band)
    ks = KeyStore(dir=str(tmp_path / "kd"))
    old_kid = ks.active_kid()
    new_kid = ks.rotate()
    # need a NEW app instance to re-read active_kid? No — /pubkey reads
    # ks.active_kid() at request time, not at boot.
    c = TestClient(app)
    body = c.get("/pubkey").json()
    assert body["kid"] == new_kid
    assert set(body["keys"].keys()) == {old_kid, new_kid}


# ── CLI ────────────────────────────────────────────────────────────
class TestKeysCli:
    def test_rotate_then_list_shows_both(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("MAGI_CP_KEY_DIR", str(tmp_path / "kd"))
        from magi_cp.cli.keys import cli
        # First rotate also calls ensure_keypair internally
        assert cli(["rotate"]) == 0
        out_rotate = capsys.readouterr().out
        assert "rotated:" in out_rotate

        assert cli(["list"]) == 0
        out_list = capsys.readouterr().out
        # two lines, one "active" one "verifying"
        lines = [l for l in out_list.strip().splitlines() if l]
        assert len(lines) == 2
        assert any("active" in l for l in lines)
        assert any("verifying" in l for l in lines)

    def test_revoke_old_kid_succeeds(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("MAGI_CP_KEY_DIR", str(tmp_path / "kd"))
        from magi_cp.cli.keys import cli
        from magi_cp.cloud.keys import KeyStore
        # Start with key1, rotate to key2, revoke key1
        cli(["rotate"])
        capsys.readouterr()
        ks = KeyStore(dir=str(tmp_path / "kd"))
        active = ks.active_kid()
        old_kid = [k for k in ks.list_kids() if k != active][0]
        assert cli(["revoke", old_kid]) == 0
        out = capsys.readouterr().out
        assert "revoked" in out
        # list now shows only one (the active)
        cli(["list"])
        out = capsys.readouterr().out.strip().splitlines()
        assert len(out) == 1

    def test_revoke_active_refused(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("MAGI_CP_KEY_DIR", str(tmp_path / "kd"))
        from magi_cp.cli.keys import cli
        from magi_cp.cloud.keys import KeyStore
        cli(["rotate"])
        capsys.readouterr()
        ks = KeyStore(dir=str(tmp_path / "kd"))
        active = ks.active_kid()
        rc = cli(["revoke", active])
        assert rc == 2
        err = capsys.readouterr().err
        assert "active" in err

    def test_unknown_subcommand_returns_2(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MAGI_CP_KEY_DIR", str(tmp_path / "kd"))
        from magi_cp.cli.keys import cli
        with pytest.raises(SystemExit) as e:
            cli(["bogus"])
        assert e.value.code == 2
