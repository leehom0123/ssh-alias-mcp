---
name: ssh-alias-mcp
description: AI-driven server operations via SSH CLI or ssh-alias-mcp MCP. Run commands, deploy, restart services, check logs, and upload scripts. Supports bash, cmd, and powershell.

## MCP Tools

| Tool | Parameters | Description |
|------|------------|-------------|
| `ssh_list_servers` | — | List all configured servers |
| `ssh_list_aliases` | `server` | List quick-command aliases |
| `ssh_list_scripts` | `server`, `sudo`(default false) | List uploaded scripts on remote. With `sudo: true`, list root-owned scripts_dir. |
| `ssh_run` | `server`, `command`, `timeout`(default 60s), `sudo`(default false) | Execute a command. Set `sudo: true` to run as root (requires `sudo_password`). |
| `ssh_run_alias` | `server`, `alias_name` | Run an alias |
| `ssh_run_script` | `server`, `script_name`, `timeout`(default 300s), `sudo`(default false) | Run an uploaded script. Set `sudo: true` to run as root. |
| `ssh_upload_script` | `server`, `local_path`, `script_name`(optional), `run_immediately`(default false), `timeout`(default 300s), `overwrite`(default true), `sudo`(default false) | Upload a script. With `sudo: true`, stages via /tmp and installs preserving original owner/mode. |
| `ssh_download` | `server`, `remote_path`, `local_path`, `pattern`(optional regex), `timeout`(default 300s), `sudo`(default false) | Download a file or directory from remote. With `sudo: true`, reads root-owned files via /tmp staging. |
| `ssh_alias.{server}.{name}` | — (auto-generated) | One-click alias execution |
| `ssh_upload_all_scripts` | `server`, `sudo`(default false) | Upload all scripts from alias definitions. With `sudo: true`, installs as root. |

### Notes
- **sudo** — Use `ssh_run` with `sudo: true`, do not inline `sudo -S` in `ssh_run`
- **Docker permissions** — If the user is not in the `docker` group, set `sudo: true` on the alias
- **Dynamic alias tools** — MCP exposes aliases as `ssh_alias.server.alias`.
- **Live stream output** — If you want to live-stream output, use cli. Look at [SKILL.CLI.md](SKILL.CLI.md) for details.

## Shell Support

Set `shell` in server YAML to auto-adapt commands:

| Shell | Use Case |
|-------|---------|
| `bash` | Linux/macOS servers (default) |
| `cmd` | Windows servers (CMD) |
| `powershell` | Windows servers (PowerShell) |

## Configuration & Aliases

Full server yml, alias, and shared-inheritance reference → [DOCS.md](DOCS.md)
