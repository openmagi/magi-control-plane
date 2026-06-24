"""D52b: /custom-verifiers REST + custom_verifier_store unit tests.

The dashboard's /verifiers/new page POSTs here. Slug validation, trigger
shape, and verdict membership are enforced both in the store helper
(unit-tested below) and at the route boundary (so a hand-rolled client
cannot bypass the dashboard's client-side validation).
"""
import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.custom_verifier_store import (
    CustomVerifierError, CustomVerifierStore, build_from_dict, validate_name,
    validate_description, validate_triggers, validate_verdict_set,
    validate_body_type,
)
from magi_cp.cloud.keys import KeyStore


API_KEY = "cv-api-key"
HITL_KEY = "cv-hitl-key"
ADMIN_KEY = "cv-admin-key"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAGI_CP_API_KEY", API_KEY)
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", HITL_KEY)
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", ADMIN_KEY)


@pytest.fixture
def client(tmp_path):
    ks = KeyStore(dir=str(tmp_path / "keys"))
    app = create_app(
        keystore=ks,
        dsn="sqlite:///:memory:",
        policy_store_path=str(tmp_path / "policies.json"),
        custom_verifier_store_path=str(tmp_path / "custom_verifiers.json"),
    )
    return TestClient(app)


def _valid_body(**override):
    base = {
        "name": "my_custom_check",
        "description": "Checks something",
        "triggers": [
            {"event": "PreToolUse", "matcher_class": "tool"},
        ],
        "verdict_set": ["pass", "fail"],
        "body_type": "preview",
    }
    base.update(override)
    return base


# ── store-level unit tests (no HTTP) ─────────────────────────────────
class TestStoreValidators:
    def test_validate_name_accepts_slug(self):
        validate_name("ok_name")
        validate_name("a")
        validate_name("a1_b2")

    def test_validate_name_rejects_uppercase(self):
        with pytest.raises(CustomVerifierError):
            validate_name("BadName")

    def test_validate_name_rejects_leading_digit(self):
        with pytest.raises(CustomVerifierError):
            validate_name("9foo")

    def test_validate_name_rejects_hyphen(self):
        with pytest.raises(CustomVerifierError):
            validate_name("foo-bar")

    def test_validate_name_rejects_empty(self):
        with pytest.raises(CustomVerifierError):
            validate_name("")

    def test_validate_name_rejects_overlong(self):
        with pytest.raises(CustomVerifierError):
            validate_name("a" * 65)

    def test_validate_description_rejects_empty(self):
        with pytest.raises(CustomVerifierError):
            validate_description("")
        with pytest.raises(CustomVerifierError):
            validate_description("   ")

    def test_validate_description_rejects_overlong(self):
        with pytest.raises(CustomVerifierError):
            validate_description("x" * 501)

    def test_validate_triggers_rejects_empty(self):
        with pytest.raises(CustomVerifierError):
            validate_triggers([])

    def test_validate_triggers_rejects_bad_matcher_class(self):
        with pytest.raises(CustomVerifierError):
            validate_triggers([{"event": "PreToolUse", "matcher_class": "wildcard"}])

    def test_validate_triggers_rejects_missing_event(self):
        with pytest.raises(CustomVerifierError):
            validate_triggers([{"matcher_class": "tool"}])

    def test_validate_verdict_set_rejects_empty(self):
        with pytest.raises(CustomVerifierError):
            validate_verdict_set([])

    def test_validate_verdict_set_rejects_unknown(self):
        with pytest.raises(CustomVerifierError):
            validate_verdict_set(["pass", "magic"])

    def test_validate_verdict_set_dedupes(self):
        out = validate_verdict_set(["pass", "pass", "fail"])
        assert out == ("pass", "fail")

    def test_validate_body_type_only_preview(self):
        assert validate_body_type("preview") == "preview"
        with pytest.raises(CustomVerifierError):
            validate_body_type("regex")

    def test_build_from_dict_happy_path(self):
        v = build_from_dict({
            "name": "x",
            "description": "y",
            "triggers": [{"event": "Stop", "matcher_class": "final"}],
            "verdict_set": ["pass"],
            "body_type": "preview",
        })
        assert v.name == "x"
        assert v.description == "y"
        assert v.triggers[0].event == "Stop"
        assert v.verdict_set == ("pass",)
        assert v.body_type == "preview"
        # server-issued id, not from input
        assert v.id and isinstance(v.id, str)
        assert len(v.id) == 16  # 8 bytes hex


