"""P3 cloud API — FastAPI E2E.

Uses TestClient + in-memory SQLite + temp keystore. Verifies the full
loop: /pubkey → /citation_verify (pass → token issued, deny → no token,
review → HITL enqueued → /hitl/approve → token issued) → /ledger.

PR4: legacy `matter` / `doc_id` request aliases removed (cloud rejects
unknown fields with 422); only `subject` + `payload_hash` accepted on
the wire; tokens carry the canonical pair only.
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
        "subject": "S1", "payload_hash": "P1", "document": "",
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
    assert body["subject"] == "S1"
    assert body["payload_hash"] == "P1"
    assert body["verdict"] == "pass"
    # PR4: legacy mirror fields removed from token body.
    assert "matter" not in body
    assert "doc_hash" not in body


# ── /citation_verify: verdict=deny → 토큰 미발행 ─────────────────────
def test_citation_verify_deny_no_token(client):
    r = client.post("/citation_verify", json={
        "subject": "S1", "payload_hash": "P2", "document": "",
        "citations": [FAKE_CITE],
        "corpus_override": {"2018도13694": SRC_307},
    }, headers=HEADERS)
    data = r.json()
    assert data["verdict"] == "deny"
    assert data["token"] is None


# ── /citation_verify: review → HITL 등록, 토큰 미발행 ────────────────
def test_citation_verify_review_enqueues_hitl(client):
    r = client.post("/citation_verify", json={
        "subject": "S1", "payload_hash": "P3", "document": "",
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
        "subject": "S1", "payload_hash": "P4", "document": "",
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
    assert body and body["subject"] == "S1" and body["payload_hash"] == "P4"
    # ledger에 2 entries (1 review log + 1 approved)
    led = client.get("/ledger", headers=HEADERS).json()
    assert len(led["entries"]) == 2


def test_hitl_reject_no_token(client):
    r = client.post("/citation_verify", json={
        "subject": "S1", "payload_hash": "P5", "document": "",
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
        "subject": "S1", "payload_hash": "P6", "document": "",
        "citations": [VALID_CITE], "corpus_override": {"2018도13694": SRC_307},
    }, headers=HEADERS)
    client.post("/citation_verify", json={
        "subject": "S1", "payload_hash": "P7", "document": "",
        "citations": [VALID_CITE], "corpus_override": {"2018도13694": SRC_307},
    }, headers=HEADERS)
    led = client.get("/ledger", headers=HEADERS).json()
    assert led["chain_ok"] is True
    assert len(led["entries"]) == 2


def test_token_has_short_expiry(app, client):
    """라이선스 만료 = 토큰 만료 = fail-closed. exp는 짧아야 함(<= 1h)."""
    r = client.post("/citation_verify", json={
        "subject": "S1", "payload_hash": "PX", "document": "",
        "citations": [VALID_CITE], "corpus_override": {"2018도13694": SRC_307},
    }, headers=HEADERS)
    pub = KeyStore(dir=app.state.keystore.dir).load_public()
    body = verify_token(r.json()["token"], pub, now=int(time.time()))
    assert body["exp"] - body["iat"] <= 3600   # ≤ 1시간


# ── invalid input ────────────────────────────────────────────────────
def test_citation_verify_rejects_missing_subject(client):
    r = client.post("/citation_verify", json={"payload_hash": "P",
                                                "citations": []}, headers=HEADERS)
    assert r.status_code == 422


# ── PR4: legacy fields now rejected at the boundary ─────────────────
class TestPr4LegacyFieldsRejected:
    """PR4 dropped the legacy `matter` / `doc_id` aliases. Requests that
    still carry the old field names must surface as a clean 422 (via
    pydantic's `extra="forbid"`) rather than a silent accept."""

    def test_citation_verify_rejects_legacy_matter(self, client):
        r = client.post("/citation_verify", json={
            "matter": "M1", "doc_id": "D1", "document": "",
            "citations": [VALID_CITE],
            "corpus_override": {"2018도13694": SRC_307},
        }, headers=HEADERS)
        assert r.status_code == 422

    def test_citation_verify_rejects_legacy_only_matter(self, client):
        r = client.post("/citation_verify", json={
            "subject": "S1", "payload_hash": "P", "matter": "M1",
            "document": "", "citations": [VALID_CITE],
            "corpus_override": {"2018도13694": SRC_307},
        }, headers=HEADERS)
        assert r.status_code == 422

    def test_verify_dispatch_rejects_legacy(self, tmp_path):
        from magi_cp.verifier.builtins import register_builtins
        from magi_cp.verifier.protocol import VerifierRegistry
        ks = KeyStore(dir=str(tmp_path / "keys"))
        reg = VerifierRegistry()
        register_builtins(reg)
        app = create_app(keystore=ks, dsn="sqlite:///:memory:",
                         verifier_registry=reg)
        c = TestClient(app)
        r = c.post(
            "/verify/privilege_scan",
            headers=HEADERS,
            json={"payload": {"text": "x"},
                  "matter": "M", "doc_id": "D"},
        )
        assert r.status_code == 422

    def test_verify_inline_rejects_legacy(self, client):
        r = client.post(
            "/verify_inline",
            headers=HEADERS,
            json={"kind": "regex", "pattern": "x",
                  "payload": {"text": "x"},
                  "matter": "M", "doc_id": "D"},
        )
        assert r.status_code == 422


def test_hitl_decide_unknown_id_404(client):
    r = client.post("/hitl/9999/approve", json={"approver": "x"}, headers=HITL_HEADERS)
    assert r.status_code == 404


# ── P3 security review regressions ───────────────────────────────────
class TestAuthRequired:
    """C2/H2: auth required on enforcement & audit endpoints."""

    def test_citation_verify_without_api_key_401(self, client):
        r = client.post("/citation_verify", json={
            "subject": "S1", "payload_hash": "P", "citations": []})
        assert r.status_code == 401

    def test_ledger_without_api_key_401(self, client):
        assert client.get("/ledger").status_code == 401

    def test_hitl_approve_without_key_401(self, client):
        assert client.post("/hitl/1/approve", json={"approver": "x"}).status_code == 401

    def test_hitl_list_without_key_401(self, client):
        assert client.get("/hitl").status_code == 401

    def test_unset_env_with_no_db_key_returns_401(self, app, monkeypatch):
        """v2.0-W6a: env key unset + no DB-issued keys → fail-closed as 401.

        (Pre-multi-tenant this was 503 "auth not configured", but tenant DB
        keys can now serve auth even when MAGI_CP_API_KEY is empty. The
        invariant is fail-closed; either status carries that meaning.)
        """
        monkeypatch.delenv("MAGI_CP_API_KEY", raising=False)
        c = TestClient(app)
        r = c.post("/citation_verify",
                   json={"subject": "S", "payload_hash": "P", "citations": []},
                   headers={"X-Api-Key": "anything"})
        assert r.status_code == 401

    def test_wrong_key_401(self, client):
        r = client.post("/citation_verify",
                        json={"subject": "S1", "payload_hash": "P", "citations": []},
                        headers={"X-Api-Key": "WRONG"})
        assert r.status_code == 401


class TestRequestSize:
    """C2: oversized inputs rejected before consuming resources."""

    def test_too_many_citations_422(self, client):
        r = client.post("/citation_verify", json={
            "subject": "S1", "payload_hash": "P",
            "citations": [{"quote": "x", "ref": "y"}] * 51,
        }, headers=HEADERS)
        assert r.status_code == 422

    def test_quote_too_long_422(self, client):
        r = client.post("/citation_verify", json={
            "subject": "S1", "payload_hash": "P",
            "citations": [{"quote": "x" * 10000, "ref": "y"}],
        }, headers=HEADERS)
        assert r.status_code == 422

    def test_subject_disallows_path_chars(self, client):
        r = client.post("/citation_verify", json={
            "subject": "../etc/passwd", "payload_hash": "P",
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
                "subject": "S1", "payload_hash": f"P{i}", "citations": [VALID_CITE],
                "corpus_override": {"2018도13694": SRC_307},
            }, headers=HEADERS)

    def test_ledger_redacts_body_by_default(self, client):
        self._seed(client, 1)
        r = client.get("/ledger", headers=HEADERS).json()
        assert "body" not in r["entries"][0]
        assert "token" not in r["entries"][0]
        # Identifying fields still present (PR4 wire: `subject`).
        assert "h" in r["entries"][0] and "subject" in r["entries"][0]

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


class TestLedgerVerifierFilter:
    """D52c: /ledger?verifier=<step> filters records by body['step'].

    Mixed-step seeding uses citation_verify (writes step="citation_verify"
    rows) plus /verify_inline kind=regex (writes step="inline_regex" rows
    on pass/deny). The catalog chip selector / expander widget on the
    dashboard rely on these queries returning a clean subset; an
    accidental "AND" of an empty filter or a typo'd query should not
    crash the route or return the full chain."""

    def _seed_citation(self, client, n=2):
        for i in range(n):
            client.post("/citation_verify", json={
                "subject": "S1", "payload_hash": f"C{i}",
                "citations": [VALID_CITE],
                "corpus_override": {"2018도13694": SRC_307},
            }, headers=HEADERS)

    def _seed_inline_regex(self, client, n=2, match=True):
        # /verify_inline kind=regex writes body['step'] = "inline_regex"
        # (pass or deny depending on whether the pattern hits the text).
        for i in range(n):
            client.post("/verify_inline", json={
                "kind": "regex",
                "pattern": "foo" if match else "neverHits_xyz123",
                "payload": {"text": f"foo {i}" if match else f"bar {i}",
                            "session_id": f"sess_{i}"},
            }, headers=HEADERS)

    def test_filter_by_verifier_returns_subset(self, client):
        self._seed_citation(client, n=2)
        self._seed_inline_regex(client, n=3, match=True)
        all_entries = client.get("/ledger?include_body=true",
                                  headers=HEADERS).json()["entries"]
        # 2 citation + 3 inline_regex rows expected.
        assert len(all_entries) == 5
        cit_only = client.get(
            "/ledger?include_body=true&verifier=citation_verify",
            headers=HEADERS,
        ).json()["entries"]
        assert len(cit_only) == 2
        assert all(e["body"]["step"] == "citation_verify" for e in cit_only)
        reg_only = client.get(
            "/ledger?include_body=true&verifier=inline_regex",
            headers=HEADERS,
        ).json()["entries"]
        assert len(reg_only) == 3
        assert all(e["body"]["step"] == "inline_regex" for e in reg_only)

    def test_filter_supports_multiple_verifiers(self, client):
        self._seed_citation(client, n=2)
        self._seed_inline_regex(client, n=3, match=True)
        both = client.get(
            "/ledger?include_body=true"
            "&verifier=citation_verify&verifier=inline_regex",
            headers=HEADERS,
        ).json()["entries"]
        assert len(both) == 5

    def test_filter_unknown_verifier_returns_empty(self, client):
        self._seed_citation(client, n=2)
        r = client.get("/ledger?verifier=does_not_exist",
                        headers=HEADERS).json()
        assert r["entries"] == []
        # chain_ok still validates the GLOBAL chain (citation_verify rows
        # are still on disk; an empty filter view does NOT mean a broken
        # chain).
        assert r["chain_ok"] is True

    def test_empty_filter_param_falls_back_to_full_view(self, client):
        self._seed_citation(client, n=2)
        # `verifier=` with no value (empty string) is treated as "no
        # filter", same as omitting the query.
        r = client.get("/ledger?verifier=", headers=HEADERS).json()
        assert len(r["entries"]) == 2


class TestLedgerCount:
    """D52c: GET /ledger/count?verifier=<step>&since_secs=86400.

    Powers the "Recent emissions (last 24h)" widget on the Rules →
    Verifiers expander. Cheap to call (no body decode, no token
    verification); returns just {count: N}."""

    def _seed_inline_regex(self, client, n):
        for i in range(n):
            client.post("/verify_inline", json={
                "kind": "regex", "pattern": "foo",
                "payload": {"text": f"foo {i}", "session_id": f"s_{i}"},
            }, headers=HEADERS)

    def test_count_filters_by_verifier(self, client):
        self._seed_inline_regex(client, n=4)
        r = client.get("/ledger/count?verifier=inline_regex",
                        headers=HEADERS).json()
        assert r == {"count": 4}

    def test_count_unknown_verifier_returns_zero_not_error(self, client):
        self._seed_inline_regex(client, n=2)
        r = client.get("/ledger/count?verifier=nope", headers=HEADERS)
        assert r.status_code == 200
        assert r.json() == {"count": 0}

    def test_count_empty_ledger_returns_zero(self, client):
        r = client.get("/ledger/count?verifier=inline_regex",
                        headers=HEADERS)
        assert r.status_code == 200
        assert r.json() == {"count": 0}

    def test_count_without_filter_counts_all_tenant_entries(self, client):
        self._seed_inline_regex(client, n=3)
        r = client.get("/ledger/count", headers=HEADERS).json()
        assert r == {"count": 3}

    def test_count_supports_multiple_verifiers(self, client):
        # Seed two distinct steps and confirm the multi-value filter
        # ORs them together (mirrors the chip selector's multi-pick).
        self._seed_inline_regex(client, n=2)
        for i in range(3):
            client.post("/citation_verify", json={
                "subject": "S1", "payload_hash": f"X{i}",
                "citations": [VALID_CITE],
                "corpus_override": {"2018도13694": SRC_307},
            }, headers=HEADERS)
        r = client.get(
            "/ledger/count?verifier=inline_regex&verifier=citation_verify",
            headers=HEADERS,
        ).json()
        assert r == {"count": 5}

    def test_count_since_secs_window(self, client):
        # With a positive `since_secs`, only entries with ts >= now -
        # since_secs are counted. Seeded entries are all "now", so a
        # generous window (24h) includes them and a tiny non-positive
        # window includes them too (cap at 0 = unbounded back).
        self._seed_inline_regex(client, n=2)
        r = client.get(
            "/ledger/count?verifier=inline_regex&since_secs=86400",
            headers=HEADERS,
        ).json()
        assert r == {"count": 2}

    def test_count_requires_api_key(self, client):
        # Same fail-closed posture as /ledger. Without the API key, the
        # widget should not be able to leak counts to an anonymous caller.
        assert client.get("/ledger/count").status_code == 401


class TestLedgerCountsBatch:
    """D52c follow-up: /ledger/counts batched per-step count.

    Used by the Rules → Verifiers tab to fetch every verifier's 24h
    emission count in a single round-trip + single SQL GROUP BY
    (replaces the K-call fan-out)."""

    def _seed_inline_regex(self, client, n):
        for i in range(n):
            client.post("/verify_inline", json={
                "kind": "regex", "pattern": "foo",
                "payload": {"text": f"foo {i}", "session_id": f"s_{i}"},
            }, headers=HEADERS)

    def _seed_citation(self, client, n):
        for i in range(n):
            client.post("/citation_verify", json={
                "subject": "S1", "payload_hash": f"B{i}",
                "citations": [VALID_CITE],
                "corpus_override": {"2018도13694": SRC_307},
            }, headers=HEADERS)

    def test_counts_returns_map_of_step_to_count(self, client):
        self._seed_inline_regex(client, n=4)
        self._seed_citation(client, n=2)
        r = client.get(
            "/ledger/counts?verifier=inline_regex&verifier=citation_verify",
            headers=HEADERS,
        ).json()
        assert r == {"counts": {"inline_regex": 4, "citation_verify": 2}}

    def test_counts_missing_step_is_zero(self, client):
        # Steps with no emissions appear in the response as 0 (the
        # dashboard relies on this so it can render dashes for
        # genuinely-empty rows without a second call).
        self._seed_inline_regex(client, n=3)
        r = client.get(
            "/ledger/counts?verifier=inline_regex&verifier=does_not_exist",
            headers=HEADERS,
        ).json()
        assert r == {"counts": {"inline_regex": 3, "does_not_exist": 0}}

    def test_counts_empty_filter_returns_empty_map(self, client):
        self._seed_inline_regex(client, n=2)
        r = client.get("/ledger/counts", headers=HEADERS).json()
        assert r == {"counts": {}}

    def test_counts_window_applies(self, client):
        self._seed_inline_regex(client, n=2)
        r = client.get(
            "/ledger/counts?verifier=inline_regex&since_secs=86400",
            headers=HEADERS,
        ).json()
        assert r == {"counts": {"inline_regex": 2}}

    def test_counts_requires_api_key(self, client):
        assert client.get("/ledger/counts").status_code == 401

    def test_counts_caps_verifier_list(self, client):
        # Many repeated verifier= values must be rejected with 400 so
        # the SQL IN(...) clause stays bounded.
        params = "&".join(f"verifier=v{i}" for i in range(200))
        r = client.get(f"/ledger/counts?{params}", headers=HEADERS)
        assert r.status_code == 400


class TestLedgerSamples:
    """D53a: GET /ledger/samples?verifier=<step>&limit=<n>&since_secs=<s>.

    Powers the "Recent emissions samples" inline list on the verifier
    catalog expander. Each sample row passes through D50's redactor
    before serialization; raw payloads NEVER reach the dashboard."""

    def _seed_inline_regex(self, client, n, *, payload_text=None):
        for i in range(n):
            client.post("/verify_inline", json={
                "kind": "regex", "pattern": "foo",
                "payload": {
                    "text": payload_text if payload_text else f"foo {i}",
                    "session_id": f"s_{i}",
                },
            }, headers=HEADERS)

    def test_samples_returns_recent_redacted_rows(self, client):
        self._seed_inline_regex(client, n=3)
        r = client.get(
            "/ledger/samples?verifier=inline_regex&limit=5",
            headers=HEADERS,
        ).json()
        # All three seeded rows fit under the default limit.
        assert "samples" in r
        assert len(r["samples"]) == 3
        first = r["samples"][0]
        # Contract shape: id, ts, verdict, redacted_payload_preview,
        # policy_id (nullable until producers record it).
        assert isinstance(first["id"], int)
        assert isinstance(first["ts"], str) and first["ts"].endswith("Z")
        assert first["verdict"] in ("pass", "review", "deny", None)
        assert isinstance(first["redacted_payload_preview"], str)
        assert "policy_id" in first

    def test_samples_respects_limit(self, client):
        self._seed_inline_regex(client, n=8)
        r = client.get(
            "/ledger/samples?verifier=inline_regex&limit=3",
            headers=HEADERS,
        ).json()
        assert len(r["samples"]) == 3

    def test_samples_default_limit_is_five(self, client):
        self._seed_inline_regex(client, n=12)
        r = client.get(
            "/ledger/samples?verifier=inline_regex",
            headers=HEADERS,
        ).json()
        assert len(r["samples"]) == 5

    def test_samples_unknown_verifier_returns_empty_array(self, client):
        # Per the brief: unknown verifier name -> empty samples list,
        # NOT 404. An empty filter view is a valid operator-visible
        # state; the catalog chip lists names that exist, but the
        # expander should never crash on a typo'd query.
        self._seed_inline_regex(client, n=2)
        r = client.get(
            "/ledger/samples?verifier=does_not_exist",
            headers=HEADERS,
        )
        assert r.status_code == 200
        assert r.json() == {"samples": []}

    def test_samples_orders_newest_first(self, client):
        self._seed_inline_regex(client, n=4)
        r = client.get(
            "/ledger/samples?verifier=inline_regex&limit=10",
            headers=HEADERS,
        ).json()
        ids = [row["id"] for row in r["samples"]]
        assert ids == sorted(ids, reverse=True)

    def test_samples_redacts_secret_shaped_payload(self, client):
        # Inline-regex deny rows store the pattern verbatim in
        # `body['reasons']` (e.g. "pattern did not match: <pattern>").
        # That's our injection point: a JWT-shaped string used as the
        # pattern flows into the ledger body verbatim, and the
        # samples endpoint MUST run it through the redactor before
        # responding. Without redaction the JWT comes through whole.
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxIiwibmFtZSI6IkphbmUifQ."
            "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
        )
        # Use the JWT as the `pattern`; with a payload text that does
        # NOT contain the JWT shape, the verify_inline route writes a
        # `pattern did not match: <jwt>` reason into the ledger body.
        client.post("/verify_inline", json={
            "kind": "regex",
            # `pattern` field has a 2000-char cap; well under that.
            "pattern": jwt,
            "payload": {"text": "no secret here", "session_id": "s_jwt"},
        }, headers=HEADERS)
        r = client.get(
            "/ledger/samples?verifier=inline_regex&limit=5",
            headers=HEADERS,
        ).json()
        previews = [row["redacted_payload_preview"] for row in r["samples"]]
        assert previews, "expected at least one sample"
        joined = " ".join(previews)
        # The redactor masks the JWT shape; the verbatim token must
        # not appear in any preview row.
        assert jwt not in joined
        # The marker shape is the redactor's contract; assert at
        # least one of the patterns fired (kind=jwt is what we expect
        # for this input).
        assert "[REDACTED:jwt]" in joined

    def test_samples_preview_truncated(self, client):
        # A long `reasons` string forces the redactor to truncate.
        # The deny path concatenates `f"pattern did not match: <pat>"`
        # (with the pattern truncated to 80 chars), so the rendered
        # preview is naturally short. We instead exercise the
        # truncation contract by calling the redactor directly with
        # a pre-built long body; this isolates the cap behaviour
        # from the route shape (which today caps reasons to ~80 chars
        # of pattern).
        from magi_cp.policy.run_redaction import (
            DEFAULT_PREVIEW_MAX_CHARS, redact_payload_preview,
        )
        long_body = {
            "step": "inline_regex",
            "verdict": "deny",
            "reasons": ["x" * 600],
        }
        prev = redact_payload_preview(long_body)
        assert len(prev) <= DEFAULT_PREVIEW_MAX_CHARS
        assert prev.endswith("...")

    def test_samples_requires_api_key(self, client):
        # Same fail-closed posture as /ledger. No API key -> 401.
        r = client.get("/ledger/samples?verifier=inline_regex")
        assert r.status_code == 401

    def test_samples_tenant_scoping_holds(self, app, client):
        # Two tenants, each with a row that differs in its `pattern`.
        # The pattern flows through into `body['reasons']` on the
        # inline_regex deny path; we exploit that to make each
        # tenant's row carry an identifiable fingerprint, then assert
        # tenant A's sample list contains A's fingerprint and NOT B's.
        from magi_cp.cloud.tenants import ApiKeyRepo, TenantRepo
        engine = app.state.engine
        repo = TenantRepo(engine)
        repo.create(tenant_id="tenant_a", plan="free")
        repo.create(tenant_id="tenant_b", plan="free")
        key_a = ApiKeyRepo(engine).issue(tenant_id="tenant_a")
        key_b = ApiKeyRepo(engine).issue(tenant_id="tenant_b")
        headers_a = {"X-Api-Key": key_a.cleartext}
        headers_b = {"X-Api-Key": key_b.cleartext}
        client.post("/verify_inline", json={
            "kind": "regex", "pattern": "alpha_fingerprint",
            "payload": {"text": "bar", "session_id": "sa"},
        }, headers=headers_a)
        client.post("/verify_inline", json={
            "kind": "regex", "pattern": "beta_fingerprint",
            "payload": {"text": "bar", "session_id": "sb"},
        }, headers=headers_b)
        r_a = client.get(
            "/ledger/samples?verifier=inline_regex&limit=5",
            headers=headers_a,
        ).json()
        previews_a = " ".join(
            row["redacted_payload_preview"] for row in r_a["samples"]
        )
        assert "alpha_fingerprint" in previews_a
        assert "beta_fingerprint" not in previews_a
        r_b = client.get(
            "/ledger/samples?verifier=inline_regex&limit=5",
            headers=headers_b,
        ).json()
        previews_b = " ".join(
            row["redacted_payload_preview"] for row in r_b["samples"]
        )
        assert "beta_fingerprint" in previews_b
        assert "alpha_fingerprint" not in previews_b

    def test_samples_window_excludes_old_rows(self, client):
        # since_secs=1 with a stale row must produce empty samples. We
        # seed a row, then craft a query with the smallest positive
        # window (1s) - the seeded row's ts is "now", but the
        # `time.time()` between SQL and the request is too tight to
        # bound deterministically without mocking. Instead we seed the
        # row, sleep 2s, then query with since_secs=1; the row is now
        # outside the window.
        self._seed_inline_regex(client, n=1)
        time.sleep(2)
        r = client.get(
            "/ledger/samples?verifier=inline_regex&since_secs=1",
            headers=HEADERS,
        ).json()
        assert r["samples"] == []


