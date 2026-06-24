"""D54: prebuilt policy template catalog.

A prebuilt is a (verifier, event, matcher, action) tuple the operator
REVIEWS in the PolicyBuilder before saving. The 5 templates ship one
per built-in verifier. Tests here pin the contract:

  1. The module exports exactly 5 entries.
  2. Every entry round-trips through policy_from_dict (i.e. the IR
     the dashboard prefill carries actually loads).
  3. Every entry's (event, matcher_class, action) is in the legal
     matrix (a template that can't save through PUT /policies is a
     bug. The operator would hit the matrix at save time and have to
     hand-edit something they didn't author).
  4. Every entry binds a known built-in verifier step.
  5. GET /policies/prebuilt returns the 5 with the same shape.
  6. The D52d field_checks paths-resolve invariant still passes
     after the description rewrites (the assertion runs at
     descriptors.py import time, but we exercise it here to surface
     a regression in CI grep).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.keys import KeyStore
from magi_cp.policy.ir import policy_from_dict
from magi_cp.policy.matrix import LEGAL_COMBINATIONS, matcher_class_of
from magi_cp.policy.prebuilt import all_prebuilt_policies


ADMIN_KEY = "p-admin-key"
ADMIN_HEADERS = {"X-Admin-Api-Key": ADMIN_KEY}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAGI_CP_API_KEY", "p-api-key")
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", "p-hitl-key")
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", ADMIN_KEY)


@pytest.fixture
def client(tmp_path):
    ks = KeyStore(dir=str(tmp_path / "keys"))
    app = create_app(keystore=ks, dsn="sqlite:///:memory:",
                     policy_store_path=str(tmp_path / "policies.json"))
    return TestClient(app)


_EXPECTED_VERIFIER_STEPS: set[str] = {
    "citation_verify",
    "privilege_scan",
    "source_allowlist",
    "structured_output",
    "prompt_injection_screen",
}


def test_returns_five_entries() -> None:
    items = all_prebuilt_policies()
    assert len(items) == 5
    # Stable shape. The dashboard renders these directly so missing
    # keys would crash the Policies tab on render rather than at a
    # later /verify time.
    for p in items:
        assert {"id", "title", "summary", "verifier_step", "ir"} <= set(p.keys())
        assert isinstance(p["id"], str) and p["id"]
        assert isinstance(p["title"], str) and p["title"]
        assert isinstance(p["summary"], str) and p["summary"]
        assert isinstance(p["ir"], dict)


def test_each_entry_covers_one_builtin_verifier() -> None:
    """Every built-in verifier MUST have exactly one prebuilt entry
    paired with it. A missing pairing means the operator landing on
    the Policies tab cannot use the "Use this" shortcut for that
    verifier (a UX regression), and the brief explicitly asks for one
    per verifier."""
    by_step = {p["verifier_step"] for p in all_prebuilt_policies()}
    assert by_step == _EXPECTED_VERIFIER_STEPS


def test_each_entry_ir_validates_through_policy_from_dict() -> None:
    """The prefill the dashboard hands to PolicyBuilder must be a
    valid IR. `policy_from_dict` runs the same loader path that PUT
    /policies uses, so a failure here means the "Use this" button
    would hand the operator a draft that the cloud rejects on save:
    bug, not feature."""
    for p in all_prebuilt_policies():
        policy = policy_from_dict(p["ir"])
        # `description` round-trips so the prefill carries the
        # one-liner into the form's `description` field.
        assert getattr(policy, "description", None) == p["ir"]["description"]


def test_every_entry_in_legal_matrix() -> None:
    """The (event, matcher_class, action) triple every prebuilt
    represents must be in LEGAL_COMBINATIONS. Otherwise the "Use
    this" handoff is a trap: the matrix rejects the combination at
    save time and the operator has to mutate a triple they didn't
    author. The brief explicitly limits the templates to combos the
    matrix supports."""
    for p in all_prebuilt_policies():
        trig = p["ir"]["trigger"]
        kls = matcher_class_of(trig["matcher"])
        triple = (trig["event"], kls, p["ir"]["action"])
        assert triple in LEGAL_COMBINATIONS, (
            f"prebuilt {p['id']} triple {triple} not in legal matrix"
        )


def test_unique_ids_and_titles() -> None:
    """Stable React keys / accessible labels for the dashboard render
    require both id and title to be unique across the catalog."""
    items = all_prebuilt_policies()
    ids = [p["id"] for p in items]
    titles = [p["title"] for p in items]
    assert len(set(ids)) == len(ids)
    assert len(set(titles)) == len(titles)


def test_prebuilt_endpoint_returns_five(client) -> None:
    """GET /policies/prebuilt is the dashboard's data source. Same
    shape as the module function, admin-key gated like the rest of
    the /policies surface."""
    r = client.get("/policies/prebuilt", headers=ADMIN_HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "items" in body
    items = body["items"]
    assert len(items) == 5
    for p in items:
        assert {"id", "title", "summary", "verifier_step", "ir"} <= set(p.keys())
    # Matches the in-process catalog (no drift between request
    # serialization and module).
    in_process = all_prebuilt_policies()
    assert [p["id"] for p in items] == [p["id"] for p in in_process]


def test_prebuilt_endpoint_requires_admin_key(client) -> None:
    r = client.get("/policies/prebuilt")
    assert r.status_code == 401


def test_prebuilt_route_not_swallowed_by_path_catchall(client) -> None:
    """Defensive: `/policies/{policy_id:path}` is registered RIGHT
    AFTER the prebuilt route. If anyone reorders the routes and
    drops the explicit `/policies/prebuilt` declaration above the
    catch-all, a request would 404 with `policy 'prebuilt' not
    found` instead of returning the catalog. Pin the contract so a
    future reshuffle fails CI."""
    r = client.get("/policies/prebuilt", headers=ADMIN_HEADERS)
    # Must NOT be a "policy 'prebuilt' not found" 404 from the
    # catch-all (that would surface as a 404 with body text).
    assert r.status_code == 200
    assert "not found" not in r.text.lower()


def test_descriptors_field_checks_paths_resolve_invariant() -> None:
    """D52d invariant survives the D54 description rewrites.

    Importing magi_cp.verifier.descriptors triggers
    `_assert_field_checks_paths_resolve()` at module load (see the
    end of descriptors.py). Re-import here so a regression that
    leaves the module cached but breaks the invariant on a fresh
    process still surfaces. Asserting `True` after import is the
    pin: the assertion is in the import path."""
    import importlib
    import magi_cp.verifier.descriptors as d
    importlib.reload(d)
    # The reload ran the import-time guards. If we got here, all 3
    # `_assert_*` passed.
    assert d.all_descriptors() != []
