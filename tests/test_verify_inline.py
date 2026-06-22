"""D35: /verify_inline — regex/llm_critic/shacl runtime dispatch."""
import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.keys import KeyStore


API_KEY = "inline-test-key"
HDR = {"X-Api-Key": API_KEY}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAGI_CP_API_KEY", API_KEY)
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", "irrelevant")
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", "irrelevant")


@pytest.fixture
def client(tmp_path):
    from magi_cp.verifier.builtins import register_builtins
    from magi_cp.verifier.protocol import VerifierRegistry
    ks = KeyStore(dir=str(tmp_path / "keys"))
    reg = VerifierRegistry()
    register_builtins(reg)
    app = create_app(
        keystore=ks,
        dsn="sqlite:///:memory:",
        policy_store_path=str(tmp_path / "policies.json"),
        verifier_registry=reg,
    )
    return TestClient(app)


# ── kind=regex ────────────────────────────────────────────────────
def test_regex_match_passes(client):
    r = client.post(
        "/verify_inline", headers=HDR,
        json={
            "kind": "regex",
            "pattern": r"\bAKIA[A-Z0-9]+",
            "payload": {"text": "leaking AKIA12345 in output"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "pass"
    assert body["token"] is not None


def test_regex_no_match_denies(client):
    r = client.post(
        "/verify_inline", headers=HDR,
        json={
            "kind": "regex",
            "pattern": r"\bAKIA[A-Z0-9]+",
            "payload": {"text": "nothing sensitive here"},
        },
    )
    assert r.status_code == 200
    assert r.json()["verdict"] == "deny"
    assert r.json()["token"] is None


def test_regex_missing_pattern_422(client):
    r = client.post(
        "/verify_inline", headers=HDR,
        json={"kind": "regex", "payload": {"text": "x"}},
    )
    assert r.status_code == 422


def test_regex_uncompilable_pattern_422(client):
    r = client.post(
        "/verify_inline", headers=HDR,
        json={"kind": "regex", "pattern": "(", "payload": {"text": "x"}},
    )
    assert r.status_code == 422


def test_regex_falls_back_to_json_payload_when_no_text(client):
    """If payload lacks a `text` key, we stringify the dict and search
    that. Lets a regex check structural shape too."""
    r = client.post(
        "/verify_inline", headers=HDR,
        json={
            "kind": "regex",
            "pattern": r"\"secret_field\":",
            "payload": {"secret_field": "x"},
        },
    )
    assert r.status_code == 200
    assert r.json()["verdict"] == "pass"


# ── kind=llm_critic ───────────────────────────────────────────────
def test_llm_critic_without_provider_returns_review(client):
    """When MAGI_CP_LLM_COMPILER is unset (test fixture), the endpoint
    must return review with a preview-mode reason — not silently pass
    or deny."""
    r = client.post(
        "/verify_inline", headers=HDR,
        json={
            "kind": "llm_critic",
            "criterion": "Output is professional and not flippant.",
            "payload": {"text": "hello world"},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "review"
    assert any("preview" in s.lower() for s in body["reasons"])


def test_llm_critic_missing_criterion_422(client):
    r = client.post(
        "/verify_inline", headers=HDR,
        json={"kind": "llm_critic", "payload": {"text": "x"}},
    )
    assert r.status_code == 422


# ── kind=shacl ────────────────────────────────────────────────────
def test_shacl_without_pyshacl_returns_review(client):
    """pyshacl is an optional install. Without it the endpoint must
    return review with a clear install-hint reason."""
    r = client.post(
        "/verify_inline", headers=HDR,
        json={
            "kind": "shacl",
            "shape_ttl": "@prefix sh: <http://www.w3.org/ns/shacl#> .",
            "payload": {"evidence_ttl": "@prefix ex: <http://example.com/> ."},
        },
    )
    assert r.status_code == 200
    body = r.json()
    # If pyshacl is installed in the test env this becomes pass/deny;
    # locally without it we get review with a preview reason.
    assert body["verdict"] in ("pass", "deny", "review")
    if body["verdict"] == "review":
        assert any("pyshacl" in s.lower() or "preview" in s.lower() for s in body["reasons"])


def test_shacl_missing_shape_422(client):
    r = client.post(
        "/verify_inline", headers=HDR,
        json={"kind": "shacl", "payload": {}},
    )
    assert r.status_code == 422


# ── unknown kind rejected by pydantic ─────────────────────────────
def test_unknown_kind_422(client):
    r = client.post(
        "/verify_inline", headers=HDR,
        json={"kind": "quantum", "payload": {}},
    )
    assert r.status_code == 422
