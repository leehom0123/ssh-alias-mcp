#!/usr/bin/env python3
"""Tool-layer tests for the MCP server (pytest rewrite of test_mcp_server.py).

Covers the tool layer only:
  - Utilities: _sanitize_tool_part, _unique_alias_tool_name, _raw_output
  - Summaries / redaction: _with_execution_summary, _tool_args_summary,
    _redact_secrets, _format_tool_exception (timeout formatting)
  - STATIC_TOOLS completeness (cross-checked against a fresh import)
  - Dynamic alias tool specs: generation, TTL cache, invalidation
  - Static handlers: argument mapping onto the mocked SSHConnection
    (MagicMock style, cf. McpDownloadFileToolTests in test_file_transfer.py)
  - Live integration (marked `live`, auto-skip when SSH_TEST_SERVER is unset)

Offline by default: paramiko and the network are mocked; only the five
`live`-marked tests touch a real server, via the conftest fixtures.
"""
import ast
import json
import subprocess
import sys
from hashlib import sha1
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SKILL_DIR = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(SKILL_DIR))

import mcp_server
from mcp_server import (
    _sanitize_tool_part, _unique_alias_tool_name, _raw_output,
    _with_execution_summary, _tool_args_summary, _redact_secrets,
    _format_tool_exception,
    handle_request,
    STATIC_TOOLS, TOOL_NAME_MAX_LENGTH,
    _alias_tool_specs, _build_alias_tool_specs, _tool_specs,
    invalidate_alias_tool_specs,
)


@pytest.fixture(autouse=True)
def _isolate_alias_cache():
    """Keep the module-level alias-spec cache from leaking between tests."""
    invalidate_alias_tool_specs()
    yield
    invalidate_alias_tool_specs()


def _text(result: dict) -> str:
    return result["content"][0]["text"]


# ── 3. _sanitize_tool_part edge cases ────────────────────────────
@pytest.mark.parametrize("value,expected", [
    ("simple", "simple"),
    ("Hello World", "Hello_World"),
    ("a/b:c", "a_b_c"),
    ("", "unnamed"),
    ("...", "unnamed"),
    ("_foo_", "foo"),
    ("-bar-", "bar"),
])
def test_sanitize_tool_part(value, expected):
    assert _sanitize_tool_part(value) == expected


# ── 4. _unique_alias_tool_name dedup & truncation ────────────────
_ALIAS_DIGEST = sha1("server1\0my alias".encode("utf-8")).hexdigest()[:8]


def test_unique_alias_tool_name_basic():
    used: set = set()
    n1 = _unique_alias_tool_name("server1", "my alias", used)
    assert n1 == "ssh_alias.server1.my_alias"
    assert used == {n1}


def test_unique_alias_tool_name_duplicate_gets_digest_suffix():
    used: set = set()
    n1 = _unique_alias_tool_name("server1", "my alias", used)
    n2 = _unique_alias_tool_name("server1", "my alias", used)
    assert n1 != n2
    assert n2 == f"ssh_alias.server1.my_alias.{_ALIAS_DIGEST}"
    assert len(n2) <= TOOL_NAME_MAX_LENGTH


def test_unique_alias_tool_name_third_dup_gets_counter_suffix():
    used: set = set()
    n1 = _unique_alias_tool_name("server1", "my alias", used)
    n2 = _unique_alias_tool_name("server1", "my alias", used)
    n3 = _unique_alias_tool_name("server1", "my alias", used)
    assert len({n1, n2, n3}) == 3
    assert n3 == f"ssh_alias.server1.my_alias.{_ALIAS_DIGEST}.2"


def test_unique_alias_tool_name_long_names_truncated():
    n4 = _unique_alias_tool_name("a" * 100, "b" * 100, set())
    assert len(n4) == TOOL_NAME_MAX_LENGTH
    assert n4.startswith("ssh_alias.")


