"""Stdio MCP server (JSON-RPC 2.0).

Implements the minimum MCP surface CC needs to call our tools:
  - initialize / initialized
  - tools/list
  - tools/call

Why not the official Python MCP SDK: the protocol surface we need is small,
keeping the runtime trivial avoids one more dep and one more vendor coupling.
The wire format is line-delimited JSON-RPC 2.0, which CC speaks natively.
"""
from __future__ import annotations
import json
import sys
from dataclasses import dataclass
from typing import Any, Callable

from ..verifier import Citation, verify_document
from ..verifier.sources import SourceResolver
from .lbox import fetch_by_case_number


PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "magi-cp", "version": "0.0.1"}


# ── tool implementations ─────────────────────────────────────────────
class _LboxResolver:
    """SourceResolver that resolves case numbers via law.go.kr.

    Resolved texts are cached in-process for the duration of a single tool
    invocation to avoid redundant fetches when a document cites the same case
    multiple times.
    """

    def __init__(self):
        self._cache: dict[str, str | None] = {}

    def resolve(self, case_number: str) -> str | None:
        if case_number in self._cache:
            return self._cache[case_number]
        prec = fetch_by_case_number(case_number)
        self._cache[case_number] = prec["judgment_full"] if prec else None
        return self._cache[case_number]


def _tool_verify_citations(args: dict) -> dict:
    """Verify each citation against law.go.kr (or a provided corpus override).

    args = {"document": str, "citations": [{"quote": str, "ref": str}],
            "corpus_override": {case_no: source_text}?}
    """
    citations = [Citation(c["quote"], c["ref"]) for c in args.get("citations") or []]

    override = args.get("corpus_override") or {}
    if override:
        # tests + offline use: bypass network
        from ..verifier.sources import DictResolver
        resolver: SourceResolver = DictResolver(override)
    else:
        resolver = _LboxResolver()

    doc = verify_document(citations, resolver)
    return {
        "verdict": doc.verdict,
        "citations": [
            {
                "ref": v.citation.ref,
                "case_number": v.case_number,
                "status": v.status,
                "exists": v.exists,
                "verbatim": v.verbatim,
                "reasons": v.reasons,
            }
            for v in doc.verdicts
        ],
    }


def _tool_lbox_fetch(args: dict) -> dict:
    case_no = args.get("case_number")
    if not case_no:
        raise ValueError("missing required arg: case_number")
    prec = fetch_by_case_number(case_no)
    if prec is None:
        return {"case_no": case_no, "found": False}
    return {**prec, "found": True}


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], Any]


TOOLS: list[Tool] = [
    Tool(
        name="verify_citations",
        description=("법률 인용을 결정론 검증한다. 각 인용에 대해 사건번호 존재(하드 게이트) + "
                     "원문 verbatim 대조(advisory). 가짜 판례(존재 X)는 100% 차단."),
        input_schema={
            "type": "object",
            "required": ["citations"],
            "properties": {
                "document": {"type": "string", "description": "옵션: 전체 문서 텍스트"},
                "citations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["quote", "ref"],
                        "properties": {
                            "quote": {"type": "string"},
                            "ref": {"type": "string"},
                        },
                    },
                },
                "corpus_override": {
                    "type": "object",
                    "description": "테스트/오프라인용. case_no → source text",
                },
            },
        },
        handler=_tool_verify_citations,
    ),
    Tool(
        name="lbox_fetch",
        description=("한국 판례 fetch (law.go.kr). 사건번호로 판시사항·판결요지·전문을 가져온다. "
                     "결과는 verify_citations의 코퍼스 자료로 쓰인다."),
        input_schema={
            "type": "object",
            "required": ["case_number"],
            "properties": {"case_number": {"type": "string"}},
        },
        handler=_tool_lbox_fetch,
    ),
]
_TOOL_BY_NAME = {t.name: t for t in TOOLS}


# ── JSON-RPC 2.0 server ─────────────────────────────────────────────
def _ok(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


class Server:
    def _handle(self, req: dict) -> dict | None:
        method = req.get("method")
        rid = req.get("id")
        is_notification = "id" not in req
        params = req.get("params") or {}

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "serverInfo": SERVER_INFO,
                    "capabilities": {"tools": {}},
                }
            elif method == "notifications/initialized" or method == "initialized":
                return None  # client→server notification
            elif method == "tools/list":
                result = {
                    "tools": [
                        {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
                        for t in TOOLS
                    ]
                }
            elif method == "tools/call":
                name = params.get("name")
                tool = _TOOL_BY_NAME.get(name)
                if tool is None:
                    return _err(rid, -32602, f"unknown tool: {name!r}")
                payload = tool.handler(params.get("arguments") or {})
                result = {
                    "content": [{
                        "type": "text",
                        "text": json.dumps(payload, ensure_ascii=False),
                    }],
                    "isError": False,
                }
            else:
                return _err(rid, -32601, f"method not found: {method!r}")
        except Exception as e:  # tool error
            return _err(rid, -32000, f"{type(e).__name__}: {e}")

        if is_notification:
            return None
        return _ok(rid, result)

    def serve(self, inp=None, out=None) -> None:
        inp = inp or sys.stdin
        out = out or sys.stdout
        for line in inp:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                out.write(json.dumps(_err(None, -32700, "parse error")) + "\n")
                out.flush()
                continue
            resp = self._handle(req)
            if resp is not None:
                out.write(json.dumps(resp, ensure_ascii=False) + "\n")
                out.flush()


def main() -> int:  # pragma: no cover (CLI entry)
    Server().serve()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