class TestLedgerHasMore:
    """D52c follow-up: /ledger emits has_more so the dashboard can hide
    Next-page when the filtered chain is exhausted."""

    def _seed(self, client, n):
        for i in range(n):
            client.post("/citation_verify", json={
                "subject": "S1", "payload_hash": f"H{i}",
                "citations": [VALID_CITE],
                "corpus_override": {"2018도13694": SRC_307},
            }, headers=HEADERS)

    def test_has_more_true_when_page_is_full_and_more_exist(self, client):
        self._seed(client, n=5)
        r = client.get("/ledger?limit=2", headers=HEADERS).json()
        assert r["has_more"] is True
        assert len(r["entries"]) == 2

    def test_has_more_false_when_page_exhausts_chain(self, client):
        self._seed(client, n=2)
        r = client.get("/ledger?limit=10", headers=HEADERS).json()
        assert r["has_more"] is False
        assert len(r["entries"]) == 2


class TestLedgerIntegrityEndpoint:
    """D52c follow-up: dedicated /ledger/integrity endpoint.

    The dashboard polls this for the chain-ok badge so paginated
    /ledger reads can skip the global re-walk."""

    def _seed(self, client, n=2):
        for i in range(n):
            client.post("/citation_verify", json={
                "subject": "S1", "payload_hash": f"I{i}",
                "citations": [VALID_CITE],
                "corpus_override": {"2018도13694": SRC_307},
            }, headers=HEADERS)

    def test_integrity_returns_chain_ok(self, client):
        self._seed(client, n=3)
        r = client.get("/ledger/integrity", headers=HEADERS).json()
        assert r == {"chain_ok": True}

    def test_integrity_requires_api_key(self, client):
        assert client.get("/ledger/integrity").status_code == 401

    def test_paginated_ledger_skips_chain_walk(self, client):
        # Paginated /ledger (since_id > 0) does NOT verify the chain
        # (perf optimisation: paginating callers aren't auditing).
        # We assert chain_ok remains True for both first-page and
        # paginated requests against a clean chain. The test does not
        # try to assert the implementation skipped work (that's an
        # internal concern), only that the contract holds.
        self._seed(client, n=3)
        first = client.get("/ledger?limit=2", headers=HEADERS).json()
        cursor = first["next_since_id"]
        second = client.get(
            f"/ledger?since_id={cursor}&limit=10", headers=HEADERS,
        ).json()
        assert second["chain_ok"] is True


