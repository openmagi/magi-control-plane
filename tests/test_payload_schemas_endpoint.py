"""P7: HTTP surface for the CC hook payload schema menu.

Endpoints under test:
  GET /payload-schemas              — full registry dump
  GET /payload-schemas/{event}      — filtered, optional ?matcher=
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.keys import KeyStore


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    # The /payload-schemas routes are public (reference data, not
    # tenant-scoped) but the app still requires these env vars to
    # boot the other endpoints under create_app.
    monkeypatch.setenv("MAGI_CP_API_KEY", "irrelevant")
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", "irrelevant")
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", "irrelevant")


@pytest.fixture
def client(tmp_path):
    ks = KeyStore(dir=str(tmp_path / "keys"))
    app = create_app(
        keystore=ks,
        dsn="sqlite:///:memory:",
        policy_store_path=str(tmp_path / "policies.json"),
    )
    return TestClient(app)


def test_list_returns_non_empty_registry(client):
    r = client.get("/payload-schemas")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "schemas" in body
    schemas = body["schemas"]
    assert isinstance(schemas, list)
    assert len(schemas) > 0, "registry must surface at least one event"
    # Every entry has event + matcher_class + fields[]
    for s in schemas:
        assert "event" in s
        assert "matcher_class" in s
        assert "fields" in s
        assert len(s["fields"]) >= 1


def test_list_includes_pretooluse_and_stop(client):
    r = client.get("/payload-schemas")
    assert r.status_code == 200
    events_seen = {s["event"] for s in r.json()["schemas"]}
    assert "PreToolUse" in events_seen
    assert "Stop" in events_seen


def test_event_lookup_returns_bash_command_field(client):
    r = client.get("/payload-schemas/PreToolUse?matcher=Bash")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["event"] == "PreToolUse"
    assert body["matcher"] == "Bash"
    paths = [f["path"] for f in body["fields"]]
    assert "tool_input.command" in paths


def test_event_lookup_without_matcher_returns_generic(client):
    r = client.get("/payload-schemas/PreToolUse")
    assert r.status_code == 200
    paths = [f["path"] for f in r.json()["fields"]]
    # No matcher → coerced to no_tool; PreToolUse has a tool bucket
    # only, helper falls back to the tool bucket's generic shape.
    # Either way the generic tool_input dict path must appear.
    assert "tool_input" in paths


def test_unknown_event_returns_404(client):
    r = client.get("/payload-schemas/BogusEventName")
    assert r.status_code == 404


def test_field_descriptor_has_description_and_type(client):
    """Wizard chip-row hover surface needs description + type. The
    response shape must include them or the hover tooltip renders
    empty."""
    r = client.get("/payload-schemas/PreToolUse?matcher=Bash")
    assert r.status_code == 200
    for f in r.json()["fields"]:
        assert "description" in f and isinstance(f["description"], str)
        assert "type" in f and f["type"] in (
            "str", "int", "bool", "list", "dict",
        )


def test_stop_event_exposes_final_message(client):
    r = client.get("/payload-schemas/Stop")
    assert r.status_code == 200
    paths = [f["path"] for f in r.json()["fields"]]
    assert "final_message" in paths


def test_endpoint_is_public_no_api_key_needed(client):
    """The schema menu is reference data shared across every tenant;
    we don't want the wizard to fail to render chips just because
    the tenant key isn't loaded into the dashboard env yet."""
    # No X-Api-Key header at all.
    r = client.get("/payload-schemas")
    assert r.status_code == 200
    r2 = client.get("/payload-schemas/PreToolUse?matcher=Bash")
    assert r2.status_code == 200
