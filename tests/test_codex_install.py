"""P3: ``magi-cp install`` drops the Codex adapter surface.

Covers the four brief requirements:
  1. ``--runtime codex`` drops the 4 skills + 4 prompts + 3 managed files
     with the right permissions.
  2. Idempotence: a re-run does not clobber operator-edited skill bodies.
  3. Skill/prompt bodies carry ``description`` + ``argument-hint``
     frontmatter and invoke the pack CLI subcommand.
  4. ``--force-remove-codex`` removes the managed files and is idempotent.

The managed-config root is redirected to a scratch dir via
``MAGI_CP_CODEX_ETC_DIR`` so the test never touches ``/etc``.
"""
import stat
from pathlib import Path

from magi_cp.local import codex_install


_SKILLS = (
    "pack-activate.md",
    "pack-deactivate.md",
    "pack-status.md",
    "pack-sticky.md",
)
_PROMPTS = (
    "magi:pack:activate.md",
    "magi:pack:deactivate.md",
    "magi:pack:status.md",
    "magi:pack:sticky.md",
)


def _wire(monkeypatch, tmp_path) -> tuple[Path, Path]:
    """Point HOME + the Codex etc dir at scratch locations."""
    home = tmp_path / "home"
    home.mkdir()
    etc = tmp_path / "etc-codex"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("MAGI_CP_CODEX_ETC_DIR", str(etc))
    # Isolate cloud url so managed_config.toml is deterministic.
    monkeypatch.delenv("MAGI_CP_CLOUD_URL", raising=False)
    return home, etc


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ── 1. drops the surface with the right permissions ────────────────────
def test_install_codex_drops_skills_prompts_and_managed(monkeypatch, tmp_path):
    home, etc = _wire(monkeypatch, tmp_path)

    rc = codex_install.cli(["--runtime", "codex"])
    assert rc == 0

    skills_dir = home / ".codex" / "skills" / "magi"
    prompts_dir = home / ".codex" / "prompts"

    for name in _SKILLS:
        p = skills_dir / name
        assert p.is_file(), f"skill {name} missing"
        assert _mode(p) == 0o644, f"skill {name} mode {oct(_mode(p))}"
    for name in _PROMPTS:
        p = prompts_dir / name
        assert p.is_file(), f"prompt {name} missing"
        assert _mode(p) == 0o644, f"prompt {name} mode {oct(_mode(p))}"

    # 3 managed artifacts: requirements.toml, managed_config.toml,
    # context-templates dir (Shim B sidecars land here).
    req = etc / "requirements.toml"
    managed = etc / "managed_config.toml"
    templates = etc / "magi-cp" / "context-templates"
    assert req.is_file() and _mode(req) == 0o644
    assert managed.is_file() and _mode(managed) == 0o644
    assert templates.is_dir()

    # requirements.toml is a real compiled bundle (base features block).
    req_text = req.read_text("utf-8")
    assert "[features]" in req_text
    assert "hooks = true" in req_text

    # managed_config.toml pins the runtime + cloud url env passthrough.
    managed_text = managed.read_text("utf-8")
    assert 'MAGI_CP_RUNTIME = "codex"' in managed_text
    assert "MAGI_CP_CLOUD_URL" in managed_text


def test_install_codex_does_not_touch_cc_commands(monkeypatch, tmp_path):
    home, _ = _wire(monkeypatch, tmp_path)
    codex_install.cli(["--runtime", "codex"])
    assert not (home / ".claude" / "commands" / "magi").exists()


def test_install_both_drops_cc_and_codex(monkeypatch, tmp_path):
    home, _ = _wire(monkeypatch, tmp_path)
    codex_install.cli(["--runtime", "both"])
    assert (home / ".claude" / "commands" / "magi" / "pack.md").is_file()
    assert (home / ".codex" / "skills" / "magi" / "pack-activate.md").is_file()


# ── 2. idempotence: preserve operator edits ────────────────────────────
def test_rerun_preserves_user_edited_skill_body(monkeypatch, tmp_path):
    home, _ = _wire(monkeypatch, tmp_path)
    assert codex_install.cli(["--runtime", "codex"]) == 0

    skill = home / ".codex" / "skills" / "magi" / "pack-activate.md"
    sentinel = "\n<!-- operator edit: keep me -->\n"
    skill.write_text(skill.read_text("utf-8") + sentinel, "utf-8")

    # Re-run must succeed and leave the hand edit intact.
    assert codex_install.cli(["--runtime", "codex"]) == 0
    assert sentinel in skill.read_text("utf-8"), "user edit clobbered"
    # Every other file is still present (nothing deleted on re-run).
    for name in _SKILLS:
        assert (home / ".codex" / "skills" / "magi" / name).is_file()
    for name in _PROMPTS:
        assert (home / ".codex" / "prompts" / name).is_file()


