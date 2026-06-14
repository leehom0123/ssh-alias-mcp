#!/usr/bin/env python3
"""MCP Server - ssh-alias-mcp

MCP stdio protocol wrapper around ssh_client.py.
"""
import io
import json
import re
import sys
from dataclasses import dataclass
from hashlib import sha1
from typing import Callable, Dict, Tuple

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


@dataclass(frozen=True)
class ArgSpec:
    name: str
    json_type: str
    description: str
    required: bool = False
    default: object = None

    def schema(self) -> dict:
        data = {"type": self.json_type, "description": self.description}
        if not self.required and self.default is not None:
            data["default"] = self.default
        return data


@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    description: str
    args: Tuple[ArgSpec, ...]
    annotations: dict
    handler: Callable[[dict], dict]

    def tool(self) -> dict:
        input_schema = {
            "type": "object",
            "additionalProperties": False,
        }
        if self.args:
            input_schema["properties"] = {arg.name: arg.schema() for arg in self.args}
            required = [arg.name for arg in self.args if arg.required]
            if required:
                input_schema["required"] = required
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": input_schema,
            "annotations": self.annotations,
        }


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


def _validate_tool_args(args: dict, specs: Tuple[ArgSpec, ...]) -> dict:
    values = {}
    for spec in specs:
        value = args.get(spec.name, spec.default)
        if spec.required and (value is None or value == ""):
            raise McpProtocolError(
                -32602,
                f"Missing or invalid required {spec.json_type} argument: {spec.name}",
            )
        if value is None:
            values[spec.name] = None
            continue
        if spec.json_type == "string" and not isinstance(value, str):
            raise McpProtocolError(-32602, f"Invalid string argument: {spec.name}")
        if spec.json_type == "string" and spec.required and not value:
            raise McpProtocolError(
                -32602,
                f"Missing or invalid required string argument: {spec.name}",
            )
        if spec.json_type == "boolean" and not isinstance(value, bool):
            raise McpProtocolError(-32602, f"Invalid boolean argument: {spec.name}")
        if spec.json_type == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise McpProtocolError(-32602, f"Invalid number argument: {spec.name}")
            if value <= 0:
                raise McpProtocolError(-32602, f"{spec.name} must be greater than 0")
        values[spec.name] = value
    return values


