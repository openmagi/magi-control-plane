"""``magi-cp install`` : Codex (and Claude Code) adapter installer (P3).

Design brief: docs/plans/2026-06-30-codex-runtime-adapter-design.md
Section 5 (slash-command shipping) + Section 6 (managed enforcement) +
Section 13 (rollback runbook / ``--force-remove-codex``).

This is the runtime-side installer for the Codex adapter. It drops the
same operator surface the Claude Code installer ships (``install.sh``),
but shaped for Codex CLI 0.142.x:

  Forward-compat, primary:  ``~/.codex/skills/magi/pack-*.md``
  Works-today fallback:     ``~/.codex/prompts/magi:pack:*.md``
  Managed enforcement:      ``<etc>/requirements.toml``
                            ``<etc>/managed_config.toml``
                            ``<etc>/magi-cp/context-templates/<sha>.txt``

Every skill / prompt body shells out to the EXACT same CLI the Claude
Code slash commands use, ``magi-cp session pack <sub> "$1"`` (the
pack-centric P3 CLI, reused verbatim, no new subcommand and no branch on
runtime — the gate dispatcher handles the runtime split).

Runtime selection
=================
  magi-cp install                     # default: Claude Code side files
  magi-cp install --runtime cc        # explicit CC side
  magi-cp install --runtime codex     # Codex side files instead
  magi-cp install --runtime both      # both surfaces
  magi-cp install --force-remove-codex  # customer rollback (Section 13)

Managed-config location
=======================
On Linux/macOS the managed enforcement files live under ``/etc/codex``.
On Windows they live under ``%ProgramData%\\OpenAI\\Codex``. Both are
overridable with ``MAGI_CP_CODEX_ETC_DIR`` so a test (or a rootless
install) can point them at a scratch directory. The two ``.toml`` files
are dropped root-owned (``chown root`` only when the installer itself
runs as root; the trust boundary is filesystem ownership, Section 6.3).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ..policy.codex_toml_emitter import _toml_str, compile_to_codex_requirements
from ..policy.ir import policy_from_dict

# ── default cloud url ──────────────────────────────────────────────────
# Matches ``magi_cp.local.cli._DEFAULT_CLOUD_URL`` so the managed env
# passthrough points the gate + Codex at the same self-host cloud the
# session CLI talks to by default.
_DEFAULT_CLOUD_URL = "http://127.0.0.1:8787"


# ── skill / prompt bodies ──────────────────────────────────────────────
# One spec per pack subcommand. ``arg`` is the Codex positional-arg
# placeholder appended to the invocation ("" for the argument-less
# status command). ``hint`` is the ``argument-hint:`` frontmatter value
# (Codex custom-prompts docs, research report Section 4).
class _PackCmd:
    __slots__ = ("sub", "skill_name", "prompt_name", "description", "hint", "arg")

    def __init__(self, sub, skill_name, prompt_name, description, hint, arg):
        self.sub = sub
        self.skill_name = skill_name
        self.prompt_name = prompt_name
        self.description = description
        self.hint = hint
        self.arg = arg


_PACK_CMDS: tuple[_PackCmd, ...] = (
    _PackCmd(
        sub="activate",
        skill_name="pack-activate.md",
        prompt_name="magi:pack:activate.md",
        description="Activate a Magi policy pack for this Codex session",
        hint="<pack_id>",
        arg=' "$1"',
    ),
    _PackCmd(
        sub="deactivate",
        skill_name="pack-deactivate.md",
        prompt_name="magi:pack:deactivate.md",
        description="Deactivate a Magi policy pack for this session",
        hint="<pack_id>",
        arg=' "$1"',
    ),
    _PackCmd(
        sub="status",
        skill_name="pack-status.md",
        prompt_name="magi:pack:status.md",
        description="Show which Magi policy packs are active this session",
        hint="(no arguments)",
        arg="",
    ),
    _PackCmd(
        sub="sticky",
        skill_name="pack-sticky.md",
        prompt_name="magi:pack:sticky.md",
        description="Make a Magi policy pack auto-activate for this project",
        hint="<pack_id>",
        arg=' "$1"',
    ),
)


def _pack_body(cmd: _PackCmd) -> str:
    """Render the markdown body shared by the skill and the prompt file.

    YAML frontmatter (``description`` + ``argument-hint``) followed by a
    one-line instruction and the CLI invocation. Identical shape to the
    Claude Code slash commands the ``install.sh`` installer drops, so the
    two runtimes route to byte-equal CLI commands.
    """
    invocation = f"magi-cp session pack {cmd.sub}{cmd.arg}"
    return (
        "---\n"
        f"description: {cmd.description}\n"
        f"argument-hint: {cmd.hint}\n"
        "---\n"
        f"{cmd.description}, then report the result verbatim. The pack's "
        "policies fire on matching Codex tool hooks until the session ends.\n"
        "\n"
        f"{invocation}\n"
    )


# ── Claude Code slash-command bodies (mirrors install.sh) ───────────────
# Kept here so ``--runtime cc`` / ``--runtime both`` can drop the CC
# surface without shelling back into the bash installer. The bodies are
# byte-equal to ``web/public/install.sh``'s ``install_slash_commands``.
_CC_COMMANDS: dict[str, str] = {
    "pack.md": (
        "---\n"
        "description: Magi policy packs (activate/deactivate/status for this session)\n"
        "---\n"
        "Magi control-plane session packs. Subcommands:\n"
        "\n"
        "- `/magi:pack-activate <pack_id>`: turn a pack on for this session\n"
        "- `/magi:pack-deactivate <pack_id>`: turn it off\n"
        "- `/magi:pack-status`: show active packs and how many policies will fire\n"
        "\n"
        "Packs group policies by intent (research-mode, coding-safety, and so on).\n"
        "The floor pack is always on. Activation lasts until the session ends or\n"
        "you run `/magi:pack-deactivate`.\n"
    ),
    "pack-activate.md": (
        "---\n"
        "description: Activate a Magi policy pack for this Claude Code session\n"
        "allowed-tools: Bash(magi-cp session pack activate:*)\n"
        "---\n"
        "Activate the named Magi policy pack for the current session, then report\n"
        "the result verbatim. The pack's policies fire on matching tool hooks\n"
        "until the session ends or `/magi:pack-deactivate` is run.\n"
        "\n"
        '!`magi-cp session pack activate "$ARGUMENTS"`\n'
    ),
    "pack-deactivate.md": (
        "---\n"
        "description: Deactivate a Magi policy pack for this session\n"
        "allowed-tools: Bash(magi-cp session pack deactivate:*)\n"
        "---\n"
        "Deactivate the named Magi policy pack for the current session, then\n"
        "report the result verbatim. The always-on floor pack always remains.\n"
        "\n"
        '!`magi-cp session pack deactivate "$ARGUMENTS"`\n'
    ),
    "pack-status.md": (
        "---\n"
        "description: Show which Magi policy packs are active this session\n"
        "allowed-tools: Bash(magi-cp session pack status:*)\n"
        "---\n"
        "Show the Magi policy packs active for the current session, including the\n"
        "always-on floor pack and a count of how many policies will fire, then\n"
        "report it verbatim.\n"
        "\n"
        "!`magi-cp session pack status`\n"
    ),
}


# ── filesystem helpers ─────────────────────────────────────────────────
def _make_dir(path: Path, mode: int = 0o755) -> None:
    """mkdir -p with an exact mode (umask-independent)."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _write_file(
    path: Path, content: str, mode: int = 0o644, *,
    preserve: bool = True, root_owned: bool = False,
) -> str:
    """Write ``content`` to ``path`` at ``mode``.

    ``preserve``: when the file already exists, leave it untouched (same
    policy as the docker-compose.yml / slash-command preservation in
    ``install.sh`` — never clobber operator edits on re-run). Returns
    ``"preserved"`` in that case, otherwise ``"wrote"``.

    ``root_owned``: attempt ``chown root`` (uid/gid 0) so the managed
    files carry the filesystem trust boundary. Best-effort: only fires
    when the installer itself runs as root, silently skipped otherwise.
    """
    if preserve and path.exists():
        return "preserved"
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
    return "wrote"


