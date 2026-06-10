# AI Installation Guide

> This document is written for AI agents. Follow the steps in order to complete installation.

## 1. Clone the Repository

Clone directly into your agent's skills directory:

**Claude Code (project-level):**
```bash
git clone https://github.com/leehom0123/ssh-alias-mcp.git .claude/skills/ssh-manager
```

**Claude Code (global):**
```bash
git clone https://github.com/leehom0123/ssh-alias-mcp.git ~/.claude/skills/ssh-manager
```

**Codex CLI:**
```bash
git clone https://github.com/leehom0123/ssh-alias-mcp.git ~/.codex/skills/ssh-manager
```

Requires: Python ≥ 3.9

## 2. Install Dependencies

```bash
pip install -r <skills-dir>/ssh-manager/requirements.txt
```

Only three dependencies: `paramiko`, `pyyaml`, `pysocks`. Nothing else.

## 3. Register the MCP Server

Add the following entry to your agent's MCP configuration:

**Claude Code** (`.claude/settings.local.json`):
```json
{
  "mcpServers": {
    "server-management": {
      "command": "python",
      "args": ["<skills-dir>/ssh-manager/mcp_server.py"]
    }
  }
}
```

**Codex CLI / OpenCode** (`mcp.json`):
```json
{
  "mcpServers": {
    "server-management": {
      "command": "python",
      "args": ["<skills-dir>/ssh-manager/mcp_server.py"]
    }
  }
}
```

Replace `<skills-dir>` with the actual path. For Claude Code global, it's `~/.claude/skills`. For Codex, `~/.codex/skills`.

## 4. Verify

After restarting your AI agent, confirm the MCP tools are loaded:

```bash
# Quick CLI check (optional but recommended)
python <skills-dir>/ssh-manager/cli.py list-servers
```

If `servers/` already contains `.yml` configs, the CLI will list them. An empty list is fine too.

The following MCP tools should appear in your agent session:
- `ssh_list_servers`
- `ssh_run`
- `ssh_run_sudo`
- `ssh_upload_script`
- `ssh_run_script`
- `ssh_run_alias`
- `ssh_list_aliases`
- `ssh_alias:{server}:{name}` (one per alias)

## 5. Add Servers

Create `.yml` files under `servers/`. Minimal config:

```yaml
server:
  host: "your-server-ip"
  user: "your-username"
  password: "your-password"
```

Full configuration reference: [DOCS.md](DOCS.md)


Done. Your AI agent can now manage this server via MCP. Defining a few aliases makes the experience much better — see the README for examples.
