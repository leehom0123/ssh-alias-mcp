#!/usr/bin/env python3
"""SSH Client CLI - Command line entry point

Usage:
    python cli.py list-servers
    python cli.py create-server <name> <config.yml>
    python cli.py update-server <name> <config.yml> [--replace]
    python cli.py copy-server <source> <new-name>
    python cli.py delete-server <name>
    python cli.py <server> run "<command>" [-s]      # -s = run as root via sudo
    python cli.py <server> run-script <name> [-s]    # run uploaded script (optional sudo)
    python cli.py <server> alias <name>              # sudo is part of the alias YAML config
    python cli.py <server> upload <local-script> [-r] [-s] [-n NAME]
    python cli.py <server> upload-all
    python cli.py <server> list-scripts
    python cli.py <server> list-aliases
    python cli.py <server> download <remote> <local> [-p PATTERN]

Examples:
    python cli.py my-server run "tail -100 /var/log/app.log"
    python cli.py my-server run "apt update" -s
    python cli.py my-server run-script restart.sh -s
    python cli.py my-server alias healthcheck
    python cli.py my-server upload-all
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ssh_client import pool, _normalize_path, load_yaml

HELP = """SSH Client CLI

Usage:
    python cli.py list-servers
    python cli.py create-server <name> <config.yml>
    python cli.py update-server <name> <config.yml> [--replace]
    python cli.py copy-server <source> <new-name>
    python cli.py delete-server <name>
    python cli.py <server> <command> [args...] [-s] [-t SECONDS]

Commands:
    list-servers                List all configured servers
    create-server <name> <file> Create a server from a YAML config
    update-server <name> <file> Merge a YAML config patch (--replace for full replace)
    copy-server <source> <name> Copy a server config to a new name
    delete-server <name>        Delete a server config
    <server> run <cmd>          Run a command on remote server
    <server> run-script <name>  Run an uploaded script (from scripts_dir)
    <server> alias <name>       Run an alias-defined command (sudo set in YAML)
    <server> upload <path>      Upload local script (-r = run after upload; -n NAME)
    <server> download <remote> <local>  Download a file or dir from remote (-p PATTERN)
    <server> upload-all         Upload all scripts from alias definitions
    <server> list-scripts       List uploaded scripts on remote
    <server> list-aliases       List configured aliases (local, no SSH)

Common flags (work with ALL commands that connect to SSH):
    -s, --sudo            Execute the operation as root (requires sudo_password
                          in server config). For upload: stages via /tmp and
                          installs preserving original owner/mode (or matching
                          parent dir for new files). For download: stages via
                          /tmp + chown to user, then SFTPs (original file is
                          NEVER modified).
    -t, --timeout SECS    Command timeout in seconds (default: 300)

Per-command flags:
    -n, --name NAME       (upload) Custom script name on remote
    -r, --run             (upload) Run script immediately after upload
    -p, --pattern PAT     (download) Regex pattern to filter filenames

Examples:
    python cli.py my-server run "uptime"
    python cli.py my-server run "apt update" -s
    python cli.py my-server run-script restart.sh -s
    python cli.py my-server alias healthcheck
    python cli.py my-server upload ./fix.sh -r -s
    python cli.py my-server download /var/log/secure ./secure.log -s
    python cli.py my-server list-scripts -s     # list root-owned scripts dir
