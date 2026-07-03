"""D76: tests for /ledger/aggregate + /metrics/summary + metrics module.

Covers:
  - normalize_aggregate_params clamps + rejects oversized bucket sets
  - ledger_aggregate buckets rows by ts; counts known action/verdict
    pairs into the closed-set buckets and ignores unknowns
  - metrics_summary one-shot aggregator returns the policy / pack /
    script / HITL / ledger totals
  - HTTP surfaces honour tenant auth + the same shape as the helpers
"""
import time

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.db import (
    HitlItem, HitlStatus, LedgerEntry, init_schema, make_engine,
)
from magi_cp.cloud.keys import KeyStore
from magi_cp.cloud.metrics import (
    MIN_BUCKET_SECS, MAX_SINCE_SECS,
    ledger_aggregate, ledger_aggregate_to_dict, metrics_summary,
    metrics_summary_to_dict, normalize_aggregate_params,
)
from magi_cp.cloud.db import LedgerRepo


API_KEY = "test-api-key"
HITL_KEY = "test-hitl-key"
HEADERS = {"X-Api-Key": API_KEY}


@pytest.fixture(autouse=True)
def _set_api_keys(monkeypatch):
    monkeypatch.setenv("MAGI_CP_API_KEY", API_KEY)
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", HITL_KEY)


@pytest.fixture
def app(tmp_path):
    ks = KeyStore(dir=str(tmp_path / "keys"))
    return create_app(
        keystore=ks, dsn="sqlite:///:memory:",
        policy_store_path=str(tmp_path / "policies.json"),
        pack_store_path=str(tmp_path / "packs.json"),
        custom_verifier_store_path=str(tmp_path / "custom_verifiers.json"),
    )


@pytest.fixture
def client(app):
    return TestClient(app)


# ── normalize_aggregate_params ───────────────────────────────────────
class TestNormalizeAggregateParams:
    def test_defaults(self):
        since, bucket = normalize_aggregate_params(None, None)
        assert since == 86_400
        assert bucket == 3_600

    def test_since_caps_at_30d(self):
        since, _ = normalize_aggregate_params(10 * MAX_SINCE_SECS, 3_600)
        assert since == MAX_SINCE_SECS

    def test_bucket_floor(self):
        _, bucket = normalize_aggregate_params(3_600, 1)
        assert bucket == MIN_BUCKET_SECS

    def test_rejects_too_many_buckets(self):
        # 30d window at 60s buckets → 43_200 buckets ≫ MAX_BUCKETS.
        with pytest.raises(ValueError):
            normalize_aggregate_params(MAX_SINCE_SECS, MIN_BUCKET_SECS)

    def test_zero_bucket_falls_to_default(self):
        _, bucket = normalize_aggregate_params(3_600, 0)
        assert bucket == 3_600

    def test_negative_since_falls_to_default(self):
        since, _ = normalize_aggregate_params(-1, 3_600)
        assert since == 86_400

    def test_rejects_bucket_larger_than_since(self):
        # `?since_secs=3600&bucket_secs=86400` collapses to a single
        # bucket whose nominal width exceeds the requested window —
        # logically empty. Reject so the dashboard does not silently
        # mis-label its time range.
        with pytest.raises(ValueError):
            normalize_aggregate_params(3_600, 86_400)


# ── ledger_aggregate (in-process helper) ────────────────────────────
def _seed_ledger(engine, rows: list[tuple[int, dict]],
                 tenant_id: str = "default") -> None:
    """Insert pre-built (ts, body) rows directly through LedgerRepo.append.

    Each row uses a fresh sha so the chain stays valid; the helper
    drives `time.time` via patching is NOT needed because LedgerRepo
    doesn't read ts from us — we hand-write to the table here so the
    bucket window math is deterministic.
    """
    from sqlalchemy.orm import Session
    with Session(engine) as s:
        prev = ""
        for i, (ts, body) in enumerate(rows):
            import hashlib
            import json as _json
            h_input = (prev or "") + _json.dumps(body, sort_keys=True) + str(i)
            h = hashlib.sha256(h_input.encode("utf-8")).hexdigest()
            entry = LedgerEntry(
                ts=int(ts), tenant_id=tenant_id, matter="S1",
                prev=prev, body=body, token=f"tok-{i}", h=h,
            )
            s.add(entry)
            prev = h
        s.commit()