def _sanitize_tool_part(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._-")
    return value or "unnamed"


def _alias_tool_base(server: str, alias_name: str) -> str:
    return f"ssh_alias.{_sanitize_tool_part(server)}.{_sanitize_tool_part(alias_name)}"


def _unique_alias_tool_name(server: str, alias_name: str, used: set) -> str:
    base = _alias_tool_base(server, alias_name)
    tool_name = base[:TOOL_NAME_MAX_LENGTH]
    if tool_name not in used:
        used.add(tool_name)
        return tool_name

    digest = sha1(f"{server}\0{alias_name}".encode("utf-8")).hexdigest()[:8]
    suffix = f".{digest}"
    tool_name = base[:TOOL_NAME_MAX_LENGTH - len(suffix)] + suffix
    if tool_name not in used:
        used.add(tool_name)
        return tool_name

    counter = 2
    while True:
        suffix = f".{digest}.{counter}"
        tool_name = base[:TOOL_NAME_MAX_LENGTH - len(suffix)] + suffix
        if tool_name not in used:
            used.add(tool_name)
            return tool_name
        counter += 1


def _alias_tool_specs() -> Dict[str, ToolSpec]:
    specs = {}
    used = set()
    for entry in pool.list_alias_entries():
        server = entry["server"]
        alias_name = entry["alias_name"]
        alias = entry["alias"]
        tool_name = _unique_alias_tool_name(server, alias_name, used)
        desc = alias.get("desc", "")
        script = alias.get("script", "") or alias.get("inline", "")
        script_hint = "(inline)" if "inline" in alias else f"(runs {script})"
        specs[tool_name] = ToolSpec(
            name=tool_name,
            title=f"{server}: {alias_name}",
            description=f"[{server}] {desc} {script_hint}".strip(),
            args=(),
            annotations={"readOnlyHint": False, "destructiveHint": True},
            handler=_run_alias_handler(server, alias_name),
        )
    return specs


def _run_alias_handler(server: str, alias_name: str) -> Callable[[dict], dict]:
    def handler(args: dict) -> dict:
        conn = pool.get(server)
        return _raw_output(conn.run_alias(alias_name))

    return handler


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


def _json_text(payload: dict) -> dict:
    return mcp_text(json.dumps(payload, indent=2, ensure_ascii=False))


def _server_conn(args: dict):
    return pool.get(args["server"])


def _handle_ssh_run(args: dict) -> dict:
    return _raw_output(
        _server_conn(args).run(
            args["command"],
            timeout=args["timeout"],
            sudo=args["sudo"],
        )
    )


def _handle_ssh_run_script(args: dict) -> dict:
    return _raw_output(
        _server_conn(args).run_script(
            args["script_name"],
            timeout=args["timeout"],
            sudo=args["sudo"],
        )
    )


def _handle_ssh_upload_script(args: dict) -> dict:
    return _raw_output(
        _server_conn(args).upload_script(
            args["local_path"],
            args["script_name"],
            args["run_immediately"],
            args["timeout"],
            args["overwrite"],
            args["sudo"],
        )
    )


def _handle_ssh_list_scripts(args: dict) -> dict:
    return _raw_output(_server_conn(args).list_scripts(sudo=args["sudo"]))


def _handle_ssh_list_servers(args: dict) -> dict:
    servers = pool.list_servers()
    return _json_text({"count": len(servers), "servers": servers})


def _handle_ssh_download(args: dict) -> dict:
    return _raw_output(
        _server_conn(args).download(
            args["remote_path"],
            args["local_path"],
            timeout=args["timeout"],
            pattern=args["pattern"],
            overwrite=args["overwrite"],
            sudo=args["sudo"],
        )
    )


def _handle_ssh_upload_all_scripts(args: dict) -> dict:
    return _raw_output(_server_conn(args).upload_all_scripts(sudo=args["sudo"]))


def _handle_ssh_run_alias(args: dict) -> dict:
    return _raw_output(_server_conn(args).run_alias(args["alias_name"]))


def _handle_ssh_list_aliases(args: dict) -> dict:
    aliases = _server_conn(args).list_aliases()
    return _json_text({"count": len(aliases), "aliases": aliases})


def _arg(name: str, json_type: str, description: str, required: bool = False, default=None) -> ArgSpec:
    return ArgSpec(name, json_type, description, required, default)


SERVER_ARG = _arg("server", "string", "Server name", required=True)
SUDO_ARG = _arg("sudo", "boolean", "Run as root via sudo", default=False)
TIMEOUT_300_ARG = _arg("timeout", "number", "Timeout in seconds", default=300)


STATIC_TOOLS = {
    "ssh_run": ToolSpec(
        name="ssh_run",
        title="Run SSH Command",
        description="Execute a command on a remote server. Set sudo=true to run as root.",
        args=(
            SERVER_ARG,
            _arg("command", "string", "Command to execute", required=True),
            _arg("timeout", "number", "Timeout in seconds", default=60),
            SUDO_ARG,
        ),
        annotations={"readOnlyHint": False, "destructiveHint": True},
        handler=_handle_ssh_run,
    ),
    "ssh_upload_script": ToolSpec(
        name="ssh_upload_script",
        title="Upload SSH Script",
        description="Upload a local script to the server scripts_dir.",
        args=(
            SERVER_ARG,
            _arg("local_path", "string", "Local script path", required=True),
            _arg("script_name", "string", "Optional rename"),
            _arg("run_immediately", "boolean", "Run after upload", default=False),
            TIMEOUT_300_ARG,
            _arg("overwrite", "boolean", "Overwrite existing script", default=True),
            SUDO_ARG,
        ),
        annotations={"readOnlyHint": False, "destructiveHint": False},
        handler=_handle_ssh_upload_script,
    ),
    "ssh_run_script": ToolSpec(
        name="ssh_run_script",
        title="Run SSH Script",
        description="Execute an uploaded script from scripts_dir.",
        args=(
            SERVER_ARG,
            _arg("script_name", "string", "Script filename", required=True),
            TIMEOUT_300_ARG,
            _arg("sudo", "boolean", "Run as root", default=False),
        ),
        annotations={"readOnlyHint": False, "destructiveHint": False},
        handler=_handle_ssh_run_script,
    ),
    "ssh_list_scripts": ToolSpec(
        name="ssh_list_scripts",
        title="List SSH Scripts",
        description="List uploaded scripts on the remote server.",
        args=(SERVER_ARG, _arg("sudo", "boolean", "List as root", default=False)),
        annotations={"readOnlyHint": True},
        handler=_handle_ssh_list_scripts,
    ),
    "ssh_list_servers": ToolSpec(
        name="ssh_list_servers",
        title="List SSH Servers",
        description="List all available server configurations.",
        args=(),
        annotations={"readOnlyHint": True},
        handler=_handle_ssh_list_servers,
    ),
    "ssh_download": ToolSpec(
        name="ssh_download",
        title="Download SSH File",
        description="Download a file or directory from the remote server to a local path.",
        args=(
            SERVER_ARG,
            _arg("remote_path", "string", "Remote file or directory path", required=True),
            _arg("local_path", "string", "Local file or directory path", required=True),
            _arg("pattern", "string", "Optional regex filename filter"),
            TIMEOUT_300_ARG,
            _arg("overwrite", "boolean", "Overwrite existing local files", default=True),
            _arg("sudo", "boolean", "Read root-owned files via sudo stage", default=False),
        ),
        annotations={"readOnlyHint": False, "destructiveHint": False},
        handler=_handle_ssh_download,
    ),
    "ssh_upload_all_scripts": ToolSpec(
        name="ssh_upload_all_scripts",
        title="Upload All SSH Scripts",
        description="Upload all scripts referenced by alias definitions.",
        args=(SERVER_ARG, _arg("sudo", "boolean", "Install as root", default=False)),
        annotations={"readOnlyHint": False, "destructiveHint": False},
        handler=_handle_ssh_upload_all_scripts,
    ),
    "ssh_run_alias": ToolSpec(
        name="ssh_run_alias",
        title="Run SSH Alias",
        description="Run an alias-defined quick command.",
        args=(
            SERVER_ARG,
            _arg("alias_name", "string", "Alias name", required=True),
        ),
        annotations={"readOnlyHint": False, "destructiveHint": True},
        handler=_handle_ssh_run_alias,
    ),
    "ssh_list_aliases": ToolSpec(
        name="ssh_list_aliases",
        title="List SSH Aliases",
        description="List quick commands configured for a server.",
        args=(SERVER_ARG,),
        annotations={"readOnlyHint": True},
        handler=_handle_ssh_list_aliases,
    ),
}


def _tool_specs() -> Dict[str, ToolSpec]:
    specs = dict(STATIC_TOOLS)
    specs.update(_alias_tool_specs())
    return specs


def handle_tools_call(params: dict) -> dict:
    params = _require_object(params, "params")
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise McpProtocolError(-32602, "Missing or invalid tool name")
    args = _require_object(params.get("arguments", {}), "arguments")

    spec = STATIC_TOOLS.get(name)
    if spec is None:
        spec = _alias_tool_specs().get(name)
    if spec is None:
        raise McpProtocolError(-32602, f"Unknown tool: {name}")
    return spec.handler(_validate_tool_args(args, spec.args))


def _build_tools_list() -> list:
    return [spec.tool() for spec in _tool_specs().values()]


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
