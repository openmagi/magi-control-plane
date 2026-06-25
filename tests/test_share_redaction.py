"""Vendored public-link redaction for the run-share path (src/magi_cp/share).

Mirrors the magi-agent run_redaction test coverage (format coverage, linearity,
allowlist fail-closed, nested-key scrub) plus the Claude-Code producer additions
(summary.title, top-level results, string summary.model).
"""
from __future__ import annotations

import time

import pytest

from magi_cp.share.redaction import build_public_run_view, redact_public_text


def _leaks(text: str, secret: str) -> bool:
    return secret in redact_public_text(text, max_chars=None)


@pytest.mark.parametrize(
    "secret",
    [
        "AKIA" + "IOSFODNN7EXAMPLE",
        "AIza" + "SyA1234567890abcdefghij",
        "xoxb-" + "123456789012-abcdef",
        "eyJ" + "abcdefgh.eyJpayload01.sigsigsig0",
        "ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "/Users/" + "kevin/.ssh/id_rsa",
    ],
)
def test_kernel_covered_formats_are_redacted(secret: str) -> None:
    assert not _leaks(f"value {secret} end", secret)


@pytest.mark.parametrize(
    "text,secret",
    [
        ('password="a\\"PROD_DB_PW_LEAKS"', "PROD_DB_PW_LEAKS"),
        ("secret=\"he said 'topsecretpw' ok\"", "topsecretpw"),
        ("service_role_key=eyJsupabaseSERVICErole", "eyJsupabaseSERVICErole"),
        ("passwd=mypwLEAKS123", "mypwLEAKS123"),
        ("AccountKey=abcDEF123456789xyzBASE64KEYvalue", "abcDEF123456789xyzBASE64KEYvalue"),
    ],
)
def test_named_and_opaque_credentials_do_not_leak(text: str, secret: str) -> None:
    assert not _leaks(text, secret)


def test_http_basic_auth_url_creds_redacted() -> None:
    out = redact_public_text("curl https://admin:hunter2@example.com/api", max_chars=None)
    assert "hunter2" not in out and "admin" not in out


def test_cluster_host_rfc1918_email_redacted() -> None:
    out = redact_public_text(
        "host api.prod.svc.cluster.local 10.1.2.3 a@b.com", max_chars=None
    )
    assert "svc.cluster.local" not in out
    assert "10.1.2.3" not in out
    assert "a@b.com" not in out


def test_public_url_and_ip_survive() -> None:
    out = redact_public_text("see https://github.com/x/y/pull/1234 dns 8.8.8.8", max_chars=None)
    assert "github.com/x/y/pull/1234" in out
    assert "8.8.8.8" in out


def test_redaction_is_linear_on_large_input() -> None:
    start = time.perf_counter()
    redact_public_text("x" * 100_000, max_chars=None)
    assert time.perf_counter() - start < 1.0


@pytest.mark.parametrize("payload", ["A=" * 60_000, "password=" * 20_000, 'secret="' + "a" * 120_000])
def test_no_redos_on_adversarial_input(payload: str) -> None:
    start = time.perf_counter()
    redact_public_text(payload, max_chars=None)
    assert time.perf_counter() - start < 1.0


# --- allowlist projection over a Claude-Code producer-shaped view ---
def _view() -> dict:
    return {
        "schemaVersion": "openmagi.runView.v1",
        "sessionId": "s",
        "summary": {
            "goal": "deploy with token ghp_" + "A" * 36,
            "result": "done",
            "status": "completed",
            "model": "claude-opus-4-8",  # producer emits a STRING, not a dict
            "usage": {"inputTokens": 10, "outputTokens": 5},
            "title": "fix the AKIA" + "IOSFODNN7EXAMPLE leak",  # secret in title
            "EVIL": "drop me",
        },
        "results": [
            {"prNumber": 1234, "prUrl": "https://github.com/x/y/pull/1234", "EVIL": "drop"},
            {"prNumber": 9, "prUrl": "https://u:p@host/pull/9"},  # creds in url
        ],
        "trace": [
            {
                "name": "Bash",
                "status": "ok",
                "activityType": "ToolCall",
                "argsSummary": {"command": "curl https://u:p@host/x"},
                "SECRET": "drop me",
            }
        ],
        "governance": [{"name": "Bash", "status": "blocked", "reason": "unsafe", "kind": "policy"}],
        "counts": {"stepCount": 1, "resultCount": 2, "governanceCount": 1},
    }


