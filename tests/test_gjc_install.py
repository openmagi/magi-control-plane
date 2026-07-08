"""P4 (U6): ``magi-cp install --runtime gjc`` drops the plugin bundle.

Covers §6.3 requirements:
  1. Install writes the exact bundle bytes (0755 dirs / 0644 files).
  2. Refuses to overwrite a diverging existing bundle without ``--force``.
  3. ``--remove`` deletes exactly what was written (idempotent).
  4. Prints the ``gjc plugin install`` command when the binary is absent.
  5. Doctor checks: bundle bytes match vendored goldens; gate binary present
     and executable; ``gjc plugin list`` shows ``magi-cp-gate`` enabled;
     dry-run gate returns expected verdict.
  6. Launcher checklist states plainly: NO prevention tier for user-invoked
     ``gjc`` from the user's own shell — detection only.
  7. Top-level ``magi-cp doctor`` dispatch reaches the gjc checks.

The bundle dir is redirected via ``MAGI_CP_GJC_PLUGIN_DIR`` so the test
never writes to ``~/.gjc``.
"""
import os
import stat
from pathlib import Path
from unittest import mock

from magi_cp.local import gjc_install
from magi_cp.policy.gjc_bundle_emitter import compile_to_gjc_bundle


# ── helpers ───────────────────────────────────────────────────────────────────

def _wire(monkeypatch, tmp_path) -> tuple[Path, Path]:
    """Point HOME + the gjc plugin dir at scratch locations."""
    home = tmp_path / "home"
    home.mkdir()
    plugin_dir = tmp_path / "magi-cp-gate"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("MAGI_CP_GJC_PLUGIN_DIR", str(plugin_dir))
    return home, plugin_dir


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _expected_bundle() -> dict[str, str]:
    """Reference bundle from the emitter (no policies needed — the bundle is static)."""
    return compile_to_gjc_bundle([]).files


# ── 1. Install writes exact bundle bytes with correct permissions ──────────────

def test_install_gjc_writes_bundle_files(monkeypatch, tmp_path):
    _home, plugin_dir = _wire(monkeypatch, tmp_path)

    rc = gjc_install.cli(["--runtime", "gjc"])
    assert rc == 0

    expected = _expected_bundle()
    for rel_path, content in expected.items():
        p = plugin_dir / rel_path
        assert p.is_file(), f"bundle file {rel_path} missing"
        assert p.read_text("utf-8") == content, f"{rel_path} content mismatch"
        assert _mode(p) == 0o644, f"{rel_path} mode {oct(_mode(p))}"


def test_install_gjc_creates_dirs_with_correct_mode(monkeypatch, tmp_path):
    _home, plugin_dir = _wire(monkeypatch, tmp_path)
    gjc_install.cli(["--runtime", "gjc"])

    # Top-level bundle dir
    assert plugin_dir.is_dir()
    assert _mode(plugin_dir) == 0o755

    # hooks/ subdirectory
    hooks_dir = plugin_dir / "hooks"
    assert hooks_dir.is_dir()
    assert _mode(hooks_dir) == 0o755


def test_install_gjc_creates_all_five_bundle_keys(monkeypatch, tmp_path):
    _home, plugin_dir = _wire(monkeypatch, tmp_path)
    gjc_install.cli(["--runtime", "gjc"])

    expected_keys = {
        "gajae-plugin.json",
        "hooks/magi-gate-tool-call.ts",
        "hooks/magi-gate-session-start.ts",
        "hooks/magi-gate-session-shutdown.ts",
        "magi-cp-tool-map.json",
    }
    for rel_path in expected_keys:
        assert (plugin_dir / rel_path).is_file(), f"missing {rel_path}"


# ── 2. Refuses to overwrite diverging bundle without --force ──────────────────

def test_install_gjc_refuses_overwrite_without_force(monkeypatch, tmp_path, capsys):
    _home, plugin_dir = _wire(monkeypatch, tmp_path)
    gjc_install.cli(["--runtime", "gjc"])

    # Mutate one bundle file to simulate drift
    manifest = plugin_dir / "gajae-plugin.json"
    original = manifest.read_text("utf-8")
    manifest.write_text(original + "\n# operator drift\n", "utf-8")

    # A second install without --force must refuse and exit non-zero.
    rc = gjc_install.cli(["--runtime", "gjc"])
    assert rc != 0

    err = capsys.readouterr().err
    assert "force" in err.lower() or "diverge" in err.lower() or "mismatch" in err.lower()

    # The drifted file must remain unchanged.
    assert "operator drift" in manifest.read_text("utf-8")


