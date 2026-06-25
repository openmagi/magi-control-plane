"""D35: EvidenceReq discriminated union — kind=step/regex/llm_critic/shacl."""
import json

import pytest

from magi_cp.policy.ir import (
    EvidenceReq, Policy, Trigger, load_policy, _coerce_evidence_req,
)
from magi_cp.cloud.policy_store import _evidence_req_to_dict


_OK_SENTINEL = r"FILE_(?P<matter>[A-Za-z0-9]+)_(?P<doc_id>[A-Za-z0-9]+)"
_TRIGGER = Trigger(host="claude-code", event="PreToolUse", matcher="Bash")


def _policy_with(requires):
    return Policy(
        id="p/v1", description="t", version="0.1",
        trigger=_TRIGGER, sentinel_re=_OK_SENTINEL,
        requires=requires, action="block",
        on_signature_invalid="deny",
        gate_binary="/usr/local/bin/magi-gate.sh",
    )


# ── kind=step (default) — back-compat ─────────────────────────────
def test_default_kind_is_step():
    """Existing `{step, verdict}` rows round-trip — kind defaults to step."""
    req = _coerce_evidence_req({"step": "citation_verify", "verdict": "pass"})
    assert req.kind == "step"
    assert req.step == "citation_verify"
    assert req.verdict == "pass"


def test_step_kind_round_trip_serialization():
    """Legacy serialization shape preserved: no `kind` key for step."""
    req = EvidenceReq(kind="step", step="citation_verify")
    d = _evidence_req_to_dict(req)
    assert d == {"step": "citation_verify", "verdict": "pass"}


def test_step_kind_rejects_empty_step():
    bad = EvidenceReq(kind="step", step="")
    with pytest.raises(ValueError, match="non-empty `step`"):
        bad.validate()


# ── kind=regex ────────────────────────────────────────────────────
def test_regex_kind_accepts_valid_pattern():
    req = _coerce_evidence_req({"kind": "regex", "pattern": r"\bSECRET_\w+"})
    req.validate()
    assert req.kind == "regex"
    assert req.pattern == r"\bSECRET_\w+"


def test_regex_kind_rejects_uncompilable_pattern():
    bad = EvidenceReq(kind="regex", pattern="(")
    with pytest.raises(ValueError, match="fails to compile"):
        bad.validate()


def test_regex_kind_round_trip():
    req = EvidenceReq(kind="regex", pattern=r"AKIA[0-9A-Z]{16}")
    d = _evidence_req_to_dict(req)
    assert d == {"kind": "regex", "pattern": r"AKIA[0-9A-Z]{16}"}
    reloaded = _coerce_evidence_req(d)
    assert reloaded.kind == "regex"
    assert reloaded.pattern == r"AKIA[0-9A-Z]{16}"


def test_regex_pattern_length_capped():
    bad = EvidenceReq(kind="regex", pattern="a" * 2001)
    with pytest.raises(ValueError, match="too long"):
        bad.validate()


# ── D82c fix: kind=regex field_path scoping ───────────────────────


def test_regex_field_path_round_trips_when_set():
    """Saved policies carry the field_path scoping choice through
    ser/deser so a wizard-authored regex doesn't regress to whole-
    payload on reload."""
    req = EvidenceReq(
        kind="regex",
        pattern=r"\bSSN\b",
        field_path="tool_response.output",
    )
    req.validate()
    d = _evidence_req_to_dict(req)
    assert d == {
        "kind": "regex",
        "pattern": r"\bSSN\b",
        "field_path": "tool_response.output",
    }
    reloaded = _coerce_evidence_req(d)
    assert reloaded.field_path == "tool_response.output"


def test_regex_no_field_path_omits_key_on_serialize():
    """Pre-D82c regex rows round-trip byte-identical (no field_path
    key when empty). Without this, the policy_store would diff on
    every load."""
    req = EvidenceReq(kind="regex", pattern=r"\bSSN\b")
    d = _evidence_req_to_dict(req)
    assert d == {"kind": "regex", "pattern": r"\bSSN\b"}
    assert "field_path" not in d


