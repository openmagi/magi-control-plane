"""P7 (issue #1) — JSON → RDF lift + SHACL target lint.

These tests cover the new helpers added to
src/magi_cp/policy/payload_schemas.py to close the silent fail-open
hole the prior chip menu advertised but didn't enforce.
"""
from __future__ import annotations

import pytest

from magi_cp.policy.payload_schemas import (
    MAGI_HOOK_NS,
    MAGI_HOOK_SUBJECT,
    available_fields,
    extract_targets,
    lift_payload_to_data_graph,
    lint_shacl_targets,
)


pytest.importorskip("rdflib", reason="rdflib required for lift/lint helpers")


# ── Edit / Write split (P2 fix) ────────────────────────────────────

def test_edit_fields_do_not_leak_into_write():
    """A wizard scoping policy to Write must NOT see Edit-only fields,
    because a SHACL shape targeting them would be vacuously satisfied
    at runtime — exactly the silent fail-open mode P7 closes."""
    write_paths = {f["path"] for f in available_fields("PreToolUse", "Write")}
    assert "tool_input.file_path" in write_paths
    assert "tool_input.content" in write_paths
    assert "tool_input.old_string" not in write_paths
    assert "tool_input.new_string" not in write_paths


def test_write_content_does_not_leak_into_edit():
    edit_paths = {f["path"] for f in available_fields("PreToolUse", "Edit")}
    assert "tool_input.file_path" in edit_paths
    assert "tool_input.old_string" in edit_paths
    assert "tool_input.new_string" in edit_paths
    assert "tool_input.content" not in edit_paths


# ── sh_datatype + sh_kind hints (P1 #5 fix) ────────────────────────

def test_string_field_carries_xsd_string_datatype():
    fields = {f["path"]: f for f in available_fields("PreToolUse", "Bash")}
    cmd = fields["tool_input.command"]
    assert cmd.get("sh_datatype") == "xsd:string"
    assert cmd.get("sh_kind") == "property"


def test_int_field_carries_xsd_integer_datatype():
    fields = {f["path"]: f for f in available_fields("PreToolUse", "Bash")}
    timeout = fields["tool_input.timeout"]
    assert timeout.get("sh_datatype") == "xsd:integer"
    assert timeout.get("sh_kind") == "property"


def test_dict_field_carries_rdf_json_and_node_kind():
    fields = {f["path"]: f for f in available_fields("PreToolUse", "*")}
    ti = fields["tool_input"]
    assert ti.get("sh_datatype") == "rdf:JSON"
    assert ti.get("sh_kind") == "node"


# ── JSON → RDF lift (P0 #1 fix) ───────────────────────────────────

def test_lift_materializes_bash_command_under_canonical_namespace():
    """A SHACL shape targeting `magi:tool_input.command` must find at
    least one focus node when the runtime sees a Bash call — that's
    the whole P0 fix. We assert the triple exists on the lifted graph."""
    import rdflib

    payload = {"tool_input": {"command": "rm -rf /"}}
    g = lift_payload_to_data_graph(payload, event="PreToolUse", matcher="Bash")
    pred = rdflib.URIRef(MAGI_HOOK_NS + "tool_input.command")
    subj = rdflib.URIRef(MAGI_HOOK_SUBJECT)
    assert (subj, pred, rdflib.Literal("rm -rf /")) in g


def test_lift_marks_hook_with_rdf_type():
    """`sh:targetClass magi:Hook` must land on exactly one focus node
    per hook firing — so the lift adds `<:hook> a magi:Hook`."""
    import rdflib

    g = lift_payload_to_data_graph(
        {"tool_input": {"command": "ls"}},
        event="PreToolUse", matcher="Bash",
    )
    subj = rdflib.URIRef(MAGI_HOOK_SUBJECT)
    hook_cls = rdflib.URIRef(MAGI_HOOK_NS + "Hook")
    assert (subj, rdflib.RDF.type, hook_cls) in g


def test_lift_omits_fields_not_in_schema():
    """Authoring contract: the menu IS the runtime contract. A JSON
    key the menu doesn't advertise must not appear in the data
    graph — otherwise authors could rely on undocumented paths and
    the menu would no longer be honest."""
    import rdflib

    g = lift_payload_to_data_graph(
        {"tool_input": {"command": "ls", "bogus": "x"}},
        event="PreToolUse", matcher="Bash",
    )
    bogus = rdflib.URIRef(MAGI_HOOK_NS + "tool_input.bogus")
    assert (None, bogus, None) not in g


def test_lift_dict_fields_serialize_as_json_literal():
    """When the matcher is wildcard, the menu carries `tool_input` as
    a dict; the lift emits a JSON-literal so SHACL `sh:pattern`
    constraints still work on the serialized form."""
    import rdflib

    g = lift_payload_to_data_graph(
        {"tool_input": {"command": "ls"}},
        event="PreToolUse", matcher="*",
    )
    pred = rdflib.URIRef(MAGI_HOOK_NS + "tool_input")
    objs = [o for _, _, o in g.triples((None, pred, None))]
    assert len(objs) == 1
    lit = str(objs[0])
    assert "command" in lit and "ls" in lit


# ── extract_targets + lint_shacl_targets (P0 #3 fix) ──────────────

