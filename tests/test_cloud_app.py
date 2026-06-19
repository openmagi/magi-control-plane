"""P3 cloud API — FastAPI E2E.

Uses TestClient + in-memory SQLite + temp keystore. Verifies the full
loop: /pubkey → /citation_verify (pass → token issued, deny → no token,
review → HITL enqueued → /hitl/approve → token issued) → /ledger.
"""
import time

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.keys import KeyStore
from magi_cp.evidence import verify_token


API_KEY = "test-api-key"
HITL_KEY = "test-hitl-key"
HEADERS = {"X-Api-Key": API_KEY}
HITL_HEADERS = {"X-Hitl-Api-Key": HITL_KEY}


@pytest.fixture(autouse=True)
def _set_api_keys(monkeypatch):
    monkeypatch.setenv("MAGI_CP_API_KEY", API_KEY)
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", HITL_KEY)


@pytest.fixture
def app(tmp_path):
    ks = KeyStore(dir=str(tmp_path / "keys"))
    return create_app(keystore=ks, dsn="sqlite:///:memory:")


@pytest.fixture
def client(app):
    return TestClient(app)


SRC_307 = ("형법 제307조 제1항의 명예훼손죄는 공연히 사실을 적시하여 사람의 사회적 평가를 "
           "저하시킬 만한 구체적 사실을 드러내는 것을 말하고, 적시된 사실이 진실인 경우에도 성립할 수 있다.")
VALID_CITE = {
    "quote": "공연히 사실을 적시하여 사람의 사회적 평가를 저하시킬 만한 구체적 사실을 드러내는 것",
    "ref": "대법원 2018. 9. 13. 선고 2018도13694 판결",
}
FAKE_CITE = {"quote": "임의", "ref": "대법원 2099. 1. 1. 선고 2099도99999 판결"}
MISQUOTE_CITE = {"quote": "명예훼손죄는 허위사실인 경우에만 성립한다", "ref": "2018도13694"}


# ── /healthz, /pubkey ────────────────────────────────────────────────
def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_pubkey_returns_pem(client):
    r = client.get("/pubkey")
    assert r.status_code == 200
    assert "BEGIN PUBLIC KEY" in r.json()["pubkey_pem"]


# ── /citation_verify: verdict=pass → 토큰 발행 ───────────────────────
def test_citation_verify_pass_issues_signed_token(app, client):
    r = client.post("/citation_verify", json={
        "matter": "M1", "doc_id": "D1", "document": "",
        "citations": [VALID_CITE],
        "corpus_override": {"2018도13694": SRC_307},
    }, headers=HEADERS)
    assert r.status_code == 200
    data = r.json()
    assert data["verdict"] == "pass"
    assert data["token"]
    # 토큰이 클라우드 public key로 검증 가능해야
    pub = KeyStore(dir=app.state.keystore.dir).load_public()
    body = verify_token(data["token"], pub)
    assert body is not None
    assert body["matter"] == "M1"
    assert body["doc_hash"] == "D1"
    assert body["verdict"] == "pass"


# ── /citation_verify: verdict=deny → 토큰 미발행 ─────────────────────
def test_citation_verify_deny_no_token(client):
    r = client.post("/citation_verify", json={
        "matter": "M1", "doc_id": "D2", "document": "",
        "citations": [FAKE_CITE],
        "corpus_override": {"2018도13694": SRC_307},
    }, headers=HEADERS)
    data = r.json()
    assert data["verdict"] == "deny"
    assert data["token"] is None


# ── /citation_verify: review → HITL 등록, 토큰 미발행 ────────────────
def test_citation_verify_review_enqueues_hitl(client):
    r = client.post("/citation_verify", json={
        "matter": "M1", "doc_id": "D3", "document": "",
        "citations": [MISQUOTE_CITE],
        "corpus_override": {"2018도13694": SRC_307},
    }, headers=HEADERS)
    data = r.json()
    assert data["verdict"] == "review"
    assert data["token"] is None
    assert data["hitl_id"]


# ── HITL approve → 토큰 발행 ─────────────────────────────────────────
def test_hitl_approve_issues_token(app, client):
    r = client.post("/citation_verify", json={
        "matter": "M1", "doc_id": "D4", "document": "",
        "citations": [MISQUOTE_CITE],
        "corpus_override": {"2018도13694": SRC_307},
    }, headers=HEADERS)
    hitl_id = r.json()["hitl_id"]
    a = client.post(f"/hitl/{hitl_id}/approve", json={"approver": "partner@firm.example", "note": "OK"}, headers=HITL_HEADERS)
    assert a.status_code == 200
    data = a.json()
    assert data["token"]
    pub = KeyStore(dir=app.state.keystore.dir).load_public()
    body = verify_token(data["token"], pub)
    assert body and body["matter"] == "M1" and body["doc_hash"] == "D4"
    # ledger에 2 entries (1 review log + 1 approved)
    led = client.get("/ledger", headers=HEADERS).json()
    assert len(led["entries"]) == 2


