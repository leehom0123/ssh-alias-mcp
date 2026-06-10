---
name: ssh-manager
description: Manage remote Linux servers via SSH CLI or server-management MCP. Run commands, deploy, restart services, check logs, and upload scripts.
---

# SSH Manager

Two ways to operate: **MCP tools** (directly in the AI agent session) and **CLI** (manual terminal use).

## MCP Tools

| Tool | Parameters | Description |
|------|------------|-------------|
| `ssh_list_servers` | — | List all configured servers |
| `ssh_list_aliases` | `server` | List quick-command aliases |
| `ssh_list_scripts` | `server` | List uploaded scripts on remote |
| `ssh_run` | `server`, `command`, `timeout`(default 60s) | Execute a command |
| `ssh_run_sudo` | `server`, `command`, `timeout`(default 60s) | Execute as root (requires `sudo_password`) |
| `ssh_run_alias` | `server`, `alias_name` | Run an alias |
| `ssh_run_script` | `server`, `script_name`, `timeout`(default 300s) | Run an uploaded script |
| `ssh_upload_script` | `server`, `local_path`, `script_name`(optional), `run_immediately`(default false), `timeout`(default 300s) | Upload a script |
| `ssh_alias:{server}:{name}` | — (auto-generated) | One-click alias execution |
| `ssh_upload_all_scripts` | `server` | Upload all scripts from alias definitions |

### Notes
- **sudo** — Use `ssh_run_sudo`, do not inline `sudo -S` in `ssh_run`
- **Prefer aliases** — Define common operations as aliases, invoke via `ssh_run_alias`
- **Docker permissions** — If the user is not in the `docker` group, set `sudo: true` on the alias or use `ssh_run_sudo`

## CLI Commands

```
python ./cli.py list-servers                            # List all servers
python ./cli.py <server> run "<command>" [-t sec]        # Execute a remote command
python ./cli.py <server> sudo "<command>" [-t sec]       # Execute as root (requires sudo_password)
python ./cli.py <server> alias <alias-name>              # Run an alias
python ./cli.py <server> upload <local-path> [-r|--run] [-n name]  # Upload script (-r/--run to run immediately)
python ./cli.py <server> upload-all                      # Upload all alias scripts
python ./cli.py <server> list-scripts                    # List uploaded scripts
python ./cli.py <server> list-aliases                    # List aliases
```

## Configuration & Aliases

Full server yml, alias, and shared-inheritance reference → [README.md](README.md)