def test_regex_field_path_must_be_dotted_identifier():
    """Garbage paths are caught at validate-time — a typo like
    `foo bar` would otherwise silently degrade to whole-payload."""
    bad = EvidenceReq(
        kind="regex", pattern="x", field_path="foo bar",
    )
    with pytest.raises(ValueError, match="dotted-identifier"):
        bad.validate()


def test_regex_field_path_length_capped():
    bad = EvidenceReq(
        kind="regex", pattern="x", field_path="a." * 200,
    )
    with pytest.raises(ValueError, match="too long"):
        bad.validate()


# ── kind=llm_critic ───────────────────────────────────────────────
def test_llm_critic_kind_accepts_short_criterion():
    req = _coerce_evidence_req({
        "kind": "llm_critic",
        "criterion": "Does the output cite at least one verified source?",
    })
    req.validate()
    assert req.kind == "llm_critic"


def test_llm_critic_rejects_empty_criterion():
    bad = EvidenceReq(kind="llm_critic", criterion="")
    with pytest.raises(ValueError, match="non-empty `criterion`"):
        bad.validate()


def test_llm_critic_round_trip():
    req = EvidenceReq(kind="llm_critic", criterion="Output must be polite")
    d = _evidence_req_to_dict(req)
    assert d == {"kind": "llm_critic", "criterion": "Output must be polite"}


# ── kind=shacl ────────────────────────────────────────────────────
def test_shacl_kind_accepts_shape():
    ttl = "@prefix sh: <http://www.w3.org/ns/shacl#> ."
    req = _coerce_evidence_req({"kind": "shacl", "shape_ttl": ttl})
    req.validate()
    assert req.kind == "shacl"


def test_shacl_rejects_empty_shape():
    bad = EvidenceReq(kind="shacl", shape_ttl="")
    with pytest.raises(ValueError, match="non-empty `shape_ttl`"):
        bad.validate()


def test_shacl_round_trip():
    req = EvidenceReq(kind="shacl", shape_ttl="@prefix sh: <foo> .")
    d = _evidence_req_to_dict(req)
    assert d == {"kind": "shacl", "shape_ttl": "@prefix sh: <foo> ."}


# ── unknown kind rejected ─────────────────────────────────────────
def test_unknown_kind_rejected():
    bad = EvidenceReq(kind="quantum")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unsupported kind"):
        bad.validate()


# ── Policy validation propagates per-entry errors ─────────────────
def test_policy_validate_surfaces_bad_evidence_with_index():
    """Bad requires entry must be reported with its index for clear
    operator feedback."""
    with pytest.raises(ValueError, match=r"requires\[1\]"):
        _policy_with([
            EvidenceReq(kind="step", step="citation_verify"),
            EvidenceReq(kind="regex", pattern=""),  # bad
        ])


def test_policy_accepts_mixed_kind_requires():
    """A single policy can hold step + regex + llm + shacl all at once.
    The semantics is "all must pass" so kinds are independent."""
    _policy_with([
        EvidenceReq(kind="step", step="citation_verify"),
        EvidenceReq(kind="regex", pattern=r"\bAPPROVED\b"),
        EvidenceReq(kind="llm_critic", criterion="Output is professional"),
        EvidenceReq(kind="shacl", shape_ttl="@prefix sh: <foo> ."),
    ])


# ── load_policy round-trip ────────────────────────────────────────
def test_load_policy_round_trips_mixed_kinds(tmp_path):
    raw = {
        "id": "mixed/v1",
        "description": "t",
        "version": "0.1",
        "trigger": {"host": "claude-code", "event": "PreToolUse", "matcher": "Bash"},
        "sentinel_re": _OK_SENTINEL,
        "requires": [
            {"step": "citation_verify", "verdict": "pass"},
            {"kind": "regex", "pattern": r"\bAKIA\w+"},
            {"kind": "llm_critic", "criterion": "Polite tone"},
        ],
        "action": "block",
        "on_signature_invalid": "deny",
        "gate_binary": "/usr/local/bin/magi-gate.sh",
    }
    p = tmp_path / "p.json"
    p.write_text(json.dumps(raw))
    loaded = load_policy(str(p))
    assert [r.kind for r in loaded.requires] == ["step", "regex", "llm_critic"]
    assert loaded.requires[1].pattern == r"\bAKIA\w+"
    assert loaded.requires[2].criterion == "Polite tone"
