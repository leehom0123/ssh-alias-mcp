# CLI Usage Reference

All commands that connect to SSH support `-s` (sudo) and `-t` (timeout).
`run`, `run-script`, and `alias` support real-time streaming output via `stream_cb`.

## Commands

```bash
python cli.py list-servers                             # List all servers
python cli.py <server> run "<command>" [-s] [-t sec]   # Execute a command (-s = sudo, streams output)
python cli.py <server> run-script <name> [-s] [-t sec]  # Run an uploaded script (streams output)
python cli.py <server> alias <name>                     # Run an alias (streams output, sudo in YAML)
python cli.py <server> upload <local-path> [-r] [-s] [-n name] [-t sec]  # Upload script (-s = sudo install)
python cli.py <server> download <remote> <local> [-s] [-p PATTERN] [-t sec]  # Download file/dir (-s = sudo read)
python cli.py <server> upload-all [-s] [-t sec]         # Upload all alias scripts (-s = sudo)
python cli.py <server> list-scripts [-s] [-t sec]       # List remote scripts (-s = list root-owned)
python cli.py <server> list-aliases                      # List aliases (local, no SSH)
```

## Common Flags

- `-s` / `--sudo` — Execute as root (requires `sudo_password`). Upload: stages via /tmp preserving owner/mode. Download: stages via /tmp + chown.
- `-t` / `--timeout` SECS — Timeout in seconds (default: 300)

## Per-Command Flags

- `-n` / `--name` NAME — (upload) Custom script name on remote
- `-r` / `--run` — (upload) Run script immediately after upload
- `-p` / `--pattern` PAT — (download) Regex pattern to filter filenames
- `--no-overwrite` — (download) Skip existing local files

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

# List root-owned scripts
python cli.py my-server list-scripts -s
```

## Notes

- **sudo** — Use `run` with `sudo: true`, do not inline `sudo -S` in `run`
- **Docker permissions** — If the user is not in the `docker` group, set `sudo: true` on the alias
