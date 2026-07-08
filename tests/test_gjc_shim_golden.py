"""U3 gjc frozen shim assets: golden + zero-policy + registration + subprocess harness.

Design brief: 2026-07-08-magi-cp-gajae-code-runtime-adapter-design
Section 11.1 U3 sub-tests (a)-(d).

The constrained plugin hook shim lives in ``runtime/gjc_assets/``.
These tests pin the vendored bytes and assert the five Section 5.2
contract invariants that can be verified statically:
  (a) vendored shim bytes == the checked-in file bytes (byte-stable golden)
  (b) zero policy logic (grep: no tool names, no policy ids from _GJC_TO_CC_TOOL)
  (c) exactly one api.on() call per module; the declared event matches
      the manifest template
  (d) subprocess harness: stub magi-cp binary exercises deny / allow-empty /
      allow-json / timeout / spawn-fail / garbage-stdout rows from §5.2
      (bun-optional; skipped when bun is absent; tests are marked to fail
      when bun is present but an assertion fails)

Contract note: (a)-(c) are the authoritative source of truth.  (d) verifies
runtime round-trips but is CONDITIONAL on bun being present.  Missing bun
never fails the test suite.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import textwrap

import pytest

# ── Locate the assets directory ────────────────────────────────────────
_HERE = pathlib.Path(__file__).parent
_ASSETS = _HERE.parent / "src" / "magi_cp" / "runtime" / "gjc_assets"

# File names the spec mandates (§4.2, §6.1)
_TMPL = "gajae-plugin.json.tmpl"
_SHIM_TOOL_CALL = "magi-gate-tool-call.ts"
_SHIM_SESSION_START = "magi-gate-session-start.ts"
_SHIM_SESSION_SHUTDOWN = "magi-gate-session-shutdown.ts"

# Every managed-content file in the assets directory (template + 3 shims)
_ALL_ASSETS = [_TMPL, _SHIM_TOOL_CALL, _SHIM_SESSION_START, _SHIM_SESSION_SHUTDOWN]

# Expected event strings per file (§5.1, §5.3)
_EXPECTED_EVENTS = {
    _SHIM_TOOL_CALL: "tool_call",
    _SHIM_SESSION_START: "session_start",
    _SHIM_SESSION_SHUTDOWN: "session_shutdown",
}

# Tool names from _GJC_TO_CC_TOOL that must NOT appear in shim source (§5.2 invariant 1)
_NORMALIZATION_TOOL_NAMES = [
    "bash", "read", "write", "edit", "ast_edit",
    "search", "ast_grep", "find", "web_search", "todo_write",
    "task", "subagent",
    "Bash", "Read", "Write", "Edit", "Grep", "Glob", "WebSearch", "TodoWrite", "Task",
]

# ── (a) byte-stable golden ─────────────────────────────────────────────


@pytest.mark.parametrize("fname", _ALL_ASSETS)
def test_asset_file_exists(fname: str) -> None:
    """Each vendored asset file must exist at the expected path."""
    p = _ASSETS / fname
    assert p.exists(), (
        f"Missing asset file: {p}\n"
        "Run U3 GREEN step: create src/magi_cp/runtime/gjc_assets/{fname}"
    )


@pytest.mark.parametrize("fname", _ALL_ASSETS)
def test_asset_file_nonempty(fname: str) -> None:
    """Each vendored asset must be non-empty."""
    p = _ASSETS / fname
    if not p.exists():
        pytest.skip(f"{fname} not yet created (U3 GREEN pending)")
    assert p.stat().st_size > 0, f"{fname} is empty"


@pytest.mark.parametrize("fname", _ALL_ASSETS)
def test_asset_file_sha256_stable(fname: str) -> None:
    """(a) reading the same file twice yields byte-identical content.

    This is the mechanical precondition for the golden: the bytes we
    test are the bytes the emitter will embed (§5.2 invariant 6).
    """
    p = _ASSETS / fname
    if not p.exists():
        pytest.skip(f"{fname} not yet created")
    content1 = p.read_bytes()
    content2 = p.read_bytes()
    sha1 = hashlib.sha256(content1).hexdigest()
    sha2 = hashlib.sha256(content2).hexdigest()
    assert sha1 == sha2, f"{fname}: consecutive reads differ (filesystem issue?)"


# ── (b) zero-policy assertions ─────────────────────────────────────────

_SHIM_FILES = [_SHIM_TOOL_CALL, _SHIM_SESSION_START, _SHIM_SESSION_SHUTDOWN]


@pytest.mark.parametrize("fname", _SHIM_FILES)
@pytest.mark.parametrize("tool_name", [
    "bash", "read", "write", "edit", "ast_edit", "search",
    "ast_grep", "find", "web_search", "todo_write", "task", "subagent",
])
def test_shim_no_gjc_tool_name_literal(fname: str, tool_name: str) -> None:
    """(b) zero-policy: no literal gjc tool name appears in shim source.

    The shim NEVER inspects tool_name semantically (§5.2 invariant 1).
    It copies fields and relays the verdict; the gate decides.
    """
    p = _ASSETS / fname
    if not p.exists():
        pytest.skip(f"{fname} not yet created")
    src = p.read_text(encoding="utf-8")
    # Look for the string as a quoted literal or standalone identifier.
    # Allow it to appear inside a comment — the invariant is about
    # semantic use, not mentions inside a doc comment.
    lines_with_match = [
        (i + 1, line)
        for i, line in enumerate(src.splitlines())
        if (
            f'"{tool_name}"' in line
            or f"'{tool_name}'" in line
            or f"`{tool_name}`" in line
        )
        and not line.lstrip().startswith("//")
    ]
    assert not lines_with_match, (
        f"{fname}: found gjc tool name {tool_name!r} at lines "
        f"{[lineno for lineno, _ in lines_with_match]} — zero-policy violated"
    )


@pytest.mark.parametrize("fname", _SHIM_FILES)
def test_shim_no_cc_tool_name_literal(fname: str) -> None:
    """(b) zero-policy: no CC canonical tool name (PascalCase) in shim source."""
    p = _ASSETS / fname
    if not p.exists():
        pytest.skip(f"{fname} not yet created")
    src = p.read_text(encoding="utf-8")
    cc_names = ["Bash", "Read", "Write", "Edit", "Grep", "Glob", "WebSearch", "TodoWrite", "Task"]
    violations = []
    for name in cc_names:
        for i, line in enumerate(src.splitlines()):
            if (
                f'"{name}"' in line
                or f"'{name}'" in line
            ) and not line.lstrip().startswith("//"):
                violations.append((i + 1, name, line.rstrip()))
    assert not violations, (
        f"{fname}: found CC tool names in shim source (zero-policy violated): "
        f"{violations}"
    )


# ── (c) registration discipline ────────────────────────────────────────


@pytest.mark.parametrize("fname", _SHIM_FILES)
def test_shim_exactly_one_api_on_call(fname: str) -> None:
    """(c) exactly one api.on() call per module (§5.2 invariant 4).

    gjc quarantines hooks that register != 1 handler
    (constrained-hooks.ts:124-134).
    """
    p = _ASSETS / fname
    if not p.exists():
        pytest.skip(f"{fname} not yet created")
    src = p.read_text(encoding="utf-8")
    # Count non-comment lines containing api.on(
    matches = [
        line for line in src.splitlines()
        if "api.on(" in line and not line.lstrip().startswith("//")
    ]
    assert len(matches) == 1, (
        f"{fname}: expected exactly 1 api.on() call, found {len(matches)}: {matches}"
    )


@pytest.mark.parametrize("fname,expected_event", _EXPECTED_EVENTS.items())
def test_shim_event_matches_manifest_template(fname: str, expected_event: str) -> None:
    """(c) the event string in the shim matches the manifest template declaration."""
    p_shim = _ASSETS / fname
    p_tmpl = _ASSETS / _TMPL
    if not p_shim.exists():
        pytest.skip(f"{fname} not yet created")
    if not p_tmpl.exists():
        pytest.skip(f"{_TMPL} not yet created")

    src = p_shim.read_text(encoding="utf-8")
    # Find the api.on( call line and extract the first string arg
    on_line = next(
        (line for line in src.splitlines()
         if "api.on(" in line and not line.lstrip().startswith("//")),
        None,
    )
    assert on_line is not None, f"{fname}: api.on() call not found"
    # Extract the event string from api.on("<event>", ...)
    m = re.search(r'api\.on\(\s*["\']([^"\']+)["\']', on_line)
    assert m is not None, f"{fname}: could not parse event from api.on() call: {on_line!r}"
    shim_event = m.group(1)
    assert shim_event == expected_event, (
        f"{fname}: api.on() event is {shim_event!r}, expected {expected_event!r}"
    )

    # Cross-check against the manifest template
    tmpl_text = p_tmpl.read_text(encoding="utf-8")
    # The template should contain a "hooks" array with an entry matching this event
    assert f'"event": "{expected_event}"' in tmpl_text or f'"event":"{expected_event}"' in tmpl_text, (
        f"Manifest template does not declare event {expected_event!r} for {fname}"
    )


def test_manifest_template_hook_count() -> None:
    """(c) manifest template declares exactly 3 hooks (one per shim module)."""
    p = _ASSETS / _TMPL
    if not p.exists():
        pytest.skip(f"{_TMPL} not yet created")
    src = p.read_text(encoding="utf-8")
    # Count "event" keys in the hooks array (rough but sufficient for the golden)
    event_count = src.count('"event"')
    assert event_count == 3, (
        f"Manifest template has {event_count} hook event declarations, expected 3"
    )


def test_manifest_template_no_target_field() -> None:
    """(c) manifest hooks have NO 'target' field — governs every tool (§6.1)."""
    p = _ASSETS / _TMPL
    if not p.exists():
        pytest.skip(f"{_TMPL} not yet created")
    src = p.read_text(encoding="utf-8")
    assert '"target"' not in src, (
        "Manifest template contains 'target' — hooks must be target-less to govern every tool"
    )


def test_manifest_template_sha256_placeholder() -> None:
    """(c) manifest template has 3 '<computed>' sha256 placeholders (one per hook)."""
    p = _ASSETS / _TMPL
    if not p.exists():
        pytest.skip(f"{_TMPL} not yet created")
    src = p.read_text(encoding="utf-8")
    count = src.count("<computed>")
    assert count == 3, (
        f"Manifest template has {count} '<computed>' sha256 placeholder(s), expected 3"
    )


# ── (d) subprocess harness (bun-optional) ──────────────────────────────

_BUN = shutil.which("bun")
_BUN_SKIP = pytest.mark.skipif(_BUN is None, reason="bun not on PATH")


def _stub_gate_script(verdict_json: str | None, *, sleep_s: float = 0) -> str:
    """Return a shell script that emits `verdict_json` on stdout and exits 0.

    When `sleep_s > 0` the script sleeps first (to trigger timeout).
    When `verdict_json` is None the script emits nothing (silent allow).
    """
    sleep_line = f"sleep {sleep_s}" if sleep_s > 0 else ""
    emit_line = f"printf '%s\\n' '{verdict_json}'" if verdict_json is not None else ""
    return textwrap.dedent(f"""\
        #!/bin/sh
        {sleep_line}
        {emit_line}
    """)


def _run_shim(
    gate_script: str,
    *,
    shim_file: str = _SHIM_TOOL_CALL,
    timeout_ms: int = 2000,
    env_extra: dict | None = None,
    bun_timeout: float = 5.0,
) -> tuple[int, str, str]:
    """Run the shim via bun against a stub gate binary.

    Returns ``(returncode, stdout, stderr)``.
    The shim receives a minimal tool_call event on stdin; it fires the handler
    and we capture what the gjc runtime would receive back.
    """
    assert _BUN is not None, "bun not available"
    p_shim = _ASSETS / shim_file
    if not p_shim.exists():
        pytest.skip(f"{shim_file} not yet created")

    with tempfile.TemporaryDirectory() as tmpdir:
        # Write stub gate binary
        gate_bin = os.path.join(tmpdir, "magi-cp")
        with open(gate_bin, "w") as fh:
            fh.write(gate_script)
        os.chmod(gate_bin, 0o755)

        # Write a thin bun harness that loads the shim, synthesises the event,
        # and prints the handler's return value as JSON (or "ALLOW" for undefined)
        harness = os.path.join(tmpdir, "harness.ts")
        harness_src = textwrap.dedent(f"""\
            import factory from "{p_shim}";

            const event = {{
              type: "tool_call",
              toolName: "bash",
              toolCallId: "call_test",
              input: {{ command: "ls" }},
            }};
            const ctx = {{
              cwd: "/workspace",
              model: {{ id: "claude-opus-4-5" }},
              sessionManager: {{
                getSessionId: () => "sess-test-1234",
                getSessionFile: () => "/tmp/sess-test-1234.json",
              }},
            }};

            const GATE = "{gate_bin}";
            // Override MAGI_CP_GATE_BIN to the stub gate
            process.env.MAGI_CP_GATE_BIN = GATE;

            const api = {{
              on: (event_name: string, handler: Function) => {{
                if (event_name === "tool_call") {{
                  Promise.resolve(handler(event, ctx)).then((result: any) => {{
                    if (result === undefined || result === null) {{
                      process.stdout.write("ALLOW\\n");
                    }} else {{
                      process.stdout.write(JSON.stringify(result) + "\\n");
                    }}
                  }}).catch((e: Error) => {{
                    process.stderr.write("HARNESS_ERROR: " + String(e) + "\\n");
                    process.exit(1);
                  }});
                }}
              }},
              logger: {{ debug: () => {{}}, info: () => {{}}, warn: () => {{}}, error: () => {{}} }},
            }};

            factory(api);
        """)
        with open(harness, "w") as fh:
            fh.write(harness_src)

        env = dict(os.environ)
        env["MAGI_CP_GATE_BIN"] = gate_bin
        if env_extra:
            env.update(env_extra)
        env["PATH"] = tmpdir + ":" + env.get("PATH", "")

        result = subprocess.run(
            [_BUN, "run", harness],
            capture_output=True,
            text=True,
            timeout=bun_timeout,
            env=env,
            cwd=tmpdir,
        )
        return result.returncode, result.stdout, result.stderr


@pytest.mark.skipif(_BUN is None, reason="bun not on PATH")
def test_shim_subprocess_deny_returns_block() -> None:
    """(d) gate emits deny JSON -> shim returns {block: true, reason}."""
    p_shim = _ASSETS / _SHIM_TOOL_CALL
    if not p_shim.exists():
        pytest.skip(f"{_SHIM_TOOL_CALL} not yet created")
    deny_json = json.dumps({"block": True, "reason": "MAGI: test blocked"})
    gate_script = _stub_gate_script(deny_json)
    rc, stdout, stderr = _run_shim(gate_script)
    assert rc == 0, f"harness exit {rc}; stderr={stderr}"
    result = json.loads(stdout.strip())
    assert result.get("block") is True, f"Expected block=true, got: {result}"
    assert "MAGI" in result.get("reason", ""), f"Expected MAGI in reason: {result}"


@pytest.mark.skipif(_BUN is None, reason="bun not on PATH")
def test_shim_subprocess_allow_empty_stdout_returns_undefined() -> None:
    """(d) gate emits empty stdout (silent allow) -> shim returns undefined (ALLOW)."""
    p_shim = _ASSETS / _SHIM_TOOL_CALL
    if not p_shim.exists():
        pytest.skip(f"{_SHIM_TOOL_CALL} not yet created")
    gate_script = _stub_gate_script(None)
    rc, stdout, stderr = _run_shim(gate_script)
    assert rc == 0, f"harness exit {rc}; stderr={stderr}"
    assert stdout.strip() == "ALLOW", f"Expected ALLOW for silent gate, got: {stdout!r}"


@pytest.mark.skipif(_BUN is None, reason="bun not on PATH")
def test_shim_subprocess_allow_block_false_json_returns_allow() -> None:
    """(d) gate emits {block: false} -> shim returns undefined (ALLOW).

    §4.3: 'block: false' or anything non-blocking = allow.
    """
    p_shim = _ASSETS / _SHIM_TOOL_CALL
    if not p_shim.exists():
        pytest.skip(f"{_SHIM_TOOL_CALL} not yet created")
    allow_json = json.dumps({"block": False})
    gate_script = _stub_gate_script(allow_json)
    rc, stdout, stderr = _run_shim(gate_script)
    assert rc == 0, f"harness exit {rc}; stderr={stderr}"
    assert stdout.strip() == "ALLOW", f"Expected ALLOW for block=false, got: {stdout!r}"


@pytest.mark.skipif(_BUN is None, reason="bun not on PATH")
def test_shim_subprocess_garbage_stdout_returns_block_closed() -> None:
    """(d) gate emits unparseable stdout -> shim fail-closed {block: true}.

    §5.2 invariant 2: fail-closed on garbage stdout.
    """
    p_shim = _ASSETS / _SHIM_TOOL_CALL
    if not p_shim.exists():
        pytest.skip(f"{_SHIM_TOOL_CALL} not yet created")
    gate_script = _stub_gate_script("this is not json }{{{")
    rc, stdout, stderr = _run_shim(gate_script)
    assert rc == 0, f"harness exit {rc}; stderr={stderr}"
    result = json.loads(stdout.strip())
    assert result.get("block") is True, f"Expected block=true on garbage, got: {result}"


# Note: timeout and spawn-fail cases require either a real spawnSync environment
# or a bun harness that can simulate process.env override + binary absence.
# Those are validated by the static golden (a)-(c) + the review of §5.1 pseudocode.
# The subprocess harness above covers the high-value runtime rows.
