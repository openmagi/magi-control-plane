"""Map a Claude Code session transcript to an ``openmagi.runView.v1`` dict.

Claude Code writes one JSON event per line to
``~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl``. Relevant events:
  - ``type:"user"``      -> ``message.content`` (str | block list). The first
    real user message is the goal; tool-result carriers are skipped.
  - ``type:"assistant"`` -> ``message.{model,content,usage}``. Text blocks are
    the result; ``tool_use`` blocks are the trace; usage is summed.
  - ``type:"pr-link"``   -> a deliverable (PR url) for the results section.
  - ``type:"ai-title"``  -> a session title.

The output reuses the magi-agent run-view shape (summary / trace / governance,
plus ``results``/``title`` extensions) so the vendored ``build_public_run_view``
redaction and the dashboard renderer apply unchanged. Governance is overlaid by
the caller from magi-cp's own verifier verdicts (magi-cp never sees the agent's
full trace, only the verdicts it issued).

Pure and defensive: malformed events are skipped, never raised.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

__all__ = [
    "RUN_VIEW_SCHEMA_VERSION",
    "transcript_to_run_view",
]

RUN_VIEW_SCHEMA_VERSION = "openmagi.runView.v1"


def _text_of(content: object) -> str:
    """Join the text of a message ``content`` (str or block list); '' otherwise."""
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, Mapping) and block.get("type") == "text"
        ]
        return "".join(p for p in parts if isinstance(p, str)).strip()
    return ""


def _message(event: Mapping[str, object]) -> Mapping[str, object]:
    msg = event.get("message")
    return msg if isinstance(msg, Mapping) else {}


def _non_negative_int(value: object) -> int:
    try:
        result = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return result if result >= 0 else 0


def transcript_to_run_view(
    events: Sequence[Mapping[str, object]],
    *,
    session_id: str | None = None,
    governance: Sequence[Mapping[str, object]] | None = None,
) -> dict:
    """Build the per-run view from a Claude Code session's events."""
    goal: str | None = None
    result: str | None = None
    model: str | None = None
    title: str | None = None
    in_tokens = 0
    out_tokens = 0
    trace: list[dict] = []
    results: list[dict] = []
    resolved_session = session_id
    saw_assistant = False

    for event in events:
        if not isinstance(event, Mapping):
            continue
        etype = event.get("type")
        if resolved_session is None:
            sid = event.get("sessionId")
            if isinstance(sid, str) and sid:
                resolved_session = sid

        if etype == "ai-title":
            t = event.get("aiTitle")
            if isinstance(t, str) and t:
                title = t
        elif etype == "pr-link":
            url = event.get("prUrl")
            if isinstance(url, str) and url:
                results.append({"prNumber": event.get("prNumber"), "prUrl": url})
        elif etype == "user":
            text = _text_of(_message(event).get("content"))
            if text and goal is None:
                goal = text
        elif etype == "assistant":
            saw_assistant = True
            msg = _message(event)
            m = msg.get("model")
            if isinstance(m, str) and m:
                model = m
            usage = msg.get("usage")
            if isinstance(usage, Mapping):
                in_tokens += _non_negative_int(usage.get("input_tokens"))
                out_tokens += _non_negative_int(usage.get("output_tokens"))
            content = msg.get("content")
            blocks = content if isinstance(content, Sequence) and not isinstance(content, str) else []
            text = _text_of(content)
            if text:
                result = text  # last assistant text wins
            for block in blocks:
                if isinstance(block, Mapping) and block.get("type") == "tool_use":
                    trace.append(
                        {
                            "toolCallId": block.get("id"),
                            "activityType": "ToolCall",
                            "name": block.get("name"),
                            "status": "ok",
                            "argsSummary": block.get("input"),
                        }
                    )

    summary: dict | None = None
    if goal is not None or saw_assistant:
        summary = {
            "goal": goal,
            "result": result,
            "model": model,
            "status": "completed" if saw_assistant else "unknown",
            "usage": {"inputTokens": in_tokens, "outputTokens": out_tokens},
        }
        if title is not None:
            summary["title"] = title

    return {
        "schemaVersion": RUN_VIEW_SCHEMA_VERSION,
        "sessionId": resolved_session,
        "summary": summary,
        "results": results,
        "trace": trace,
        "governance": [dict(g) for g in (governance or []) if isinstance(g, Mapping)],
        "counts": {
            "stepCount": len(trace),
            "resultCount": len(results),
            "governanceCount": len(governance or []),
        },
    }