# ── path resolution ────────────────────────────────────────────────────
def _home() -> Path:
    return Path(os.path.expanduser("~"))


def codex_skills_dir(home: Path | None = None) -> Path:
    return (home or _home()) / ".codex" / "skills" / "magi"


def codex_prompts_dir(home: Path | None = None) -> Path:
    return (home or _home()) / ".codex" / "prompts"


def cc_commands_dir(home: Path | None = None) -> Path:
    return (home or _home()) / ".claude" / "commands" / "magi"


def _managed_dir_writable(etc: Path) -> tuple[bool, str]:
    """Preflight the managed-enforcement dir before writing anything.

    Returns ``(True, "")`` when the dir can be created and written, else
    ``(False, reason)``. Used to hard-fail a Codex install up front instead
    of dropping the user-facing pack surface first (user-writable
    ``~/.codex``) and only then hitting ``PermissionError`` on
    ``/etc/codex`` (reader is not env-overridable). Without this preflight a
    non-root ``curl | bash`` install would leave packs looking installed
    while the compiled policy layer (``requirements.toml``) is absent, i.e.
    a fail-open shape for a security-enforcement surface.
    """
    try:
        etc.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"cannot create managed dir {etc}: {exc}"
    probe = etc / ".magi-cp-write-probe"
    try:
        probe.write_text("", encoding="utf-8")
    except OSError as exc:
        return False, f"managed dir {etc} is not writable: {exc}"
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    return True, ""