def test_unique_alias_tool_name_sanitizes_special_chars():
    n5 = _unique_alias_tool_name("my server", "restart nginx!", set())
    assert " " not in n5 and "!" not in n5
    assert n5 == "ssh_alias.my_server.restart_nginx"


# ── 5. _raw_output formatting ────────────────────────────────────
def test_raw_output_stdout_only():
    r1 = _raw_output({"stdout": "hello\n", "code": 0})
    assert r1 == {"content": [{"type": "text", "text": "hello"}], "isError": False}


def test_raw_output_stderr_and_nonzero_exit():
    r2 = _raw_output({"stdout": "out", "stderr": "err", "code": 1})
    assert r2["content"][0]["text"] == "out\nSTDERR:\nerr\n\nExited with code 1"
    assert r2["isError"] is True


def test_raw_output_empty_result():
    r3 = _raw_output({})
    assert r3["content"][0]["text"] == "(empty output)"
    assert r3["isError"] is False


def test_raw_output_zero_code_no_streams():
    r4 = _raw_output({"code": 0})
    assert r4["content"][0]["text"] == "(empty output)"
    assert r4["isError"] is False


# ── 5b. execution summary output ─────────────────────────────────
def test_execution_summary_is_prepended():
    result = _with_execution_summary(
        {"stdout": "ok\n", "stderr": "", "code": 0},
        ["Tool: ssh_run", "Server: demo", "Command: whoami"],
    )
    assert _text(result) == "Tool: ssh_run\nServer: demo\nCommand: whoami\n\nok"
    assert result["isError"] is False


def test_execution_summary_preserves_error_output():
    err = _with_execution_summary(
        {"stdout": "", "stderr": "nope", "code": 2},
        ["Tool: ssh_run", "Server: demo", "Command: false"],
    )
    err_text = _text(err)
    assert err["isError"] is True
    assert err_text.startswith("Tool: ssh_run\nServer: demo\nCommand: false\n\n")
    assert "Exited with code 2" in err_text


# ── 5c. tool argument summary / secret redaction ─────────────────
def test_tool_args_summary_includes_concrete_args():
    text = _tool_args_summary("ssh_run", {"server": "demo", "command": "uname -a"})
    assert "Tool: ssh_run" in text
    assert "Arguments:" in text
    assert '"command": "uname -a"' in text


def test_redact_secrets_masks_nested_secret_fields():
    redacted = _redact_secrets({
        "config": {
            "server": {"password": "one", "sudo_password": "two"},
            "aliases": [{"name": "ok"}],
        }
    })
    assert redacted["config"]["server"]["password"] == "***REDACTED***"
    assert redacted["config"]["server"]["sudo_password"] == "***REDACTED***"
    assert redacted["config"]["aliases"] == [{"name": "ok"}]
    encoded = json.dumps(redacted)
    assert "one" not in encoded
    assert "two" not in encoded
    assert "***REDACTED***" in encoded


# ── 5d. timeout error formatting ─────────────────────────────────
def test_timeout_error_from_socket_timeout():
    text = _format_tool_exception(
        "ssh_run", {"server": "demo", "command": "sleep 10", "timeout": 5},
        TimeoutError("channel read timed out"),
    )
    assert "Timeout:" in text
    assert "Requested timeout: 5 seconds" in text


def test_timeout_error_from_wrapped_connection_timeout():
    text = _format_tool_exception(
        "ssh_run", {"server": "demo", "command": "sleep 10", "timeout": 5},
        OSError("SSH connection timed out for bit@demo:22 after 5 seconds"),
    )
    assert "Timeout:" in text
    assert "SSH connection timed out" in text


# ── STATIC_TOOLS completeness ────────────────────────────────────
EXPECTED_STATIC_TOOL_NAMES = [
    "ssh_copy_server",
    "ssh_create_server",
    "ssh_delete_server",
    "ssh_download",
    "ssh_download_file",
    "ssh_download_script",
    "ssh_list_aliases",
    "ssh_list_scripts",
    "ssh_list_servers",
    "ssh_run",
    "ssh_run_alias",
    "ssh_run_script",
    "ssh_update_server",
    "ssh_upload_all_scripts",
    "ssh_upload_file",
    "ssh_upload_script",
]


