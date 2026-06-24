"""D53b: POST /policies/dry-run.

Replay a draft Policy IR over the last 24h / 7d of ledger rows and
report how many of those rows would have triggered the proposed
action. Read-only: no ledger writes, no policy persistence.

Contract under test:
  - valid IR + populated ledger window → coherent counts
  - invalid IR → 422 (reuses /policies validation)
  - sample_matched rows pass through D50's redactor
  - total_records honors `limit`
  - `since` selects the window (24h vs 7d)
  - non-evidence archetypes return skipped_reason
"""
from __future__ import annotations

import time
import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.db import LedgerRepo
from magi_cp.cloud.keys import KeyStore


API_KEY = "test-api-key"
ADMIN_KEY = "dry-run-admin-key"
HEADERS_API = {"X-Api-Key": API_KEY}
HEADERS_ADMIN = {"X-Admin-Api-Key": ADMIN_KEY}


@pytest.fixture(autouse=True)
def _set_keys(monkeypatch):
    monkeypatch.setenv("MAGI_CP_API_KEY", API_KEY)
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", ADMIN_KEY)


@pytest.fixture
def app(tmp_path):
    ks = KeyStore(dir=str(tmp_path / "keys"))
    policy_store_path = str(tmp_path / "policies.json")
    return create_app(
        keystore=ks,
        dsn="sqlite:///:memory:",
        policy_store_path=policy_store_path,
    )


@pytest.fixture
def client(app):
    return TestClient(app)


# ── helpers ─────────────────────────────────────────────────────────


def _seed_ledger_rows(app, rows: list[dict], tenant_id: str = "default") -> None:
    """Append literal-body rows directly so we can control `step`,
    `verdict`, `text` etc. without going through /verify_inline."""
    engine = app.state.engine
    repo = LedgerRepo(engine)
    for body in rows:
        repo.append(
            subject=body.get("subject", "s_dry"),
            body=body,
            token="",
            tenant_id=tenant_id,
        )


def _evidence_ir(
    *,
    pid: str = "dr/test",
    requires: list[dict] | None = None,
    event: str = "PreToolUse",
    matcher: str = "Bash",
    action: str = "block",
) -> dict:
    return {
        "id": pid,
        "description": "dry-run fixture",
        "trigger": {"host": "claude-code", "event": event, "matcher": matcher},
        "sentinel_re": None,
        "requires": requires or [],
        "action": action,
        "on_signature_invalid": "deny",
        "gate_binary": "/usr/local/bin/magi-gate.sh",
        "version": "0.1",
    }


# ── auth + validation ──────────────────────────────────────────────


def test_dry_run_requires_admin_key(client):
    r = client.post("/policies/dry-run", json={
        "ir": _evidence_ir(requires=[{"step": "citation_verify", "verdict": "pass"}]),
    })
    assert r.status_code == 401


def test_dry_run_rejects_invalid_ir_with_422(client):
    # An EvidenceReq kind=regex with an empty pattern is rejected by
    # `EvidenceReq.validate()` (raises ValueError). The dry-run
    # endpoint surfaces the validation failure as 422 - same code
    # /policies PUT uses for the same matrix / shape error so the
    # authoring page renders the message it already knows how to.
    bad = _evidence_ir(requires=[{"kind": "regex", "pattern": ""}])
    r = client.post(
        "/policies/dry-run", json={"ir": bad}, headers=HEADERS_ADMIN,
    )
    assert r.status_code == 422, r.text


def test_dry_run_rejects_unknown_event_with_422(client):
    bad = _evidence_ir(event="BogusEvent")
    r = client.post(
        "/policies/dry-run", json={"ir": bad}, headers=HEADERS_ADMIN,
    )
    assert r.status_code == 422, r.text


def test_dry_run_rejects_unknown_since_with_422(client):
    r = client.post(
        "/policies/dry-run",
        json={
            "ir": _evidence_ir(requires=[
                {"step": "citation_verify", "verdict": "pass"},
            ]),
            "since": "30d",
        },
        headers=HEADERS_ADMIN,
    )
    assert r.status_code == 422


def test_dry_run_rejects_oversize_limit_with_422(client):
    r = client.post(
        "/policies/dry-run",
        json={
            "ir": _evidence_ir(requires=[
                {"step": "citation_verify", "verdict": "pass"},
            ]),
            "limit": 999_999,
        },
        headers=HEADERS_ADMIN,
    )
    assert r.status_code == 422


# ── matched count vs synthetic ledger ──────────────────────────────


