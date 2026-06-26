"""Q97a — `/admin/llm-keys` dashboard surface.

Three endpoints, all admin-key gated:
  GET  /admin/llm-keys       returns {anthropic:{set,last4}, openai:{set,last4}}
  PUT  /admin/llm-keys       writes the on-disk overlay (0600) and rebuilds
                              app.state.llm_compiler / llm_reviewer in-place
  POST /admin/llm-keys/test  one "ping" completion per provider

The GET response NEVER carries the raw key value, only `set: bool` and
`last4`. PUT preserves missing fields, clears empty strings, overwrites
non-empty strings. After PUT the very next /policies/compile-interactive
call uses the just-rebuilt singleton (no container restart).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud import llm_key_store
from magi_cp.cloud.app import create_app


HDR_ADMIN = {"X-Admin-Api-Key": "test-admin"}


@pytest.fixture(autouse=True)
def _admin_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", "test-admin")


@pytest.fixture(autouse=True)
def _isolated_key_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Path:
    target = tmp_path / "keys"
    target.mkdir()
    monkeypatch.setenv("MAGI_CP_KEY_DIR", str(target))
    return target


def _tmp_policy_path() -> str:
    f = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    f.write("[]")
    f.close()
    return f.name


class _FakeProvider:
    """Captures `.complete(...)` calls so tests can verify hot-reload."""

    def __init__(self, label: str, *, should_raise: bool = False) -> None:
        self.label = label
        self.should_raise = should_raise
        self.calls: list = []

    def complete(self, *a, **kw):
        self.calls.append((a, kw))
        if self.should_raise:
            raise RuntimeError(f"{self.label} simulated failure")
        return f"ok-{self.label}"


def _client(
    *,
    compiler=None,
    reviewer=None,
) -> TestClient:
    app = create_app(
        dsn="sqlite:///:memory:",
        policy_store_path=_tmp_policy_path(),
        llm_compiler=compiler,
        llm_reviewer=reviewer,
    )
    return TestClient(app)


# ── GET ───────────────────────────────────────────────────────────────


def test_get_returns_unset_when_store_empty() -> None:
    c = _client()
    r = c.get("/admin/llm-keys", headers=HDR_ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "anthropic": {"set": False, "last4": None},
        "openai": {"set": False, "last4": None},
    }


def test_get_returns_last4_only_never_raw_key() -> None:
    llm_key_store.set(anthropic="sk-ant-secret1234", openai="sk-secret5678")
    c = _client()
    r = c.get("/admin/llm-keys", headers=HDR_ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["anthropic"]["set"] is True
    assert body["anthropic"]["last4"] == "1234"
    assert body["openai"]["set"] is True
    assert body["openai"]["last4"] == "5678"
    # Defense in depth: the raw value must never appear anywhere in the
    # response body. A buggy dev who reaches for `raw=` would catch it
    # here.
    text = r.text
    assert "sk-ant-secret1234" not in text
    assert "sk-secret5678" not in text


def test_get_requires_admin_key() -> None:
    c = _client()
    r = c.get("/admin/llm-keys")
    assert r.status_code == 401, r.text


def test_get_rejects_wrong_admin_key() -> None:
    c = _client()
    r = c.get("/admin/llm-keys", headers={"X-Admin-Api-Key": "wrong"})
    assert r.status_code == 401


# ── PUT ───────────────────────────────────────────────────────────────


def test_put_persists_both_keys_and_returns_status() -> None:
    c = _client()
    r = c.put(
        "/admin/llm-keys",
        headers=HDR_ADMIN,
        json={"anthropic_api_key": "sk-ant-aa11",
              "openai_api_key": "sk-bb22"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["anthropic"] == {"set": True, "last4": "aa11"}
    assert body["openai"] == {"set": True, "last4": "bb22"}
    # On-disk store actually got the values.
    assert llm_key_store.get() == {
        "anthropic": "sk-ant-aa11", "openai": "sk-bb22",
    }


def test_put_missing_field_preserves_prior_value() -> None:
    llm_key_store.set(anthropic="sk-ant-old", openai="sk-old")
    c = _client()
    r = c.put(
        "/admin/llm-keys",
        headers=HDR_ADMIN,
        json={"anthropic_api_key": "sk-ant-new"},  # openai omitted
    )
    assert r.status_code == 200
    body = r.json()
    assert body["anthropic"]["last4"] == "-new"
    assert body["openai"]["last4"] == "-old"


def test_put_empty_string_clears_key() -> None:
    llm_key_store.set(anthropic="sk-ant-x", openai="sk-y")
    c = _client()
    r = c.put(
        "/admin/llm-keys",
        headers=HDR_ADMIN,
        json={"anthropic_api_key": ""},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["anthropic"] == {"set": False, "last4": None}
    assert body["openai"] == {"set": True, "last4": "sk-y"}


def test_put_requires_admin_key() -> None:
    c = _client()
    r = c.put(
        "/admin/llm-keys",
        json={"anthropic_api_key": "x"},
    )
    assert r.status_code == 401


def test_put_rejects_unknown_fields() -> None:
    """extra='forbid' on the pydantic body — typos / SSRF-style smuggling
    keys land as a clean 422 instead of a silent accept."""
    c = _client()
    r = c.put(
        "/admin/llm-keys",
        headers=HDR_ADMIN,
        json={"anthropic_api_key": "x", "secret_backdoor": "y"},
    )
    assert r.status_code == 422


def test_put_rebuilds_app_state_singletons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After PUT, `app.state.llm_compiler` and `.llm_reviewer` must be
    re-built via `_resolve_llm_provider_from_env`. The construct-time
    singletons (stale) must be REPLACED, not appended-to."""
    # Wire fake factories the env-pointed resolver will load.
    fake_compiler = _FakeProvider("compiler-after-put")
    fake_reviewer = _FakeProvider("reviewer-after-put")
    import sys
    import types
    mod = types.ModuleType("magi_cp_test_llm_factory")

    def _make_compiler():
        return fake_compiler

    def _make_reviewer():
        return fake_reviewer

    mod.make_compiler = _make_compiler
    mod.make_reviewer = _make_reviewer
    sys.modules["magi_cp_test_llm_factory"] = mod
    monkeypatch.setenv(
        "MAGI_CP_LLM_COMPILER",
        "magi_cp_test_llm_factory:make_compiler",
    )
    monkeypatch.setenv(
        "MAGI_CP_LLM_REVIEWER",
        "magi_cp_test_llm_factory:make_reviewer",
    )

    # Start the app with the OLD compiler/reviewer singletons; the PUT
    # route should swap them out for the env-pointed factory output.
    old_compiler = _FakeProvider("compiler-before-put")
    old_reviewer = _FakeProvider("reviewer-before-put")
    c = _client(compiler=old_compiler, reviewer=old_reviewer)
    # Sanity check construct-time wiring.
    assert c.app.state.llm_compiler is old_compiler
    assert c.app.state.llm_reviewer is old_reviewer

    r = c.put(
        "/admin/llm-keys",
        headers=HDR_ADMIN,
        json={"anthropic_api_key": "sk-ant-zzzz",
              "openai_api_key": "sk-yyyy"},
    )
    assert r.status_code == 200, r.text

    # Q97a contract: in-place swap of the singletons.
    assert c.app.state.llm_compiler is fake_compiler
    assert c.app.state.llm_reviewer is fake_reviewer


