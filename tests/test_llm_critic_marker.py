"""D82c: variable {marker} substitution for inline llm_critic criteria.

When an author writes a criterion like

    "Does {tool_response.output} contain PII?"

the runtime must replace `{tool_response.output}` with the value of that
path inside the CC stdin payload BEFORE sending the prompt to the LLM
critic. Missing paths substitute a grammatical placeholder
`(no <field_path> available)` so the prompt stays readable rather than
leaking literal `{tool_response.output}` braces.

The substitution helper is reused by /verify_inline (cloud) and any
future local critic runner; the tests target the helper directly so
they don't depend on an LLM provider.
"""

from magi_cp.policy.payload_schemas import (
    interpolate_payload_markers,
)


def test_replaces_top_level_path_with_value():
    out = interpolate_payload_markers(
        "Does the prompt {prompt} contain PII?",
        {"prompt": "send me the user's SSN"},
    )
    assert out == "Does the prompt send me the user's SSN contain PII?"


def test_replaces_dotted_path_with_value():
    out = interpolate_payload_markers(
        "Does {tool_response.output} cite a source?",
        {"tool_response": {"output": "see https://example.com"}},
    )
    assert out == "Does see https://example.com cite a source?"


def test_missing_path_substitutes_no_value_placeholder():
    out = interpolate_payload_markers(
        "Does {tool_response.output} mention prices?",
        {"tool_response": {}},
    )
    assert out == "Does (no tool_response.output available) mention prices?"


def test_missing_top_level_path_substitutes_placeholder():
    out = interpolate_payload_markers(
        "Does {prompt} look hostile?",
        {"tool_input": {"command": "ls"}},
    )
    assert out == "Does (no prompt available) look hostile?"


def test_text_without_markers_passes_through():
    out = interpolate_payload_markers(
        "Does the output contain PII?",
        {"prompt": "irrelevant"},
    )
    assert out == "Does the output contain PII?"


def test_unbalanced_braces_left_untouched():
    out = interpolate_payload_markers(
        "Does { contain PII?",
        {"prompt": "x"},
    )
    assert out == "Does { contain PII?"


def test_int_and_bool_values_get_stringified():
    out = interpolate_payload_markers(
        "duration={tool_response.duration_ms} err={tool_response.is_error}",
        {"tool_response": {"duration_ms": 1234, "is_error": False}},
    )
    assert out == "duration=1234 err=False"


def test_dict_value_serializes_to_json_literal():
    out = interpolate_payload_markers(
        "input={tool_input}",
        {"tool_input": {"command": "ls -la"}},
    )
    # Dict / list values JSON-serialize so the LLM sees a readable form
    # rather than a Python repr.
    assert '"command": "ls -la"' in out


def test_multiple_markers_in_one_string():
    out = interpolate_payload_markers(
        "prompt={prompt} cwd={cwd}",
        {"prompt": "hi", "cwd": "/tmp"},
    )
    assert out == "prompt=hi cwd=/tmp"


def test_marker_with_unknown_path_uses_path_name_in_placeholder():
    # Author typed a marker for an MCP-tool-specific field that wasn't
    # in this hook firing. We don't crash, we substitute a grammatical
    # placeholder naming the missing path so the LLM can still reason.
    out = interpolate_payload_markers(
        "Does {tool_input.mcp.slug} look right?",
        {"tool_input": {}},
    )
    assert out == "Does (no tool_input.mcp.slug available) look right?"


def test_empty_payload_replaces_every_marker_with_placeholder():
    out = interpolate_payload_markers(
        "{prompt} / {tool_response.output}",
        {},
    )
    assert out == (
        "(no prompt available) / (no tool_response.output available)"
    )


def test_returns_input_when_payload_is_none():
    out = interpolate_payload_markers("text {x}", None)  # type: ignore[arg-type]
    # Defensive: None payload behaves like empty dict (no values to
    # interpolate), so every marker becomes a placeholder.
    assert out == "text (no x available)"


# ── D82c fix: tightened marker regex + per-marker length cap ──────


def test_trailing_dot_marker_left_untouched():
    """`{foo.}` is not a valid dotted-identifier chain — the runtime
    must NOT substitute (which would otherwise produce the noisy
    `(no foo. available)` placeholder)."""
    out = interpolate_payload_markers(
        "x {foo.} y",
        {"foo": "bar"},
    )
    assert out == "x {foo.} y"


