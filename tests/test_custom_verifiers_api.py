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
        # D52d: at least one field_check is required by the wire model
        # so a canonical valid body must carry one.
        "field_checks": [
            {
                "path": "tool_input.command",
                "check_description": "matches a custom pattern",
            },
        ],
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
            "field_checks": [
                {"path": "tool_input.command", "check_description": "x"},
            ],
        })
        assert v.name == "x"
        assert v.description == "y"
        assert v.triggers[0].event == "Stop"
        assert v.verdict_set == ("pass",)
        assert v.body_type == "preview"
        assert len(v.field_checks) == 1
        assert v.field_checks[0].path == "tool_input.command"
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
            "field_checks": [
                {"path": "tool_input.command", "check_description": "x"},
            ],
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
            "field_checks": [
                {"path": "tool_input.command", "check_description": "x"},
            ],
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
                "field_checks": [
                    {"path": "tool_input.command", "check_description": "x"},
                ],
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

    # ── D52d field_checks ──────────────────────────────────────────
    def test_accepts_field_checks(self, client):
        # _valid_body already supplies one; round-trip the row back.
        r = client.post(
            "/custom-verifiers",
            json=_valid_body(field_checks=[
                {"path": "tool_input.url",
                 "check_description": "hostname is in allowlist"},
                {"path": "tool_response.output",
                 "check_description": "matches some pattern"},
            ]),
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["field_checks"]) == 2
        assert body["field_checks"][0]["path"] == "tool_input.url"
        assert (
            body["field_checks"][0]["check_description"]
            == "hostname is in allowlist"
        )

    def test_rejects_missing_field_checks(self, client):
        # field_checks is required (>=1 row).
        body = _valid_body()
        body.pop("field_checks", None)
        r = client.post(
            "/custom-verifiers",
            json=body,
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 422

    def test_rejects_empty_field_checks(self, client):
        r = client.post(
            "/custom-verifiers",
            json=_valid_body(field_checks=[]),
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 422

    def test_rejects_field_check_missing_path(self, client):
        r = client.post(
            "/custom-verifiers",
            json=_valid_body(field_checks=[
                {"check_description": "no path here"},
            ]),
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 422

    def test_rejects_field_check_overlong_description(self, client):
        r = client.post(
            "/custom-verifiers",
            json=_valid_body(field_checks=[
                {"path": "tool_input.url",
                 "check_description": "x" * 201},
            ]),
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 422

    # ── D57c input_assembly + caller_assembly_hint ─────────────────
    def test_default_input_assembly_is_cc_stdin(self, client):
        """D57c: omitting input_assembly defaults to cc_stdin so
        pre-D57c clients keep working without touching the body."""
        body = _valid_body()
        body.pop("input_assembly", None)
        body.pop("caller_assembly_hint", None)
        r = client.post(
            "/custom-verifiers", json=body,
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["input_assembly"] == "cc_stdin"
        assert out["caller_assembly_hint"] == ""

    def test_caller_assembled_round_trip(self, client):
        """D57c: caller_assembled rows persist the hint verbatim."""
        r = client.post(
            "/custom-verifiers",
            json=_valid_body(
                name="my_caller_check",
                input_assembly="caller_assembled",
                caller_assembly_hint=(
                    "recipe extracts every (quote, ref) pair from "
                    "the agent answer with a regex and POSTs "
                    "{citations: [...]}"
                ),
            ),
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["input_assembly"] == "caller_assembled"
        assert "recipe" in out["caller_assembly_hint"]

    def test_rejects_caller_assembled_without_hint(self, client):
        """D57c: caller_assembled rows MUST carry a non-empty hint."""
        r = client.post(
            "/custom-verifiers",
            json=_valid_body(
                input_assembly="caller_assembled",
                caller_assembly_hint="",
            ),
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 422

    def test_rejects_cc_stdin_with_hint(self, client):
        """D57c: cc_stdin rows MUST leave the hint blank — surfacing
        the contract at the wire boundary so a hand-rolled client does
        not silently set a hint that the dashboard would never render."""
        r = client.post(
            "/custom-verifiers",
            json=_valid_body(
                input_assembly="cc_stdin",
                caller_assembly_hint="oops, this should not be here",
            ),
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 422

    def test_rejects_unknown_input_assembly(self, client):
        """D57c: input_assembly enum is a closed set; a typo or a
        future-look value is a 422 rather than silently coerced."""
        r = client.post(
            "/custom-verifiers",
            json=_valid_body(
                input_assembly="kaboom",
                caller_assembly_hint="x",
            ),
            headers={"X-Api-Key": API_KEY},
        )
        assert r.status_code == 422

    def test_rejects_overlong_caller_assembly_hint(self, client):
        """D57c: the hint is capped at 500 chars to match the dashboard
        notice cell budget."""
        r = client.post(
            "/custom-verifiers",
            json=_valid_body(
                input_assembly="caller_assembled",
                caller_assembly_hint="x" * 501,
            ),
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

    # ── D52d (D57e: grouped by lifecycle) ─────────────────────────
    def test_every_builtin_has_field_checks(self, client):
        """Every built-in descriptor must declare at least one
        lifecycle group with at least one field_check row. The
        dashboard's catalog expander + wizard picker both render off
        this dict-of-arrays, so an empty dict misleads the operator
        into the "preview mode" branch for a verifier that actually
        has a runtime body.

        D57e: field_checks shape changed from `list[FieldCheck]` to
        `dict[event, list[FieldCheck]]`. We walk both axes here so
        a future row-level invariant regression surfaces with the
        lifecycle key it landed under.
        """
        r = client.get("/verifier-descriptors")
        assert r.status_code == 200
        for d in r.json()["descriptors"]:
            groups = d.get("field_checks") or {}
            assert isinstance(groups, dict), (d["step"], type(groups).__name__)
            assert len(groups) >= 1, d["step"]
            for event, rows in groups.items():
                assert isinstance(event, str) and event.strip(), (d["step"], event)
                assert isinstance(rows, list) and len(rows) >= 1, (d["step"], event)
                for i, fc in enumerate(rows):
                    assert fc.get("path"), (d["step"], event, i)
                    assert fc.get("check_description"), (d["step"], event, i)
                    assert len(fc["check_description"]) <= 200, (d["step"], event, i)

    # ── D57c: input_assembly on descriptor list endpoint ──────────
    def test_every_descriptor_has_input_assembly(self, client):
        """D57c: every built-in descriptor MUST surface an
        `input_assembly` value through the public endpoint so the
        dashboard catalog renderer can pick the right notice without
        a second lookup."""
        r = client.get("/verifier-descriptors")
        assert r.status_code == 200
        for d in r.json()["descriptors"]:
            assert d.get("input_assembly") in (
                "cc_stdin", "caller_assembled",
            ), d["step"]
            # caller_assembled rows expose a non-empty hint
            if d["input_assembly"] == "caller_assembled":
                assert d.get("caller_assembly_hint"), d["step"]

    def test_citation_verify_descriptor_is_caller_assembled(self, client):
        """D57c: citation_verify is the canonical caller_assembled
        verifier; the dashboard surface keys off this to render the
        notice. Lock the contract at the public endpoint."""
        r = client.get("/verifier-descriptors/citation_verify")
        assert r.status_code == 200
        d = r.json()
        assert d["input_assembly"] == "caller_assembled"
        # Hint mentions the caller / assembly seam in some form
        assert d["caller_assembly_hint"]
        assert "caller" in d["caller_assembly_hint"].lower() or \
            "post" in d["caller_assembly_hint"].lower()

    def test_structured_output_descriptor_is_caller_assembled(self, client):
        """D57c: structured_output is also caller_assembled (the cloud
        does not forward CC stdin into the JSON-schema verifier; a
        recipe / wrapper extracts the payload)."""
        r = client.get("/verifier-descriptors/structured_output")
        assert r.status_code == 200
        d = r.json()
        assert d["input_assembly"] == "caller_assembled"
        assert d["caller_assembly_hint"]

    def test_privilege_scan_descriptor_is_caller_assembled(self, client):
        """D57c follow-up: privilege_scan is caller_assembled.

        The verifier's run() reads only `payload.get("text")` from its
        OWN input dict, and the cloud's `_verify_dispatch_impl`
        forwards `req.payload` verbatim — there is no runtime
        extractor that pulls `tool_input.command` / `final_message`
        off CC stdin into the verifier's `text` key. A caller has to
        do that routing; the hint surfaces the contract on the
        dashboard.
        """
        r = client.get("/verifier-descriptors/privilege_scan")
        assert r.status_code == 200
        d = r.json()
        assert d["input_assembly"] == "caller_assembled"
        assert d["caller_assembly_hint"]
        # Hint names the CC stdin surfaces the caller has to read.
        hint = d["caller_assembly_hint"].lower()
        assert "tool_input.command" in hint
        assert "final_message" in hint

    def test_source_allowlist_descriptor_is_caller_assembled(self, client):
        """D57c follow-up: source_allowlist is caller_assembled.

        The verifier's run() reads `sources` (a LIST of URLs) and
        `allowlist` from its OWN input dict. There is no runtime path
        that pulls `tool_input.url` off CC stdin into `sources`; a
        wrapper has to read the URL (or parse URLs out of the tool
        response), wrap it as `[url]`, attach the policy-bound
        `allowlist`, and POST.
        """
        r = client.get("/verifier-descriptors/source_allowlist")
        assert r.status_code == 200
        d = r.json()
        assert d["input_assembly"] == "caller_assembled"
        assert d["caller_assembly_hint"]
        hint = d["caller_assembly_hint"].lower()
        assert "tool_input.url" in hint
        assert "sources" in hint

    def test_prompt_injection_screen_descriptor_is_caller_assembled(self, client):
        """D57c follow-up: prompt_injection_screen is caller_assembled.

        Same shape as privilege_scan: the verifier reads
        `payload.get("text")` from its OWN input dict; the cloud does
        not auto-forward `prompt` (UserPromptSubmit) or
        `tool_response.output` (PostToolUse) into that field.
        """
        r = client.get("/verifier-descriptors/prompt_injection_screen")
        assert r.status_code == 200
        d = r.json()
        assert d["input_assembly"] == "caller_assembled"
        assert d["caller_assembly_hint"]
        hint = d["caller_assembly_hint"].lower()
        assert "prompt" in hint or "tool_response.output" in hint

    def test_citation_verify_field_checks_shape(self, client):
        """D52d follow-up + D57e: citation_verify is a caller-
        assembled verifier; its run() reads `citations` and
        `corpus_override` from its OWN input dict, not CC stdin
        paths. The catalog row therefore documents the verifier's
        input contract, not CC stdin paths. The earlier brief asking
        for `tool_input.url` / `tool_response.output` /
        `transcript_path` was fabrication.
        descriptors.py:_assert_field_checks_paths_resolve() hard-
        fails import if any built-in carries a row that resolves
        neither to a CC stdin path on a declared trigger nor to one
        of the verifier's own input_payload_paths.

        D57e: citation_verify groups its rows under the Stop
        lifecycle (the only lifecycle the verifier fires under). The
        earlier PostToolUse trigger was pruned because the verifier
        does not actually run on every research-tool result.
        """
        r = client.get("/verifier-descriptors/citation_verify")
        assert r.status_code == 200
        d = r.json()
        groups = d["field_checks"]
        # D57e: dict-of-arrays keyed by lifecycle event. citation_verify
        # only fires at Stop time.
        assert isinstance(groups, dict)
        assert list(groups.keys()) == ["Stop"]
        paths = [fc["path"] for fc in groups["Stop"]]
        assert "citations[].quote" in paths
        assert "citations[].ref" in paths
        assert "corpus_override" in paths

    def test_prompt_injection_screen_lifecycle_groups(self, client):
        """D57e: prompt_injection_screen does NOT fire on PreToolUse
        (no tool input is "screen-worthy" before the call lands); the
        brief explicitly hides PreToolUse here. The verifier groups
        carry UserPromptSubmit / PostToolUse / Stop only."""
        r = client.get("/verifier-descriptors/prompt_injection_screen")
        assert r.status_code == 200
        groups = r.json()["field_checks"]
        assert isinstance(groups, dict)
        assert "PreToolUse" not in groups
        assert {"UserPromptSubmit", "PostToolUse", "Stop"} <= set(groups.keys())
        assert any(fc["path"] == "prompt" for fc in groups["UserPromptSubmit"])
        assert any(
            fc["path"] == "tool_response.output" for fc in groups["PostToolUse"]
        )
        assert any(fc["path"] == "final_message" for fc in groups["Stop"])

    def test_privilege_scan_lifecycle_groups(self, client):
        """D57e: privilege_scan walks four lifecycles. PreToolUse
        carries three tool-specific rows (Bash command, Edit
        new_string, Write content); the other lifecycles carry one
        row each."""
        r = client.get("/verifier-descriptors/privilege_scan")
        assert r.status_code == 200
        groups = r.json()["field_checks"]
        assert set(groups.keys()) == {
            "PreToolUse", "PostToolUse", "Stop", "UserPromptSubmit",
        }
        pre_paths = {fc["path"] for fc in groups["PreToolUse"]}
        assert {
            "tool_input.command", "tool_input.new_string", "tool_input.content",
        } <= pre_paths
        assert any(fc["path"] == "final_message" for fc in groups["Stop"])
        assert any(fc["path"] == "prompt" for fc in groups["UserPromptSubmit"])

    def test_source_allowlist_lifecycle_groups(self, client):
        """D57e: source_allowlist is PreToolUse-only (per brief). The
        old PostToolUse trigger was pruned because there is no
        runtime path that re-checks the URL after the fetch already
        ran."""
        r = client.get("/verifier-descriptors/source_allowlist")
        assert r.status_code == 200
        groups = r.json()["field_checks"]
        assert list(groups.keys()) == ["PreToolUse"]
        assert any(fc["path"] == "tool_input.url" for fc in groups["PreToolUse"])

    def test_structured_output_lifecycle_groups(self, client):
        """D57e: structured_output groups under Stop only. The row set
        documents the caller-assembled contract (the caller extracts
        a fenced JSON block from `final_message` and POSTs the
        verifier's own `json` / `data` / `schema` keys)."""
        r = client.get("/verifier-descriptors/structured_output")
        assert r.status_code == 200
        groups = r.json()["field_checks"]
        assert list(groups.keys()) == ["Stop"]
        paths = {fc["path"] for fc in groups["Stop"]}
        # The CC stdin surface the caller extracts FROM, plus the
        # verifier's own input dict keys.
        assert "final_message" in paths
        assert {"json", "data", "schema"} <= paths


# ── D52d module-level descriptor invariants ────────────────────────
class TestDescriptorFieldChecksModule:
    """Hits the Python module directly (no HTTP) so the assertion-shaped
    schema enforcement (`_assert_field_checks_shape`) is part of the
    standard pytest run, not just an import-time side effect."""

    def test_module_imports_clean(self):
        # If `_assert_field_checks_shape` failed at import time the test
        # process would have died before reaching here. This test is a
        # smoke-level guard for future contributors.
        from magi_cp.verifier.descriptors import (
            all_descriptors, get_descriptor,
        )
        ds = all_descriptors()
        assert len(ds) == 5
        for d in ds:
            assert get_descriptor(d["step"]) is d

    def test_descriptor_field_checks_export(self):
        """D57e: field_checks is a dict keyed by lifecycle event with
        list-of-rows values. We walk both axes so a future drift on
        either layer surfaces with the right key in the assertion."""
        from magi_cp.verifier.descriptors import all_descriptors
        for d in all_descriptors():
            assert "field_checks" in d
            groups = d["field_checks"]
            assert isinstance(groups, dict)
            assert len(groups) >= 1
            for event, rows in groups.items():
                assert isinstance(event, str) and event
                assert isinstance(rows, list) and len(rows) >= 1
                for fc in rows:
                    assert isinstance(fc["path"], str) and fc["path"]
                    assert isinstance(fc["check_description"], str)
                    assert fc["check_description"]

    def test_descriptor_field_checks_flat_helper(self):
        """D57e: `field_checks_flat()` flattens the grouped dict into
        a list, preserving lifecycle insertion order. Existing
        consumers (the /checks catalog, custom-verifier authoring
        tooling, the older /verifier-descriptors flat-list tests)
        keep parsing the same rows. The dashboard reads the grouped
        shape directly when it needs the lifecycle keying."""
        from magi_cp.verifier.descriptors import (
            all_descriptors, field_checks_flat,
        )
        for d in all_descriptors():
            flat = field_checks_flat(d)
            # Flat list mirrors the union of every group's rows.
            total = sum(len(rows) for rows in d["field_checks"].values())
            assert len(flat) == total, d["step"]
            for fc in flat:
                assert fc["path"]
                assert fc["check_description"]

    def test_descriptor_lifecycle_groups_match_triggers(self):
        """D57e structural invariant: every lifecycle group key in
        field_checks must match an event the verifier declares in its
        triggers list. The dashboard Step 3 picker filters verifiers
        on `event in field_checks`; an orphan group (no trigger row)
        would mean the picker shows the verifier for a lifecycle the
        runtime never actually fires under, which is the silent-
        drift mode this gate exists to prevent.

        The same constraint runs at descriptors.py import time via
        `_assert_field_checks_paths_resolve()`; this re-asserts it
        in pytest for grep / CI surface.
        """
        from magi_cp.verifier.descriptors import all_descriptors
        for d in all_descriptors():
            trigger_events = {tr["event"] for tr in d.get("triggers", [])}
            for event in d.get("field_checks", {}).keys():
                assert event in trigger_events, (d["step"], event)

    def test_descriptor_input_assembly_export(self):
        """D57c module-level guarantee: every descriptor declares an
        input_assembly value, caller_assembled rows carry a
        non-empty hint, and cc_stdin rows leave the hint blank. The
        matching assertion in descriptors.py runs at import time;
        this surfaces the same guarantee in the test suite for future
        contributors.

        D57c follow-up: all five current built-ins are
        caller_assembled because the cloud's `_verify_dispatch_impl`
        forwards `req.payload` verbatim and none of the built-ins
        ship a CC-stdin-to-payload extractor. The shape check below
        still tolerates a future cc_stdin row (if a real extractor
        ever lands), so this test does not block adding one.
        """
        from magi_cp.verifier.descriptors import all_descriptors
        seen_caller = 0
        seen_cc = 0
        for d in all_descriptors():
            assert d.get("input_assembly") in (
                "cc_stdin", "caller_assembled",
            ), d["step"]
            hint = d.get("caller_assembly_hint", "")
            if d["input_assembly"] == "caller_assembled":
                assert hint and hint.strip(), d["step"]
                seen_caller += 1
            else:
                assert not hint.strip(), d["step"]
                seen_cc += 1
        # Every current built-in is caller_assembled (see docstring).
        # Pin the count to the full set of 5 so a future cc_stdin
        # flip is a deliberate decision the author has to revisit
        # this test for.
        assert seen_caller == 5
        assert seen_cc == 0


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
