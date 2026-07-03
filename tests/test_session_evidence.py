"""Session-scoped evidence ledger + the audit/gate hook binaries."""
from __future__ import annotations

import json

import pytest

from magi_cp.local import session_audit, session_evidence, session_gate
from magi_cp.local.session_scope import cwd_in_scope


@pytest.fixture(autouse=True)
def _ledger_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CP_SESSION_EVIDENCE_DIR", str(tmp_path / "ev"))


# ── ledger ───────────────────────────────────────────────────────────
def test_record_then_has():
    session_evidence.record("s1", "source_credibility", subject="https://sec.gov/x",
                            verdict="pass", detail="official")
    assert session_evidence.has("s1", "source_credibility") is True
    assert session_evidence.has("s1", "source_credibility", verdict="fail") is False
    assert session_evidence.has("s1", "other_kind") is False
    assert session_evidence.has("other_session", "source_credibility") is False


def test_unknown_verdict_canonicalized_to_review():
    rec = session_evidence.record("s1", "k", verdict="totally-made-up")
    assert rec["verdict"] == "review"
    assert session_evidence.has("s1", "k", verdict="review") is True


def test_entries_filter_and_order():
    session_evidence.record("s1", "a", subject="x", verdict="pass")
    session_evidence.record("s1", "b", subject="y", verdict="fail")
    assert [e["kind"] for e in session_evidence.entries("s1")] == ["a", "b"]
    assert [e["kind"] for e in session_evidence.entries("s1", kind="b")] == ["b"]


def test_missing_session_is_empty_not_error():
    assert session_evidence.entries("nope") == []
    assert session_evidence.has("nope", "k") is False


def test_session_id_path_traversal_is_sanitized(tmp_path, monkeypatch):
    # A malicious session_id must not escape the ledger dir (no path separators;
    # the written file resolves to a direct child of the ledger dir).
    session_evidence.record("../../etc/passwd", "k", verdict="pass")
    d = (tmp_path / "ev").resolve()
    files = list(d.iterdir())
    assert files, "a file was written"
    for f in files:
        assert "/" not in f.name
        assert f.resolve().parent == d  # stayed inside the ledger dir
    # the traversal target was never created
    assert not (tmp_path.parent / "etc" / "passwd").exists()


# ── audit hook ───────────────────────────────────────────────────────
def _audit(payload: dict, *args) -> int:
    import io
    import sys
    buf = io.BytesIO(json.dumps(payload).encode())
    old = sys.stdin
    sys.stdin = type("S", (), {"buffer": buf})()
    try:
        return session_audit.cli(list(args))
    finally:
        sys.stdin = old


def test_audit_records_credible_source_from_webfetch():
    _audit({"session_id": "s2", "tool_name": "WebFetch", "tool_use_id": "t1",
            "tool_input": {"url": "https://www.sec.gov/Archives/edgar/x.htm"}},
           "--kind", "source_credibility")
    e = session_evidence.entries("s2", kind="source_credibility")
    assert len(e) == 1 and e[0]["verdict"] == "pass" and e[0]["toolUseId"] == "t1"
    assert "CREDIBLE" in e[0]["detail"]


def test_audit_records_noncredible_and_bash_url():
    _audit({"session_id": "s3", "tool_name": "Bash",
            "tool_input": {"command": "curl -s https://randomblog.example/x"}},
           "--kind", "source_credibility")
    e = session_evidence.entries("s3", kind="source_credibility")
    assert len(e) == 1 and e[0]["verdict"] == "fail"


def test_audit_noop_when_no_url():
    rc = _audit({"session_id": "s4", "tool_name": "Bash",
                 "tool_input": {"command": "echo hello"}}, "--kind", "source_credibility")
    assert rc == 0 and session_evidence.entries("s4") == []


# ── P0 self-attest fixes: parsed-hostname allowlist + fetch-only + response ──
def test_audit_substring_hostname_forgery_now_fails():
    # `evil.blog/dir.html` contains "ir." but the parsed HOST is evil.blog.
    _audit({"session_id": "sf1", "tool_name": "WebFetch",
            "tool_input": {"url": "https://evil.blog/dir.html"}},
           "--kind", "source_credibility")
    e = session_evidence.entries("sf1", kind="source_credibility")
    assert len(e) == 1 and e[0]["verdict"] == "fail"


def test_audit_echo_url_is_not_a_fetch_records_nothing():
    # A bare `echo <url>` (not curl/wget) must not mint evidence.
    rc = _audit({"session_id": "sf2", "tool_name": "Bash",
                 "tool_input": {"command": "echo https://sec.gov"}},
                "--kind", "source_credibility")
    assert rc == 0 and session_evidence.entries("sf2") == []


def test_audit_errored_fetch_records_nothing():
    # A 403/empty WebFetch response must not record a pass.
    _audit({"session_id": "sf3", "tool_name": "WebFetch",
            "tool_input": {"url": "https://sec.gov/x"},
            "tool_response": {"is_error": True, "content": ""}},
           "--kind", "source_credibility")
    assert session_evidence.entries("sf3") == []


def test_audit_official_subdomain_passes():
    _audit({"session_id": "sf4", "tool_name": "WebFetch",
            "tool_input": {"url": "https://www.sec.gov/Archives/x.htm"},
            "tool_response": {"content": "TESLA 10-Q ..."}},
           "--kind", "source_credibility")
    e = session_evidence.entries("sf4", kind="source_credibility")
    assert len(e) == 1 and e[0]["verdict"] == "pass"


