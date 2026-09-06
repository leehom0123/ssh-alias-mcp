# REFERENCE — Server Configuration, Aliases, Security & File Transfer

## Architecture

```
┌──────────────────────────────────────────────────────┐
│  AI Agent (Claude Code / Codex / OpenCode / ...)      │
│  └── MCP tool calls (ssh_run, ssh_alias.*)         │
└──────────────┬───────────────────────────────────────┘
               │ JSON-RPC over stdio
┌──────────────▼───────────────────────────────────────┐
│  mcp_server.py  (MCP stdio server)                   │
│  └── Dynamically exposes aliases as MCP tools        │
└──────────────┬───────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────┐
│  ssh_client.py  (Shared core)                        │
│  ├── ConnectionPool (connection reuse, keepalive)    │
│  ├── Host key verification (known_hosts, tofu/strict)│
│  ├── SOCKS5 proxy + direct auto-fallback             │
│  └── SFTP script upload & download                   │
└──────────────┬───────────────────────────────────────┘
               │ SSH
┌──────────────▼───────────────────────────────────────┐
│  Remote Server (Linux / Windows)                     │
└──────────────────────────────────────────────────────┘
```

## Directory Structure

```
├── <skills-dir>/
│   └── ssh-alias-mcp/         # Core code
│       ├── SKILL.md           # AI Agent skill definition
│       ├── REFERENCE.md       # Detailed reference (this file)
│       ├── CLI_USAGE.md       # CLI commands reference
│       ├── INSTALL.md         # AI Agent installation guide
│       ├── cli.py             # CLI entry point
│       ├── mcp_server.py      # MCP stdio server
│       ├── ssh_client.py      # Core module (SSH connection, proxy, pool)
│       ├── config.yaml        # Global config (servers_dir, proxy, timeout)
│       └── requirements.txt   # Python dependencies
│
└── servers/                   # Server config directory (path in config.yaml)
    ├── my-server.yml          # Server connection info + alias definitions
    ├── _shared/common.yml     # Shared aliases (inherited via extends)
    └── my-server/             # Local shell scripts for this server (.sh)
```

> Server configs `servers/` are separated from the skill directory for easy management and credential isolation. Path configured via `servers_dir` in `config.yaml` (absolute or relative path supported).

## Global Configuration (`config.yaml`)

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
  auto_reconnect: true        # Automatically reconnect on lost connection
  reconnect_interval: 5       # Interval between reconnection attempts (seconds)
  keepalive_interval: 15      # SSH keepalive interval (seconds, 0 = disable)
  host_key_checking: tofu     # Host key verification: tofu / strict / insecure

security:
  blacklist: []               # Regex patterns to block commands (searched anywhere in the command)
  whitelist: []               # Regex patterns; the WHOLE command must match one (empty = no restriction)
  command_template: ""        # Command template to wrap all commands
```

The global `security` section applies to every server; a per-server
`blacklist`/`whitelist`/`command_template` (when present) overrides it.

`host_key_checking` controls SSH host key verification (server key is verified
**before** any password/passphrase is sent):

| Mode | Behaviour |
|------|-----------|
| `tofu` (default) | Trust on first use; the key is persisted to `<skill-dir>/known_hosts`. A later key change aborts the connection (possible MITM). |
| `strict` | Connect only if the key already exists in known_hosts (system + local). Unknown or changed keys abort the connection. |
| `insecure` | No verification (not recommended) |

To re-enroll a server whose host key legitimately changed, delete its entry
from `<skill-dir>/known_hosts`.

Proxy is tried first, auto-fallback to direct connection on failure.

`auto_reconnect` enables background reconnects after a failed request.
`reconnect_interval` is used while there have been fewer than five consecutive
connection failures. After the fifth failure, retries slow to once every 60
seconds. A new CLI or MCP request always triggers an immediate attempt, even
during that cooldown. Set `auto_reconnect: false` to disable background attempts.

## Server Configuration (`{servers_dir}/{name}.yml`)

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
  host_key_checking: "tofu"      # Override global: tofu / strict / insecure

  # Server-level proxy override (optional, overrides global config.yaml)
  proxy:
    host: "127.0.0.1"
    port: 10808

  # Security: command filtering (when present, overrides global config.yaml)
  blacklist:
    - "rm -rf|mkfs|dd "          # Regex pattern, match anywhere = block
  whitelist:
    - "(ls|df|docker|tail).*"    # Regex pattern; the WHOLE command must match one
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

## Shared Alias Inheritance (`extends`)

### Three-Level Inheritance Chain

```
config.yaml (global defaults: proxy, timeout)
    ↓ extends
