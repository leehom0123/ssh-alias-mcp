# ssh-alias-mcp Technical Documentation

AI-driven server operations tool over SSH — supports **MCP mode** (AI Agent invocation) and **CLI mode** (manual use). All modes share the same connection pool, proxy logic, and configuration.

## ✨ Key Features

### One Config, Three Shells

The same YAML configuration automatically adapts to three Shell environments:

| Shell | Use Case | Command Examples |
|-------|---------|---------|
| `bash` | Linux/macOS servers | `docker ps`, `systemctl restart` |
| `cmd` | Windows servers (CMD) | `cmd /c "dir /Q"`, `call deploy.bat` |
| `powershell` | Windows servers (PowerShell) | `Get-Service`, `Invoke-WebRequest` |

**Just set the `shell` type in YAML, and the tool handles the rest:**

```yaml
# Linux server
server:
  host: "192.168.1.100"
  shell: bash          # Automatically uses bash command templates

# Windows server
server:
  host: "10.0.0.50"
  shell: powershell    # Automatically uses PowerShell command templates
```

Internally implemented via a **command template dictionary** — each Shell has its own command set (mkdir, file_exists, run_script, move, etc.), no `if/else` branching in code.

### Alias System: One YAML Line = One AI Skill

Define shortcut commands that automatically expose as MCP tools for AI Agents:

```yaml
aliases:
  - name: deploy
    script: deploy.sh
    desc: "Deploy application"
    sudo: true

  - name: healthcheck
    inline: "docker ps && df -h /"
    desc: "Health check"
```

**What the AI Agent sees:**
```
ssh_alias:my-server:deploy       # One-click deploy
ssh_alias:my-server:healthcheck  # One-click health check
```

**Same for CLI:**
```bash
python cli.py my-server alias deploy
python cli.py my-server alias healthcheck
```

**Two alias types:**
- **Inline** — Execute a command directly (for simple operations)
- **Script** — Upload local script then execute (for complex deployments)

**Inheritance support**: Share aliases via `extends` — maintain one common alias file for 50 servers.

## Quick Start

### AI Agent Usage (MCP Mode)

```bash
# Claude Code
claude mcp add ssh-alias-mcp python <this-directory>/mcp_server.py

# OpenCode / Codex CLI — add to mcp.json:
# {
#   "mcpServers": {
#     "ssh-alias-mcp": {
#       "command": "python",
#       "args": ["<this-directory>/mcp_server.py"]
#     }
#   }
# }
```

After registration, the AI Agent can automatically:
- Execute commands on any configured server (`ssh_run` with `sudo: true`)
- Upload and run scripts (`ssh_upload_script`, `ssh_run_script`)
- Invoke predefined shortcuts (`ssh_alias:{server}:{name}`)
- Download remote files (`ssh_download`)

### CLI Usage (Manual)

```bash
cd <this-directory>
python cli.py list-servers
python cli.py my-server run "uptime"
python cli.py my-server alias healthcheck
python cli.py my-server upload /path/to/script.sh -r   # Upload and run immediately
```

## Directory Structure

```
├── <skills-dir>/
│   └── ssh-alias-mcp/         # Core code
│       ├── SKILL.md           # AI Agent skill definition
│       ├── cli.py             # CLI entry point
│       ├── mcp_server.py      # MCP stdio server
│       ├── ssh_client.py      # Core module (SSH connection, proxy, pool)
│       ├── config.yaml        # Global config (servers_dir, proxy, timeout)
│       ├── DOCS.md            # Detailed docs (English, this file)
│       └── DOCS.zh-CN.md      # Detailed docs (Chinese)
│
└── servers/                   # Server config directory (outside skill dir, path in config.yaml)
    ├── my-server.yml          # Server connection info + alias definitions
    ├── _shared/common.yml     # Shared aliases (inherited via extends)
    └── my-server/             # Local shell scripts for this server (.sh)
```

> Server configs `servers/` are separated from the skill directory for easy management and credential isolation. Path configured via `servers_dir` in `config.yaml` (absolute or relative path supported).

## Architecture

