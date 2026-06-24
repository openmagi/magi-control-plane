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

PR4: legacy `matter` / `doc_id` request aliases removed. Only `subject` +
`payload_hash` are accepted; tokens carry the canonical pair only;
HITL surface exposes canonical names only.
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
    # The policy IR still allows arbitrary sentinel_re patterns; the named
    # groups in the sample regex here are illustrative only — the runtime
    # no longer reads specific group names.
    base = {
        "id": "legal-filing/v1",
        "description": "v1 e2e policy",
        "version": "0.1",
        "trigger": {"host": "claude-code", "event": "PreToolUse", "matcher": "Bash"},
        "sentinel_re": r"FILE_COURT_(?P<subject>[A-Za-z0-9]+)_(?P<payload_hash>[A-Za-z0-9]+)",
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
    # Issue #1 non-blocking #a: the sha hashes the same bytes
    # `compile_files` writes to disk (json + trailing newline) so the
    # dashboard's compiled_sha256 aligns with the gate's
    # active_policy_digest.
    expected_sha = hashlib.sha256(
        (json.dumps(expected, ensure_ascii=False, indent=2,
                     sort_keys=True) + "\n").encode("utf-8")).hexdigest()
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
        "subject": "S1", "payload_hash": "P1", "document": "",
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
        "subject": "S1", "payload_hash": "PREV", "document": "",
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


# ── PR4: canonical-only subject/payload_hash keying ─────────────────
def test_citation_verify_rejects_legacy_matter_doc_id(client):
    """PR4: callers that still pass `matter`/`doc_id` get a clean 422
    (pydantic extra="forbid") rather than a silent accept under an alias."""
    r = client.post("/citation_verify", json={
        "matter": "M1", "doc_id": "D1", "document": "",
        "citations": [VALID_CITE], "corpus_override": {"2018도13694": SRC_307},
    }, headers=HEADERS)
    assert r.status_code == 422


def test_citation_verify_accepts_canonical_subject_payload_hash(client):
    r = client.post("/citation_verify", json={
        "subject": "S1", "payload_hash": "P1", "document": "",
        "citations": [VALID_CITE], "corpus_override": {"2018도13694": SRC_307},
    }, headers=HEADERS).json()
    assert r["verdict"] == "pass"
    assert r["token"]


def test_citation_verify_token_carries_canonical_only(client):
    """PR4: issued token body carries ONLY subject + payload_hash. The
    legacy mirror fields (matter / doc_hash) are gone from the body."""
    from magi_cp.evidence.tokens import verify_token
    pub_pem = client.get("/pubkey").json()["pubkey_pem"]
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    pub = load_pem_public_key(pub_pem.encode())
    r = client.post("/citation_verify", json={
        "subject": "S2", "payload_hash": "P2", "document": "",
        "citations": [VALID_CITE], "corpus_override": {"2018도13694": SRC_307},
    }, headers=HEADERS).json()
    body = verify_token(r["token"], pub)
    assert body["subject"] == "S2"
    assert body["payload_hash"] == "P2"
    # Legacy mirror fields removed.
    assert "matter" not in body
    assert "doc_hash" not in body


def test_citation_verify_missing_subject_is_422(client):
    r = client.post("/citation_verify", json={
        "payload_hash": "P", "document": "",
        "citations": [VALID_CITE], "corpus_override": {"2018도13694": SRC_307},
    }, headers=HEADERS)
    assert r.status_code == 422


def test_citation_verify_missing_payload_hash_is_422(client):
    r = client.post("/citation_verify", json={
        "subject": "S", "document": "",
        "citations": [VALID_CITE], "corpus_override": {"2018도13694": SRC_307},
    }, headers=HEADERS)
    assert r.status_code == 422


def _client_with_registry(tmp_path):
    from magi_cp.verifier.builtins import register_builtins
    from magi_cp.verifier.protocol import VerifierRegistry
    ks = KeyStore(dir=str(tmp_path / "keys"))
    reg = VerifierRegistry()
    register_builtins(reg)
    app = create_app(keystore=ks, dsn="sqlite:///:memory:",
                     policy_store_path=str(tmp_path / "policies.json"),
                     verifier_registry=reg)
    return TestClient(app)


def test_verify_dispatch_accepts_canonical_keys(tmp_path):
    """The generic /verify/{step} endpoint takes subject/payload_hash."""
    c = _client_with_registry(tmp_path)
    r = c.post(
        "/verify/privilege_scan",
        headers=HEADERS,
        json={"payload": {"text": "clean filing"},
              "subject": "S3", "payload_hash": "P3"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "pass"


def test_verify_dispatch_rejects_legacy_keys(tmp_path):
    """PR4: matter/doc_id are removed from the request schema (422)."""
    c = _client_with_registry(tmp_path)
    r = c.post(
        "/verify/privilege_scan",
        headers=HEADERS,
        json={"payload": {"text": "clean filing"},
              "matter": "M3", "doc_id": "D3"},
    )
    assert r.status_code == 422


def test_verify_inline_accepts_canonical_keys(client):
    r = client.post(
        "/verify_inline",
        headers=HEADERS,
        json={"kind": "regex", "pattern": r"x",
              "payload": {"text": "x"},
              "subject": "S4", "payload_hash": "P4"},
    )
    assert r.status_code == 200
    assert r.json()["verdict"] == "pass"


def test_verify_inline_rejects_legacy_keys(client):
    r = client.post(
        "/verify_inline",
        headers=HEADERS,
        json={"kind": "regex", "pattern": r"x",
              "payload": {"text": "x"},
              "matter": "M4", "doc_id": "D4"},
    )
    assert r.status_code == 422


def test_hitl_detail_surfaces_canonical_only(client):
    """PR4: list + detail responses expose ONLY subject + payload_hash."""
    r = client.post("/citation_verify", json={
        "subject": "S5", "payload_hash": "P5", "document": "",
        "citations": [MISQUOTE_CITE], "corpus_override": {"2018도13694": SRC_307},
    }, headers=HEADERS).json()
    assert r["verdict"] == "review"
    hitl_id = r["hitl_id"]
    items = client.get("/hitl", headers=HITL_HEADERS).json()["items"]
    target = next(i for i in items if i["id"] == hitl_id)
    assert target["subject"] == "S5"
    assert target["payload_hash"] == "P5"
    assert "matter" not in target
    assert "doc_id" not in target
    d = client.get(f"/hitl/{hitl_id}/detail", headers=HITL_HEADERS).json()
    assert d["subject"] == "S5"
    assert d["payload_hash"] == "P5"
    assert "matter" not in d
    assert "doc_id" not in d


def test_ledger_entries_surface_subject_only(client):
    """PR4: /ledger entries carry ONLY canonical `subject`. Legacy
    `matter` is no longer on the wire (the underlying DB column is
    still named `matter` but the surface is canonical)."""
    client.post("/citation_verify", json={
        "subject": "S6", "payload_hash": "P6", "document": "",
        "citations": [VALID_CITE], "corpus_override": {"2018도13694": SRC_307},
    }, headers=HEADERS)
    led = client.get("/ledger?include_body=true", headers=HEADERS).json()
    assert led["entries"]
    e = led["entries"][-1]
    assert e["subject"] == "S6"
    assert "matter" not in e
    # Body carries the canonical pair only.
    body = e["body"]
    assert body["subject"] == "S6"
    assert body["payload_hash"] == "P6"
    assert "matter" not in body
    assert "doc_hash" not in body


def test_synth_subject_and_hash_uses_session_id_when_present():
    """When the caller has a session id, synth uses it for subject so the
    ledger entry threads to that session — payload_hash is sha256 over the
    canonical payload (deterministic)."""
    from magi_cp.cloud.app import _synth_subject_and_hash
    s1, p1 = _synth_subject_and_hash({"a": 1}, session_id="abc")
    s2, p2 = _synth_subject_and_hash({"a": 1}, session_id="abc")
    assert s1 == s2 == "session_abc"
    assert p1 == p2
    assert len(p1) == 32   # sha256 prefix


def test_synth_subject_and_hash_uses_random_subject_when_no_session():
    """Without a session id we mint a one-shot tag so the ledger entry is
    still uniquely keyed."""
    from magi_cp.cloud.app import _synth_subject_and_hash
    s1, _ = _synth_subject_and_hash({"a": 1})
    s2, _ = _synth_subject_and_hash({"a": 1})
    assert s1.startswith("req_") and s2.startswith("req_")
    assert s1 != s2   # nonce differs


def test_synth_subject_strips_unsafe_session_id_chars():
    """A hostile session_id must NOT smuggle bytes into the ledger key."""
    from magi_cp.cloud.app import _synth_subject_and_hash
    import re as _re
    safe_re = _re.compile(r"^[A-Za-z0-9_\-]+$")
    s, _ = _synth_subject_and_hash({"a": 1}, session_id='abc"\n\x00def')
    assert safe_re.match(s)
    assert s == "session_abcdef"
    s, _ = _synth_subject_and_hash({"a": 1}, session_id='":!@#$%^&*()')
    assert s.startswith("req_")
    assert safe_re.match(s)
    s, _ = _synth_subject_and_hash({"a": 1}, session_id="A" * 100_000)
    assert safe_re.match(s)
    assert len(s) <= 64


def test_verify_dispatch_synthesises_when_no_keys_supplied(tmp_path):
    c = _client_with_registry(tmp_path)
    r = c.post(
        "/verify/privilege_scan",
        headers=HEADERS,
        json={"payload": {"text": "clean filing"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "pass"


def test_verify_dispatch_synthesised_subject_threads_into_token_and_ledger(tmp_path):
    """PR4 integration coverage of the synthesis path.

    Unit tests prove `_synth_subject_and_hash` produces deterministic
    output. This integration test proves the route layer actually wires
    that output into the issued token body AND the ledger entry."""
    c = _client_with_registry(tmp_path)
    payload = {"session_id": "sess123", "text": "clean filing"}
    r = c.post(
        "/verify/privilege_scan",
        headers=HEADERS,
        json={"payload": payload},
    )
    assert r.status_code == 200, r.text
    body_resp = r.json()
    assert body_resp["verdict"] == "pass"
    from magi_cp.evidence.tokens import verify_token
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    pub_pem = c.get("/pubkey").json()["pubkey_pem"]
    pub = load_pem_public_key(pub_pem.encode())
    token_body = verify_token(body_resp["token"], pub)
    assert token_body is not None
    assert token_body["subject"] == "session_sess123"
    # PR4: only canonical names in the token body.
    assert "matter" not in token_body
    assert "doc_hash" not in token_body
    import hashlib as _hashlib
    import json as _json
    expected_hash = _hashlib.sha256(_json.dumps(
        payload, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()[:32]
    assert token_body["payload_hash"] == expected_hash
    led = c.get("/ledger?include_body=true", headers=HEADERS).json()
    last = led["entries"][-1]
    assert last["subject"] == "session_sess123"
    assert last["body"]["subject"] == "session_sess123"
    assert last["body"]["payload_hash"] == expected_hash


def test_protected_token_fields_canonical_only():
    """PR4: PROTECTED_TOKEN_FIELDS includes ONLY the canonical pair plus
    the existing verdict/issuance fields. Legacy `matter` / `doc_hash`
    were dropped together with the request-side aliases."""
    from magi_cp.cloud.app import PROTECTED_TOKEN_FIELDS
    assert {"subject", "payload_hash"} <= PROTECTED_TOKEN_FIELDS
    assert {"verdict", "iat", "exp", "issuer", "kid", "step"} <= PROTECTED_TOKEN_FIELDS
    # Negative: legacy names are NOT in the set.
    assert "matter" not in PROTECTED_TOKEN_FIELDS
    assert "doc_hash" not in PROTECTED_TOKEN_FIELDS


@pytest.mark.parametrize("clash_field", ["subject", "payload_hash"])
def test_issue_token_rejects_protected_field_clobber_via_extra(tmp_path, clash_field):
    """Calling `_issue_token(..., extra={protected_field: <atk>})` MUST
    raise an HTTPException(500) "protected field clash: {…}"."""
    from fastapi import HTTPException
    from magi_cp.cloud.app import _issue_token
    from magi_cp.cloud.db import LedgerRepo, init_schema, make_engine
    from magi_cp.cloud.keys import KeyStore
    engine = make_engine("sqlite:///:memory:")
    init_schema(engine)
    ks = KeyStore(dir=str(tmp_path / "keys"))
    ks.ensure_keypair()
    ledger = LedgerRepo(engine)
    with pytest.raises(HTTPException) as exc:
        _issue_token(
            "S", "P", "pass",
            ledger=ledger, keystore=ks, kid="k",
            extra={clash_field: "attacker_value"},
        )
    assert exc.value.status_code == 500
    assert "protected field clash" in str(exc.value.detail)
    assert clash_field in str(exc.value.detail)


def test_verify_dispatch_rejects_subject_over_64_chars(tmp_path):
    """Cap at 64 to match the LedgerEntry column width."""
    c = _client_with_registry(tmp_path)
    r = c.post(
        "/verify/privilege_scan",
        headers=HEADERS,
        json={"payload": {"text": "clean"},
              "subject": "S" * 65, "payload_hash": "P"},
    )
    assert r.status_code == 422


def test_verify_dispatch_rejects_unsafe_charset_in_subject(tmp_path):
    """A subject containing newlines, quotes, or control characters must
    be rejected at the pydantic boundary."""
    c = _client_with_registry(tmp_path)
    bad_subjects = [
        "with newline\nstill",
        'with "quote"',
        "with\x00null",
        "with space",
        "with:colon",
    ]
    for bad in bad_subjects:
        r = c.post(
            "/verify/privilege_scan",
            headers=HEADERS,
            json={"payload": {"text": "x"},
                  "subject": bad, "payload_hash": "P"},
        )
        assert r.status_code == 422, f"expected 422 for subject={bad!r}, got {r.status_code}"


def test_gate_finds_token_issued_with_canonical_keys(tmp_path, capsys, monkeypatch):
    """Round-trip: cloud issues a token with canonical keys, local gate
    accepts it. The sentinel regex captures (subject, payload_hash) and
    the gate matches on those token-body fields."""
    import os
    from fastapi.testclient import TestClient
    from magi_cp.cloud.app import create_app
    from magi_cp.cloud.keys import KeyStore
    from magi_cp.evidence import Wal
    monkeypatch.setenv("MAGI_CP_API_KEY", API_KEY)
    monkeypatch.setenv("MAGI_CP_LOCAL_DIR", str(tmp_path / "local"))
    monkeypatch.setenv("MAGI_CP_CLOUD_URL", "http://magi-cp-test")
    ks = KeyStore(dir=str(tmp_path / "keys"))
    app = create_app(keystore=ks, dsn="sqlite:///:memory:")
    client = TestClient(app)
    pem = client.get("/pubkey").json()["pubkey_pem"]
    local_dir = str(tmp_path / "local")
    os.makedirs(local_dir, exist_ok=True)
    pkpath = os.path.join(local_dir, "pubkey.pem")
    with open(pkpath, "w") as f:
        f.write(pem)
    os.chmod(pkpath, 0o600)
    r = client.post("/citation_verify", json={
        "subject": "M9", "payload_hash": "D9", "document": "",
        "citations": [VALID_CITE], "corpus_override": {"2018도13694": SRC_307},
    }, headers=HEADERS).json()
    assert r["token"]
    Wal(path=os.path.join(local_dir, "wal.jsonl")).append(
        {"step": "citation_verify", "token": r["token"]}
    )
    from magi_cp.local.gate import evaluate
    payload = {"hook_event_name": "PreToolUse",
                "tool_input": {"command": "echo FILE_COURT_M9_D9 motion"}}
    import pytest as _pytest
    with _pytest.raises(SystemExit) as exc:
        evaluate(payload)
    out = capsys.readouterr().out
    assert exc.value.code == 0
    assert out == ""
