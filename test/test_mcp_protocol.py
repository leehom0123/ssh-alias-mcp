#!/usr/bin/env python3
"""Protocol-layer tests for the MCP server (pytest rewrite of test_mcp_server.py).

Covers the JSON-RPC / MCP wire layer only:
  - Data classes: ArgSpec schema, ToolSpec.tool()
  - Helpers: mcp_result, mcp_text, jsonrpc_error, jsonrpc_result
  - Validation: _require_object, _validate_tool_args, _negotiate_protocol_version
  - Protocol: handle_request (initialize, ping-free paths, tools/list,
    tools/call error paths, notifications, unknown methods)
  - Message: handle_message (single, batch, empty batch, error wrapping)

Offline: no SSH connection is made. Tool-layer / handler tests live in
test_mcp_tools.py.
"""
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(SKILL_DIR))

import mcp_server
from mcp_server import (
    ArgSpec, McpProtocolError,
    mcp_result, mcp_text, jsonrpc_error, jsonrpc_result,
    _require_object, _validate_tool_args, _negotiate_protocol_version,
    handle_request, handle_message,
    SUPPORTED_PROTOCOL_VERSIONS, LATEST_PROTOCOL_VERSION,
    SERVER_INFO, MCP_INSTRUCTIONS, STATIC_TOOLS, TOOL_NAME_MAX_LENGTH,
)

SAMPLE_SPECS = (
    ArgSpec("name", "string", "Name", required=True),
    ArgSpec("count", "number", "Count", default=10),
    ArgSpec("flag", "boolean", "Flag", default=False),
)


# ── 1. ArgSpec schema generation ────────────────────────────────
def test_arg_spec_required_string_has_no_default():
    s1 = ArgSpec("name", "string", "Server name", required=True)
    assert s1.schema() == {"type": "string", "description": "Server name"}


def test_arg_spec_optional_number_keeps_default():
    s2 = ArgSpec("timeout", "number", "Timeout", default=60)
    assert s2.schema().get("default") == 60


def test_arg_spec_optional_bool_keeps_false_default():
    s3 = ArgSpec("sudo", "boolean", "Run as root", default=False)
    assert s3.schema().get("default") is False


def test_arg_spec_optional_without_default_omits_key():
    s4 = ArgSpec("extra", "string", "Optional field")
    assert "default" not in s4.schema()


# ── 2. ToolSpec.tool() output ────────────────────────────────────
def test_tool_spec_no_args_shape():
    t = STATIC_TOOLS["ssh_list_servers"].tool()
    assert t["name"] == "ssh_list_servers"
    assert t["inputSchema"]["type"] == "object"
    assert t["inputSchema"].get("additionalProperties") is False
    assert "required" not in t["inputSchema"]


def test_tool_spec_required_fields_and_properties():
    t2 = STATIC_TOOLS["ssh_run"].tool()
    assert "required" in t2["inputSchema"]
    assert "server" in t2["inputSchema"].get("properties", {})
    assert "command" in t2["inputSchema"].get("properties", {})


# ── 6. Helper functions (mcp_result, mcp_text, jsonrpc_*) ───────
def test_mcp_result_default():
    assert mcp_result([{"type": "text", "text": "hi"}]) == {
        "content": [{"type": "text", "text": "hi"}], "isError": False,
    }


def test_mcp_result_is_error():
    r2 = mcp_result([], is_error=True)
    assert r2["content"] == []
    assert r2["isError"] is True


def test_mcp_text_content():
    r3 = mcp_text("hello")
    assert r3["content"][0] == {"type": "text", "text": "hello"}
    assert r3["isError"] is False


def test_jsonrpc_error_with_data():
    e1 = jsonrpc_error(1, -32602, "Bad", {"detail": "x"})
    assert e1["jsonrpc"] == "2.0"
    assert e1["id"] == 1
    assert e1["error"]["code"] == -32602
    assert e1["error"]["message"] == "Bad"
    assert e1["error"]["data"] == {"detail": "x"}


def test_jsonrpc_error_no_id_no_data():
    e2 = jsonrpc_error(None, -32700, "Parse")
    assert e2["id"] is None
    assert "data" not in e2["error"]


def test_jsonrpc_result_shape():
    res = jsonrpc_result(42, {"ok": True})
    assert res == {"jsonrpc": "2.0", "id": 42, "result": {"ok": True}}


# ── 7. _require_object ──────────────────────────────────────────
def test_require_object_passes_dict():
    assert _require_object({"a": 1}, "p") == {"a": 1}


def test_require_object_none_becomes_empty_dict():
    assert _require_object(None, "p") == {}