```
┌──────────────────────────────────────────────────────┐
│  AI Agent (Claude Code / Codex / OpenCode / ...)     │
│  └── MCP tool calls (ssh_run, ssh_alias:...)         │
└──────────────┬───────────────────────────────────────┘
               │ JSON-RPC over stdio
┌──────────────▼───────────────────────────────────────┐
│  mcp_server.py  (MCP stdio server)                   │
│  └── Dynamically exposes aliases as MCP tools        │
└──────────────┬───────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────┐
│  ssh_client.py  (Shared core)                        │
│  ├── ConnectionPool (connection reuse, 60s keepalive)│
│  ├── SOCKS5 proxy + direct auto-fallback             │
│  └── SFTP script upload & download                   │
└──────────────┬───────────────────────────────────────┘
               │ SSH
┌──────────────▼───────────────────────────────────────┐
│  Remote Server (Linux / Windows)                     │
└──────────────────────────────────────────────────────┘
```

## Configuration

### Global Configuration (`config.yaml`)

```yaml
# servers/ directory path (absolute or relative to this file)
servers_dir: "../servers"

proxy:
  enabled: false              # Globally enable SOCKS5 proxy
  host: "127.0.0.1"           # Proxy address
  port: 1080                  # Proxy port
  timeout: 30                 # Proxy connection timeout (seconds)

server:
  timeout: 30                 # Default SSH connection timeout (seconds)
```

Proxy is tried first, auto-fallback to direct connection on failure.

### Shared Alias Inheritance (`extends`)

#### Three-Level Inheritance Chain

```
config.yaml (global defaults: proxy, timeout)
    ↓ extends
_shared/*.yml (shared connection info, shared aliases)
    ↓ extends
Server YAML (your server — override + append)
```

#### Multiple Inheritance

A server YAML can inherit from multiple files — layered configuration composition:

```yaml
# my-server.yml
extends:
  - _shared/conn-base.yml    # ① Shared host, user, scripts_dir
  - _shared/common.yml       # ② Shared aliases

server:
  host: "198.51.100.10"      # Override ①'s host
  port: 22
  password: "xxx"

aliases:                      # Append — local aliases overlay on inherited
  - name: deploy
    script: deploy.sh
```

#### Merge Rules

| Field | Merge Behavior |
|-------|----------------|
| `server` | Shallow merge — local overrides base |
| `aliases` | All inherited from base. Same `name` overrides, different names append |
| `security` (whitelist/blacklist/command_template) | Base sets defaults, local inherits. Local overrides if present |
| `proxy` | Same as security — local falls back to base |
| `allowed_local_paths` / `allowed_remote_paths` | Same as security — local falls back to base |

### Server Configuration (`{servers_dir}/{name}.yml`)

Create a `.yml` file for each server in the `servers/` directory:

```yaml
# my-server.yml

server:
  # === Required fields ===
  host: "your.host.com"         # Server IP or hostname
  user: "username"               # SSH login username

  # === Display info ===
  name: "My Server"              # Display name
  desc: "Application server"    # Description
  group: "dept/team"            # Group identifier (multi-level path for sorting)

  # Authentication (choose one)
  password: "your-password"      # Password auth
  # key: "/path/to/private_key"     # Or key auth
  # key_password: "passphrase"      # Key passphrase (optional)
  sudo_password: "sudo-pass"     # Sudo password (optional, defaults to password)

  # === Optional fields ===
  port: 22                       # SSH port (default 22)
  timeout: 30                    # SSH connection timeout (seconds, default 30)
  scripts_dir: "/home/user/scripts"  # Remote script directory
  shell: "bash"                  # Shell type (bash / cmd / powershell, default bash)
  system: "Ubuntu 24.04 LTS"    # OS information

  # Server-level proxy override (optional, overrides global config.yaml)
  proxy:
    host: "127.0.0.1"
    port: 10808

  # Security: command filtering (overrides global config.yaml)
  blacklist:
    - "rm -rf|mkfs|dd "          # Regex pattern, match = block
  whitelist:
    - "ls|df|docker|tail"        # Regex pattern, only allow matching commands
  command_template: "cd /opt/app && <command>"  # Auto-wrap commands

  # Upload/download path restrictions (empty = no restriction)
  allowed_local_paths:
    - "/home/bit/scripts"
  allowed_remote_paths:
    - "/home/bit/scripts"

# Quick commands — automatically exposed as MCP tools
aliases:
  # Script type: upload local script file then execute
  - name: deploy
    script: app-deploy.sh        # Script path, relative to YAML file directory
    desc: "Deploy application"
    timeout: 600                 # Command timeout (seconds), default 300
    sudo: true                   # Optional, execute with sudo

  # Inline type: execute a command directly on remote
  - name: logs
    inline: "docker logs --tail 100 my-app"
    desc: "View logs"
    timeout: 10
```

