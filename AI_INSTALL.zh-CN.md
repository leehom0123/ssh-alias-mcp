# AI 安装说明

> 写给 AI Agent 看的安装指南。你只需要按顺序执行以下步骤即可完成安装。

## 1. 克隆仓库到 skills 目录

直接 clone 到 agent 的 skills 目录下：

**Claude Code（项目级）：**
```bash
git clone https://github.com/leehom0123/ssh-alias-mcp.git .claude/skills/ssh-manager
```

**Claude Code（全局）：**
```bash
git clone https://github.com/leehom0123/ssh-alias-mcp.git ~/.claude/skills/ssh-manager
```

**Codex CLI：**
```bash
git clone https://github.com/leehom0123/ssh-alias-mcp.git ~/.codex/skills/ssh-manager
```

要求：Python ≥ 3.9

## 2. 安装依赖

```bash
pip install -r <skills-dir>/ssh-manager/requirements.txt
```

依赖只有三个：`paramiko`、`pyyaml`、`pysocks`。没有其他要求。

## 3. 注册 MCP 服务器

在 agent 的 MCP 配置文件中添加以下条目：

**Claude Code**（`.claude/settings.local.json`）：
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

**Codex CLI / OpenCode**（`mcp.json`）：
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

把 `<skills-dir>` 替换为实际路径。Claude Code 全局安装，路径是 `~/.claude/skills`；Codex 是 `~/.codex/skills`。

## 4. 验证

重启你的 AI Agent 后，确认 MCP 工具已加载：

```bash
# 用 CLI 验证配置是否正常（非必需，但建议跑一下）
python <skills-dir>/ssh-manager/cli.py list-servers
```

如果 `servers/` 目录已有 `.yml` 配置，CLI 会列出服务器信息。没有服务器配置文件也会正常运行，只是返回空列表。

Agent 会话中应该能看到以下 MCP 工具：
- `ssh_list_servers`
- `ssh_run`
- `ssh_run_sudo`
- `ssh_upload_script`
- `ssh_run_script`
- `ssh_run_alias`
- `ssh_list_aliases`
- `ssh_alias:{server}:{name}`（每个 alias 一个）

## 5. 添加服务器

在 `servers/` 目录下创建 `.yml` 文件。最小配置：

```yaml
server:
  host: "your-server-ip"
  user: "your-username"
  password: "your-password"
```

完整配置参考：[DOCS.zh-CN.md](DOCS.zh-CN.md)


搞定了。现在你的 AI Agent 可以通过 MCP 直接管理这台服务器。定义几个 alias 会让体验更好——参考 README 里的示例。
