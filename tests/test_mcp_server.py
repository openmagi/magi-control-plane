"""P2 MCP server — stdio JSON-RPC 2.0.

We test the server by feeding it newline-delimited JSON-RPC frames and reading
the responses. This avoids needing a live CC subprocess.
"""
import io
import json

from magi_cp.mcp.server import Server


def _rt(srv: Server, *requests: dict) -> list[dict]:
    """Round-trip: send each request line, return list of response dicts."""
    inp = io.StringIO("\n".join(json.dumps(r) for r in requests) + "\n")
    out = io.StringIO()
    srv.serve(inp, out)
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def test_initialize_handshake():
    srv = Server()
    resp = _rt(srv, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2025-06-18"}})
    assert len(resp) == 1
    assert resp[0]["id"] == 1
    assert "result" in resp[0]
    assert resp[0]["result"]["serverInfo"]["name"]


def test_tools_list_includes_verify_and_lbox():
    srv = Server()
    [resp] = _rt(srv, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tool_names = {t["name"] for t in resp["result"]["tools"]}
    assert "verify_citations" in tool_names
    assert "lbox_fetch" in tool_names


def test_tools_list_each_has_schema():
    srv = Server()
    [resp] = _rt(srv, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    for t in resp["result"]["tools"]:
        assert "inputSchema" in t
        assert t["inputSchema"]["type"] == "object"


def test_verify_citations_call_pass(monkeypatch):
    """verify_citations 툴 호출 — pass 시나리오. lbox는 모킹."""
    from magi_cp.mcp import server as mod

    def fake_fetch(case_no):
        return {"case_no": "2018도13694",
                "judgment_full":
                    "공연히 사실을 적시하여 사람의 사회적 평가를 저하시킬 만한 구체적 사실을 드러내는 것을 말한다."}
    monkeypatch.setattr(mod, "fetch_by_case_number", fake_fetch)

    srv = Server()
    [resp] = _rt(srv, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "verify_citations",
                                  "arguments": {"document": "doc",
                                                "citations": [{
                                                    "quote": "공연히 사실을 적시하여 사람의 사회적 평가를 저하시킬 만한",
                                                    "ref": "대법원 2018. 9. 13. 선고 2018도13694 판결",
                                                }]}}})
    assert "result" in resp
    content = resp["result"]["content"][0]
    assert content["type"] == "text"
    payload = json.loads(content["text"])
    assert payload["verdict"] == "pass"


def test_verify_citations_call_deny_for_fake_case(monkeypatch):
    from magi_cp.mcp import server as mod
    monkeypatch.setattr(mod, "fetch_by_case_number", lambda case_no: None)
    srv = Server()
    [resp] = _rt(srv, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                       "params": {"name": "verify_citations",
                                  "arguments": {"document": "x",
                                                "citations": [{"quote": "임의",
                                                              "ref": "대법원 2099. 1. 1. 선고 2099도99999 판결"}]}}})
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["verdict"] == "deny"
    assert any(c["status"] == "missing" for c in payload["citations"])


def test_unknown_method_returns_error():
    srv = Server()
    [resp] = _rt(srv, {"jsonrpc": "2.0", "id": 5, "method": "no/such/method"})
    assert "error" in resp
    assert resp["error"]["code"] == -32601


def test_unknown_tool_returns_error():
    srv = Server()
    [resp] = _rt(srv, {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                       "params": {"name": "ghost", "arguments": {}}})
    assert "error" in resp


def test_notification_returns_no_response():
    """JSON-RPC notification (no id) gets no response."""
    srv = Server()
    resps = _rt(srv, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert resps == []


def test_lbox_fetch_call(monkeypatch):
    from magi_cp.mcp import server as mod

    def fake_fetch(case_no):
        return {"case_no": "X", "title": "T", "judgment_full": "FULL"}
    monkeypatch.setattr(mod, "fetch_by_case_number", fake_fetch)

    srv = Server()
    [resp] = _rt(srv, {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                       "params": {"name": "lbox_fetch",
                                  "arguments": {"case_number": "X"}}})
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["case_no"] == "X"
    assert payload["judgment_full"] == "FULL"