def test_install_gjc_force_overwrites_diverging_bundle(monkeypatch, tmp_path):
    _home, plugin_dir = _wire(monkeypatch, tmp_path)
    gjc_install.cli(["--runtime", "gjc"])

    manifest = plugin_dir / "gajae-plugin.json"
    manifest.write_text(manifest.read_text("utf-8") + "\n# drift\n", "utf-8")

    rc = gjc_install.cli(["--runtime", "gjc", "--force"])
    assert rc == 0

    expected = _expected_bundle()
    assert manifest.read_text("utf-8") == expected["gajae-plugin.json"]


def test_install_gjc_idempotent_when_bytes_match(monkeypatch, tmp_path):
    """Re-run without --force must succeed when no drift exists."""
    _home, plugin_dir = _wire(monkeypatch, tmp_path)
    assert gjc_install.cli(["--runtime", "gjc"]) == 0
    assert gjc_install.cli(["--runtime", "gjc"]) == 0


# ── 3. --remove deletes exactly the bundle dir ────────────────────────────────

def test_remove_gjc_deletes_bundle_dir(monkeypatch, tmp_path):
    _home, plugin_dir = _wire(monkeypatch, tmp_path)
    gjc_install.cli(["--runtime", "gjc"])
    assert plugin_dir.is_dir()

    rc = gjc_install.cli(["--remove"])
    assert rc == 0
    assert not plugin_dir.exists()


def test_remove_gjc_is_idempotent(monkeypatch, tmp_path):
    _home, plugin_dir = _wire(monkeypatch, tmp_path)
    gjc_install.cli(["--runtime", "gjc"])
    assert gjc_install.cli(["--remove"]) == 0
    # Second removal must also succeed cleanly.
    assert gjc_install.cli(["--remove"]) == 0


def test_remove_gjc_leaves_other_home_dirs(monkeypatch, tmp_path):
    """--remove must only delete the managed bundle dir, nothing else."""
    home, plugin_dir = _wire(monkeypatch, tmp_path)
    other = home / ".gjc" / "other-plugin"
    other.mkdir(parents=True)
    gjc_install.cli(["--runtime", "gjc"])
    gjc_install.cli(["--remove"])

    assert not plugin_dir.exists()
    assert other.is_dir()


# ── 4. Prints gjc plugin install command when binary is absent ────────────────

def test_install_prints_plugin_install_command_when_gjc_absent(
    monkeypatch, tmp_path, capsys,
):
    _home, plugin_dir = _wire(monkeypatch, tmp_path)

    # Patch the PATH-lookup so ``gjc`` binary appears absent.
    monkeypatch.setattr(gjc_install, "_gjc_binary_on_path", lambda: None)

    rc = gjc_install.cli(["--runtime", "gjc"])
    assert rc == 0

    out = capsys.readouterr().out
    # Must print the exact gjc plugin install command with --user flag.
    assert "gjc plugin install" in out
    assert "--user" in out
    assert str(plugin_dir) in out


def test_install_runs_gjc_plugin_install_when_binary_present(
    monkeypatch, tmp_path,
):
    """When gjc is on PATH the installer must RUN the command (D6)."""
    _home, plugin_dir = _wire(monkeypatch, tmp_path)

    called_with: list[list[str]] = []

    def _fake_run(args, **kwargs):
        called_with.append(args)
        return mock.Mock(returncode=0, stderr="")

    monkeypatch.setattr(gjc_install, "_gjc_binary_on_path", lambda: "gjc")
    monkeypatch.setattr(gjc_install, "_run_subprocess", _fake_run)

    rc = gjc_install.cli(["--runtime", "gjc"])
    assert rc == 0

    assert len(called_with) == 1
    cmd = called_with[0]
    assert cmd[0] == "gjc"
    assert "plugin" in cmd
    assert "install" in cmd
    assert "--user" in cmd
    assert str(plugin_dir) in " ".join(cmd)


# ── 5. Doctor checks ──────────────────────────────────────────────────────────

def test_doctor_gjc_bundle_bytes_pass_when_matching(monkeypatch, tmp_path):
    _home, plugin_dir = _wire(monkeypatch, tmp_path)
    gjc_install.cli(["--runtime", "gjc"])

    results = gjc_install.doctor_gjc(plugin_dir=plugin_dir)
    bundle_check = next(r for r in results if r["check"] == "bundle_bytes")
    assert bundle_check["ok"] is True


