#!/usr/bin/env python3
"""MCP Server - ssh-alias-mcp

MCP stdio protocol wrapper around ssh_client.py, providing tool calls for Claude Code.

Usage:
    claude mcp add ssh-alias-mcp python <path-to-this-dir>/mcp_server.py
"""
import io
import json
import sys
from pathlib import Path

# Force UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Import shared module
from ssh_client import pool, load_yaml


def mcp_result(content: list, is_error: bool = False) -> dict:
    return {"content": content, "isError": is_error}


def mcp_text(text: str, is_error: bool = False) -> dict:
    return mcp_result([{"type": "text", "text": text}], is_error)


def handle_tools_call(params: dict) -> dict:
    name = params.get("name", "")
    args = params.get("arguments", {})

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

    if name == "ssh_run":
        server = args.get("server", "")
        command = args.get("command", "")
        timeout = args.get("timeout", 60)
        sudo = args.get("sudo", False)
        conn = pool.get(server)
        result = conn.run(command, timeout=timeout, sudo=sudo)
        return _raw_output(result)

    elif name == "ssh_run_script":
        server = args.get("server", "")
        script_name = args.get("script_name", "")
        timeout = args.get("timeout", 300)
        sudo = args.get("sudo", False)
        conn = pool.get(server)
        result = conn.run_script(script_name, timeout=timeout, sudo=sudo)
        return _raw_output(result)

    elif name == "ssh_upload_script":
        server = args.get("server", "")
        local_path = args.get("local_path", "")
        script_name = args.get("script_name", None)
        run_immediately = args.get("run_immediately", False)
        timeout = args.get("timeout", 300)
        overwrite = args.get("overwrite", True)
        sudo = args.get("sudo", False)
        conn = pool.get(server)
        result = conn.upload_script(local_path, script_name, run_immediately, timeout, overwrite, sudo)
        return _raw_output(result)

    elif name == "ssh_list_scripts":
        server = args.get("server", "")
        sudo = args.get("sudo", False)
        conn = pool.get(server)
        result = conn.list_scripts(sudo=sudo)
        return _raw_output(result)

    elif name == "ssh_list_servers":
        servers = pool.list_servers()
        return mcp_text(json.dumps({"count": len(servers), "servers": servers},
                                    indent=2, ensure_ascii=False))

    elif name.startswith("ssh_alias:"):
        parts = name[len("ssh_alias:"):].rsplit(":", 1)
        if len(parts) != 2:
            return mcp_text(f"Invalid alias tool name: {name}", True)
        server, alias_name = parts
        conn = pool.get(server)
        result = conn.run_alias(alias_name)
        return _raw_output(result)

    elif name == "ssh_download":
        server = args.get("server", "")
        remote_path = args.get("remote_path", "")
        local_path = args.get("local_path", "")
        pattern = args.get("pattern", None)
        timeout = args.get("timeout", 300)
        overwrite = args.get("overwrite", True)
        sudo = args.get("sudo", False)
        conn = pool.get(server)
        result = conn.download(remote_path, local_path, timeout=timeout,
                               pattern=pattern, overwrite=overwrite, sudo=sudo)
        return _raw_output(result)

    elif name == "ssh_upload_all_scripts":
        server = args.get("server", "")
        sudo = args.get("sudo", False)
        conn = pool.get(server)
        result = conn.upload_all_scripts(sudo=sudo)
        return _raw_output(result)

    elif name == "ssh_run_alias":
        server = args.get("server", "")
        alias_name = args.get("alias_name", "")
        conn = pool.get(server)
        result = conn.run_alias(alias_name)
        return _raw_output(result)

    elif name == "ssh_list_aliases":
        server = args.get("server", "")
        conn = pool.get(server)
        aliases = conn.list_aliases()
        return mcp_text(json.dumps({"count": len(aliases), "aliases": aliases},
                                    indent=2, ensure_ascii=False))

    return mcp_text(f"Unknown tool: {name}", True)


# ─── Tool definitions ───────────────────────────────────

