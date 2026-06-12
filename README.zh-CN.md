# ssh-alias-mcp

[English](README.md) | [中文](README.zh-CN.md) | [文档](DOCS.zh-CN.md)

AI 驱动的服务器运维工具。YAML 配置服务器，执行命令、部署脚本、管理文件 — AI Agent 和命令行统一接口。

## ✨ 亮点

- **一套配置，三种 Shell** — `bash`、`cmd`、`powershell` 通过命令模板自动适配
- **Alias 系统** — 一行 YAML = 一个 AI 技能，自动暴露为 MCP 工具
- **MCP + CLI** — 同一套配置、同一个连接池，AI 和人工共用

## 快速开始

```bash
# AI Agent (MCP)
claude mcp add ssh-alias-mcp python <路径>/mcp_server.py

# CLI
python cli.py my-server run "uptime"
python cli.py my-server alias deploy
```

## 一句话agent安装（把下面一句话复制给你的Agent）

安装请阅读 https://github.com/leehom0123/ssh-alias-mcp/blob/main/AI_INSTALL.md ，按说明配置好SKILL及安装MCP服务。

## 使用场景

### 场景 1：并行部署 5 台服务器

```yaml
# _shared/common.yml
aliases:
  - name: deploy
    script: deploy.sh
    sudo: true
```

**AI 工作流：** 你说"部署到所有生产服务器" → AI 读取服务器列表 → 对每台服务器调用 `ssh_run` 或 `ssh_alias` 并行部署 → 汇报结果。无需 SSH 连接信息，无需密码交互。

### 场景 2：AI 分析死机原因

```yaml
aliases:
  - name: crash-check
    inline: "journalctl -xe --since '1 hour ago' && dmesg -T | tail -100 && free -h && df -h /"
```

**之前：** SSH 进服务器 → 手动查日志 → 搜索内核 panic → 分析 core dump → 几个小时
**AI 工作流：** 你说"服务器崩了，查原因" → AI 用 `ssh_run` 或 `ssh_alias` 抓取日志 → 分析崩溃原因 → 定位 OOM killer → 给出修复建议

### 场景 3：紧急故障排查

```yaml
aliases:
  - name: check
    inline: "docker logs --tail 50 my-app && df -h / && free -h"
```

**之前：** 打开终端 → SSH → 敲命令 → 复制输出 → 分析
**AI 工作流：** 你说"程序崩了，帮我看看" → AI 用 `ssh_run` 或 `ssh_alias` 获取日志和指标 → 分析崩溃原因 → 给出修复建议

### 场景 4：跨平台部署

```yaml
# Linux 服务器
server:
  host: "192.168.1.100"
  shell: bash

# Windows 服务器
server:
  host: "10.0.0.50"
  shell: powershell
```

**AI 工作流：** 你说"部署到 Linux 和 Windows 服务器" → AI 读取服务器配置 → 对每台服务器使用 `ssh_run` 或 `ssh_alias` → 工具自动适配 bash/powershell 命令 → 统一汇报结果

## 安装

详见 [AI_INSTALL.zh-CN.md](AI_INSTALL.zh-CN.md)。

## 链接

- [完整文档](DOCS.zh-CN.md)
- [GitHub](https://github.com/leohom0123/ssh-alias-mcp)