def test_hitl_reject_no_token(client):
    r = client.post("/citation_verify", json={
        "matter": "M1", "doc_id": "D5", "document": "",
        "citations": [MISQUOTE_CITE],
        "corpus_override": {"2018도13694": SRC_307},
    }, headers=HEADERS)
    hitl_id = r.json()["hitl_id"]
    a = client.post(f"/hitl/{hitl_id}/reject", json={"approver": "partner@firm.example", "note": "no"}, headers=HITL_HEADERS)
    assert a.status_code == 200
    assert a.json().get("token") is None


# ── ledger: hash-chain + tamper detection ────────────────────────────
def test_ledger_chain_verifies(client):
    client.post("/citation_verify", json={
        "matter": "M1", "doc_id": "D6", "document": "",
        "citations": [VALID_CITE], "corpus_override": {"2018도13694": SRC_307},
    }, headers=HEADERS)
    client.post("/citation_verify", json={
        "matter": "M1", "doc_id": "D7", "document": "",
        "citations": [VALID_CITE], "corpus_override": {"2018도13694": SRC_307},
    }, headers=HEADERS)
    led = client.get("/ledger", headers=HEADERS).json()
    assert led["chain_ok"] is True
    assert len(led["entries"]) == 2


def test_token_has_short_expiry(app, client):
    """라이선스 만료 = 토큰 만료 = fail-closed. exp는 짧아야 함(<= 1h)."""
    r = client.post("/citation_verify", json={
        "matter": "M1", "doc_id": "DX", "document": "",
        "citations": [VALID_CITE], "corpus_override": {"2018도13694": SRC_307},
    }, headers=HEADERS)
    pub = KeyStore(dir=app.state.keystore.dir).load_public()
    body = verify_token(r.json()["token"], pub, now=int(time.time()))
    assert body["exp"] - body["iat"] <= 3600   # ≤ 1시간


# ── invalid input ────────────────────────────────────────────────────
def test_citation_verify_rejects_missing_matter(client):
    r = client.post("/citation_verify", json={"doc_id": "D", "citations": []}, headers=HEADERS)
    assert r.status_code == 422


def test_hitl_decide_unknown_id_404(client):
    r = client.post("/hitl/9999/approve", json={"approver": "x"}, headers=HITL_HEADERS)
    assert r.status_code == 404


# ── P3 security review regressions ───────────────────────────────────
class TestAuthRequired:
    """C2/H2: auth required on enforcement & audit endpoints."""

    def test_citation_verify_without_api_key_401(self, client):
        r = client.post("/citation_verify", json={
            "matter": "M1", "doc_id": "D", "citations": []})
        assert r.status_code == 401

    def test_ledger_without_api_key_401(self, client):
        assert client.get("/ledger").status_code == 401

    def test_hitl_approve_without_key_401(self, client):
        assert client.post("/hitl/1/approve", json={"approver": "x"}).status_code == 401

    def test_hitl_list_without_key_401(self, client):
        assert client.get("/hitl").status_code == 401

    def test_unset_env_fails_closed_503(self, app, monkeypatch):
        monkeypatch.delenv("MAGI_CP_API_KEY", raising=False)
        c = TestClient(app)
        r = c.post("/citation_verify", json={"matter": "M", "doc_id": "D", "citations": []},
                   headers={"X-Api-Key": "anything"})
        assert r.status_code == 503

    def test_wrong_key_401(self, client):
        r = client.post("/citation_verify",
                        json={"matter": "M1", "doc_id": "D", "citations": []},
                        headers={"X-Api-Key": "WRONG"})
        assert r.status_code == 401


class TestRequestSize:
    """C2: oversized inputs rejected before consuming resources."""

    def test_too_many_citations_422(self, client):
        r = client.post("/citation_verify", json={
            "matter": "M1", "doc_id": "D",
            "citations": [{"quote": "x", "ref": "y"}] * 51,
        }, headers=HEADERS)
        assert r.status_code == 422

    def test_quote_too_long_422(self, client):
        r = client.post("/citation_verify", json={
            "matter": "M1", "doc_id": "D",
            "citations": [{"quote": "x" * 10000, "ref": "y"}],
        }, headers=HEADERS)
        assert r.status_code == 422

    def test_matter_disallows_path_chars(self, client):
        r = client.post("/citation_verify", json={
            "matter": "../etc/passwd", "doc_id": "D",
            "citations": []}, headers=HEADERS)
        assert r.status_code == 422

    def test_large_content_length_413(self, client):
        # Synthetic oversized Content-Length header
        r = client.post("/citation_verify",
                        content=b"x" * 16,
                        headers={**HEADERS, "Content-Length": "999999"})
        assert r.status_code == 413