def _build_tools_list() -> list:
    tools = [
        {
            "name": "ssh_run",
            "description": "Execute a command on a remote server. Set `sudo: true` to run as root (requires sudo_password in server YAML).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "Server name (yml filename without .yml)"},
                    "command": {"type": "string", "description": "Command to execute"},
                    "timeout": {"type": "number", "description": "Timeout in seconds", "default": 60},
                    "sudo": {"type": "boolean", "description": "Run as root via sudo", "default": False},
                },
                "required": ["server", "command"],
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": True},
        },
        {
            "name": "ssh_upload_script",
            "description": "Upload a local script to the server scripts_dir. With sudo=true, stages via /tmp and installs preserving original owner/mode (or matching parent dir for new files).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "Server name"},
                    "local_path": {"type": "string", "description": "Local script path"},
                    "script_name": {"type": "string", "description": "Optional rename (default: original filename)"},
                    "run_immediately": {"type": "boolean", "description": "Run script immediately (default false)"},
                    "timeout": {"type": "number", "description": "Timeout in seconds, applied only when run_immediately=true", "default": 300},
                    "sudo": {"type": "boolean", "description": "Install + run as root", "default": False},
                },
                "required": ["server", "local_path"],
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": False},
        },
        {
            "name": "ssh_run_script",
            "description": "Execute an uploaded script (from scripts_dir) on the remote server.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "Server name"},
                    "script_name": {"type": "string", "description": "Script filename"},
                    "timeout": {"type": "number", "description": "Timeout in seconds", "default": 300},
                    "sudo": {"type": "boolean", "description": "Run as root", "default": False},
                },
                "required": ["server", "script_name"],
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": False},
        },
        {
            "name": "ssh_list_scripts",
            "description": "List uploaded scripts on the remote server.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "Server name"},
                    "sudo": {"type": "boolean", "description": "List as root (for root-owned scripts_dir)", "default": False},
                },
                "required": ["server"],
            },
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "ssh_list_servers",
            "description": "List all available server configurations (auto-loaded from servers/ .yml files)",
            "inputSchema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "ssh_download",
            "description": "Download a file or directory from the remote server to local path. With sudo=true, stages via /tmp + chown to user, then SFTPs (original file is NEVER modified).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "Server name"},
                    "remote_path": {"type": "string", "description": "Remote file or directory path"},
                    "local_path": {"type": "string", "description": "Local file or directory path"},
                    "pattern": {"type": "string", "description": "Optional regex pattern to filter filenames (e.g. '.log$' to download only .log files)"},
                    "timeout": {"type": "number", "description": "Timeout in seconds", "default": 300},
                    "sudo": {"type": "boolean", "description": "Read root-owned files via sudo stage", "default": False},
                },
                "required": ["server", "remote_path", "local_path"],
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": False},
        },
        {
            "name": "ssh_upload_all_scripts",
            "description": "Upload all scripts from alias definitions by traversing their script paths.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "Server name"},
                    "sudo": {"type": "boolean", "description": "Install as root (preserves original owner/mode on overwrite)", "default": False},
                },
                "required": ["server"],
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": False},
        },
        {
            "name": "ssh_run_alias",
            "description": "Run an alias-defined quick command (check ssh_list_aliases first for available names).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "Server name"},
                    "alias_name": {"type": "string", "description": "Alias name"},
                },
                "required": ["server", "alias_name"],
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": True},
        },
        {
            "name": "ssh_list_aliases",
            "description": "List quick commands (aliases) configured for a server.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "Server name"},
                },
                "required": ["server"],
            },
            "annotations": {"readOnlyHint": True},
        },
    ]
    # Dynamically generate alias tools
    servers = pool.list_servers()
    for srv in servers:
        sname = srv.get("name", "")
        try:
            data = pool._load_config(sname)
            aliases = data.get("aliases", [])
            for alias in aliases:
                aname = alias.get("name", "")
                desc = alias.get("desc", "")
                script = alias.get("script", "") or alias.get("inline", "")
                script_hint = "(inline)" if "inline" in alias else f"(runs {script})"
                if aname:
                    tools.append({
                        "name": f"ssh_alias:{sname}:{aname}",
                        "description": f"[{sname}] {desc} {script_hint}",
                        "inputSchema": {"type": "object", "properties": {}},
                        "annotations": {"readOnlyHint": False, "destructiveHint": True},
                    })
        except Exception:
            pass
    return tools


# ─── MCP Protocol (JSON-RPC 2.0 over stdio) ─────────────────

def handle_request(req: dict) -> dict:
    method = req.get("method", "")
    params = req.get("parameters", {}) or req.pop("params", {}) or {}
    req_id = req.get("id", 0)

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "ssh-alias-mcp", "version": "1.0.0"},
            },
        }

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": _build_tools_list()}}

    if method == "tools/call":
        result = handle_tools_call(params)
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def write_response(msg: dict):
    line = json.dumps(msg, ensure_ascii=False) + "\n"
    sys.stdout.write(line)
    sys.stdout.flush()


def main():
    """MCP stdio main loop"""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        try:
            resp = handle_request(req)
            write_response(resp)
        except Exception as e:
            req_id = req.get("id", 0)
            error_resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32000, "message": str(e)},
            }
            write_response(error_resp)


if __name__ == "__main__":
    main()
