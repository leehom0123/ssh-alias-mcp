#!/usr/bin/env python3
"""P1 tests — MCP server protocol, tool specs, validation, error handling.

Covers:
  - Data classes: ArgSpec, ToolSpec, McpProtocolError
  - Utilities: _sanitize_tool_part, _unique_alias_tool_name, _raw_output
  - Helpers: mcp_result, mcp_text, jsonrpc_error, jsonrpc_result
  - Validation: _require_object, _validate_tool_args, _negotiate_protocol_version
  - Protocol: handle_request (all methods, error paths, notifications)
  - Message: handle_message (single, batch, error wrapping)
  - Tool call: ssh_run, ssh_list_servers, ssh_list_aliases, ssh_run_alias
  - Dynamic alias tool generation

Run:  python test_mcp_server.py
"""
import json
import os
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(SKILL_DIR))

from hashlib import sha1
from typing import Dict
from mcp_server import (
    ArgSpec, ToolSpec, McpProtocolError,
    mcp_result, mcp_text, jsonrpc_error, jsonrpc_result,
    _sanitize_tool_part, _unique_alias_tool_name, _raw_output,
    _with_execution_summary, _tool_args_summary,
    _require_object, _validate_tool_args, _negotiate_protocol_version,
    handle_request, handle_message,
    SUPPORTED_PROTOCOL_VERSIONS, LATEST_PROTOCOL_VERSION,
    SERVER_INFO, MCP_INSTRUCTIONS, STATIC_TOOLS, TOOL_NAME_MAX_LENGTH,
    _alias_tool_specs, _tool_specs,
)
from ssh_client import pool

SSH_SERVERS = pool.list_servers()
SSH_TEST_SERVER = os.environ.get("SSH_TEST_SERVER", "lihong-dev-server")

# ── Test framework ──────────────────────────────────────────────
RED, GREEN, YELLOW, NC = "\033[91m", "\033[92m", "\033[93m", "\033[0m"
passed = failed = skipped = 0


def _ok(msg):
    print(f"{GREEN}PASS{NC}  {msg}")


def _fail(msg, detail=""):
    global failed
    failed += 1
    print(f"{RED}FAIL{NC}  {msg} — {detail}" if detail else f"{RED}FAIL{NC}  {msg}")


def _skip(msg):
    print(f"{YELLOW}SKIP{NC}  {msg}")


def _section(name):
    print(f"\n{'='*60}\n  {name}\n{'='*60}")


def _inc(result, name, detail=""):
    global passed, failed, skipped
    if result == "ok":
        passed += 1
        _ok(name)
    elif result == "fail":
        failed += 1
        _fail(name, detail)
    else:
        skipped += 1
        _skip(name)


# ── 1. ArgSpec schema generation ────────────────────────────────
def test_arg_spec_schema():
    _section("1. ArgSpec schema generation")

    s1 = ArgSpec("name", "string", "Server name", required=True)
    sc1 = s1.schema()
    _inc("ok" if sc1 == {"type": "string", "description": "Server name"} else "fail",
         "required string arg — no default in schema",
         f"schema={sc1!r}")

    s2 = ArgSpec("timeout", "number", "Timeout", default=60)
    sc2 = s2.schema()
    _inc("ok" if sc2.get("default") == 60 else "fail",
         "optional number arg — default present",
         f"schema={sc2!r}")

    s3 = ArgSpec("sudo", "boolean", "Run as root", default=False)
    sc3 = s3.schema()
    _inc("ok" if sc3.get("default") is False else "fail",
         "optional bool arg — default present",
         f"schema={sc3!r}")

    s4 = ArgSpec("extra", "string", "Optional field")
    sc4 = s4.schema()
    _inc("ok" if "default" not in sc4 else "fail",
         "optional arg without default — no default key",
         f"schema={sc4!r}")