def test_put_oversize_key_rejected_at_pydantic_boundary() -> None:
    """Defense against pasted garbage / runaway payload."""
    c = _client()
    r = c.put(
        "/admin/llm-keys",
        headers=HDR_ADMIN,
        json={"anthropic_api_key": "x" * 10_000},
    )
    assert r.status_code == 422


# ── POST .../test ────────────────────────────────────────────────────


def test_test_endpoint_runs_one_provider_when_requested() -> None:
    compiler = _FakeProvider("c1")
    reviewer = _FakeProvider("r1")
    c = _client(compiler=compiler, reviewer=reviewer)
    r = c.post(
        "/admin/llm-keys/test",
        headers=HDR_ADMIN,
        json={"provider": "anthropic"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"ok": True, "error": None, "provider_used": "anthropic"}
    assert len(compiler.calls) == 1
    assert len(reviewer.calls) == 0


def test_test_endpoint_runs_both_when_unspecified() -> None:
    compiler = _FakeProvider("c1")
    reviewer = _FakeProvider("r1")
    c = _client(compiler=compiler, reviewer=reviewer)
    r = c.post("/admin/llm-keys/test", headers=HDR_ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"anthropic", "openai"}
    assert body["anthropic"]["ok"] is True
    assert body["openai"]["ok"] is True
    assert len(compiler.calls) == 1
    assert len(reviewer.calls) == 1


def test_test_endpoint_surfaces_provider_error() -> None:
    compiler = _FakeProvider("c1", should_raise=True)
    c = _client(compiler=compiler, reviewer=_FakeProvider("r1"))
    r = c.post(
        "/admin/llm-keys/test",
        headers=HDR_ADMIN,
        json={"provider": "anthropic"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "simulated failure" in body["error"]
    assert body["provider_used"] == "anthropic"


def test_test_endpoint_requires_admin_key() -> None:
    c = _client()
    r = c.post("/admin/llm-keys/test")
    assert r.status_code == 401
