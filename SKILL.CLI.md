---
name: ssh-alias-mcp
description: AI-driven server operations via SSH CLI. Run commands, deploy, restart services, check logs, and upload scripts. Supports bash, cmd, and powershell.

## CLI Commands

All commands that connect to SSH support `-s` (sudo) and `-t` (timeout).

```
python ./cli.py list-servers                             # List all servers
python ./cli.py <server> run "<command>" [-s] [-t sec]   # Execute a command (-s = sudo)
python ./cli.py <server> run-script <name> [-s] [-t sec]  # Run an uploaded script
python ./cli.py <server> alias <name>                     # Run an alias (sudo defined in YAML)
python ./cli.py <server> upload <local-path> [-r] [-s] [-n name] [-t sec]  # Upload script (-s = sudo install)
python ./cli.py <server> download <remote> <local> [-s] [-p PATTERN] [-t sec]  # Download file/dir (-s = sudo read)
python ./cli.py <server> upload-all [-s] [-t sec]         # Upload all alias scripts (-s = sudo)
python ./cli.py <server> list-scripts [-s] [-t sec]       # List remote scripts (-s = list root-owned)
python ./cli.py <server> list-aliases                      # List aliases (local, no SSH)
```

Common flags:
- `-s` / `--sudo` — Execute as root (requires `sudo_password`). Upload: stages via /tmp preserving owner/mode. Download: stages via /tmp + chown.
- `-t` / `--timeout` SECS — Timeout in seconds (default: 300)

Per-command flags:
- `-n` / `--name` NAME — (upload) Custom script name on remote
- `-r` / `--run` — (upload) Run script immediately after upload
- `-p` / `--pattern` PAT — (download) Regex pattern to filter filenames


### Notes
- **sudo** — Use `run` with `sudo: true`, do not inline `sudo -S` in `run`
- **Docker permissions** — If the user is not in the `docker` group, set `sudo: true` on the alias

## Shell Support

Set `shell` in server YAML to auto-adapt commands:

| Shell | Use Case |
|-------|---------|
| `bash` | Linux/macOS servers (default) |
| `cmd` | Windows servers (CMD) |
| `powershell` | Windows servers (PowerShell) |

## Configuration & Aliases

Full server yml, alias, and shared-inheritance reference → [DOCS.md](DOCS.md)
