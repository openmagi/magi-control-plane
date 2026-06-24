"""Producer: a Claude Code session transcript -> openmagi.runView.v1 dict.

Claude Code writes one JSON event per line to
``~/.claude/projects/<cwd>/<sessionId>.jsonl``. This maps those events to the
same run-view shape the magi-agent serializer produces, so the vendored
``build_public_run_view`` redaction + the dashboard renderer apply unchanged.
Governance is overlaid from magi-cp's own verifier verdicts.
"""
from __future__ import annotations

from magi_cp.share.claude_code_view import (
    RUN_VIEW_SCHEMA_VERSION,
    transcript_to_run_view,
)


def _assistant(text=None, *, model="claude-opus-4-8", tool=None, in_tok=10, out_tok=5):
    content = []
    content.append({"type": "thinking", "thinking": "..."})
    if text is not None:
        content.append({"type": "text", "text": text})
    if tool is not None:
        content.append({"type": "tool_use", "id": tool["id"], "name": tool["name"], "input": tool["input"]})
    return {
        "type": "assistant",
        "sessionId": "sess-1",
        "message": {
            "role": "assistant",
            "model": model,
            "content": content,
            "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
        },
    }


def _user(text):
    return {"type": "user", "sessionId": "sess-1", "message": {"role": "user", "content": text}}


def _tool_result(tool_use_id, content):
    return {
        "type": "user",
        "sessionId": "sess-1",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": content}],
        },
    }


def _events():
    return [
        {"type": "ai-title", "sessionId": "sess-1", "aiTitle": "Fix the lint errors"},
        _user("Fix all the lint errors and open a PR"),
        _assistant(text="Let me look.", tool={"id": "t1", "name": "Bash", "input": {"command": "npm run lint"}}, in_tok=100, out_tok=20),
        _tool_result("t1", "12 errors"),
        _assistant(text="Fixed 12 errors and opened PR #1234.", in_tok=200, out_tok=40),
        {"type": "pr-link", "sessionId": "sess-1", "prNumber": 1234, "prUrl": "https://github.com/x/y/pull/1234"},
    ]


def test_schema_and_session() -> None:
    view = transcript_to_run_view(_events())
    assert view["schemaVersion"] == RUN_VIEW_SCHEMA_VERSION
    assert view["sessionId"] == "sess-1"


def test_summary_goal_result_model_usage() -> None:
    s = transcript_to_run_view(_events())["summary"]
    assert s["goal"] == "Fix all the lint errors and open a PR"
    assert s["result"] == "Fixed 12 errors and opened PR #1234."
    assert s["model"] == "claude-opus-4-8"
    assert s["usage"] == {"inputTokens": 300, "outputTokens": 60}
    assert s["title"] == "Fix the lint errors"
    assert s["status"] == "completed"


def test_trace_from_tool_use_blocks() -> None:
    trace = transcript_to_run_view(_events())["trace"]
    assert [t["name"] for t in trace] == ["Bash"]
    assert trace[0]["argsSummary"] == {"command": "npm run lint"}
    assert trace[0]["activityType"] == "ToolCall"


def test_pr_link_in_results() -> None:
    results = transcript_to_run_view(_events())["results"]
    assert {"prNumber": 1234, "prUrl": "https://github.com/x/y/pull/1234"} in results


def test_governance_overlay() -> None:
    gov = [{"name": "Bash", "status": "blocked", "reason": "unsafe command", "kind": "policy"}]
    view = transcript_to_run_view(_events(), governance=gov)
    assert view["governance"] == gov


def test_user_content_as_block_list() -> None:
    events = [
        {"type": "user", "sessionId": "s", "message": {"role": "user", "content": [{"type": "text", "text": "do the thing"}]}},
        _assistant(text="done"),
    ]
    assert transcript_to_run_view(events)["summary"]["goal"] == "do the thing"


def test_empty_transcript_is_safe() -> None:
    view = transcript_to_run_view([])
    assert view["schemaVersion"] == RUN_VIEW_SCHEMA_VERSION
    assert view["summary"] is None
    assert view["trace"] == []


def test_meta_user_messages_skipped_for_goal() -> None:
    # Tool-result user events must not be mistaken for the goal.
    events = [
        _tool_result("t0", "noise"),
        _user("the real goal"),
        _assistant(text="ok"),
    ]
    assert transcript_to_run_view(events)["summary"]["goal"] == "the real goal"