def test_dry_run_step_requires_counts_failed_records(app, client):
    # IR requires citation_verify=pass. We seed 3 rows where the
    # verdict differs:
    #   row A: step=citation_verify verdict=pass  →  requires passes
    #                                                →  action NOT fired
    #   row B: step=citation_verify verdict=deny  →  fails
    #                                                →  action fired
    #   row C: step=citation_verify verdict=deny  →  fails
    #                                                →  action fired
    _seed_ledger_rows(app, [
        {"step": "citation_verify", "verdict": "pass",
         "hook_event": "PreToolUse", "matcher": "Bash"},
        {"step": "citation_verify", "verdict": "deny",
         "hook_event": "PreToolUse", "matcher": "Bash"},
        {"step": "citation_verify", "verdict": "deny",
         "hook_event": "PreToolUse", "matcher": "Bash"},
    ])
    ir = _evidence_ir(requires=[
        {"step": "citation_verify", "verdict": "pass"},
    ])
    r = client.post(
        "/policies/dry-run", json={"ir": ir}, headers=HEADERS_ADMIN,
    ).json()
    assert r["total_records"] == 3
    assert r["matched"] == 2  # two deny rows would have fired block
    assert r["by_action"]["block"] == 2
    assert r["by_verdict"]["pass"] == 1
    assert r["by_verdict"]["deny"] == 2


def test_dry_run_regex_requires_uses_payload_text(app, client):
    # A regex requires entry should match against the recorded body's
    # `text` field, mirroring /verify_inline's payload_text slicing.
    # We seed two rows:
    #   row A text="bar baz"     → regex 'foo' does NOT match → requires
    #                              fails → action fires → counted as
    #                              matched.
    #   row B text="foo bar baz" → regex 'foo' matches → requires
    #                              passes → action does NOT fire →
    #                              not counted.
    _seed_ledger_rows(app, [
        {"step": "inline_regex", "verdict": "deny",
         "text": "bar baz",
         "hook_event": "PreToolUse", "matcher": "Bash"},
        {"step": "inline_regex", "verdict": "pass",
         "text": "foo bar baz",
         "hook_event": "PreToolUse", "matcher": "Bash"},
    ])
    ir = _evidence_ir(
        action="audit",
        requires=[{"kind": "regex", "pattern": "foo"}],
    )
    r = client.post(
        "/policies/dry-run", json={"ir": ir}, headers=HEADERS_ADMIN,
    ).json()
    assert r["total_records"] == 2
    assert r["matched"] == 1
    assert r["by_action"]["audit"] == 1


def test_dry_run_empty_requires_fires_on_every_trigger_match(app, client):
    # action=audit with empty requires = unconditional "emit signal"
    # archetype. Every row in the trigger frame fires.
    _seed_ledger_rows(app, [
        {"step": "x", "verdict": "pass",
         "hook_event": "PreToolUse", "matcher": "Bash"},
        {"step": "y", "verdict": "deny",
         "hook_event": "PreToolUse", "matcher": "Bash"},
        # Outside the matcher frame; must NOT count.
        {"step": "z", "verdict": "pass",
         "hook_event": "PreToolUse", "matcher": "Read"},
    ])
    ir = _evidence_ir(action="audit", requires=[])
    r = client.post(
        "/policies/dry-run", json={"ir": ir}, headers=HEADERS_ADMIN,
    ).json()
    assert r["total_records"] == 2  # Bash-frame rows only
    assert r["matched"] == 2
    assert r["by_action"]["audit"] == 2


def test_dry_run_trigger_event_mismatch_excluded(app, client):
    # A PostToolUse row in the ledger must not be admitted by a
    # PreToolUse-triggered policy. The recorded hook_event is the
    # source of truth.
    _seed_ledger_rows(app, [
        {"step": "x", "verdict": "deny",
         "hook_event": "PostToolUse", "matcher": "Bash"},
    ])
    ir = _evidence_ir(
        event="PreToolUse",
        action="audit",
        requires=[],
    )
    r = client.post(
        "/policies/dry-run", json={"ir": ir}, headers=HEADERS_ADMIN,
    ).json()
    assert r["total_records"] == 0
    assert r["matched"] == 0
    assert r["skipped_reason"] == "no-records-in-trigger-frame"


def test_dry_run_limit_caps_replayed_rows(app, client):
    _seed_ledger_rows(app, [
        {"step": "x", "verdict": "deny",
         "hook_event": "PreToolUse", "matcher": "Bash"}
        for _ in range(20)
    ])
    ir = _evidence_ir(action="audit", requires=[])
    r = client.post(
        "/policies/dry-run",
        json={"ir": ir, "limit": 5},
        headers=HEADERS_ADMIN,
    ).json()
    # total_records is bounded by the replay limit; matched <= total.
    assert r["total_records"] == 5
    assert r["matched"] == 5
    assert r["limit"] == 5


def test_dry_run_since_window_excludes_old_rows(app, client):
    # Seed a row, force-rewrite its ts to outside the 24h window by
    # mutating the row in the DB. The dry-run with since=24h must
    # exclude it; since=7d still includes it.
    from sqlalchemy.orm import Session
    from magi_cp.cloud.db import LedgerEntry
    _seed_ledger_rows(app, [
        {"step": "x", "verdict": "deny",
         "hook_event": "PreToolUse", "matcher": "Bash"},
    ])
    # Backdate the row 3 days. 24h window excludes it, 7d still
    # contains it.
    three_days_ago = int(time.time()) - 3 * 86_400
    with Session(app.state.engine) as s:
        row = s.scalars(
            __import__("sqlalchemy").select(LedgerEntry)
        ).one()
        row.ts = three_days_ago
        s.commit()

    ir = _evidence_ir(action="audit", requires=[])

    r_24h = client.post(
        "/policies/dry-run",
        json={"ir": ir, "since": "24h"},
        headers=HEADERS_ADMIN,
    ).json()
    assert r_24h["total_records"] == 0
    assert r_24h["since"] == "24h"

    r_7d = client.post(
        "/policies/dry-run",
        json={"ir": ir, "since": "7d"},
        headers=HEADERS_ADMIN,
    ).json()
    assert r_7d["total_records"] == 1
    assert r_7d["matched"] == 1
    assert r_7d["since"] == "7d"


