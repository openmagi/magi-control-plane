"""``magi-cp install --runtime gjc`` : gjc plugin-bundle installer (U6).

Design brief: 2026-07-08-magi-cp-gajae-code-runtime-adapter-design
Section 6.3 (installer, 5 numbered behaviours) + Section 9 (enforcement
integrity — launch-tier honesty).

Mirrors ``local/codex_install.py`` in shape:

  Plugin bundle:  ``~/.gjc/agent/gjc-plugins/magi-cp-gate/``
                  (overridable via ``MAGI_CP_GJC_PLUGIN_DIR``)

The bundle contains five files (Section 6.1):
  gajae-plugin.json                    — manifest with sha256 hashes
  hooks/magi-gate-tool-call.ts         — frozen tool_call gate shim
  hooks/magi-gate-session-start.ts     — session_start observer
  hooks/magi-gate-session-shutdown.ts  — session_shutdown observer
  magi-cp-tool-map.json               — normalization table sidecar

Locked decisions (D6):
  - When ``gjc`` is on PATH, RUN ``gjc plugin install <dir> --user``
    (upstream writes the registry + hash-records; we never hand-write
    ``registry.json`` directly).
  - When ``gjc`` is absent, PRINT the exact command instead.
  - ``--remove``: ``gjc plugin uninstall magi-cp-gate`` + delete the dir.
  - ``--force``: overwrite even if existing content diverges.
  - Without ``--force``, refuse to overwrite a diverging bundle.

Enforcement-integrity (Section 9.3): gjc with user-invoked CLI is the
WEAKEST of the four runtimes. There is NO prevention tier for the
user's own shell. The launcher checklist states this plainly.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..policy.gjc_bundle_emitter import compile_to_gjc_bundle


# ── bundle source ──────────────────────────────────────────────────────────────

def _bundle_files() -> dict[str, str]:
    """Return the canonical bundle file map (pure; no IR policies needed)."""
    return compile_to_gjc_bundle([]).files


# ── path resolution ────────────────────────────────────────────────────────────

def _home() -> Path:
    return Path(os.path.expanduser("~"))


def gjc_plugin_dir(home: Path | None = None) -> Path:
    """Managed bundle dir for the gjc plugin.

    ``MAGI_CP_GJC_PLUGIN_DIR`` overrides (tests / rootless installs).
    Else ``~/.gjc/agent/gjc-plugins/magi-cp-gate`` (Section 6.2).
    """
    override = os.environ.get("MAGI_CP_GJC_PLUGIN_DIR")
    if override:
        return Path(override)
    return (home or _home()) / ".gjc" / "agent" / "gjc-plugins" / "magi-cp-gate"


# ── subprocess helpers (patchable in tests) ────────────────────────────────────

def _gjc_binary_on_path() -> str | None:
    """Return the gjc binary path if found on PATH, else None."""
    return shutil.which("gjc")


def _gate_binary_on_path() -> str | None:
    """Return the magi-cp gate binary path if found on PATH, else None."""
    return shutil.which("magi-cp")


def _run_subprocess(args: list[str], **kwargs: Any) -> Any:
    """Thin wrapper around subprocess.run (patchable in tests)."""
    return subprocess.run(args, **kwargs)


# ── filesystem helpers (mirrors codex_install) ─────────────────────────────────

def _make_dir(path: Path, mode: int = 0o755) -> None:
    """mkdir -p with an exact mode (umask-independent)."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _write_file(
    path: Path,
    content: str,
    mode: int = 0o644,
    *,
    root_owned: bool = False,
) -> None:
    """Write ``content`` to ``path`` at ``mode``.

    Unlike the codex installer there is NO ``preserve`` option: the gjc
    installer always writes (the caller checks for divergence before calling
    this). ``root_owned``: best-effort ``chown root``, only when running as root.
    """
    path.write_text(content, encoding="utf-8")
    try:
        os.chmod(path, mode)
    except OSError:
        pass
    if root_owned and hasattr(os, "geteuid") and os.geteuid() == 0:
        try:
            os.chown(path, 0, 0)
        except OSError:
            pass


