"""LIVE smoke test — actually hit Anthropic + OpenAI APIs.

Run BEFORE the first design partner demo. Confirms:
  1. AnthropicProvider successfully calls the Messages API and returns text
  2. OpenAIProvider successfully calls the Chat Completions API and returns JSON
  3. End-to-end /policies/compile flow produces a parseable Policy IR
  4. The server-side schema validation catches a deliberately bad NL

Costs ~$0.01 in total. Requires:
  ANTHROPIC_API_KEY=sk-ant-…   (real key)
  OPENAI_API_KEY=sk-…          (real key)

Usage:
  python -m scripts.smoke_live_llm
"""
from __future__ import annotations

import json
import os
import sys


def _require_keys() -> None:
    missing = []
    if not os.environ.get("ANTHROPIC_API_KEY"):
        missing.append("ANTHROPIC_API_KEY")
    if not os.environ.get("OPENAI_API_KEY"):
        missing.append("OPENAI_API_KEY")
    if missing:
        print(f"missing required env vars: {missing}")
        sys.exit(2)


def _phase(name: str) -> None:
    print(f"\n== {name} ==")


def smoke_anthropic_minimal() -> None:
    _phase("Anthropic — minimal 'reply with JSON {\"ok\":true}'")
    from magi_cp.llm.anthropic_provider import AnthropicProvider
    p = AnthropicProvider()
    out = p.complete([
        {"role": "system", "content": "Reply ONLY with the JSON object {\"ok\":true}. No prose."},
        {"role": "user", "content": "ping"},
    ])
    print(f"  raw: {out!r}")
    obj = json.loads(out.strip().strip("`").lstrip("json").strip())
    assert obj.get("ok") is True, obj
    print("  ✓ Anthropic round-trip OK")


def smoke_openai_minimal() -> None:
    _phase("OpenAI — minimal 'reply with JSON {\"ok\":true}'")
    from magi_cp.llm.openai_provider import OpenAIProvider
    p = OpenAIProvider()
    out = p.complete([
        {"role": "system", "content": "Reply ONLY with the JSON object {\"ok\": true}."},
        {"role": "user", "content": "ping"},
    ])
    print(f"  raw: {out!r}")
    obj = json.loads(out)
    assert obj.get("ok") is True, obj
    print("  ✓ OpenAI round-trip OK")


def smoke_compile_end_to_end() -> None:
    _phase("NL→IR compile — Korean legal-filing NL")
    from magi_cp.cloud.nl_compiler import compile_with_review
    from magi_cp.llm.anthropic_provider import AnthropicProvider
    from magi_cp.llm.openai_provider import OpenAIProvider
    compiler = AnthropicProvider()
    reviewer = OpenAIProvider()
    nl = (
        "법원 filing 시 인용을 결정론으로 검증하고, "
        "검증 미통과 시 차단하라. "
        "Bash 도구의 FILE_COURT_<matter>_<doc_id> 패턴에만 적용."
    )
    result = compile_with_review(
        compiler=compiler, reviewer=reviewer,
        nl=nl, prior_turns=None,
    )
    print(f"  IR id:        {result['ir'].get('id')!r}")
    print(f"  IR matcher:   {result['ir'].get('trigger', {}).get('matcher')!r}")
    print(f"  IR on_miss:   {result['ir'].get('on_missing')!r}")
    print(f"  review.ok:    {result['review']['ok']}")
    print(f"  review.issues:{result['review']['issues']}")
    print(f"  schema_issues:{result['schema_issues']}")
    assert result["ir"].get("trigger", {}).get("matcher") == "Bash", \
        f"unexpected matcher: {result['ir']}"
    assert not result["schema_issues"], \
        f"schema issues (compiler is producing bad IR): {result['schema_issues']}"
    print("  ✓ /policies/compile end-to-end OK")


def smoke_compile_rejects_garbage() -> None:
    _phase("NL→IR compile — degenerate NL must precheck-reject")
    from magi_cp.cloud.nl_compiler import PrecheckError, compile_nl_to_ir
    from magi_cp.llm.anthropic_provider import AnthropicProvider
    p = AnthropicProvider()
    try:
        compile_nl_to_ir(p, nl="x")
    except PrecheckError as e:
        print(f"  ✓ precheck rejected as expected: {e}")
        return
    print("  ✗ precheck did NOT reject — bug")
    sys.exit(1)


def main() -> int:
    _require_keys()
    smoke_anthropic_minimal()
    smoke_openai_minimal()
    smoke_compile_rejects_garbage()
    smoke_compile_end_to_end()
    print("\nAll LIVE smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
