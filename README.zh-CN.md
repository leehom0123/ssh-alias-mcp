# Server Management MCP

基于 SSH 的远程 Linux 服务器管理工具 — 既是 **MCP 服务器**（供 Claude Code、Codex CLI、OpenCode、Cursor 等 AI Agent 调用），也是**命令行工具**（人工使用）。所有模式共享同一套连接池和代理逻辑。

## 快速开始

### AI Agent 使用（MCP 模式）

注册为 MCP 服务器，不同客户端注册方式略有差异：

```bash
# Claude Code
claude mcp add server-management python <本目录路径>/mcp_server.py

# Codex CLI / OpenCode — 在 mcp.json 或 settings 中添加：
# {
#   "mcpServers": {
#     "server-management": {
#       "command": "python",
#       "args": ["<本目录路径>/mcp_server.py"]
#     }
#   }
# }
```

注册后，AI Agent 可自动：
- 在任意已配置服务器上执行命令（`ssh_run`、`ssh_run_sudo`）
- 上传并执行脚本（`ssh_upload_script`、`ssh_run_script`）
- 一键调用预定义快捷命令（`ssh_alias:{server}:{name}`）
- 列出服务器和别名发现上下文

### 命令行使用（人工）

```bash
cd <本目录路径>
python cli.py list-servers
python cli.py my-server run "uptime"
python cli.py my-server alias healthcheck
python cli.py my-server upload /path/to/script.sh -r   # 上传并立即执行
```

## 目录结构

```
├── <skills-dir>/
│   └── ssh-manager/           # 核心代码
│       ├── SKILL.md           # AI Agent skill 定义文件
│       ├── cli.py             # 命令行入口
│       ├── mcp_server.py      # MCP stdio 服务器
│       ├── ssh_client.py      # 核心模块（SSH 连接、代理、连接池）
│       ├── config.yaml        # 全局配置（servers_dir、代理、超时）
│       ├── README.md          # 英文说明
│       └── README.zh-CN.md    # 中文说明（本文件）
│
└── servers/                   # 服务器配置目录（位于 skill 外部，路径在 config.yaml 中配置）
    ├── my-server.yml          # 服务器连接信息 + alias 定义
    ├── _shared/common.yml     # 共享 alias（通过 extends 继承）
    └── my-server/             # 该服务器的本地 shell 脚本 (.sh)
```

> 服务器配置 `servers/` 与 skill 目录分离，便于管理且凭证不入库。路径通过 `config.yaml` 的 `servers_dir` 配置（支持绝对路径或相对路径）。

## 架构

```
┌──────────────────────────────────────────────────────┐
│  AI Agent（Claude Code / Codex / OpenCode / ...）   │
│  └── MCP 工具调用（ssh_run, ssh_alias:...）         │
└──────────────┬───────────────────────────────────────┘
               │ JSON-RPC over stdio
┌──────────────▼───────────────────────────────────────┐
│  mcp_server.py  （MCP stdio 服务器）                │
│  └── 动态将 alias 暴露为 MCP 工具                   │
└──────────────┬───────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────┐
│  ssh_client.py  （共享核心）                         │
│  ├── ConnectionPool（连接复用，60s 保活）            │
│  ├── SOCKS5 代理 + 直连自动回退                     │
│  └── SFTP 脚本上传与执行                             │
└──────────────┬───────────────────────────────────────┘
               │ SSH
┌──────────────▼───────────────────────────────────────┐
│  远程 Linux 服务器                                   │
└──────────────────────────────────────────────────────┘
```

## 配置

### 全局配置（`config.yaml`）

```yaml
# servers/ 目录路径（绝对路径或相对于本文件的路径）
servers_dir: "../servers"

proxy:
  enabled: false              # 全局启用 SOCKS5 代理
  host: "127.0.0.1"           # 代理地址
  port: 1080                  # 代理端口

server:
  timeout: 30                 # 默认 SSH 连接超时（秒）
```

代理优先尝试，失败后自动回退直连。

### 共享别名继承（`extends`）

多个服务器共用同一组 alias，无需重复定义：

```yaml
# _shared/common.yml
aliases:
  - name: healthcheck
    inline: "df -h / && free -h"
    desc: "健康检查"
  - name: disk-usage
    inline: "df -h"
    desc: "磁盘使用"
```

```yaml
# my-server.yml
extends:
  - _shared/common.yml    # 继承共享 alias

server:
  host: "..."
  ...

aliases:
  - name: deploy-backend
    script: deploy-backend.sh
    desc: "部署后端"
```

- `extends` 指向 `servers_dir` 下的其他 `.yml` 文件
- 继承的 alias 自动合并，本文件同名优先
- 支持多个继承目标

### 服务器配置（`{servers_dir}/{name}.yml`）

在 `servers/` 目录下为每个服务器创建一个 `.yml` 文件：