class TestLedgerAggregateHelper:
    def test_empty_returns_zero_filled_buckets(self, tmp_path):
        engine = make_engine(f"sqlite:///{tmp_path / 'a.db'}")
        init_schema(engine)
        agg = ledger_aggregate(engine, "default",
                                since_secs=3_600, bucket_secs=600,
                                now=10_000)
        assert agg.since_secs == 3_600
        assert agg.bucket_secs == 600
        assert len(agg.buckets) == 6
        for b in agg.buckets:
            assert b.count == 0
            assert all(v == 0 for v in b.by_action.values())
            assert all(v == 0 for v in b.by_verdict.values())

    def test_buckets_rows_by_ts(self, tmp_path):
        engine = make_engine(f"sqlite:///{tmp_path / 'b.db'}")
        init_schema(engine)
        # Pick `now` on a bucket boundary so the calendar-aligned grid
        # starts at exactly `now - since`. With now=10_200 and
        # bucket=600 we get cutoff = 10_200 - 3_000 = 7_200, buckets
        # [7_200, 7_800, 8_400, 9_000, 9_600, 10_200).
        now = 10_200
        _seed_ledger(engine, [
            (7_300, {"action": "block", "verdict": "deny"}),
            (7_900, {"action": "audit", "verdict": "pass"}),
            (10_100, {"action": "ask", "verdict": "review"}),
        ])
        agg = ledger_aggregate(engine, "default",
                                since_secs=3_000, bucket_secs=600,
                                now=now)
        assert len(agg.buckets) == 5
        assert agg.buckets[0].count == 1
        assert agg.buckets[0].by_action["block"] == 1
        assert agg.buckets[0].by_verdict["fail"] == 1   # deny → fail
        assert agg.buckets[1].count == 1
        assert agg.buckets[1].by_action["audit"] == 1
        assert agg.buckets[1].by_verdict["pass"] == 1
        assert agg.buckets[4].count == 1
        assert agg.buckets[4].by_action["ask"] == 1
        assert agg.buckets[4].by_verdict["needs_review"] == 1  # review

    def test_skips_future_stamped_rows(self, tmp_path):
        """Rows stamped past `now` (clock-drift on a producer host,
        future-dated test fixture) MUST NOT be silently absorbed into
        the last bucket — see the comment on the WHERE bound. They are
        skipped so drift surfaces as missing data rather than as a
        misleading spike on the "now" hour."""
        engine = make_engine(f"sqlite:///{tmp_path / 'b_future.db'}")
        init_schema(engine)
        now = 10_000
        _seed_ledger(engine, [
            # 60s past `now` — should NOT land in any bucket.
            (now + 60, {"action": "block", "verdict": "deny"}),
        ])
        agg = ledger_aggregate(
            engine, "default",
            since_secs=86_400, bucket_secs=3_600, now=now,
        )
        assert sum(b.count for b in agg.buckets) == 0
        for b in agg.buckets:
            assert b.by_action["block"] == 0

    def test_internal_empty_buckets_kept(self, tmp_path):
        """Empty buckets between two populated buckets MUST stay in
        the response with count=0 — a regression to a groupby-style
        omit-empty result would silently change the chart's column
        count and X-axis ticks."""
        engine = make_engine(f"sqlite:///{tmp_path / 'b_empty_mid.db'}")
        init_schema(engine)
        # 5-bucket window of 600s. Seed bkt 0 + bkt 4 only.
        now = 10_200
        _seed_ledger(engine, [
            (7_300, {"action": "block", "verdict": "deny"}),
            (10_100, {"action": "audit", "verdict": "pass"}),
        ])
        agg = ledger_aggregate(
            engine, "default",
            since_secs=3_000, bucket_secs=600, now=now,
        )
        assert len(agg.buckets) == 5
        assert agg.buckets[0].count == 1
        assert agg.buckets[1].count == 0
        assert agg.buckets[2].count == 0
        assert agg.buckets[3].count == 0
        assert agg.buckets[4].count == 1

    def test_crosses_midnight(self, tmp_path):
        """Rows seeded on either side of a calendar midnight (UTC)
        must land in distinct, adjacent buckets — the 24h chart must
        not collapse the boundary into a single column."""
        engine = make_engine(f"sqlite:///{tmp_path / 'b_midnight.db'}")
        init_schema(engine)
        # Fixed UTC midnight: 2026-06-24 00:00:00 UTC = 1782604800.
        midnight = 1_782_604_800
        # `now` ~30 min past midnight; bucket=3600, since=86400 → 24
        # buckets. After alignment, bucket_end = ceil(now/3600)*3600 =
        # midnight + 3600. cutoff = midnight + 3600 - 24*3600 =
        # midnight - 23h. So midnight - 30min lands in bucket 22
        # (covers [midnight - 1h, midnight)), and midnight + 30min
        # lands in bucket 23 (covers [midnight, midnight + 1h)).
        now = midnight + 30 * 60
        _seed_ledger(engine, [
            (midnight - 30 * 60, {"action": "block", "verdict": "deny"}),
            (midnight + 20 * 60, {"action": "audit", "verdict": "pass"}),
        ])
        agg = ledger_aggregate(
            engine, "default",
            since_secs=86_400, bucket_secs=3_600, now=now,
        )
        assert len(agg.buckets) == 24
        # Find the bucket whose [ts_start, ts_start+bucket) contains
        # midnight - 30min — that's the bucket starting at midnight - 1h.
        pre_idx = next(
            i for i, b in enumerate(agg.buckets)
            if b.ts_start == midnight - 3600
        )
        post_idx = next(
            i for i, b in enumerate(agg.buckets)
            if b.ts_start == midnight
        )
        assert post_idx == pre_idx + 1
        assert agg.buckets[pre_idx].count == 1
        assert agg.buckets[pre_idx].by_action["block"] == 1
        assert agg.buckets[post_idx].count == 1
        assert agg.buckets[post_idx].by_action["audit"] == 1

    def test_returns_zero_filled_for_unknown_tenant(self, tmp_path):
        """A tenant with no rows in the store still gets a fully
        zero-filled bucket grid — the chart never sees an empty list
        for "no data for this tenant"."""
        engine = make_engine(f"sqlite:///{tmp_path / 'b_unknown.db'}")
        init_schema(engine)
        _seed_ledger(engine, [
            (10_100, {"action": "block", "verdict": "deny"}),
        ], tenant_id="other")
        agg = ledger_aggregate(
            engine, "fresh-tenant",
            since_secs=3_000, bucket_secs=600, now=10_200,
        )
        assert len(agg.buckets) == 5
        for b in agg.buckets:
            assert b.count == 0
            assert sum(b.by_action.values()) == 0
            assert sum(b.by_verdict.values()) == 0

    def test_unknown_action_and_verdict_ignored(self, tmp_path):
        engine = make_engine(f"sqlite:///{tmp_path / 'c.db'}")
        init_schema(engine)
        now = 5_000
        _seed_ledger(engine, [
            (4_500, {"action": "totally-novel", "verdict": "shrug"}),
        ])
        agg = ledger_aggregate(engine, "default",
                                since_secs=1_000, bucket_secs=500,
                                now=now)
        # Row counted toward `count` but no closed-set bucket touched.
        assert sum(b.count for b in agg.buckets) == 1
        for b in agg.buckets:
            assert sum(b.by_action.values()) == 0
            assert sum(b.by_verdict.values()) == 0

    def test_tenant_scope(self, tmp_path):
        engine = make_engine(f"sqlite:///{tmp_path / 'd.db'}")
        init_schema(engine)
        _seed_ledger(engine, [
            (1_000, {"action": "block", "verdict": "deny"}),
        ], tenant_id="other")
        agg = ledger_aggregate(engine, "default",
                                since_secs=10_000, bucket_secs=1_000,
                                now=10_500)
        assert sum(b.count for b in agg.buckets) == 0

    def test_to_dict_shape(self, tmp_path):
        engine = make_engine(f"sqlite:///{tmp_path / 'e.db'}")
        init_schema(engine)
        agg = ledger_aggregate(engine, "default",
                                since_secs=600, bucket_secs=300, now=10_000)
        d = ledger_aggregate_to_dict(agg)
        assert d["since_secs"] == 600
        assert d["bucket_secs"] == 300
        assert "block" in d["action_buckets"]
        assert "needs_review" in d["verdict_buckets"]
        assert len(d["buckets"]) == 2
        assert "ts_start" in d["buckets"][0]
        assert "by_action" in d["buckets"][0]
        assert "by_verdict" in d["buckets"][0]

    def test_aggregate_response_carries_no_body_fields(self, tmp_path):
        """Defense-in-depth redaction gate. The aggregator promises
        count-only egress: no ledger body bytes ever appear in the
        response. Seed a row whose body contains a sentinel string +
        lock the per-bucket allowed key set; a future maintainer
        adding (say) `sample_body` for a "last emission" affordance
        trips this guard."""
        import json
        sentinel = "SENTINEL_NOT_FOR_EGRESS"
        engine = make_engine(f"sqlite:///{tmp_path / 'e_redact.db'}")
        init_schema(engine)
        _seed_ledger(engine, [
            (
                9_700,
                {
                    "action": "block",
                    "verdict": "deny",
                    "leaked_payload": sentinel,
                    "another": {"nested": sentinel},
                },
            ),
        ])
        agg = ledger_aggregate(
            engine, "default",
            since_secs=600, bucket_secs=300, now=10_000,
        )
        d = ledger_aggregate_to_dict(agg)
        wire = json.dumps(d)
        assert sentinel not in wire
        # Allowed per-bucket key set is locked. Widening it (e.g. for
        # a 'last emission body' affordance) MUST be a deliberate
        # change here so the redaction contract is reviewed.
        allowed = {"ts_start", "count", "by_action", "by_verdict"}
        for b in d["buckets"]:
            assert set(b.keys()) == allowed