# ── gate hook ────────────────────────────────────────────────────────
def _gate(payload: dict, *args) -> tuple[int, str]:
    import io
    import sys
    buf = io.BytesIO(json.dumps(payload).encode())
    old_in, old_out = sys.stdin, sys.stdout
    out = io.StringIO()
    sys.stdin = type("S", (), {"buffer": buf})()
    sys.stdout = out
    try:
        rc = session_gate.cli(list(args))
    finally:
        sys.stdin, sys.stdout = old_in, old_out
    return rc, out.getvalue()


def test_gate_denies_without_evidence():
    rc, out = _gate({"session_id": "g1", "tool_name": "mcp__trading__execute_trade"},
                    "--require-kind", "source_credibility", "--reason", "need a source")
    assert rc == 0
    decision = json.loads(out)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert decision["permissionDecisionReason"] == "need a source"


def test_gate_allows_when_evidence_present():
    session_evidence.record("g2", "source_credibility", verdict="pass")
    rc, out = _gate({"session_id": "g2", "tool_name": "mcp__trading__execute_trade"},
                    "--require-kind", "source_credibility")
    assert rc == 0 and out.strip() == ""  # silent allow


def test_gate_verdict_must_match():
    session_evidence.record("g3", "source_credibility", verdict="fail")
    rc, out = _gate({"session_id": "g3", "tool_name": "x"},
                    "--require-kind", "source_credibility", "--require-verdict", "pass")
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_gate_fails_open_without_session_id():
    rc, out = _gate({"tool_name": "x"}, "--require-kind", "k")
    assert rc == 0 and out.strip() == ""


# ── end-to-end: audit writes, gate reads (the closed gap) ────────────
def test_audit_then_gate_pipeline_allows():
    sid = "e2e"
    _audit({"session_id": sid, "tool_name": "WebFetch",
            "tool_input": {"url": "https://ir.tesla.com/q1"}}, "--kind", "source_credibility")
    rc, out = _gate({"session_id": sid, "tool_name": "mcp__trading__execute_trade"},
                    "--require-kind", "source_credibility")
    assert rc == 0 and out.strip() == ""  # credible source on record -> allowed


def test_only_noncredible_audit_still_blocks():
    sid = "e2e2"
    _audit({"session_id": sid, "tool_name": "Bash",
            "tool_input": {"command": "curl https://randomblog.example/x"}},
           "--kind", "source_credibility")
    rc, out = _gate({"session_id": sid, "tool_name": "x"},
                    "--require-kind", "source_credibility", "--require-verdict", "pass")
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


# ── project (cwd) scope ──────────────────────────────────────────────
def test_cwd_in_scope_boundaries():
    assert cwd_in_scope("/a/proj", "") is True            # empty prefix = global
    assert cwd_in_scope("/a/proj", "/a/proj") is True     # exact
    assert cwd_in_scope("/a/proj/sub", "/a/proj") is True  # descendant
    assert cwd_in_scope("/a/proj-x", "/a/proj") is False  # sibling, not a prefix match
    assert cwd_in_scope("/other", "/a/proj") is False
    assert cwd_in_scope("", "/a/proj") is False           # no cwd, scoped policy


def test_gate_out_of_scope_is_noop(tmp_path):
    # A scoped gate does not fire for a session in a different cwd, even with no
    # evidence (which would otherwise deny).
    rc, out = _gate({"session_id": "sc1", "tool_name": "x", "cwd": "/somewhere/else"},
                    "--require-kind", "source_credibility",
                    "--cwd-prefix", str(tmp_path / "proj"))
    assert rc == 0 and out.strip() == ""


def test_gate_in_scope_still_enforces(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    rc, out = _gate({"session_id": "sc2", "tool_name": "x", "cwd": str(proj)},
                    "--require-kind", "source_credibility",
                    "--cwd-prefix", str(proj))
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_audit_out_of_scope_records_nothing(tmp_path):
    rc = _audit({"session_id": "sc3", "tool_name": "WebFetch", "cwd": "/elsewhere",
                 "tool_input": {"url": "https://sec.gov/x"},
                 "tool_response": {"content": "ok"}},
                "--kind", "source_credibility", "--cwd-prefix", str(tmp_path / "proj"))
    assert rc == 0 and session_evidence.entries("sc3") == []


def test_audit_bash_comment_smuggle_records_nothing():
    # A URL hidden in a comment (fetch token inside `#`) must not mint evidence.
    rc = _audit({"session_id": "cs1", "tool_name": "Bash",
                 "tool_input": {"command": "echo done # curl https://sec.gov/x"},
                 "tool_response": {"content": "done"}},
                "--kind", "source_credibility")
    assert rc == 0 and session_evidence.entries("cs1") == []


def test_audit_real_curl_arg_records():
    _audit({"session_id": "cs2", "tool_name": "Bash",
            "tool_input": {"command": "curl -s https://www.sec.gov/x.htm"},
            "tool_response": {"content": "ok"}},
           "--kind", "source_credibility")
    e = session_evidence.entries("cs2", kind="source_credibility")
    assert len(e) == 1 and e[0]["verdict"] == "pass"


def test_audit_url_after_shell_operator_not_attributed_to_curl():
    # `curl x && echo https://sec.gov` -> the URL belongs to echo, not curl.
    rc = _audit({"session_id": "cs3", "tool_name": "Bash",
                 "tool_input": {"command": "curl -s http://localhost/ && echo https://sec.gov"},
                 "tool_response": {"content": "ok"}},
                "--kind", "source_credibility")
    e = session_evidence.entries("cs3", kind="source_credibility")
    # only localhost (the curl arg) is judged -> fail, not the sec.gov echo
    assert all(x["verdict"] == "fail" for x in e)
