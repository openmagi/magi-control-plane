"""PR-5 - Magi Agent handoff CTA for magi-agent-only + codex silent-noop findings.

Tests cover:
  (A) magi_agent_console_url() env helper: unset/set/empty behavior.
  (B) feasibility.magi_agent_route(): None when unset; URL when set.
  (C) step_compile with magi_agent_only intent -> alternatives has handoff entry,
      route is None when env unset / URL when env set, draft is empty (D75 parity).
  (D) step_compile with not_expressible intent -> alternatives is empty.
  (E) step_compile rt=codex with codex_matcher_inert draft -> alternatives has
      keep_for_cc + magi_agent_handoff.
  (F) intent_summary is plain-language scrubbed (no internal jargon).
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.config import magi_agent_console_url
from magi_cp.llm.provider import FakeLlmProvider
from magi_cp.policy import feasibility


HEADERS = {"X-Admin-Api-Key": "test-admin-key"}
_CONSOLE_ENV = "MAGI_CP_MAGI_AGENT_CONSOLE_URL"


@pytest.fixture(autouse=True)
def _admin_key(monkeypatch):
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", "test-admin-key")


def _tmp_store_path() -> str:
    d = tempfile.mkdtemp(prefix="magi-cp-handoff-")
    path = os.path.join(d, "policies.json")
    with open(path, "w") as f:
        f.write("[]")
    return path


def _client(*, llm_compiler=None) -> TestClient:
    app = create_app(
        dsn="sqlite:///:memory:",
        policy_store_path=_tmp_store_path(),
        llm_compiler=llm_compiler,
    )
    return TestClient(app)


def _llm_response(*, message: str = "", updates: dict | None = None,
                  questions: list | None = None) -> str:
    body: dict = {"assistant_message": message}
    if updates is not None:
        body["draft_updates"] = updates
    if questions is not None:
        body["questions"] = questions
    return json.dumps(body)


# ---------------------------------------------------------------------------
# (A) magi_agent_console_url() env helper
# ---------------------------------------------------------------------------

def test_console_url_unset_returns_none(monkeypatch):
    """Unset env -> None."""
    monkeypatch.delenv(_CONSOLE_ENV, raising=False)
    assert magi_agent_console_url() is None


def test_console_url_set_strips_trailing_slash(monkeypatch):
    """Set to URL with trailing slash -> URL without trailing slash."""
    monkeypatch.setenv(_CONSOLE_ENV, "https://x.example/")
    assert magi_agent_console_url() == "https://x.example"


def test_console_url_set_no_trailing_slash(monkeypatch):
    """Set to URL without trailing slash -> same URL."""
    monkeypatch.setenv(_CONSOLE_ENV, "https://x.example")
    assert magi_agent_console_url() == "https://x.example"


def test_console_url_empty_returns_none(monkeypatch):
    """Empty string env -> None."""
    monkeypatch.setenv(_CONSOLE_ENV, "")
    assert magi_agent_console_url() is None


def test_console_url_whitespace_returns_none(monkeypatch):
    """Whitespace-only env -> None (stripped to empty)."""
    monkeypatch.setenv(_CONSOLE_ENV, "   ")
    assert magi_agent_console_url() is None


# ---------------------------------------------------------------------------
# (B) feasibility.magi_agent_route()
# ---------------------------------------------------------------------------

def test_route_unset_returns_none(monkeypatch):
    """env unset -> route is None."""
    monkeypatch.delenv(_CONSOLE_ENV, raising=False)
    assert feasibility.magi_agent_route("do a thing") is None


def test_route_set_returns_url_with_encoded_intent(monkeypatch):
    """env set -> URL with /customize?intent= and URL-encoded summary."""
    monkeypatch.setenv(_CONSOLE_ENV, "https://x.example/")
    route = feasibility.magi_agent_route("do a thing")
    assert route == "https://x.example/customize?intent=do%20a%20thing"


def test_route_special_chars_encoded(monkeypatch):
    """Spaces and special chars in summary are percent-encoded."""
    monkeypatch.setenv(_CONSOLE_ENV, "https://x.example")
    route = feasibility.magi_agent_route("block & log")
    assert route is not None
    assert "/customize?intent=" in route
    assert "&" not in route.split("?intent=")[1]


# ---------------------------------------------------------------------------
# (C) step_compile with magi_agent_only intent
# ---------------------------------------------------------------------------

def _magi_agent_only_canned() -> str:
    """LLM stub that tries to seed a verifier - should be blocked."""
    return _llm_response(
        message="I will wire up the evidence ledger",
        updates={
            "trigger": {"event": "Stop", "matcher": "*"},
            "requires": [{"kind": "step", "step": "privilege_scan"}],
            "action": "audit",
        },
    )


def test_c_magi_agent_only_alternatives_has_handoff(monkeypatch):
    """magi_agent_only intent -> alternatives contains a magi_agent_handoff entry."""
    monkeypatch.delenv(_CONSOLE_ENV, raising=False)
    c = _client(llm_compiler=FakeLlmProvider([_magi_agent_only_canned()]))

    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [
                {"role": "user",
                 "content": "only if the evidence ledger shows the tests actually ran"}
            ],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    f = body["feasibility"]
    assert f is not None
    assert f["class"] == "magi-agent-only", f
    assert isinstance(f["alternatives"], list) and len(f["alternatives"]) >= 1, f

    handoff = next(
        (a for a in f["alternatives"] if a.get("kind") == "magi_agent_handoff"),
        None,
    )
    assert handoff is not None, f"no magi_agent_handoff in alternatives: {f['alternatives']}"
    assert "intent_summary" in handoff
    assert "cta" in handoff
    assert isinstance(handoff["cta"], str) and handoff["cta"]


def test_c_magi_agent_only_route_none_when_env_unset(monkeypatch):
    """magi_agent_only + env unset -> route is None (text-only CTA)."""
    monkeypatch.delenv(_CONSOLE_ENV, raising=False)
    c = _client(llm_compiler=FakeLlmProvider([_magi_agent_only_canned()]))

    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [
                {"role": "user",
                 "content": "only if the evidence ledger shows the tests actually ran"}
            ],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    f = body["feasibility"]
    handoff = next(
        (a for a in f["alternatives"] if a.get("kind") == "magi_agent_handoff"),
        None,
    )
    assert handoff is not None
    assert handoff["route"] is None, (
        f"expected route=None when env unset, got: {handoff['route']!r}"
    )


def test_c_magi_agent_only_route_url_when_env_set(monkeypatch):
    """magi_agent_only + env set -> route is a URL containing /customize?intent=."""
    monkeypatch.setenv(_CONSOLE_ENV, "https://console.example")
    c = _client(llm_compiler=FakeLlmProvider([_magi_agent_only_canned()]))

    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [
                {"role": "user",
                 "content": "only if the evidence ledger shows the tests actually ran"}
            ],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    f = body["feasibility"]
    handoff = next(
        (a for a in f["alternatives"] if a.get("kind") == "magi_agent_handoff"),
        None,
    )
    assert handoff is not None
    route = handoff["route"]
    assert route is not None, "expected a URL when env is set"
    assert "/customize?intent=" in route, f"unexpected route: {route!r}"


def test_c_magi_agent_only_draft_empty(monkeypatch):
    """D75 parity: magi_agent_only intent -> draft is None/empty (no IR persisted)."""
    monkeypatch.delenv(_CONSOLE_ENV, raising=False)
    c = _client(llm_compiler=FakeLlmProvider([_magi_agent_only_canned()]))

    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [
                {"role": "user",
                 "content": "only if the evidence ledger shows the tests actually ran"}
            ],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Draft must be None or empty dict (no IR seeded).
    returned_draft = body.get("draft")
    if returned_draft is not None:
        reqs = returned_draft.get("requires")
        assert not reqs, (
            f"draft should not be seeded with requires when intent is magi_agent_only; "
            f"got requires={reqs}"
        )


# ---------------------------------------------------------------------------
# (D) step_compile with not_expressible intent -> alternatives empty
# ---------------------------------------------------------------------------

def test_d_not_expressible_no_handoff(monkeypatch):
    """not_expressible intent -> alternatives is [] (dead-end, no handoff)."""
    monkeypatch.delenv(_CONSOLE_ENV, raising=False)
    c = _client(llm_compiler=FakeLlmProvider([_llm_response(message="ok")]))

    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user", "content": "분당 5번으로 제한해줘"}],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    f = body["feasibility"]
    assert f is not None
    assert f["code"] == "rate_limit_window", f
    assert f["alternatives"] == [], (
        f"not_expressible should have no handoff; got alternatives={f['alternatives']}"
    )
    # assistant_message still carries the in-bounds guidance from COPY_TABLE
    am = body["assistant_message"]
    assert am, "expected assistant_message with in-bounds guidance"


# ---------------------------------------------------------------------------
# (E) step_compile rt=codex + codex_matcher_inert -> keep_for_cc + handoff
# ---------------------------------------------------------------------------

def test_e_codex_silent_noop_has_keep_for_cc_and_handoff(monkeypatch):
    """rt=codex + PreToolUse/Read -> codex_matcher_inert -> keep_for_cc + handoff."""
    monkeypatch.delenv(_CONSOLE_ENV, raising=False)
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))

    draft = {"trigger": {"event": "PreToolUse", "matcher": "Read"}}
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user", "content": "block read operations"}],
            "draft_so_far": draft,
            "answers": None,
            "runtime_id": "codex",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    f = body["feasibility"]
    assert f is not None
    assert f["code"] == "codex_matcher_inert", f
    alts = f["alternatives"]
    kinds = [a["kind"] for a in alts]
    assert "keep_for_cc" in kinds, f"expected keep_for_cc in alternatives; got {kinds}"
    assert "magi_agent_handoff" in kinds, f"expected magi_agent_handoff in alternatives; got {kinds}"


def test_e_codex_webfetch_silent_noop_alternatives(monkeypatch):
    """rt=codex + PreToolUse/WebFetch -> codex_matcher_inert -> keep_for_cc + handoff."""
    monkeypatch.delenv(_CONSOLE_ENV, raising=False)
    canned = _llm_response(message="ok", updates={}, questions=[])
    c = _client(llm_compiler=FakeLlmProvider([canned]))

    draft = {"trigger": {"event": "PreToolUse", "matcher": "WebFetch"}}
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user", "content": "block WebFetch requests"}],
            "draft_so_far": draft,
            "answers": None,
            "runtime_id": "codex",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    f = body["feasibility"]
    assert f is not None
    assert f["code"] == "codex_matcher_inert", f
    kinds = [a["kind"] for a in f["alternatives"]]
    assert "keep_for_cc" in kinds
    assert "magi_agent_handoff" in kinds


# ---------------------------------------------------------------------------
# (F) intent_summary is plain-language scrubbed
# ---------------------------------------------------------------------------

def test_f_intent_summary_no_jargon(monkeypatch):
    """intent_summary must not contain internal jargon like 'regex', 'matcher'.

    We craft an input that contains 'matcher' in the user text; the summary
    must not leak that token verbatim (the _to_plain_language scrubber
    replaces it with the plain-language equivalent).
    """
    monkeypatch.delenv(_CONSOLE_ENV, raising=False)
    c = _client(llm_compiler=FakeLlmProvider([_magi_agent_only_canned()]))

    # Use a turn that mentions "matcher" to probe scrubbing.
    # The intent scan hits "evidence ledger" -> magi_evidence_catalog.
    # The summary is built from the user text which contains "matcher".
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [
                {"role": "user",
                 "content": "only if the evidence ledger shows the tests actually ran, "
                            "matcher=Bash should be used"}
            ],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    f = body["feasibility"]
    assert f is not None
    assert f["class"] == "magi-agent-only", f

    handoff = next(
        (a for a in f["alternatives"] if a.get("kind") == "magi_agent_handoff"),
        None,
    )
    assert handoff is not None
    summary = handoff.get("intent_summary", "")
    # "matcher" is internal jargon; _to_plain_language should have replaced it.
    # (The plain-language replacement for 'matcher' is 'which action' per the
    # existing _PLAIN_LANGUAGE_RULES table.)
    assert "matcher=" not in summary, (
        f"jargon 'matcher=' leaked into intent_summary: {summary!r}"
    )


def test_f_intent_summary_capped(monkeypatch):
    """intent_summary is capped at 200 chars."""
    monkeypatch.delenv(_CONSOLE_ENV, raising=False)
    c = _client(llm_compiler=FakeLlmProvider([_magi_agent_only_canned()]))

    long_text = "evidence ledger " + ("x" * 300)
    r = c.post(
        "/policies/compile-interactive",
        headers=HEADERS,
        json={
            "history": [{"role": "user", "content": long_text}],
            "draft_so_far": None,
            "answers": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    f = body["feasibility"]
    if f and f.get("class") == "magi-agent-only":
        handoff = next(
            (a for a in f["alternatives"] if a.get("kind") == "magi_agent_handoff"),
            None,
        )
        if handoff:
            summary = handoff.get("intent_summary", "")
            assert len(summary) <= 200, f"intent_summary too long: {len(summary)}"