# ── metrics_summary (in-process helper) ─────────────────────────────
class TestMetricsSummaryHelper:
    def test_empty_install(self, tmp_path):
        engine = make_engine(f"sqlite:///{tmp_path / 'f.db'}")
        init_schema(engine)
        ledger = LedgerRepo(engine)
        s = metrics_summary(
            engine, "default",
            policy_overrides=[],
            pack_member_lists=[],
            scripts_total=0,
            ledger_repo=ledger,
            now=10_000,
        )
        assert s.policies_total == 0
        assert s.policies_enabled == 0
        assert s.packs_total_active == 0
        assert s.packs_partial == 0
        assert s.scripts_total == 0
        assert s.hitl_pending == 0
        assert s.ledger_24h_total == 0
        assert s.ledger_chain_ok is True   # empty chain is vacuously OK
        assert s.last_emission_ts is None

    def test_summary_to_dict_shape(self, tmp_path):
        engine = make_engine(f"sqlite:///{tmp_path / 'g.db'}")
        init_schema(engine)
        ledger = LedgerRepo(engine)
        s = metrics_summary(
            engine, "default",
            policy_overrides=[],
            pack_member_lists=[],
            scripts_total=0,
            ledger_repo=ledger,
            now=10_000,
        )
        d = metrics_summary_to_dict(s)
        assert set(d.keys()) == {
            "policies", "packs", "scripts", "hitl_pending",
            "ledger_24h_total", "ledger_chain_ok", "last_emission_ts",
        }
        assert d["policies"]["by_action"]["block"] == 0