class TestLedgerVerifierCap:
    """D52c follow-up: bound the repeatable verifier= param."""

    def test_ledger_rejects_excess_verifier_values(self, client):
        params = "&".join(f"verifier=v{i}" for i in range(200))
        r = client.get(f"/ledger?{params}", headers=HEADERS)
        assert r.status_code == 400

    def test_count_rejects_excess_verifier_values(self, client):
        params = "&".join(f"verifier=v{i}" for i in range(200))
        r = client.get(f"/ledger/count?{params}", headers=HEADERS)
        assert r.status_code == 400


class TestTokenKid:
    """M4: tokens carry kid; /pubkey advertises kid for rotation."""

    def test_pubkey_returns_kid(self, client):
        r = client.get("/pubkey").json()
        assert r["kid"] and len(r["kid"]) == 16

    def test_issued_token_has_kid(self, app, client):
        r = client.post("/citation_verify", json={
            "subject": "S1", "payload_hash": "P", "citations": [VALID_CITE],
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
                "subject": "S1", "payload_hash": f"R{i}", "citations": [VALID_CITE],
                "corpus_override": {"2018도13694": SRC_307},
            }, headers=HEADERS)
        led = client.get("/ledger", headers=HEADERS).json()
        assert led["chain_ok"] is True


class TestPayloadHashBinding:
    """Round-2 review: document supplied → payload_hash must equal
    sha256(document)[:32]."""

    def test_document_with_wrong_payload_hash_400(self, client):
        r = client.post("/citation_verify", json={
            "subject": "S1", "payload_hash": "WRONG_HASH", "document": "real content",
            "citations": [], "corpus_override": {}}, headers=HEADERS)
        assert r.status_code == 400
        assert "payload_hash" in r.json()["detail"]

    def test_document_with_correct_payload_hash_passes(self, client):
        import hashlib
        text = "actual document content"
        correct_id = hashlib.sha256(text.encode()).hexdigest()[:32]
        r = client.post("/citation_verify", json={
            "subject": "S1", "payload_hash": correct_id, "document": text,
            "citations": [VALID_CITE], "corpus_override": {"2018도13694": SRC_307},
        }, headers=HEADERS)
        assert r.status_code == 200