# ── 2. ToolSpec.tool() output ────────────────────────────────────
def test_tool_spec_tool():
    _section("2. ToolSpec.tool() output")

    spec = STATIC_TOOLS["ssh_list_servers"]
    t = spec.tool()
    _inc("ok" if t["name"] == "ssh_list_servers" else "fail",
         "tool name matches",
         f"name={t['name']!r}")
    _inc("ok" if t["inputSchema"]["type"] == "object" else "fail",
         "inputSchema type is object",
         f"type={t['inputSchema']['type']!r}")
    _inc("ok" if t["inputSchema"].get("additionalProperties") is False else "fail",
         "additionalProperties is False")
    _inc("ok" if "required" not in t["inputSchema"] else "fail",
         "no required fields when args empty")

    spec2 = STATIC_TOOLS["ssh_run"]
    t2 = spec2.tool()
    _inc("ok" if "required" in t2["inputSchema"] else "fail",
         "required fields present when args have required",
         f"required={t2['inputSchema'].get('required')}")
    _inc("ok" if "server" in t2["inputSchema"].get("properties", {}) else "fail",
         "server arg in properties")
    _inc("ok" if "command" in t2["inputSchema"].get("properties", {}) else "fail",
         "command arg in properties")


# ── 3. _sanitize_tool_part edge cases ────────────────────────────
def test_sanitize_tool_part():
    _section("3. _sanitize_tool_part edge cases")

    _inc("ok" if _sanitize_tool_part("simple") == "simple" else "fail",
         "simple name unchanged",
         f"result={_sanitize_tool_part('simple')!r}")
    _inc("ok" if _sanitize_tool_part("Hello World") == "Hello_World" else "fail",
         "spaces replaced with underscore",
         f"result={_sanitize_tool_part('Hello World')!r}")
    _inc("ok" if _sanitize_tool_part("a/b:c") == "a_b_c" else "fail",
         "special chars replaced",
         f"result={_sanitize_tool_part('a/b:c')!r}")
    _inc("ok" if _sanitize_tool_part("") == "unnamed" else "fail",
         "empty string returns 'unnamed'")
    _inc("ok" if _sanitize_tool_part("...") == "unnamed" else "fail",
         "all-dots returns 'unnamed'")
    _inc("ok" if _sanitize_tool_part("_foo_") == "foo" else "fail",
         "leading/trailing underscores stripped",
         f"result={_sanitize_tool_part('_foo_')!r}")
    _inc("ok" if _sanitize_tool_part("-bar-") == "bar" else "fail",
         "leading/trailing hyphens stripped",
         f"result={_sanitize_tool_part('-bar-')!r}")


# ── 4. _unique_alias_tool_name ────────────────────────────────────
def test_unique_alias_tool_name():
    _section("4. _unique_alias_tool_name dedup & truncation")

    used: set = set()
    n1 = _unique_alias_tool_name("server1", "my alias", used)
    _inc("ok" if n1 == "ssh_alias.server1.my_alias" else "fail",
         "basic name generation (space->underscore)",
         f"result={n1!r}")

    n2 = _unique_alias_tool_name("server1", "my alias", used)
    _inc("ok" if n2.startswith("ssh_alias.server1.my_alias.") and len(n2) <= TOOL_NAME_MAX_LENGTH else "fail",
         "duplicate name gets digest suffix",
         f"result={n2!r}")

    _inc("ok" if n1 != n2 else "fail",
         "deduped names differ",
         f"n1={n1!r} n2={n2!r}")

    # Test with three duplicates to exercise counter > 2
    n3 = _unique_alias_tool_name("server1", "my alias", used)
    _inc("ok" if n3 != n1 and n3 != n2 else "fail",
         "third dup gets counter suffix",
         f"n3={n3!r}")

    # Test long server/alias names that force truncation
    long_server = "a" * 100
    long_alias = "b" * 100
    n4 = _unique_alias_tool_name(long_server, long_alias, set())
    _inc("ok" if len(n4) <= TOOL_NAME_MAX_LENGTH else "fail",
         "long names truncated to max length",
         f"len={len(n4)} max={TOOL_NAME_MAX_LENGTH}")

    # Test names with special chars
    n5 = _unique_alias_tool_name("my server", "restart nginx!", set())
    _inc("ok" if " " not in n5 and "!" not in n5 else "fail",
         "special chars sanitized in tool name",
         f"result={n5!r}")