def _warn_if_not_root_owned(paths: list[str]) -> None:
    """Warn operator when managed bundle files are not root-owned.

    Mirrors ``codex_install._warn_if_not_root_owned``. The gjc install is
    weaker than Codex (Section 9.3), but the root-ownership trust boundary
    still applies under controlled launch (container / systemd that owns
    ``$HOME``). Emit a visible warning when not established.
    """
    if not hasattr(os, "geteuid"):
        return
    not_root = []
    for p in paths:
        try:
            if os.stat(p).st_uid != 0:
                not_root.append(p)
        except OSError:
            continue
    if not_root:
        print(
            "warning: gjc plugin bundle files are NOT root-owned "
            f"({len(not_root)} file(s)); the filesystem trust boundary is "
            "not established (design doc Section 6.3 / Section 9.3). "
            "Re-run `magi-cp install --runtime gjc` under sudo to establish it.",
            file=sys.stderr,
        )


# ── divergence check ───────────────────────────────────────────────────────────

def _bundle_diverges(plugin_dir: Path, expected: dict[str, str]) -> list[str]:
    """Return list of relative file paths that differ from expected content.

    An absent file is not divergence — it will simply be written. Only
    FILES THAT EXIST AND HAVE DIFFERENT CONTENT are divergence failures.
    """
    drifted: list[str] = []
    for rel_path, content in expected.items():
        p = plugin_dir / rel_path
        if p.exists() and p.read_text("utf-8") != content:
            drifted.append(rel_path)
    return drifted


# ── install step ───────────────────────────────────────────────────────────────

def install_gjc_bundle(
    plugin_dir: Path | None = None,
    *,
    force: bool = False,
) -> tuple[int, list[str]]:
    """Materialize the bundle dir from ``emit_managed_config`` output.

    Returns ``(exit_code, written_paths)``. Exit code is non-zero when a
    diverging bundle is detected without ``--force``.
    """
    pdir = plugin_dir or gjc_plugin_dir()
    expected = _bundle_files()

    # Check for divergence before writing anything.
    drifted = _bundle_diverges(pdir, expected)
    if drifted and not force:
        print(
            f"error: existing gjc bundle has diverging content in "
            f"{len(drifted)} file(s): {', '.join(drifted)}. "
            "Re-run with --force to overwrite.",
            file=sys.stderr,
        )
        return 1, []

    # Materialise dirs and files.
    _make_dir(pdir)
    hooks_dir = pdir / "hooks"
    _make_dir(hooks_dir)

    written: list[str] = []
    for rel_path, content in expected.items():
        p = pdir / rel_path
        _write_file(p, content, root_owned=True)
        written.append(str(p))

    _warn_if_not_root_owned(written)
    return 0, written


# ── register step (D6) ────────────────────────────────────────────────────────

def register_gjc_plugin(plugin_dir: Path) -> None:
    """Run ``gjc plugin install <dir> --user`` or print the command.

    D6: when the ``gjc`` binary is on PATH, RUN it. When absent, PRINT
    the exact command. NEVER hand-write ``registry.json``.
    """
    gjc = _gjc_binary_on_path()
    cmd = ["gjc", "plugin", "install", str(plugin_dir), "--user"]
    if gjc:
        _run_subprocess(cmd, check=False, capture_output=True, text=True)
    else:
        print(
            "\n[gjc] Binary not found on PATH. "
            "After installing gjc, run:\n"
            f"  {' '.join(cmd)}\n"
        )


# ── remove step ───────────────────────────────────────────────────────────────

def force_remove_gjc(plugin_dir: Path | None = None) -> list[str]:
    """Remove the gjc plugin bundle (Section 6.3 rollback).

    1. Run ``gjc plugin uninstall magi-cp-gate`` (when gjc is on PATH).
    2. Delete exactly the bundle dir.
    Idempotent: absent dir is a no-op.
    """
    pdir = plugin_dir or gjc_plugin_dir()
    removed: list[str] = []

    gjc = _gjc_binary_on_path()
    if gjc and pdir.exists():
        _run_subprocess(
            ["gjc", "plugin", "uninstall", "magi-cp-gate"],
            check=False, capture_output=True, text=True,
        )

    if pdir.exists():
        shutil.rmtree(pdir)
        removed.append(str(pdir))

    return removed


