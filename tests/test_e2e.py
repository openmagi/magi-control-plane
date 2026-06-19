"""P7 E2E — full money demo loop across real components:
  cloud /citation_verify (FastAPI in-process)
    → HITL queue (review case)
    → /hitl/approve → cloud signs token
    → local emit caches in WAL
    → local gate (PreToolUse) reads WAL → ALLOW

This is the "production money demo" wired to real components: real cloud
service, real Ed25519 keys, real ledger chain, real HITL queue, real WAL.
The only mocks are the CC hook payload format (we feed JSON directly) and
the law.go.kr resolver (replaced by corpus_override).

Verifies the four critical security properties end-to-end on the *integrated*
system, not just on isolated unit tests:
  1. Fake citation → cloud verdict=deny → no token → local gate DENY
  2. Misquote → cloud verdict=review → HITL enqueued → no token → gate DENY
  3. HITL approve → cloud signs → WAL gets token → gate ALLOW
  4. Doc-swap attempt rejected even with a valid token for a different doc
"""
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.keys import KeyStore
from magi_cp.evidence import Wal


SRC = ("형법 제307조 제1항의 명예훼손죄는 공연히 사실을 적시하여 사람의 사회적 평가를 "
       "저하시킬 만한 구체적 사실을 드러내는 것을 말하고, 적시된 사실이 진실인 경우에도 성립할 수 있다.")
VALID = {"quote": "공연히 사실을 적시하여 사람의 사회적 평가를 저하시킬 만한 구체적 사실을 드러내는 것",
         "ref": "대법원 2018. 9. 13. 선고 2018도13694 판결"}
FAKE = {"quote": "허위", "ref": "대법원 2099. 1. 1. 선고 2099도99999 판결"}
MISQUOTE = {"quote": "명예훼손죄는 허위사실인 경우에만 성립한다", "ref": "2018도13694"}
CORPUS = {"2018도13694": SRC}

API_KEY = "e2e-api-key"
HITL_KEY = "e2e-hitl-key"
HEADERS = {"X-Api-Key": API_KEY}
HITL_HEADERS = {"X-Hitl-Api-Key": HITL_KEY}


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_CP_API_KEY", API_KEY)
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", HITL_KEY)
    monkeypatch.setenv("MAGI_CP_LOCAL_DIR", str(tmp_path / "local"))
    monkeypatch.setenv("MAGI_CP_CLOUD_URL", "http://magi-cp-test")


@pytest.fixture
def cloud(tmp_path):
    ks = KeyStore(dir=str(tmp_path / "keys"))
    app = create_app(keystore=ks, dsn="sqlite:///:memory:")
    return app, TestClient(app)


def _hook_payload(cmd: str) -> dict:
    return {"hook_event_name": "PreToolUse", "tool_input": {"command": cmd}}


def _prime_pubkey_cache(client, tmp_path):
    """Bridge: in real CC, gate fetches /pubkey via HTTP. Tests don't have an
    HTTP server, so we mirror what the gate would cache."""
    pem = client.get("/pubkey").json()["pubkey_pem"]
    local_dir = os.environ["MAGI_CP_LOCAL_DIR"]
    os.makedirs(local_dir, exist_ok=True)
    path = os.path.join(local_dir, "pubkey.pem")
    with open(path, "w") as f:
        f.write(pem)
    os.chmod(path, 0o600)   # match gate's mode policy


def _gate(payload, capsys):
    from magi_cp.local.gate import evaluate
    with pytest.raises(SystemExit) as exc:
        evaluate(payload)
    return capsys.readouterr().out, exc.value.code


def _emit_locally(client, *, matter, doc_id, citations, corpus):
    """Bypass the emit.py HTTP layer (it would need a real socket) — call
    /citation_verify directly via TestClient and append result to WAL."""
    r = client.post("/citation_verify", json={
        "matter": matter, "doc_id": doc_id, "document": "",
        "citations": citations, "corpus_override": corpus,
    }, headers=HEADERS).json()
    if r.get("token"):
        Wal(path=os.path.join(os.environ["MAGI_CP_LOCAL_DIR"], "wal.jsonl")
            ).append({"step": "citation_verify", "token": r["token"]})
    return r