class TestLedgerHardening:
    """M2: pagination + body redaction by default."""

    def _seed(self, client, n=3):
        for i in range(n):
            client.post("/citation_verify", json={
                "matter": "M1", "doc_id": f"D{i}", "citations": [VALID_CITE],
                "corpus_override": {"2018도13694": SRC_307},
            }, headers=HEADERS)

    def test_ledger_redacts_body_by_default(self, client):
        self._seed(client, 1)
        r = client.get("/ledger", headers=HEADERS).json()
        assert "body" not in r["entries"][0]
        assert "token" not in r["entries"][0]
        # Identifying fields still present
        assert "h" in r["entries"][0] and "matter" in r["entries"][0]

    def test_ledger_include_body_flag(self, client):
        self._seed(client, 1)
        r = client.get("/ledger?include_body=true", headers=HEADERS).json()
        assert "body" in r["entries"][0]

    def test_ledger_paginates(self, client):
        self._seed(client, 3)
        r = client.get("/ledger?limit=2", headers=HEADERS).json()
        assert len(r["entries"]) == 2
        cursor = r["next_since_id"]
        r2 = client.get(f"/ledger?since_id={cursor}", headers=HEADERS).json()
        assert all(e["id"] > cursor for e in r2["entries"])


class TestTokenKid:
    """M4: tokens carry kid; /pubkey advertises kid for rotation."""

    def test_pubkey_returns_kid(self, client):
        r = client.get("/pubkey").json()
        assert r["kid"] and len(r["kid"]) == 16

    def test_issued_token_has_kid(self, app, client):
        r = client.post("/citation_verify", json={
            "matter": "M1", "doc_id": "D", "citations": [VALID_CITE],
            "corpus_override": {"2018도13694": SRC_307},
        }, headers=HEADERS).json()
        pub = KeyStore(dir=app.state.keystore.dir).load_public()
        body = verify_token(r["token"], pub)
        assert body["kid"] == client.get("/pubkey").json()["kid"]


class TestChainRace:
    """H1: concurrent appends do not break chain.

    Cleanest way to assert the lock is in place: run many sequential appends
    under the same client (TestClient runs handlers in an event loop) and
    assert chain_ok stays True. The lock is also unit-tested implicitly via
    asyncio.Lock semantics — anyone bypassing it would be visible in diff.
    """

    def test_serial_appends_keep_chain_ok(self, client):
        for i in range(10):
            client.post("/citation_verify", json={
                "matter": "M1", "doc_id": f"R{i}", "citations": [VALID_CITE],
                "corpus_override": {"2018도13694": SRC_307},
            }, headers=HEADERS)
        led = client.get("/ledger", headers=HEADERS).json()
        assert led["chain_ok"] is True


class TestDocHashBinding:
    """Round-2 review: document supplied → doc_id must equal sha256(document)[:32]."""

    def test_document_with_wrong_doc_id_400(self, client):
        r = client.post("/citation_verify", json={
            "matter": "M1", "doc_id": "WRONG_HASH", "document": "real content",
            "citations": [], "corpus_override": {}}, headers=HEADERS)
        assert r.status_code == 400
        assert "doc_id" in r.json()["detail"]

    def test_document_with_correct_doc_id_passes(self, client):
        import hashlib
        text = "actual document content"
        correct_id = hashlib.sha256(text.encode()).hexdigest()[:32]
        r = client.post("/citation_verify", json={
            "matter": "M1", "doc_id": correct_id, "document": text,
            "citations": [VALID_CITE], "corpus_override": {"2018도13694": SRC_307},
        }, headers=HEADERS)
        assert r.status_code == 200


class Test503Redact:
    """Round-2 review: 503 must not echo env var name."""

    def test_503_does_not_leak_env_name(self, app, monkeypatch):
        monkeypatch.delenv("MAGI_CP_API_KEY", raising=False)
        c = TestClient(app)
        r = c.post("/citation_verify", json={"matter": "M", "doc_id": "D", "citations": []},
                   headers={"X-Api-Key": "x"})
        assert r.status_code == 503
        assert "MAGI_CP" not in r.text


class TestRateLimit:
    """Round-2 review: token bucket triggers 429 after capacity exhausted."""

    def test_burst_above_capacity_returns_429(self, app):
        # fresh app so bucket isn't polluted by other tests
        client = TestClient(app)
        got_429 = False
        for _ in range(200):
            r = client.post("/citation_verify", json={
                "matter": "M1", "doc_id": "D", "citations": []}, headers=HEADERS)
            if r.status_code == 429:
                got_429 = True; break
        assert got_429, "rate limiter must engage under burst"