# ── launcher checklist ────────────────────────────────────────────────────────

def _print_launcher_checklist(plugin_dir: Path) -> None:
    """Print the operator launch guidance (Section 6.3 item 3, Section 9.3).

    States plainly: for user-invoked ``gjc`` from the user's own shell
    there is NO prevention tier, only detection (Section 9.3 honesty).
    """
    print(
        "\n"
        "=== magi-cp gjc launcher checklist ===\n"
        "\n"
        "For CONTROLLED launch (container entrypoint / systemd unit):\n"
        "  • Set or pin GJC_CONFIG_DIR to a directory your process owns.\n"
        "  • Mount the bundle dir read-only in the image:\n"
        f"      {plugin_dir}\n"
        "  • Pre-bake the registry (run `gjc plugin install --user` at image build).\n"
        "  • This gives you the Codex/Hermes middle trust tier.\n"
        "\n"
        "For USER-INVOKED gjc from the user's own shell:\n"
        "  ⚠ There is NO prevention tier — only detection.\n"
        "    The user can run `gjc plugin disable magi-cp-gate`, edit\n"
        "    registry.json, or set GJC_CONFIG_DIR=/tmp/x gjc to bypass\n"
        "    enforcement entirely. These are first-class product features.\n"
        "    magi-cp doctor + session heartbeat absence are the only\n"
        "    detection signal (Section 9.2, GB1-GB6).\n"
        "\n"
        "=== end checklist ===\n"
    )


# ── doctor checks ─────────────────────────────────────────────────────────────

def doctor_gjc(plugin_dir: Path | None = None) -> list[dict]:
    """Run the four §6.3 doctor checks and return a list of result dicts.

    Each result dict has:
      ``check``   — one of "bundle_bytes", "plugin_list", "gate_binary",
                    "gate_dry_run"
      ``ok``      — True / False / None (None = skipped)
      ``detail``  — human-readable string (on failure or skip)

    The checks are independent; a failure in one does not skip others.
    """
    pdir = plugin_dir or gjc_plugin_dir()
    results: list[dict] = []

    # (a) Bundle dir present + bytes match vendored goldens.
    expected = _bundle_files()
    if not pdir.is_dir():
        results.append({
            "check": "bundle_bytes",
            "ok": False,
            "detail": f"bundle dir absent: {pdir}",
        })
    else:
        drifted = _bundle_diverges(pdir, expected)
        # Also check for missing files.
        missing = [k for k in expected if not (pdir / k).exists()]
        all_bad = list(dict.fromkeys(missing + drifted))  # dedup, preserve order
        if all_bad:
            results.append({
                "check": "bundle_bytes",
                "ok": False,
                "detail": f"files drifted or missing: {', '.join(all_bad)}",
            })
        else:
            results.append({"check": "bundle_bytes", "ok": True, "detail": ""})

    # (b) ``gjc plugin list`` shows ``magi-cp-gate`` enabled.
    gjc = _gjc_binary_on_path()
    if gjc is None:
        results.append({
            "check": "plugin_list",
            "ok": None,
            "skipped": True,
            "detail": "gjc binary not found on PATH",
        })
    else:
        try:
            proc = _run_subprocess(
                ["gjc", "plugin", "list"],
                capture_output=True, text=True, check=False,
            )
            output = proc.stdout or ""
            if "magi-cp-gate" in output and "enabled" in output:
                results.append({"check": "plugin_list", "ok": True, "detail": ""})
            else:
                results.append({
                    "check": "plugin_list",
                    "ok": False,
                    "detail": (
                        "magi-cp-gate not found or not enabled in "
                        "`gjc plugin list` output"
                    ),
                })
        except Exception as exc:  # noqa: BLE001
            results.append({
                "check": "plugin_list",
                "ok": False,
                "detail": f"gjc plugin list failed: {exc}",
            })

    # (c) Gate binary present + executable + version.
    gate = _gate_binary_on_path()
    if gate is None:
        results.append({
            "check": "gate_binary",
            "ok": False,
            "detail": "magi-cp gate binary not found on PATH",
        })
    else:
        gate_path = Path(gate)
        if gate_path.exists() and os.access(gate_path, os.X_OK):
            results.append({"check": "gate_binary", "ok": True, "detail": gate})
        else:
            results.append({
                "check": "gate_binary",
                "ok": False,
                "detail": f"magi-cp at {gate} is not executable",
            })

    # (d) Dry-run ``tool_call`` fixture through ``magi-cp gate --runtime gjc``.
    gate = _gate_binary_on_path()
    if gate is None:
        results.append({
            "check": "gate_dry_run",
            "ok": None,
            "skipped": True,
            "detail": "magi-cp gate binary not found on PATH",
        })
    else:
        _fixture = (
            '{"gjc_event":"tool_call","tool_name":"read",'
            '"session_id":"dr-session","tool_input":{"path":"/tmp/x"}}'
        )
        try:
            proc = _run_subprocess(
                ["magi-cp", "gate", "--runtime", "gjc"],
                input=_fixture, capture_output=True, text=True, check=False,
            )
            # An allow verdict produces empty stdout; a deny is non-empty JSON.
            # Either is a valid gate response; an error exit code is a failure.
            if proc.returncode == 0:
                results.append({
                    "check": "gate_dry_run",
                    "ok": True,
                    "detail": f"stdout={proc.stdout!r}",
                })
            else:
                results.append({
                    "check": "gate_dry_run",
                    "ok": False,
                    "detail": f"gate exited {proc.returncode}: {proc.stderr}",
                })
        except Exception as exc:  # noqa: BLE001
            results.append({
                "check": "gate_dry_run",
                "ok": False,
                "detail": f"gate dry-run failed: {exc}",
            })

    return results


