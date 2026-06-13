# ssh-alias-mcp 技术文档

AI 驱动的服务器运维工具 — 支持 **MCP 模式**（AI Agent 调用）和 **CLI 模式**（人工使用）。所有模式共享同一套连接池、代理逻辑和配置。

## ✨ 核心亮点

### 一套配置，三种 Shell

同一套 YAML 配置，自动适配三种 Shell 环境：

| Shell | 适用场景 | 命令示例 |
|-------|---------|---------|
| `bash` | Linux/macOS 服务器 | `docker ps`, `systemctl restart` |
| `cmd` | Windows 服务器（CMD） | `cmd /c "dir /Q"`, `call deploy.bat` |
| `powershell` | Windows 服务器（PowerShell） | `Get-Service`, `Invoke-WebRequest` |

**你只需要在 YAML 中指定 `shell` 类型，其余交给工具自动处理：**

```yaml
# Linux 服务器
server:
  host: "192.168.1.100"
  shell: bash          # 自动使用 bash 命令模板

# Windows 服务器
server:
  host: "10.0.0.50"
  shell: powershell    # 自动使用 PowerShell 命令模板
```

内部通过 **命令模板字典** 实现，每种 Shell 有独立的命令集（mkdir、file_exists、run_script、move 等），无需在代码中写 `if/else` 分支。

### Alias 系统：一行 YAML = 一个 AI 技能

定义快捷命令，自动暴露为 AI Agent 可调用的 MCP 工具：

```yaml
aliases:
  - name: deploy
    script: deploy.sh
    desc: "部署应用"
    sudo: true

  - name: healthcheck
    inline: "docker ps && df -h /"
    desc: "健康检查"
```

**AI Agent 看到的效果：**
```
ssh_alias.my-server.deploy       # 一键部署
ssh_alias.my-server.healthcheck  # 一键健康检查
```

**人工 CLI 同样可用：**
```bash
python cli.py my-server alias deploy
python cli.py my-server alias healthcheck
```

**Alias 支持两种类型：**
- **Inline** — 直接执行一行命令（适合简单操作）
- **Script** — 上传本地脚本后执行（适合复杂部署）

**支持继承**：通过 `extends` 共享别名，50 台服务器只需维护一份公共别名。

## 快速开始

### AI Agent 使用（MCP 模式）

```bash
# Claude Code
claude mcp add ssh-alias-mcp python <本目录路径>/mcp_server.py

# OpenCode / Codex CLI — 在 mcp.json 中添加：
# {
#   "mcpServers": {
#     "ssh-alias-mcp": {
#       "command": "python",
#       "args": ["<本目录路径>/mcp_server.py"]
#     }
#   }
# }
```

注册后，AI Agent 可自动：
- 在任意已配置服务器上执行命令（`ssh_run` with `sudo: true`）
- 上传并执行脚本（`ssh_upload_script`、`ssh_run_script`）
- 一键调用预定义快捷命令（`ssh_alias.{server}.{name}`）
- 下载远程文件（`ssh_download`）

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
│   └── ssh-alias-mcp/         # 核心代码
│       ├── SKILL.md           # AI Agent skill 定义文件
│       ├── cli.py             # 命令行入口
│       ├── mcp_server.py      # MCP stdio 服务器
│       ├── ssh_client.py      # 核心模块（SSH 连接、代理、连接池）
│       ├── config.yaml        # 全局配置（servers_dir、代理、超时）
│       ├── DOCS.md            # 详细文档（英文）
│       └── DOCS.zh-CN.md      # 详细文档（中文，本文件）
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
│  └── MCP 工具调用（ssh_run, ssh_alias.*）         │
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
│  └── SFTP 脚本上传与下载                             │
└──────────────┬───────────────────────────────────────┘
               │ SSH
┌──────────────▼───────────────────────────────────────┐
│  远程服务器（Linux / Windows）                       │
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
  timeout: 30                 # 代理连接超时（秒）

server:
  timeout: 30                 # 默认 SSH 连接超时（秒）
