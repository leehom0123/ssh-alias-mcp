#!/usr/bin/env python3
"""SSH Client CLI - Command line entry point

Usage:
    python cli.py list-servers
    python cli.py <server> run "<command>"
    python cli.py <server> alias <name>
    python cli.py <server> upload <local-script> [-r]
    python cli.py <server> upload-all
    python cli.py <server> list-scripts
    python cli.py <server> list-aliases

Examples:
    python cli.py my-server run "tail -100 /var/log/app.log"
    python cli.py my-server alias healthcheck
    python cli.py my-server upload-all
    python cli.py my-server run "docker ps"
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ssh_client import pool, SSHConnection

HELP = """SSH Client CLI

Usage:
    python cli.py list-servers
    python cli.py <server> <command> [args...]

Commands:
    list-servers              List all configured servers
    <server> run <cmd>        Run a command on remote server
    <server> sudo <cmd>       Run a command as root (requires sudo_password in server config)
    <server> alias <name>     Run an alias-defined command
    <server> upload <path>    Upload a local script (-r to run immediately)
    <server> upload-all       Upload all scripts from alias definitions
    <server> list-scripts     List uploaded scripts on remote
    <server> list-aliases     List configured aliases

Examples:
    python cli.py my-server run "uptime"
    python cli.py my-server sudo "apt update"
    python cli.py my-server alias healthcheck
    python cli.py my-server upload D:\\scripts\\fix.sh -r
    python cli.py my-server upload-all
"""


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "-h" or sys.argv[1] == "--help":
        print(HELP)
        sys.exit(0)

    first = sys.argv[1]
    rest = sys.argv[2:]
    timeout = 300

    # Parse -t / --timeout
    i = 2
    while i < len(sys.argv) - 1:
        if sys.argv[i] in ("-t", "--timeout"):
            timeout = int(sys.argv[i + 1])
            rest = [a for a in rest if a not in ("-t", "--timeout", str(timeout))]
        i += 1

    # list-servers
    if first == "list-servers":
        servers = pool.list_servers()
        print(json.dumps({"count": len(servers), "servers": servers}, indent=2, ensure_ascii=False))
        return

    # Get connection
    try:
        conn = pool.get(first)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Route command
    cmd = rest[0] if rest else None

    if cmd == "run" and len(rest) >= 2:
        result = conn.run(" ".join(rest[1:]), timeout=timeout)
        if result["stdout"]:
            sys.stdout.write(result["stdout"])
        if result["stderr"]:
            sys.stderr.write(result["stderr"])
        sys.exit(result["code"])

    elif cmd == "sudo" and len(rest) >= 2:
        result = conn.run_sudo(" ".join(rest[1:]), timeout=timeout)
        if result["stdout"]:
            sys.stdout.write(result["stdout"])
        if result["stderr"]:
            sys.stderr.write(result["stderr"])
        sys.exit(result["code"])

    elif cmd == "alias" and len(rest) >= 2:
        result = conn.run_alias(rest[1])
        if result["stdout"]:
            sys.stdout.write(result["stdout"])
        if result["stderr"]:
            sys.stderr.write(result["stderr"])
        sys.exit(result["code"])

    elif cmd == "upload" and len(rest) >= 2:
        local_script = rest[1]
        run_immediately = "-r" in rest or "--run" in rest
        name = None
        for j, a in enumerate(rest):
            if a in ("-n", "--name") and j + 1 < len(rest):
                name = rest[j + 1]
        result = conn.upload_script(local_script, script_name=name,
                                     run_immediately=run_immediately, timeout=timeout)
        print(result["stdout"], end="")
        if result.get("run_stdout"):
            print(result["run_stdout"], end="")
        if result.get("run_stderr"):
            sys.stderr.write(result["run_stderr"])
        sys.exit(result["code"])

    elif cmd == "upload-all":
        result = conn.upload_all_scripts()
        print(result["stdout"], end="")
        sys.exit(result["code"])

    elif cmd == "list-scripts":
        result = conn.list_scripts()
        if result["stdout"]:
            sys.stdout.write(result["stdout"])
        sys.exit(result["code"])

    elif cmd == "list-aliases":
        aliases = conn.list_aliases()
        print(json.dumps({"count": len(aliases), "aliases": aliases}, indent=2, ensure_ascii=False))

    else:
        print(f"Usage: python cli.py <server> <command>", file=sys.stderr)
        print(HELP, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