def test_doctor_gjc_bundle_bytes_fail_when_drifted(monkeypatch, tmp_path):
    _home, plugin_dir = _wire(monkeypatch, tmp_path)
    gjc_install.cli(["--runtime", "gjc"])

    # Drift one file
    (plugin_dir / "gajae-plugin.json").write_text("CORRUPTED", "utf-8")

    results = gjc_install.doctor_gjc(plugin_dir=plugin_dir)
    bundle_check = next(r for r in results if r["check"] == "bundle_bytes")
    assert bundle_check["ok"] is False
    assert "gajae-plugin.json" in bundle_check.get("detail", "")


def test_doctor_gjc_bundle_absent_is_fail(monkeypatch, tmp_path):
    _home, plugin_dir = _wire(monkeypatch, tmp_path)
    # Do NOT install — plugin_dir is absent.

    results = gjc_install.doctor_gjc(plugin_dir=plugin_dir)
    bundle_check = next(r for r in results if r["check"] == "bundle_bytes")
    assert bundle_check["ok"] is False


def test_doctor_gjc_gate_binary_present(monkeypatch, tmp_path):
    _home, plugin_dir = _wire(monkeypatch, tmp_path)
    gjc_install.cli(["--runtime", "gjc"])

    # Fake a gate binary on PATH.
    fake_bin = tmp_path / "bin" / "magi-cp"
    fake_bin.parent.mkdir()
    fake_bin.write_text("#!/bin/sh\necho 'magi-cp 1.0'\n", "utf-8")
    fake_bin.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin.parent) + ":" + os.environ.get("PATH", ""))

    results = gjc_install.doctor_gjc(plugin_dir=plugin_dir)
    gate_check = next(r for r in results if r["check"] == "gate_binary")
    assert gate_check["ok"] is True


def test_doctor_gjc_gate_binary_absent_is_fail(monkeypatch, tmp_path):
    _home, plugin_dir = _wire(monkeypatch, tmp_path)
    gjc_install.cli(["--runtime", "gjc"])

    monkeypatch.setattr(gjc_install, "_gate_binary_on_path", lambda: None)

    results = gjc_install.doctor_gjc(plugin_dir=plugin_dir)
    gate_check = next(r for r in results if r["check"] == "gate_binary")
    assert gate_check["ok"] is False


def test_doctor_gjc_plugin_list_enabled(monkeypatch, tmp_path):
    _home, plugin_dir = _wire(monkeypatch, tmp_path)
    gjc_install.cli(["--runtime", "gjc"])

    # Fake gjc plugin list returning magi-cp-gate enabled.
    def _fake_run(args, **kwargs):
        return mock.Mock(
            returncode=0,
            stdout="magi-cp-gate  enabled\n",
            stderr="",
        )

    monkeypatch.setattr(gjc_install, "_gjc_binary_on_path", lambda: "gjc")
    monkeypatch.setattr(gjc_install, "_run_subprocess", _fake_run)

    results = gjc_install.doctor_gjc(plugin_dir=plugin_dir)
    list_check = next(r for r in results if r["check"] == "plugin_list")
    assert list_check["ok"] is True


def test_doctor_gjc_plugin_list_not_found_is_fail(monkeypatch, tmp_path):
    _home, plugin_dir = _wire(monkeypatch, tmp_path)
    gjc_install.cli(["--runtime", "gjc"])

    def _fake_run(args, **kwargs):
        return mock.Mock(returncode=0, stdout="other-plugin  enabled\n", stderr="")

    monkeypatch.setattr(gjc_install, "_gjc_binary_on_path", lambda: "gjc")
    monkeypatch.setattr(gjc_install, "_run_subprocess", _fake_run)

    results = gjc_install.doctor_gjc(plugin_dir=plugin_dir)
    list_check = next(r for r in results if r["check"] == "plugin_list")
    assert list_check["ok"] is False


def test_doctor_gjc_plugin_list_skipped_when_gjc_absent(monkeypatch, tmp_path):
    _home, plugin_dir = _wire(monkeypatch, tmp_path)
    gjc_install.cli(["--runtime", "gjc"])

    monkeypatch.setattr(gjc_install, "_gjc_binary_on_path", lambda: None)

    results = gjc_install.doctor_gjc(plugin_dir=plugin_dir)
    list_check = next(r for r in results if r["check"] == "plugin_list")
    # When gjc is not present the check is skipped (ok=None or absent).
    assert list_check.get("ok") is None or list_check.get("skipped") is True