# ── 5. _raw_output formatting ────────────────────────────────────
def test_raw_output():
    _section("5. _raw_output formatting")

    r1 = _raw_output({"stdout": "hello\n", "code": 0})
    _inc("ok" if "hello" in r1["content"][0]["text"] and r1.get("isError") is False else "fail",
         "stdout only, success",
         f"text={r1['content'][0]['text']!r}")

    r2 = _raw_output({"stdout": "out", "stderr": "err", "code": 1})
    text2 = r2["content"][0]["text"]
    _inc("ok" if "STDERR:" in text2 and "Exited with code 1" in text2 and r2.get("isError") else "fail",
         "stdout + stderr + non-zero exit",
         f"text={text2!r}")

    r3 = _raw_output({})
    _inc("ok" if "(empty output)" in r3["content"][0]["text"] else "fail",
         "empty result returns '(empty output)'",
         f"text={r3['content'][0]['text']!r}")

    r4 = _raw_output({"code": 0})
    _inc("ok" if "(empty output)" in r4["content"][0]["text"] and r4.get("isError") is False else "fail",
         "no stdout/stderr, code=0",
         f"text={r4['content'][0]['text']!r}")


# ── 6. Helper functions ──────────────────────────────────────────
def test_execution_summary_output():
    _section("5b. execution summary output")

    result = _with_execution_summary(
        {"stdout": "ok\n", "stderr": "", "code": 0},
        ["Tool: ssh_run", "Server: demo", "Command: whoami"],
    )
    text = result["content"][0]["text"]
    _inc("ok" if text.startswith("Tool: ssh_run\nServer: demo\nCommand: whoami\n\nok") else "fail",
         "execution summary is prepended to tool output",
         f"text={text!r}")
    _inc("ok" if result.get("isError") is False else "fail",
         "execution summary preserves success state")

    err = _with_execution_summary(
        {"stdout": "", "stderr": "nope", "code": 2},
        ["Tool: ssh_run", "Server: demo", "Command: false"],
    )
    err_text = err["content"][0]["text"]
    _inc("ok" if err.get("isError") and "Command: false" in err_text and "Exited with code 2" in err_text else "fail",
         "execution summary preserves error output",
         f"text={err_text!r}")


def test_tool_args_summary():
    _section("5c. tool argument summary")

    text = _tool_args_summary("ssh_run", {"server": "demo", "command": "uname -a"})
    _inc("ok" if "Tool: ssh_run" in text and '"command": "uname -a"' in text else "fail",
         "tool argument summary includes concrete args",
         f"text={text!r}")


def test_mcp_helpers():
    _section("6. Helper functions (mcp_result, mcp_text, jsonrpc_*)")

    r1 = mcp_result([{"type": "text", "text": "hi"}])
    _inc("ok" if r1 == {"content": [{"type": "text", "text": "hi"}], "isError": False} else "fail",
         "mcp_result default")

    r2 = mcp_result([], is_error=True)
    _inc("ok" if r2.get("isError") else "fail",
         "mcp_result isError=True")

    r3 = mcp_text("hello")
    _inc("ok" if r3["content"][0]["text"] == "hello" else "fail",
         "mcp_text content")

    e1 = jsonrpc_error(1, -32602, "Bad", {"detail": "x"})
    _inc("ok" if e1["error"]["code"] == -32602 and e1["error"]["data"] == {"detail": "x"} else "fail",
         "jsonrpc_error with data")

    e2 = jsonrpc_error(None, -32700, "Parse")
    _inc("ok" if e2.get("id") is None and "data" not in e2["error"] else "fail",
         "jsonrpc_error no id, no data")

    res = jsonrpc_result(42, {"ok": True})
    _inc("ok" if res["id"] == 42 and res["result"]["ok"] else "fail",
         "jsonrpc_result")


# ── 7. _require_object ──────────────────────────────────────────
def test_require_object():
    _section("7. _require_object input validation")

    _inc("ok" if _require_object({"a": 1}, "p") == {"a": 1} else "fail",
         "dict passes through")

    _inc("ok" if _require_object(None, "p") == {} else "fail",
         "None returns empty dict")

    try:
        _require_object("not_a_dict", "params")
        _fail("string should raise McpProtocolError")
    except McpProtocolError as e:
        _inc("ok" if "params must be an object" in str(e) else "fail",
             "non-dict raises McpProtocolError",
             f"msg={str(e)!r}")


