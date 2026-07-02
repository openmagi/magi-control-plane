"""PR-G: local transit + enforcement integrity.

- TRANSIT-1: `magi-cp share` refuses plain http:// to a non-loopback host
  (tenant key would travel in cleartext) unless --allow-plain-http.
- LOCAL-1: run_command specs must be signed BY DEFAULT (opt out with =0).
- PLUGIN-1: the installed gate shim resolves an absolute path and fails closed.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from magi_cp.cli import share
from magi_cp.local.gate import _require_signed_run_command_spec


def _write_transcript(projects_dir: Path, slug: str, sid: str) -> None:
    d = projects_dir / slug
    d.mkdir(parents=True, exist_ok=True)
    events = [
        {"type": "user", "sessionId": sid,
         "message": {"role": "user", "content": "do the thing"}},
        {"type": "assistant", "sessionId": sid,
         "message": {"role": "assistant", "model": "claude-opus-4-8",
                     "content": [{"type": "text", "text": "done"}],
                     "usage": {"input_tokens": 5, "output_tokens": 2}}},
    ]
    (d / f"{sid}.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


# ── TRANSIT-1 ────────────────────────────────────────────────────────
def _run_share(tmp_path, cloud_url, *extra, monkeypatch):
    _write_transcript(tmp_path, "proj", "sess-1")
    uploaded = {"called": False}
    monkeypatch.setattr(
        share, "upload",
        lambda *a, **k: uploaded.__setitem__("called", True) or {"token": "t", "url": "u"},
    )
    rc = share.cli([
        "sess-1", "--projects-dir", str(tmp_path),
        "--api-key", "mcp_test", "--cloud-url", cloud_url, *extra,
    ])
    return rc, uploaded["called"]


def test_share_refuses_remote_plain_http(tmp_path, monkeypatch, capsys):
    rc, called = _run_share(tmp_path, "http://cp.example.com", monkeypatch=monkeypatch)
    assert rc == 2
    assert called is False   # rejected before upload
    assert "cleartext" in capsys.readouterr().err


def test_share_allows_remote_plain_http_with_override(tmp_path, monkeypatch):
    rc, called = _run_share(
        tmp_path, "http://cp.example.com", "--allow-plain-http",
        monkeypatch=monkeypatch)
    assert rc == 0
    assert called is True


def test_share_allows_loopback_plain_http(tmp_path, monkeypatch):
    rc, called = _run_share(tmp_path, "http://127.0.0.1:8788", monkeypatch=monkeypatch)
    assert rc == 0
    assert called is True


def test_share_allows_remote_https(tmp_path, monkeypatch):
    rc, called = _run_share(tmp_path, "https://cp.example.com", monkeypatch=monkeypatch)
    assert rc == 0
    assert called is True


# ── LOCAL-1 ──────────────────────────────────────────────────────────
def test_signed_spec_required_by_default(monkeypatch):
    monkeypatch.delenv("MAGI_CP_REQUIRE_SIGNED_RUN_COMMAND_SPEC", raising=False)
    assert _require_signed_run_command_spec() is True


def test_signed_spec_opt_out_only_with_zero(monkeypatch):
    # Only an explicit "0" disables; every other value (including "") keeps
    # signing required, since the check is `!= "0"`.
    monkeypatch.setenv("MAGI_CP_REQUIRE_SIGNED_RUN_COMMAND_SPEC", "0")
    assert _require_signed_run_command_spec() is False
    for v in ("1", "yes", "true", ""):
        monkeypatch.setenv("MAGI_CP_REQUIRE_SIGNED_RUN_COMMAND_SPEC", v)
        assert _require_signed_run_command_spec() is True


# ── PLUGIN-1: gate shim ──────────────────────────────────────────────
_SHIM = Path(__file__).resolve().parents[1] / "scripts" / "magi-gate.sh"
_BASH = "/bin/bash"


def _install_shim(tmp_path: Path, baked: str) -> Path:
    """Write the shim with the @MAGI_CP_GATE_BIN@ sentinel replaced by `baked`
    (empty string leaves it unbaked)."""
    installed = tmp_path / "magi-gate.sh"
    text = _SHIM.read_text()
    if baked:
        # Only the first sentinel (the assignment); the second is the
        # substitution-detection comparison and must stay intact (matches the
        # installer's replace(..., 1)).
        text = text.replace("@MAGI_CP_GATE_BIN@", baked, 1)
    installed.write_text(text)
    return installed


def test_gate_shim_fails_closed_when_baked_binary_missing(tmp_path):
    # Installer baked a path that no longer exists -> block, not exec-nothing.
    shim = _install_shim(tmp_path, str(tmp_path / "does-not-exist"))
    r = subprocess.run([_BASH, str(shim)], env={"PATH": "/usr/bin:/bin"},
                       capture_output=True, text=True)
    assert r.returncode == 0
    assert '"decision":"block"' in r.stdout


def test_gate_shim_execs_baked_binary(tmp_path):
    gate = tmp_path / "real-gate"
    gate.write_text("#!/bin/bash\necho GATE_RAN\n")
    gate.chmod(0o755)
    shim = _install_shim(tmp_path, str(gate))
    r = subprocess.run([_BASH, str(shim)], env={"PATH": "/usr/bin:/bin"},
                       capture_output=True, text=True)
    assert r.returncode == 0
    assert "GATE_RAN" in r.stdout


def test_gate_shim_baked_path_beats_path_shadow(tmp_path):
    # The baked absolute path must win over a PATH-shadowing magi-cp-gate.
    baked = tmp_path / "real-gate"
    baked.write_text("#!/bin/bash\necho BAKED_RAN\n")
    baked.chmod(0o755)
    shadow_dir = tmp_path / "shadow"
    shadow_dir.mkdir()
    shadow = shadow_dir / "magi-cp-gate"
    shadow.write_text("#!/bin/bash\necho SHADOW_RAN\n")
    shadow.chmod(0o755)

    shim = _install_shim(tmp_path, str(baked))
    r = subprocess.run(
        [_BASH, str(shim)],
        env={"PATH": f"{shadow_dir}:/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "BAKED_RAN" in r.stdout
    assert "SHADOW_RAN" not in r.stdout