## Shell Type Support

### Three Shell Types

| Shell | Use Case | Command Template |
|-------|---------|-----------------|
| `bash` | Linux/macOS servers | Full bash command set |
| `cmd` | Windows servers (CMD) | `cmd /c "..."` wrapper |
| `powershell` | Windows servers (PowerShell) | Native PowerShell syntax |

### Shell Command Template Comparison

| Template | bash | cmd | powershell |
|----------|------|-----|------------|
| `mkdir` | `mkdir -p {path}` | `cmd /c "mkdir \\"{path}\\" 2>nul"` | `New-Item -ItemType Directory -Path "{path}" -Force` |
| `file_exists` | `test -f {path}` | `cmd /c "if exist \\"{path}\\" echo exists"` | `Test-Path "{path}"` |
| `run_script` | `bash {path}` | `cmd /c "call {path}"` | `& "{path}"` |
| `chmod` | `chmod {mode:o} {path}` | Not supported | Not supported |
| `stat_owner` | `stat -c '%U:%G' {path}` | Not supported | Not supported |
| `stat_mode` | `stat -c '%a' {path}` | Not supported | Not supported |
| `rm_dir` | `rm -rf {path}` | `cmd /c "rmdir /S /Q \\"{path}\\""` | `Remove-Item -Recurse -Force "{path}"` |
| `cp_r` | `cp -r {src} {dst}` | `cmd /c "xcopy /Y \\"{src}\\" \\"{dst}\\" /E /I"` | `Copy-Item -Recurse "{src}" "{dst}"` |
| `move` | `mv {tmp} {target}` | `cmd /c "move /y \\"{tmp}\\" \\"{target}\\""` | `Move-Item -Force "{tmp}" "{target}"` |
| `chown` | `chown {user} {path}` | Not supported | Not supported |
| `chown_r` | `chown -R {user}:{user} {path}` | Not supported | Not supported |
| `list_dir` | `ls -la {path}/` | `dir "{path}" /Q` | `Get-ChildItem -Path "{path}" \| Format-List` |
| `install` | Preserves owner/mode | Move only | Move only |
| `tmp_prefix` | `/tmp` | `%TEMP%` | `$env:TEMP` |

### Feature Differences

| Feature | bash | cmd | powershell |
|---------|------|-----|------------|
| Sudo execution | ✅ | ❌ | ❌ |
| Permission management (chmod/chown) | ✅ | ❌ | ❌ |
| File owner check | ✅ | ❌ | ❌ |
| Pipe commands | ✅ | ✅ | ✅ |
| Multi-line commands | ✅ | ✅ | ✅ |
| Script execution | ✅ | ✅ | ✅ |
| Directory operations | ✅ | ✅ | ✅ |
| File upload/download | ✅ | ✅ | ✅ |

## Usage Modes

### Mode 1: AI Agent via MCP (Primary)

After registration, the Agent auto-discovers all servers and alias tools. Servers are discovered via glob `{servers_dir}/*.yml`, and the Agent retrieves host, user, system, alias, etc. directly from the parsed YAML config.

**Available MCP Tools:**

| Tool | Description |
|------|-------------|
| `ssh_list_servers` | List all configured servers |
| `ssh_run` | Execute command on remote server, set `sudo: true` for root |
| `ssh_upload_script` | Upload local script, optionally run immediately |
| `ssh_download` | Download file from remote, supports sudo/overwrite |
| `ssh_run_script` | Run uploaded script |
| `ssh_list_scripts` | List remote scripts |
| `ssh_upload_all_scripts` | Upload all scripts from alias definitions |
| `ssh_run_alias` | Execute alias shortcut |
| `ssh_list_aliases` | List server aliases |
| `ssh_alias:{server}:{name}` | **Dynamically generated one-click alias** (one tool per alias) |