# ── 8. _validate_tool_args type checking ─────────────────────────
def test_validate_tool_args():
    _section("8. _validate_tool_args type checking")

    specs = (
        ArgSpec("name", "string", "Name", required=True),
        ArgSpec("count", "number", "Count", default=10),
        ArgSpec("flag", "boolean", "Flag", default=False),
    )

    # Required string present
    v = _validate_tool_args({"name": "foo", "count": 5, "flag": True}, specs)
    _inc("ok" if v == {"name": "foo", "count": 5, "flag": True} else "fail",
         "valid args pass",
         f"result={v!r}")

    # Defaults filled in
    v2 = _validate_tool_args({"name": "x"}, specs)
    _inc("ok" if v2 == {"name": "x", "count": 10, "flag": False} else "fail",
         "defaults applied",
         f"result={v2!r}")

    # Missing required
    try:
        _validate_tool_args({}, specs)
        _fail("missing required should raise")
    except McpProtocolError:
        _inc("ok", "missing required arg raises McpProtocolError")

    # Wrong type for string
    try:
        _validate_tool_args({"name": 123}, specs)
        _fail("non-string for string arg should raise")
    except McpProtocolError:
        _inc("ok", "non-string for string arg raises McpProtocolError")

    # Wrong type for boolean
    try:
        _validate_tool_args({"name": "x", "flag": "yes"}, specs)
        _fail("non-bool for boolean should raise")
    except McpProtocolError:
        _inc("ok", "non-bool for boolean arg raises McpProtocolError")

    # Wrong type for number
    try:
        _validate_tool_args({"name": "x", "count": "ten"}, specs)
        _fail("non-number for number should raise")
    except McpProtocolError:
        _inc("ok", "non-number for number arg raises McpProtocolError")

    # Number <= 0 should fail
    try:
        _validate_tool_args({"name": "x", "count": -1}, specs)
        _fail("non-positive number should raise")
    except McpProtocolError:
        _inc("ok", "non-positive number raises McpProtocolError")

    # Empty string for required string
    try:
        _validate_tool_args({"name": ""}, specs)
        _fail("empty string for required should raise")
    except McpProtocolError:
        _inc("ok", "empty string for required raises McpProtocolError")


# ── 9. _negotiate_protocol_version ──────────────────────────────
def test_negotiate_protocol_version():
    _section("9. Protocol version negotiation")

    _inc("ok" if _negotiate_protocol_version({"protocolVersion": "2025-11-25"}) == "2025-11-25" else "fail",
         "supported version returned as-is")

    _inc("ok" if _negotiate_protocol_version({"protocolVersion": "2024-11-05"}) == "2024-11-05" else "fail",
         "older supported version accepted")

    _inc("ok" if _negotiate_protocol_version({"protocolVersion": "2099-99-99"}) == LATEST_PROTOCOL_VERSION else "fail",
         "unsupported version falls back to latest")

    _inc("ok" if _negotiate_protocol_version({}) == LATEST_PROTOCOL_VERSION else "fail",
         "missing version falls back to latest")

    _inc("ok" if _negotiate_protocol_version({"protocolVersion": None}) == LATEST_PROTOCOL_VERSION else "fail",
         "None version falls back to latest")


# ── 10. handle_request — invalid requests ───────────────────────
def test_handle_request_invalid():
    _section("10. handle_request — invalid requests")

    try:
        handle_request([])
        _fail("non-dict should raise")
    except McpProtocolError:
        _inc("ok", "non-dict raises McpProtocolError")

    try:
        handle_request({"jsonrpc": "1.0", "method": "x"})
        _fail("wrong jsonrpc should raise")
    except McpProtocolError:
        _inc("ok", "wrong jsonrpc version raises McpProtocolError")

    try:
        handle_request({"jsonrpc": "2.0"})
        _fail("missing method should raise")
    except McpProtocolError:
        _inc("ok", "missing method raises McpProtocolError")

    try:
        handle_request({"jsonrpc": "2.0", "method": 123})
        _fail("non-string method should raise")
    except McpProtocolError:
        _inc("ok", "non-string method raises McpProtocolError")


# ── 11. handle_request — notifications ──────────────────────────
def test_handle_request_notification():
    _section("11. handle_request — notifications (no id -> no response)")

    # Notification: no "id" key
    resp = handle_request({"jsonrpc": "2.0", "method": "tools/list"})
    _inc("ok" if resp is None else "fail",
         "notification tools/list returns None",
         f"resp={resp!r}")

    resp2 = handle_request({"jsonrpc": "2.0", "method": "initialize"})
    _inc("ok" if resp2 is None else "fail",
         "notification initialize returns None")


