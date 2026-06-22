"""v1-P1 — Persistent policy store with round-trip _normalize.

Pattern from magi-agent customize/store.py: persist override list as a
single JSON file with a normalize() pass that drops empty fields, sorts
arrays, and produces byte-stable output. Critical for the gate to detect
"nothing actually changed" cheaply and for `git diff` clarity.
"""
import json
import pytest

from magi_cp.policy.ir import Policy, Trigger, EvidenceReq
from magi_cp.policy.resolved import PolicyOverride
from magi_cp.cloud.policy_store import PolicyStore


def _make(id: str) -> Policy:
    return Policy(
        id=id, description="t", version="0.1",
        trigger=Trigger(host="claude-code", event="PreToolUse", matcher="Bash"),
        sentinel_re=r"FILE_COURT_(?P<matter>[A-Za-z0-9]+)_(?P<doc_id>[A-Za-z0-9]+)",
        requires=[EvidenceReq(step="citation_verify", verdict="pass")],
        action="block", on_signature_invalid="deny",
        gate_binary="/usr/local/bin/magi-gate.sh",
    )


def _ov(id="x", source="user", enabled=True) -> PolicyOverride:
    return PolicyOverride(policy=_make(id), source=source, enabled=enabled)


def test_empty_store_reads_empty_list(tmp_path):
    store = PolicyStore(path=str(tmp_path / "policies.json"))
    assert store.load() == []


def test_save_and_load_round_trip(tmp_path):
    """Round-trip preserves (id, source, enabled). Normalize sorts by
    (precedence, id) so 'b' (org) precedes 'a' (user) regardless of input order."""
    store = PolicyStore(path=str(tmp_path / "policies.json"))
    store.save([_ov("a"), _ov("b", source="org")])
    loaded = store.load()
    assert {(o.policy.id, o.source, o.enabled) for o in loaded} == {
        ("a", "user", True), ("b", "org", True),
    }
    # And the on-disk order is deterministic (precedence first)
    assert [(o.policy.id, o.source) for o in loaded] == [
        ("b", "org"), ("a", "user"),
    ]


def test_normalize_is_byte_stable(tmp_path):
    """Saving the same overrides twice → identical file bytes (sha-stable)."""
    import hashlib
    p1 = tmp_path / "a.json"; p2 = tmp_path / "b.json"
    PolicyStore(path=str(p1)).save([_ov("a"), _ov("b")])
    PolicyStore(path=str(p2)).save([_ov("a"), _ov("b")])
    assert hashlib.sha256(p1.read_bytes()).hexdigest() == \
           hashlib.sha256(p2.read_bytes()).hexdigest()


def test_normalize_sorts_for_determinism(tmp_path):
    """Different input order, same content → same byte output."""
    import hashlib
    p1 = tmp_path / "a.json"; p2 = tmp_path / "b.json"
    PolicyStore(path=str(p1)).save([_ov("a"), _ov("b")])
    PolicyStore(path=str(p2)).save([_ov("b"), _ov("a")])  # reversed
    assert hashlib.sha256(p1.read_bytes()).hexdigest() == \
           hashlib.sha256(p2.read_bytes()).hexdigest()


def test_load_rejects_malformed_json(tmp_path):
    p = tmp_path / "policies.json"
    p.write_text("not json")
    store = PolicyStore(path=str(p))
    with pytest.raises(ValueError, match="malformed"):
        store.load()


def test_save_creates_parent_dir(tmp_path):
    store = PolicyStore(path=str(tmp_path / "sub" / "policies.json"))
    store.save([_ov("a")])
    assert (tmp_path / "sub" / "policies.json").exists()


def test_disable_then_load_preserves_enabled_flag(tmp_path):
    store = PolicyStore(path=str(tmp_path / "policies.json"))
    store.save([_ov("a", enabled=False)])
    loaded = store.load()
    assert loaded[0].enabled is False


def test_load_fails_on_illegal_triple_in_on_disk_policy(tmp_path):
    """A hand-edited policy with an illegal (event, matcher, decision) triple
    must fail-fast at load time, not silently flow to the compiler."""
    bad = [{
        "source": "user", "enabled": True,
        "policy": {
            "id": "bad", "description": "", "version": "0.1",
            "trigger": {"host": "claude-code", "event": "PostToolUse", "matcher": "Bash"},
            "sentinel_re": r"FILE_COURT_(?P<matter>[A-Za-z0-9]+)_(?P<doc_id>[A-Za-z0-9]+)",
            "requires": [{"step": "citation_verify", "verdict": "pass"}],
            "action": "block",   # illegal: PostToolUse + block not in matrix
            "on_signature_invalid": "deny",
            "gate_binary": "/usr/local/bin/magi-gate.sh",
        },
    }]
    p = tmp_path / "policies.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match=r"item 0.*illegal combination"):
        PolicyStore(path=str(p)).load()