def test_projection_drops_unknown_keys() -> None:
    pub = build_public_run_view(_view())
    assert "EVIL" not in pub["summary"]
    assert "SECRET" not in pub["trace"][0]
    assert "EVIL" not in pub["results"][0]


def test_projection_scrubs_summary_free_text_incl_title() -> None:
    pub = build_public_run_view(_view())
    assert "ghp_" + "A" * 36 not in pub["summary"]["goal"]
    assert "AKIA" + "IOSFODNN7EXAMPLE" not in pub["summary"]["title"]


def test_projection_string_model_preserved() -> None:
    assert build_public_run_view(_view())["summary"]["model"] == "claude-opus-4-8"


def test_projection_results_pr_links() -> None:
    pub = build_public_run_view(_view())
    # plain PR link survives; one with embedded creds is scrubbed
    assert pub["results"][0]["prUrl"] == "https://github.com/x/y/pull/1234"
    assert pub["results"][0]["prNumber"] == 1234
    assert "u:p@host" not in pub["results"][1]["prUrl"]


def test_pr_number_non_int_is_dropped_fail_closed() -> None:
    # prNumber comes verbatim from a model-influenceable pr-link event; a
    # secret-shaped string or object must NOT pass through to the public link.
    view = {
        "schemaVersion": "openmagi.runView.v1",
        "results": [
            {"prNumber": "ghp_" + "Z" * 36, "prUrl": "https://x/y/pull/1"},
            {"prNumber": {"leak": "/Users/kevin/.ssh/id_rsa"}, "prUrl": "https://x/y/pull/2"},
            {"prNumber": 42, "prUrl": "https://x/y/pull/42"},
        ],
    }
    pub = build_public_run_view(view)
    assert pub["results"][0]["prNumber"] is None
    assert pub["results"][1]["prNumber"] is None
    assert pub["results"][2]["prNumber"] == 42
    assert "ghp_" + "Z" * 36 not in str(pub["results"])
    assert "id_rsa" not in str(pub["results"])


def test_model_unexpected_shape_is_scrubbed() -> None:
    view = {"schemaVersion": "v", "summary": {"model": ["ghp_" + "E" * 36]}}
    pub = build_public_run_view(view)
    assert "ghp_" + "E" * 36 not in str(pub["summary"]["model"])


def test_projection_scrubs_nested_trace_args() -> None:
    pub = build_public_run_view(_view())
    assert "u:p@host" not in str(pub["trace"][0]["argsSummary"])
    assert pub["trace"][0]["name"] == "Bash"


def test_projection_counts_and_structure() -> None:
    pub = build_public_run_view(_view())
    assert pub["schemaVersion"] == "openmagi.runView.v1"
    assert pub["summary"]["usage"] == {"inputTokens": 10, "outputTokens": 5}
    assert pub["counts"]["resultCount"] == 2
    assert pub["governance"][0]["kind"] == "policy"


def test_projection_handles_none_summary_and_missing_lists() -> None:
    pub = build_public_run_view({"schemaVersion": "openmagi.runView.v1", "summary": None})
    assert pub["summary"] is None
    assert pub["results"] == []
    assert pub["trace"] == []
    assert pub["governance"] == []


def test_producer_to_redaction_pipeline_scrubs_secret_in_goal() -> None:
    # The real use: Claude Code transcript -> run view -> public (redacted) view.
    from magi_cp.share.claude_code_view import transcript_to_run_view

    token = "ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    events = [
        {"type": "user", "sessionId": "s",
         "message": {"role": "user", "content": f"deploy using {token}"}},
        {"type": "assistant", "sessionId": "s",
         "message": {"role": "assistant", "model": "claude-opus-4-8",
                     "content": [{"type": "text", "text": "done"}],
                     "usage": {"input_tokens": 5, "output_tokens": 2}}},
    ]
    pub = build_public_run_view(transcript_to_run_view(events))
    assert token not in pub["summary"]["goal"]
    assert pub["summary"]["model"] == "claude-opus-4-8"
    assert pub["sessionId"] == "s"
