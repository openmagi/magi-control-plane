"""U7 gjc runtime adapter: dashboard coverage + per-tenant runtime.

Design brief: 2026-07-08-magi-cp-gajae-code-runtime-adapter-design
Section 7 (event coverage) + Section 8 (ledger) + Section 9.3 (flag ladder).
Mirrors tests/test_codex_runtime_dashboard_p4.py for the gjc runtime.

Covered here:
  - GET /policies/{id}/coverage/gjc - per-policy strip: enforced on a
    mapped tool, unsupported (red) on a ContextInjection (no context
    channel), downgraded (amber) on a Subagent (via the task tool).
  - GET /packs/{id}/coverage/gjc - per-pack rollup, mutually exclusive
    counts summing to the member total.
  - GET /tenants/{id}/runtime - picker lists THREE rollups and carries
    the gjc_enabled flag (reflecting MAGI_CP_GJC_RUNTIME_ENABLED).
  - POST /tenants/{id}/runtime - refuses gjc with the flag off (403),
    accepts it with the flag on and persists; switch-back to CC is
    always allowed; the gjc flag is independent of the codex flag.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.keys import KeyStore
from magi_cp.cloud.policy_store import PolicyStore
from magi_cp.cloud.pack_store import PackStore, UserPackRow
from magi_cp.cloud.tenants import TenantRepo
from magi_cp.policy.ir import (
    ContextInjectionPolicy,
    EvidencePolicy,
    EvidenceReq,
    SubagentPolicy,
    Trigger,
)
from magi_cp.policy.resolved import PolicyOverride


ADMIN_KEY = "gjc-runtime-admin-key"
LEGACY_API_KEY = "gjc-runtime-legacy-key"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", ADMIN_KEY)
    monkeypatch.setenv("MAGI_CP_API_KEY", LEGACY_API_KEY)
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", "gjc-runtime-hitl-key")
    # Both adapters default-ON: unset means available. The disabled-path
    # tests set the explicit falsy token themselves.
    monkeypatch.delenv("MAGI_CP_GJC_RUNTIME_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_CP_CODEX_RUNTIME_ENABLED", raising=False)


def _evidence(pid, *, event="PreToolUse", matcher="Bash", action="block"):
    return EvidencePolicy(
        id=pid, description="t", version="0.1",
        trigger=Trigger(host="claude-code", event=event, matcher=matcher),
        sentinel_re=None,
        requires=[EvidenceReq(kind="step", step="privilege_scan",
                              verdict="pass")],
        action=action, on_signature_invalid="deny",
        gate_binary="/usr/local/bin/magi-gate.sh",
    )


def _seed_policies(path):
    """Four archetypes exercising distinct gjc coverage cells:
    ev/bash + ev/read → enforced (mapped tools); ctx/cite → unsupported
    (gjc_no_context_channel, no context channel on the block return);
    sub/researcher → downgraded (gjc_subagent_via_task_tool)."""
    store = PolicyStore(path=path)
    store.save([
        PolicyOverride(policy=_evidence("ev/bash"), source="user",
                       enabled=True),
        PolicyOverride(policy=_evidence("ev/read", matcher="Read"),
                       source="user", enabled=True),
        PolicyOverride(
            policy=ContextInjectionPolicy(
                id="ctx/cite", description="t",
                event="PreToolUse", template="always cite", matcher="Bash",
            ),
            source="user", enabled=True,
        ),
        PolicyOverride(
            policy=SubagentPolicy(
                id="sub/researcher", description="t", version="0.1",
                subagent_type="researcher",
            ),
            source="user", enabled=True,
        ),
    ])


@pytest.fixture
def cloud(tmp_path):
    ks = KeyStore(dir=str(tmp_path / "keys"))
    dsn = f"sqlite:///{tmp_path}/cloud.sqlite"
    policy_path = str(tmp_path / "policies.json")
    pack_path = str(tmp_path / "packs.json")
    _seed_policies(policy_path)
    PackStore(path=pack_path).save([
        UserPackRow(
            id="user-pack/coding", name="Coding", description="",
            policy_ids=["ev/bash", "ev/read", "ctx/cite", "sub/researcher"],
        ),
    ])
    app = create_app(
        keystore=ks, dsn=dsn,
        policy_store_path=policy_path,
        pack_store_path=pack_path,
    )
    return {
        "app": app,
        "client": TestClient(app),
        "engine": app.state.engine,
    }


def _admin(client, method, path, **kw):
    return client.request(method, path,
                          headers={"X-Admin-Api-Key": ADMIN_KEY}, **kw)


# ── per-policy coverage strip ────────────────────────────────────────
def test_policy_coverage_gjc_bash_enforced(cloud):
    r = _admin(cloud["client"], "GET", "/policies/ev/bash/coverage/gjc")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["policy_id"] == "ev/bash"
    assert body["runtime_id"] == "gjc"
    assert body["coverage"] == "enforced"
    assert body["status"] == "enforced"


def test_policy_coverage_gjc_read_enforced(cloud):
    # gjc maps the read family natively (unlike Codex, where Read is a
    # silent-skip → downgraded). This is the gjc-distinct assertion.
    r = _admin(cloud["client"], "GET", "/policies/ev/read/coverage/gjc")
    assert r.status_code == 200, r.text
    assert r.json()["coverage"] == "enforced"


def test_policy_coverage_gjc_context_injection_unsupported(cloud):
    # No context channel on the tool_call block return (v1) → red.
    r = _admin(cloud["client"], "GET", "/policies/ctx/cite/coverage/gjc")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["coverage"] == "unsupported"
    assert body["status"] == "gjc_no_context_channel"


def test_policy_coverage_gjc_subagent_downgraded(cloud):
    # Enforced parent-side via the task tool, but flagged as a downgrade.
    r = _admin(cloud["client"], "GET", "/policies/sub/researcher/coverage/gjc")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["coverage"] == "downgraded"
    assert body["downgrade"] == "gjc_subagent_via_task_tool"


def test_policy_coverage_gjc_unknown_policy_404(cloud):
    r = _admin(cloud["client"], "GET", "/policies/nope/coverage/gjc")
    assert r.status_code == 404


# ── per-pack coverage rollup ─────────────────────────────────────────
def test_pack_coverage_gjc_rollup_counts_sum_to_total(cloud):
    r = _admin(cloud["client"], "GET",
               "/packs/user-pack/coding/coverage/gjc")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pack_id"] == "user-pack/coding"
    assert body["total"] == 4
    total = (body["enforced"] + body["downgraded"]
             + body["unsupported"] + body["not_applicable"])
    assert total == body["total"]
    # ev/bash + ev/read enforced; sub/researcher downgraded; ctx/cite
    # unsupported (no context channel in v1).
    assert body["enforced"] == 2
    assert body["downgraded"] == 1
    assert body["unsupported"] == 1


# ── per-tenant runtime picker state ──────────────────────────────────
def test_get_tenant_runtime_lists_three_rollups(cloud):
    r = _admin(cloud["client"], "GET", "/tenants/default/runtime")
    assert r.status_code == 200, r.text
    body = r.json()
    ids = {rt["id"] for rt in body["runtimes"]}
    assert ids == {"claude-code", "codex", "gjc"}
    gjc = next(rt for rt in body["runtimes"] if rt["id"] == "gjc")
    # gjc enforces ev/bash + ev/read natively out of the four members.
    assert gjc["enforced"] == 2 and gjc["total"] == 4


def test_get_tenant_runtime_reflects_gjc_enabled(cloud, monkeypatch):
    monkeypatch.setenv("MAGI_CP_GJC_RUNTIME_ENABLED", "1")
    r = _admin(cloud["client"], "GET", "/tenants/default/runtime")
    assert r.json()["gjc_enabled"] is True


def test_get_tenant_runtime_reports_gjc_disabled_when_flag_off(cloud, monkeypatch):
    monkeypatch.setenv("MAGI_CP_GJC_RUNTIME_ENABLED", "0")
    r = _admin(cloud["client"], "GET", "/tenants/default/runtime")
    assert r.status_code == 200, r.text
    assert r.json()["gjc_enabled"] is False


# ── runtime switch (feature-flag ladder + persistence) ───────────────
def test_switch_to_gjc_refused_when_flag_off(cloud, monkeypatch):
    monkeypatch.setenv("MAGI_CP_GJC_RUNTIME_ENABLED", "0")
    r = _admin(cloud["client"], "POST", "/tenants/default/runtime",
               json={"runtime_id": "gjc"})
    assert r.status_code == 403
    assert TenantRepo(cloud["engine"]).get_runtime("default") == "claude-code"


def test_switch_to_gjc_persists_when_flag_on(cloud, monkeypatch):
    monkeypatch.setenv("MAGI_CP_GJC_RUNTIME_ENABLED", "1")
    r = _admin(cloud["client"], "POST", "/tenants/default/runtime",
               json={"runtime_id": "gjc"})
    assert r.status_code == 200, r.text
    assert r.json()["runtime_id"] == "gjc"
    assert TenantRepo(cloud["engine"]).get_runtime("default") == "gjc"
    got = _admin(cloud["client"], "GET", "/tenants/default/runtime").json()
    assert got["runtime_id"] == "gjc"


def test_switch_back_to_cc_always_allowed_from_gjc(cloud, monkeypatch):
    monkeypatch.setenv("MAGI_CP_GJC_RUNTIME_ENABLED", "1")
    _admin(cloud["client"], "POST", "/tenants/default/runtime",
           json={"runtime_id": "gjc"})
    monkeypatch.delenv("MAGI_CP_GJC_RUNTIME_ENABLED", raising=False)
    r = _admin(cloud["client"], "POST", "/tenants/default/runtime",
               json={"runtime_id": "claude-code"})
    assert r.status_code == 200, r.text
    assert TenantRepo(cloud["engine"]).get_runtime("default") == "claude-code"


def test_gjc_flag_independent_of_codex_flag(cloud, monkeypatch):
    # gjc disabled must NOT block a codex switch (the two flags are
    # independent — the detection refactor's core invariant).
    monkeypatch.setenv("MAGI_CP_GJC_RUNTIME_ENABLED", "0")
    monkeypatch.setenv("MAGI_CP_CODEX_RUNTIME_ENABLED", "1")
    r = _admin(cloud["client"], "POST", "/tenants/default/runtime",
               json={"runtime_id": "codex"})
    assert r.status_code == 200, r.text
    assert TenantRepo(cloud["engine"]).get_runtime("default") == "codex"


def test_switch_gjc_requires_admin_key(cloud):
    r = cloud["client"].post(
        "/tenants/default/runtime", json={"runtime_id": "gjc"})
    assert r.status_code in (401, 403)