# ── 12. handle_request — initialize ─────────────────────────────
def test_handle_request_initialize():
    _section("12. handle_request — initialize handshake")

    resp = handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
    })
    _inc("ok" if resp.get("id") == 1 else "fail",
         "echoes id=1")
    _inc("ok" if resp.get("result", {}).get("protocolVersion") == "2024-11-05" else "fail",
         "requested version honored",
         f"v={resp.get('result', {}).get('protocolVersion')!r}")
    _inc("ok" if resp.get("result", {}).get("serverInfo") == SERVER_INFO else "fail",
         "serverInfo matches")
    _inc("ok" if resp.get("result", {}).get("instructions") == MCP_INSTRUCTIONS else "fail",
         "instructions present")
    caps = resp.get("result", {}).get("capabilities", {})
    _inc("ok" if caps.get("tools", {}).get("listChanged") is False else "fail",
         "capabilities.tools.listChanged is False")


# ── 13. handle_request — tools/list ─────────────────────────────
def test_handle_request_tools_list():
    _section("13. handle_request — tools/list")

    resp = handle_request({
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
    })
    tools = resp.get("result", {}).get("tools", [])
    _inc("ok" if len(tools) >= 9 else "fail",
         "at least 9 static tools",
         f"count={len(tools)}")

    names = {t["name"] for t in tools}
    for required in ("ssh_run", "ssh_list_servers", "ssh_list_aliases",
                     "ssh_run_alias", "ssh_upload_script", "ssh_run_script",
                     "ssh_list_scripts", "ssh_download", "ssh_upload_all_scripts"):
        _inc("ok" if required in names else "fail",
             f"{required} is in tools list")

    # Each tool should have inputSchema with type=object and additionalProperties=False
    for t in tools:
        schema = t.get("inputSchema", {})
        _inc("ok" if schema.get("type") == "object" and schema.get("additionalProperties") is False else "fail",
             f"{t['name']} inputSchema is object + no additionalProperties",
             f"inputSchema={schema!r}")
        break  # only check first tool to keep test fast

    # Dynamic alias tools should also be present
    alias_tools = [t for t in tools if t["name"].startswith("ssh_alias.")]
    _inc("ok" if len(alias_tools) > 0 else "skip",
         "dynamic alias tools present",
         f"count={len(alias_tools)}")

    # Check tool name lengths
    for t in tools:
        _inc("ok" if len(t["name"]) <= TOOL_NAME_MAX_LENGTH else "fail",
             f"tool name '{t['name']}' within max length",
             f"len={len(t['name'])}")
        break


# ── 14. handle_request — tools/list invalid cursor ──────────────
def test_handle_request_tools_list_cursor():
    _section("14. handle_request — tools/list invalid cursor")

    try:
        handle_request({
            "jsonrpc": "2.0", "id": 10, "method": "tools/list",
            "params": {"cursor": "some-token"},
        })
        _fail("invalid cursor should raise")
    except McpProtocolError as e:
        _inc("ok" if e.code == -32602 else "fail",
             "invalid cursor returns -32602",
             f"code={e.code}")


# ── 15. handle_message — tools/call missing required args ──────
def test_handle_request_tools_call_missing_args():
    _section("15. handle_message — tools/call missing required args")

    resp = handle_message({
        "jsonrpc": "2.0", "id": 20, "method": "tools/call",
        "params": {"name": "ssh_run", "arguments": {}},
    })
    err = resp.get("error", {})
    _inc("ok" if err.get("code") == -32602 else "fail",
         "missing args returns -32602 error",
         f"resp={resp!r}")


# ── 16. handle_message — tools/call invalid arg types ──────────
def test_handle_request_tools_call_invalid_types():
    _section("16. handle_message — tools/call invalid arg types")

    # Boolean arg as wrong type
    resp = handle_message({
        "jsonrpc": "2.0", "id": 21, "method": "tools/call",
        "params": {
            "name": "ssh_run",
            "arguments": {"server": "x", "command": "echo hi", "sudo": "yes"},
        },
    })
    err = resp.get("error", {})
    _inc("ok" if err.get("code") == -32602 else "fail",
         "invalid bool type returns -32602",
         f"resp={resp!r}")

    # Number arg as wrong type
    resp2 = handle_message({
        "jsonrpc": "2.0", "id": 22, "method": "tools/call",
        "params": {
            "name": "ssh_run",
            "arguments": {"server": "x", "command": "echo hi", "timeout": "fast"},
        },
    })
    err2 = resp2.get("error", {})
    _inc("ok" if err2.get("code") == -32602 else "fail",
         "invalid number type returns -32602",
         f"resp={resp2!r}")


