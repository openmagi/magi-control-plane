"""P4 Codex runtime adapter: dashboard coverage + per-tenant runtime.

Design brief: docs/plans/2026-06-30-codex-runtime-adapter-design.md
Section 7 (dashboard changes) + Section 9.3 (feature-flag ladder).

Covered here:
  - GET /policies/{id}/coverage/{runtime} - per-policy strip data,
    green on CC, amber (downgraded) on Codex for a silent-skip tool.
  - GET /packs/{id}/coverage/{runtime} - per-pack rollup, mutually
    exclusive counts summing to the member total.
  - GET /tenants/{id}/runtime - picker state incl. codex_enabled flag
    (reflecting MAGI_CP_CODEX_RUNTIME_ENABLED) + per-runtime rollup.
  - POST /tenants/{id}/runtime - refuses codex with the flag off (403),
    accepts it with the flag on and persists tenants.runtime_id (E2E).
  - the admin key is required on every coverage/runtime read.
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


ADMIN_KEY = "p4-runtime-admin-key"
LEGACY_API_KEY = "p4-runtime-legacy-key"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", ADMIN_KEY)
    monkeypatch.setenv("MAGI_CP_API_KEY", LEGACY_API_KEY)
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", "p4-runtime-hitl-key")
    # Default ON (2026-07-01): unset means the adapter is available, so
    # tests here run codex-on unless they explicitly set the flag falsy.
    # The two disabled-path tests below set MAGI_CP_CODEX_RUNTIME_ENABLED=0.
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
    """Three archetypes: a natively-enforced Bash rule, a Read rule that
    maps onto a Codex silent-skip tool (amber downgrade), and a subagent
    policy (native-config-pending → red unsupported on Codex)."""
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
    # A user pack bundling the four seeded policies.
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
def test_policy_coverage_cc_is_enforced(cloud):
    r = _admin(cloud["client"], "GET", "/policies/ev/read/coverage/claude-code")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["policy_id"] == "ev/read"
    assert body["runtime_id"] == "claude-code"
    assert body["coverage"] == "enforced"
    assert body["status"] == "enforced"


def test_policy_coverage_codex_silent_skip_downgraded(cloud):
    # Read maps onto a Codex silent-skip tool → amber post-hoc audit.
    r = _admin(cloud["client"], "GET", "/policies/ev/read/coverage/codex")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["coverage"] == "downgraded"
    assert body["status"] == "codex_silent_skip"
    assert body["downgrade"]


def test_policy_coverage_codex_bash_enforced(cloud):
    r = _admin(cloud["client"], "GET", "/policies/ev/bash/coverage/codex")
    assert r.status_code == 200, r.text
    assert r.json()["coverage"] == "enforced"


def test_policy_coverage_unknown_runtime_404(cloud):
    r = _admin(cloud["client"], "GET", "/policies/ev/bash/coverage/cursor")
    assert r.status_code == 404


def test_policy_coverage_unknown_policy_404(cloud):
    r = _admin(cloud["client"], "GET", "/policies/nope/coverage/codex")
    assert r.status_code == 404


def test_policy_coverage_requires_admin_key(cloud):
    r = cloud["client"].get("/policies/ev/bash/coverage/codex")
    assert r.status_code in (401, 403)


# ── per-pack coverage rollup ─────────────────────────────────────────
def test_pack_coverage_rollup_counts_sum_to_total(cloud):
    r = _admin(cloud["client"], "GET",
               "/packs/user-pack/coding/coverage/codex")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pack_id"] == "user-pack/coding"
    assert body["total"] == 4
    total = (body["enforced"] + body["downgraded"]
             + body["unsupported"] + body["not_applicable"])
    assert total == body["total"]
    # ev/bash enforced; ev/read + ctx/cite downgraded; sub/researcher now
    # rides features.multi_agent + the spawn_agent hook (a compat fallback),
    # so it renders downgraded rather than unsupported (design 2026-07-01).
    assert body["enforced"] == 1
    assert body["downgraded"] == 3
    assert body["unsupported"] == 0


def test_pack_coverage_cc_all_enforced(cloud):
    r = _admin(cloud["client"], "GET",
               "/packs/user-pack/coding/coverage/claude-code")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enforced"] == 4
    assert body["downgraded"] == 0
    assert body["unsupported"] == 0


def test_pack_coverage_unknown_pack_404(cloud):
    r = _admin(cloud["client"], "GET",
               "/packs/user-pack/ghost/coverage/codex")
    assert r.status_code == 404


# ── per-tenant runtime picker state ──────────────────────────────────
def test_get_tenant_runtime_reports_disabled_when_flag_off(cloud, monkeypatch):
    # Default-ON flip (2026-07-01): the disabled path now requires an
    # explicit falsy token; unset means the adapter is available.
    monkeypatch.setenv("MAGI_CP_CODEX_RUNTIME_ENABLED", "0")
    r = _admin(cloud["client"], "GET", "/tenants/default/runtime")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["runtime_id"] == "claude-code"
    assert body["codex_enabled"] is False
    ids = {rt["id"] for rt in body["runtimes"]}
    assert ids == {"claude-code", "codex"}
    cc = next(rt for rt in body["runtimes"] if rt["id"] == "claude-code")
    assert cc["enforced"] == 4 and cc["total"] == 4


def test_get_tenant_runtime_reflects_codex_enabled(cloud, monkeypatch):
    monkeypatch.setenv("MAGI_CP_CODEX_RUNTIME_ENABLED", "1")
    r = _admin(cloud["client"], "GET", "/tenants/default/runtime")
    assert r.json()["codex_enabled"] is True


def test_picker_rollup_counts_floor_pack_members_with_empty_store(tmp_path):
    """Regression: a pack-centric tenant with an EMPTY policy_store but a
    built-in floor pack must not report "0 policies enforced". The picker
    rollup unions floor-pack members (resolved via the prebuilt catalog)
    so its total matches the per-pack coverage cards instead of
    under-reporting."""
    ks = KeyStore(dir=str(tmp_path / "keys"))
    dsn = f"sqlite:///{tmp_path}/cloud.sqlite"
    policy_path = str(tmp_path / "policies.json")
    pack_path = str(tmp_path / "packs.json")
    # Empty operator policy store.
    PolicyStore(path=policy_path).save([])
    # Floor pack made of two prebuilt members (no store rows exist for them).
    floor_members = [
        "prebuilt/citation-verify-at-final",
        "prebuilt/privilege-scan-bash",
    ]
    PackStore(path=pack_path).save([
        UserPackRow(
            id="user-pack/floor", name="Floor", description="",
            policy_ids=floor_members, is_floor=True,
        ),
    ])
    app = create_app(
        keystore=ks, dsn=dsn,
        policy_store_path=policy_path,
        pack_store_path=pack_path,
    )
    client = TestClient(app)
    body = _admin(client, "GET", "/tenants/default/runtime").json()
    cc = next(rt for rt in body["runtimes"] if rt["id"] == "claude-code")
    # CC enforces every policy natively: the picker now counts the two
    # floor-pack members instead of showing 0.
    assert cc["total"] == len(floor_members)
    assert cc["enforced"] == len(floor_members)


# ── runtime switch (feature-flag ladder + persistence) ───────────────
def test_switch_to_codex_refused_when_flag_off(cloud, monkeypatch):
    # Default-ON flip (2026-07-01): kill switch is an explicit falsy token.
    monkeypatch.setenv("MAGI_CP_CODEX_RUNTIME_ENABLED", "0")
    r = _admin(cloud["client"], "POST", "/tenants/default/runtime",
               json={"runtime_id": "codex"})
    assert r.status_code == 403
    # Not persisted.
    assert TenantRepo(cloud["engine"]).get_runtime("default") == "claude-code"


def test_switch_to_codex_persists_when_flag_on(cloud, monkeypatch):
    monkeypatch.setenv("MAGI_CP_CODEX_RUNTIME_ENABLED", "1")
    r = _admin(cloud["client"], "POST", "/tenants/default/runtime",
               json={"runtime_id": "codex"})
    assert r.status_code == 200, r.text
    assert r.json()["runtime_id"] == "codex"
    # E2E: the switch flipped tenants.runtime_id in the DB.
    assert TenantRepo(cloud["engine"]).get_runtime("default") == "codex"
    # And a subsequent read reflects it.
    got = _admin(cloud["client"], "GET", "/tenants/default/runtime").json()
    assert got["runtime_id"] == "codex"


def test_switch_back_to_cc_always_allowed(cloud, monkeypatch):
    # Put the tenant on codex first (flag on), then flip flag off and
    # revert - reverting to CC must not require the flag.
    monkeypatch.setenv("MAGI_CP_CODEX_RUNTIME_ENABLED", "1")
    _admin(cloud["client"], "POST", "/tenants/default/runtime",
           json={"runtime_id": "codex"})
    monkeypatch.delenv("MAGI_CP_CODEX_RUNTIME_ENABLED", raising=False)
    r = _admin(cloud["client"], "POST", "/tenants/default/runtime",
               json={"runtime_id": "claude-code"})
    assert r.status_code == 200, r.text
    assert TenantRepo(cloud["engine"]).get_runtime("default") == "claude-code"


def test_switch_unknown_runtime_400(cloud):
    r = _admin(cloud["client"], "POST", "/tenants/default/runtime",
               json={"runtime_id": "cursor"})
    assert r.status_code == 400


def test_runtime_routes_require_admin_key(cloud):
    assert cloud["client"].get("/tenants/default/runtime").status_code in (401, 403)
    assert cloud["client"].post(
        "/tenants/default/runtime", json={"runtime_id": "codex"},
    ).status_code in (401, 403)
