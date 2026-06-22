"""v1-P6 — v1 E2E: full policy lifecycle through real components.

Flow:
  1. POST a new policy via PUT /policies/{id} (admin scope)
  2. GET /policies → it appears in the list
  3. GET /policies/{id}/compiled → managed-settings.json shape matches
     what the existing in-tree compiler emits for the same IR (byte-stable)
  4. PATCH /policies/{id}/enabled → disable, list shows disabled
  5. PATCH /policies/{id}/enabled → re-enable
  6. POST /citation_verify (with the policy active) still works as v0 expects
  7. HITL detail endpoint surfaces a review item's payload + ledger context
"""
import hashlib
import json
import os

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.keys import KeyStore
from magi_cp.policy import compile_to_managed_settings, load_policy


API_KEY = "v1e2e-api"
HITL_KEY = "v1e2e-hitl"
ADMIN_KEY = "v1e2e-admin"
HEADERS = {"X-Api-Key": API_KEY}
HITL_HEADERS = {"X-Hitl-Api-Key": HITL_KEY}
ADMIN = {"X-Admin-Api-Key": ADMIN_KEY}


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_CP_API_KEY", API_KEY)
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", HITL_KEY)
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", ADMIN_KEY)


@pytest.fixture
def client(tmp_path):
    ks = KeyStore(dir=str(tmp_path / "keys"))
    app = create_app(keystore=ks, dsn="sqlite:///:memory:",
                     policy_store_path=str(tmp_path / "policies.json"))
    return TestClient(app)


def _valid_policy(**override):
    base = {
        "id": "legal-filing/v1",
        "description": "v1 e2e policy",
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


SRC_307 = ("형법 제307조 제1항의 명예훼손죄는 공연히 사실을 적시하여 사람의 사회적 평가를 "
           "저하시킬 만한 구체적 사실을 드러내는 것을 말하고, 적시된 사실이 진실인 경우에도 성립할 수 있다.")
VALID_CITE = {
    "quote": "공연히 사실을 적시하여 사람의 사회적 평가를 저하시킬 만한 구체적 사실을 드러내는 것",
    "ref": "대법원 2018. 9. 13. 선고 2018도13694 판결",
}
MISQUOTE_CITE = {"quote": "명예훼손죄는 허위사실인 경우에만 성립한다", "ref": "2018도13694"}


# ── 1. Create policy via API ─────────────────────────────────────────
def test_e2e_lifecycle_v1(client, tmp_path):
    pid = "legal-filing/v1"
    body = _valid_policy()

    # PUT
    r = client.put(f"/policies/{pid}",
                   json={"policy": body, "source": "org", "enabled": True},
                   headers=ADMIN)
    assert r.status_code == 200

    # List
    items = client.get("/policies", headers=ADMIN).json()["items"]
    assert any(i["id"] == pid and i["enabled"] for i in items)

    # Compiled — matches the in-tree compiler for the same IR
    compiled = client.get(f"/policies/{pid}/compiled", headers=ADMIN).json()
    policy = load_policy(
        # write the body to disk and roundtrip through load_policy
        str(_dump_policy(body, tmp_path / "ir.json")))
    expected = compile_to_managed_settings([policy])
    assert compiled["managed_settings"] == expected
    expected_sha = hashlib.sha256(
        json.dumps(expected, ensure_ascii=False, indent=2,
                    sort_keys=True).encode("utf-8")).hexdigest()
    assert compiled["sha256"] == expected_sha

    # Disable
    r = client.patch(f"/policies/{pid}/enabled",
                     json={"enabled": False}, headers=ADMIN)
    assert r.status_code == 200
    items = client.get("/policies", headers=ADMIN).json()["items"]
    assert next(i for i in items if i["id"] == pid)["enabled"] is False

    # Re-enable
    client.patch(f"/policies/{pid}/enabled",
                 json={"enabled": True}, headers=ADMIN)

    # /citation_verify still works (orthogonal: gate runs from managed-settings,
    # verifier runs from the cloud)
    r = client.post("/citation_verify", json={
        "matter": "M1", "doc_id": "D1", "document": "",
        "citations": [VALID_CITE], "corpus_override": {"2018도13694": SRC_307},
    }, headers=HEADERS).json()
    assert r["verdict"] == "pass"
    assert r["token"]


def _dump_policy(body: dict, path) -> str:
    path = str(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False)
    return path


# ── 2. HITL detail surface (drill-down) ──────────────────────────────
def test_e2e_hitl_detail_surfaces_why_review(client):
    """The v1-P5 detail page consumes this endpoint."""
    r = client.post("/citation_verify", json={
        "matter": "M1", "doc_id": "DREV", "document": "",
        "citations": [MISQUOTE_CITE], "corpus_override": {"2018도13694": SRC_307},
    }, headers=HEADERS).json()
    assert r["verdict"] == "review"
    hitl_id = r["hitl_id"]
    d = client.get(f"/hitl/{hitl_id}/detail", headers=HITL_HEADERS).json()
    # Citation payload exposes the predicate that caused review
    assert d["payload"]["citations"][0]["status"] in {"review", "missing"}
    assert d["payload"]["citations"][0]["reasons"]
    # Ledger context contains the corresponding review entry
    assert any(e["body"].get("verdict") == "review" for e in d["ledger_context"])


# ── 3. Auth isolation — admin endpoints don't accept hitl/api keys ──
def test_e2e_admin_routes_reject_other_keys(client):
    body = _valid_policy(id="x")
    assert client.put("/policies/x",
                      json={"policy": body, "source": "org", "enabled": True},
                      headers=HEADERS).status_code == 401   # wrong key
    assert client.put("/policies/x",
                      json={"policy": body, "source": "org", "enabled": True},
                      headers=HITL_HEADERS).status_code == 401


# ── 4. Reject illegal matrix combos at the API boundary ──────────────
def test_e2e_api_rejects_illegal_matrix(client):
    # D31: PostToolUse + Bash + block is illegal (post-event can't block).
    body = _valid_policy(
        trigger={"host": "claude-code", "event": "PostToolUse", "matcher": "Bash"},
        action="block",
    )
    r = client.put("/policies/legal-filing/v1",
                   json={"policy": body, "source": "org", "enabled": True},
                   headers=ADMIN)
    assert r.status_code == 400
    assert "illegal" in r.json()["detail"].lower()


# ── 5. Persistence across simulated app restart ──────────────────────
def test_e2e_persistence_across_restart(tmp_path):
    ks = KeyStore(dir=str(tmp_path / "keys"))
    psp = str(tmp_path / "policies.json")
    app1 = create_app(keystore=ks, dsn="sqlite:///:memory:", policy_store_path=psp)
    c1 = TestClient(app1)
    c1.put("/policies/legal-filing/v1",
           json={"policy": _valid_policy(), "source": "org", "enabled": True},
           headers=ADMIN)

    # Restart — fresh app, same store path
    app2 = create_app(keystore=ks, dsn="sqlite:///:memory:", policy_store_path=psp)
    c2 = TestClient(app2)
    items = c2.get("/policies", headers=ADMIN).json()["items"]
    assert any(i["id"] == "legal-filing/v1" for i in items)
