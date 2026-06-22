"""P1 policy — IR + 결정론 컴파일러."""
import hashlib
import json
import re

import pytest

from magi_cp.policy import (
    Policy, Trigger, EvidenceReq,
    load_policy, compile_to_managed_settings, compile_files,
)


SAMPLE_IR = {
    "id": "legal-filing/v1",
    "version": "0.1",
    "description": "한국 법률 송무 filing",
    "trigger": {"host": "claude-code", "event": "PreToolUse", "matcher": "Bash"},
    "sentinel_re": r"FILE_COURT_(?P<matter>[A-Za-z0-9]+)_(?P<doc_id>[A-Za-z0-9]+)",
    "requires": [{"step": "citation_verify", "verdict": "pass"}],
    "action": "block",
    "on_signature_invalid": "deny",
    "gate_binary": "/usr/local/bin/magi-gate.sh",
}


def _write_policy(tmp_path, override=None):
    data = {**SAMPLE_IR}
    if override:
        data.update(override)
    p = tmp_path / "policy.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(p)


# ── IR load / validate ───────────────────────────────────────────────
def test_load_policy_ok(tmp_path):
    p = load_policy(_write_policy(tmp_path))
    assert p.id == "legal-filing/v1"
    assert p.trigger.event == "PreToolUse"
    assert p.requires[0].step == "citation_verify"


def test_load_policy_rejects_re_without_named_groups(tmp_path):
    with pytest.raises(ValueError, match="named groups"):
        load_policy(_write_policy(tmp_path, {"sentinel_re": r"FILE_COURT_\w+_\w+"}))


def test_load_policy_accepts_empty_requires_with_audit(tmp_path):
    """D31: requires=[] is the emit-signal archetype; legal with action=audit."""
    p = load_policy(_write_policy(tmp_path, {"requires": [], "action": "audit"}))
    assert p.requires == []
    assert p.action == "audit"


def test_load_policy_rejects_unsupported_event(tmp_path):
    with pytest.raises(ValueError, match="trigger.event"):
        load_policy(_write_policy(tmp_path, {"trigger": {**SAMPLE_IR["trigger"], "event": "X"}}))


def test_load_policy_rejects_unknown_action(tmp_path):
    with pytest.raises(ValueError, match="action"):
        load_policy(_write_policy(tmp_path, {"action": "xyzzy"}))


def test_load_policy_rejects_illegal_matrix_combination(tmp_path):
    """D31: PreToolUse + tool + audit is now legal, but
    PostToolUse + Bash + block is still rejected (post-event can't block)."""
    with pytest.raises(ValueError, match="illegal combination"):
        load_policy(_write_policy(tmp_path, {
            "trigger": {**SAMPLE_IR["trigger"], "event": "PostToolUse"},
            "action": "block",
        }))


def test_load_policy_accepts_legacy_on_missing_alias(tmp_path):
    """D31: existing policies stored with the on_missing wording still
    deserialize. deny→block / ask→ask / log→audit / allow→audit mapping
    handled by _coerce_action."""
    raw = {**SAMPLE_IR}
    raw.pop("action", None)
    raw["on_missing"] = "deny"
    p = tmp_path / "policy.json"
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    loaded = load_policy(str(p))
    assert loaded.action == "block"


# ── v1-P6 review fixes: server-side id pattern enforcement ──────────
@pytest.mark.parametrize("bad_id,reason", [
    # Pattern check fires first for most malformed inputs
    ("../etc/passwd", "must match"),    # leading "." disallowed (must start [A-Za-z0-9])
    ("..foo", "must match"),
    ("foo bar", "must match"),
    ("foo?bar", "must match"),
    ("foo<script>", "must match"),
    ("/abs", "must match"),
    # Pattern allows these; the dedicated checks reject them
    ("foo/compiled", "must not end"),
    ("foo/enabled", "must not end"),
    ("foo/..bar/x", "must not contain"),  # passes pattern but contains ..
    ("", "required"),
])
def test_load_policy_rejects_bad_id(tmp_path, bad_id, reason):
    with pytest.raises(ValueError, match=reason):
        load_policy(_write_policy(tmp_path, {"id": bad_id}))


def test_compiler_rejects_duplicate_policy_ids(tmp_path):
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    p1 = load_policy(_write_policy(a, {"id": "dup/v1"}))
    p2 = load_policy(_write_policy(b, {"id": "dup/v1"}))
    with pytest.raises(ValueError, match="중복"):
        compile_to_managed_settings([p1, p2])


# ── sentinel regex semantics ─────────────────────────────────────────
def test_sentinel_extracts_matter_and_doc_id(tmp_path):
    p = load_policy(_write_policy(tmp_path))
    m = re.compile(p.sentinel_re).search("echo FILE_COURT_M123_DOC1 motion.pdf")
    assert m.group("matter") == "M123"
    assert m.group("doc_id") == "DOC1"


# ── compiler: 결정론 ─────────────────────────────────────────────────
def test_compiler_is_deterministic(tmp_path):
    pol = load_policy(_write_policy(tmp_path))
    a = compile_to_managed_settings([pol])
    b = compile_to_managed_settings([pol])
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_compiler_output_shape(tmp_path):
    pol = load_policy(_write_policy(tmp_path))
    out = compile_to_managed_settings([pol])
    assert out["allowManagedHooksOnly"] is True
    assert out["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
    assert out["hooks"]["PreToolUse"][0]["hooks"][0]["type"] == "command"
    assert out["_magi_policies"][0]["id"] == "legal-filing/v1"


def test_compiler_multi_policy_preserves_order(tmp_path):
    a_dir = tmp_path / "a"; a_dir.mkdir()
    b_dir = tmp_path / "b"; b_dir.mkdir()
    p1 = load_policy(_write_policy(a_dir, {"id": "a/v1"}))
    p2 = load_policy(_write_policy(b_dir, {"id": "b/v1"}))
    out = compile_to_managed_settings([p1, p2])
    ids = [m["id"] for m in out["_magi_policies"]]
    assert ids == ["a/v1", "b/v1"]


def test_compile_files_roundtrip(tmp_path):
    ir_path = _write_policy(tmp_path)
    out_path = tmp_path / "managed.json"
    settings = compile_files([ir_path], str(out_path))
    on_disk = json.loads(out_path.read_text())
    assert on_disk == settings
    # 동일 입력 두 번 컴파일 → 동일 sha256
    out_path2 = tmp_path / "managed2.json"
    compile_files([ir_path], str(out_path2))
    h1 = hashlib.sha256(out_path.read_bytes()).hexdigest()
    h2 = hashlib.sha256(out_path2.read_bytes()).hexdigest()
    assert h1 == h2


# ── 안전성: host 'claude-code'만 지원 (v0) ──────────────────────────
def test_compiler_rejects_unknown_host(tmp_path):
    bad = load_policy(_write_policy(tmp_path))
    bad.trigger = Trigger(host="opencode", event="PreToolUse", matcher="Bash")
    with pytest.raises(ValueError, match="host"):
        compile_to_managed_settings([bad])