"""


def _extract_flag(args: list, *flags) -> bool:
    """Pop boolean flag from args, return True if present."""
    found = False
    for f in flags:
        while f in args:
            args.remove(f)
            found = True
    return found


def _pop_tail_flags(tokens: list, bool_flags: set, opt_flags: set) -> dict:
    """Pop recognised flags from the TAIL of tokens only.

    Flags buried inside the command text (e.g. ``grep -s foo``) are not at
    the tail and therefore survive into the payload unchanged.
    Returns {flag: True} for booleans and {flag: value} for valued options.
    """
    found = {}
    while tokens:
        if tokens[-1] in bool_flags:
            found[tokens[-1]] = True
            tokens.pop()
        elif len(tokens) >= 2 and tokens[-2] in opt_flags:
            found[tokens[-2]] = tokens[-1]
            del tokens[-2:]
        else:
            break
    return found


def _flag_value(found: dict, *names):
    for n in names:
        if n in found:
            return found[n]
    return None


def _write_result(result: dict) -> int:
    """Write a standard SSH result and return its exit code."""
    if result.get("stdout"):
        sys.stdout.write(result["stdout"])
    if result.get("stderr"):
        sys.stderr.write(result["stderr"])
    return result.get("code", 1)


def _write_upload_result(result: dict) -> int:
    """Write upload output, including optional immediate-run streams."""
    if result.get("stdout"):
        sys.stdout.write(result["stdout"])
    if result.get("run_stdout"):
        sys.stdout.write(result["run_stdout"])
    if result.get("stderr"):
        sys.stderr.write(result["stderr"])
    if result.get("run_stderr"):
        sys.stderr.write(result["run_stderr"])
    return result.get("code", 1)


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "-h" or sys.argv[1] == "--help":
        print(HELP)
        sys.exit(0)

    first = sys.argv[1]
    rest = sys.argv[2:]

    # list-servers
    if first == "list-servers":
        servers = pool.list_servers()
        print(json.dumps({"count": len(servers), "servers": servers}, indent=2, ensure_ascii=False))
        return

    if first in ("create-server", "update-server"):
        if len(rest) < 2:
            print(f"Usage: python cli.py {first} <name> <config.yml>", file=sys.stderr)
            sys.exit(1)
        name = rest[0]
        config = load_yaml(_normalize_path(rest[1]))
        if first == "create-server":
            result = pool.create_server(name, config)
        else:
            result = pool.update_server(
                name, config, replace=_extract_flag(rest, "--replace")
            )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    if first == "delete-server":
        if len(rest) != 1:
            print("Usage: python cli.py delete-server <name>", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(pool.delete_server(rest[0]), indent=2, ensure_ascii=False))
        return

    if first == "copy-server":
        if len(rest) != 2:
            print("Usage: python cli.py copy-server <source> <new-name>", file=sys.stderr)
            sys.exit(1)
        result = pool.copy_server(rest[0], rest[1])
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    # Get connection
    conn = pool.get(first)

    # Route command; flags are only consumed from the TAIL of the arguments
    # after the subcommand, so flags inside the command text are preserved.
    cmd = rest[0] if rest else None
    payload = rest[1:]
    flags = _pop_tail_flags(
        payload,
        bool_flags={"-s", "--sudo", "-r", "--run", "--no-overwrite"},
        opt_flags={"-t", "--timeout", "-n", "--name", "-p", "--pattern"},
    )
    sudo = ("-s" in flags) or ("--sudo" in flags)
    timeout_str = _flag_value(flags, "-t", "--timeout")
    if timeout_str is not None:
        try:
            timeout = int(timeout_str)
        except ValueError:
            print(f"Invalid timeout: {timeout_str}", file=sys.stderr)
            sys.exit(2)
    else:
        timeout = conn.timeout

    if cmd == "run" and payload:
        command = " ".join(payload)
        result = conn.run(
            command, timeout=timeout, sudo=sudo,
            stream_cb=lambda chunk, is_stderr: (
                sys.stderr.write(chunk) if is_stderr else sys.stdout.write(chunk)
            ),
        )
        sys.exit(result.get("code", 1))

    elif cmd == "alias" and payload:
        # sudo for alias is defined inside the YAML (per-alias `sudo: true`)
        result = conn.run_alias(payload[0], stream_cb=lambda chunk, is_stderr: (
            sys.stderr.write(chunk) if is_stderr else sys.stdout.write(chunk)
        ))
        sys.exit(result.get("code", 1))

    elif cmd == "run-script" and payload:
        result = conn.run_script(payload[0], timeout=timeout, sudo=sudo, stream_cb=lambda chunk, is_stderr: (
            sys.stderr.write(chunk) if is_stderr else sys.stdout.write(chunk)
        ))
        sys.exit(result.get("code", 1))

    elif cmd == "upload" and payload:
        local_script = _normalize_path(payload[0])
        run_immediately = ("-r" in flags) or ("--run" in flags)
        name = _flag_value(flags, "-n", "--name")
        result = conn.upload_script(local_script, script_name=name,
                                     run_immediately=run_immediately,
                                     timeout=timeout, sudo=sudo)
        sys.exit(_write_upload_result(result))

    elif cmd == "upload-all":
        result = conn.upload_all_scripts(sudo=sudo)
        sys.exit(_write_result(result))

    elif cmd == "list-scripts":
        result = conn.list_scripts(sudo=sudo)
        sys.exit(_write_result(result))

    elif cmd == "list-aliases":
        aliases = conn.list_aliases()
        print(json.dumps({"count": len(aliases), "aliases": aliases}, indent=2, ensure_ascii=False))

    elif cmd == "download" and len(payload) >= 2:
        remote_path = payload[0]
        local_path = _normalize_path(payload[1])
        pattern = _flag_value(flags, "-p", "--pattern")
        overwrite = "--no-overwrite" not in flags
        result = conn.download(remote_path, local_path, timeout=timeout,
                               pattern=pattern, overwrite=overwrite, sudo=sudo)
        sys.exit(_write_result(result))

    else:
        print(f"Usage: python cli.py <server> <command>", file=sys.stderr)
        print(HELP, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