def test_require_object_rejects_string():
    with pytest.raises(McpProtocolError) as ei:
        _require_object("not_a_dict", "params")
    assert ei.value.code == -32602
    assert "params must be an object" in str(ei.value)


# ── 8. _validate_tool_args type checking ─────────────────────────
def test_validate_valid_args_pass():
    v = _validate_tool_args({"name": "foo", "count": 5, "flag": True}, SAMPLE_SPECS)
    assert v == {"name": "foo", "count": 5, "flag": True}


def test_validate_defaults_applied():
    v2 = _validate_tool_args({"name": "x"}, SAMPLE_SPECS)
    assert v2 == {"name": "x", "count": 10, "flag": False}


def test_validate_missing_required_raises():
    with pytest.raises(McpProtocolError) as ei:
        _validate_tool_args({}, SAMPLE_SPECS)
    assert ei.value.code == -32602
    assert "name" in str(ei.value)


def test_validate_non_string_for_string_raises():
    with pytest.raises(McpProtocolError, match="Invalid string argument: name"):
        _validate_tool_args({"name": 123}, SAMPLE_SPECS)


def test_validate_non_bool_for_boolean_raises():
    with pytest.raises(McpProtocolError, match="Invalid boolean argument: flag"):
        _validate_tool_args({"name": "x", "flag": "yes"}, SAMPLE_SPECS)


def test_validate_non_number_for_number_raises():
    with pytest.raises(McpProtocolError, match="Invalid number argument: count"):
        _validate_tool_args({"name": "x", "count": "ten"}, SAMPLE_SPECS)


def test_validate_negative_number_raises():
    with pytest.raises(McpProtocolError, match="count must be >= 0"):
        _validate_tool_args({"name": "x", "count": -1}, SAMPLE_SPECS)


def test_validate_empty_string_for_required_raises():
    with pytest.raises(McpProtocolError) as ei:
        _validate_tool_args({"name": ""}, SAMPLE_SPECS)
    assert "name" in str(ei.value)


def test_validate_unknown_arg_raises():
    with pytest.raises(McpProtocolError, match="Unknown argument"):
        _validate_tool_args({"name": "x", "bogus": 1}, SAMPLE_SPECS)


# ── 9. _negotiate_protocol_version ──────────────────────────────
@pytest.mark.parametrize(
    "params,expected",
    [( {"protocolVersion": v}, v) for v in SUPPORTED_PROTOCOL_VERSIONS]
    + [
        ({"protocolVersion": "2099-99-99"}, LATEST_PROTOCOL_VERSION),
        ({}, LATEST_PROTOCOL_VERSION),
        ({"protocolVersion": None}, LATEST_PROTOCOL_VERSION),
    ],
)
def test_negotiate_protocol_version(params, expected):
    assert _negotiate_protocol_version(params) == expected


# ── 10. handle_request — invalid requests ───────────────────────
@pytest.mark.parametrize("req", [
    [],
    {"jsonrpc": "1.0", "method": "x"},
    {"jsonrpc": "2.0"},
    {"jsonrpc": "2.0", "method": 123},
])
def test_handle_request_invalid(req):
    with pytest.raises(McpProtocolError) as ei:
        handle_request(req)
    assert ei.value.code == -32600


# ── 11. handle_request — notifications (no id -> no response) ───
@pytest.mark.parametrize("method", ["tools/list", "initialize"])
def test_handle_request_notification_returns_none(method):
    assert handle_request({"jsonrpc": "2.0", "method": method}) is None


# ── 12. handle_request — initialize handshake ───────────────────
def test_handle_request_initialize():
    resp = handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
    })
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    result = resp["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"] == SERVER_INFO
    assert result["instructions"] == MCP_INSTRUCTIONS
    assert result["capabilities"] == {"tools": {"listChanged": False}}


# ── 13. handle_request — tools/list ─────────────────────────────
def test_handle_request_tools_list():
    resp = handle_request({
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
    })
    assert resp["id"] == 2
    tools = resp["result"]["tools"]
    names = {t["name"] for t in tools}

    # Every static tool is advertised; the extras are exactly the cached
    # dynamic alias tools (empty when no server config defines aliases).
    assert set(STATIC_TOOLS) <= names
    assert names - set(STATIC_TOOLS) == set(mcp_server._alias_tool_specs())
    assert len(tools) == len(names)

    for t in tools:
        schema = t["inputSchema"]
        assert schema["type"] == "object", t["name"]
        assert schema["additionalProperties"] is False, t["name"]
        assert len(t["name"]) <= TOOL_NAME_MAX_LENGTH, t["name"]