**Design notes:**
- Aliases dynamically exposed as standalone MCP tools (e.g., `ssh_alias:my-server:deploy`)
- All paths dynamically resolved via `__file__`, no hardcoding

### Mode 2: CLI (Manual)

```bash
cd <this-directory>

# List all servers
python cli.py list-servers

# Execute command
python cli.py <server> run "<command>"

# Root execution (requires sudo_password)
python cli.py <server> run "<command>" -s

# Run uploaded script
python cli.py <server> run-script <name> [-s]

# Execute alias
python cli.py <server> alias <name>

# Upload and run script
python cli.py <server> upload /path/to/script.sh -r/--run [-s]

# Upload all alias scripts
python cli.py <server> upload-all [-s]

# List remote scripts / aliases
python cli.py <server> list-scripts [-s]
python cli.py <server> list-aliases

# Download files
python cli.py <server> download /remote/path ./local/path [-s]
```

All commands support `-t` / `--timeout` (seconds, default 300).

### Mode 3: Python Module

```python
import sys
sys.path.insert(0, "<this-directory>")
from ssh_client import pool

conn = pool.get("my-server")
result = conn.run("ls -la /opt")
print(result["stdout"])
```

## Security: Command Filtering + Path Restrictions

### Command Filtering

Protect your servers with regex whitelist/blacklist:

```yaml
# Server YAML override
server:
  blacklist:
    - "rm -rf"                 # Block rm -rf on this server
  whitelist:
    - "ls|df|docker|tail"      # Only allow these commands
  command_template: "cd /opt/app && <command>"  # Auto cd then execute
```

- **Blacklist**: Command matching any pattern is rejected.
- **Whitelist**: When configured (non-empty), only commands matching at least one pattern are executed.
- **Command template**: Wraps all commands. Use `<command>` as placeholder.

### Path Restrictions

Restrict upload/download paths:

```yaml
server:
  allowed_local_paths:
    - "/home/bit/scripts"      # Allowed local upload paths
  allowed_remote_paths:
    - "/home/bit/scripts"      # Allowed remote upload/download paths
```

- Empty (default): No restriction.
- When set: Only paths under specified directories are allowed.
- Applies to both `upload` and `download`.

## File Transfer: Upload + Download

### Upload

```bash
# Upload single script
python cli.py my-server upload script.sh

# Upload and run immediately
python cli.py my-server upload script.sh -r

# Custom remote filename
python cli.py my-server upload script.sh -n remote-name.sh

# Upload all alias scripts
python cli.py my-server upload-all
```

### Download

```bash
# Download single file
python cli.py my-server download /remote/file.log ./local/file.log

# Set timeout
python cli.py my-server download /remote/file.log ./local/file.log -t 600

# Recursive directory download
python cli.py my-server download /var/log ./logs

# Download only .log files
python cli.py my-server download /var/log ./logs -p "\\.log$"
```

Downloads obey `allowed_remote_paths` restrictions. Directory downloads preserve remote directory structure locally. Files not matching the `pattern` regex are skipped.

### Sudo File Transfer

```bash
# Sudo upload (via /tmp staging + sudo mv)
python cli.py my-server upload script.sh -s

# Sudo download (via /tmp staging + chown + SFTP)
python cli.py my-server download /root/secret.txt ./secret.txt -s
```

## Path Resolution

### Relative Paths

Relative script paths resolve against the YAML file's directory:

```yaml
# servers/prod.yml
aliases:
  - name: deploy
    script: deploy.sh           # → servers/deploy.sh
  - name: backup
    script: ./scripts/backup.sh # → servers/scripts/backup.sh
```

### Absolute Paths

Absolute path scripts upload to `scripts_dir/external/`:

```yaml
aliases:
  - name: external-tool
    script: /opt/tools/tool.sh  # → /home/user/scripts/external/tool.sh
```

### Windows Paths

Windows paths auto-convert to WSL format (Linux environment):

```
D:\agents\servers\script.sh → /mnt/d/agents/servers/script.sh
```

## Feature Summary

