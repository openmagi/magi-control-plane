"""v1-P2 — /policies CRUD API."""
import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.keys import KeyStore


API_KEY = "p-api-key"
HITL_KEY = "p-hitl-key"
ADMIN_KEY = "p-admin-key"

ADMIN = {"X-Admin-Api-Key": ADMIN_KEY}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAGI_CP_API_KEY", API_KEY)
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", HITL_KEY)
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", ADMIN_KEY)


@pytest.fixture
def client(tmp_path):
    ks = KeyStore(dir=str(tmp_path / "keys"))
    app = create_app(keystore=ks, dsn="sqlite:///:memory:",
                     policy_store_path=str(tmp_path / "policies.json"))
    return TestClient(app)


@pytest.fixture
def client_with_registry(tmp_path):
    """P8: the production wiring — verifier_registry is supplied so
    PUT /policies/{id} resolves requires[].step against the live
    catalog (fail-closed on unknown / inactive)."""
    from magi_cp.verifier.builtins import register_builtins
    from magi_cp.verifier.protocol import VerifierRegistry
    ks = KeyStore(dir=str(tmp_path / "keys"))
    reg = VerifierRegistry()
    register_builtins(reg)
    app = create_app(keystore=ks, dsn="sqlite:///:memory:",
                     policy_store_path=str(tmp_path / "policies.json"),
                     verifier_registry=reg)
    return TestClient(app)


def _valid_policy(**override):
    # D57e P1: the (PreToolUse, citation_verify) combination this
    # fixture used to ship is exactly the lifecycle-drift case the
    # new gate refuses (citation_verify only fires on Stop). Swap to
    # privilege_scan (declares a PreToolUse field_checks group) so
    # the baseline policies-API tests exercise an endorsed
    # combination and the lifecycle gate only fires for tests that
    # explicitly opt in to the drift case.
    base = {
        "id": "legal-filing/v1",
        "description": "t",
        "version": "0.1",
        "trigger": {"host": "claude-code", "event": "PreToolUse", "matcher": "Bash"},
        "sentinel_re": r"FILE_COURT_(?P<matter>[A-Za-z0-9]+)_(?P<doc_id>[A-Za-z0-9]+)",
        "requires": [{"step": "privilege_scan", "verdict": "pass"}],
        "action": "block",
        "on_signature_invalid": "deny",
        "gate_binary": "/usr/local/bin/magi-gate.sh",
    }
    base.update(override)
    return base


def _put(client, pid, body, *, source="org", enabled=True):
    return client.put(f"/policies/{pid}",
                      json={"policy": body, "source": source, "enabled": enabled},
                      headers=ADMIN)


# ── auth ─────────────────────────────────────────────────────────────
def test_admin_endpoints_require_key(client):
    assert client.get("/policies").status_code == 401
    assert client.put("/policies/x", json={}).status_code == 401
    assert client.patch("/policies/x/enabled", json={"enabled": False}).status_code == 401
    assert client.get("/policies/x").status_code == 401
    assert client.get("/policies/x/compiled").status_code == 401


def test_admin_unset_env_fails_closed_503(client, monkeypatch):
    monkeypatch.delenv("MAGI_CP_ADMIN_API_KEY")
    r = client.get("/policies", headers={"X-Admin-Api-Key": "anything"})
    assert r.status_code == 503
    # round-2 review: env var name must NOT leak to caller.
    assert "MAGI_CP_ADMIN_API_KEY" not in r.text


def test_put_rejects_reserved_id_suffix(client):
    """policy id must not end with /compiled or /enabled (sibling-route collision)."""
    body = _valid_policy(id="foo/compiled")
    r = client.put("/policies/foo/compiled",
                   json={"policy": body, "source": "org", "enabled": True},
                   headers=ADMIN)
    assert r.status_code == 400
    assert "reserved" in r.json()["detail"].lower() or "compiled" in r.json()["detail"].lower()