def test_rerun_preserves_user_edited_prompt_body(monkeypatch, tmp_path):
    home, _ = _wire(monkeypatch, tmp_path)
    assert codex_install.cli(["--runtime", "codex"]) == 0

    prompt = home / ".codex" / "prompts" / "magi:pack:sticky.md"
    sentinel = "\n<!-- do not clobber -->\n"
    prompt.write_text(prompt.read_text("utf-8") + sentinel, "utf-8")

    assert codex_install.cli(["--runtime", "codex"]) == 0
    assert sentinel in prompt.read_text("utf-8")


# ── 3. skill/prompt body shape ─────────────────────────────────────────
def _parse_frontmatter(text: str) -> dict:
    """Minimal ``key: value`` YAML frontmatter parser (top block only)."""
    assert text.startswith("---\n"), "no frontmatter fence"
    end = text.index("\n---\n", 4)
    block = text[4:end]
    out: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def test_skill_bodies_have_frontmatter_and_invoke_cli(monkeypatch, tmp_path):
    home, _ = _wire(monkeypatch, tmp_path)
    codex_install.cli(["--runtime", "codex"])
    skills_dir = home / ".codex" / "skills" / "magi"

    expected_sub = {
        "pack-activate.md": "magi-cp session pack activate",
        "pack-deactivate.md": "magi-cp session pack deactivate",
        "pack-status.md": "magi-cp session pack status",
        "pack-sticky.md": "magi-cp session pack sticky",
    }
    for name, invocation in expected_sub.items():
        text = (skills_dir / name).read_text("utf-8")
        fm = _parse_frontmatter(text)
        assert fm.get("description"), f"{name} missing description"
        assert fm.get("argument-hint"), f"{name} missing argument-hint"
        assert invocation in text, f"{name} does not invoke {invocation!r}"


def test_prompt_bodies_route_to_same_cli(monkeypatch, tmp_path):
    home, _ = _wire(monkeypatch, tmp_path)
    codex_install.cli(["--runtime", "codex"])
    prompts_dir = home / ".codex" / "prompts"

    expected_sub = {
        "magi:pack:activate.md": "magi-cp session pack activate",
        "magi:pack:deactivate.md": "magi-cp session pack deactivate",
        "magi:pack:status.md": "magi-cp session pack status",
        "magi:pack:sticky.md": "magi-cp session pack sticky",
    }
    for name, invocation in expected_sub.items():
        text = (prompts_dir / name).read_text("utf-8")
        fm = _parse_frontmatter(text)
        assert fm.get("description")
        assert fm.get("argument-hint")
        assert invocation in text


def test_skill_and_prompt_bodies_are_byte_equal(monkeypatch, tmp_path):
    """Same body shape between the two surfaces (design doc Section 5.2):
    a Codex skill and its sibling prompt route to the exact same CLI."""
    home, _ = _wire(monkeypatch, tmp_path)
    codex_install.cli(["--runtime", "codex"])
    skills_dir = home / ".codex" / "skills" / "magi"
    prompts_dir = home / ".codex" / "prompts"
    pairs = (
        ("pack-activate.md", "magi:pack:activate.md"),
        ("pack-deactivate.md", "magi:pack:deactivate.md"),
        ("pack-status.md", "magi:pack:status.md"),
        ("pack-sticky.md", "magi:pack:sticky.md"),
    )
    for skill_name, prompt_name in pairs:
        assert (skills_dir / skill_name).read_text("utf-8") == \
            (prompts_dir / prompt_name).read_text("utf-8")


# ── managed requirements.toml compiles supplied policies ───────────────
def test_managed_requirements_compiles_context_template(monkeypatch, tmp_path):
    """A ContextInjection policy produces a hook table AND a sha256
    sidecar under context-templates/ (Shim B)."""
    _, etc = _wire(monkeypatch, tmp_path)
    policies = tmp_path / "policies.json"
    policies.write_text(
        '[{"type": "context_injection", "id": "ctx1", '
        '"event": "UserPromptSubmit", "matcher": "*", '
        '"template": "remember: cite sources"}]',
        "utf-8",
    )
    rc = codex_install.cli(["--runtime", "codex", "--policies", str(policies)])
    assert rc == 0

    req_text = (etc / "requirements.toml").read_text("utf-8")
    assert "[[hooks.UserPromptSubmit]]" in req_text

    templates = list((etc / "magi-cp" / "context-templates").glob("*.txt"))
    assert len(templates) == 1
    assert templates[0].read_text("utf-8") == "remember: cite sources"
    assert _mode(templates[0]) == 0o644


