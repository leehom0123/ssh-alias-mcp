# CLI Usage Reference

All commands that connect to SSH support `-s` (sudo) and `-t` (timeout).
`run`, `run-script`, and `alias` support real-time streaming output via `stream_cb`.

## Commands

```bash
python cli.py list-servers                             # List all servers
python cli.py create-server <name> <config.yml>        # Create; refuses overwrite
python cli.py update-server <name> <patch.yml> [--replace]  # Merge patch or replace
python cli.py copy-server <source> <new-name>           # Copy to a new server name
python cli.py delete-server <name>                     # Delete local server config
python cli.py <server> run "<command>" [-s] [-t sec]   # Execute a command (-s = sudo, streams output)
python cli.py <server> run-script <name> [-s] [-t sec]  # Run an uploaded script (streams output)
python cli.py <server> alias <name>                     # Run an alias (streams output, sudo in YAML)
python cli.py <server> upload <local-path> [-r] [-s] [-n name] [-t sec]  # Upload script (-s = sudo install)
python cli.py <server> upload-file <local> <remote> [-s] [-x] [-t sec]  # Upload one file to explicit path (-x = executable)
python cli.py <server> download <remote> <local> [-s] [-p PATTERN] [-t sec]  # Download file/dir (-s = sudo read)
python cli.py <server> download-file <remote> <local> [-s] [-t sec]  # Download one file to explicit path
python cli.py <server> download-script <name> <local> [-s] [-t sec]  # Download script from scripts_dir
python cli.py <server> upload-all [-s] [-t sec]         # Upload all alias scripts (-s = sudo)
python cli.py <server> list-scripts [-s] [-t sec]       # List remote scripts (-s = list root-owned)
python cli.py <server> list-aliases                      # List aliases (local, no SSH)
```

## Common Flags

- `-s` / `--sudo` — Execute as root (requires `sudo_password`). Upload: stages via /tmp preserving owner/mode. Download: stages via /tmp + chown.
- `-t` / `--timeout` SECS — Timeout in seconds (default: server `timeout` from YAML)

Quoting: your **local shell** processes the command line first. Plain commands
need no quotes (`run df -h`); characters the local shell would consume — `| > ; & * ?` —
must be inside quotes to reach the remote shell, and `$`/backticks additionally need
single quotes or `run -` (double quotes still expand them).

Flags are parsed **from the tail** of the arguments after the subcommand only.
Write them at the end so flags inside the command text survive:

```bash
python cli.py my-server run "grep -s ERROR app.log"        # inner -s kept
python cli.py my-server run "grep ERROR app.log" -s        # trailing -s = sudo
```

Commands with heavy `$`/quote usage (awk/sed one-liners) can be read from
stdin instead of argv, via `run -` and a quoted heredoc:

```bash
python cli.py my-server run - <<'EOF'
awk 'match($0,/req: [0-9]+/){print substr($0,RSTART+5,RLENGTH-5)}' log
EOF
```

## Per-Command Flags

- `-n` / `--name` NAME — (upload) Custom script name on remote
- `-r` / `--run` — (upload) Run script immediately after upload
- `-p` / `--pattern` PAT — (download) Regex pattern to filter filenames
- `-x` / `--executable` — (upload-file) Set execute permission on Unix
- `--no-overwrite` — (upload-file / download / download-file / download-script) Fail if the destination already exists
- `--replace` — (update-server) Replace the complete config instead of recursively merging the patch

## Manage Server Configurations

Create and replace operations read standard server YAML files. Updates recursively merge
objects; arrays such as `aliases` are replaced as a whole. Server names may contain only
letters, numbers, dots, underscores, and hyphens.

```bash
python cli.py create-server staging ./staging.yml
python cli.py update-server staging ./port-patch.yml
python cli.py update-server staging ./staging.yml --replace
python cli.py copy-server staging staging-copy
python cli.py delete-server staging
```

## Real-time Streaming

`run`, `run-script`, and `alias` stream output in real-time via `stream_cb` callback.
Output is written to `stdout`/`stderr` as it arrives, not buffered until completion.

## Examples

```bash
# List servers
python cli.py list-servers

# Run command
python cli.py my-server run "uptime"

# Run as root
python cli.py my-server run "apt update" -s

# Run uploaded script
python cli.py my-server run-script restart.sh -s

# Run alias
python cli.py my-server alias healthcheck

# Upload and run immediately
python cli.py my-server upload ./fix.sh -r -s

# Download with sudo
python cli.py my-server download /var/log/secure ./secure.log -s

# Upload/download single files to explicit paths
python cli.py my-server upload-file ./config.json /etc/myapp/config.json -s
python cli.py my-server download-file /etc/myapp/app.log ./app.log

# List root-owned scripts
python cli.py my-server list-scripts -s
```

## Notes

- **sudo** — Use `run` with `sudo: true`, do not inline `sudo -S` in `run`
- **Docker permissions** — If the user is not in the `docker` group, set `sudo: true` on the alias