# ── empty list ───────────────────────────────────────────────────────
def test_list_starts_empty(client):
    r = client.get("/policies", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["items"] == []


# ── create via PUT ───────────────────────────────────────────────────
def test_put_creates_policy(client):
    r = _put(client, "legal-filing/v1", _valid_policy())
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["id"] == "legal-filing/v1"
    assert j["source"] == "org"
    assert j["enabled"] is True


def test_put_rejects_id_mismatch(client):
    r = _put(client, "wrong/id", _valid_policy(id="legal-filing/v1"))
    assert r.status_code == 400
    assert "id mismatch" in r.json()["detail"].lower()


def test_put_rejects_illegal_matrix_combo(client):
    # D31: PostToolUse + Bash + block is illegal (post-event can't block).
    body = _valid_policy(
        trigger={"host": "claude-code", "event": "PostToolUse", "matcher": "Bash"},
        action="block",
    )
    r = _put(client, "legal-filing/v1", body)
    assert r.status_code == 400
    assert "illegal" in r.json()["detail"].lower()


def test_put_rejects_bad_source(client):
    r = client.put("/policies/x",
                   json={"policy": _valid_policy(id="x"),
                         "source": "ghost", "enabled": True},
                   headers=ADMIN)
    assert r.status_code == 422


# ── list / get / compiled ────────────────────────────────────────────
def test_list_after_put(client):
    _put(client, "legal-filing/v1", _valid_policy())
    r = client.get("/policies", headers=ADMIN)
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == "legal-filing/v1"
    assert items[0]["enabled"] is True
    assert items[0]["source"] == "org"
    assert items[0]["enforcement"]   # label present


def test_get_returns_resolved_view(client):
    _put(client, "legal-filing/v1", _valid_policy())
    r = client.get("/policies/legal-filing/v1", headers=ADMIN)
    assert r.status_code == 200
    j = r.json()
    assert j["id"] == "legal-filing/v1"
    assert j["policy"]["trigger"]["event"] == "PreToolUse"
    assert "compiled_sha256" in j


def test_get_unknown_returns_404(client):
    assert client.get("/policies/ghost", headers=ADMIN).status_code == 404


def test_compiled_returns_managed_settings(client):
    _put(client, "legal-filing/v1", _valid_policy())
    r = client.get("/policies/legal-filing/v1/compiled", headers=ADMIN)
    assert r.status_code == 200
    j = r.json()
    assert j["managed_settings"]["allowManagedHooksOnly"] is True
    assert j["managed_settings"]["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
    assert j["sha256"] and len(j["sha256"]) == 64


def test_compiled_same_input_same_sha256(client):
    """Deterministic compiler: same policy compiles to same sha256."""
    _put(client, "legal-filing/v1", _valid_policy())
    a = client.get("/policies/legal-filing/v1/compiled", headers=ADMIN).json()["sha256"]
    b = client.get("/policies/legal-filing/v1/compiled", headers=ADMIN).json()["sha256"]
    assert a == b


# ── patch enabled ────────────────────────────────────────────────────
def test_patch_enabled_toggles(client):
    _put(client, "legal-filing/v1", _valid_policy())
    r = client.patch("/policies/legal-filing/v1/enabled",
                     json={"enabled": False}, headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    again = client.get("/policies/legal-filing/v1", headers=ADMIN).json()
    assert again["enabled"] is False


def test_patch_enabled_unknown_404(client):
    r = client.patch("/policies/ghost/enabled",
                     json={"enabled": False}, headers=ADMIN)
    assert r.status_code == 404


# ── persistence ──────────────────────────────────────────────────────
def test_put_persists_across_app_restart(tmp_path):
    """Two TestClients sharing the same policy_store_path see the same data."""
    ks = KeyStore(dir=str(tmp_path / "keys"))
    psp = str(tmp_path / "policies.json")
    app1 = create_app(keystore=ks, dsn="sqlite:///:memory:", policy_store_path=psp)
    c1 = TestClient(app1)
    _put(c1, "legal-filing/v1", _valid_policy())

    app2 = create_app(keystore=ks, dsn="sqlite:///:memory:", policy_store_path=psp)
    c2 = TestClient(app2)
    items = c2.get("/policies", headers=ADMIN).json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == "legal-filing/v1"


# ── update existing keeps file deterministic ─────────────────────────
def test_put_overwrites_existing_same_id(client):
    _put(client, "x", _valid_policy(id="x", description="orig"))
    _put(client, "x", _valid_policy(id="x", description="updated"))
    j = client.get("/policies/x", headers=ADMIN).json()
    assert j["policy"]["description"] == "updated"
    # only 1 entry total — overwrite, not append
    items = client.get("/policies", headers=ADMIN).json()["items"]
    assert len(items) == 1


# ── P8: step IR fail-closed at REST layer ──────────────────────────────
def test_put_with_active_wired_step_returns_enforcing(client_with_registry):
    """citation_verify ships in register_builtins() — wired + active.
    PUT must accept it and stamp enforcement="enforcing"."""
    r = _put(client_with_registry, "legal-filing/v1", _valid_policy())
    assert r.status_code == 200, r.text
    assert r.json()["enforcement"] == "enforcing"
    # And the stamped label persists across reads.
    got = client_with_registry.get("/policies/legal-filing/v1",
                                    headers=ADMIN).json()
    assert got["enforcement"] == "enforcing"
    listed = client_with_registry.get("/policies", headers=ADMIN).json()["items"]
    assert listed[0]["enforcement"] == "enforcing"


def test_put_with_unwired_step_name_returns_422(client_with_registry):
    """A step that is NOT in the verifier registry AND NOT in the
    vendor catalog must reject with 422 "not in catalog" — this is the
    "missing" silent-fail path P8 closes."""
    body = _valid_policy(
        requires=[{"step": "definitely_not_a_real_verifier", "verdict": "pass"}],
    )
    r = _put(client_with_registry, "legal-filing/v1", body)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"].lower()
    assert "not in catalog" in detail
    assert "definitely_not_a_real_verifier" in detail
    # And nothing was persisted.
    listed = client_with_registry.get("/policies", headers=ADMIN).json()["items"]
    assert listed == []


def test_put_with_preview_prefix_returns_200_preview(client_with_registry):
    """`preview:` prefix is the explicit opt-in for in-development
    verifiers — the policy is accepted and enforcement stamped
    deterministically as "preview" so the dashboard can flag the row."""
    body = _valid_policy(
        requires=[{"step": "preview:my_new_verifier", "verdict": "pass"}],
    )
    r = _put(client_with_registry, "legal-filing/v1", body)
    assert r.status_code == 200, r.text
    assert r.json()["enforcement"] == "preview"
    # Stamped, not lazily re-derived per read.
    got = client_with_registry.get("/policies/legal-filing/v1",
                                    headers=ADMIN).json()
    assert got["enforcement"] == "preview"
    # The IR retains the prefix so a reader can tell it was authored
    # against an unwired verifier (not just downgraded after the fact).
    assert got["policy"]["requires"][0]["step"] == "preview:my_new_verifier"


def test_put_with_catalogued_but_inactive_step_returns_422(client_with_registry):
    """answer-quality / answer_quality is in the vendor catalog but has
    no live Verifier registered — must 422 "not active" with the
    activate-or-preview hint."""
    body = _valid_policy(
        requires=[{"step": "answer_quality", "verdict": "pass"}],
    )
    r = _put(client_with_registry, "legal-filing/v1", body)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"].lower()
    assert "not active" in detail
    assert "preview:" in detail or "/presets" in detail


def test_put_mixed_preview_and_enforcing_resolves_to_preview(client_with_registry):
    """If ANY req is preview, the policy-level label is preview — a
    single unwired condition blocks the gate from claiming the policy
    as a whole is enforcing."""
    # D57e P1: privilege_scan declares a PreToolUse field_checks
    # group, so the (PreToolUse, privilege_scan) combination passes
    # the lifecycle gate. The earlier fixture's citation_verify pair
    # was the lifecycle-drift case the new gate refuses.
    body = _valid_policy(
        requires=[
            {"step": "privilege_scan", "verdict": "pass"},
            {"step": "preview:future_check", "verdict": "pass"},
        ],
    )
    r = _put(client_with_registry, "legal-filing/v1", body)
    assert r.status_code == 200, r.text
    assert r.json()["enforcement"] == "preview"


def test_put_persists_enforcement_label_to_disk(tmp_path):
    """The label is stamped at PUT time and round-trips through the
    on-disk JSON store — a re-incarnated app does NOT re-resolve the
    label against whatever registry is wired at read time."""
    from magi_cp.verifier.builtins import register_builtins
    from magi_cp.verifier.protocol import VerifierRegistry
    ks = KeyStore(dir=str(tmp_path / "keys"))
    psp = str(tmp_path / "policies.json")

    # PUT with registry wired → "enforcing" stamped on disk.
    reg = VerifierRegistry()
    register_builtins(reg)
    app1 = create_app(keystore=ks, dsn="sqlite:///:memory:",
                       policy_store_path=psp, verifier_registry=reg)
    c1 = TestClient(app1)
    _put(c1, "legal-filing/v1", _valid_policy())

    # Re-open WITHOUT a registry — the stamped label still wins.
    app2 = create_app(keystore=ks, dsn="sqlite:///:memory:",
                       policy_store_path=psp, verifier_registry=None)
    c2 = TestClient(app2)
    got = c2.get("/policies/legal-filing/v1", headers=ADMIN).json()
    assert got["enforcement"] == "enforcing"


def test_put_without_registry_skips_strict_validation(client):
    """Hermetic-test path (no registry) keeps the legacy lenient
    behaviour — `citation_verify` is accepted even though there is no
    catalog to confirm it against. This is the back-compat seam that
    keeps every pre-P8 fixture working."""
    r = _put(client, "legal-filing/v1", _valid_policy())
    assert r.status_code == 200
    # Enforcement still surfaces — derived from the (action, event)
    # triple when no step resolution happened.
    assert r.json()["enforcement"] in ("enforcing", "deterministic-gate")


# ── P8 fix-cycle: tests for the follow-up findings ─────────────────────
def _write_legacy_policy_store(path, *, policy_id: str, step: str,
                                enabled: bool = True) -> None:
    """Hand-craft a pre-P8 on-disk policy row.

    Pre-P8 rows omit the `enforcement` field — the REST layer used to
    fall back to the legacy (action, event) label. The fix-cycle adds
    re-validation on read so a row whose step ref has been
    decommissioned renders `"unresolved-legacy"` and is treated as
    disabled at compile.
    """
    import json
    import os
    row = {
        "source": "org",
        "enabled": enabled,
        "policy": {
            "id": policy_id,
            "description": "legacy",
            "version": "0.1",
            "trigger": {"host": "claude-code", "event": "PreToolUse",
                        "matcher": "Bash"},
            "sentinel_re": r"FILE_COURT_(?P<matter>[A-Za-z0-9]+)_(?P<doc_id>[A-Za-z0-9]+)",
            "requires": [{"step": step, "verdict": "pass"}],
            "action": "block",
            "on_signature_invalid": "deny",
            "gate_binary": "/usr/local/bin/magi-gate.sh",
        },
        # NOTE: no "enforcement" key — that is the pre-P8 shape.
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([row], f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def test_legacy_row_with_unresolved_step_renders_unresolved_label(tmp_path):
    """Fix-cycle #1 P0 + #7: a pre-P8 row referencing a step that does
    not exist in the live registry MUST render `"unresolved-legacy"`
    on /policies and /policies/{id}. The pre-fix behaviour silently
    fell back to `"deterministic-gate"` — a false safety signal."""
    from magi_cp.verifier.builtins import register_builtins
    from magi_cp.verifier.protocol import VerifierRegistry
    psp = str(tmp_path / "policies.json")
    _write_legacy_policy_store(psp, policy_id="ghost-policy/v1",
                                step="ghost_step_does_not_exist")

    ks = KeyStore(dir=str(tmp_path / "keys"))
    reg = VerifierRegistry()
    register_builtins(reg)
    app = create_app(keystore=ks, dsn="sqlite:///:memory:",
                     policy_store_path=psp, verifier_registry=reg)
    c = TestClient(app)

    # /policies list re-validates the legacy row.
    listed = c.get("/policies", headers=ADMIN).json()["items"]
    assert len(listed) == 1
    assert listed[0]["enforcement"] == "unresolved-legacy"

    # /policies/{id} agrees with the list.
    got = c.get("/policies/ghost-policy/v1", headers=ADMIN).json()
    assert got["enforcement"] == "unresolved-legacy"

    # The compile path still works (no crash); it is the dashboard's
    # job to surface that the row is gated. The runtime safety net is
    # the gate binary 404'ing on the unwired verifier.
    comp = c.get("/policies/ghost-policy/v1/compiled", headers=ADMIN)
    assert comp.status_code == 200


def test_legacy_row_with_active_step_renders_enforcing(tmp_path):
    """Fix-cycle #1 + #7: when the step still resolves cleanly, the
    on-read re-validation stamps `"enforcing"` (instead of the legacy
    `"deterministic-gate"` lazy label). This is the success case for
    a pre-P8 row that survived a registry change unscathed."""
    from magi_cp.verifier.builtins import register_builtins
    from magi_cp.verifier.protocol import VerifierRegistry
    psp = str(tmp_path / "policies.json")
    _write_legacy_policy_store(psp, policy_id="ok-policy/v1",
                                step="citation_verify")

    ks = KeyStore(dir=str(tmp_path / "keys"))
    reg = VerifierRegistry()
    register_builtins(reg)
    app = create_app(keystore=ks, dsn="sqlite:///:memory:",
                     policy_store_path=psp, verifier_registry=reg)
    c = TestClient(app)

    got = c.get("/policies/ok-policy/v1", headers=ADMIN).json()
    assert got["enforcement"] == "enforcing"


def test_overwrite_legacy_row_with_unwired_step_returns_422(tmp_path):
    """Fix-cycle #7: PUT re-arms the strict gate. A row that was
    grandfathered on disk does NOT acquire a permanent waiver — an
    operator re-authoring the row must either pick a wired step or
    use the `preview:` prefix. This is the "cloud got stricter"
    semantics; without it, a stale row could pin a bad step name in
    the policy store forever."""
    from magi_cp.verifier.builtins import register_builtins
    from magi_cp.verifier.protocol import VerifierRegistry
    psp = str(tmp_path / "policies.json")
    _write_legacy_policy_store(psp, policy_id="ghost-policy/v1",
                                step="ghost_step_does_not_exist")

    ks = KeyStore(dir=str(tmp_path / "keys"))
    reg = VerifierRegistry()
    register_builtins(reg)
    app = create_app(keystore=ks, dsn="sqlite:///:memory:",
                     policy_store_path=psp, verifier_registry=reg)
    c = TestClient(app)

    # PUT-overwrite the legacy row keeping the same bad step — the
    # strict P8 gate kicks in and rejects with 422 "not in catalog".
    bad_body = _valid_policy(
        id="ghost-policy/v1",
        requires=[{"step": "ghost_step_does_not_exist", "verdict": "pass"}],
    )
    r = c.put("/policies/ghost-policy/v1",
              json={"policy": bad_body, "source": "org", "enabled": True},
              headers=ADMIN)
    assert r.status_code == 422, r.text
    assert "not in catalog" in r.json()["detail"].lower()


def test_patch_enabled_true_with_decommissioned_step_returns_409(tmp_path):
    """Fix-cycle #4 P1 + #8: an operator toggling a stale row back on
    must hit a 409 conflict, not a silent re-arm. The row's step was
    valid at PUT time but the verifier has since been
    decommissioned — re-enabling without a re-author would silently
    ship a hook for an unwired verifier."""
    from magi_cp.verifier.builtins import register_builtins
    from magi_cp.verifier.protocol import VerifierRegistry
    psp = str(tmp_path / "policies.json")
    _write_legacy_policy_store(psp, policy_id="stale-policy/v1",
                                step="ghost_step_does_not_exist",
                                enabled=False)

    ks = KeyStore(dir=str(tmp_path / "keys"))
    reg = VerifierRegistry()
    register_builtins(reg)
    app = create_app(keystore=ks, dsn="sqlite:///:memory:",
                     policy_store_path=psp, verifier_registry=reg)
    c = TestClient(app)

    # Disabling (enabled=False) is metadata-only — no re-resolve.
    r_off = c.patch("/policies/stale-policy/v1/enabled",
                     json={"enabled": False}, headers=ADMIN)
    assert r_off.status_code == 200

    # Re-enabling re-resolves and 409s on the unwired step.
    r_on = c.patch("/policies/stale-policy/v1/enabled",
                    json={"enabled": True}, headers=ADMIN)
    assert r_on.status_code == 409, r_on.text
    body = r_on.json()["detail"].lower()
    assert "ghost_step_does_not_exist" in body
    assert "preview:" in body or "no longer registered" in body


def test_patch_enabled_true_with_active_step_succeeds(client_with_registry):
    """Fix-cycle #4: the 409 only fires for unresolved steps. A row
    whose step is still wired toggles enabled cleanly with no surprise
    400/409."""
    _put(client_with_registry, "fine-policy/v1", _valid_policy(id="fine-policy/v1"))
    # Toggle off.
    r_off = client_with_registry.patch("/policies/fine-policy/v1/enabled",
                                        json={"enabled": False}, headers=ADMIN)
    assert r_off.status_code == 200
    # Toggle back on — citation_verify still wired.
    r_on = client_with_registry.patch("/policies/fine-policy/v1/enabled",
                                       json={"enabled": True}, headers=ADMIN)
    assert r_on.status_code == 200
    assert r_on.json()["enabled"] is True


def test_stamped_enforcement_survives_registry_drop(tmp_path):
    """Fix-cycle #8: pin the stamped-at-PUT-time semantics.

    The module docstring explicitly promises "stable record of what
    was authored, not what is now wired". A future refactor that
    re-resolves on every read would fail this test, surfacing the
    semantic change before it lands."""
    from magi_cp.verifier.builtins import register_builtins
    from magi_cp.verifier.protocol import VerifierRegistry
    ks = KeyStore(dir=str(tmp_path / "keys"))
    psp = str(tmp_path / "policies.json")

    # PUT with the full registry — stamp `"enforcing"`.
    reg = VerifierRegistry()
    register_builtins(reg)
    app1 = create_app(keystore=ks, dsn="sqlite:///:memory:",
                       policy_store_path=psp, verifier_registry=reg)
    c1 = TestClient(app1)
    _put(c1, "stable-policy/v1", _valid_policy(id="stable-policy/v1"))

    # Simulate a registry drop: re-open with an EMPTY registry.
    empty_reg = VerifierRegistry()
    app2 = create_app(keystore=ks, dsn="sqlite:///:memory:",
                       policy_store_path=psp,
                       verifier_registry=empty_reg)
    c2 = TestClient(app2)

    # Stamped label survives. We are NOT re-resolving on read for
    # stamped rows — that is the deliberate semantic.
    listed = c2.get("/policies", headers=ADMIN).json()["items"]
    assert listed[0]["enforcement"] == "enforcing"
    got = c2.get("/policies/stable-policy/v1", headers=ADMIN).json()
    assert got["enforcement"] == "enforcing"


def test_inactive_step_becomes_enforcing_after_activation(tmp_path):
    """Fix-cycle #8: a step that 422s today as `inactive` becomes
    `enforcing` after the operator activates the verifier (registers
    it under the live registry). Pins the "path of least resistance"
    in the dashboard so a future regression that breaks the
    activation→PUT loop is caught here."""
    from magi_cp.verifier.builtins import register_builtins
    from magi_cp.verifier.protocol import VerifierRegistry
    ks = KeyStore(dir=str(tmp_path / "keys"))
    psp = str(tmp_path / "policies.json")

    # Start with builtins only — answer_quality is in the vendor
    # catalog but NOT in the registry → first PUT 422s "not active".
    reg = VerifierRegistry()
    register_builtins(reg)
    app = create_app(keystore=ks, dsn="sqlite:///:memory:",
                     policy_store_path=psp, verifier_registry=reg)
    c = TestClient(app)
    body = _valid_policy(
        id="activated/v1",
        requires=[{"step": "answer_quality", "verdict": "pass"}],
    )
    r1 = c.put("/policies/activated/v1",
                json={"policy": body, "source": "org", "enabled": True},
                headers=ADMIN)
    assert r1.status_code == 422, r1.text
    assert "not active" in r1.json()["detail"].lower()

    # Activate answer_quality: rebuild the app with a registry that
    # also has a stub answer_quality verifier registered.
    from magi_cp.verifier.protocol import Enforcement, Verdict

    class _StubAnswerQuality:
        name = "answer_quality"
        step = "answer_quality"
        category = "ANSWER"
        description = "stub for test"
        enforcement = Enforcement.enforcing
        input_schema: dict = {}

        def run(self, payload):  # pragma: no cover
            return Verdict(status="pass")

    reg2 = VerifierRegistry()
    register_builtins(reg2)
    reg2.register(_StubAnswerQuality())
    app2 = create_app(keystore=ks, dsn="sqlite:///:memory:",
                       policy_store_path=psp, verifier_registry=reg2)
    c2 = TestClient(app2)
    r2 = c2.put("/policies/activated/v1",
                 json={"policy": body, "source": "org", "enabled": True},
                 headers=ADMIN)
    assert r2.status_code == 200, r2.text
    assert r2.json()["enforcement"] == "enforcing"


def test_create_app_requires_registry_when_env_set(tmp_path, monkeypatch):
    """Fix-cycle #2 P0: when `MAGI_CP_REQUIRE_REGISTRY=1` the factory
    refuses to construct without a registry. This is the deploy-shape
    invariant the prod Helm chart sets so a regression that
    drops the registry wire fails at boot, not at the first PUT."""
    monkeypatch.setenv("MAGI_CP_REQUIRE_REGISTRY", "1")
    ks = KeyStore(dir=str(tmp_path / "keys"))
    with pytest.raises(RuntimeError) as ei:
        create_app(keystore=ks, dsn="sqlite:///:memory:",
                   policy_store_path=str(tmp_path / "policies.json"),
                   verifier_registry=None)
    # Operator-visible message names the env var so an operator can
    # disable the gate if they really mean to construct a registry-less
    # factory.
    assert "MAGI_CP_REQUIRE_REGISTRY" in str(ei.value)


# ── Issue #1 P0 (#12, #13, #14): native-surface archetypes via REST ──


def test_put_permission_policy_round_trips(client_with_registry):
    """Issue #1 P0 (#12): PUT /policies accepts a discriminated-union
    body, persists the archetype, and returns the matching shape on
    GET. Pre-P2 evidence shape still works because `type` defaults
    to evidence."""
    pid = "block-rm-rf/v1"
    body = {
        "type": "permission",
        "id": pid,
        "description": "block destructive Bash",
        "version": "0.1",
        "trigger": {"host": "claude-code",
                     "event": "PreToolUse", "matcher": "Bash"},
        "permission": "deny",
        "pattern": "Bash(rm -rf /*)",
    }
    r = _put(client_with_registry, pid, body)
    assert r.status_code == 200, r.text
    assert r.json()["type"] == "permission"
    assert r.json()["enforcement"] == "enforcing"

    detail = client_with_registry.get(f"/policies/{pid}",
                                       headers=ADMIN).json()
    assert detail["policy"]["type"] == "permission"
    assert detail["policy"]["pattern"] == "Bash(rm -rf /*)"
    assert detail["enforcement"] == "enforcing"


def test_put_subagent_policy_round_trips(client_with_registry):
    """Issue #1 P0 (#9, #12): SubagentPolicy persists as a binary
    disable; the request's empty tool_allowlist must round-trip."""
    pid = "disable-research/v1"
    body = {
        "type": "subagent",
        "id": pid,
        "description": "research subagent disabled",
        "subagent_type": "research",
        "tool_allowlist": [],
    }
    r = _put(client_with_registry, pid, body)
    assert r.status_code == 200, r.text

    detail = client_with_registry.get(f"/policies/{pid}",
                                       headers=ADMIN).json()
    assert detail["policy"]["type"] == "subagent"
    # Compiled-managed-settings exposes the Agent(name) deny rule.
    compiled = client_with_registry.get(f"/policies/{pid}/compiled",
                                         headers=ADMIN).json()
    assert "Agent(research)" in compiled["managed_settings"]["permissions"]["deny"]


def test_put_mcp_gating_policy_round_trips(client_with_registry):
    pid = "deny-mcp-github/v1"
    body = {
        "type": "mcp_gating",
        "id": pid,
        "description": "GitHub MCP off",
        "server": "github",
        "action": "deny",
    }
    r = _put(client_with_registry, pid, body)
    assert r.status_code == 200, r.text

    compiled = client_with_registry.get(f"/policies/{pid}/compiled",
                                         headers=ADMIN).json()
    assert compiled["managed_settings"]["deniedMcpServers"] == [
        {"serverName": "github"}
    ]


def test_put_input_rewrite_policy_round_trips(client_with_registry):
    """D57f-2: an InputRewritePolicy PUT round-trips through the cloud
    + compiles into a PreToolUse hook command that names the policy
    id (and NOT the rewriter literal value)."""
    pid = "strip-sudo-from-bash/v1"
    body = {
        "type": "input_rewrite",
        "id": pid,
        "description": "strip sudo from bash commands",
        "trigger": {"host": "claude-code", "event": "PreToolUse",
                     "matcher": "Bash"},
        "rewriter": {
            "kind": "prefix_strip",
            "config": {"field": "command", "prefix": "sudo "},
        },
    }
    r = _put(client_with_registry, pid, body)
    assert r.status_code == 200, r.text

    got = client_with_registry.get(f"/policies/{pid}", headers=ADMIN).json()
    assert got["policy"]["type"] == "input_rewrite"
    assert got["policy"]["rewriter"]["config"]["prefix"] == "sudo "

    compiled = client_with_registry.get(f"/policies/{pid}/compiled",
                                         headers=ADMIN).json()
    hooks = compiled["managed_settings"]["hooks"]["PreToolUse"]
    assert hooks[0]["matcher"] == "Bash"
    cmd = hooks[0]["hooks"][0]
    assert cmd["type"] == "command"
    assert "magi-cp-input-rewrite" in cmd["command"]
    assert pid in cmd["command"]
    # Rewriter literal MUST NOT leak into the managed-settings hook.
    assert "sudo " not in cmd["command"]


def test_input_rewrite_runtime_endpoint_returns_updated_input(client_with_registry):
    """The local shim POSTs (policy_id, tool_name, tool_input); cloud
    looks up the InputRewritePolicy and applies the rewriter."""
    pid = "strip-sudo-from-bash/v1"
    body = {
        "type": "input_rewrite",
        "id": pid,
        "description": "",
        "trigger": {"host": "claude-code", "event": "PreToolUse",
                     "matcher": "Bash"},
        "rewriter": {
            "kind": "prefix_strip",
            "config": {"field": "command", "prefix": "sudo "},
        },
    }
    r = _put(client_with_registry, pid, body)
    assert r.status_code == 200, r.text

    rr = client_with_registry.post(
        "/policies/input_rewrite",
        # P1 follow-up: the autouse `_env` fixture sets MAGI_CP_API_KEY,
        # which now activates the optional auth on the endpoint. Pass
        # the same value as the shim would; the no-env path is covered
        # by `test_input_rewrite_endpoint_open_when_api_key_env_unset`.
        headers={"X-Api-Key": API_KEY},
        json={
            "policy_id": pid,
            "tool_name": "Bash",
            "tool_input": {"command": "sudo apt update", "other": 1},
        },
    )
    assert rr.status_code == 200, rr.text
    data = rr.json()
    assert data["rewrote"] is True
    assert data["updated_input"]["command"] == "apt update"
    # Unrelated input fields preserved.
    assert data["updated_input"]["other"] == 1


def test_input_rewrite_endpoint_refuses_wrong_tool(client_with_registry):
    pid = "wrong-tool/v1"
    body = {
        "type": "input_rewrite",
        "id": pid,
        "description": "",
        "trigger": {"host": "claude-code", "event": "PreToolUse",
                     "matcher": "Bash"},
        "rewriter": {
            "kind": "prefix_strip",
            "config": {"field": "command", "prefix": "sudo "},
        },
    }
    assert _put(client_with_registry, pid, body).status_code == 200
    rr = client_with_registry.post(
        "/policies/input_rewrite",
        headers={"X-Api-Key": API_KEY},
        json={
            "policy_id": pid,
            "tool_name": "Edit",
            "tool_input": {"command": "sudo apt update"},
        },
    )
    assert rr.status_code == 200
    assert rr.json() == {"rewrote": False}


def test_input_rewrite_endpoint_unknown_policy_id(client_with_registry):
    rr = client_with_registry.post(
        "/policies/input_rewrite",
        headers={"X-Api-Key": API_KEY},
        json={
            "policy_id": "does-not-exist/v1",
            "tool_name": "Bash",
            "tool_input": {"command": "sudo ls"},
        },
    )
    assert rr.status_code == 200
    assert rr.json() == {"rewrote": False}


def test_input_rewrite_endpoint_caps_oversize_tool_input_value(client_with_registry):
    """A 64KB+ string value inside `tool_input` must be refused at
    the boundary so the regex engine never sees a pathological
    target. Returns 422 (FastAPI / pydantic validation)."""
    pid = "cap-strip/v1"
    body = {
        "type": "input_rewrite",
        "id": pid,
        "description": "",
        "trigger": {"host": "claude-code", "event": "PreToolUse",
                     "matcher": "Bash"},
        "rewriter": {
            "kind": "prefix_strip",
            "config": {"field": "command", "prefix": "sudo "},
        },
    }
    assert _put(client_with_registry, pid, body).status_code == 200
    huge = "x" * (64 * 1024 + 1)
    rr = client_with_registry.post(
        "/policies/input_rewrite",
        headers={"X-Api-Key": API_KEY},
        json={
            "policy_id": pid,
            "tool_name": "Bash",
            "tool_input": {"command": huge},
        },
    )
    # pydantic's `field_validator` raise → FastAPI returns 422.
    assert rr.status_code == 422


def test_input_rewrite_endpoint_optional_auth_when_env_set(
    client_with_registry, monkeypatch,
):
    """When `MAGI_CP_API_KEY` is set, the endpoint requires a
    matching `X-Api-Key` header. Closes the unauthenticated probe
    surface that leaked policy id existence + rewriter semantics."""
    monkeypatch.setenv("MAGI_CP_API_KEY", "right-key")
    pid = "auth-required/v1"
    body = {
        "type": "input_rewrite",
        "id": pid,
        "description": "",
        "trigger": {"host": "claude-code", "event": "PreToolUse",
                     "matcher": "Bash"},
        "rewriter": {
            "kind": "prefix_strip",
            "config": {"field": "command", "prefix": "sudo "},
        },
    }
    assert _put(client_with_registry, pid, body).status_code == 200
    # No header → 401.
    rr = client_with_registry.post(
        "/policies/input_rewrite",
        json={
            "policy_id": pid,
            "tool_name": "Bash",
            "tool_input": {"command": "sudo ls"},
        },
    )
    assert rr.status_code == 401
    # Wrong header → 401.
    rr = client_with_registry.post(
        "/policies/input_rewrite",
        headers={"X-Api-Key": "wrong"},
        json={
            "policy_id": pid,
            "tool_name": "Bash",
            "tool_input": {"command": "sudo ls"},
        },
    )
    assert rr.status_code == 401
    # Correct header → 200 + rewrite happens.
    rr = client_with_registry.post(
        "/policies/input_rewrite",
        headers={"X-Api-Key": "right-key"},
        json={
            "policy_id": pid,
            "tool_name": "Bash",
            "tool_input": {"command": "sudo ls"},
        },
    )
    assert rr.status_code == 200
    assert rr.json()["rewrote"] is True


def test_input_rewrite_endpoint_open_when_api_key_env_unset(
    client_with_registry, monkeypatch,
):
    """Defaults: env unset → endpoint accepts anonymous calls so the
    local-gate loopback dev loop still works."""
    monkeypatch.delenv("MAGI_CP_API_KEY", raising=False)
    pid = "open-anon/v1"
    body = {
        "type": "input_rewrite",
        "id": pid,
        "description": "",
        "trigger": {"host": "claude-code", "event": "PreToolUse",
                     "matcher": "Bash"},
        "rewriter": {
            "kind": "prefix_strip",
            "config": {"field": "command", "prefix": "sudo "},
        },
    }
    assert _put(client_with_registry, pid, body).status_code == 200
    rr = client_with_registry.post(
        "/policies/input_rewrite",
        json={
            "policy_id": pid,
            "tool_name": "Bash",
            "tool_input": {"command": "sudo ls"},
        },
    )
    assert rr.status_code == 200
    assert rr.json()["rewrote"] is True


def test_put_input_rewrite_rejects_post_tool_use(client_with_registry):
    """Authoring-time gate: PostToolUse can't carry an input_rewrite."""
    pid = "bad-event/v1"
    body = {
        "type": "input_rewrite",
        "id": pid,
        "description": "",
        "trigger": {"host": "claude-code", "event": "PostToolUse",
                     "matcher": "Bash"},
        "rewriter": {
            "kind": "prefix_strip",
            "config": {"field": "command", "prefix": "sudo "},
        },
    }
    r = _put(client_with_registry, pid, body)
    assert r.status_code == 400
    assert "PreToolUse" in r.json()["detail"]


def test_put_context_injection_policy_round_trips(client_with_registry):
    pid = "team-context/v1"
    body = {
        "type": "context_injection",
        "id": pid,
        "description": "team standards",
        "event": "UserPromptSubmit",
        "matcher": "*",
        "template": "Follow team standards: TDD.",
    }
    r = _put(client_with_registry, pid, body)
    assert r.status_code == 200, r.text

    compiled = client_with_registry.get(f"/policies/{pid}/compiled",
                                         headers=ADMIN).json()
    hook = compiled["managed_settings"]["hooks"]["UserPromptSubmit"][0]
    # Issue #1 P0 (#3, #8): real CC hook type is `command`, not `write`.
    assert hook["hooks"][0]["type"] == "command"


def test_list_policies_handles_mixed_archetypes(client_with_registry):
    """Issue #1 P0 (#13, #14): GET /policies must not crash when the
    store contains non-Evidence rows."""
    bodies = [
        {
            "type": "permission",
            "id": "block-rm-rf/v1", "description": "",
            "trigger": {"host": "claude-code",
                         "event": "PreToolUse", "matcher": "Bash"},
            "permission": "deny", "pattern": "Bash(rm -rf /*)",
        },
        {
            "type": "subagent",
            "id": "disable-research/v1", "description": "",
            "subagent_type": "research", "tool_allowlist": [],
        },
        {
            "type": "mcp_gating",
            "id": "deny-mcp-github/v1", "description": "",
            "server": "github", "action": "deny",
        },
        _valid_policy(),  # evidence
    ]
    for b in bodies:
        r = _put(client_with_registry, b["id"], b)
        assert r.status_code == 200, r.text

    items = client_with_registry.get("/policies", headers=ADMIN).json()["items"]
    types = {i["id"]: i.get("type") for i in items}
    assert types["block-rm-rf/v1"] == "permission"
    assert types["disable-research/v1"] == "subagent"
    assert types["deny-mcp-github/v1"] == "mcp_gating"
    # Non-event-scoped rows render WITHOUT a `trigger` key (Issue #14).
    sub = next(i for i in items if i["id"] == "disable-research/v1")
    assert "trigger" not in sub


def test_put_permission_policy_rejects_malformed_pattern(client_with_registry):
    """Issue #1 P1 (#7): the cloud rejects a pattern that doesn't match
    the CC permission grammar — catches authoring mistakes at PUT
    time instead of silently shipping a dead rule."""
    pid = "bad/v1"
    body = {
        "type": "permission",
        "id": pid, "description": "",
        "trigger": {"host": "claude-code",
                     "event": "PreToolUse", "matcher": "Bash"},
        "permission": "deny",
        "pattern": "garbage(( no verb",
    }
    r = _put(client_with_registry, pid, body)
    assert r.status_code == 400
    assert "permission grammar" in r.json()["detail"]