@pytest.mark.parametrize("cursor", [None, ""])
def test_handle_request_tools_list_accepts_empty_cursor(cursor):
    resp = handle_request({
        "jsonrpc": "2.0", "id": 10, "method": "tools/list",
        "params": {"cursor": cursor},
    })
    assert "tools" in resp["result"]


def test_handle_request_tools_list_invalid_cursor():
    with pytest.raises(McpProtocolError) as ei:
        handle_request({
            "jsonrpc": "2.0", "id": 10, "method": "tools/list",
            "params": {"cursor": "some-token"},
        })
    assert ei.value.code == -32602
    assert "cursor" in str(ei.value)


# ── 15/16. tools/call validation via handle_message ─────────────
def test_tools_call_missing_required_args():
    resp = handle_message({
        "jsonrpc": "2.0", "id": 20, "method": "tools/call",
        "params": {"name": "ssh_run", "arguments": {}},
    })
    assert resp["id"] == 20
    assert resp["error"]["code"] == -32602


def test_tools_call_invalid_bool_type():
    resp = handle_message({
        "jsonrpc": "2.0", "id": 21, "method": "tools/call",
        "params": {
            "name": "ssh_run",
            "arguments": {"server": "x", "command": "echo hi", "sudo": "yes"},
        },
    })
    assert resp["error"]["code"] == -32602


def test_tools_call_invalid_number_type():
    resp2 = handle_message({
        "jsonrpc": "2.0", "id": 22, "method": "tools/call",
        "params": {
            "name": "ssh_run",
            "arguments": {"server": "x", "command": "echo hi", "timeout": "fast"},
        },
    })
    assert resp2["error"]["code"] == -32602


# ── 17. unknown method / unknown tool ────────────────────────────
def test_handle_request_unknown_method():
    with pytest.raises(McpProtocolError) as ei:
        handle_request({
            "jsonrpc": "2.0", "id": 30, "method": "foobar", "params": {},
        })
    assert ei.value.code == -32601
    assert "not found" in str(ei.value)


def test_handle_message_unknown_tool():
    resp = handle_message({
        "jsonrpc": "2.0", "id": 31, "method": "tools/call",
        "params": {"name": "no_such_tool", "arguments": {}},
    })
    assert resp["error"]["code"] == -32602
    assert "Unknown tool" in resp["error"]["message"]


# ── 18. handler exception wrapped as isError result ─────────────
def test_handle_request_handler_exception_is_wrapped():
    # Non-existent server triggers FileNotFoundError inside the handler;
    # the protocol layer must convert it into an isError result, not an
    # JSON-RPC error.
    resp = handle_request({
        "jsonrpc": "2.0", "id": 40, "method": "tools/call",
        "params": {
            "name": "ssh_run",
            "arguments": {"server": "nonexistent-xyz", "command": "echo hi",
                          "timeout": 5, "sudo": False},
        },
    })
    assert resp["id"] == 40
    assert "error" not in resp
    result = resp["result"]
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "Server config not found" in text
    assert "Tool: ssh_run" in text
    assert '"command": "echo hi"' in text


# ── 19-22. handle_message wrapping ───────────────────────────────
def test_handle_message_single():
    resp = handle_message({
        "jsonrpc": "2.0", "id": 50, "method": "initialize", "params": {},
    })
    assert isinstance(resp, dict)
    assert resp["id"] == 50
    assert "result" in resp


def test_handle_message_batch():
    batch = [
        {"jsonrpc": "2.0", "id": 60, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 61, "method": "tools/list", "params": {}},
    ]
    responses = handle_message(batch)
    assert isinstance(responses, list)
    assert len(responses) == 2
    assert responses[0]["id"] == 60
    assert responses[1]["id"] == 61


def test_handle_message_batch_notification_filtered():
    batch2 = [
        {"jsonrpc": "2.0", "method": "tools/list"},  # notification, no response
        {"jsonrpc": "2.0", "id": 62, "method": "tools/list", "params": {}},
    ]
    responses2 = handle_message(batch2)
    assert isinstance(responses2, list)
    assert len(responses2) == 1
    assert responses2[0]["id"] == 62


def test_handle_message_empty_batch():
    resp = handle_message([])
    assert resp["error"]["code"] == -32600
    assert resp["id"] is None


def test_handle_message_wraps_protocol_error():
    resp = handle_message({"jsonrpc": "2.0", "id": 70, "method": "invalid"})
    assert resp["error"]["code"] == -32601
    assert resp["id"] == 70


def test_handle_message_error_notification_gets_no_response():
    # Error with no id is a JSON-RPC notification and gets no response.
    assert handle_message({"jsonrpc": "2.0", "method": "unknown_method"}) is None
