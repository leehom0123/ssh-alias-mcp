# ssh-alias-mcp

[English](README.md) | [中文](README.zh-CN.md) | [Docs](DOCS.md)

AI-driven server operations tool. Configure servers in YAML, execute commands, deploy scripts, and manage files — all through a unified interface for AI Agents and CLI.

## ✨ Highlights

- **One config, three shells** — `bash`, `cmd`, `powershell` auto-adapted via command templates
- **Alias system** — One YAML line = one AI skill, auto-exposed as MCP tools
- **MCP + CLI** — Same config, same connection pool, shared by AI and humans

## Quick Start

```bash
# AI Agent (MCP)
claude mcp add ssh-alias-mcp python <path>/mcp_server.py

# CLI
python cli.py my-server run "uptime"
python cli.py my-server alias deploy
```

## Agent Setup in One Sentence (Copy It To Your Agents)

Read https://github.com/leehom0123/ssh-alias-mcp/blob/main/AI_INSTALL.md — set up the SKILL and install the MCP service as described.

## Real-World Scenarios

### Scenario 1: Deploy to 5 servers in parallel

```yaml
# _shared/common.yml
aliases:
  - name: deploy
    script: deploy.sh
    sudo: true
```

**AI workflow:** You say "deploy to all prod servers" → AI reads server list → calls `ssh_run` or `ssh_alias` on each server in parallel → reports result. No SSH boilerplate, no password prompts.

### Scenario 2: AI analyzes crash cause

```yaml
aliases:
  - name: crash-check
    inline: "journalctl -xe --since '1 hour ago' && dmesg -T | tail -100 && free -h && df -h /"
```

**Before:** SSH into server → manually check logs → search for kernel panic → analyze core dump → hours later
**After:** Tell AI "server crashed, check why" → AI calls `ssh_alias:prod-01:crash-check` → analyzes logs → identifies OOM killer → suggests fix

### Scenario 3: Emergency troubleshooting

```yaml
aliases:
  - name: check
    inline: "docker logs --tail 50 my-app && df -h / && free -h"
```

**Before:** Open terminal → SSH → type commands → copy output → analyze
**AI workflow:** You say "my app is slow, check it" → AI uses `ssh_run` or `ssh_alias` to grab logs and metrics → identifies bottleneck → suggests fix

### Scenario 4: Cross-platform deployment

```yaml
# Linux server
server:
  host: "192.168.1.100"
  shell: bash

# Windows server
server:
  host: "10.0.0.50"
  shell: powershell
```

**AI workflow:** You say "deploy to both Linux and Windows servers" → AI reads server configs → uses `ssh_run` or `ssh_alias` on each → tool auto-adapts bash/powershell commands → reports unified result

## Installation

See [AI_INSTALL.md](AI_INSTALL.md) for setup instructions.

- [Full Documentation](DOCS.md)
- [GitHub](https://github.com/leohom0123/ssh-alias-mcp)