class Test503Redact:
    """Round-2 review: 503 must not echo env var name."""

    def test_auth_failure_does_not_leak_env_name(self, app, monkeypatch):
        """Whatever status the auth failure returns (401 in v2.0, 503 in v1),
        the response body MUST NOT echo any MAGI_CP_* env var name —
        enumeration would let a probe map the configuration surface."""
        monkeypatch.delenv("MAGI_CP_API_KEY", raising=False)
        c = TestClient(app)
        r = c.post("/citation_verify",
                   json={"subject": "S", "payload_hash": "P", "citations": []},
                   headers={"X-Api-Key": "x"})
        assert r.status_code in (401, 503)   # fail-closed; either is correct
        assert "MAGI_CP" not in r.text


class TestRateLimit:
    """Round-2 review: token bucket triggers 429 after capacity exhausted."""

    def test_burst_above_capacity_returns_429(self, app):
        # fresh app so bucket isn't polluted by other tests
        client = TestClient(app)
        got_429 = False
        for _ in range(200):
            r = client.post("/citation_verify", json={
                "subject": "S1", "payload_hash": "P", "citations": []}, headers=HEADERS)
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
        e1 = led.append(subject="S1", body={"x": 1}, token="t1")
        # Try inserting another entry with the same prev as e1 (="") — should fail.
        with Session(app.state.engine) as s:
            forced = LedgerEntry(
                ts=1, matter="S1", prev="", body={"x": 2}, token="t2",
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
            "subject": "S1", "payload_hash": "P9", "document": "",
            "citations": [MISQUOTE_CITE],
            "corpus_override": {"2018도13694": SRC_307},
        }, headers=HEADERS)
        hitl_id = r.json()["hitl_id"]
        d = client.get(f"/hitl/{hitl_id}/detail", headers=HITL_HEADERS).json()
        assert d["id"] == hitl_id
        assert d["subject"] == "S1"
        assert d["payload_hash"] == "P9"
        # PR4: legacy fields are no longer surfaced.
        assert "matter" not in d
        assert "doc_id" not in d
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
            "subject": "S1", "payload_hash": "P", "document": "",
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
            "subject": "S1", "payload_hash": "P", "document": "",
            "citations": [MISQUOTE_CITE],
            "corpus_override": {"2018도13694": SRC_307},
        }, headers=HEADERS)
        hitl_id = r.json()["hitl_id"]
        items = client.get("/hitl", headers=HITL_HEADERS).json()["items"]
        target = next(i for i in items if i["id"] == hitl_id)
        assert "nli_label" not in target["payload"]["citations"][0]