class TestStorePersistence:
    def test_add_then_get(self, tmp_path):
        store = CustomVerifierStore(path=str(tmp_path / "cv.json"))
        v = build_from_dict({
            "name": "x", "description": "y",
            "triggers": [{"event": "Stop", "matcher_class": "final"}],
            "verdict_set": ["pass"],
            "body_type": "preview",
        })
        stored = store.add("tenant-a", v)
        assert stored.tenant_id == "tenant-a"
        got = store.get("tenant-a", stored.id)
        assert got is not None
        assert got.name == "x"

    def test_get_returns_none_when_unknown_tenant(self, tmp_path):
        store = CustomVerifierStore(path=str(tmp_path / "cv.json"))
        assert store.get("nobody", "deadbeefdeadbeef") is None

    def test_tenant_isolation_on_get(self, tmp_path):
        # Tenant B must not see Tenant A's verifier.
        store = CustomVerifierStore(path=str(tmp_path / "cv.json"))
        v = build_from_dict({
            "name": "secret_check", "description": "y",
            "triggers": [{"event": "Stop", "matcher_class": "final"}],
            "verdict_set": ["pass"],
            "body_type": "preview",
        })
        stored = store.add("tenant-a", v)
        assert store.get("tenant-b", stored.id) is None

    def test_list_for_tenant_scopes(self, tmp_path):
        store = CustomVerifierStore(path=str(tmp_path / "cv.json"))
        for tenant in ("tenant-a", "tenant-b"):
            v = build_from_dict({
                "name": f"check_{tenant.replace('-', '_')}",
                "description": "y",
                "triggers": [{"event": "Stop", "matcher_class": "final"}],
                "verdict_set": ["pass"],
                "body_type": "preview",
            })
            store.add(tenant, v)
        a = store.list_for_tenant("tenant-a")
        b = store.list_for_tenant("tenant-b")
        assert len(a) == 1 and len(b) == 1
        assert a[0].name != b[0].name