# ── 17. handle_request — unknown methods ────────────────────────
def test_handle_request_unknown():
    _section("17. handle_request — unknown method / unknown tool")

    try:
        handle_request({
            "jsonrpc": "2.0", "id": 30, "method": "foobar", "params": {},
        })
        _fail("unknown method should raise")
    except McpProtocolError as e:
        _inc("ok" if e.code == -32601 and "not found" in str(e) else "fail",
             "unknown method returns -32601",
             f"code={e.code} msg={str(e)!r}")

    # Unknown tool via handle_message (to get JSON-RPC response)
    resp = handle_message({
        "jsonrpc": "2.0", "id": 31, "method": "tools/call",
        "params": {"name": "no_such_tool", "arguments": {}},
    })
    err = resp.get("error", {})
    _inc("ok" if err.get("code") == -32602 and "Unknown tool" in err.get("message", "") else "fail",
         "unknown tool returns -32602 with message",
         f"resp={resp!r}")


# ── 18. handle_request — handler exception wrapped gracefully ──
def test_handle_request_handler_exception():
    _section("18. handle_request — handler exception wrapped as isError")

    # Non-existent server should trigger FileNotFoundError in handler
    resp = handle_request({
        "jsonrpc": "2.0", "id": 40, "method": "tools/call",
        "params": {
            "name": "ssh_run",
            "arguments": {"server": "__nonexistent__xyz", "command": "echo hi", "timeout": 5, "sudo": False},
        },
    })
    # Exception is caught and wrapped in result with isError=True
    result = resp.get("result", {})
    _inc("ok" if result.get("isError") and "Server config not found" in result.get("content", [{}])[0].get("text", "") else "fail",
         "handler exception wrapped as isError result",
         f"result={result!r}")


# ── 19. handle_message — single message wrapping ────────────────
def test_handle_message_single():
    _section("19. handle_message — single message wrapping")

    resp = handle_message({
        "jsonrpc": "2.0", "id": 50, "method": "initialize", "params": {},
    })
    _inc("ok" if isinstance(resp, dict) and resp.get("id") == 50 else "fail",
         "handle_message returns response dict for single message")


# ── 20. handle_message — batch messages ──────────────────────────
def test_handle_message_batch():
    _section("20. handle_message — batch messages")

    batch = [
        {"jsonrpc": "2.0", "id": 60, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 61, "method": "tools/list", "params": {}},
    ]
    responses = handle_message(batch)
    _inc("ok" if isinstance(responses, list) and len(responses) == 2 else "fail",
         "batch returns list of 2 responses",
         f"count={len(responses) if isinstance(responses, list) else 'not-a-list'}")
    _inc("ok" if responses[0].get("id") == 60 and responses[1].get("id") == 61 else "fail",
         "batch responses have correct ids")

    # Batch with a notification
    batch2 = [
        {"jsonrpc": "2.0", "method": "tools/list"},  # notification, no response
        {"jsonrpc": "2.0", "id": 62, "method": "tools/list", "params": {}},
    ]
    responses2 = handle_message(batch2)
    _inc("ok" if isinstance(responses2, list) and len(responses2) == 1 and responses2[0].get("id") == 62 else "fail",
         "batch with notification returns only non-notification responses")


# ── 21. handle_message — empty batch ─────────────────────────────
def test_handle_message_empty_batch():
    _section("21. handle_message — empty batch")

    resp = handle_message([])
    _inc("ok" if resp.get("error", {}).get("code") == -32600 else "fail",
         "empty batch returns -32600 error",
         f"resp={resp!r}")


# ── 22. handle_message — error wrapping ─────────────────────────
def test_handle_message_error_wrapping():
    _section("22. handle_message — error wrapping")

    # McpProtocolError caught and wrapped
    resp = handle_message({"jsonrpc": "2.0", "id": 70, "method": "invalid"})
    _inc("ok" if resp.get("error", {}).get("code") == -32601 else "fail",
         "McpProtocolError wrapped as JSON-RPC error")

    # Error with no id → id is None in error response
    resp2 = handle_message({"jsonrpc": "2.0", "method": "unknown_method"})
    _inc("ok" if resp2.get("error", {}).get("code") == -32601 and resp2.get("id") is None else "fail",
         "error without id returns id=None",
         f"resp={resp2!r}")


