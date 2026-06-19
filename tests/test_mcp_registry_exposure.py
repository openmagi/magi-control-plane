"""v1.1-PA — MCP server auto-exposes registered verifiers as tools.

Once a verifier is registered, it shows up in tools/list and dispatches via
tools/call without server.py needing to know about it. This is the plug-and-play
substrate the 36-preset roadmap rides on.
"""
import io
import json

from magi_cp.mcp.server import Server
from magi_cp.verifier.protocol import (
    Verdict, Enforcement, VerifierRegistry,
)


def _rt(srv: Server, *requests: dict) -> list[dict]:
    inp = io.StringIO("\n".join(json.dumps(r) for r in requests) + "\n")
    out = io.StringIO()
    srv.serve(inp, out)
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


class _AlwaysPassVerifier:
    name = "verify_pass_stub"
    step = "stub_pass_check"
    category = "TEST"
    enforcement = Enforcement.enforcing
    description = "test stub that always passes"
    input_schema = {"type": "object", "properties": {"x": {"type": "string"}}}

    def run(self, payload: dict) -> Verdict:
        return Verdict(status="pass", reasons=[])


class _DenyOnSentinel:
    name = "verify_deny_stub"
    step = "stub_deny_check"
    category = "TEST"
    enforcement = Enforcement.enforcing
    description = "deny when payload.text contains BLOCK"
    input_schema = {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}}

    def run(self, payload: dict) -> Verdict:
        text = payload.get("text", "")
        if "BLOCK" in text:
            return Verdict(status="deny", reasons=["sentinel hit"])
        return Verdict(status="pass", reasons=[])


def test_registered_verifier_shows_in_tools_list():
    r = VerifierRegistry()
    r.register(_AlwaysPassVerifier())
    srv = Server(registry=r)
    [resp] = _rt(srv, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    assert "verify_pass_stub" in names
    # legacy tools still present (backward compat)
    assert "verify_citations" in names


def test_registered_verifier_schema_is_exposed_verbatim():
    r = VerifierRegistry()
    r.register(_DenyOnSentinel())
    srv = Server(registry=r)
    [resp] = _rt(srv, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    spec = next(t for t in resp["result"]["tools"] if t["name"] == "verify_deny_stub")
    assert spec["inputSchema"]["required"] == ["text"]
    assert "deny when payload.text contains BLOCK" in spec["description"]


def test_registered_verifier_dispatches_via_tools_call_pass():
    r = VerifierRegistry()
    r.register(_AlwaysPassVerifier())
    srv = Server(registry=r)
    [resp] = _rt(srv, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                       "params": {"name": "verify_pass_stub",
                                  "arguments": {"x": "hello"}}})
    assert "result" in resp
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["status"] == "pass"
    assert payload["reasons"] == []


def test_registered_verifier_dispatches_via_tools_call_deny():
    r = VerifierRegistry()
    r.register(_DenyOnSentinel())
    srv = Server(registry=r)
    [resp] = _rt(srv, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "verify_deny_stub",
                                  "arguments": {"text": "this contains BLOCK"}}})
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["status"] == "deny"
    assert "sentinel hit" in payload["reasons"]


def test_unknown_tool_returns_jsonrpc_error():
    srv = Server(registry=VerifierRegistry())
    [resp] = _rt(srv, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                       "params": {"name": "ghost_tool", "arguments": {}}})
    assert "error" in resp
    assert resp["error"]["code"] == -32602


def test_legacy_tools_unchanged_when_no_registry_passed():
    """Backwards compat: server without registry still serves legacy tools."""
    srv = Server()
    [resp] = _rt(srv, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    assert {"verify_citations", "lbox_fetch"}.issubset(names)


def test_registry_tool_name_collision_with_legacy_raises():
    """A registered verifier whose name collides with a legacy tool must fail
    construction — silent shadowing would let policy IR bind to the wrong code."""
    r = VerifierRegistry()
    class _Collide:
        name = "verify_citations"   # SAME as legacy
        step = "collision_step"
        category = "FACT"
        enforcement = Enforcement.enforcing
        description = "collides on purpose"
        input_schema = {"type": "object"}
        def run(self, payload): return Verdict("pass", [])
    r.register(_Collide())
    try:
        Server(registry=r)
    except ValueError as e:
        assert "collision" in str(e) or "shadow" in str(e) or "duplicate" in str(e)
    else:
        raise AssertionError("expected ValueError on name collision")