```yaml
# my-server.yml

server:
  # === 必填字段 ===
  host: "your.host.com"         # 服务器 IP 或域名
  user: "username"               # SSH 登录用户名

  # === 显示信息 ===
  name: "我的服务器"              # 显示名称
  desc: "应用服务器"              # 描述

  # 认证方式二选一
  password: "your-password"      # 密码认证
  # key: "/path/to/private_key"     # 或密钥认证
  # key_password: "passphrase"      # 密钥密码（可选）
  sudo_password: "sudo-pass"     # sudo 密码（可选，不填则用 password）

  # === 可选字段 ===
  port: 22                       # SSH 端口（默认 22）
  timeout: 30                    # SSH 连接超时（秒，默认 30）
  scripts_dir: "/home/user/scripts"  # 远程脚本存放目录
  system: "Ubuntu 24.04 LTS"    # 操作系统信息

  # 服务器级代理覆盖（可选，覆盖全局 config.yaml 设置）
  proxy:
    host: "127.0.0.1"
    port: 10808
    type: socks5                 # 目前仅支持 socks5

# 快捷命令 — 自动暴露为 MCP 工具
aliases:
  # script 类型：上传本地 .sh 文件后执行
  - name: deploy
    script: app-deploy.sh        # 脚本路径，相对于当前工作目录(CWD)
    desc: "部署应用"
    timeout: 600                 # 命令超时（秒），默认 300
    sudo: true                   # 可选，以 sudo 执行

  # inline 类型：直接在远程执行一行命令
  - name: logs
    inline: "docker logs --tail 100 my-app"
    desc: "查看日志"
    timeout: 10
```

## 使用方式

### 方式一：AI Agent 通过 MCP（主要）

注册后 Agent 自动发现所有服务器和 alias 工具。服务器通过 glob `{servers_dir}/*.yml` 发现，Agent 直接从解析后的 YAML 配置中获取主机、用户、系统、alias 等信息。

**可用 MCP 工具：**

| 工具 | 说明 |
|------|------|
| `ssh_list_servers` | 列出所有已配置的服务器 |
| `ssh_run` | 在远程服务器上执行命令 |
| `ssh_run_sudo` | 以 root 身份执行命令（需 `sudo_password`） |
| `ssh_upload_script` | 上传本地脚本，可选立即执行 |
| `ssh_run_script` | 运行已上传的脚本 |
| `ssh_list_scripts` | 列出远程脚本 |
| `ssh_upload_all_scripts` | 按 alias 定义上传所有脚本 |
| `ssh_run_alias` | 执行 alias 定义的快捷命令 |
| `ssh_list_aliases` | 列出服务器 alias |
| `ssh_alias:{server}:{name}` | **动态生成的一键 alias**（每个 alias 一个工具） |

**设计要点：**
- Alias 动态暴露为独立 MCP 工具（如 `ssh_alias:my-server:deploy`）
- 所有路径基于 `__file__` 动态解析，无硬编码

### 方式二：命令行（人工）

```bash
cd <本目录路径>

# 列出所有服务器
python cli.py list-servers

# 执行命令
python cli.py <server> run "<command>"

# root 执行（需 sudo_password）
python cli.py <server> sudo "<command>"

# 执行 alias
python cli.py <server> alias <name>

# 上传并立即执行脚本
python cli.py <server> upload /path/to/script.sh -r/--run

# 上传所有 alias 关联的脚本
python cli.py <server> upload-all

# 列出远程脚本 / alias
python cli.py <server> list-scripts
python cli.py <server> list-aliases
```

所有命令支持 `-t` / `--timeout`（秒，默认 300）。

### 方式三：Python 模块

```python
import sys
sys.path.insert(0, "<本目录路径>")
from ssh_client import pool

conn = pool.get("my-server")
result = conn.run("ls -la /opt")
print(result["stdout"])
```

## 示例脚本

### 部署脚本

```bash
#!/usr/bin/env bash
set -eo pipefail
cd /opt/my-app
git pull origin main 2>&1 | tail -5
npm install && npm run build
systemctl restart my-app
echo "部署完成"
```

### 健康检查脚本

```bash
#!/usr/bin/env bash
echo "--- 服务状态 ---"
systemctl status my-app --no-pager | head -10
echo "--- 磁盘使用 ---"
df -h /
echo "--- 内存 ---"
free -h
```

## 核心特性

- **连接池**：SSH 连接复用，60s 保活
- **SOCKS5 代理**：支持全局或按服务器配置代理，失败自动回退直连
- **Sudo 支持**：通过 `sudo_password` 以 root 执行命令 — `run_sudo()` API、`sudo` CLI 命令、alias 中 `sudo: true`
- **动态 MCP 工具**：Alias 自动暴露为 AI Agent 可一键调用的 MCP 工具
- **脚本管理**：上传、存储、执行远程脚本
- **零硬编码路径**：所有路径通过 `Path(__file__).parent` 动态解析
- **外部配置目录**：服务器配置文件与 skill 目录分离，通过 `config.yaml` 灵活指定位置