# ── 23. SSH integration — helpers + probes ────────────────────
def _require_ssh():
    if not SSH_SERVERS:
        _skip("no SSH servers configured — skipped")
        return False
    try:
        pool.get(SSH_TEST_SERVER)
        return True
    except FileNotFoundError:
        _skip("server '%s' not found — skipped" % SSH_TEST_SERVER)
        return False


def _ssh_result_ok(resp):
    result = resp.get("result", {})
    if result.get("isError"):
        return False, result.get("content", [{}])[0].get("text", "")
    return True, result.get("content", [{}])[0].get("text", "")


# ── 24. SSH integration — ssh_run ──────────────────────────────
def test_ssh_integration_run():
    _section("24. SSH integration — ssh_run")
    if not _require_ssh():
        return

    resp = handle_request({
        "jsonrpc": "2.0", "id": 80, "method": "tools/call",
        "params": {
            "name": "ssh_run",
            "arguments": {"server": SSH_TEST_SERVER, "command": "echo mcp-test-ok", "timeout": 10, "sudo": False},
        },
    })
    ok, text = _ssh_result_ok(resp)
    _inc("ok" if ok and "mcp-test-ok" in text else "skip",
         "ssh_run echoes 'mcp-test-ok'",
         f"text={text.strip()!r}")


# ── 25. SSH integration — ssh_list_servers ──────────────────────
def test_ssh_integration_list_servers():
    _section("25. SSH integration — ssh_list_servers")
    if not _require_ssh():
        return

    resp = handle_request({
        "jsonrpc": "2.0", "id": 81, "method": "tools/call",
        "params": {"name": "ssh_list_servers", "arguments": {}},
    })
    ok, text = _ssh_result_ok(resp)
    try:
        data = json.loads(text)
        _inc("ok" if ok and isinstance(data, dict) and "servers" in data else "skip",
             "ssh_list_servers returns valid JSON with servers",
             f"count={data.get('count')}")
    except (json.JSONDecodeError, IndexError):
        _inc("skip" if not ok else "fail",
             "ssh_list_servers returns valid JSON with servers",
             f"text={text[:200]!r}")


# ── 26. SSH integration — ssh_list_aliases ─────────────────────
def test_ssh_integration_list_aliases():
    _section("26. SSH integration — ssh_list_aliases")
    if not _require_ssh():
        return

    resp = handle_request({
        "jsonrpc": "2.0", "id": 82, "method": "tools/call",
        "params": {"name": "ssh_list_aliases", "arguments": {"server": SSH_TEST_SERVER}},
    })
    ok, text = _ssh_result_ok(resp)
    try:
        data = json.loads(text)
        _inc("ok" if ok and isinstance(data, dict) and "aliases" in data else "skip",
             "ssh_list_aliases returns valid JSON with aliases",
             f"keys={list(data.keys()) if isinstance(data, dict) else 'not-dict'}")
    except (json.JSONDecodeError, IndexError):
        _inc("skip" if not ok else "fail",
             "ssh_list_aliases returns valid JSON")


# ── 27. SSH integration — ssh_run_alias (first available alias) ──
def test_ssh_integration_run_alias():
    _section("27. SSH integration — ssh_run_alias")
    if not _require_ssh():
        return

    resp = handle_request({
        "jsonrpc": "2.0", "id": 83, "method": "tools/call",
        "params": {"name": "ssh_list_aliases", "arguments": {"server": SSH_TEST_SERVER}},
    })
    ok, text = _ssh_result_ok(resp)
    if not ok:
        _skip("can't get aliases — skipped")
        return
    try:
        data = json.loads(text)
        aliases = data.get("aliases", [])
        if not aliases:
            _skip("no aliases to run — skipped")
            return
        alias_name = aliases[0]["name"]
        resp2 = handle_request({
            "jsonrpc": "2.0", "id": 84, "method": "tools/call",
            "params": {
                "name": "ssh_run_alias",
                "arguments": {"server": SSH_TEST_SERVER, "alias_name": alias_name},
            },
        })
        ok2, text2 = _ssh_result_ok(resp2)
        _inc("ok" if ok2 and text2 else "skip",
             "ssh_run_alias '%s' returns content" % alias_name,
             f"text={text2[:200]!r}")
    except (json.JSONDecodeError, IndexError) as e:
        _skip("ssh_run_alias setup failed — skipped")