# ── E2E #1: fake citation = deterministic deny end-to-end ───────────
def test_e2e_fake_citation_denied(cloud, tmp_path, capsys):
    app, client = cloud
    _prime_pubkey_cache(client, tmp_path)
    r = _emit_locally(client, matter="M1", doc_id="D1",
                       citations=[VALID, FAKE], corpus=CORPUS)
    assert r["verdict"] == "deny"
    assert r["token"] is None
    out, _ = _gate(_hook_payload("echo FILE_COURT_M1_D1 motion"), capsys)
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


# ── E2E #2: misquote → HITL → approval → token → gate ALLOW ─────────
def test_e2e_misquote_review_approve_allow(cloud, tmp_path, capsys):
    app, client = cloud
    _prime_pubkey_cache(client, tmp_path)
    r = _emit_locally(client, matter="M1", doc_id="D2",
                       citations=[MISQUOTE], corpus=CORPUS)
    assert r["verdict"] == "review"
    assert r["token"] is None
    hitl_id = r["hitl_id"]

    # Gate before approval: deny.
    out, _ = _gate(_hook_payload("echo FILE_COURT_M1_D2 brief"), capsys)
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"

    # Partner approves → cloud signs.
    a = client.post(f"/hitl/{hitl_id}/approve",
                    json={"approver": "partner@firm.example", "note": "ok"},
                    headers=HITL_HEADERS).json()
    assert a["token"]
    Wal(path=os.path.join(os.environ["MAGI_CP_LOCAL_DIR"], "wal.jsonl")
        ).append({"step": "citation_verify", "token": a["token"]})

    # Gate after approval: allow.
    out, _ = _gate(_hook_payload("echo FILE_COURT_M1_D2 brief"), capsys)
    assert out == ""   # silent allow


# ── E2E #3: doc-swap — token for D3 doesn't unlock D4 ───────────────
def test_e2e_doc_swap_blocked(cloud, tmp_path, capsys):
    app, client = cloud
    _prime_pubkey_cache(client, tmp_path)
    r = _emit_locally(client, matter="M1", doc_id="D3",
                       citations=[VALID], corpus=CORPUS)
    assert r["token"]
    out, _ = _gate(_hook_payload("echo FILE_COURT_M1_D4 other"), capsys)
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


# ── E2E #4: ledger chain stays intact across the full flow ──────────
def test_e2e_ledger_chain_remains_ok_after_full_flow(cloud, tmp_path, capsys):
    app, client = cloud
    _prime_pubkey_cache(client, tmp_path)
    _emit_locally(client, matter="M1", doc_id="D5", citations=[VALID], corpus=CORPUS)
    r = _emit_locally(client, matter="M1", doc_id="D6", citations=[MISQUOTE], corpus=CORPUS)
    client.post(f"/hitl/{r['hitl_id']}/approve",
                json={"approver": "p@x.example"}, headers=HITL_HEADERS)
    _emit_locally(client, matter="M1", doc_id="D7", citations=[FAKE], corpus=CORPUS)
    led = client.get("/ledger", headers=HEADERS).json()
    assert led["chain_ok"] is True
    # Expect at least 4 entries: pass(D5) + review(D6) + approve(D6) + deny(D7).
    assert len(led["entries"]) >= 4


# ── E2E #5: NLI advisory annotates HITL payload end-to-end ──────────
def test_e2e_nli_advisory_in_hitl_payload(tmp_path, capsys):
    class _Stub:
        def score(self, q, s):
            return "contradiction", 0.91
    ks = KeyStore(dir=str(tmp_path / "keys"))
    app = create_app(keystore=ks, dsn="sqlite:///:memory:", nli_classifier=_Stub())
    client = TestClient(app)
    _prime_pubkey_cache(client, tmp_path)
    r = client.post("/citation_verify", json={
        "matter": "M1", "doc_id": "DN", "document": "",
        "citations": [MISQUOTE], "corpus_override": CORPUS,
    }, headers=HEADERS).json()
    assert r["verdict"] == "review"
    items = client.get("/hitl", headers=HITL_HEADERS).json()["items"]
    target = next(i for i in items if i["id"] == r["hitl_id"])
    cites = target["payload"]["citations"]
    assert cites[0]["nli_label"] == "contradiction"
    assert cites[0]["nli_score"] == 0.91