# ── HTTP surface ─────────────────────────────────────────────────────
class TestLedgerAggregateRoute:
    def test_requires_api_key(self, client):
        assert client.get("/ledger/aggregate").status_code == 401

    def test_empty_24h_default(self, client):
        r = client.get("/ledger/aggregate", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["since_secs"] == 86_400
        assert body["bucket_secs"] == 3_600
        assert len(body["buckets"]) == 24
        assert all(b["count"] == 0 for b in body["buckets"])
        assert "block" in body["action_buckets"]
        assert "inject_context" in body["action_buckets"]

    def test_custom_window(self, client):
        r = client.get(
            "/ledger/aggregate?since_secs=3600&bucket_secs=600",
            headers=HEADERS,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["since_secs"] == 3600
        assert body["bucket_secs"] == 600
        assert len(body["buckets"]) == 6

    def test_rejects_oversized_request(self, client):
        # 30d window at 60s buckets → 43_200 buckets ≫ MAX_BUCKETS.
        r = client.get(
            f"/ledger/aggregate?since_secs={MAX_SINCE_SECS}&bucket_secs=60",
            headers=HEADERS,
        )
        assert r.status_code == 400

    def test_rejects_bucket_larger_than_since_request(self, client):
        # `bucket_secs > since_secs` is a logically empty request that
        # used to silently widen the SQL window past `now` and clamp
        # rows into a single oversized bucket. Reject at the route
        # layer so the chart never quietly mis-labels its window.
        r = client.get(
            "/ledger/aggregate?since_secs=3600&bucket_secs=86400",
            headers=HEADERS,
        )
        assert r.status_code == 400


class TestMetricsSummaryRoute:
    def test_requires_api_key(self, client):
        assert client.get("/metrics/summary").status_code == 401

    def test_empty_install_shape(self, client):
        r = client.get("/metrics/summary", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["hitl_pending"] == 0
        assert body["ledger_24h_total"] == 0
        assert body["ledger_chain_ok"] is True
        assert body["last_emission_ts"] is None
        assert body["policies"]["total"] == 0
        assert body["policies"]["enabled"] == 0
        # Built-in packs surface in `packs.total_active` only when at
        # least one member is enabled; a fresh install has nothing
        # enabled so `total_active` must be 0.
        assert body["packs"]["total_active"] == 0
        assert body["packs"]["partial"] == 0
        assert body["scripts"]["total"] == 0

    def test_reflects_pending_hitl(self, app, client):
        # Drop a pending HITL item directly through the SQL layer so
        # we don't depend on the verifier->review path for this test.
        from sqlalchemy.orm import Session
        with Session(app.state.engine) as s:
            s.add(HitlItem(
                ts_created=int(time.time()),
                tenant_id="default",
                subject="S1", payload_hash="P1",
                reason="probe", payload={"tenant_id": "default"},
                status=HitlStatus.pending,
            ))
            s.commit()
        r = client.get("/metrics/summary", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["hitl_pending"] == 1