def codex_etc_dir() -> Path:
    """Managed-config root for Codex.

    ``MAGI_CP_CODEX_ETC_DIR`` overrides (tests / rootless installs). Else
    ``%ProgramData%\\OpenAI\\Codex`` on Windows, ``/etc/codex`` elsewhere
    (design doc Section 6.1).
    """
    override = os.environ.get("MAGI_CP_CODEX_ETC_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        program_data = os.environ.get("ProgramData", r"C:\ProgramData")
        return Path(program_data) / "OpenAI" / "Codex"
    return Path("/etc/codex")


# ── install steps ──────────────────────────────────────────────────────
def install_codex_skills(home: Path | None = None) -> list[str]:
    """Drop the four ``~/.codex/skills/magi/pack-*.md`` skills."""
    d = codex_skills_dir(home)
    _make_dir(d)
    written: list[str] = []
    for cmd in _PACK_CMDS:
        path = d / cmd.skill_name
        if _write_file(path, _pack_body(cmd)) == "wrote":
            written.append(str(path))
    return written


def install_codex_prompts(home: Path | None = None) -> list[str]:
    """Drop the four ``~/.codex/prompts/magi:pack:*.md`` prompts."""
    d = codex_prompts_dir(home)
    _make_dir(d)
    written: list[str] = []
    for cmd in _PACK_CMDS:
        path = d / cmd.prompt_name
        if _write_file(path, _pack_body(cmd)) == "wrote":
            written.append(str(path))
    return written


def install_cc_commands(home: Path | None = None) -> list[str]:
    """Drop the four ``~/.claude/commands/magi/*.md`` slash commands.

    Byte-equal to ``install.sh``; here so ``--runtime both`` is
    self-contained without shelling into the bash installer.
    """
    d = cc_commands_dir(home)
    _make_dir(d)
    written: list[str] = []
    for name, body in _CC_COMMANDS.items():
        path = d / name
        if _write_file(path, body) == "wrote":
            written.append(str(path))
    return written


def install_codex_managed(
    policies: list | None = None,
    cloud_url: str | None = None,
    etc_dir: Path | None = None,
) -> list[str]:
    """Write the three managed-enforcement artifacts (Section 6).

    ``requirements.toml`` is compiled from ``policies`` (empty list on a
    bare install → just the ``[features]`` block). ``managed_config.toml``
    is the env passthrough that pins ``MAGI_CP_RUNTIME=codex`` +
    ``MAGI_CP_CLOUD_URL``. Context templates land under
    ``magi-cp/context-templates/<sha>.txt`` for Shim B.

    Managed files are regenerated on every run (byte-stable generated
    content, not an operator-editable surface) and root-owned.
    """
    etc = etc_dir or codex_etc_dir()
    url = cloud_url or os.environ.get("MAGI_CP_CLOUD_URL", _DEFAULT_CLOUD_URL)
    _make_dir(etc)

    bundle = compile_to_codex_requirements(policies or [])

    written: list[str] = []
    req_path = etc / "requirements.toml"
    _write_file(
        req_path, bundle.requirements_toml,
        preserve=False, root_owned=True,
    )
    written.append(str(req_path))

    # managed_config.toml = env passthrough + the Magi-owned permission
    # profile block (filesystem/network rules from PermissionPolicy native
    # lowering, design 2026-07-01). requirements.toml forces + allowlists the
    # profile and carries command prefix_rules; the profile DEFINITION lives
    # here in the managed config layer.
    managed_body = _managed_config_toml(url)
    if bundle.permissions_toml:
        managed_body = managed_body.rstrip("\n") + "\n\n" + bundle.permissions_toml
    managed_path = etc / "managed_config.toml"
    _write_file(
        managed_path, managed_body,
        preserve=False, root_owned=True,
    )
    written.append(str(managed_path))

    # ── Shim B context-template sidecars ──────────────────────────────
    templates_dir = etc / "magi-cp" / "context-templates"
    _make_dir(etc / "magi-cp")
    _make_dir(templates_dir)
    for sha, template in sorted(bundle.context_templates.items()):
        tpath = templates_dir / f"{sha}.txt"
        _write_file(tpath, template, preserve=False, root_owned=True)
        written.append(str(tpath))

    _warn_if_not_root_owned(written)
    return written


def _warn_if_not_root_owned(paths: list[str]) -> None:
    """Make a weak-trust-boundary install operator-visible.

    ``install_codex_managed`` writes the managed enforcement files
    ``root_owned=True``, but ``_write_file`` only ``chown``s when the
    installer itself runs as root (design Section 6.3: the trust boundary
    is filesystem ownership). In the standard ``curl | bash`` flow the
    installer runs unprivileged, so where ``/etc/codex`` happens to be
    writable the managed files land owned by the unprivileged user and the
    trust boundary is *not* established. The only prior signal was a comment
    inside the generated file, which the operator never sees. Emit an
    explicit stderr warning listing the actual owner mismatch so the
    weaker-boundary condition is visible. POSIX-only (Windows has no
    meaningful ``st_uid``).
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
            "warning: Codex managed enforcement files are NOT root-owned "
            f"({len(not_root)} file(s)); the filesystem trust boundary is "
            "not established (design doc Section 6.3). Re-run "
            "`magi-cp install --runtime codex` under sudo to establish it.",
            file=sys.stderr,
        )


def _managed_config_toml(cloud_url: str) -> str:
    """Env passthrough TOML: pin the runtime + cloud url for Codex.

    ``MAGI_CP_RUNTIME=codex`` makes the gate dispatcher (design doc
    Section 3.4) resolve the Codex driver even when the payload sniff is
    ambiguous; ``MAGI_CP_CLOUD_URL`` points the gate at the self-host
    cloud. Both flow to child processes via Codex's managed env layer.
    """
    # Emit both env values via the shared TOML string emitter so an
    # operator-controlled ``cloud_url`` (``--cloud-url`` / ``MAGI_CP_CLOUD_URL``)
    # containing a quote, backslash, newline or tab can never produce a
    # malformed ``managed_config.toml``. A malformed file would silently brick
    # the env passthrough that pins ``MAGI_CP_RUNTIME=codex`` (regenerated with
    # ``preserve=False`` every install), degrading enforcement rather than
    # failing loudly. Matches ``codex_toml_emitter._toml_str`` used for matchers.
    return (
        "# Magi control-plane managed config for the Codex runtime.\n"
        "# Auto-generated by `magi-cp install --runtime codex`.\n"
        "# Managed layer: highest precedence, trusted by filesystem\n"
        "# ownership (design doc Section 6.3).\n"
        "[env]\n"
        f"MAGI_CP_RUNTIME = {_toml_str('codex')}\n"
        f"MAGI_CP_CLOUD_URL = {_toml_str(cloud_url)}\n"
    )


def force_remove_codex(etc_dir: Path | None = None) -> list[str]:
    """Delete the Codex managed enforcement files (Section 13 rollback).

    Removes ``requirements.toml``, ``managed_config.toml`` and the whole
    ``magi-cp/`` subtree. Idempotent: missing files are a no-op. Leaves
    the ``~/.codex`` skills / prompts in place (those are the user
    surface; only the managed layer is torn down here).
    """
    import shutil

    etc = etc_dir or codex_etc_dir()
    removed: list[str] = []
    for name in ("requirements.toml", "managed_config.toml"):
        p = etc / name
        if p.exists():
            p.unlink()
            removed.append(str(p))
    magi_dir = etc / "magi-cp"
    if magi_dir.exists():
        shutil.rmtree(magi_dir)
        removed.append(str(magi_dir))
    return removed


# ── policy source loader ───────────────────────────────────────────────
def _load_policies(path: str) -> list:
    """Load a JSON policy source (object or array of IR policy dicts)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = [raw]
    return [policy_from_dict(r) for r in raw]


# ── CLI ────────────────────────────────────────────────────────────────
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="magi-cp install")
    p.add_argument(
        "--runtime", choices=("cc", "codex", "both"), default="cc",
        help="which runtime surface to install (default: cc)",
    )
    p.add_argument(
        "--force-remove-codex", action="store_true",
        help="delete the Codex managed enforcement files (rollback)",
    )
    p.add_argument(
        "--cloud-url", default=None,
        help="cloud url pinned in managed_config.toml "
             "(default: MAGI_CP_CLOUD_URL or the local cloud)",
    )
    p.add_argument(
        "--policies", default=None,
        help="JSON file of IR policies to compile into requirements.toml "
             "(default: none → base [features] block only)",
    )
    return p