def _print_doctor_results(results: list[dict]) -> int:
    """Print doctor results to stdout and return exit code (0=all ok/skip, 1=any fail)."""
    any_fail = False
    for r in results:
        ok = r.get("ok")
        check = r["check"]
        detail = r.get("detail", "")
        if ok is True:
            print(f"  [PASS] {check}: {detail}")
        elif ok is False:
            print(f"  [FAIL] {check}: {detail}")
            any_fail = True
        else:
            print(f"  [SKIP] {check}: {detail}")
    return 1 if any_fail else 0


# ── CLI ────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="magi-cp install / magi-cp doctor (gjc)")
    p.add_argument(
        "--runtime", choices=("gjc",), default="gjc",
        help="runtime surface (gjc)",
    )
    p.add_argument(
        "--remove", action="store_true",
        help="uninstall the gjc plugin bundle",
    )
    p.add_argument(
        "--force", action="store_true",
        help="overwrite diverging bundle content",
    )
    return p


def cli(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    if args.remove:
        removed = force_remove_gjc()
        if removed:
            for path in removed:
                print(f"removed {path}")
        else:
            print("nothing to remove (gjc plugin bundle already absent)")
        return 0

    # Install path.
    pdir = gjc_plugin_dir()
    rc, written = install_gjc_bundle(plugin_dir=pdir, force=args.force)
    if rc != 0:
        return rc

    register_gjc_plugin(pdir)
    _print_launcher_checklist(pdir)

    if written:
        for path in written:
            print(f"wrote {path}")
    else:
        print("nothing to write (bundle already up-to-date)")
    return 0


def doctor_cli(argv: list[str] | None = None) -> int:
    """``magi-cp doctor`` gjc checks entry point."""
    pdir = gjc_plugin_dir()
    print("gjc doctor checks:")
    results = doctor_gjc(plugin_dir=pdir)
    return _print_doctor_results(results)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(cli())