def _shape(target_node: str = "", target_class: str = "", path: str = "") -> str:
    parts = [
        "@prefix sh:   <http://www.w3.org/ns/shacl#> .",
        f"@prefix magi: <{MAGI_HOOK_NS}> .",
        "[]",
    ]
    body = []
    if target_node:
        body.append(f"sh:targetNode magi:{target_node}")
    if target_class:
        body.append(f"sh:targetClass magi:{target_class}")
    if path:
        body.append(f"sh:path magi:{path}")
    body.append("sh:minCount 1")
    return parts[0] + "\n" + parts[1] + "\n[] " + " ; ".join(body) + " ."


def test_extract_targets_finds_target_node():
    targets = extract_targets(_shape(target_node="tool_input.command"))
    assert "tool_input.command" in targets["targetNode"]


def test_extract_targets_finds_target_class():
    targets = extract_targets(_shape(target_class="Hook"))
    assert "Hook" in targets["targetClass"]


def test_extract_targets_finds_path():
    targets = extract_targets(_shape(path="tool_input.command"))
    assert "tool_input.command" in targets["path"]


def test_lint_passes_when_path_in_menu():
    issues = lint_shacl_targets(
        _shape(path="tool_input.command"),
        event="PreToolUse", matcher="Bash",
    )
    assert issues == []


def test_lint_flags_unknown_path():
    issues = lint_shacl_targets(
        _shape(path="tool_input.bogus"),
        event="PreToolUse", matcher="Bash",
    )
    assert len(issues) == 1
    assert "magi:tool_input.bogus" in issues[0]
    assert "PreToolUse" in issues[0]


def test_lint_flags_unknown_target_node():
    issues = lint_shacl_targets(
        _shape(target_node="tool_input.bogus"),
        event="PreToolUse", matcher="Bash",
    )
    assert any("magi:tool_input.bogus" in m for m in issues)


def test_lint_accepts_target_class_hook():
    issues = lint_shacl_targets(
        _shape(target_class="Hook"),
        event="PreToolUse", matcher="Bash",
    )
    assert issues == []


def test_lint_flags_unknown_target_class():
    issues = lint_shacl_targets(
        _shape(target_class="BogusType"),
        event="PreToolUse", matcher="Bash",
    )
    assert any("BogusType" in m for m in issues)


def test_lint_suggests_close_match_for_typos():
    issues = lint_shacl_targets(
        _shape(path="tool_input.commandz"),  # one-char typo
        event="PreToolUse", matcher="Bash",
    )
    assert any("did you mean" in m for m in issues)


def test_lint_ignores_non_magi_namespace():
    """A shape anchored on an unrelated namespace is out-of-contract;
    we don't flag it (callers may surface a separate banner). This
    keeps existing legal-vertical shapes that target their own
    namespace from spuriously warning."""
    ttl = (
        "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
        "@prefix ex: <http://example.com/> .\n"
        "[] sh:targetClass ex:Filing ; sh:minCount 1 .\n"
    )
    assert lint_shacl_targets(ttl, "PreToolUse", "Bash") == []


# ── Policy.validate strict-mode integration (P0 #3 / P1 #4) ───────

def test_policy_validate_strict_raises_on_unknown_path(monkeypatch):
    """Under MAGI_CP_STRICT_SHACL_TARGETS=1 the IR refuses to load a
    policy whose SHACL shape targets a path the runtime never
    delivers. This is the canonical line of defence — the cloud's PUT
    /policies handler routes through Policy.__post_init__ so a
    direct admin-key holder also gets blocked."""
    from magi_cp.policy.ir import EvidenceReq, Policy, Trigger

    monkeypatch.setenv("MAGI_CP_STRICT_SHACL_TARGETS", "1")
    bad = EvidenceReq(
        kind="shacl",
        shape_ttl=_shape(path="tool_input.bogus"),
    )
    with pytest.raises(ValueError, match="SHACL lint"):
        Policy(
            id="p/v1", description="",
            trigger=Trigger(host="claude-code", event="PreToolUse",
                            matcher="Bash"),
            requires=[bad], action="block",
        )


def test_policy_validate_default_mode_collects_warnings(monkeypatch):
    """In the default (non-strict) mode, the policy loads but the
    lint issues surface via `_shacl_lint_issues` so the dashboard
    server-side renderer can warn. Mirrors the warn-banner contract."""
    from magi_cp.policy.ir import EvidenceReq, Policy, Trigger

    monkeypatch.delenv("MAGI_CP_STRICT_SHACL_TARGETS", raising=False)
    bad = EvidenceReq(
        kind="shacl",
        shape_ttl=_shape(path="tool_input.bogus"),
    )
    p = Policy(
        id="p/v1", description="",
        trigger=Trigger(host="claude-code", event="PreToolUse",
                        matcher="Bash"),
        requires=[bad], action="block",
    )
    assert p._shacl_lint_issues
    assert any("tool_input.bogus" in m for m in p._shacl_lint_issues)


def test_policy_validate_clean_shape_no_issues():
    from magi_cp.policy.ir import EvidenceReq, Policy, Trigger

    good = EvidenceReq(
        kind="shacl",
        shape_ttl=_shape(path="tool_input.command"),
    )
    p = Policy(
        id="p/v1", description="",
        trigger=Trigger(host="claude-code", event="PreToolUse",
                        matcher="Bash"),
        requires=[good], action="block",
    )
    assert p._shacl_lint_issues == []