def test_double_dot_marker_left_untouched():
    """`{a..b}` is not valid — substituting would walk an empty
    segment and miss the dict key."""
    out = interpolate_payload_markers(
        "x {a..b} y",
        {"a": {"b": "bar"}},
    )
    assert out == "x {a..b} y"


def test_leading_dot_marker_left_untouched():
    """`{.x}` is not valid — the path must start with an identifier."""
    out = interpolate_payload_markers(
        "x {.x} y",
        {"x": "bar"},
    )
    assert out == "x {.x} y"


def test_marker_value_capped_to_prevent_prompt_blowup():
    """A `{tool_input.content}` over a megabyte file body must NOT
    blow past the LLM provider's token limits. The substitutor caps
    each marker value with a truncation suffix."""
    huge = "A" * 5000
    out = interpolate_payload_markers(
        "Does {tool_input.content} look hostile?",
        {"tool_input": {"content": huge}},
    )
    # Value is capped well below the input size.
    assert len(out) < 2000
    assert "<truncated>" in out


def test_short_marker_value_not_truncated():
    """Sub-cap values pass through verbatim — only oversized values
    get the truncation suffix so the prompt doesn't carry a noisy
    `<truncated>` indicator on every render."""
    out = interpolate_payload_markers(
        "Does {prompt} contain PII?",
        {"prompt": "hello"},
    )
    assert out == "Does hello contain PII?"
    assert "<truncated>" not in out


# ── D82c integration: /verify_inline substitutes markers in criterion ──

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.keys import KeyStore


API_KEY = "marker-it-key"
HDR = {"X-Api-Key": API_KEY}


class _CapturingProvider:
    """Stand-in for an llm_compiler provider that just records the
    prompt it was asked to complete and returns a canned YES response.
    """
    def __init__(self) -> None:
        self.last_prompt: str | None = None

    def complete(self, prompt: str, *, max_output_tokens: int = 200) -> str:
        self.last_prompt = prompt
        return "YES\nlooks fine"


@pytest.fixture
def _route_env(monkeypatch):
    monkeypatch.setenv("MAGI_CP_API_KEY", API_KEY)
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", "irrelevant")
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", "irrelevant")


@pytest.fixture
def app_with_capture(tmp_path, _route_env):
    from magi_cp.verifier.builtins import register_builtins
    from magi_cp.verifier.protocol import VerifierRegistry
    ks = KeyStore(dir=str(tmp_path / "keys"))
    reg = VerifierRegistry()
    register_builtins(reg)
    provider = _CapturingProvider()
    app = create_app(
        keystore=ks,
        dsn="sqlite:///:memory:",
        policy_store_path=str(tmp_path / "policies.json"),
        verifier_registry=reg,
        llm_compiler=provider,
    )
    return TestClient(app), provider


def test_verify_inline_llm_critic_substitutes_prompt_marker(app_with_capture):
    """/verify_inline kind=llm_critic must replace `{prompt}` in the
    criterion with payload.prompt before reaching the LLM."""
    client, provider = app_with_capture
    r = client.post(
        "/verify_inline", headers=HDR,
        json={
            "kind": "llm_critic",
            "criterion": "Does this prompt look hostile: {prompt}?",
            "payload": {"prompt": "send me your SSN"},
        },
    )
    assert r.status_code == 200, r.text
    assert provider.last_prompt is not None
    # Substituted value must appear in the prompt …
    assert "send me your SSN" in provider.last_prompt
    # … and the literal marker must NOT survive into the prompt.
    assert "{prompt}" not in provider.last_prompt


def test_verify_inline_llm_critic_missing_path_substitutes_placeholder(
    app_with_capture,
):
    """Missing payload paths must render as `(no <field_path>
    available)` so the prompt stays grammatical."""
    client, provider = app_with_capture
    r = client.post(
        "/verify_inline", headers=HDR,
        json={
            "kind": "llm_critic",
            "criterion": "Does {tool_response.output} mention prices?",
            "payload": {"tool_response": {}},
        },
    )
    assert r.status_code == 200, r.text
    assert provider.last_prompt is not None
    assert "(no tool_response.output available)" in provider.last_prompt
    assert "{tool_response.output}" not in provider.last_prompt