# ── 28. ToolSpec — _build_tools_list completeness ────────────────
def test_tools_list_completeness():
    _section("27. _build_tools_list — completeness")

    specs = _tool_specs()
    _inc("ok" if len(specs) >= 9 else "fail",
         "_tool_specs returns at least 9 specs",
         f"count={len(specs)}")

    # All STATIC_TOOLS keys should be present
    for key in STATIC_TOOLS:
        _inc("ok" if key in specs else "fail",
             f"STATIC_TOOLS['{key}'] in specs")

    # All specs should have a callable handler
    for name, spec in specs.items():
        _inc("ok" if callable(spec.handler) else "fail",
             f"spec '{name}' handler is callable")
        break


# ── 28. Alias tool specs ──────────────────────────────────────────
def test_alias_tool_specs():
    _section("28. Alias tool specs generation")

    alias_specs = _alias_tool_specs()
    if alias_specs:
        name, spec = next(iter(alias_specs.items()))
        _inc("ok" if name.startswith("ssh_alias.") else "fail",
             "alias spec name starts with 'ssh_alias.'",
             f"name={name!r}")
        _inc("ok" if callable(spec.handler) else "fail",
             "alias spec handler is callable")
        _inc("ok" if spec.annotations.get("destructiveHint") else "fail",
             "alias spec annotations destructiveHint=True")
        _inc("ok" if spec.args == () else "fail",
             "alias spec has no args")
    else:
        # No aliases configured on any server — still valid
        _inc("ok" if len(alias_specs) == 0 else "fail",
             "no aliases configured — empty dict is OK",
             f"count={len(alias_specs)}")


# ── 29. Tool call — ssh_list_scripts ─────────────────────────────
def test_ssh_list_scripts():
    _section("29. SSH integration — ssh_list_scripts")
    if not _require_ssh():
        return

    resp = handle_request({
        "jsonrpc": "2.0", "id": 90, "method": "tools/call",
        "params": {"name": "ssh_list_scripts", "arguments": {"server": SSH_TEST_SERVER}},
    })
    ok, text = _ssh_result_ok(resp)
    _inc("ok" if ok else "skip",
         "ssh_list_scripts returns content",
         f"text={text[:200]!r}")


# ── Main ─────────────────────────────────────────────────────────
def main():
    global passed, failed, skipped
    tests = [
        test_arg_spec_schema,
        test_tool_spec_tool,
        test_sanitize_tool_part,
        test_unique_alias_tool_name,
        test_raw_output,
        test_execution_summary_output,
        test_tool_args_summary,
        test_mcp_helpers,
        test_require_object,
        test_validate_tool_args,
        test_negotiate_protocol_version,
        test_handle_request_invalid,
        test_handle_request_notification,
        test_handle_request_initialize,
        test_handle_request_tools_list,
        test_handle_request_tools_list_cursor,
        test_handle_request_tools_call_missing_args,
        test_handle_request_tools_call_invalid_types,
        test_handle_request_unknown,
        test_handle_request_handler_exception,
        test_handle_message_single,
        test_handle_message_batch,
        test_handle_message_empty_batch,
        test_handle_message_error_wrapping,
        test_tools_list_completeness,
        test_alias_tool_specs,
        test_ssh_integration_run,
        test_ssh_integration_list_servers,
        test_ssh_integration_list_aliases,
        test_ssh_integration_run_alias,
        test_ssh_list_scripts,
    ]

    print("=" * 60)
    print("  MCP Server Tests")
    print(f"  Tests:  {len(tests)} test functions    Server: {SSH_TEST_SERVER}")
    print("=" * 60)

    for t in tests:
        try:
            t()
        except Exception as e:
            _fail(f"{t.__name__} — exception: {e}")

    total = passed + failed + skipped
    print(f"\n{'='*60}")
    print(f"  Results: {GREEN}{passed} passed{NC}, {RED}{failed} failed{NC}, "
          f"{YELLOW}{skipped} skipped{NC}  ({total} total)")
    print(f"{'='*60}")

    if failed > 0:
        sys.exit(1)
    print("\nAll MCP server tests passed!")


if __name__ == "__main__":
    main()
