#!/usr/bin/env python3
"""MCP Server - ssh-alias-mcp

MCP stdio protocol wrapper around ssh_client.py.
"""
import io
import json
import re
import sys

# Force UTF-8 output on Windows. stdout must contain only MCP JSON messages.
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from ssh_client import pool


SERVER_INFO = {
    "name": "ssh-alias-mcp",
    "title": "SSH Alias MCP",
    "version": "1.0.0",
    "description": "Run configured SSH commands, scripts, aliases, uploads, and downloads.",
}
SUPPORTED_PROTOCOL_VERSIONS = [
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
]
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]
MCP_INSTRUCTIONS = (
    "Use ssh_list_servers first to discover configured servers. "
    "Use ssh_list_aliases before ssh_run_alias unless you already know the alias name. "
    "Commands, uploads, downloads, and aliases can modify remote systems."
)
TOOL_NAME_MAX_LENGTH = 128


class McpProtocolError(Exception):
    def __init__(self, code: int, message: str, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def mcp_result(content: list, is_error: bool = False) -> dict:
    return {"content": content, "isError": is_error}


def mcp_text(text: str, is_error: bool = False) -> dict:
    return mcp_result([{"type": "text", "text": text}], is_error)


def jsonrpc_error(req_id, code: int, message: str, data=None) -> dict:
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": error}


def jsonrpc_result(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _require_object(value, name: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise McpProtocolError(-32602, f"{name} must be an object")
    return value


def _require_string(args: dict, name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value:
        raise McpProtocolError(-32602, f"Missing or invalid required string argument: {name}")
    return value


def _optional_string(args: dict, name: str):
    value = args.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise McpProtocolError(-32602, f"Invalid string argument: {name}")
    return value


def _optional_bool(args: dict, name: str, default: bool = False) -> bool:
    value = args.get(name, default)
    if not isinstance(value, bool):
        raise McpProtocolError(-32602, f"Invalid boolean argument: {name}")
    return value


def _optional_number(args: dict, name: str, default):
    value = args.get(name, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise McpProtocolError(-32602, f"Invalid number argument: {name}")
    if value <= 0:
        raise McpProtocolError(-32602, f"{name} must be greater than 0")
    return value


def _sanitize_tool_part(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._-")
    return value or "unnamed"


def _alias_tool_base(server: str, alias_name: str) -> str:
    return f"ssh_alias.{_sanitize_tool_part(server)}.{_sanitize_tool_part(alias_name)}"


def _alias_tool_entries() -> dict:
    entries = {}
    used = set()
    for srv in pool.list_servers():
        sname = srv.get("name", "")
        if not sname:
            continue
        try:
            data = pool._load_config(sname)
            aliases = data.get("aliases", [])
            for alias in aliases:
                aname = alias.get("name", "")
                if not aname:
                    continue
                base = _alias_tool_base(sname, aname)
                tool_name = base[:TOOL_NAME_MAX_LENGTH]
                suffix = 2
                while tool_name in used:
                    suffix_text = f".{suffix}"
                    tool_name = base[:TOOL_NAME_MAX_LENGTH - len(suffix_text)] + suffix_text
                    suffix += 1
                used.add(tool_name)
                desc = alias.get("desc", "")
                script = alias.get("script", "") or alias.get("inline", "")
                script_hint = "(inline)" if "inline" in alias else f"(runs {script})"
                entries[tool_name] = {
                    "server": sname,
                    "alias_name": aname,
                    "tool": {
                        "name": tool_name,
                        "title": f"{sname}: {aname}",
                        "description": f"[{sname}] {desc} {script_hint}",
                        "inputSchema": {
                            "type": "object",
                            "additionalProperties": False,
                        },
                        "annotations": {"readOnlyHint": False, "destructiveHint": True},
                    },
                }
        except Exception:
            pass
    return entries


def _raw_output(result: dict) -> dict:
    lines = []
    if result.get("stdout"):
        lines.append(result["stdout"].rstrip())
    if result.get("stderr"):
        lines.append("STDERR:\n" + result["stderr"].rstrip())
    if result.get("code", 0) != 0:
        lines.append(f"\nExited with code {result['code']}")
    text = "\n".join(lines) if lines else "(empty output)"
    return mcp_text(text, result.get("code", 0) != 0)


def handle_tools_call(params: dict) -> dict:
    params = _require_object(params, "params")
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise McpProtocolError(-32602, "Missing or invalid tool name")
    args = _require_object(params.get("arguments", {}), "arguments")

    if name == "ssh_run":
        server = _require_string(args, "server")
        command = _require_string(args, "command")
        timeout = _optional_number(args, "timeout", 60)
        sudo = _optional_bool(args, "sudo", False)
        conn = pool.get(server)
        result = conn.run(
            command,
            timeout=timeout,
            sudo=sudo,
        )
        return _raw_output(result)

    if name == "ssh_run_script":
        server = _require_string(args, "server")
        script_name = _require_string(args, "script_name")
        timeout = _optional_number(args, "timeout", 300)
        sudo = _optional_bool(args, "sudo", False)
        conn = pool.get(server)
        result = conn.run_script(
            script_name,
            timeout=timeout,
            sudo=sudo,
        )
        return _raw_output(result)

    if name == "ssh_upload_script":
        server = _require_string(args, "server")
        local_path = _require_string(args, "local_path")
        script_name = _optional_string(args, "script_name")
        run_immediately = _optional_bool(args, "run_immediately", False)
        timeout = _optional_number(args, "timeout", 300)
        overwrite = _optional_bool(args, "overwrite", True)
        sudo = _optional_bool(args, "sudo", False)
        conn = pool.get(server)
        result = conn.upload_script(
            local_path,
            script_name,
            run_immediately,
            timeout,
            overwrite,
            sudo,
        )
        return _raw_output(result)

    if name == "ssh_list_scripts":
        server = _require_string(args, "server")
        sudo = _optional_bool(args, "sudo", False)
        conn = pool.get(server)
        result = conn.list_scripts(sudo=sudo)
        return _raw_output(result)

    if name == "ssh_list_servers":
        servers = pool.list_servers()
        return mcp_text(json.dumps({"count": len(servers), "servers": servers}, indent=2, ensure_ascii=False))

    if name == "ssh_download":
        server = _require_string(args, "server")
        remote_path = _require_string(args, "remote_path")
        local_path = _require_string(args, "local_path")
        timeout = _optional_number(args, "timeout", 300)
        pattern = _optional_string(args, "pattern")
        overwrite = _optional_bool(args, "overwrite", True)
        sudo = _optional_bool(args, "sudo", False)
        conn = pool.get(server)
        result = conn.download(
            remote_path,
            local_path,
            timeout=timeout,
            pattern=pattern,
            overwrite=overwrite,
            sudo=sudo,
        )
        return _raw_output(result)

    if name == "ssh_upload_all_scripts":
        server = _require_string(args, "server")
        sudo = _optional_bool(args, "sudo", False)
        conn = pool.get(server)
        result = conn.upload_all_scripts(sudo=sudo)
        return _raw_output(result)

    if name == "ssh_run_alias":
        server = _require_string(args, "server")
        alias_name = _require_string(args, "alias_name")
        conn = pool.get(server)
        result = conn.run_alias(alias_name)
        return _raw_output(result)

    if name == "ssh_list_aliases":
        conn = pool.get(_require_string(args, "server"))
        aliases = conn.list_aliases()
        return mcp_text(json.dumps({"count": len(aliases), "aliases": aliases}, indent=2, ensure_ascii=False))

    alias_entries = _alias_tool_entries()
    if name in alias_entries:
        entry = alias_entries[name]
        conn = pool.get(entry["server"])
        return _raw_output(conn.run_alias(entry["alias_name"]))

    raise McpProtocolError(-32602, f"Unknown tool: {name}")


def _build_tools_list() -> list:
    tools = [
        {
            "name": "ssh_run",
            "title": "Run SSH Command",
            "description": "Execute a command on a remote server. Set sudo=true to run as root.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "Server name"},
                    "command": {"type": "string", "description": "Command to execute"},
                    "timeout": {"type": "number", "description": "Timeout in seconds", "default": 60},
                    "sudo": {"type": "boolean", "description": "Run as root via sudo", "default": False},
                },
                "required": ["server", "command"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": True},
        },
        {
            "name": "ssh_upload_script",
            "title": "Upload SSH Script",
            "description": "Upload a local script to the server scripts_dir.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "Server name"},
                    "local_path": {"type": "string", "description": "Local script path"},
                    "script_name": {"type": "string", "description": "Optional rename"},
                    "run_immediately": {"type": "boolean", "description": "Run after upload", "default": False},
                    "timeout": {"type": "number", "description": "Timeout in seconds", "default": 300},
                    "overwrite": {"type": "boolean", "description": "Overwrite existing script", "default": True},
                    "sudo": {"type": "boolean", "description": "Install and run as root", "default": False},
                },
                "required": ["server", "local_path"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": False},
        },
        {
            "name": "ssh_run_script",
            "title": "Run SSH Script",
            "description": "Execute an uploaded script from scripts_dir.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "Server name"},
                    "script_name": {"type": "string", "description": "Script filename"},
                    "timeout": {"type": "number", "description": "Timeout in seconds", "default": 300},
                    "sudo": {"type": "boolean", "description": "Run as root", "default": False},
                },
                "required": ["server", "script_name"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": False},
        },
        {
            "name": "ssh_list_scripts",
            "title": "List SSH Scripts",
            "description": "List uploaded scripts on the remote server.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "Server name"},
                    "sudo": {"type": "boolean", "description": "List as root", "default": False},
                },
                "required": ["server"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "ssh_list_servers",
            "title": "List SSH Servers",
            "description": "List all available server configurations.",
            "inputSchema": {"type": "object", "additionalProperties": False},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "ssh_download",
            "title": "Download SSH File",
            "description": "Download a file or directory from the remote server to a local path.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "Server name"},
                    "remote_path": {"type": "string", "description": "Remote file or directory path"},
                    "local_path": {"type": "string", "description": "Local file or directory path"},
                    "pattern": {"type": "string", "description": "Optional regex filename filter"},
                    "timeout": {"type": "number", "description": "Timeout in seconds", "default": 300},
                    "overwrite": {"type": "boolean", "description": "Overwrite existing local files", "default": True},
                    "sudo": {"type": "boolean", "description": "Read root-owned files via sudo stage", "default": False},
                },
                "required": ["server", "remote_path", "local_path"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": False},
        },
        {
            "name": "ssh_upload_all_scripts",
            "title": "Upload All SSH Scripts",
            "description": "Upload all scripts referenced by alias definitions.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "Server name"},
                    "sudo": {"type": "boolean", "description": "Install as root", "default": False},
                },
                "required": ["server"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": False},
        },
        {
            "name": "ssh_run_alias",
            "title": "Run SSH Alias",
            "description": "Run an alias-defined quick command.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "Server name"},
                    "alias_name": {"type": "string", "description": "Alias name"},
                },
                "required": ["server", "alias_name"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": True},
        },
        {
            "name": "ssh_list_aliases",
            "title": "List SSH Aliases",
            "description": "List quick commands configured for a server.",
            "inputSchema": {
                "type": "object",
                "properties": {"server": {"type": "string", "description": "Server name"}},
                "required": ["server"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True},
        },
    ]
    tools.extend(entry["tool"] for entry in _alias_tool_entries().values())
    return tools


def _negotiate_protocol_version(params: dict) -> str:
    requested = params.get("protocolVersion")
    if requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return LATEST_PROTOCOL_VERSION


def handle_request(req: dict):
    if not isinstance(req, dict):
        raise McpProtocolError(-32600, "Invalid Request")
    if req.get("jsonrpc") != "2.0":
        raise McpProtocolError(-32600, "Invalid Request")
    if "method" not in req or not isinstance(req.get("method"), str):
        raise McpProtocolError(-32600, "Invalid Request")

    method = req["method"]
    is_notification = "id" not in req
    req_id = req.get("id")

    if is_notification:
        return None

    params = _require_object(req.get("params", {}), "params")

    if method == "initialize":
        return jsonrpc_result(req_id, {
            "protocolVersion": _negotiate_protocol_version(params),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": MCP_INSTRUCTIONS,
        })

    if method == "tools/list":
        cursor = params.get("cursor")
        if cursor not in (None, ""):
            raise McpProtocolError(-32602, "Invalid or expired pagination cursor")
        return jsonrpc_result(req_id, {"tools": _build_tools_list()})

    if method == "tools/call":
        try:
            return jsonrpc_result(req_id, handle_tools_call(params))
        except McpProtocolError:
            raise
        except Exception as e:
            return jsonrpc_result(req_id, mcp_text(str(e), True))

    raise McpProtocolError(-32601, f"Method not found: {method}")


def handle_message(msg):
    if isinstance(msg, list):
        if not msg:
            return jsonrpc_error(None, -32600, "Invalid Request")
        responses = []
        for item in msg:
            resp = _handle_message_item(item)
            if resp is not None:
                responses.append(resp)
        return responses or None
    return _handle_message_item(msg)


def _handle_message_item(item):
    try:
        return handle_request(item)
    except McpProtocolError as e:
        req_id = item.get("id") if isinstance(item, dict) and "id" in item else None
        return jsonrpc_error(req_id, e.code, e.message, e.data)
    except Exception as e:
        req_id = item.get("id") if isinstance(item, dict) and "id" in item else None
        return jsonrpc_error(req_id, -32603, str(e))


def write_response(msg):
    line = json.dumps(msg, ensure_ascii=False) + "\n"
    sys.stdout.write(line)
    sys.stdout.flush()


def main():
    """MCP stdio main loop."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            write_response(jsonrpc_error(None, -32700, "Parse error"))
            continue

        resp = handle_message(req)
        if resp is not None:
            write_response(resp)


if __name__ == "__main__":
    main()
