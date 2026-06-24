"""D64: friendly display labels for raw payload paths.

The display_label table moves the raw `tool_input.command` etc. behind
operator-friendly names ("Bash command" / "Bash 명령어") while the raw
path stays the truth source for click-to-insert and SHACL anchoring.
These tests pin the contract so a follow-up rename of a path or a
mistyped key in the KO / EN map fails CI loudly.

Invariants:
  - Every path in the canonical KNOWN list returns a non-empty,
    non-default label in both KO and EN.
  - Unknown / operator-typed paths fall back to the raw path verbatim
    (back-compat — UI never invents a friendly name).
  - `available_fields()` carries `display_label_ko` + `display_label_en`
    on every field descriptor.
  - `all_schemas()` carries the same hints (REST endpoint contract).
"""
from __future__ import annotations

import pytest

from magi_cp.policy.payload_schemas import (
    all_schemas,
    available_fields,
    get_display_label,
)


# Per the D64 brief (Truth source section). Every entry must have a
# non-empty, non-default label in BOTH locales.
KNOWN_PATHS: list[str] = [
    "tool_input.command",
    "tool_input.url",
    "tool_input.file_path",
    "tool_input.old_string",
    "tool_input.new_string",
    "tool_input.content",
    "tool_input.cwd",
    "tool_input.timeout",
    "tool_input.description",
    "tool_input.prompt",
    "tool_input.offset",
    "tool_input.limit",
    "tool_input",
    "tool_response.output",
    "tool_response.is_error",
    "tool_response.duration_ms",
    "final_message",
    "prompt",
    "transcript_path",
    "transcript",
    "session_id",
    "tool_use_id",
    "tool_name",
    "cwd",
    "citations[].quote",
    "citations[].ref",
]


@pytest.mark.parametrize("path", KNOWN_PATHS)
def test_known_path_has_english_label(path: str) -> None:
    label = get_display_label(path, "en")
    assert label != path, (
        f"path {path!r} fell through to raw path on EN; "
        "every documented path needs a friendly label"
    )
    assert label.strip() == label
    assert len(label) > 0


@pytest.mark.parametrize("path", KNOWN_PATHS)
def test_known_path_has_korean_label(path: str) -> None:
    label = get_display_label(path, "ko")
    assert label != path, (
        f"path {path!r} fell through to raw path on KO; "
        "every documented path needs a friendly label in both locales"
    )
    assert len(label) > 0


def test_unknown_path_falls_back_to_raw() -> None:
    """An operator-typed custom path (MCP tool slug, citation
    extension on a third-party verifier) MUST fall back to the raw
    path verbatim. The UI never invents a friendly name."""
    assert get_display_label("mcp__court__file.docket_id", "en") == "mcp__court__file.docket_id"
    assert get_display_label("mcp__court__file.docket_id", "ko") == "mcp__court__file.docket_id"
    assert get_display_label("custom.field.never.heard.of", "en") == "custom.field.never.heard.of"


def test_empty_path_returns_empty() -> None:
    """Defensive: never blow up on a bare empty string. The renderer
    might pass through an unfilled chip during draft compose."""
    assert get_display_label("", "en") == ""
    assert get_display_label("", "ko") == ""


def test_unsupported_locale_degrades_to_english() -> None:
    """A future widening (e.g. ja / zh) must not crash the chip
    renderer. Unsupported locales fall back to EN, then to the raw
    path."""
    assert get_display_label("tool_input.command", "ja") == "Bash command"  # type: ignore[arg-type]


def test_bash_friendly_label_matches_brief() -> None:
    """Pin the canonical examples from the D64 brief so a follow-up
    rename of "Bash command" → "Shell command" fails the gate loudly."""
    assert get_display_label("tool_input.command", "en") == "Bash command"
    assert get_display_label("tool_input.command", "ko") == "Bash 명령어"
    assert get_display_label("tool_input.url", "en") == "Fetched URL"
    assert get_display_label("tool_input.url", "ko") == "요청 URL"
    assert get_display_label("tool_input.file_path", "en") == "File path"
    assert get_display_label("tool_input.file_path", "ko") == "파일 경로"
    assert get_display_label("tool_response.output", "en") == "Tool output"
    assert get_display_label("tool_response.output", "ko") == "도구 출력"
    assert get_display_label("final_message", "en") == "Agent final answer"
    assert get_display_label("final_message", "ko") == "에이전트 최종 답변"
    assert get_display_label("prompt", "en") == "User prompt"
    assert get_display_label("prompt", "ko") == "사용자 입력"
    assert get_display_label("transcript_path", "en") == "Conversation transcript path"
    assert get_display_label("transcript_path", "ko") == "대화 기록 경로"


def test_available_fields_carries_display_labels() -> None:
    """Every field descriptor returned by available_fields() must
    carry both display_label_ko + display_label_en. The chip renderer
    reads these directly; missing them would silently revert to the
    raw path."""
    fields = available_fields("PreToolUse", "Bash")
    assert len(fields) > 0
    for f in fields:
        assert "display_label_ko" in f, f"missing ko label on {f.get('path')}"
        assert "display_label_en" in f, f"missing en label on {f.get('path')}"
        assert len(f["display_label_ko"]) > 0
        assert len(f["display_label_en"]) > 0


def test_available_fields_friendly_label_for_command() -> None:
    """Pin the resolution: PreToolUse + Bash + tool_input.command gets
    the friendly Bash label, not the raw path. UI render uses this."""
    fields = available_fields("PreToolUse", "Bash")
    cmd = next(f for f in fields if f["path"] == "tool_input.command")
    assert cmd["display_label_en"] == "Bash command"
    assert cmd["display_label_ko"] == "Bash 명령어"


def test_all_schemas_carries_display_labels() -> None:
    """The REST endpoint dumps via `all_schemas()`. Same hint contract
    so a non-dashboard client (third-party UI, linter) sees the
    friendly labels too."""
    schemas = all_schemas()
    assert len(schemas) > 0
    for schema in schemas:
        for f in schema["fields"]:
            assert "display_label_ko" in f, (
                f"missing ko label on {schema['event']}/{f.get('path')}"
            )
            assert "display_label_en" in f, (
                f"missing en label on {schema['event']}/{f.get('path')}"
            )


def test_locale_arg_defaults_to_english() -> None:
    """No-argument lookup defaults to English. Same shape as the brief's
    primary call site (`get_display_label(path)` without an explicit
    locale)."""
    assert get_display_label("tool_input.command") == "Bash command"