# ── HTTP route tests ────────────────────────────────────────────────
class TestPostCustomVerifier:
    def test_requires_tenant_auth(self, client):
        r = client.post("/custom-verifiers", json=_valid_body())
        assert r.status_code == 401

    def test_happy_path(self, client):
        r = client.post(
            "/custom-verifiers",
            json=_valid_body(),
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == "my_custom_check"
        assert body["body_type"] == "preview"
        assert body["verdict_set"] == ["pass", "fail"]
        assert body["id"]

    def test_rejects_bad_slug(self, client):
        for bad in ("BadName", "9foo", "foo-bar", "", "X" * 65):
            r = client.post(
                "/custom-verifiers",
                json=_valid_body(name=bad),
                headers={"X-Api-Key": API_KEY},
            )
            assert r.status_code == 422, (bad, r.text)

    def test_rejects_zero_triggers(self, client):
        r = client.post(
            "/custom-verifiers",
            json=_valid_body(triggers=[]),
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 422

    def test_rejects_bad_matcher_class(self, client):
        r = client.post(
            "/custom-verifiers",
            json=_valid_body(triggers=[{"event": "Stop", "matcher_class": "wildcard"}]),
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 422

    def test_rejects_empty_verdict_set(self, client):
        r = client.post(
            "/custom-verifiers",
            json=_valid_body(verdict_set=[]),
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 422

    def test_rejects_unknown_verdict(self, client):
        r = client.post(
            "/custom-verifiers",
            json=_valid_body(verdict_set=["pass", "kaboom"]),
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 422

    def test_rejects_non_preview_body_type(self, client):
        r = client.post(
            "/custom-verifiers",
            json=_valid_body(body_type="regex"),
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 422

    def test_rejects_overlong_description(self, client):
        r = client.post(
            "/custom-verifiers",
            json=_valid_body(description="x" * 501),
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 422


class TestGetCustomVerifier:
    def test_round_trip(self, client):
        r = client.post(
            "/custom-verifiers",
            json=_valid_body(),
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 200
        vid = r.json()["id"]
        g = client.get(
            f"/custom-verifiers/{vid}",
            headers={"X-Api-Key": API_KEY},
        )
        assert g.status_code == 200
        assert g.json()["name"] == "my_custom_check"
        assert g.json()["id"] == vid

    def test_404_on_unknown(self, client):
        r = client.get(
            "/custom-verifiers/deadbeefdeadbeef",
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 404

    def test_list_route_returns_items(self, client):
        client.post(
            "/custom-verifiers",
            json=_valid_body(),
            headers={"X-Api-Key": API_KEY},
        )
        r = client.get(
            "/custom-verifiers",
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 1
        assert items[0]["name"] == "my_custom_check"


# ── descriptor endpoint sanity ──────────────────────────────────────
class TestVerifierDescriptors:
    def test_list_endpoint_public(self, client):
        r = client.get("/verifier-descriptors")
        assert r.status_code == 200
        body = r.json()
        steps = [d["step"] for d in body["descriptors"]]
        for builtin in (
            "citation_verify", "privilege_scan", "source_allowlist",
            "structured_output", "prompt_injection_screen",
        ):
            assert builtin in steps

    def test_get_one_endpoint(self, client):
        r = client.get("/verifier-descriptors/privilege_scan")
        assert r.status_code == 200
        d = r.json()
        assert d["step"] == "privilege_scan"
        assert d["triggers"]
        assert d["verdict_set"]
        assert d["output_evidence"]

    def test_get_unknown_returns_404(self, client):
        r = client.get("/verifier-descriptors/does_not_exist")
        assert r.status_code == 404

    def test_input_fields_cover_paths(self, client):
        """Every input_payload_paths entry has a matching input_fields row
        with type + description. The module-level invariant catches drift
        at import; the route surfaces the same guarantee for third-party
        UIs reading the cloud's authoritative copy."""
        r = client.get("/verifier-descriptors")
        for d in r.json()["descriptors"]:
            field_paths = {f["path"] for f in d.get("input_fields", [])}
            for p in d["input_payload_paths"]:
                assert p in field_paths, (d["step"], p, field_paths)


# ── fix-cycle: catalog / verifiers merge tenant-scoped customs ─────
class TestCatalogMergesCustomVerifiers:
    """The /verifiers/new flow redirects to /rules?tab=evidence on success.
    Prior to the fix, /catalog/evidence-types did NOT include tenant-scoped
    custom rows so the operator saw a green flash but no row. Same hole
    on /verifiers (the docstring lied about merging customs in)."""

    def test_evidence_types_includes_authored_row(self, client):
        r = client.post(
            "/custom-verifiers",
            json=_valid_body(name="my_authored_check"),
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 200, r.text
        ev = client.get(
            "/catalog/evidence-types",
            headers={"X-Api-Key": API_KEY},
        )
        assert ev.status_code == 200
        steps = [i["step"] for i in ev.json()["items"]]
        assert "my_authored_check" in steps
        custom_row = next(i for i in ev.json()["items"]
                          if i["step"] == "my_authored_check")
        assert custom_row["source"] == "custom"
        assert custom_row["enforcement"] == "preview"

    def test_verifiers_route_merges_custom(self, client):
        client.post(
            "/custom-verifiers",
            json=_valid_body(name="my_other_check"),
            headers={"X-Api-Key": API_KEY},
        )
        v = client.get("/verifiers", headers={"X-Api-Key": API_KEY})
        assert v.status_code == 200
        names = [p.get("name") for p in v.json()["presets"]]
        assert "my_other_check" in names

    def test_verifiers_route_unauthed_excludes_custom(self, client):
        client.post(
            "/custom-verifiers",
            json=_valid_body(name="tenant_only_check"),
            headers={"X-Api-Key": API_KEY},
        )
        # No X-Api-Key — anonymous global view, no tenant resolution.
        v = client.get("/verifiers")
        assert v.status_code == 200
        names = [p.get("name") for p in v.json()["presets"]]
        assert "tenant_only_check" not in names


# ── fix-cycle: extra='forbid' on Pydantic body ─────────────────────
class TestCreateBodyForbidsExtras:
    """The brief explicitly calls out that hand-rolled bodies including
    `kind`/`pattern`/`criterion`/`shape_ttl` (the regex/llm_critic/shacl
    knobs) should fail loudly. Prior to the fix they were silently
    dropped by the raw-dict reader."""

    @pytest.mark.parametrize(
        "extras",
        [
            {"kind": "regex"},
            {"pattern": ".*"},
            {"criterion": "is_helpful"},
            {"shape_ttl": "@prefix sh: <...> ."},
            {"random_typo_key": True},
        ],
    )
    def test_extras_rejected_as_422(self, client, extras):
        body = _valid_body(**extras)
        r = client.post(
            "/custom-verifiers",
            json=body,
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 422, (extras, r.text)


# ── fix-cycle: name uniqueness 409 ─────────────────────────────────
class TestNameUniqueness:
    def test_duplicate_name_returns_409(self, client):
        r1 = client.post(
            "/custom-verifiers",
            json=_valid_body(name="dup_check"),
            headers={"X-Api-Key": API_KEY},
        )
        assert r1.status_code == 200
        r2 = client.post(
            "/custom-verifiers",
            json=_valid_body(name="dup_check"),
            headers={"X-Api-Key": API_KEY},
        )
        assert r2.status_code == 409


# ── fix-cycle: trigger event vocab + cap ───────────────────────────
class TestTriggerVocabAndCap:
    def test_unknown_event_rejected(self, client):
        bad = _valid_body(triggers=[{"event": "PreToolUSE", "matcher_class": "tool"}])
        r = client.post(
            "/custom-verifiers",
            json=bad,
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 422

    def test_over_cap_rejected(self, client):
        too_many = [{"event": "PreToolUse", "matcher_class": "tool"}] * 33
        bad = _valid_body(triggers=too_many)
        r = client.post(
            "/custom-verifiers",
            json=bad,
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 422

    def test_duplicate_triggers_deduped(self, client):
        # Two identical triggers -> store dedupes silently. Persisted
        # row has one trigger.
        dup = [
            {"event": "PreToolUse", "matcher_class": "tool"},
            {"event": "PreToolUse", "matcher_class": "tool"},
        ]
        r = client.post(
            "/custom-verifiers",
            json=_valid_body(name="dedup_check", triggers=dup),
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["triggers"]) == 1


# ── fix-cycle: GET path pattern ─────────────────────────────────────
class TestGetPathPattern:
    def test_non_hex_id_returns_422(self, client):
        r = client.get(
            "/custom-verifiers/not--an--id",
            headers={"X-Api-Key": API_KEY},
        )
        # FastAPI returns 422 for pattern mismatch (validation error)
        # vs 404 for "valid id, no row" — distinct signals for the caller.
        assert r.status_code == 422


# ── fix-cycle: store deserialize tolerates malformed rows ─────────
class TestStoreDeserializeTolerance:
    def test_list_skips_malformed_row(self, tmp_path):
        import json as _json
        from magi_cp.cloud.custom_verifier_store import CustomVerifierStore

        store_path = tmp_path / "cv.json"
        bucket = {
            "tenant-a": {
                "verifiers": [
                    # malformed (missing required "name")
                    {"id": "0123456789abcdef", "description": "x"},
                    # well-formed
                    {
                        "id": "fedcba9876543210",
                        "name": "good_check",
                        "description": "y",
                        "triggers": [
                            {"event": "Stop", "matcher_class": "final"},
                        ],
                        "verdict_set": ["pass"],
                        "body_type": "preview",
                        "created_at": 1700000000,
                        "tenant_id": "tenant-a",
                    },
                ],
            },
        }
        store_path.write_text(_json.dumps(bucket))
        store = CustomVerifierStore(path=str(store_path))
        items = store.list_for_tenant("tenant-a")
        # malformed row is silently skipped; good row survives
        assert len(items) == 1
        assert items[0].name == "good_check"
