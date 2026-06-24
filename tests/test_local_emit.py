"""PR2: emit.py CLI accepts both legacy (--matter/--doc-id) and canonical
(--subject/--payload-hash) flags. Legacy ones print a deprecation note to
stderr but still work end-to-end.
"""
import json
import os
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
    # no deprecation noise on the canonical path
    assert "deprecated" not in captured.err.lower()


def test_emit_accepts_legacy_matter_and_doc_id_with_warning(monkeypatch,
                                                              capsys,
                                                              tmp_path):
    monkeypatch.setenv("MAGI_CP_API_KEY", "x")
    monkeypatch.setenv("MAGI_CP_LOCAL_DIR", str(tmp_path))
    rc = _run_cli(
        ["--matter", "M1", "--doc-id", "D1"],
        monkeypatch, fake_response={"verdict": "pass", "token": "tok"},
    )
    assert rc == 0
    captured = capsys.readouterr()
    body = json.loads(captured.out)
    # Internal call still uses canonical names — populated from legacy aliases.
    assert body["_called_with"]["subject"] == "M1"
    assert body["_called_with"]["payload_hash"] == "D1"
    # Deprecation warning to stderr
    assert "deprecated" in captured.err.lower()


def test_emit_canonical_wins_when_both_supplied(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("MAGI_CP_API_KEY", "x")
    monkeypatch.setenv("MAGI_CP_LOCAL_DIR", str(tmp_path))
    rc = _run_cli(
        ["--subject", "S1", "--matter", "MX",
         "--payload-hash", "P1", "--doc-id", "DX"],
        monkeypatch, fake_response={"verdict": "pass", "token": "tok"},
    )
    assert rc == 0
    body = json.loads(capsys.readouterr().out)
    # subject/payload_hash win; legacy flags don't smuggle through
    assert body["_called_with"]["subject"] == "S1"
    assert body["_called_with"]["payload_hash"] == "P1"


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
    assert "payload-hash" in capsys.readouterr().err.lower() or \
           "payload_hash" in capsys.readouterr().err.lower()


def test_request_citation_evidence_helper_sends_both_naming_pairs(monkeypatch):
    """The underlying helper must send BOTH naming pairs in the JSON body
    so the cloud can roll forward / back across the transition."""
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
    # Both pairs present, both equal to the supplied values
    assert captured_body["subject"] == "S"
    assert captured_body["payload_hash"] == "P"
    assert captured_body["matter"] == "S"
    assert captured_body["doc_id"] == "P"