def test_static_tools_has_16_expected_names():
    assert sorted(STATIC_TOOLS) == EXPECTED_STATIC_TOOL_NAMES
    assert len(STATIC_TOOLS) == 16


def test_static_tools_match_fresh_import():
    # Cross-check the in-process registry against `import mcp_server` in a
    # fresh interpreter (same command line as in the task brief).
    proc = subprocess.run(
        [sys.executable, "-c", "import mcp_server; print(sorted(mcp_server.STATIC_TOOLS))"],
        capture_output=True, text=True, encoding="utf-8", cwd=str(SKILL_DIR), timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[:400]
    assert ast.literal_eval(proc.stdout.strip()) == sorted(STATIC_TOOLS)


def test_tool_specs_include_all_static_tools_with_handlers():
    specs = _tool_specs()
    assert set(STATIC_TOOLS) <= set(specs)
    assert len(specs) >= 16
    for name, spec in specs.items():
        assert callable(spec.handler), name


# ── 28. Alias tool specs generation + cache ──────────────────────
_ALIASES = [
    {"name": "echo", "desc": "Echo thing", "inline": "echo hi"},
    {"name": "deploy", "desc": "Deploy app", "script": "deploy.sh"},
]


def _mock_pool(aliases=None, server="srv"):
    conn = MagicMock()
    conn.list_aliases.return_value = _ALIASES if aliases is None else aliases
    # _alias_execution_summary() reads conn.aliases to enrich the header.
    conn.aliases = _ALIASES if aliases is None else aliases
    p = MagicMock()
    p.list_servers.return_value = [{"name": server}]
    p.get.return_value = conn
    return p, conn


def test_alias_specs_are_built_from_pool():
    p, _conn = _mock_pool()
    with patch("mcp_server.pool", p):
        specs = _build_alias_tool_specs()
    assert set(specs) == {"ssh_alias.srv.echo", "ssh_alias.srv.deploy"}
    spec = specs["ssh_alias.srv.echo"]
    assert spec.title == "srv: echo"
    assert spec.description == "[srv] Echo thing (runs inline: echo hi)"
    assert specs["ssh_alias.srv.deploy"].description == "[srv] Deploy app (runs script: deploy.sh)"
    assert spec.args == ()
    assert spec.annotations == {"readOnlyHint": False, "destructiveHint": True}
    assert spec.tool()["inputSchema"] == {"type": "object", "additionalProperties": False}


def test_alias_spec_handler_runs_alias_with_summary():
    p, conn = _mock_pool()
    conn.run_alias.return_value = {"stdout": "done\n", "stderr": "", "code": 0}
    with patch("mcp_server.pool", p):
        spec = _build_alias_tool_specs()["ssh_alias.srv.echo"]
        result = spec.handler({})
    conn.run_alias.assert_called_once_with("echo")
    assert _text(result) == (
        "Tool: ssh_alias.srv.echo\nServer: srv\nAlias: echo\n"
        "Inline command: echo hi\nTimeout: 300\nSudo: False\n\ndone"
    )
    assert result["isError"] is False


def test_alias_spec_build_skips_broken_server():
    p, conn = _mock_pool()
    conn.list_aliases.side_effect = FileNotFoundError("no config")
    with patch("mcp_server.pool", p):
        assert _build_alias_tool_specs() == {}


def test_alias_specs_are_cached_until_invalidated():
    with patch.object(mcp_server, "_build_alias_tool_specs",
                      return_value={"stub": "spec"}) as build:
        first = _alias_tool_specs()
        second = _alias_tool_specs()
        assert first == second == {"stub": "spec"}
        build.assert_called_once()

        invalidate_alias_tool_specs()
        _alias_tool_specs()
        assert build.call_count == 2


def test_alias_specs_cache_expires_after_ttl(monkeypatch):
    monkeypatch.setattr(mcp_server, "_ALIAS_SPECS_TTL_SECONDS", 0.0)
    with patch.object(mcp_server, "_build_alias_tool_specs",
                      return_value={}) as build:
        _alias_tool_specs()
        _alias_tool_specs()
        assert build.call_count == 2


# ── Static handler argument mapping (mocked SSHConnection) ──────
def _ok_result():
    return {"stdout": "ok\n", "stderr": "", "code": 0}


def test_handle_ssh_run_maps_args():
    conn = MagicMock()
    conn.run.return_value = _ok_result()
    args = {"server": "demo", "command": "whoami", "timeout": 60, "sudo": False}
    with patch("mcp_server._server_conn", return_value=conn):
        result = mcp_server._handle_ssh_run(args)
    conn.run.assert_called_once_with(
        "whoami", timeout=mcp_server._mcp_safe_timeout(60), sudo=False)
    text = _text(result)
    assert text.startswith("Tool: ssh_run\nServer: demo\nCommand: whoami\nTimeout:")
    assert text.endswith("Sudo: False\n\nok")


def test_handle_ssh_run_zero_timeout_passes_through():
    conn = MagicMock()
    conn.run.return_value = _ok_result()
    args = {"server": "demo", "command": "uptime", "timeout": 0, "sudo": False}
    with patch("mcp_server._server_conn", return_value=conn):
        mcp_server._handle_ssh_run(args)
    conn.run.assert_called_once_with("uptime", timeout=0, sudo=False)


def test_handle_ssh_run_script_maps_args():
    conn = MagicMock()
    conn.run_script.return_value = _ok_result()
    args = {"server": "demo", "script_name": "deploy.sh", "timeout": 120, "sudo": True}
    with patch("mcp_server._server_conn", return_value=conn):
        result = mcp_server._handle_ssh_run_script(args)
    conn.run_script.assert_called_once_with(
        "deploy.sh", timeout=mcp_server._mcp_safe_timeout(120), sudo=True)
    assert _text(result).startswith("Tool: ssh_run_script\nServer: demo\nScript: deploy.sh\n")


def test_handle_ssh_upload_script_maps_positional_args():
    conn = MagicMock()
    conn.upload_script.return_value = _ok_result()
    args = {"server": "demo", "local_path": "./deploy.sh", "script_name": "deploy.sh",
            "run_immediately": True, "timeout": 90, "overwrite": False, "sudo": True}
    with patch("mcp_server._server_conn", return_value=conn):
        mcp_server._handle_ssh_upload_script(args)
    conn.upload_script.assert_called_once_with(
        "./deploy.sh", "deploy.sh", True,
        mcp_server._mcp_safe_timeout(90), False, True)


def test_handle_ssh_upload_file_maps_positional_args():
    conn = MagicMock()
    conn.upload_file.return_value = _ok_result()
    args = {"server": "demo", "local_path": "./app.json", "remote_path": "/srv/app.json",
            "timeout": 45, "overwrite": False, "sudo": True, "executable": True}
    with patch("mcp_server._server_conn", return_value=conn):
        mcp_server._handle_ssh_upload_file(args)
    conn.upload_file.assert_called_once_with(
        "./app.json", "/srv/app.json",
        mcp_server._mcp_safe_timeout(45), False, True, True)


def test_handle_ssh_download_maps_args():
    conn = MagicMock()
    conn.download.return_value = _ok_result()
    args = {"server": "demo", "remote_path": "/var/log/app.log", "local_path": "./app.log",
            "pattern": r"\.log$", "timeout": 30, "overwrite": False, "sudo": True}
    with patch("mcp_server._server_conn", return_value=conn):
        mcp_server._handle_ssh_download(args)
    conn.download.assert_called_once_with(
        "/var/log/app.log", "./app.log",
        timeout=mcp_server._mcp_safe_timeout(30), pattern=r"\.log$",
        overwrite=False, sudo=True)


def test_handle_ssh_download_script_maps_positional_args():
    conn = MagicMock()
    conn.download_script.return_value = _ok_result()
    args = {"server": "demo", "script_name": "fix.sh", "local_path": "./fix.sh",
            "timeout": 15, "overwrite": True, "sudo": False}
    with patch("mcp_server._server_conn", return_value=conn):
        mcp_server._handle_ssh_download_script(args)
    conn.download_script.assert_called_once_with(
        "fix.sh", "./fix.sh", mcp_server._mcp_safe_timeout(15), True, False)


def test_handle_ssh_list_scripts_passes_sudo():
    conn = MagicMock()
    conn.list_scripts.return_value = _ok_result()
    with patch("mcp_server._server_conn", return_value=conn):
        result = mcp_server._handle_ssh_list_scripts({"server": "demo", "sudo": True})
    conn.list_scripts.assert_called_once_with(sudo=True)
    assert _text(result).startswith("Tool: ssh_list_scripts\nServer: demo\nSudo: True\n")


def test_handle_ssh_upload_all_scripts_passes_sudo():
    conn = MagicMock()
    conn.upload_all_scripts.return_value = _ok_result()
    with patch("mcp_server._server_conn", return_value=conn):
        mcp_server._handle_ssh_upload_all_scripts({"server": "demo", "sudo": False})
    conn.upload_all_scripts.assert_called_once_with(sudo=False)


def test_handle_ssh_run_alias_maps_args_and_summary():
    conn = MagicMock()
    conn.run_alias.return_value = {"stdout": "healthy\n", "stderr": "", "code": 0}
    conn.aliases = [{"name": "health", "inline": "curl -s localhost",
                     "timeout": 10, "sudo": False}]
    with patch("mcp_server._server_conn", return_value=conn):
        result = mcp_server._handle_ssh_run_alias(
            {"server": "demo", "alias_name": "health"})
    conn.run_alias.assert_called_once_with("health")
    assert _text(result) == (
        "Tool: ssh_alias.demo.health\nServer: demo\nAlias: health\n"
        "Inline command: curl -s localhost\nTimeout: 10\nSudo: False\n\nhealthy"
    )


def test_handle_ssh_list_aliases_returns_json():
    conn = MagicMock()
    conn.list_aliases.return_value = [{"name": "health", "inline": "uptime"}]
    with patch("mcp_server._server_conn", return_value=conn):
        result = mcp_server._handle_ssh_list_aliases({"server": "demo"})
    assert _json_payload(result) == {
        "tool": "ssh_list_aliases", "server": "demo",
        "count": 1, "aliases": [{"name": "health", "inline": "uptime"}],
    }
    assert result["isError"] is False


def _json_payload(result: dict) -> dict:
    assert result["isError"] is False
    return json.loads(_text(result))


def test_handle_ssh_list_servers_returns_json():
    p = MagicMock()
    p.list_servers.return_value = [{"name": "a"}, {"name": "b"}]
    with patch("mcp_server.pool", p):
        result = mcp_server._handle_ssh_list_servers({})
    assert _json_payload(result) == {"count": 2, "servers": [{"name": "a"}, {"name": "b"}]}


# ── Server-config tools: pool call + cache invalidation ─────────
def test_handle_create_server_delegates_and_invalidates_alias_cache():
    p = MagicMock()
    p.create_server.return_value = {"created": "new"}
    with patch("mcp_server.pool", p), \
            patch("mcp_server.invalidate_alias_tool_specs") as inv:
        result = mcp_server._handle_ssh_create_server(
            {"server": "new", "config": {"host": "h"}})
    p.create_server.assert_called_once_with("new", {"host": "h"})
    inv.assert_called_once_with()
    assert _json_payload(result) == {"created": "new"}


def test_handle_update_server_delegates_and_invalidates_alias_cache():
    p = MagicMock()
    p.update_server.return_value = {"updated": "s"}
    with patch("mcp_server.pool", p), \
            patch("mcp_server.invalidate_alias_tool_specs") as inv:
        mcp_server._handle_ssh_update_server(
            {"server": "s", "config": {"port": 2222}, "replace": True})
    p.update_server.assert_called_once_with("s", {"port": 2222}, replace=True)
    inv.assert_called_once_with()


def test_handle_copy_server_delegates_and_invalidates_alias_cache():
    p = MagicMock()
    p.copy_server.return_value = {"copied": "dst"}
    with patch("mcp_server.pool", p), \
            patch("mcp_server.invalidate_alias_tool_specs") as inv:
        mcp_server._handle_ssh_copy_server(
            {"source_server": "src", "target_server": "dst"})
    p.copy_server.assert_called_once_with("src", "dst")
    inv.assert_called_once_with()


def test_handle_delete_server_delegates_and_invalidates_alias_cache():
    p = MagicMock()
    p.delete_server.return_value = {"deleted": "s"}
    with patch("mcp_server.pool", p), \
            patch("mcp_server.invalidate_alias_tool_specs") as inv:
        mcp_server._handle_ssh_delete_server({"server": "s"})
    p.delete_server.assert_called_once_with("s")
    inv.assert_called_once_with()


# ── Live integration (offline: live_server fixture skips) ───────
@pytest.mark.live
def test_live_ssh_run(live_server):
    resp = handle_request({
        "jsonrpc": "2.0", "id": 80, "method": "tools/call",
        "params": {
            "name": "ssh_run",
            "arguments": {"server": live_server, "command": "echo mcp-test-ok",
                          "timeout": 10, "sudo": False},
        },
    })
    result = resp["result"]
    assert result["isError"] is False
    assert "mcp-test-ok" in result["content"][0]["text"]


@pytest.mark.live
def test_live_list_servers(live_server, run_cli):
    # CLI equivalent of the ssh_list_servers tool: valid JSON with servers.
    out, err, code = run_cli("list-servers")
    assert code == 0, err[:300]
    data = json.loads(out)
    assert isinstance(data, dict) and "servers" in data
    assert data["count"] == len(data["servers"])
    assert live_server in {s["name"] for s in data["servers"]}


def _live_aliases(server) -> list:
    resp = handle_request({
        "jsonrpc": "2.0", "id": 82, "method": "tools/call",
        "params": {"name": "ssh_list_aliases", "arguments": {"server": server}},
    })
    result = resp["result"]
    assert result["isError"] is False
    data = json.loads(result["content"][0]["text"])
    assert isinstance(data, dict) and "aliases" in data
    return data["aliases"]


@pytest.mark.live
def test_live_list_aliases(live_server):
    _live_aliases(live_server)  # asserts valid JSON shape with "aliases"


@pytest.mark.live
def test_live_run_alias(live_server):
    aliases = _live_aliases(live_server)
    if not aliases:
        pytest.skip(f"no aliases configured on {live_server}")
    alias_name = aliases[0]["name"]
    resp2 = handle_request({
        "jsonrpc": "2.0", "id": 84, "method": "tools/call",
        "params": {
            "name": "ssh_run_alias",
            "arguments": {"server": live_server, "alias_name": alias_name},
        },
    })
    result = resp2["result"]
    assert result["isError"] is False
    assert result["content"][0]["text"]


@pytest.mark.live
def test_live_list_scripts(live_server):
    resp = handle_request({
        "jsonrpc": "2.0", "id": 90, "method": "tools/call",
        "params": {"name": "ssh_list_scripts",
                   "arguments": {"server": live_server, "sudo": False}},
    })
    result = resp["result"]
    assert result["isError"] is False
    assert result["content"][0]["text"]
