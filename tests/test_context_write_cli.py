"""D57f-1 — `magi-cp-context-write` shim end-to-end.

The compiler emits a hook entry of the form
`magi-cp-context-write --event <Event> --id <sha256>` for every
ContextInjectionPolicy. CC invokes the shim at hook time; the shim
resolves the sha back into the template bytes from the sidecar
directory and prints the additionalContext JSON keyed on the event.

These tests cover every event kind so a future widening / narrowing
of `_SUPPORTED_EVENTS` is caught at the shim boundary too — the
compiler's command line uses the same event names the IR validates,
so a missing emit for one event would silently fail-open.
"""
from __future__ import annotations

import hashlib
import io
import json
import os

import pytest

from magi_cp.policy.compiler import (
    DEFAULT_CONTEXT_WRITE_SHIM, compile_to_managed_settings,
    context_template_sidecars,
)
from magi_cp.policy.ir import ContextInjectionPolicy, _SUPPORTED_EVENTS
from magi_cp.local import gate as gate_mod


@pytest.fixture
def sidecar_dir(monkeypatch, tmp_path):
    """Point the shim at an isolated sidecar directory + capture
    stdout via a StringIO swap on sys.stdout so the shim's
    `print(...)` lands in our buffer instead of the test runner's.
    """
    d = tmp_path / "context-templates"
    d.mkdir()
    monkeypatch.setenv("MAGI_CP_CONTEXT_TEMPLATES_DIR", str(d))
    return d


def _run_shim(monkeypatch, capsys, *, event: str, tpl_id: str) -> str:
    """Invoke `context_write_cli` with the given argv and return the
    captured stdout. The shim calls `sys.exit(0)` after printing, so
    we catch SystemExit and read capsys."""
    monkeypatch.setattr(
        "sys.argv",
        ["magi-cp-context-write", "--event", event, "--id", tpl_id],
    )
    try:
        gate_mod.context_write_cli()
    except SystemExit as e:
        assert e.code == 0
    out = capsys.readouterr().out
    return out


def test_shim_emits_additional_context_for_every_supported_event(
    monkeypatch, capsys, sidecar_dir,
):
    """Each event in `_SUPPORTED_EVENTS` is recognized by the shim.

    D57f-1 wired ContextInjectionPolicy onto the full 30-event surface
    via additionalContext. D59 narrowed the *authoring* surface to 26
    (Elicitation / ElicitationResult / WorktreeCreate / MessageDisplay
    use a specialized hookSpecificOutput shape and silently ignore
    additionalContext at runtime). The SHIM still accepts all 30
    `_SUPPORTED_EVENTS` names because a legacy managed-settings bundle
    on disk may still name them, and the shim's job is to produce a
    well-formed hookSpecificOutput JSON for whatever event CC fires —
    not to second-guess authoring.

    The shim must:
      - find the sidecar file by sha,
      - print exactly one hookSpecificOutput JSON with `hookEventName`
        set to the requested event and `additionalContext` set to the
        template bytes,
      - exit 0.
    """
    for ev in sorted(_SUPPORTED_EVENTS):
        template = f"context bytes for {ev}"
        tpl_id = hashlib.sha256(template.encode("utf-8")).hexdigest()
        (sidecar_dir / f"{tpl_id}.txt").write_text(template, encoding="utf-8")

        raw = _run_shim(monkeypatch, capsys, event=ev, tpl_id=tpl_id)
        assert raw.strip(), f"no stdout for event={ev}"
        obj = json.loads(raw)
        hso = obj["hookSpecificOutput"]
        assert hso["hookEventName"] == ev
        assert hso["additionalContext"] == template


def test_shim_silent_when_sidecar_missing(monkeypatch, capsys, sidecar_dir):
    """Missing sidecar → empty stdout, exit 0. CC continues with no
    injected context (fail-open ON ABSENCE is the only safe default;
    the compiler is the boundary that guarantees the sidecar exists
    when the policy is enabled)."""
    tpl_id = hashlib.sha256(b"absent").hexdigest()
    out = _run_shim(monkeypatch, capsys, event="UserPromptSubmit", tpl_id=tpl_id)
    assert out == ""


def test_shim_silent_on_path_traversal_attempt(monkeypatch, capsys, sidecar_dir):
    """A malformed `--id` argument (not 64 hex chars) is rejected
    before we touch the filesystem so a poisoned managed-settings.json
    cannot drive the shim into reading `/etc/passwd`."""
    out = _run_shim(
        monkeypatch, capsys,
        event="UserPromptSubmit",
        tpl_id="../../etc/passwd",
    )
    assert out == ""


def test_shim_silent_on_unknown_event_name(monkeypatch, capsys, sidecar_dir):
    """An event name outside the allowed character set means the
    managed-settings bundle was tampered with — the shim exits silently
    rather than emit a JSON that names a hook CC won't recognize."""
    tpl_id = hashlib.sha256(b"x").hexdigest()
    out = _run_shim(monkeypatch, capsys, event="garbage event", tpl_id=tpl_id)
    assert out == ""


