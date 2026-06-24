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
    # D57e P1: keep the (PreToolUse, Bash, block) trigger shape this
    # suite asserts on, but swap citation_verify (Stop-only) for
    # privilege_scan (declares a PreToolUse field_checks group) so
    # the new descriptor-endorsement gate accepts the body. Tests
    # below still assert against "citation_verify" for the catalog
    # built-in row enumeration, but that row is built off the
    # descriptor registry not this policy's requires[].
    base = {
        "id": "legal-filing/v1",
        "description": "t",
        "version": "0.1",
        "trigger": {"host": "claude-code", "event": "PreToolUse", "matcher": "Bash"},
        "sentinel_re": r"FILE_COURT_(?P<matter>[A-Za-z0-9]+)_(?P<doc_id>[A-Za-z0-9]+)",
        "requires": [{"step": "privilege_scan", "verdict": "pass"}],
        "action": "block",
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
    # D57e P1: citation_verify is Stop-only now, so an EvidencePolicy
    # that references it must trigger on Stop. The matcher narrows
    # to wildcard + action narrows to audit per the matrix table.
    body = _valid_policy(
        trigger={"host": "claude-code", "event": "Stop", "matcher": "*"},
        requires=[{"step": "citation_verify", "verdict": "pass"}],
        action="audit",
    )
    _save_policy(client, body)
    items = client.get("/catalog/evidence-types", headers=HDR_API).json()["items"]
    citation = next(i for i in items if i["step"] == "citation_verify")
    assert citation["used_by_policies"] == ["legal-filing/v1"]


def test_evidence_types_surfaces_policy_derived_step_as_missing(client):
    # P8: the bare unwired-step path is now closed at PUT time (422).
    # Authoring an in-development verifier requires the explicit
    # `preview:` opt-in, which lets the policy land in the store with a
    # known-broken step reference. The catalog should still surface
    # that as a "policy-derived" entry so the operator sees what the
    # policy is binding to.
    body = _valid_policy(
        id="custom-step/v1",
        requires=[{"step": "preview:no_such_verifier", "verdict": "pass"}],
    )
    r = _save_policy(client, body)
    assert r.status_code == 200, r.text
    items = client.get("/catalog/evidence-types", headers=HDR_API).json()["items"]
    derived = next(i for i in items if i["step"] == "preview:no_such_verifier")
    assert derived["source"] == "policy-derived"
    assert derived["enforcement"] == "missing"
    assert derived["used_by_policies"] == ["custom-step/v1"]


def test_evidence_types_injects_inline_kinds_for_policies_that_use_them(client):
    """D52c follow-up: a policy using kind=regex / llm_critic / shacl
    in its `requires` produces synthetic `inline_<kind>` catalog rows
    so the chip selector + emissions widget can surface those entries.

    Without this, /verify_inline writes `body['step'] = inline_regex`
    to the ledger but the catalog has no row → the chip selector
    can't filter to it and the rules-tab widget can't count it.
    """
    body = _valid_policy(
        id="inline-regex/v1",
        requires=[
            {"kind": "regex", "pattern": "^FOO_", "verdict": "pass"},
        ],
    )
    r = _save_policy(client, body)
    assert r.status_code == 200, r.text
    items = client.get("/catalog/evidence-types", headers=HDR_API).json()["items"]
    inline = next((i for i in items if i["step"] == "inline_regex"), None)
    assert inline is not None, f"inline_regex not in catalog: {[i['step'] for i in items]}"
    assert inline["source"] == "policy-derived"
    assert inline["enforcement"] == "enforcing"
    assert inline["used_by_policies"] == ["inline-regex/v1"]


def test_evidence_types_skips_inline_kinds_with_no_policy_consumers(client):
    # When no policy uses an inline kind, the synthetic row is NOT
    # emitted (keeps the catalog focused on what's actually live).
    _save_policy(client, _valid_policy())
    items = client.get("/catalog/evidence-types", headers=HDR_API).json()["items"]
    inline_steps = [i["step"] for i in items if i["step"].startswith("inline_")]
    assert inline_steps == []


def test_evidence_types_does_not_emit_empty_step_rows(client):
    # Inline-kind requires (kind=regex etc.) carry step="" in the IR
    # but used_by[""] must not produce a `step=""` policy-derived row
    # (was a dead chip and React key-collision risk; now skipped
    # explicitly at the catalog producer).
    body = _valid_policy(
        id="inline-only/v1",
        requires=[
            {"kind": "llm_critic", "criterion": "no PII", "verdict": "pass"},
        ],
    )
    r = _save_policy(client, body)
    assert r.status_code == 200, r.text
    items = client.get("/catalog/evidence-types", headers=HDR_API).json()["items"]
    assert all(i["step"] for i in items), \
        f"empty-step row leaked: {items}"


def test_put_with_bare_unwired_step_returns_422_no_catalog_pollution(client):
    """P8: a bare (no `preview:` prefix) reference to an unwired step is
    rejected at PUT time so the catalog never has to surface a "missing"
    entry derived from a typo. The closed-loop check: assert the catalog
    stays clean after the failed PUT."""
    body = _valid_policy(
        id="typo/v1",
        requires=[{"step": "no_such_verifier", "verdict": "pass"}],
    )
    r = _save_policy(client, body)
    assert r.status_code == 422, r.text
    items = client.get("/catalog/evidence-types", headers=HDR_API).json()["items"]
    # The bad step name did NOT pollute the policy-derived catalog —
    # PUT rejected before the row could land.
    assert all(i["step"] != "no_such_verifier" for i in items)


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