# ── sample_matched redaction ────────────────────────────────────────


def test_dry_run_sample_matched_is_redacted(app, client):
    # A row body whose text contains a JWT-shaped secret must NOT
    # round-trip the raw secret through the dry-run response. The
    # endpoint runs every sample_matched body through D50's
    # redact_payload_preview before serialization.
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxIiwibmFtZSI6IkphbmUifQ."
        "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    )
    _seed_ledger_rows(app, [
        {"step": "x", "verdict": "deny",
         "text": f"leaked token {jwt}",
         "hook_event": "PreToolUse", "matcher": "Bash"},
    ])
    ir = _evidence_ir(action="audit", requires=[])
    r = client.post(
        "/policies/dry-run", json={"ir": ir}, headers=HEADERS_ADMIN,
    ).json()
    assert r["matched"] == 1
    assert len(r["sample_matched"]) == 1
    preview = r["sample_matched"][0]["redacted_payload_preview"]
    assert jwt not in preview, (
        "redactor bypassed: raw JWT reached sample_matched"
    )
    assert "[REDACTED:jwt]" in preview


def test_dry_run_sample_matched_capped_at_three(app, client):
    _seed_ledger_rows(app, [
        {"step": "x", "verdict": "deny", "id_marker": i,
         "hook_event": "PreToolUse", "matcher": "Bash"}
        for i in range(10)
    ])
    ir = _evidence_ir(action="audit", requires=[])
    r = client.post(
        "/policies/dry-run", json={"ir": ir}, headers=HEADERS_ADMIN,
    ).json()
    assert r["matched"] == 10
    # The brief caps the inline preview at 3 rows.
    assert len(r["sample_matched"]) == 3


def test_dry_run_sample_matched_verdict_collapses_unknown_to_none(
    app, client,
):
    # Mirror the /ledger/samples allowlist behaviour: a novel verdict
    # string must NOT echo back to the client.
    _seed_ledger_rows(app, [
        {"step": "x", "verdict": "totally-novel-verdict",
         "text": "no foo here",
         "hook_event": "PreToolUse", "matcher": "Bash"},
    ])
    ir = _evidence_ir(action="audit", requires=[])
    r = client.post(
        "/policies/dry-run", json={"ir": ir}, headers=HEADERS_ADMIN,
    ).json()
    assert r["matched"] == 1
    sample = r["sample_matched"][0]
    assert sample["verdict"] is None


# ── non-evidence archetypes ────────────────────────────────────────


def test_dry_run_permission_archetype_skipped(app, client):
    # A PermissionPolicy compiles to managed-settings directly; it
    # has no requires[] to replay. The dry-run endpoint returns
    # skipped_reason so the dashboard can render an explanation
    # instead of a misleading "0 of N would have blocked".
    ir = {
        "type": "permission",
        "id": "perm/test",
        "description": "block rm -rf",
        "trigger": {
            "host": "claude-code", "event": "PreToolUse", "matcher": "Bash",
        },
        "permission": "deny",
        "pattern": "Bash(rm -rf *)",
    }
    r = client.post(
        "/policies/dry-run", json={"ir": ir}, headers=HEADERS_ADMIN,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["matched"] == 0
    assert data["total_records"] == 0
    assert data["skipped_reason"] == "archetype-not-dry-runnable"


# ── idempotence: no side effects ────────────────────────────────────


def test_dry_run_does_not_write_to_ledger(app, client):
    _seed_ledger_rows(app, [
        {"step": "x", "verdict": "deny",
         "hook_event": "PreToolUse", "matcher": "Bash"},
    ])
    pre = client.get("/ledger", headers=HEADERS_API).json()
    pre_count = len(pre["entries"])
    ir = _evidence_ir(action="audit", requires=[])
    client.post(
        "/policies/dry-run", json={"ir": ir}, headers=HEADERS_ADMIN,
    )
    post = client.get("/ledger", headers=HEADERS_API).json()
    assert len(post["entries"]) == pre_count


def test_dry_run_does_not_persist_policy(client):
    pre = client.get("/policies", headers=HEADERS_ADMIN).json()
    pre_ids = {p["id"] for p in pre["items"]}
    ir = _evidence_ir(pid="dr/should-not-persist", action="audit", requires=[])
    client.post(
        "/policies/dry-run", json={"ir": ir}, headers=HEADERS_ADMIN,
    )
    post = client.get("/policies", headers=HEADERS_ADMIN).json()
    post_ids = {p["id"] for p in post["items"]}
    assert pre_ids == post_ids
    assert "dr/should-not-persist" not in post_ids
