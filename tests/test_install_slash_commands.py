"""P3: install.sh drops the four /magi:pack-* slash command files.

Runs the real installer in its commands-only mode
(MAGI_CP_INSTALL_COMMANDS_ONLY=1) against a scratch HOME so we never
touch docker or a real ~/.claude tree.
"""
import os
import stat
import subprocess
from pathlib import Path

import pytest

_INSTALL_SH = Path(__file__).resolve().parents[1] / "web" / "public" / "install.sh"
_COMMAND_FILES = (
    "pack.md",
    "pack-activate.md",
    "pack-deactivate.md",
    "pack-status.md",
)


def _run_installer(home: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["MAGI_CP_INSTALL_COMMANDS_ONLY"] = "1"
    return subprocess.run(
        ["bash", str(_INSTALL_SH)],
        env=env, capture_output=True, text=True, timeout=60,
    )


@pytest.mark.skipif(not _INSTALL_SH.exists(), reason="install.sh missing")
def test_install_drops_four_command_files(tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    result = _run_installer(home)
    assert result.returncode == 0, result.stderr

    cmd_dir = home / ".claude" / "commands" / "magi"
    assert cmd_dir.is_dir()
    for name in _COMMAND_FILES:
        path = cmd_dir / name
        assert path.is_file(), f"{name} not written"
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o644, f"{name} mode {oct(mode)} != 0644"


@pytest.mark.skipif(not _INSTALL_SH.exists(), reason="install.sh missing")
def test_command_bodies_invoke_the_cli(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert _run_installer(home).returncode == 0

    cmd_dir = home / ".claude" / "commands" / "magi"
    activate = (cmd_dir / "pack-activate.md").read_text("utf-8")
    assert "magi-cp session pack activate" in activate
    deactivate = (cmd_dir / "pack-deactivate.md").read_text("utf-8")
    assert "magi-cp session pack deactivate" in deactivate
    status = (cmd_dir / "pack-status.md").read_text("utf-8")
    assert "magi-cp session pack status" in status


@pytest.mark.skipif(not _INSTALL_SH.exists(), reason="install.sh missing")
def test_install_is_idempotent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert _run_installer(home).returncode == 0
    # Second run must not fail (files simply overwritten).
    assert _run_installer(home).returncode == 0
    cmd_dir = home / ".claude" / "commands" / "magi"
    assert (cmd_dir / "pack.md").is_file()