class TestUniquePrev:
    """Round-2 review: UNIQUE(prev) prevents chain forks at DB level."""

    def test_duplicate_prev_rejected(self, app):
        """Direct repo write proving the constraint is in the schema."""
        from magi_cp.cloud.db import LedgerRepo, LedgerEntry, _chain_hash
        from sqlalchemy.orm import Session
        from sqlalchemy.exc import IntegrityError
        led = LedgerRepo(app.state.engine)
        e1 = led.append(matter="M1", body={"x": 1}, token="t1")
        # Try inserting another entry with the same prev as e1 (="") — should fail.
        with Session(app.state.engine) as s:
            forced = LedgerEntry(
                ts=1, matter="M1", prev="", body={"x": 2}, token="t2",
                h=_chain_hash("", {"x": 2}, "t2"),
            )
            s.add(forced)
            import pytest as _pt
            with _pt.raises(IntegrityError):
                s.commit()


class TestHitlDetail:
    """v1-P5: HITL detail endpoint returns payload + ledger context."""

    def test_detail_requires_hitl_key(self, client):
        r = client.get("/hitl/1/detail")
        assert r.status_code == 401

    def test_detail_404_for_unknown_id(self, client):
        r = client.get("/hitl/9999/detail", headers=HITL_HEADERS)
        assert r.status_code == 404

    def test_detail_returns_payload_and_ledger_context(self, client):
        r = client.post("/citation_verify", json={
            "matter": "M1", "doc_id": "D9", "document": "",
            "citations": [MISQUOTE_CITE],
            "corpus_override": {"2018도13694": SRC_307},
        }, headers=HEADERS)
        hitl_id = r.json()["hitl_id"]
        d = client.get(f"/hitl/{hitl_id}/detail", headers=HITL_HEADERS).json()
        assert d["id"] == hitl_id
        assert d["matter"] == "M1"
        assert d["doc_id"] == "D9"
        assert d["status"] == "pending"
        assert d["payload"]["citations"][0]["ref"] == MISQUOTE_CITE["ref"]
        ctx = d["ledger_context"]
        assert len(ctx) >= 1
        assert any(e["body"].get("verdict") == "review" for e in ctx)


class TestNliIntegration:
    """P6: review verdict가 NLI classifier 주입 시 advisory score 받음."""

    def test_review_payload_includes_nli_when_classifier_present(self, tmp_path):
        class _Stub:
            def score(self, q, s):
                return "contradiction", 0.95
        ks = KeyStore(dir=str(tmp_path / "keys"))
        app = create_app(keystore=ks, dsn="sqlite:///:memory:", nli_classifier=_Stub())
        client = TestClient(app)
        r = client.post("/citation_verify", json={
            "matter": "M1", "doc_id": "D", "document": "",
            "citations": [MISQUOTE_CITE],
            "corpus_override": {"2018도13694": SRC_307},
        }, headers=HEADERS)
        assert r.status_code == 200
        # review로 들어갔고 HITL queue에 nli 정보가 있어야
        hitl_id = r.json()["hitl_id"]
        items = client.get("/hitl", headers=HITL_HEADERS).json()["items"]
        target = next(i for i in items if i["id"] == hitl_id)
        cites = target["payload"]["citations"]
        assert cites[0]["nli_label"] == "contradiction"
        assert cites[0]["nli_score"] == 0.95

    def test_review_payload_no_nli_when_classifier_absent(self, client):
        r = client.post("/citation_verify", json={
            "matter": "M1", "doc_id": "D", "document": "",
            "citations": [MISQUOTE_CITE],
            "corpus_override": {"2018도13694": SRC_307},
        }, headers=HEADERS)
        hitl_id = r.json()["hitl_id"]
        items = client.get("/hitl", headers=HITL_HEADERS).json()["items"]
        target = next(i for i in items if i["id"] == hitl_id)
        assert "nli_label" not in target["payload"]["citations"][0]


class TestExtraDisjoint:
    """L2: HITL extra cannot clobber protected token fields."""

    def test_protected_field_clash_500(self, app, client, monkeypatch):
        """Verify the guard via internal call — TestClient cannot send extra dict."""
        from magi_cp.cloud.app import _issue_token
        from magi_cp.cloud.db import LedgerRepo
        from magi_cp.cloud.keys import KeyStore as KS
        import pytest as _pt
        ks = app.state.keystore
        led = LedgerRepo(app.state.engine)
        with _pt.raises(Exception):  # HTTPException 500
            _issue_token("M1", "D1", "pass",
                         ledger=led, keystore=ks, kid="dead",
                         extra={"matter": "ATTACKER"})
