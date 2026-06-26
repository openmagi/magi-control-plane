"""Tests for owner-applied share edits (range / hidden / redaction)."""
from __future__ import annotations

from magi_cp.share.edits import apply_share_edits, normalize_edits


def _view() -> dict:
    return {
        "schemaVersion": "openmagi.runView.v1",
        "summary": {"goal": "buy TSLA", "result": "held with secret-token-XYZ"},
        "transcript": [
            {"kind": "text", "text": "I'll read the file."},
            {"kind": "tool", "name": "Read", "status": "ok", "argsSummary": {"file_path": "/x"}},
            {"kind": "text", "text": "Verifying the source secret-token-XYZ."},
            {"kind": "tool", "name": "mcp__t__verify_source", "status": "ok", "argsSummary": {"url": "https://sec.gov/a"}},
            {"kind": "tool", "name": "mcp__t__execute_trade", "status": "needs_approval", "argsSummary": {"symbol": "TSLA"}},
            {"kind": "text", "text": "Done."},
        ],
        "governance": [
            {"name": "verify_source", "status": "ok", "kind": "verification", "reason": "CREDIBLE"},
            {"name": "execute_trade", "status": "needs_approval", "kind": "policy", "reason": "held"},
        ],
        "sources": [{"tool": "verify_source", "ref": "https://sec.gov/a", "isUrl": True}],
        "counts": {"stepCount": 3, "sourceCount": 1, "governanceCount": 2},
    }


def test_noop_when_no_edits() -> None:
    v = _view()
    assert apply_share_edits(v, {}) == v
    assert apply_share_edits(v, None) == v
    assert apply_share_edits(v, "garbage") == v


def test_range_trims_transcript_and_dependent_panels() -> None:
    # keep indices 0..3 -> Read + verify_source visible, execute_trade dropped
    out = apply_share_edits(_view(), {"range": [0, 3]})
    assert len(out["transcript"]) == 4
    names = [i.get("name") for i in out["transcript"] if i["kind"] == "tool"]
    assert "mcp__t__execute_trade" not in names
    # governance + sources for the dropped trade go away; verify_source stays
    gov_names = [g["name"] for g in out["governance"]]
    assert gov_names == ["verify_source"]
    assert len(out["sources"]) == 1
    assert out["counts"]["stepCount"] == 2
    assert out["counts"]["governanceCount"] == 1


def test_hidden_drops_specific_indices() -> None:
    out = apply_share_edits(_view(), {"hidden": [3]})  # hide verify_source tool
    names = [i.get("name") for i in out["transcript"] if i["kind"] == "tool"]
    assert "mcp__t__verify_source" not in names
    assert [g["name"] for g in out["governance"]] == ["execute_trade"]
    assert out["sources"] == []  # verify_source source dropped


def test_redaction_blanks_literal_across_free_text() -> None:
    out = apply_share_edits(_view(), {"redactions": ["secret-token-XYZ"]})
    joined = str(out["transcript"]) + str(out["summary"])
    assert "secret-token-XYZ" not in joined
    assert "[redacted]" in str(out["transcript"])


def test_normalize_clamps_and_rejects_garbage() -> None:
    assert normalize_edits({"range": [5, 2]}) == {"range": [2, 5]}  # sorted
    assert normalize_edits({"range": [-1, 3]}) == {"range": [0, 3]}  # non-negative
    assert normalize_edits({"hidden": [3, 1, 1, "x", -2]}) == {"hidden": [1, 3]}
    assert normalize_edits({"redactions": ["a", "", "  ", "b"]}) == {"redactions": ["a", "b"]}
    assert normalize_edits({"range": [True, False]}) == {}  # bools rejected
    assert normalize_edits("nope") == {}


def test_redaction_count_and_length_capped() -> None:
    many = [f"term{i}" for i in range(100)]
    n = normalize_edits({"redactions": many})
    assert len(n["redactions"]) == 50
    long = normalize_edits({"redactions": ["x" * 500]})
    assert len(long["redactions"][0]) == 200
