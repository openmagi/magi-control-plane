"""D56e: /checks + /evidence-types routes — pure-derivation catalog
backing the reorganized Rules page (Policies / Checks / Evidence).

Mirrors test_catalog_routes.py style: spin a TestClient with a tmpdir
key store + policy store, register the 5 built-in verifiers, and walk
the response shapes.
"""
import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.keys import KeyStore


API_KEY = "ce-api-key"
HITL_KEY = "ce-hitl-key"
ADMIN_KEY = "ce-admin-key"
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
        "description": "test policy",
        "version": "0.1",
        "trigger": {"host": "claude-code", "event": "PreToolUse", "matcher": "Bash"},
        "sentinel_re": r"FILE_COURT_(?P<matter>[A-Za-z0-9]+)_(?P<doc_id>[A-Za-z0-9]+)",
        "requires": [{"step": "citation_verify", "verdict": "pass"}],
        "action": "block",
        "on_signature_invalid": "deny",
        "gate_binary": "/usr/local/bin/magi-gate.sh",
    }
    base.update(override)
    return base


def _save(client, body, pid=None):
    pid = pid or body["id"]
    return client.put(
        f"/policies/{pid}",
        json={"policy": body, "source": "org", "enabled": True},
        headers=HDR_ADMIN,
    )


# ── /checks ──────────────────────────────────────────────────────────
def test_checks_lists_builtins(client):
    r = client.get("/checks", headers=HDR_API)
    assert r.status_code == 200
    items = r.json()["items"]
    builtin_ids = {row["id"] for row in items if row["kind"] == "builtin"}
    # 5 built-ins from register_builtins().
    assert "citation_verify" in builtin_ids
    assert "privilege_scan" in builtin_ids
    assert "source_allowlist" in builtin_ids
    assert "structured_output" in builtin_ids
    assert "prompt_injection_screen" in builtin_ids


def test_checks_builtin_carries_field_checks_from_descriptor(client):
    items = client.get("/checks", headers=HDR_API).json()["items"]
    by_id = {r["id"]: r for r in items}
    privilege = by_id["privilege_scan"]
    assert privilege["kind"] == "builtin"
    assert privilege["source"] == "built-in"
    paths = {fc["path"] for fc in privilege["field_checks"]}
    # Mirrors the descriptor entries.
    assert "tool_input.command" in paths
    assert "final_message" in paths


def test_checks_inline_regex_row_from_policy(client):
    body = _valid_policy(
        id="p-regex/v1",
        requires=[{"kind": "regex", "pattern": "DROP TABLE"}],
    )
    assert _save(client, body).status_code in (200, 201)
    items = client.get("/checks", headers=HDR_API).json()["items"]
    inline = [r for r in items if r["kind"] == "inline-regex"]
    assert len(inline) == 1
    row = inline[0]
    assert row["source"] == "p-regex/v1"
    assert row["used_by_policies"] == ["p-regex/v1"]
    assert "DROP TABLE" in row["body"]
    # Built-in rows still surface alongside the inline one.
    assert any(r["kind"] == "builtin" for r in items)


def test_checks_inline_llm_critic_and_shacl_rows(client):
    body = _valid_policy(
        id="p-mixed/v1",
        requires=[
            {"kind": "llm_critic", "criterion": "Output cites a real source."},
            {"kind": "shacl", "shape_ttl": "@prefix sh: <http://...> . sh:NodeShape ;"},
        ],
    )
    assert _save(client, body).status_code in (200, 201)
    items = client.get("/checks", headers=HDR_API).json()["items"]
    kinds = {r["kind"] for r in items}
    assert "inline-llm-critic" in kinds
    assert "inline-shacl" in kinds


def test_checks_used_by_policies_stamped_on_builtin(client):
    body = _valid_policy(id="cv-user/v1")
    assert _save(client, body).status_code in (200, 201)
    items = client.get("/checks", headers=HDR_API).json()["items"]
    citation = next(r for r in items if r["id"] == "citation_verify")
    assert "cv-user/v1" in citation["used_by_policies"]


def test_checks_requires_api_auth(client):
    assert client.get("/checks").status_code == 401


# ── /evidence-types (D56e new) ───────────────────────────────────────
def test_evidence_types_lists_builtins(client):
    r = client.get("/evidence-types", headers=HDR_API)
    assert r.status_code == 200
    items = r.json()["items"]
    by_id = {row["id"]: row for row in items}
    assert "citation_verify" in by_id
    assert by_id["citation_verify"]["origin"] == "builtin"
    # Payload schema is non-empty.
    schema = by_id["citation_verify"]["payload_schema"]
    assert isinstance(schema, list) and len(schema) > 0
    # Every entry has the four-key shape.
    for f in schema:
        assert set(f.keys()) >= {"path", "type", "description"}


def test_evidence_types_inline_rows_when_policy_uses_inline_kind(client):
    body = _valid_policy(
        id="p-regex/v1",
        requires=[{"kind": "regex", "pattern": "secret"}],
    )
    assert _save(client, body).status_code in (200, 201)
    items = client.get("/evidence-types", headers=HDR_API).json()["items"]
    inline = [r for r in items if r["id"] == "inline_regex"]
    assert len(inline) == 1
    row = inline[0]
    assert row["origin"] == "inline"
    assert "p-regex/v1" in row["used_by_policies"]
    paths = {f["path"] for f in row["payload_schema"]}
    assert "step" in paths
    assert "verdict" in paths


def test_evidence_types_no_inline_row_when_no_policy_uses_kind(client):
    items = client.get("/evidence-types", headers=HDR_API).json()["items"]
    assert not any(r["id"] == "inline_regex" for r in items)
    assert not any(r["id"] == "inline_llm_critic" for r in items)
    assert not any(r["id"] == "inline_shacl" for r in items)


def test_evidence_types_used_by_policies_for_builtin(client):
    body = _valid_policy(id="cv-user/v1")
    assert _save(client, body).status_code in (200, 201)
    items = client.get("/evidence-types", headers=HDR_API).json()["items"]
    citation = next(r for r in items if r["id"] == "citation_verify")
    assert "cv-user/v1" in citation["used_by_policies"]


def test_evidence_types_requires_api_auth(client):
    assert client.get("/evidence-types").status_code == 401