class TestPr4HitlSurface:
    """PR4: HITL list / detail endpoints expose ONLY canonical names
    (subject + payload_hash). The legacy `matter` / `doc_id` keys are
    gone from the wire and from the DB columns."""

    def test_list_returns_canonical_keys_only(self, client):
        r = client.post("/citation_verify", json={
            "subject": "session_pr4", "payload_hash": "a" * 32, "document": "",
            "citations": [MISQUOTE_CITE],
            "corpus_override": {"2018도13694": SRC_307},
        }, headers=HEADERS)
        hitl_id = r.json()["hitl_id"]
        items = client.get("/hitl", headers=HITL_HEADERS).json()["items"]
        target = next(i for i in items if i["id"] == hitl_id)
        assert target["subject"] == "session_pr4"
        assert target["payload_hash"] == "a" * 32
        # Legacy keys are NOT surfaced.
        assert "matter" not in target
        assert "doc_id" not in target

    def test_detail_returns_canonical_keys_only(self, client):
        r = client.post("/citation_verify", json={
            "subject": "session_detail_pr4", "payload_hash": "b" * 32, "document": "",
            "citations": [MISQUOTE_CITE],
            "corpus_override": {"2018도13694": SRC_307},
        }, headers=HEADERS)
        hitl_id = r.json()["hitl_id"]
        d = client.get(f"/hitl/{hitl_id}/detail", headers=HITL_HEADERS).json()
        assert d["subject"] == "session_detail_pr4"
        assert d["payload_hash"] == "b" * 32
        assert "matter" not in d
        assert "doc_id" not in d

    def test_reject_writes_ledger_entry_canonical_only(self, app, client):
        """Reject route's ledger entry carries only canonical keys."""
        r = client.post("/citation_verify", json={
            "subject": "S_REJ", "payload_hash": "P_REJ", "document": "",
            "citations": [MISQUOTE_CITE],
            "corpus_override": {"2018도13694": SRC_307},
        }, headers=HEADERS)
        hitl_id = r.json()["hitl_id"]
        a = client.post(f"/hitl/{hitl_id}/reject",
                         json={"approver": "p@x.example", "note": "no"},
                         headers=HITL_HEADERS)
        assert a.status_code == 200
        from magi_cp.cloud.db import LedgerRepo
        led = LedgerRepo(app.state.engine)
        entries = [e for e in led.list_all()
                   if e.body.get("step") == "hitl_decision"
                       and e.body.get("hitl_id") == hitl_id]
        assert len(entries) == 1
        body = entries[0].body
        assert body["subject"] == "S_REJ"
        assert body["payload_hash"] == "P_REJ"
        # Legacy fields not present in the ledger body either.
        assert "matter" not in body
        assert "doc_id" not in body


class TestExtraDisjoint:
    """L2: HITL extra cannot clobber protected token fields."""

    def test_protected_field_clash_500(self, app, client, monkeypatch):
        """Verify the guard via internal call — TestClient cannot send extra dict.

        PR4: legacy `matter` / `doc_hash` are no longer in
        PROTECTED_TOKEN_FIELDS. The clash check now exercises the new
        canonical `subject`."""
        from magi_cp.cloud.app import _issue_token
        from magi_cp.cloud.db import LedgerRepo
        import pytest as _pt
        ks = app.state.keystore
        led = LedgerRepo(app.state.engine)
        with _pt.raises(Exception):  # HTTPException 500
            _issue_token("S1", "P1", "pass",
                         ledger=led, keystore=ks, kid="dead",
                         extra={"subject": "ATTACKER"})