# ── 4. --force-remove-codex ────────────────────────────────────────────
def test_force_remove_codex_removes_managed_and_is_idempotent(
    monkeypatch, tmp_path
):
    _, etc = _wire(monkeypatch, tmp_path)
    codex_install.cli(["--runtime", "codex"])
    assert (etc / "requirements.toml").is_file()
    assert (etc / "managed_config.toml").is_file()
    assert (etc / "magi-cp").is_dir()

    assert codex_install.cli(["--force-remove-codex"]) == 0
    assert not (etc / "requirements.toml").exists()
    assert not (etc / "managed_config.toml").exists()
    assert not (etc / "magi-cp").exists()

    # Idempotent: a second removal is a clean no-op.
    assert codex_install.cli(["--force-remove-codex"]) == 0


def test_force_remove_codex_leaves_skills_and_prompts(monkeypatch, tmp_path):
    """Rollback tears down the managed layer only; the user surface
    (skills + prompts) stays put (design doc Section 13)."""
    home, _ = _wire(monkeypatch, tmp_path)
    codex_install.cli(["--runtime", "codex"])
    codex_install.cli(["--force-remove-codex"])
    assert (home / ".codex" / "skills" / "magi" / "pack-activate.md").is_file()
    assert (home / ".codex" / "prompts" / "magi:pack:activate.md").is_file()


# ── managed_config.toml cloud_url escaping ─────────────────────────────
def test_managed_config_escapes_cloud_url(monkeypatch, tmp_path):
    """An operator-controlled cloud_url with TOML-significant bytes must be
    escaped so managed_config.toml stays a valid TOML basic string (never
    silently bricks the MAGI_CP_RUNTIME=codex passthrough)."""
    import tomllib

    _, etc = _wire(monkeypatch, tmp_path)
    weird = 'http://ex"ample\\path'
    rc = codex_install.cli(["--runtime", "codex", "--cloud-url", weird])
    assert rc == 0

    text = (etc / "managed_config.toml").read_text("utf-8")
    # The raw quote/backslash are escaped, not emitted literally.
    assert 'MAGI_CP_CLOUD_URL = "http://ex\\"ample\\\\path"' in text
    # And the whole file round-trips through a real TOML parser.
    parsed = tomllib.loads(text)
    assert parsed["env"]["MAGI_CP_CLOUD_URL"] == weird
    assert parsed["env"]["MAGI_CP_RUNTIME"] == "codex"


# ── preflight: hard-fail instead of partial (enforce-nothing) install ──
def test_codex_preflight_failure_hard_fails_without_partial_install(
    monkeypatch, tmp_path, capsys
):
    home, etc = _wire(monkeypatch, tmp_path)
    monkeypatch.setattr(
        codex_install, "_managed_dir_writable",
        lambda p: (False, "managed dir is not writable"),
    )
    rc = codex_install.cli(["--runtime", "codex"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "Codex enforcement not installed" in err
    assert "sudo" in err
    # No user surface + no managed files => never enforce-nothing-but-looks-
    # installed.
    assert not (home / ".codex" / "skills" / "magi").exists()
    assert not (home / ".codex" / "prompts").exists()
    assert not (etc / "requirements.toml").exists()


def test_codex_enforcement_written_before_user_surface(monkeypatch, tmp_path):
    """requirements.toml (enforcement) lands whenever the pack skills do,
    so a visible pack surface always has its policy layer behind it."""
    home, etc = _wire(monkeypatch, tmp_path)
    assert codex_install.cli(["--runtime", "codex"]) == 0
    assert (etc / "requirements.toml").is_file()
    assert (home / ".codex" / "skills" / "magi" / "pack-activate.md").is_file()


# ── root-owned trust-boundary warning is operator-visible ──────────────
def test_managed_files_warn_when_not_root_owned(monkeypatch, tmp_path, capsys):
    import os as _os

    import pytest

    if not hasattr(_os, "geteuid") or _os.geteuid() == 0:
        pytest.skip("requires a non-root euid to observe the weak boundary")
    _wire(monkeypatch, tmp_path)
    assert codex_install.cli(["--runtime", "codex"]) == 0
    err = capsys.readouterr().err
    assert "NOT root-owned" in err
    assert "trust boundary" in err


# ── dispatch through the top-level `magi-cp install` CLI ───────────────
def test_top_level_magi_cp_install_dispatch(monkeypatch, tmp_path):
    from magi_cp.cli.__main__ import main as cli_main

    home, etc = _wire(monkeypatch, tmp_path)
    assert cli_main(["install", "--runtime", "codex"]) == 0
    assert (home / ".codex" / "skills" / "magi" / "pack-status.md").is_file()
    assert (etc / "requirements.toml").is_file()