_shared/*.yml (shared connection info, shared aliases)
    ↓ extends
Server YAML (your server — override + append)
```

### Multiple Inheritance

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

### Merge Rules

| Field | Merge Behavior |
|-------|----------------|
| `server` | Shallow merge — local overrides base |
| `aliases` | All inherited from base. Same `name` overrides, different names append |
| `security` (whitelist/blacklist/command_template) | Base sets defaults, local inherits. Local overrides if present |
| `proxy` | Same as security — local falls back to base |
| `allowed_local_paths` / `allowed_remote_paths` | Same as security — local falls back to base |

## Shell Type Support

### Three Shell Types

| Shell | Use Case | Command Template |
|-------|---------|-----------------|
| `bash` | Linux/macOS servers | Full bash command set |
| `cmd` | Windows servers (CMD) | `cmd /c "..."` wrapper |
| `powershell` | Windows servers (PowerShell) | Native PowerShell syntax |

### Shell Command Template Comparison

All placeholder values (`{path}`, `{src}`, `{dst}`, `{tmp}`, `{target}`, `{user}`) are
shell-quoted centrally for the target shell (POSIX quoting for `bash`, double-quote for
`cmd`, single-quote doubling for `powershell`), which blocks command injection through
paths and script names. Values containing control characters (or shell metacharacters on
`cmd`/`powershell`) are rejected.

| Template | bash | cmd | powershell |
|----------|------|-----|------------|
| `mkdir` | `mkdir -p {path}` | `mkdir {path} 2>nul` | `New-Item -ItemType Directory -Path {path} -Force` |
| `file_exists` | `test -f {path}` | `if exist {path} (echo exists) else exit 1` | `if (Test-Path {path}) { exit 0 } else { exit 1 }` |
| `run_script` | `bash {path}` | `call {path}` | `& {path}` |
| `chmod` | `chmod {mode} {path}` | Not supported | Not supported |
| `stat_owner` | `stat -c '%U:%G' {path}` | Not supported | Not supported |
| `stat_mode` | `stat -c '%a' {path}` | Not supported | Not supported |
| `rm_dir` | `rm -rf {path}` | `rmdir /S /Q {path}` | `Remove-Item -Recurse -Force {path}` |
| `cp_r` | `cp -r {src} {dst}` | `xcopy /Y {src} {dst} /E /I` | `Copy-Item -Recurse {src} {dst}` |
| `move` | `mv {tmp} {target}` | `move /y {tmp} {target}` | `Move-Item -Force {tmp} {target}` |
| `chown` | `chown {user} {path}` | Not supported | Not supported |
| `chown_r` | `chown -R {user}:{user} {path}` | Not supported | Not supported |
| `list_dir` | `ls -la {path}` | `dir {path} /Q` | `Get-ChildItem -Path {path} \| Format-List` |
| `install` | Preserves owner/mode | Move only | Move only |
| `tmp_prefix` | `/tmp` | `%TEMP%` | `$env:TEMP` |

`file_exists` signals existence via the command exit code (0 = exists, non-zero = missing).

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

## Security: Command Filtering + Path Restrictions

### Command Filtering

Protect your servers with regex whitelist/blacklist:

```yaml
# Server YAML override (also settable globally in config.yaml under security:)
server:
  blacklist:
    - "rm -rf"                 # Block rm -rf on this server
  whitelist:
    - "(ls|df|docker|tail).*"  # Only allow commands fully matching a pattern
  command_template: "cd /opt/app && <command>"  # Auto cd then execute
```

- **Blacklist**: Command matching any pattern (searched anywhere in the command) is rejected.
- **Whitelist**: When configured (non-empty), the **whole** command must fully match at
  least one pattern (`re.fullmatch`). This prevents bypasses like `ls; rm -rf /` slipping
  through a partially-anchored rule. Write patterns that cover the entire command, e.g.
  `ls.*` or `(ls|df).*` rather than bare `ls|df`.
- **Command template**: Wraps all commands. Use `<command>` as placeholder.
- Filtering applies to agent-initiated commands (`ssh_run`, inline aliases). Internally
  generated helper commands (mkdir/staging/etc.) are exempt but their path arguments are
  shell-quoted and validated against traversal.

> A regex blacklist is a guardrail, not a sandbox: shell-obfuscated commands can evade
> it. For hard restrictions use the whitelist.

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
- When set: Only paths under the specified directories are allowed, compared with a
  directory boundary (`/allowed/dir` permits `/allowed/dir/x` but not `/allowed/dir-evil`).
  Remote paths are normalised lexically (posixpath); local paths are resolved on the local filesystem.
- Applies to both `upload` and `download`.

### Sudo Mechanism

Sudo runs commands via `sudo -S -p '' bash -c '<command>'`. The password is delivered
over the SSH channel **stdin**, never in the remote command line (so it cannot be read
from `ps`). The password must be configured (`sudo_password`, falling back to `password`).

### Concurrency

Commands on the same server execute concurrently over independent SSH channels — a
long-running command (or a reconnecting host) never blocks another thread's command to
the same server. Timeouts close the channel and report `code: -1` with a note that the
remote process may still be running; recursive directory downloads stop at 48 levels.

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

## Feature Summary

| Category | Feature | Description | Config / API |
|----------|---------|-------------|--------------|
| **Connection** | Direct SSH | Connect to remote server | `server.host`, `server.port` |
| | Password auth | Password login | `server.password` |
| | Key auth | SSH key login (optional passphrase) | `server.key`, `server.key_password` |
| | SOCKS5 proxy | Proxy first, auto-fallback to direct | `config.yaml` proxy / `server.proxy` |
| | Host key verification | known_hosts verified before auth (tofu/strict/insecure) | `server.host_key_checking` |
| | Connection pool | Auto-reuse, keepalive (default 15s) | Global `pool.get(name)` |
| | Concurrent commands | Multiple commands per server on independent channels | Built-in (no per-server serialization) |
| **Command Execution** | `run()` | Execute command, optional sudo, real-time streaming | `ssh_run` / CLI `run -s` |
| | `run_alias()` | Execute predefined alias (streams output) | `ssh_alias.server.name` / CLI `alias` |
| | Command template | Wrap all commands (e.g., auto cd) | `server.command_template` |
| | Command filtering | Blacklist search + whitelist fullmatch | `server.blacklist` / `server.whitelist` |
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
| **MCP Tools** | Dynamic tools | Each alias auto-exposed as `ssh_alias.server.name` | Auto-generated at runtime |
| | Tool discovery | `ssh_list_servers` / `ssh_list_aliases` | Static tools |
| | Read/write markers | Tools marked readOnly/destructive | Auto-set |
| **Security** | Command blacklist | Regex pattern blocks commands (search) | `server.blacklist` |
| | Command whitelist | Only allow fully-matching commands (fullmatch) | `server.whitelist` |
| | Argument shell-quoting | Template paths quoted per shell; traversal rejected | Built-in (`_cmd` / `_safe_relpath`) |
| | Sudo stdin password | Password never appears in remote argv | Built-in (`sudo -S`) |
| | Path restrictions | Restrict upload/download paths (directory-boundary match) | `server.allowed_local_paths` / `server.allowed_remote_paths` |
| | Proxy error log | Proxy failures logged to file | `proxy_error.log` |
