"""P0 sanity: 패키지 import + 구조 존재."""
import importlib


def test_package_imports():
    import magi_cp
    assert magi_cp is not None


def test_subpackages():
    for name in ["verifier", "policy", "evidence", "cloud", "local", "mcp", "cli"]:
        m = importlib.import_module(f"magi_cp.{name}")
        assert m is not None
