"""D35: /verify_inline — regex/llm_critic/shacl runtime dispatch."""
import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.keys import KeyStore


API_KEY = "inline-test-key"
HDR = {"X-Api-Key": API_KEY}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAGI_CP_API_KEY", API_KEY)
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", "irrelevant")
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", "irrelevant")


@pytest.fixture
def client(tmp_path):
    from magi_cp.verifier.builtins import register_builtins
    from magi_cp.verifier.protocol import VerifierRegistry
    ks = KeyStore(dir=str(tmp_path / "keys"))
    reg = VerifierRegistry()
    register_builtins(reg)
    app = create_app(
        keystore=ks,
        dsn="sqlite:///:memory:",
        policy_store_path=str(tmp_path / "policies.json"),
        verifier_registry=reg,
    )
    return TestClient(app)


# ── kind=regex ────────────────────────────────────────────────────
def test_regex_match_passes(client):
    r = client.post(
        "/verify_inline", headers=HDR,
        json={
            "kind": "regex",
            "pattern": r"\bAKIA[A-Z0-9]+",
            "payload": {"text": "leaking AKIA12345 in output"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "pass"
    assert body["token"] is not None


def test_regex_no_match_denies(client):
    r = client.post(
        "/verify_inline", headers=HDR,
        json={
            "kind": "regex",
            "pattern": r"\bAKIA[A-Z0-9]+",
            "payload": {"text": "nothing sensitive here"},
        },
    )
    assert r.status_code == 200
    assert r.json()["verdict"] == "deny"
    assert r.json()["token"] is None


def test_regex_missing_pattern_422(client):
    r = client.post(
        "/verify_inline", headers=HDR,
        json={"kind": "regex", "payload": {"text": "x"}},
    )
    assert r.status_code == 422


def test_regex_uncompilable_pattern_422(client):
    r = client.post(
        "/verify_inline", headers=HDR,
        json={"kind": "regex", "pattern": "(", "payload": {"text": "x"}},
    )
    assert r.status_code == 422


def test_regex_falls_back_to_json_payload_when_no_text(client):
    """If payload lacks a `text` key, we stringify the dict and search
    that. Lets a regex check structural shape too."""
    r = client.post(
        "/verify_inline", headers=HDR,
        json={
            "kind": "regex",
            "pattern": r"\"secret_field\":",
            "payload": {"secret_field": "x"},
        },
    )
    assert r.status_code == 200
    assert r.json()["verdict"] == "pass"


# ── kind=llm_critic ───────────────────────────────────────────────
def test_llm_critic_without_provider_returns_review(client):
    """When MAGI_CP_LLM_COMPILER is unset (test fixture), the endpoint
    must return review with a preview-mode reason — not silently pass
    or deny."""
    r = client.post(
        "/verify_inline", headers=HDR,
        json={
            "kind": "llm_critic",
            "criterion": "Output is professional and not flippant.",
            "payload": {"text": "hello world"},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "review"
    assert any("preview" in s.lower() for s in body["reasons"])


def test_llm_critic_missing_criterion_422(client):
    r = client.post(
        "/verify_inline", headers=HDR,
        json={"kind": "llm_critic", "payload": {"text": "x"}},
    )
    assert r.status_code == 422


# ── kind=shacl ────────────────────────────────────────────────────
def test_shacl_without_pyshacl_returns_review(client):
    """pyshacl is an optional install. Without it the endpoint must
    return review with a clear install-hint reason."""
    r = client.post(
        "/verify_inline", headers=HDR,
        json={
            "kind": "shacl",
            "shape_ttl": "@prefix sh: <http://www.w3.org/ns/shacl#> .",
            "payload": {"evidence_ttl": "@prefix ex: <http://example.com/> ."},
        },
    )
    assert r.status_code == 200
    body = r.json()
    # If pyshacl is installed in the test env this becomes pass/deny;
    # locally without it we get review with a preview reason.
    assert body["verdict"] in ("pass", "deny", "review")
    if body["verdict"] == "review":
        assert any("pyshacl" in s.lower() or "preview" in s.lower() for s in body["reasons"])


def test_shacl_missing_shape_422(client):
    r = client.post(
        "/verify_inline", headers=HDR,
        json={"kind": "shacl", "payload": {}},
    )
    assert r.status_code == 422


# ── P7 (issue #1, P0 #1): payload lift + vacuous-satisfaction guard ─

import pytest as _pytest  # noqa: E402

_pyshacl = _pytest.importorskip("pyshacl", reason="pyshacl required for lift")
_rdflib = _pytest.importorskip("rdflib", reason="rdflib required for lift")


def test_shacl_chip_path_lands_on_focus_node_at_runtime(client):
    """The P0 fix: a SHACL shape targeting `magi:tool_input.command`
    (the canonical chip-picked path) must find a focus node when the
    runtime sees a Bash payload. Without the JSON → RDF lift the shape
    would be vacuously satisfied (zero focus nodes → conforms → silent
    allow). With the lift, a `sh:pattern` against the command actually
    fires.

    This shape DENIES any command containing `rm -rf`. We send a
    matching payload; the verdict must be deny, not the legacy
    silent-allow.
    """
    shape_ttl = (
        "@prefix sh:   <http://www.w3.org/ns/shacl#> .\n"
        "@prefix magi: <https://magi.openmagi.ai/cc/hook#> .\n"
        "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .\n"
        "[] a sh:PropertyShape ;\n"
        "   sh:targetClass magi:Hook ;\n"
        "   sh:path magi:tool_input.command ;\n"
        "   sh:datatype xsd:string ;\n"
        "   sh:not [ sh:pattern \"rm -rf\" ] ;\n"
        "   sh:minCount 1 .\n"
    )
    r = client.post(
        "/verify_inline", headers=HDR,
        json={
            "kind": "shacl",
            "shape_ttl": shape_ttl,
            "payload": {
                "__event__": "PreToolUse",
                "__matcher__": "Bash",
                "tool_input": {"command": "rm -rf /"},
            },
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "deny"


def test_shacl_chip_path_clean_payload_passes(client):
    """Same shape, clean command. The lift must still produce a focus
    node so the negated pattern can succeed; otherwise vacuous-allow
    would happen here too (false positive that would mask real bugs).
    """
    shape_ttl = (
        "@prefix sh:   <http://www.w3.org/ns/shacl#> .\n"
        "@prefix magi: <https://magi.openmagi.ai/cc/hook#> .\n"
        "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .\n"
        "[] a sh:PropertyShape ;\n"
        "   sh:targetClass magi:Hook ;\n"
        "   sh:path magi:tool_input.command ;\n"
        "   sh:datatype xsd:string ;\n"
        "   sh:not [ sh:pattern \"rm -rf\" ] ;\n"
        "   sh:minCount 1 .\n"
    )
    r = client.post(
        "/verify_inline", headers=HDR,
        json={
            "kind": "shacl",
            "shape_ttl": shape_ttl,
            "payload": {
                "__event__": "PreToolUse",
                "__matcher__": "Bash",
                "tool_input": {"command": "ls -la"},
            },
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "pass"


def test_shacl_vacuous_target_denies_not_conforms(client):
    """A shape anchored on a path the runtime never delivers MUST
    deny, not conform. The canonical vacuous-conforms case is a
    NodeShape `sh:targetNode magi:bogus` — pyshacl picks zero focus
    nodes and reports conforms=True. The /verify_inline guard
    re-reads the targets, checks the data graph, and flips the
    verdict to deny with a clear reason."""
    # `sh:targetClass magi:BogusType` selects zero focus nodes — the
    # runtime only ever materializes `magi:Hook`. pyshacl conforms with
    # zero focus nodes (per SHACL spec); the guard catches it.
    shape_ttl = (
        "@prefix sh:   <http://www.w3.org/ns/shacl#> .\n"
        "@prefix magi: <https://magi.openmagi.ai/cc/hook#> .\n"
        "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .\n"
        "[] a sh:NodeShape ;\n"
        "   sh:targetClass magi:BogusType ;\n"
        "   sh:property [\n"
        "     sh:path magi:tool_input.command ;\n"
        "     sh:datatype xsd:string ;\n"
        "     sh:minCount 1\n"
        "   ] .\n"
    )
    r = client.post(
        "/verify_inline", headers=HDR,
        json={
            "kind": "shacl",
            "shape_ttl": shape_ttl,
            "payload": {
                "__event__": "PreToolUse",
                "__matcher__": "Bash",
                "tool_input": {"command": "ls"},
            },
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "deny"
    assert any("vacuous" in s.lower() for s in body["reasons"])


def test_shacl_minCount_zero_focus_path_target_denies(client):
    """A PropertyShape with `sh:targetClass magi:Hook` + a path that
    doesn't exist in the lifted data graph DOES fire the SHACL minCount
    violation (focus node = hook subject; minCount 1 fails). This is
    still a "deny" verdict — the shape correctly reports the missing
    field — so authors who accidentally pick a non-existent path get
    a clear deny reason rather than silent allow."""
    shape_ttl = (
        "@prefix sh:   <http://www.w3.org/ns/shacl#> .\n"
        "@prefix magi: <https://magi.openmagi.ai/cc/hook#> .\n"
        "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .\n"
        "[] a sh:NodeShape ;\n"
        "   sh:targetClass magi:Hook ;\n"
        "   sh:property [\n"
        "     sh:path magi:tool_input.bogus ;\n"
        "     sh:datatype xsd:string ;\n"
        "     sh:minCount 1\n"
        "   ] .\n"
    )
    r = client.post(
        "/verify_inline", headers=HDR,
        json={
            "kind": "shacl",
            "shape_ttl": shape_ttl,
            "payload": {
                "__event__": "PreToolUse",
                "__matcher__": "Bash",
                "tool_input": {"command": "ls"},
            },
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "deny"


# ── unknown kind rejected by pydantic ─────────────────────────────
def test_unknown_kind_422(client):
    r = client.post(
        "/verify_inline", headers=HDR,
        json={"kind": "quantum", "payload": {}},
    )
    assert r.status_code == 422


# ── D53b follow-up: frame metadata + payload snapshot on ledger ──
def test_regex_verify_inline_writes_frame_meta_and_snapshot_to_ledger(client):
    """The runtime now writes `hook_event`, `matcher` (when supplied
    by the caller) and a bounded `__payload_snapshot__` (regex only)
    into the ledger row body, so the offline /policies/dry-run
    replay can scope rows by (event, matcher) AND scan the original
    payload text — not the verdict envelope JSON."""
    HDR_LEDGER = {"X-Api-Key": API_KEY}
    r = client.post(
        "/verify_inline", headers=HDR,
        json={
            "kind": "regex",
            "pattern": r"\bsecret\b",
            "hook_event": "PreToolUse",
            "matcher": "Bash",
            "payload": {"text": "this is the secret value"},
        },
    )
    assert r.status_code == 200, r.text
    # Pull the most-recent ledger row and check the new fields.
    page = client.get(
        "/ledger?limit=5&include_body=true", headers=HDR_LEDGER,
    ).json()
    assert len(page["entries"]) >= 1
    body = page["entries"][0]["body"]
    assert body["hook_event"] == "PreToolUse"
    assert body["matcher"] == "Bash"
    # Snapshot is bounded; the regex pattern would actually match it.
    assert "__payload_snapshot__" in body
    snap = body["__payload_snapshot__"]
    assert "secret" in snap


def test_llm_critic_verify_inline_skips_payload_snapshot(client):
    """Payload snapshot is regex-only — llm_critic (and shacl) bodies
    should NOT carry it. The dry-run replay treats llm_critic rows as
    indeterminate regardless of snapshot presence; writing one would
    waste ledger storage on a field nothing reads."""
    HDR_LEDGER = {"X-Api-Key": API_KEY}
    r = client.post(
        "/verify_inline", headers=HDR,
        json={
            "kind": "llm_critic",
            "criterion": "is this safe?",
            "hook_event": "PreToolUse",
            "matcher": "Bash",
            "payload": {"text": "ls -la"},
        },
    )
    assert r.status_code == 200, r.text
    page = client.get(
        "/ledger?limit=5&include_body=true", headers=HDR_LEDGER,
    ).json()
    body = page["entries"][0]["body"]
    # Frame metadata still lands…
    assert body.get("hook_event") == "PreToolUse"
    assert body.get("matcher") == "Bash"
    # …but no payload snapshot.
    assert "__payload_snapshot__" not in body
