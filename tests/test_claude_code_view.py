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


def test_tool_result_user_events_skipped_for_goal() -> None:
    # Tool-result user events must not be mistaken for the goal.
    events = [
        _tool_result("t0", "noise"),
        _user("the real goal"),
        _assistant(text="ok"),
    ]
    assert transcript_to_run_view(events)["summary"]["goal"] == "the real goal"


def test_sidechain_and_meta_user_events_skipped_for_goal() -> None:
    # Subagent prompts (isSidechain) and system reminders (isMeta) are type:user
    # but must NOT become the public headline goal.
    sidechain = {"type": "user", "sessionId": "s", "isSidechain": True,
                 "message": {"role": "user", "content": "You are a subagent. Do X."}}
    meta = {"type": "user", "sessionId": "s", "isMeta": True,
            "message": {"role": "user", "content": "<system-reminder>be nice</system-reminder>"}}
    events = [sidechain, meta, _user("the human's real ask"), _assistant(text="done")]
    assert transcript_to_run_view(events)["summary"]["goal"] == "the human's real ask"


def test_command_wrapper_user_events_skipped_for_goal() -> None:
    cmd = _user("<command-name>clear</command-name><command-message>cleared</command-message>")
    events = [cmd, _user("real goal here"), _assistant(text="ok")]
    assert transcript_to_run_view(events)["summary"]["goal"] == "real goal here"


def test_assistant_tool_only_turn_keeps_prior_result() -> None:
    events = [
        _user("g"),
        _assistant(text="partial answer", in_tok=5, out_tok=5),
        _assistant(tool={"id": "t1", "name": "Bash", "input": {"command": "ls"}}, in_tok=5, out_tok=5),
    ]
    # The last assistant turn is tool-only (no text); result stays from the prior turn.
    assert transcript_to_run_view(events)["summary"]["result"] == "partial answer"


# --- governance auto-derived from CC permission denials (real block evidence) ---
def _deny_events(cmd="curl -s https://x.test", tool="Bash", tid="t1"):
    return [
        {"type": "user", "sessionId": "s", "message": {"role": "user", "content": "check the host"}},
        {"type": "assistant", "sessionId": "s", "message": {"role": "assistant", "model": "claude-opus-4-8",
            "content": [{"type": "tool_use", "id": tid, "name": tool, "input": {"command": cmd}}],
            "usage": {"input_tokens": 5, "output_tokens": 2}}},
        {"type": "user", "sessionId": "s", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "is_error": True,
             "content": f'Permission to use {tool} with command {cmd}; echo "[exit code: $?]" has been denied.'}]}},
    ]


def test_permission_denial_becomes_governance() -> None:
    v = transcript_to_run_view(_deny_events())
    gov = v["governance"]
    assert len(gov) == 1
    assert gov[0]["name"] == "Bash"
    assert gov[0]["status"] == "blocked"
    assert gov[0]["kind"] == "policy"
    # command surfaced in reason, CC's appended echo stripped
    assert "curl -s https://x.test" in gov[0]["reason"]
    assert "exit code" not in gov[0]["reason"]
    assert v["counts"]["governanceCount"] == 1


def test_denied_tool_step_status_flipped_to_blocked() -> None:
    v = transcript_to_run_view(_deny_events())
    step = next(s for s in v["trace"] if s["toolCallId"] == "t1")
    assert step["status"] == "blocked"


def test_multiple_denials() -> None:
    ev = _deny_events("curl a", tid="t1") + _deny_events("wget b", tid="t2")[1:]
    v = transcript_to_run_view(ev)
    assert v["counts"]["governanceCount"] == 2


def test_explicit_governance_merges_with_auto() -> None:
    extra = [{"name": "FileWrite", "status": "needs_approval", "reason": "x", "kind": "policy"}]
    v = transcript_to_run_view(_deny_events(), governance=extra)
    names = {g["name"] for g in v["governance"]}
    assert names == {"Bash", "FileWrite"}


def test_no_denial_no_auto_governance() -> None:
    events = [
        {"type": "user", "sessionId": "s", "message": {"role": "user", "content": "hi"}},
        {"type": "assistant", "sessionId": "s", "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]}},
    ]
    assert transcript_to_run_view(events)["governance"] == []


# --- results dedup: the same PR is emitted as many pr-link events ---
def test_results_deduped_by_pr_url() -> None:
    events = [
        {"type": "user", "sessionId": "s", "message": {"role": "user", "content": "g"}},
        {"type": "pr-link", "sessionId": "s", "prNumber": 785, "prUrl": "https://x/y/pull/785"},
        {"type": "pr-link", "sessionId": "s", "prNumber": 785, "prUrl": "https://x/y/pull/785"},  # dup
        {"type": "pr-link", "sessionId": "s", "prNumber": 612, "prUrl": "https://x/y/pull/612"},
        {"type": "pr-link", "sessionId": "s", "prNumber": 785, "prUrl": "https://x/y/pull/785"},  # dup
    ]
    v = transcript_to_run_view(events)
    assert len(v["results"]) == 2                       # deduped
    assert [r["prNumber"] for r in v["results"]] == [785, 612]  # first-seen order
    assert v["counts"]["resultCount"] == 2


# --- sources: research evidence (WebFetch urls / WebSearch queries) ---
def _research_events():
    def asst(blocks):
        return {"type": "assistant", "sessionId": "s",
                "message": {"role": "assistant", "model": "claude-opus-4-8",
                            "content": blocks, "usage": {"input_tokens": 1, "output_tokens": 1}}}
    return [
        {"type": "user", "sessionId": "s", "message": {"role": "user", "content": "tesla financials, official sources"}},
        asst([{"type": "tool_use", "id": "s1", "name": "WebSearch", "input": {"query": "Tesla 10-Q sec.gov"}}]),
        asst([{"type": "tool_use", "id": "f1", "name": "WebFetch", "input": {"url": "https://www.sec.gov/Archives/edgar/data/tsla-20260331.htm"}}]),
        asst([{"type": "tool_use", "id": "f2", "name": "WebFetch", "input": {"url": "https://www.sec.gov/Archives/edgar/data/tsla-20260331.htm"}}]),  # dup
        asst([{"type": "text", "text": "Q1 2026 revenue $22,387M"}]),
    ]


def test_sources_extracted_from_web_tools() -> None:
    v = transcript_to_run_view(_research_events())
    src = v["sources"]
    assert len(src) == 2  # one search + one fetch (fetch dup removed)
    tools = [s["tool"] for s in src]
    assert tools == ["WebSearch", "WebFetch"]
    fetch = next(s for s in src if s["tool"] == "WebFetch")
    assert fetch["ref"] == "https://www.sec.gov/Archives/edgar/data/tsla-20260331.htm"
    assert fetch["isUrl"] is True
    search = next(s for s in src if s["tool"] == "WebSearch")
    assert search["ref"] == "Tesla 10-Q sec.gov"
    assert search["isUrl"] is False
    assert v["counts"]["sourceCount"] == 2


def test_no_sources_when_no_web_tools() -> None:
    events = [
        {"type": "user", "sessionId": "s", "message": {"role": "user", "content": "hi"}},
        {"type": "assistant", "sessionId": "s", "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]}},
    ]
    assert transcript_to_run_view(events)["sources"] == []
