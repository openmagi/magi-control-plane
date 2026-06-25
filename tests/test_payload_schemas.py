"""P7: CC hook payload schema menu — unit tests for the registry."""
from __future__ import annotations

import pytest

from magi_cp.policy.matrix import supported_events
from magi_cp.policy.payload_schemas import (
    PAYLOAD_SCHEMAS_BY_EVENT,
    all_schemas,
    available_fields,
)


# Every event Claude Code currently fires — every entry should have at
# least one field documented or authors will start guessing again.
#
# D79 — the pre-D58 floor of 8 events is preserved; the 22 D58
# candidates are added here too because the binary audit verified them
# end-to-end and they all have payload-schema entries now.
KNOWN_EVENTS = [
    # Pre-D58 verified surface
    "PreToolUse", "PostToolUse",
    "UserPromptSubmit",
    "Stop", "SubagentStop",
    "SessionStart", "SessionEnd",
    "PreCompact",
    # D79-promoted
    "PostToolUseFailure", "PostToolBatch",
    "PermissionRequest", "PermissionDenied",
    "UserPromptExpansion", "PostCompact",
    "Elicitation", "ElicitationResult",
    "SubagentStart", "StopFailure",
    "Setup", "Notification",
    "TeammateIdle", "TaskCreated", "TaskCompleted",
    "ConfigChange",
    "WorktreeCreate", "WorktreeRemove",
    "InstructionsLoaded",
    "CwdChanged", "FileChanged",
    "MessageDisplay",
]


def test_d79_payload_schemas_cover_every_matrix_event() -> None:
    """D79 — every event admitted by the matrix MUST have a payload
    schema entry. The chip menu / verifier authoring path would offer
    "no suggestions" for missing entries, which silently regresses to
    the pre-P7 guess-the-field mode the wizard was redesigned to
    eliminate."""
    matrix_events = supported_events()
    registry_events = set(PAYLOAD_SCHEMAS_BY_EVENT.keys())
    missing = matrix_events - registry_events
    assert not missing, (
        f"payload_schemas.py missing entries for: {sorted(missing)}"
    )
    # Every registry entry must also be in the matrix (catch typos /
    # stale events).
    extra = registry_events - matrix_events
    assert not extra, (
        f"payload_schemas.py has events the matrix doesn't recognize: "
        f"{sorted(extra)}"
    )


def test_d79_each_promoted_event_carries_its_signature_field() -> None:
    """Per-event regression pin: every previously-unverified event has
    at least one field that was lifted directly from the CC 2.1.170
    binary `hook_event_name:"<Event>"` constructor literal. A future
    refactor that accidentally drops the field list (e.g. by
    re-routing through the catch-all `_GENERIC_TOOL_FIELDS`) fails
    here loudly."""
    signature_fields: dict[str, str] = {
        "PostToolUseFailure": "error",
        "PostToolBatch": "tool_calls",
        "PermissionRequest": "permission_suggestions",
        "PermissionDenied": "reason",
        "UserPromptExpansion": "expansion_type",
        "PostCompact": "compact_summary",
        "Elicitation": "elicitation_id",
        "ElicitationResult": "action",
        "SubagentStart": "agent_id",
        "StopFailure": "last_assistant_message",
        "Setup": "trigger",
        "Notification": "notification_type",
        "TeammateIdle": "teammate_name",
        "TaskCreated": "task_id",
        "TaskCompleted": "task_id",
        "ConfigChange": "source",
        "WorktreeCreate": "name",
        "WorktreeRemove": "worktree_path",
        "InstructionsLoaded": "memory_type",
        "CwdChanged": "new_cwd",
        "FileChanged": "event",
        "MessageDisplay": "delta",
    }
    for event, sig in signature_fields.items():
        # Use a tool matcher for the tool-context events so per-tool
        # fields are spliced in too.
        matcher = "Bash" if event in {
            "PostToolUseFailure", "PostToolBatch",
            "PermissionRequest", "PermissionDenied",
        } else None
        fields = available_fields(event, matcher)
        paths = [f["path"] for f in fields]
        assert sig in paths, (
            f"{event}: signature field {sig!r} missing from registry "
            f"(got {paths})"
        )


@pytest.mark.parametrize("event", KNOWN_EVENTS)
def test_every_known_event_has_at_least_one_schema(event: str) -> None:
    bucket = PAYLOAD_SCHEMAS_BY_EVENT.get(event)
    assert bucket is not None, f"event {event!r} missing from registry"
    assert len(bucket) >= 1, f"event {event!r} has no matcher_class entries"
    for matcher_class, schema in bucket.items():
        assert schema["event"] == event
        assert schema["matcher_class"] == matcher_class
        assert len(schema["fields"]) >= 1, (
            f"{event}/{matcher_class} has zero fields — authors will guess"
        )


def test_every_field_descriptor_has_required_keys() -> None:
    """Each FieldDescriptor must carry path, type, description.
    `example` is optional. A bare `{"path": ...}` row would defeat
    the whole purpose of the chip menu."""
    for schema in all_schemas():
        for f in schema["fields"]:
            assert "path" in f and isinstance(f["path"], str) and f["path"], (
                f"missing/empty path in {schema['event']}/{schema['matcher_class']}"
            )
            assert "type" in f, f"missing type for {f['path']}"
            assert f["type"] in ("str", "int", "bool", "list", "dict"), (
                f"unknown type {f['type']!r} for {f['path']}"
            )
            assert "description" in f and len(f["description"]) > 0, (
                f"missing description for {f['path']}"
            )