def test_shim_silent_on_well_formed_but_unsupported_event(
    monkeypatch, capsys, sidecar_dir,
):
    """P1 follow-up: the shape regex used to be the only gate, so a
    well-formed-but-unknown name like "NotARealHook" would still emit
    a `hookSpecificOutput` JSON keyed on a hook CC won't recognize
    (silent fail-open across the policy bundle). The shim now
    cross-checks against `_SUPPORTED_EVENTS`."""
    # Place a sidecar so the only reason we'd refuse is the event name.
    template = "would have been injected"
    tpl_id = hashlib.sha256(template.encode("utf-8")).hexdigest()
    (sidecar_dir / f"{tpl_id}.txt").write_text(template, encoding="utf-8")
    out = _run_shim(monkeypatch, capsys, event="NotARealHook", tpl_id=tpl_id)
    assert out == ""


def test_shim_refuses_world_writable_template(monkeypatch, capsys, sidecar_dir):
    """P2 follow-up: a world-writable sidecar means an attacker can
    rewrite the template the model sees. The shim refuses silently
    instead of emitting attacker-chosen additionalContext."""
    template = "would-be malicious context"
    tpl_id = hashlib.sha256(template.encode("utf-8")).hexdigest()
    p = sidecar_dir / f"{tpl_id}.txt"
    p.write_text(template, encoding="utf-8")
    os.chmod(p, 0o666)
    out = _run_shim(monkeypatch, capsys, event="UserPromptSubmit", tpl_id=tpl_id)
    assert out == ""


def test_compile_to_stage_then_move_trap_documented(monkeypatch, capsys, tmp_path):
    """P2 follow-up: pin the deploy invariant. `compile_files(['p.json'],
    '/tmp/managed-settings.json')` lands sidecars at
    `/tmp/context-templates/<sha>.txt`, and the shim — when pointed at
    the same install location via `MAGI_CP_MANAGED_SETTINGS_PATH=/tmp/
    managed-settings.json` — must find them. The test makes the
    install-path coupling executable so a future refactor that splits
    build- and install-dirs fails this assertion instead of silently
    fail-opening every context_injection.
    """
    import json as _json
    from magi_cp.policy.compiler import compile_files

    pol_path = tmp_path / "ctx.json"
    pol_path.write_text(
        _json.dumps({
            "type": "context_injection",
            "id": "ctx/v1",
            "description": "",
            "version": "0.1",
            "event": "PreToolUse",
            "matcher": "Bash",
            "template": "warn before bash",
        }),
        encoding="utf-8",
    )
    out_path = tmp_path / "managed-settings.json"
    compile_files([str(pol_path)], str(out_path))
    # Sidecar landed next to managed-settings.json
    side_dir = tmp_path / "context-templates"
    assert side_dir.is_dir()
    sidecars = list(side_dir.glob("*.txt"))
    assert len(sidecars) == 1
    sha = sidecars[0].stem

    # Now point the shim at the SAME install path. Without
    # MAGI_CP_CONTEXT_TEMPLATES_DIR set, the shim resolves to
    # dirname(managed-settings.json)/context-templates/ — which is
    # where compile_files dropped the sidecar.
    monkeypatch.setenv("MAGI_CP_MANAGED_SETTINGS_PATH", str(out_path))
    monkeypatch.delenv("MAGI_CP_CONTEXT_TEMPLATES_DIR", raising=False)
    out = _run_shim(monkeypatch, capsys, event="PreToolUse", tpl_id=sha)
    obj = json.loads(out)
    assert obj["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert obj["hookSpecificOutput"]["additionalContext"] == "warn before bash"


def test_compiled_command_resolves_to_a_real_sidecar(
    monkeypatch, capsys, tmp_path,
):
    """End-to-end glue: compile a ContextInjectionPolicy, write its
    sidecar bytes to disk, and verify the compiled command line
    drives the shim to the matching template.

    This catches a compiler/shim drift where the compiler emits
    `--event X --id Y` but the shim parses arguments differently.
    """
    sidedir = tmp_path / "context-templates"
    sidedir.mkdir()
    monkeypatch.setenv("MAGI_CP_CONTEXT_TEMPLATES_DIR", str(sidedir))

    p = ContextInjectionPolicy(
        id="ctx/v1",
        description="",
        event="PreToolUse",
        template="be careful with rm -rf",
    )
    ms = compile_to_managed_settings([p])
    sidecars = context_template_sidecars([p])
    assert len(sidecars) == 1
    (sha, body), = sidecars.items()
    (sidedir / f"{sha}.txt").write_text(body, encoding="utf-8")

    cmd = ms["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert cmd.startswith(DEFAULT_CONTEXT_WRITE_SHIM)
    # Parse the compiler-emitted command line the same way the shim
    # does (whitespace split) to assert the args line up.
    parts = cmd.split()
    assert "--event" in parts
    assert "--id" in parts
    ev_arg = parts[parts.index("--event") + 1]
    id_arg = parts[parts.index("--id") + 1]
    assert ev_arg == "PreToolUse"
    assert id_arg == sha

    out = _run_shim(monkeypatch, capsys, event=ev_arg, tpl_id=id_arg)
    obj = json.loads(out)
    assert obj["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert obj["hookSpecificOutput"]["additionalContext"] == body
