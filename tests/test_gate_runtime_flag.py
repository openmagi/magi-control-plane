"""P3: ``magi-cp gate --runtime codex`` convenience flag.

The flag is documented (design doc Section 6.2) as exactly equivalent to
setting ``MAGI_CP_RUNTIME=codex`` before dispatch. It is what the Codex
managed ``requirements.toml`` emits on every hook command. This test
covers the argv parse only (no stdin dispatch), plus the no-flag no-op so
the plain CC invocation stays byte-identical.
"""
import os

from magi_cp.local.gate import _apply_runtime_flag


def _clean(monkeypatch):
    monkeypatch.delenv("MAGI_CP_RUNTIME", raising=False)


def test_runtime_flag_sets_env(monkeypatch):
    _clean(monkeypatch)
    _apply_runtime_flag(["--runtime", "codex"])
    assert os.environ["MAGI_CP_RUNTIME"] == "codex"


def test_runtime_flag_equals_form(monkeypatch):
    _clean(monkeypatch)
    _apply_runtime_flag(["--runtime=codex"])
    assert os.environ["MAGI_CP_RUNTIME"] == "codex"


def test_runtime_flag_cc_value(monkeypatch):
    _clean(monkeypatch)
    _apply_runtime_flag(["--runtime", "cc"])
    assert os.environ["MAGI_CP_RUNTIME"] == "cc"


def test_no_flag_is_noop(monkeypatch):
    """No ``--runtime`` flag must NOT set the env var (CC path unchanged)."""
    _clean(monkeypatch)
    _apply_runtime_flag([])
    assert "MAGI_CP_RUNTIME" not in os.environ
    _apply_runtime_flag(["--some-other-flag", "x"])
    assert "MAGI_CP_RUNTIME" not in os.environ


def test_dangling_flag_is_noop(monkeypatch):
    """``--runtime`` with no value must not crash or set anything."""
    _clean(monkeypatch)
    _apply_runtime_flag(["--runtime"])
    assert "MAGI_CP_RUNTIME" not in os.environ
