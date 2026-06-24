"""PR4: emit.py CLI accepts ONLY canonical (--subject/--payload-hash).
The legacy `--matter`/`--doc-id` flags are now a hard exit-2 with a
clear "deprecated, use ..." message — no silent acceptance.
"""
import json
import sys

import pytest


def _run_cli(args: list[str], monkeypatch, fake_response: dict):
    """Invoke emit.cli() with sys.argv set, stubbing the HTTP call."""
    from magi_cp.local import emit

    def _fake(*, subject, payload_hash, document, citations, corpus,
              cloud_url, api_key, **_):
        return {"_called_with": {
            "subject": subject, "payload_hash": payload_hash,
            "document": document, "cloud_url": cloud_url,
        }, **fake_response}

    monkeypatch.setattr(emit, "request_citation_evidence", _fake)
    monkeypatch.setattr(sys, "argv", ["magi-cp-emit"] + args)
    rc = emit.cli()
    return rc


def test_emit_accepts_canonical_subject_and_payload_hash(monkeypatch, capsys,
                                                          tmp_path):
    monkeypatch.setenv("MAGI_CP_API_KEY", "x")
    monkeypatch.setenv("MAGI_CP_LOCAL_DIR", str(tmp_path))
    rc = _run_cli(
        ["--subject", "S1", "--payload-hash", "P1"],
        monkeypatch, fake_response={"verdict": "pass", "token": "tok"},
    )
    assert rc == 0
    captured = capsys.readouterr()
    body = json.loads(captured.out)
    assert body["_called_with"]["subject"] == "S1"
    assert body["_called_with"]["payload_hash"] == "P1"
    # No deprecation noise on the canonical path
    assert "deprecated" not in captured.err.lower()


def test_emit_legacy_matter_is_hard_error(monkeypatch, capsys, tmp_path):
    """PR4: --matter is a clean exit-2, not a warn-and-proceed."""
    monkeypatch.setenv("MAGI_CP_API_KEY", "x")
    monkeypatch.setenv("MAGI_CP_LOCAL_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv",
                        ["magi-cp-emit", "--matter", "M1",
                         "--payload-hash", "P1"])
    from magi_cp.local import emit
    rc = emit.cli()
    assert rc == 2
    err = capsys.readouterr().err.lower()
    assert "matter" in err
    assert "deprecated" in err
    assert "--subject" in err


def test_emit_legacy_doc_id_is_hard_error(monkeypatch, capsys, tmp_path):
    """PR4: --doc-id is a clean exit-2, not a warn-and-proceed."""
    monkeypatch.setenv("MAGI_CP_API_KEY", "x")
    monkeypatch.setenv("MAGI_CP_LOCAL_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv",
                        ["magi-cp-emit", "--subject", "S1",
                         "--doc-id", "D1"])
    from magi_cp.local import emit
    rc = emit.cli()
    assert rc == 2
    err = capsys.readouterr().err.lower()
    assert "doc-id" in err or "doc_id" in err
    assert "deprecated" in err
    assert "--payload-hash" in err


def test_emit_missing_subject_is_error(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("MAGI_CP_API_KEY", "x")
    monkeypatch.setenv("MAGI_CP_LOCAL_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["magi-cp-emit", "--payload-hash", "P"])
    from magi_cp.local import emit
    rc = emit.cli()
    assert rc == 2
    assert "subject" in capsys.readouterr().err.lower()


def test_emit_missing_payload_hash_is_error(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("MAGI_CP_API_KEY", "x")
    monkeypatch.setenv("MAGI_CP_LOCAL_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["magi-cp-emit", "--subject", "S"])
    from magi_cp.local import emit
    rc = emit.cli()
    assert rc == 2
    err = capsys.readouterr().err.lower()
    assert "payload-hash" in err or "payload_hash" in err


def test_request_citation_evidence_helper_sends_canonical_only(monkeypatch):
    """PR4: the underlying helper sends ONLY subject + payload_hash in
    the JSON body. Legacy mirror keys are not present (cloud's
    extra="forbid" would 422 if they were)."""
    captured_body: dict = {}

    class _FakeResponse:
        def __init__(self, payload: bytes):
            self._payload = payload
        def read(self): return self._payload
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def _fake_urlopen(req, timeout=15):
        nonlocal captured_body
        captured_body.update(json.loads(req.data.decode()))
        return _FakeResponse(b'{"verdict":"pass","token":"tok"}')

    from magi_cp.local import emit
    monkeypatch.setattr(emit.urllib.request, "urlopen", _fake_urlopen)
    out = emit.request_citation_evidence(
        subject="S", payload_hash="P", document="",
        citations=[], corpus={},
        cloud_url="http://x", api_key="k",
    )
    assert out["verdict"] == "pass"
    assert captured_body["subject"] == "S"
    assert captured_body["payload_hash"] == "P"
    # PR4: legacy mirror keys are gone — body must NOT carry them, or
    # the cloud's `extra="forbid"` validator would reject the request.
    assert "matter" not in captured_body
    assert "doc_id" not in captured_body


def test_request_citation_evidence_helper_rejects_legacy_kwargs():
    """PR4: passing `matter=` or `doc_id=` as kwargs is a clean
    TypeError at the Python boundary."""
    from magi_cp.local import emit
    with pytest.raises(TypeError):
        emit.request_citation_evidence(
            matter="M",  # type: ignore[call-arg]
            subject="S", payload_hash="P",
            cloud_url="http://x", api_key="k",
        )