| Category | Feature | Description | Config / API |
|----------|---------|-------------|--------------|
| **Connection** | Direct SSH | Connect to remote server | `server.host`, `server.port` |
| | Password auth | Password login | `server.password` |
| | Key auth | SSH key login (optional passphrase) | `server.key`, `server.key_password` |
| | SOCKS5 proxy | Proxy first, auto-fallback to direct | `config.yaml` proxy / `server.proxy` |
| | Connection pool | Auto-reuse, 60s keepalive | Global `pool.get(name)` |
| **Command Execution** | `run()` | Execute command, optional sudo | `ssh_run` / CLI `run -s` |
| | `run_alias()` | Execute predefined alias | `ssh_alias:server:name` / CLI `alias` |
| | Command template | Wrap all commands (e.g., auto cd) | `server.command_template` |
| | Command filtering | Regex whitelist/blacklist | `server.blacklist` / `server.whitelist` |
| **Script Management** | `upload_script()` | Upload script | CLI `upload` |
| | `run_script()` | Execute uploaded script | `ssh_run_script` / CLI `run-script` |
| | `upload_all_scripts()` | Upload all scripts from alias definitions | CLI `upload-all` |
| | `list_scripts()` | List remote uploaded scripts | CLI `list-scripts` |
| | Upload then run | `upload_script(run_immediately=True)` | `ssh_upload_script` / CLI `upload -r` |
| **File Transfer** | `download()` | Download single file or recursive directory | `ssh_download` / CLI `download` |
| | Download filtering | Regex filter filenames | `pattern` parameter |
| | Download count | Return file count for directory download | `count` field |
| | Download sudo | Read root-owned files (via /tmp staging) | `sudo: true` parameter |
| | Download overwrite control | Skip existing local files | `overwrite` parameter (default: true) |
| | Upload overwrite control | Skip existing remote scripts | `overwrite` parameter (default: true) |
| | Path restrictions | Restrict upload/download paths | `server.allowed_local_paths` / `server.allowed_remote_paths` |
| **Alias System** | Inline alias | Execute command string directly | `aliases[].inline` |
| | Script alias | Upload + execute script file | `aliases[].script` |
| | Sudo alias | Execute with root privileges | `aliases[].sudo: true` |
| | Inheritance | Share aliases via `extends` | `extends: [_shared/common.yml]` |
| **MCP Tools** | Dynamic tools | Each alias auto-exposed as `ssh_alias:server:name` | Auto-generated at runtime |
| | Tool discovery | `ssh_list_servers` / `ssh_list_aliases` | Static tools |
| | Read/write markers | Tools marked readOnly/destructive | Auto-set |
| **Security** | Command blacklist | Regex pattern blocks commands | `server.blacklist` |
| | Command whitelist | Only allow matching commands | `server.whitelist` |
| | Path restrictions | Restrict upload/download paths | `server.allowed_local_paths` / `server.allowed_remote_paths` |
| | Proxy error log | Proxy failures logged to file | `proxy_error.log` |

## Example Scripts

### Deployment Script

```bash
#!/usr/bin/env bash
set -eo pipefail
cd /opt/my-app
git pull origin main 2>&1 | tail -5
npm install && npm run build
systemctl restart my-app
echo "Deployment complete"
```

### Health Check Script

```bash
#!/usr/bin/env bash
echo "--- Service Status ---"
systemctl status my-app --no-pager | head -10
echo "--- Disk Usage ---"
df -h /
echo "--- Memory ---"
free -h
```

## Core Features

- **Connection pool**: SSH connection reuse, 60s keepalive
- **SOCKS5 proxy**: Global or per-server proxy config, auto-fallback to direct on failure
- **Sudo support**: Execute as root via `sudo_password` — `run()` API with `sudo=True`, CLI `run -s`, alias `sudo: true`
- **Dynamic MCP tools**: Aliases auto-exposed as one-click MCP tools for AI Agents
- **Script management**: Upload, store, and execute remote scripts
- **Security filtering**: Regex command filtering + path restrictions
- **File transfer**: SFTP upload and download with path restrictions
- **Zero hardcoded paths**: All paths dynamically resolved via `Path(__file__).parent`
- **External config directory**: Server configs separated from skill directory, flexible location via `config.yaml`
