# 🚀 ssh-alias-mcp — Let Your AI Agent Manage Linux Servers

**GitHub: https://github.com/leehom0123/ssh-alias-mcp**

[中文](README.zh-CN.md)

📖 **[Full Technical Docs (DOCS.md)](DOCS.md)** — architecture, all config fields, MCP tools reference, script examples


## Agent Setup in One Sentence

Read https://github.com/leehom0123/ssh-alias-mcp/blob/main/AI_INSTALL.md — set up the SKILL and install the MCP service as described.

## What Is This?

A ~2,000-line Python tool that lets AI agents (Claude Code, Codex CLI, Cursor, Windsurf) **SSH into your Linux servers, run commands, deploy code, tail logs, restart services**. It also doubles as a nice CLI for day-to-day manual use.

**One set of YAML config, one connection pool, works for both AI and humans.**


## Why I Built This

I run a handful of VPS instances for different projects. Daily ops are predictable: check if logs have errors, deploy new code, restart a container, see if disk is full again.

Every time: open terminal → SSH in → type commands → exit. Not painful, but definitely not fun.

When Claude Code added MCP (Model Context Protocol) support, letting AI call external tools, I thought: *what if I just define my routine server tasks as skills and let AI handle them?*

So I spent a day building this with paramiko. It worked way better than expected. Now I tell my AI agent "restart all three web servers," and it just does it.

Open-sourcing in case anyone else managing a few servers finds it useful.


## Get Started

📥 **Installation: read [AI_INSTALL.md](AI_INSTALL.md) — set up MCP and add `SKILL.md` as described.** (You can also just paste the GitHub URL to your AI agent and let it install itself.)

| Tool | Description |
|------|-------------|
| `ssh_list_servers` | List all configured servers |
| `ssh_run` | Execute a command on a remote server |
| `ssh_run_sudo` | Execute as root (requires `sudo_password`) |
| `ssh_upload_script` | Upload a script, optionally run immediately |
| `ssh_run_script` | Run an uploaded script |
| `ssh_run_alias` | Run an alias-defined command |
| `ssh_alias:{server}:{name}` | **One-click alias, one MCP tool per alias** |

> ⚠️ **Don't inline `sudo -S`** — use `ssh_run_sudo`. For docker permission issues, set `sudo: true` in the alias.

### ⌨️ CLI Mode

```bash
python cli.py list-servers                 # See all servers
python cli.py my-server run "uptime"       # Run a command
python cli.py my-server sudo "apt update"  # Run as root
python cli.py my-server alias healthcheck  # Run an alias
python cli.py my-server upload script.sh -r   # Upload & run immediately
```


## 🎯 Key Highlights

### 1. YAML Config Reuse + `extends` Inheritance

One server = one YAML file. Structure it like this:

```
servers/
├── _shared/common.yml        # Shared aliases, inherited by all servers
├── prod-web-01.yml           # Production server
├── prod-web-02.yml           # Another production server
└── staging.yml               # Staging
```

Define common checks and commands once in `_shared/common.yml` — every server `extends` it:

```yaml
# _shared/common.yml
aliases:
  - name: healthcheck
    inline: "df -h / && free -h && uptime"
    desc: "One-click health check"
  - name: docker-ps
    inline: "docker ps --format 'table {{.Names}}\t{{.Status}}'"
    desc: "Running containers"
  - name: logs-nginx
    inline: "tail -50 /var/log/nginx/error.log"
    desc: "Nginx error logs"
```

```yaml
# prod-web-01.yml
extends:
  - _shared/common.yml         # Inherit shared aliases

server:
  host: "198.51.100.10"
  user: "deploy"
  password: "xxx"
  sudo_password: "xxx"
  system: "Ubuntu 22.04 LTS"

aliases:
  - name: deploy
    script: deploy.sh
    desc: "Deploy main site"
    timeout: 600
    sudo: true

  - name: restart
    inline: "systemctl restart my-app && echo 'restarted'"
    desc: "Restart app"
    sudo: true
```

5, 10, or 20 servers — same effortless maintenance. **Same aliases work for AI and CLI.** You never write anything twice.

### 2. Aliases Become MCP Tools Automatically

Define `deploy` in YAML, and your AI agent gets `ssh_alias:prod-web-01:deploy`. **One line of YAML = one AI skill.** To the agent, server operations feel like local function calls.

### 3. One Connection Pool, Shared by AI & Humans

```
AI Agent ──→ MCP Protocol ──→ ssh_client.py ──→ Remote Server
Terminal ──→ CLI ──────────────→ ssh_client.py ──→ Remote Server
```

Same SSH connection, same connection pool, same config. AI deploys something, then you verify with `python cli.py` — same logic underneath. No duplicate tools, no duplicate config.

### 4. Connection Pool + Proxy + Sudo

- **Connection pool**: SSH connections auto-reused with 60s keepalive — no repeated handshakes
- **SOCKS5 proxy**: Global or per-server, auto falls back to direct connect if proxy is down
- **Sudo**: configure `sudo_password` once, run root commands without interactive prompts


## Who Is This For?

- 🧑‍💻 You manage multiple VPS instances and don't want to SSH into each one manually
- 👥 Small teams with no dedicated DevOps — let AI help with daily checks
- 🤖 You want your AI agent to actually *do things*, not just chat

## Tech Stack

Pure Python. Dependencies: `paramiko` + `pyyaml` + `pysocks`. Under 2,000 lines. Easy to read and hack.

```bash
pip install -r requirements.txt
```

## License & Feedback

MIT licensed. Use it however you want. A ⭐ means a lot to me.

Issues and PRs welcome — Chinese or English, both fine.

**GitHub: https://github.com/leehom0123/ssh-alias-mcp**


📖 **[Full Technical Docs (DOCS.md)](DOCS.md)**