def cli(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    if args.force_remove_codex:
        removed = force_remove_codex()
        if removed:
            for path in removed:
                print(f"removed {path}")
        else:
            print("nothing to remove (Codex managed files already absent)")
        return 0

    policies = _load_policies(args.policies) if args.policies else []

    # Preflight the managed enforcement dir BEFORE writing anything for a
    # Codex install. If the enforcement layer cannot be written (non-root
    # `curl | bash` hitting /etc/codex), hard-fail loudly instead of dropping
    # the user-facing pack surface and leaving an enforce-nothing-but-looks-
    # installed state.
    etc: Path | None = None
    if args.runtime in ("codex", "both"):
        etc = codex_etc_dir()
        ok, reason = _managed_dir_writable(etc)
        if not ok:
            print(
                f"error: Codex enforcement not installed: {reason}",
                file=sys.stderr,
            )
            print(
                "The managed enforcement layer (requirements.toml) requires a "
                "writable managed dir. Re-run with sudo, or set "
                "MAGI_CP_CODEX_ETC_DIR to a writable path. No files were "
                "written; packs must not activate visually without the policy "
                "layer behind them.",
                file=sys.stderr,
            )
            return 1

    dropped: list[str] = []
    if args.runtime in ("cc", "both"):
        dropped += install_cc_commands()
    if args.runtime in ("codex", "both"):
        # Enforcement layer first, then the user-facing pack surface, so a
        # failure never yields packs-look-installed-but-policies-do-not-fire.
        dropped += install_codex_managed(policies, args.cloud_url, etc)
        dropped += install_codex_skills()
        dropped += install_codex_prompts()

    if dropped:
        for path in dropped:
            print(f"wrote {path}")
    else:
        print("nothing to write (files already present; delete to refresh)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(cli())
