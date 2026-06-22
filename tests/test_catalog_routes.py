"""Derived (pure-derivation) catalog: /catalog/evidence-types + /catalog/conditions.

Verifies that the catalog endpoints walk the live policy + verifier
state without any separate storage — no custom_verifiers table, no
authoring endpoints. Entries appear when a policy references them and
disappear when the policy is deleted.
"""
import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.keys import KeyStore


API_KEY = "cat-api-key"
HITL_KEY = "cat-hitl-key"
ADMIN_KEY = "cat-admin-key"
HDR_API = {"X-Api-Key": API_KEY}
HDR_ADMIN = {"X-Admin-Api-Key": ADMIN_KEY}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAGI_CP_API_KEY", API_KEY)
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", HITL_KEY)
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", ADMIN_KEY)


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


def _valid_policy(**override):
    base = {
        "id": "legal-filing/v1",
        "description": "t",
        "version": "0.1",
        "trigger": {"host": "claude-code", "event": "PreToolUse", "matcher": "Bash"},
        "sentinel_re": r"FILE_COURT_(?P<matter>[A-Za-z0-9]+)_(?P<doc_id>[A-Za-z0-9]+)",
        "requires": [{"step": "citation_verify", "verdict": "pass"}],
        "on_missing": "deny",
        "on_signature_invalid": "deny",
        "gate_binary": "/usr/local/bin/magi-gate.sh",
    }
    base.update(override)
    return base


def _save_policy(client, body, pid=None):
    pid = pid or body["id"]
    return client.put(
        f"/policies/{pid}",
        json={"policy": body, "source": "org", "enabled": True},
        headers=HDR_ADMIN,
    )


# ── /catalog/evidence-types ──────────────────────────────────────
def test_evidence_types_lists_builtin_steps(client):
    r = client.get("/catalog/evidence-types", headers=HDR_API)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    steps = [i["step"] for i in items]
    assert "citation_verify" in steps
    for i in items:
        assert i["source"] == "builtin"
        assert i["used_by_policies"] == []


def test_evidence_types_annotates_used_by_when_policy_references_step(client):
    _save_policy(client, _valid_policy())
    items = client.get("/catalog/evidence-types", headers=HDR_API).json()["items"]
    citation = next(i for i in items if i["step"] == "citation_verify")
    assert citation["used_by_policies"] == ["legal-filing/v1"]


def test_evidence_types_surfaces_policy_derived_step_as_missing(client):
    # Build the policy with a non-existent step name. Backend allows
    # this — the runtime denies at /verify time. The catalog should
    # still surface it as a "missing" entry so the operator sees the
    # broken reference.
    body = _valid_policy(
        id="custom-step/v1",
        requires=[{"step": "no_such_verifier", "verdict": "pass"}],
    )
    _save_policy(client, body)
    items = client.get("/catalog/evidence-types", headers=HDR_API).json()["items"]
    derived = next(i for i in items if i["step"] == "no_such_verifier")
    assert derived["source"] == "policy-derived"
    assert derived["enforcement"] == "missing"
    assert derived["used_by_policies"] == ["custom-step/v1"]


# ── /catalog/conditions ──────────────────────────────────────────
def test_conditions_empty_with_no_policies(client):
    r = client.get("/catalog/conditions", headers=HDR_API)
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_conditions_extracts_sentinel_re_and_tool_match_per_policy(client):
    _save_policy(client, _valid_policy())
    items = client.get("/catalog/conditions", headers=HDR_API).json()["items"]
    kinds = sorted({i["kind"] for i in items})
    assert kinds == ["sentinel_re", "tool_match"]
    # Both rows carry the originating policy id so the dashboard can
    # link back to the editable surface.
    for i in items:
        assert i["policy_id"] == "legal-filing/v1"
        assert i["trigger_event"] == "PreToolUse"


def test_conditions_reflect_all_policies_regardless_of_enabled(client):
    """Pure-derivation invariant: catalog walks every stored policy.
    Toggling a policy's enabled flag does not strip its byproducts
    from the catalog (a paused policy is still a reference operators
    may want to inspect or clone)."""
    _save_policy(client, _valid_policy())
    items = client.get("/catalog/conditions", headers=HDR_API).json()["items"]
    assert len(items) == 2
    r = client.patch(
        "/policies/legal-filing/v1/enabled",
        headers=HDR_ADMIN,
        json={"enabled": False},
    )
    assert r.status_code == 200, r.text
    items = client.get("/catalog/conditions", headers=HDR_API).json()["items"]
    assert len(items) == 2


# ── custom_verifiers infra is gone ───────────────────────────────
def test_no_tenants_verifiers_endpoint(client):
    """Pure-derivation pivot — the standalone authoring endpoints
    from the previous iteration must not exist."""
    assert client.get("/tenants/verifiers", headers=HDR_API).status_code == 404
    assert client.post(
        "/tenants/verifiers",
        headers=HDR_API,
        json={"step": "x", "name": "x", "category": "SECURITY",
              "description": "", "kind": "regex",
              "config": {"pattern": ".", "on_match": "deny", "reasons": []},
              "enabled": True},
    ).status_code == 404