def test_pretooluse_bash_exposes_command_field() -> None:
    """The canonical case: PreToolUse + Bash MUST surface
    `tool_input.command`. Every sentinel regex in the gate runs on it,
    and the SHACL menu absolutely needs this path or every shape
    targeting Bash will be vacuously satisfied."""
    fields = available_fields("PreToolUse", "Bash")
    paths = [f["path"] for f in fields]
    assert "tool_input.command" in paths, (
        f"PreToolUse+Bash missing tool_input.command; got {paths}"
    )


def test_pretooluse_webfetch_exposes_url_field() -> None:
    fields = available_fields("PreToolUse", "WebFetch")
    paths = [f["path"] for f in fields]
    assert "tool_input.url" in paths


def test_pretooluse_edit_exposes_file_path_field() -> None:
    fields = available_fields("PreToolUse", "Edit")
    paths = [f["path"] for f in fields]
    assert "tool_input.file_path" in paths
    assert "tool_input.new_string" in paths


def test_pretooluse_read_exposes_offset_and_limit() -> None:
    fields = available_fields("PreToolUse", "Read")
    paths = [f["path"] for f in fields]
    assert "tool_input.file_path" in paths
    assert "tool_input.offset" in paths
    assert "tool_input.limit" in paths


def test_pretooluse_wildcard_falls_back_to_generic_tool_input() -> None:
    """A wildcard matcher means the gate could see ANY tool. We should
    not advertise Bash-specific or Edit-specific fields because the
    runtime might not see them. The generic `tool_input` dict path is
    always present and is the only honest claim we can make."""
    fields = available_fields("PreToolUse", "*")
    paths = [f["path"] for f in fields]
    assert "tool_input" in paths, (
        "wildcard matcher should expose the generic tool_input dict "
        "(SHACL shapes targeting it never go vacuous)"
    )
    # Bash-specific must NOT appear under wildcard — that's the very
    # vacuous-satisfaction failure mode this menu exists to prevent.
    assert "tool_input.command" not in paths
    assert "tool_input.url" not in paths


def test_pretooluse_alternation_falls_back_to_generic() -> None:
    """A `Bash|Edit` alternation could match either; we don't know
    which the runtime will deliver. Same rule as wildcard."""
    fields = available_fields("PreToolUse", "Bash|Edit")
    paths = [f["path"] for f in fields]
    assert "tool_input" in paths
    assert "tool_input.command" not in paths


def test_pretooluse_mcp_matcher_falls_back_to_generic() -> None:
    """MCP tools have arbitrary input shapes — we cannot enumerate
    them ahead of time."""
    fields = available_fields("PreToolUse", "mcp__court__file")
    paths = [f["path"] for f in fields]
    assert "tool_input" in paths
    assert "tool_input.command" not in paths


def test_posttooluse_exposes_tool_response_fields() -> None:
    """After-the-fact policies (regex/llm_critic on output) need
    tool_response.output. Without it, the wizard can't honestly
    suggest a target field."""
    fields = available_fields("PostToolUse", "Bash")
    paths = [f["path"] for f in fields]
    assert "tool_response.output" in paths
    assert "tool_response.is_error" in paths


def test_stop_exposes_final_message() -> None:
    """pre_final policies need a way to reach the agent's final
    answer. Without `final_message`, SHACL shapes on the answer
    can't even be targeted."""
    fields = available_fields("Stop")
    paths = [f["path"] for f in fields]
    assert "final_message" in paths
    assert "transcript_path" in paths


def test_userpromptsubmit_exposes_prompt() -> None:
    fields = available_fields("UserPromptSubmit")
    paths = [f["path"] for f in fields]
    assert "prompt" in paths


def test_unknown_event_returns_empty() -> None:
    """Unknown events return [] so the wizard can hide the chips row
    rather than render fake suggestions."""
    assert available_fields("BogusEvent", "Bash") == []
    assert available_fields("BogusEvent") == []


def test_matcher_none_resolves_to_no_tool_for_userprompt() -> None:
    """When matcher is omitted the helper coerces to the no_tool /
    final class — UserPromptSubmit has no tool context anyway."""
    fields = available_fields("UserPromptSubmit", None)
    paths = [f["path"] for f in fields]
    assert "prompt" in paths


def test_common_envelope_includes_session_id() -> None:
    """Every tool-context schema carries session_id so cross-turn
    policies have a stable correlator."""
    for ev in ("PreToolUse", "PostToolUse"):
        fields = available_fields(ev, "Bash")
        paths = [f["path"] for f in fields]
        assert "session_id" in paths, f"{ev}+Bash missing session_id"


def test_no_duplicate_paths_in_resolved_view() -> None:
    """Resolution must not double-add a path (envelope + tool-specific
    overlap). A SHACL targetNode that points at a duplicated path
    would be ambiguous to the author."""
    fields = available_fields("PreToolUse", "Bash")
    paths = [f["path"] for f in fields]
    assert len(paths) == len(set(paths)), (
        f"duplicate paths in PreToolUse+Bash: {paths}"
    )