def test_doctor_gjc_dry_run_gate_verdict(monkeypatch, tmp_path):
    _home, plugin_dir = _wire(monkeypatch, tmp_path)
    gjc_install.cli(["--runtime", "gjc"])

    # Fake gate binary that outputs the expected allow verdict (empty bytes = allow).
    def _fake_run(args, **kwargs):
        if "gate" in args:
            return mock.Mock(returncode=0, stdout="", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(gjc_install, "_gate_binary_on_path", lambda: "magi-cp")
    monkeypatch.setattr(gjc_install, "_run_subprocess", _fake_run)

    results = gjc_install.doctor_gjc(plugin_dir=plugin_dir)
    gate_check = next(r for r in results if r["check"] == "gate_dry_run")
    assert gate_check["ok"] is True


def test_doctor_gjc_dry_run_skipped_when_gate_absent(monkeypatch, tmp_path):
    _home, plugin_dir = _wire(monkeypatch, tmp_path)
    gjc_install.cli(["--runtime", "gjc"])

    monkeypatch.setattr(gjc_install, "_gate_binary_on_path", lambda: None)

    results = gjc_install.doctor_gjc(plugin_dir=plugin_dir)
    gate_check = next(r for r in results if r["check"] == "gate_dry_run")
    assert gate_check.get("ok") is None or gate_check.get("skipped") is True


# ── 6. Launcher checklist honesty (§9 — no prevention tier for user-invoked gjc) ──

def test_install_gjc_launcher_checklist_states_no_prevention_tier(
    monkeypatch, tmp_path, capsys,
):
    _home, plugin_dir = _wire(monkeypatch, tmp_path)
    gjc_install.cli(["--runtime", "gjc"])

    out = capsys.readouterr().out
    # The checklist must state plainly that user-invoked gjc has no prevention tier.
    # Accept any phrasing that conveys "no prevention" or "detection only".
    text = out.lower()
    assert (
        "no prevention" in text
        or "detection only" in text
        or "detect" in text
    ), f"launcher checklist missing honesty statement; got:\n{out}"


def test_install_gjc_launcher_checklist_mentions_gjc_config_dir(
    monkeypatch, tmp_path, capsys,
):
    _home, plugin_dir = _wire(monkeypatch, tmp_path)
    gjc_install.cli(["--runtime", "gjc"])

    out = capsys.readouterr().out
    assert "GJC_CONFIG_DIR" in out


# ── 7. Top-level dispatch ─────────────────────────────────────────────────────

def test_top_level_magi_cp_install_gjc_dispatch(monkeypatch, tmp_path):
    from magi_cp.cli.__main__ import main as cli_main

    _home, plugin_dir = _wire(monkeypatch, tmp_path)
    monkeypatch.setattr(gjc_install, "_gjc_binary_on_path", lambda: None)

    rc = cli_main(["install", "--runtime", "gjc"])
    assert rc == 0

    expected = _expected_bundle()
    for rel_path in expected:
        assert (plugin_dir / rel_path).is_file(), f"bundle file {rel_path} missing"


def test_top_level_magi_cp_doctor_dispatch(monkeypatch, tmp_path, capsys):
    from magi_cp.cli.__main__ import main as cli_main

    _home, plugin_dir = _wire(monkeypatch, tmp_path)
    gjc_install.cli(["--runtime", "gjc"])

    monkeypatch.setattr(gjc_install, "_gjc_binary_on_path", lambda: None)
    monkeypatch.setattr(gjc_install, "_gate_binary_on_path", lambda: None)

    rc = cli_main(["doctor"])
    # Doctor exits 0 (checks pass/skip) or 1 (check failures).
    # Either way it must not crash and must produce output.
    assert rc in (0, 1)
    out = capsys.readouterr().out + capsys.readouterr().err
    assert "bundle_bytes" in out or "gjc" in out.lower()


# ── root-own warning (mirrors codex_install) ──────────────────────────────────

def test_managed_files_warn_when_not_root_owned(monkeypatch, tmp_path, capsys):
    import os as _os
    import pytest

    if not hasattr(_os, "geteuid") or _os.geteuid() == 0:
        pytest.skip("requires a non-root euid to observe the weak boundary")

    _home, plugin_dir = _wire(monkeypatch, tmp_path)
    gjc_install.cli(["--runtime", "gjc"])

    err = capsys.readouterr().err
    assert "NOT root-owned" in err or "trust boundary" in err or "root" in err.lower()
