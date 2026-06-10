# Server Management MCP

Remote Linux server management via SSH — designed as an **MCP server for AI agents** (Claude Code, Codex CLI, OpenCode, Cursor, Windsurf, and any MCP-compatible client), with a **CLI for manual human use**. All modes share the same connection pool and proxy logic.

## Quick Start

### For AI Agents (MCP Mode)

Register as an MCP server. The exact command varies by client:

```bash
# Claude Code
claude mcp add server-management python <path-to-this-dir>/mcp_server.py

# Codex CLI / OpenCode — add to your mcp.json or settings
# {
#   "mcpServers": {
#     "server-management": {
#       "command": "python",
#       "args": ["<path-to-this-dir>/mcp_server.py"]
#     }
#   }
# }
```

Once registered, the agent can:
- Execute commands on any configured server (`ssh_run`, `ssh_run_sudo`)
- Upload and run scripts (`ssh_upload_script`, `ssh_run_script`)
- Invoke pre-defined aliases with a single tool call (`ssh_alias:{server}:{name}`)
- List servers and aliases for context discovery

### For Humans (CLI Mode)

```bash
cd <path-to-this-dir>
python cli.py list-servers
python cli.py my-server run "uptime"
python cli.py my-server alias healthcheck
python cli.py my-server upload /path/to/script.sh -r   # upload & run
```

## Directory Structure

```
├── <skills-dir>/
│   └── ssh-manager/           # Skill + core code
│       ├── SKILL.md           # AI agent skill definition
│       ├── cli.py             # CLI entry point
│       ├── mcp_server.py      # MCP stdio server
│       ├── ssh_client.py      # Core module (SSH connection, proxy, pool)
│       ├── config.yaml        # Global config (servers_dir, proxy, timeout)
│       ├── README.md          # English readme (this file)
│       └── README.zh-CN.md    # Chinese readme
│
└── servers/                   # Server configs (placed outside skills, configured in config.yaml)
    ├── my-server.yml          # Connection info + alias definitions
    ├── _shared/common.yml     # Shared aliases (inherited via `extends`)
    └── my-server/             # Local shell scripts (.sh) for this server
```

> **Note**: The `servers/` directory lives outside `ssh-manager/` for easier management and to keep credentials separate from the skill. Its path is configured in `config.yaml` via `servers_dir` (absolute or relative path).

## Architecture

```
┌──────────────────────────────────────────────────────┐
│  AI Agent (Claude Code / Codex / OpenCode / ...)    │
│  └── MCP tool calls (ssh_run, ssh_alias:...)        │
└──────────────┬───────────────────────────────────────┘
               │ JSON-RPC over stdio
┌──────────────▼───────────────────────────────────────┐
│  mcp_server.py  (MCP stdio server)                  │
│  └── dynamically exposes aliases as MCP tools       │
└──────────────┬───────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────┐
│  ssh_client.py  (shared core)                        │
│  ├── ConnectionPool (auto-reuse, 60s keepalive)      │
│  ├── SOCKS5 proxy with auto-fallback                 │
│  └── SFTP script upload & execution                  │
└──────────────┬───────────────────────────────────────┘
               │ SSH
┌──────────────▼───────────────────────────────────────┐
│  Remote Linux Servers                                │
└──────────────────────────────────────────────────────┘
```

## Configuration

### Global Config (`config.yaml`)

```yaml
# Path to servers/ directory (absolute or relative to this file)
servers_dir: "../servers"

proxy:
  enabled: false              # Enable SOCKS5 proxy globally
  host: "127.0.0.1"           # Proxy address
  port: 1080                  # Proxy port

server:
  timeout: 30                 # Default SSH connection timeout (seconds)
```

Proxy is tried first with automatic fallback to direct connection.

### YAML Inheritance (`extends`)

Share aliases across multiple servers without duplication:

```yaml
# _shared/common.yml
aliases:
  - name: healthcheck
    inline: "df -h / && free -h"
    desc: "Health check"
  - name: disk-usage
    inline: "df -h"
    desc: "Disk usage"
```

```yaml
# my-server.yml
extends:
  - _shared/common.yml    # Inherit shared aliases

server:
  host: "..."
  ...

aliases:
  - name: deploy-backend
    script: deploy-backend.sh
    desc: "Deploy backend"
```

- `extends` points to other `.yml` files in the same `servers_dir`
- `aliases` from extended files are merged into the server's `aliases`
- Local aliases take priority (overwrite inherited aliases with the same name)
- Supports multiple inheritance targets

### Server YAML Configuration (`{servers_dir}/{name}.yml`)

Create a `.yml` file in the `servers/` directory for each server:

```yaml
# my-server.yml

server:
  # === Required fields ===
  host: "your.host.com"         # Server IP or domain name
  user: "username"               # SSH login username

  # === Display info ===
  name: "My Server"             # Display name
  desc: "Application server"    # Description

  # Authentication: choose one of the following
  password: "your-password"      # Password authentication
  # key: "/path/to/private_key"     # OR key-based authentication
  # key_password: "passphrase"      # Passphrase for the private key (optional)
  sudo_password: "sudo-pass"     # Sudo password for root commands (optional, fallback to password)

  # === Optional fields ===
  port: 22                       # SSH port (default: 22)
  timeout: 30                    # SSH connection timeout (seconds, default: 30)
  scripts_dir: "/home/user/scripts"  # Remote directory for uploaded scripts
  system: "Ubuntu 24.04 LTS"    # OS info (for documentation & command selection)

  # Per-server proxy override (optional, overrides global config.yaml)
  proxy:
    host: "127.0.0.1"            # Proxy host
    port: 10808                  # Proxy port
    type: socks5                 # Proxy type (currently only socks5)

# Quick command aliases — auto-exposed as MCP tools
aliases:
  # script type: uploads .sh file first, then executes
  - name: deploy
    script: app-deploy.sh        # Script path (relative to CWD)
    desc: "Deploy app"           # Description (for display)
    timeout: 600                 # Command timeout in seconds (default: 300)

  # inline type: execute command directly on remote server
  - name: logs
    inline: "docker logs --tail 100 my-app"
    desc: "View logs"
    timeout: 10

  # script type with sudo
  - name: restart-service
    script: restart-service.sh
    desc: "Restart service"
    sudo: true                   # Run with sudo (uses server.sudo_password, fallback to password)
```

## Usage Modes

### Mode 1: AI Agent via MCP (Primary)

Register once, then the agent auto-discovers all servers and aliases as tools.

Servers are discovered by globbing `{servers_dir}/*.yml` — the agent sees each server's host, user, OS, aliases, etc. directly from the parsed YAML config.

**Available MCP tools:**

| Tool | Description |
|------|-------------|
| `ssh_list_servers` | List all configured servers |
| `ssh_run` | Execute a command on a remote server |
| `ssh_run_sudo` | Execute a command as root (requires `sudo_password` in server config) |
| `ssh_upload_script` | Upload a local script, optionally run immediately |
| `ssh_run_script` | Run an already-uploaded script |
| `ssh_list_scripts` | List scripts on the remote server |
| `ssh_upload_all_scripts` | Upload all scripts from alias definitions |
| `ssh_run_alias` | Run an alias-defined command |
| `ssh_list_aliases` | List aliases for a server |
| `ssh_alias:{server}:{name}` | **Dynamic one-click alias** (auto-generated per alias) |

**Key design for AI agents:**
- Aliases are dynamically exposed as individual MCP tools (e.g. `ssh_alias:my-server:deploy`)
- No hardcoded paths — all paths resolve relative to `__file__`

### Mode 2: CLI (Manual Human Use)

```bash
cd <path-to-this-dir>

# List all servers
python cli.py list-servers

# Run a command
python cli.py <server> run "<command>"

# Run as root (requires sudo_password in server config)
python cli.py <server> sudo "<command>"

# Run an alias
python cli.py <server> alias <name>

# Upload and run a script immediately
python cli.py <server> upload /path/to/script.sh -r/--run

# Upload all local scripts
python cli.py <server> upload-all

# List scripts / aliases
python cli.py <server> list-scripts
python cli.py <server> list-aliases
```

All commands support `-t` / `--timeout` (seconds, default 300).

### Mode 3: Python Module

```python
import sys
sys.path.insert(0, "<path-to-this-dir>")
from ssh_client import pool

conn = pool.get("my-server")
result = conn.run("ls -la /opt")
print(result["stdout"])
```

## Script Examples

### Deploy Script

```bash
#!/usr/bin/env bash
set -eo pipefail
cd /opt/my-app
git pull origin main 2>&1 | tail -5
npm install && npm run build
systemctl restart my-app
echo "Deploy complete"
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

## Key Features

- **Connection pooling**: SSH connections are reused with 60s keepalive
- **SOCKS5 proxy**: Per-server or global proxy with automatic direct fallback
- **Sudo support**: Execute commands as root via `sudo_password` in server config — `run_sudo()` API, `sudo` CLI, `sudo: true` in aliases
- **Dynamic MCP tools**: Aliases auto-exposed as one-click MCP tools for AI agents
- **Script management**: Upload, store, and execute scripts on remote servers
- **No hardcoded paths**: All paths resolve dynamically via `Path(__file__).parent`
- **External servers dir**: Server configs live outside the skill directory, configurable via `config.yaml`