```

代理优先尝试，失败后自动回退直连。

### 共享别名继承（`extends`）

#### 三级继承链

```
config.yaml（全局默认：代理、超时）
    ↓ extends
_shared/*.yml（共享连接信息、共享 alias）
    ↓ extends
服务器 YAML（你的服务器 — 覆盖 + 追加）
```

#### 多重继承

一个服务器 YAML 可以继承多个文件 — 分层组合配置：

```yaml
# my-server.yml
extends:
  - _shared/conn-base.yml    # ① 共享 host、user、scripts_dir
  - _shared/common.yml       # ② 共享 alias

server:
  host: "198.51.100.10"      # 覆盖 ① 中的 host
  port: 22
  password: "xxx"

aliases:                      # 追加 — 本地 alias 叠加在继承之上
  - name: deploy
    script: deploy.sh
```

#### 合并规则

| 字段 | 合并行为 |
|------|----------|
| `server` | 浅合并 — 本地覆盖基础 |
| `aliases` | 全部继承自基础文件。同名 `name` 覆盖，不同名追加 |
| `security`（whitelist/blacklist/command_template） | 基础文件设定，本地继承。本地有则覆盖 |
| `proxy` | 同 security — 本地无则用基础文件 |
| `allowed_local_paths` / `allowed_remote_paths` | 同 security — 本地无则用基础文件 |

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
  group: "部门/团队"              # 分组标识（多级路径格式，用于分类和排序）

  # 认证方式二选一
  password: "your-password"      # 密码认证
  # key: "/path/to/private_key"     # 或密钥认证
  # key_password: "passphrase"      # 密钥密码（可选）
  sudo_password: "sudo-pass"     # sudo 密码（可选，不填则用 password）

  # === 可选字段 ===
  port: 22                       # SSH 端口（默认 22）
  timeout: 30                    # SSH 连接超时（秒，默认 30）
  scripts_dir: "/home/user/scripts"  # 远程脚本存放目录
  shell: "bash"                  # Shell 类型（bash / cmd / powershell，默认 bash）
  system: "Ubuntu 24.04 LTS"    # 操作系统信息

  # 服务器级代理覆盖（可选，覆盖全局 config.yaml 设置）
  proxy:
    host: "127.0.0.1"
    port: 10808

  # 安全：命令过滤（覆盖全局 config.yaml）
  blacklist:
    - "rm -rf|mkfs|dd "          # 正则模式，匹配即拦截
  whitelist:
    - "ls|df|docker|tail"        # 正则模式，仅允许匹配命令
  command_template: "cd /opt/app && <command>"  # 自动包装命令

  # 上传/下载路径限制（空 = 无限制）
  allowed_local_paths:
    - "/home/bit/scripts"
  allowed_remote_paths:
    - "/home/bit/scripts"

# 快捷命令 — 自动暴露为 MCP 工具
aliases:
  # script 类型：上传本地脚本文件后执行
  - name: deploy
    script: app-deploy.sh        # 脚本路径，相对于 YAML 文件所在目录
    desc: "部署应用"
    timeout: 600                 # 命令超时（秒），默认 300
    sudo: true                   # 可选，以 sudo 执行

  # inline 类型：直接在远程执行一行命令
  - name: logs
    inline: "docker logs --tail 100 my-app"
    desc: "查看日志"
    timeout: 10
```

## Shell 类型支持

### 三种 Shell 类型

| Shell | 适用场景 | 命令模板 |
|-------|---------|---------|
| `bash` | Linux/macOS 服务器 | 完整 bash 命令集 |
| `cmd` | Windows 服务器（CMD） | `cmd /c "..."` 包装 |
| `powershell` | Windows 服务器（PowerShell） | 原生 PowerShell 语法 |

### Shell 命令模板对比

| 模板名 | bash | cmd | powershell |
|--------|------|-----|------------|
| `mkdir` | `mkdir -p {path}` | `cmd /c "mkdir \\"{path}\\" 2>nul"` | `New-Item -ItemType Directory -Path "{path}" -Force` |
| `file_exists` | `test -f {path}` | `cmd /c "if exist \\"{path}\\" echo exists"` | `Test-Path "{path}"` |
| `run_script` | `bash {path}` | `cmd /c "call {path}"` | `& "{path}"` |
| `chmod` | `chmod {mode:o} {path}` | 不支持 | 不支持 |
| `stat_owner` | `stat -c '%U:%G' {path}` | 不支持 | 不支持 |
| `stat_mode` | `stat -c '%a' {path}` | 不支持 | 不支持 |
| `rm_dir` | `rm -rf {path}` | `cmd /c "rmdir /S /Q \\"{path}\\""` | `Remove-Item -Recurse -Force "{path}"` |
| `cp_r` | `cp -r {src} {dst}` | `cmd /c "xcopy /Y \\"{src}\\" \\"{dst}\\" /E /I"` | `Copy-Item -Recurse "{src}" "{dst}"` |
| `move` | `mv {tmp} {target}` | `cmd /c "move /y \\"{tmp}\\" \\"{target}\\""` | `Move-Item -Force "{tmp}" "{target}"` |
| `chown` | `chown {user} {path}` | 不支持 | 不支持 |
| `chown_r` | `chown -R {user}:{user} {path}` | 不支持 | 不支持 |
| `list_dir` | `ls -la {path}/` | `dir "{path}" /Q` | `Get-ChildItem -Path "{path}" \| Format-List` |
| `install` | 保留 owner/mode | 仅 move | 仅 move |
| `tmp_prefix` | `/tmp` | `%TEMP%` | `$env:TEMP` |

### 功能差异

| 功能 | bash | cmd | powershell |
|------|------|-----|------------|
| sudo 执行 | ✅ | ❌ | ❌ |
| 权限管理（chmod/chown） | ✅ | ❌ | ❌ |
| 文件所有者检查 | ✅ | ❌ | ❌ |
| 管道命令 | ✅ | ✅ | ✅ |
| 多行命令 | ✅ | ✅ | ✅ |
| 脚本执行 | ✅ | ✅ | ✅ |
| 目录操作 | ✅ | ✅ | ✅ |
| 文件上传/下载 | ✅ | ✅ | ✅ |

## 使用方式

### 方式一：AI Agent 通过 MCP（主要）

注册后 Agent 自动发现所有服务器和 alias 工具。服务器通过 glob `{servers_dir}/*.yml` 发现，Agent 直接从解析后的 YAML 配置中获取主机、用户、系统、alias 等信息。

**可用 MCP 工具：**

| 工具 | 说明 |
|------|------|
| `ssh_list_servers` | 列出所有已配置的服务器 |
| `ssh_run` | 在远程服务器上执行命令，设置 `sudo: true` 以 root 身份执行 |
| `ssh_upload_script` | 上传本地脚本，可选立即执行 |
| `ssh_download` | 从远程服务器下载文件到本地，支持 sudo/overwrite 参数 |
| `ssh_run_script` | 运行已上传的脚本 |
| `ssh_list_scripts` | 列出远程脚本 |
| `ssh_upload_all_scripts` | 按 alias 定义上传所有脚本 |
| `ssh_run_alias` | 执行 alias 定义的快捷命令 |
| `ssh_list_aliases` | 列出服务器 alias |
| `ssh_alias.{server}.{name}` | **动态生成的一键 alias**（每个 alias 一个工具） |

**设计要点：**
- Alias 动态暴露为独立 MCP 工具（如 `ssh_alias.my-server.deploy`）
- 所有路径基于 `__file__` 动态解析，无硬编码

**MCP 协议行为：**
- stdio 服务端使用 JSON-RPC 2.0，并支持协商 `2025-11-25`、`2025-06-18`、`2025-03-26`、`2024-11-05`。
- `notifications/initialized` 以及其他 JSON-RPC notification 不返回响应。
- JSON 解析错误返回 `-32700` 且 `id: null`；非法 request 结构返回 `-32600`。
- 未知 method 返回 `-32601`；未知工具和非法工具参数返回 `-32602`。
- SSH 连接失败、远程命令非零退出码等工具执行失败，会作为 MCP 工具结果返回，并设置 `isError: true`。
- `tools.listChanged` 声明为 `false`；如果预期服务器 YAML 发生变化，客户端应重新请求 `tools/list`。

### 方式二：命令行（人工）

```bash
cd <本目录路径>

# 列出所有服务器
python cli.py list-servers

# 执行命令
python cli.py <server> run "<command>"

# root 执行（需 sudo_password）
python cli.py <server> run "<command>" -s

# 执行已上传脚本
python cli.py <server> run-script <脚本名> [-s]

# 执行 alias
python cli.py <server> alias <name>

# 上传并立即执行脚本
python cli.py <server> upload /path/to/script.sh -r/--run [-s]

# 上传所有 alias 关联的脚本
python cli.py <server> upload-all [-s]

# 列出远程脚本 / alias
python cli.py <server> list-scripts [-s]
python cli.py <server> list-aliases

# 下载文件
python cli.py <server> download /remote/path ./local/path [-s]
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

## 安全：命令过滤 + 路径限制

### 命令过滤

用正则白名单/黑名单保护你的服务器：

```yaml
# 服务器 YAML 覆盖
server:
  blacklist:
    - "rm -rf"                 # 此服务器拦截 rm -rf
  whitelist:
    - "ls|df|docker|tail"      # 仅允许这些命令
  command_template: "cd /opt/app && <command>"  # 自动 cd 再执行
```

- **黑名单**：命令匹配任一模式即拒绝执行。
- **白名单**：配置后（非空），仅执行匹配至少一个模式的命令。
- **命令模板**：所有命令包装。使用 `<command>` 作为占位符。

### 路径限制

限制 upload/download 可操作的本地/远程路径：

```yaml
server:
  allowed_local_paths:
    - "/home/bit/scripts"      # 允许上传的本地路径
  allowed_remote_paths:
    - "/home/bit/scripts"      # 允许上传/下载的远程路径
```

- 空（默认）：无限制。
- 设置后：仅允许指定目录下的路径。
- 同时作用于 `upload` 和 `download`。

## 文件传输：上传 + 下载

### 上传

```bash
# 上传单个脚本
python cli.py my-server upload script.sh

# 上传并立即执行
python cli.py my-server upload script.sh -r

# 自定义远程文件名
python cli.py my-server upload script.sh -n remote-name.sh

# 按 alias 上传所有脚本
python cli.py my-server upload-all
```

### 下载

```bash
# 从远程下载单个文件到本地
python cli.py my-server download /remote/file.log ./local/file.log

# 设置超时
python cli.py my-server download /remote/file.log ./local/file.log -t 600

# 递归下载整个目录
python cli.py my-server download /var/log ./logs

# 只下载 .log 文件
python cli.py my-server download /var/log ./logs -p "\\.log$"
```

下载遵守 `allowed_remote_paths` 限制。下载目录时，本地目录结构会保持与远程一致。不匹配 `pattern` 正则的文件会被跳过。

### sudo 文件传输

```bash
# sudo 上传（通过 /tmp 暂存 + sudo mv）
python cli.py my-server upload script.sh -s

# sudo 下载（通过 /tmp 暂存 + chown + SFTP）
python cli.py my-server download /root/secret.txt ./secret.txt -s
```

## 路径解析

### 相对路径

相对路径脚本相对于 YAML 文件所在目录解析：

```yaml
# servers/prod.yml
aliases:
  - name: deploy
    script: deploy.sh           # → servers/deploy.sh
  - name: backup
    script: ./scripts/backup.sh # → servers/scripts/backup.sh
```

### 绝对路径

绝对路径脚本上传到 `scripts_dir/external/` 目录：

```yaml
aliases:
  - name: external-tool
    script: /opt/tools/tool.sh  # → /home/user/scripts/external/tool.sh
```

### Windows 路径

Windows 路径自动转换为 WSL 格式（Linux 环境下）：

```
D:\agents\servers\script.sh → /mnt/d/agents/servers/script.sh
```

## 功能汇总

| 分类 | 功能 | 说明 | 配置 / API |
|------|------|------|------------|
| **连接** | 直连 SSH | 连接远程服务器 | `server.host`, `server.port` |
| | 密码认证 | 密码登录 | `server.password` |
| | 密钥认证 | SSH 密钥登录（可选密码） | `server.key`, `server.key_password` |
| | SOCKS5 代理 | 代理优先，失败自动回退直连 | `config.yaml` 代理 / `server.proxy` |
| | 连接池 | 自动复用，60 秒保活 | 全局 `pool.get(name)` |
| **命令执行** | `run()` | 执行命令，可选 sudo | `ssh_run` / CLI `run -s` |
| | `run_alias()` | 执行预定义 alias | `ssh_alias.server.name` / CLI `alias` |
| | 命令模板 | 包装所有命令（如自动 cd） | `server.command_template` |
| | 命令过滤 | 正则白名单/黑名单 | `server.blacklist` / `server.whitelist` |
| **脚本管理** | `upload_script()` | 上传脚本 | CLI `upload` |
| | `run_script()` | 执行已上传脚本 | `ssh_run_script` / CLI `run-script` |
| | `upload_all_scripts()` | 按 alias 定义上传所有脚本 | CLI `upload-all` |
| | `list_scripts()` | 列出远程已上传脚本 | CLI `list-scripts` |
| | 上传后执行 | `upload_script()` 传入 `run_immediately=True` | `ssh_upload_script` / CLI `upload -r` |
| **文件传输** | `download()` | 下载单文件或递归目录 | `ssh_download` / CLI `download` |
| | 下载过滤 | 正则过滤文件名 | `pattern` 参数 |
| | 下载计数 | 下载目录时返回文件数量 | `count` 字段 |
| | 下载 sudo | 读取 root 拥有的文件（通过 /tmp 暂存） | `sudo: true` 参数 |
| | 下载覆盖控制 | 本地文件已存在时跳过 | `overwrite` 参数（默认：true） |
| | 上传覆盖控制 | 远程脚本已存在时跳过 | `overwrite` 参数（默认：true） |
| | 路径限制 | 限制上传/下载路径 | `server.allowed_local_paths` / `server.allowed_remote_paths` |
| **Alias 系统** | Inline alias | 直接执行命令字符串 | `aliases[].inline` |
| | Script alias | 上传 + 执行脚本文件 | `aliases[].script` |
| | Sudo alias | 以 root 权限执行 | `aliases[].sudo: true` |
| | 继承 | 通过 `extends` 共享 alias | `extends: [_shared/common.yml]` |
| **MCP 工具** | 动态工具 | 每个 alias 自动暴露为 `ssh_alias.server.name` | 运行时自动生成 |
| | 工具发现 | `ssh_list_servers` / `ssh_list_aliases` | 静态工具 |
| | 读写标记 | 工具标记 readOnly/destructive | 自动设置 |
| **安全** | 命令黑名单 | 正则模式拦截命令 | `server.blacklist` |
| | 命令白名单 | 仅允许匹配的命令 | `server.whitelist` |
| | 路径限制 | 限制上传/下载路径 | `server.allowed_local_paths` / `server.allowed_remote_paths` |
| | 代理错误日志 | 代理失败记录到文件 | `proxy_error.log` |

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
- **Sudo 支持**：通过 `sudo_password` 以 root 执行命令 — `run()` API 传 `sudo=True`、CLI `run -s`、alias 中 `sudo: true`
- **动态 MCP 工具**：Alias 自动暴露为 AI Agent 可一键调用的 MCP 工具
- **脚本管理**：上传、存储、执行远程脚本
- **安全过滤**：正则命令过滤 + 路径限制
- **文件传输**：SFTP 上传和下载，支持路径限制
- **零硬编码路径**：所有路径通过 `Path(__file__).parent` 动态解析
- **外部配置目录**：服务器配置文件与 skill 目录分离，通过 `config.yaml` 灵活指定位置
