#!/usr/bin/env python3
"""SSH Client CLI - Command line entry point for ssh-alias-mcp.

Run `python cli.py --help` (or with no arguments) for full usage.
HELP below is the single source of truth for the usage text.
"""
import sys
import json
from pathlib import Path

import paramiko

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
    <server> run -              Read command from stdin (use quoted heredoc
                          <<'EOF' so the local shell won't expand $)
    <server> run-script <name>  Run an uploaded script (from scripts_dir)
    <server> alias <name>       Run an alias-defined command (sudo set in YAML)
    <server> upload <path>      Upload local script (-r = run after upload; -n NAME)
    <server> upload-file <local> <remote>  Upload one local file to an explicit path
    <server> download <remote> <local>  Download a file or dir from remote (-p PATTERN)
    <server> download-file <remote> <local>  Download one remote file to an explicit path
    <server> download-script <name> <local>  Download a file from scripts_dir
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
    -t, --timeout SECS    Command timeout in seconds
                          (default: server ``timeout`` from its YAML config)

Per-command flags:
    -n, --name NAME       (upload) Custom script name on remote
    -r, --run             (upload) Run script immediately after upload
    -p, --pattern PAT     (download) Regex pattern to filter filenames
    -x, --executable      (upload-file) Set execute permission on Unix
    --no-overwrite        (upload-file/download/download-file/download-script)
                          Fail if the destination already exists

Flags are consumed from the END of the argument list only, so they must
appear after all positional arguments. Anything before the trailing flag
block stays verbatim in the command payload (e.g. `run "grep -s foo" -t 5`
passes `grep -s foo` unchanged).

Examples:
    python cli.py my-server run "uptime"
    python cli.py my-server run "apt update" -s
    python cli.py my-server run-script restart.sh -s
    python cli.py my-server alias healthcheck
    python cli.py my-server upload ./fix.sh -r -s
    python cli.py my-server download /var/log/secure ./secure.log -s
    python cli.py my-server upload-file ./config.json /etc/myapp/config.json -s
    python cli.py my-server download-file /etc/myapp/app.log ./app.log --no-overwrite
    python cli.py my-server list-scripts -s     # list root-owned scripts dir
"""

# All per-server flags flow through the single tail-consumer in main().
# Flags buried in the command payload are never consumed.
_BOOL_FLAGS = {"-s", "--sudo", "-r", "--run", "--no-overwrite", "-x", "--executable"}
_OPT_FLAGS = {"-t", "--timeout", "-n", "--name", "-p", "--pattern"}


def _extract_flag(args: list, *flags) -> bool:
    """Pop boolean flag from anywhere in args, return True if present."""
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


def _safe_json(payload) -> str:
    """Pretty-print JSON.

    Some legacy server configs contain literal ``\\udcxx`` escapes that YAML
    loads as real lone surrogates, which are unencodable in UTF-8. Fall back
    to ``ensure_ascii=True`` so the command still emits valid JSON instead of
    crashing the whole listing.
    """
    # json.dumps(ensure_ascii=False) itself never raises (it returns a str);
    # the UnicodeEncodeError only fires later at print/write, so proactively
    # test-encode here to decide whether the ASCII-escaped form is needed.
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        text = json.dumps(payload, indent=2, ensure_ascii=True)
    return text


def _stream_chunk(chunk: str, is_stderr: bool):
    """Real-time stream callback: echo remote output as it arrives."""
    (sys.stderr if is_stderr else sys.stdout).write(chunk)


def _write_result(result: dict) -> int:
    """Write a standard SSH result (plus optional immediate-run streams)
    and return its exit code."""
    for key in ("stdout", "run_stdout"):
        if result.get(key):
            sys.stdout.write(result[key])
    for key in ("stderr", "run_stderr"):
        if result.get(key):
            sys.stderr.write(result[key])
    return result.get("code", 1)


def _dispatch(first: str, rest: list):
    # list-servers
    if first == "list-servers":
        servers = pool.list_servers()
        print(_safe_json({"count": len(servers), "servers": servers}))
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
        print(_safe_json(result))
        return

    if first == "delete-server":
        if len(rest) != 1:
            print("Usage: python cli.py delete-server <name>", file=sys.stderr)
            sys.exit(1)
        print(_safe_json(pool.delete_server(rest[0])))
        return

    if first == "copy-server":
        if len(rest) != 2:
            print("Usage: python cli.py copy-server <source> <new-name>", file=sys.stderr)
            sys.exit(1)
        result = pool.copy_server(rest[0], rest[1])
        print(_safe_json(result))
        return

    # Per-server commands. Flags come from the tail only; the remaining
    # tokens are the positional payload.
    conn = pool.get(first)

    cmd = rest[0] if rest else None
    payload = rest[1:]
    flags = _pop_tail_flags(payload, _BOOL_FLAGS, _OPT_FLAGS)
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
    overwrite = not _flag_value(flags, "--no-overwrite")

    if cmd == "run" and payload:
        if payload == ["-"]:
            # Read the command from stdin instead of argv. Lets callers pass
            # $/quote-heavy payloads (awk/sed one-liners) without the local
            # shell mangling them — pipe or use a QUOTED heredoc:
            #   cli.py srv run - <<'EOF'
            #   awk 'match($0,/x/){print}' file
            #   EOF
            command = sys.stdin.read()
            if not command.strip():
                print("Empty command read from stdin", file=sys.stderr)
                sys.exit(2)
        elif "-" in payload:
            print("'-' must be the only command argument "
                  "(reads the whole command from stdin)", file=sys.stderr)
            sys.exit(2)
        else:
            command = " ".join(payload)
        result = conn.run(
            command, timeout=timeout, sudo=sudo, stream_cb=_stream_chunk,
        )
        sys.exit(result.get("code", 1))

    elif cmd == "alias" and payload:
        # sudo for alias is defined inside the YAML (per-alias `sudo: true`)
        result = conn.run_alias(payload[0], stream_cb=_stream_chunk)
        sys.exit(result.get("code", 1))

    elif cmd == "run-script" and payload:
        result = conn.run_script(payload[0], timeout=timeout, sudo=sudo,
                                 stream_cb=_stream_chunk)
        sys.exit(result.get("code", 1))

    elif cmd == "upload" and payload:
        result = conn.upload_script(
            _normalize_path(payload[0]),
            script_name=_flag_value(flags, "-n", "--name"),
            run_immediately=("-r" in flags) or ("--run" in flags),
            timeout=timeout, sudo=sudo,
        )
        sys.exit(_write_result(result))

    elif cmd == "upload-file" and len(payload) == 2:
        result = conn.upload_file(
            _normalize_path(payload[0]), payload[1], timeout, overwrite, sudo,
            executable=("-x" in flags) or ("--executable" in flags),
        )
        sys.exit(_write_result(result))

    elif cmd == "upload-all":
        sys.exit(_write_result(conn.upload_all_scripts(sudo=sudo)))

    elif cmd == "list-scripts":
        sys.exit(_write_result(conn.list_scripts(sudo=sudo)))

    elif cmd == "list-aliases":
        aliases = conn.list_aliases()
        print(_safe_json({"count": len(aliases), "aliases": aliases}))

    elif cmd == "download" and len(payload) >= 2:
        result = conn.download(payload[0], _normalize_path(payload[1]),
                               timeout=timeout,
                               pattern=_flag_value(flags, "-p", "--pattern"),
                               overwrite=overwrite, sudo=sudo)
        sys.exit(_write_result(result))

    elif cmd == "download-file" and len(payload) == 2:
        result = conn.download_file(payload[0], _normalize_path(payload[1]),
                                    timeout, overwrite, sudo)
        sys.exit(_write_result(result))

    elif cmd == "download-script" and len(payload) == 2:
        result = conn.download_script(payload[0], _normalize_path(payload[1]),
                                      timeout, overwrite, sudo)
        sys.exit(_write_result(result))

    else:
        print(f"Usage: python cli.py <server> <command>", file=sys.stderr)
        print(HELP, file=sys.stderr)
        sys.exit(1)


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "-h" or sys.argv[1] == "--help":
        print(HELP)
        sys.exit(0)

    first = sys.argv[1]
    rest = sys.argv[2:]

    try:
        _dispatch(first, rest)
    except (ValueError, RuntimeError, OSError, paramiko.SSHException) as exc:
        # Known operational errors: clean one-line message, no traceback.
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
