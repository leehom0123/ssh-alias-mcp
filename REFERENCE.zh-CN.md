# REFERENCE — 服务器配置、别名、安全与文件传输

## 架构

```
┌──────────────────────────────────────────────────────┐
│  AI Agent (Claude Code / Codex / OpenCode / ...)      │
│  └── MCP 工具调用 (ssh_run, ssh_alias.*)         │
└──────────────┬───────────────────────────────────────┘
               │ JSON-RPC over stdio
┌──────────────▼───────────────────────────────────────┐
│  mcp_server.py  (MCP stdio 服务器)                   │
│  └── 动态将别名暴露为 MCP 工具                         │
└──────────────┬───────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────┐
│  ssh_client.py  (共享核心)                            │
│  ├── 连接池 (连接复用，默认 15s 保活)                  │
│  ├── 主机密钥校验 (known_hosts, tofu/strict)          │
│  ├── SOCKS5 代理 + 直连自动回退                        │
│  └── SFTP 通用文件/目录传输（脚本传输为其包装）          │
└──────────────┬───────────────────────────────────────┘
               │ SSH
┌──────────────▼───────────────────────────────────────┐
│  远程服务器 (Linux / Windows)                         │
└──────────────────────────────────────────────────────┘
```

## 目录结构

```
├── <skills-dir>/
│   └── ssh-alias-mcp/         # 核心代码
│       ├── SKILL.md           # AI Agent 技能定义
│       ├── REFERENCE.md       # 详细参考文档（英文版）
│       ├── REFERENCE.zh-CN.md # 详细参考文档（中文版）
│       ├── CLI_USAGE.md       # CLI 命令参考（英文版）
│       ├── CLI_USAGE.zh-CN.md # CLI 命令参考（中文版）
│       ├── INSTALL.md         # AI Agent 安装指南（英文版）
│       ├── INSTALL.zh-CN.md   # AI Agent 安装指南（中文版）
│       ├── cli.py             # CLI 入口
│       ├── mcp_server.py      # MCP stdio 服务器
│       ├── ssh_client.py      # 核心模块（SSH 连接、代理、连接池）
│       ├── config.yaml        # 全局配置（servers_dir、代理、超时）
│       └── requirements.txt   # Python 依赖
│
└── servers/                   # 服务器配置目录（路径在 config.yaml 中配置）
    ├── my-server.yml          # 服务器连接信息 + 别名定义
    ├── _shared/common.yml     # 共享别名（通过 extends 继承）
    └── my-server/             # 此服务器的本地 shell 脚本（.sh）
```

> 服务器配置 `servers/` 与技能目录分离，便于管理和凭证隔离。路径通过 `config.yaml` 中的 `servers_dir` 配置（支持绝对路径或相对路径）。

## 全局配置（`config.yaml`）

```yaml
# servers/ 目录路径（绝对路径或相对于此文件的路径）
servers_dir: "../servers"

proxy:
  enabled: false              # 全局启用 SOCKS5 代理
  host: "127.0.0.1"           # 代理地址
  port: 1080                  # 代理端口
  timeout: 30                 # 代理连接超时（秒）

server:
  timeout: 30                 # 默认 SSH 连接超时（秒）
  auto_reconnect: true        # 连接丢失时自动重连
  reconnect_interval: 5       # 重连尝试间隔（秒）
  keepalive_interval: 15      # SSH 保活间隔（秒，0 = 关闭）
  host_key_checking: tofu     # 主机密钥校验模式：tofu / strict / insecure

security:
  blacklist: []               # 阻止命令的正则表达式（在命令中任意位置匹配即阻止）
  whitelist: []               # 允许命令的正则表达式（整条命令必须完整匹配其一；空 = 无限制）
  command_template: ""        # 包装所有命令的命令模板
```

全局 `security` 段对所有服务器生效；服务器 YAML 中如配置了
`blacklist`/`whitelist`/`command_template`，则覆盖全局设置。

`host_key_checking` 控制 SSH 主机密钥校验（在发送任何密码/密钥口令**之前**完成校验）：

| 模式 | 行为 |
|------|------|
| `tofu`（默认） | 首次信任：密钥写入 `<技能目录>/known_hosts`；之后密钥变化将中断连接（防中间人）。 |
| `strict` | 仅允许 known_hosts（系统 + 本地）中已有的密钥；未知或变化的密钥中断连接。 |
| `insecure` | 不校验（不推荐） |

服务器密钥确属合法变更时，从 `<技能目录>/known_hosts` 删除对应条目即可重新登记。

代理优先尝试，失败后自动回退到直连。

`auto_reconnect` 用于控制连接请求失败后的后台重连。连续连接失败少于 5 次时，
按 `reconnect_interval` 指定的秒数重试；第 5 次失败后降为每 60 秒重试一次。
即使处于冷却期，新的 CLI 或 MCP 请求也会立即触发一次连接尝试。
设置 `auto_reconnect: false` 可完全关闭后台重连。

## 服务器配置（`{servers_dir}/{name}.yml`）

在 `servers/` 目录中为每台服务器创建 `.yml` 文件：

```yaml
# my-server.yml

server:
  # === 必填字段 ===
  host: "your.host.com"         # 服务器 IP 或主机名
  user: "username"               # SSH 登录用户名

  # === 显示信息 ===
  name: "My Server"              # 显示名称
  desc: "Application server"    # 描述
  group: "dept/team"            # 分组标识符（多级路径用于排序）

  # 认证方式（二选一）
  password: "your-password"      # 密码认证
  # key: "/path/to/private_key"     # 或密钥认证
  # key_password: "passphrase"      # 密钥密码（可选）
  sudo_password: "sudo-pass"     # Sudo 密码（可选，默认为 password）

  # === 可选字段 ===
  port: 22                       # SSH 端口（默认 22）
  timeout: 30                    # SSH 连接超时（秒，默认 30）
  scripts_dir: "/home/user/scripts"  # 远程脚本目录
  shell: "bash"                  # Shell 类型（bash / cmd / powershell，默认 bash）
  system: "Ubuntu 24.04 LTS"    # 操作系统信息
  host_key_checking: "tofu"      # 覆盖全局设置：tofu / strict / insecure

  # 服务器级代理覆盖（可选，覆盖全局 config.yaml）
  proxy:
    host: "127.0.0.1"
    port: 10808

  # 安全：命令过滤（存在时覆盖全局 config.yaml）
  blacklist:
    - "rm -rf|mkfs|dd "          # 正则表达式，命令中任意位置匹配则阻止
  whitelist:
    - "(ls|df|docker|tail).*"    # 正则表达式，整条命令必须完整匹配其一
  command_template: "cd /opt/app && <command>"  # 自动包装命令

  # 上传/下载路径限制（空 = 无限制）
  allowed_local_paths:
    - "/home/bit/scripts"
  allowed_remote_paths:
    - "/home/bit/scripts"

# 快捷命令 — 自动暴露为 MCP 工具
aliases:
  # 脚本类型：上传本地脚本文件后执行
  - name: deploy
    script: app-deploy.sh        # 脚本路径，相对于 YAML 文件目录
    desc: "Deploy application"
    timeout: 600                 # 命令超时（秒），默认 300
    sudo: true                   # 可选，以 root 权限执行

  # 内联类型：直接在远程执行命令
  - name: logs
    inline: "docker logs --tail 100 my-app"
    desc: "View logs"
    timeout: 10
```

## 共享别名继承（`extends`）

### 三级继承链

```
config.yaml (全局默认值：代理、超时)
    ↓ extends
_shared/*.yml (共享连接信息、共享别名)
    ↓ extends
服务器 YAML (你的服务器 — 覆盖 + 追加)
```

### 多重继承

服务器 YAML 可以从多个文件继承 — 层叠式配置组合：

```yaml
# my-server.yml
extends:
  - _shared/conn-base.yml    # ① 共享主机、用户、scripts_dir
  - _shared/common.yml       # ② 共享别名

server:
  host: "198.51.100.10"      # 覆盖 ① 的主机
  port: 22
  password: "xxx"

aliases:                      # 追加 — 本地别名胜承继承的别名
  - name: deploy
    script: deploy.sh
```

### 合并规则

| 字段 | 合并行为 |
|------|---------|
| `server` | 浅合并 — 本地覆盖基础 |
| `aliases` | 全部继承自基础。同名覆盖，不同名追加 |
| `security`（whitelist/blacklist/command_template） | 基础设置默认值，本地继承。本地存在则覆盖 |
| `proxy` | 同安全 — 本地回退到基础 |
| `allowed_local_paths` / `allowed_remote_paths` | 同安全 — 本地回退到基础 |

## Shell 类型支持

### 三种 Shell 类型

| Shell | 用途 | 命令模板 |
|-------|------|---------|
| `bash` | Linux/macOS 服务器 | 完整 bash 命令集 |
| `cmd` | Windows 服务器（CMD） | `cmd /c "..."` 包装 |
| `powershell` | Windows 服务器（PowerShell） | 原生 PowerShell 语法 |

### Shell 命令模板对比

所有占位符（`{path}`、`{src}`、`{dst}`、`{tmp}`、`{target}`、`{user}`）的值都由 `_cmd`
按目标 shell 统一加引号转义（bash 用 POSIX 引号、cmd 用双引号、powershell 用单引号翻倍），
从而阻断经由路径/脚本名注入命令的攻击。包含控制字符（cmd/powershell 上还禁止 shell 元字符）
的值会被直接拒绝。

| 模板 | bash | cmd | powershell |
|------|------|-----|------------|
| `mkdir` | `mkdir -p {path}` | `mkdir {path} 2>nul` | `New-Item -ItemType Directory -Path {path} -Force` |
| `file_exists` | `test -f {path}` | `if exist {path} (echo exists) else exit 1` | `if (Test-Path {path}) { exit 0 } else { exit 1 }` |
| `run_script` | `bash {path}` | `call {path}` | `& {path}` |
| `chmod` | `chmod {mode} {path}` | 不支持 | 不支持 |
| `stat_owner` | `stat -c '%U:%G' {path}` | 不支持 | 不支持 |
| `stat_mode` | `stat -c '%a' {path}` | 不支持 | 不支持 |
| `rm_dir` | `rm -rf {path}` | `rmdir /S /Q {path}` | `Remove-Item -Recurse -Force {path}` |
| `cp_r` | `cp -r {src} {dst}` | `xcopy /Y {src} {dst} /E /I` | `Copy-Item -Recurse {src} {dst}` |
| `move` | `mv {tmp} {target}` | `move /y {tmp} {target}` | `Move-Item -Force {tmp} {target}` |
| `chown` | `chown {user} {path}` | 不支持 | 不支持 |
| `chown_r` | `chown -R {user}:{user} {path}` | 不支持 | 不支持 |
| `list_dir` | `ls -la {path}` | `dir {path} /Q` | `Get-ChildItem -Path {path} \| Format-List` |
| `install` | 保留所有者/模式 | 仅移动 | 仅移动 |
| `tmp_prefix` | `/tmp` | `%TEMP%` | `$env:TEMP` |

`file_exists` 通过命令退出码表达结果（0 = 存在，非 0 = 不存在）。

### 功能差异

| 功能 | bash | cmd | powershell |
|------|------|-----|------------|
| Sudo 执行 | ✅ | ❌ | ❌ |
| 权限管理（chmod/chown） | ✅ | ❌ | ❌ |
| 文件所有者检查 | ✅ | ❌ | ❌ |
| 管道命令 | ✅ | ✅ | ✅ |
| 多行命令 | ✅ | ✅ | ✅ |
| 脚本执行 | ✅ | ✅ | ✅ |
| 目录操作 | ✅ | ✅ | ✅ |
| 文件上传/下载 | ✅ | ✅ | ✅ |

## 文件与脚本传输

`upload_file()` / `ssh_upload_file` 是通用的单文件上传能力：需要同时提供本地路径和远程目标路径。
`download()` / `ssh_download` 已是通用下载能力，支持单文件及递归目录下载；`download_file()` / `ssh_download_file` 是其单文件入口，与 `upload_file()` 对称。

脚本工具不另行实现传输：

- `upload_script()` 将远程目标限定为 `scripts_dir/<script_name>`，调用 `upload_file()`，然后可选地执行脚本；
- `download_script()` 将远程来源限定为 `scripts_dir/<script_name>`，调用 `download_file()`。

CLI 对应命令：

```bash
python cli.py my-server upload-file ./config.json /etc/myapp/config.json -s
python cli.py my-server download /var/log/app.log ./app.log
python cli.py my-server download-script deploy.sh ./deploy.sh
python cli.py my-server download-file /etc/myapp/app.log ./app.log
```

## 安全：命令过滤 + 路径限制

### 命令过滤

使用正则表达式白名单/黑名单保护服务器：

```yaml
# 服务器 YAML 覆盖（也可在全局 config.yaml 的 security: 段设置）
server:
  blacklist:
    - "rm -rf"                 # 阻止此服务器上的 rm -rf
  whitelist:
    - "(ls|df|docker|tail).*"  # 仅允许完整匹配模式的命令
  command_template: "cd /opt/app && <command>"  # 自动 cd 后执行
```

- **黑名单**：命令中任意位置匹配任何模式则被拒绝。
- **白名单**：配置后（非空），整条命令必须与至少一个模式**完整匹配**（`re.fullmatch`）。
  这避免了 `ls; rm -rf /` 借助只锚定开头的规则绕过。请写成覆盖整条命令的模式，
  如 `ls.*` 或 `(ls|df).*`，而不是裸的 `ls|df`。
- **命令模板**：包装所有命令。使用 `<command>` 作为占位符。
- 过滤只作用于智能体发起的命令（`ssh_run`、内联别名）。程序内部生成的辅助命令
  （mkdir/暂存等）不受过滤约束，但其路径参数会统一 shell 转义并校验目录穿越。

> 正则黑名单是护栏而非沙箱：经过 shell 混淆的命令可能绕过它。硬性限制请使用白名单。

### 路径限制

限制上传/下载路径：

```yaml
server:
  allowed_local_paths:
    - "/home/bit/scripts"      # 允许的本地上传路径
  allowed_remote_paths:
    - "/home/bit/scripts"      # 允许的远程上传/下载路径
```

- 空（默认）：无限制。
- 设置后：仅允许指定目录下的路径，按目录边界比较（`/allowed/dir` 允许 `/allowed/dir/x`，
  但不允许 `/allowed/dir-evil`）。远程路径按 posixpath 词法归一化；本地路径在本地文件系统上解析。
- 同时适用于 `upload` 和 `download`。

### Sudo 机制

Sudo 通过 `sudo -S -p '' bash -c '<命令>'` 执行。密码经 SSH 通道 **stdin** 传入，
从不出现在远程命令行中（`ps` 无法读取）。密码必须配置（`sudo_password`，缺省回退到 `password`）。

### 并发能力

同一服务器上的命令通过独立 SSH 通道并发执行——长命令（或正在重连的主机）不会阻塞
其他线程对同一服务器的操作。命令超时后通道被关闭并返回 `code: -1`，同时提示远程进程
可能仍在运行；递归目录下载最深 48 层。

## 文件传输：上传 + 下载

### 上传

```bash
# 上传单个脚本
python cli.py my-server upload script.sh

# 上传并立即执行
python cli.py my-server upload script.sh -r

# 自定义远程文件名
python cli.py my-server upload script.sh -n remote-name.sh

# 上传所有别名脚本
python cli.py my-server upload-all
```

### 下载

```bash
# 下载单个文件
python cli.py my-server download /remote/file.log ./local/file.log

# 设置超时
python cli.py my-server download /remote/file.log ./local/file.log -t 600

# 递归目录下载
python cli.py my-server download /var/log ./logs

# 仅下载 .log 文件
python cli.py my-server download /var/log ./logs -p "\\.log$"
```

下载遵守 `allowed_remote_paths` 限制。目录下载保留远程目录结构。不匹配 `pattern` 正则表达式的文件将被跳过。

### Sudo 文件传输

```bash
# Sudo 上传（通过 /tmp 暂存 + sudo mv）
python cli.py my-server upload script.sh -s

# Sudo 下载（通过 /tmp 暂存 + chown + SFTP）
python cli.py my-server download /root/secret.txt ./secret.txt -s
```

## 路径解析

### 相对路径

相对脚本路径相对于 YAML 文件目录解析：

```yaml
# servers/prod.yml
aliases:
  - name: deploy
    script: deploy.sh           # → servers/deploy.sh
  - name: backup
    script: ./scripts/backup.sh # → servers/scripts/backup.sh
```

### 绝对路径

绝对路径脚本上传到 `scripts_dir/external/`：

```yaml
aliases:
  - name: external-tool
    script: /opt/tools/tool.sh  # → /home/user/scripts/external/tool.sh
```

### Windows 路径

Windows 路径在 Linux 环境中自动转换为 WSL 格式：

```
D:\agents\servers\script.sh → /mnt/d/agents/servers/script.sh
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
echo "Deployment complete"
```

### 健康检查脚本

```bash
#!/usr/bin/env bash
echo "--- Service Status ---"
systemctl status my-app --no-pager | head -10
echo "--- Disk Usage ---"
df -h /
echo "--- Memory ---"
free -h
```

## 功能汇总

| 类别 | 功能 | 描述 | 配置 / API |
|------|------|------|-----------|
| **连接** | 直连 SSH | 连接远程服务器 | `server.host`, `server.port` |
| | 密码认证 | 密码登录 | `server.password` |
| | 密钥认证 | SSH 密钥登录（可选密码） | `server.key`, `server.key_password` |
| | SOCKS5 代理 | 代理优先，失败自动回退直连 | `config.yaml` proxy / `server.proxy` |
| | 主机密钥校验 | 认证前校验 known_hosts（tofu/strict/insecure） | `server.host_key_checking` |
| | 连接池 | 自动复用，保活（默认 15s） | 全局 `pool.get(name)` |
| | 命令并发 | 同一服务器多命令走独立通道并行 | 内置（无同服务器串行锁） |
| **命令执行** | `run()` | 执行命令，可选 sudo，实时流式输出 | `ssh_run` / CLI `run -s` |
| | `run_alias()` | 执行预定义别名（流式输出） | `ssh_alias.server.name` / CLI `alias` |
| | 命令模板 | 包装所有命令（如自动 cd） | `server.command_template` |
| | 命令过滤 | 黑名单 search + 白名单 fullmatch | `server.blacklist` / `server.whitelist` |
| **脚本管理** | `upload_script()` | 上传脚本 | CLI `upload` |
| | `run_script()` | 执行已上传脚本 | `ssh_run_script` / CLI `run-script` |
| | `upload_all_scripts()` | 上传所有别名定义的脚本 | CLI `upload-all` |
| | `list_scripts()` | 列出远程已上传脚本 | CLI `list-scripts` |
| | 上传后执行 | `upload_script(run_immediately=True)` | `ssh_upload_script` / CLI `upload -r` |
| **文件传输** | `download()` | 下载单个文件或递归目录 | `ssh_download` / CLI `download` |
| | `upload_file()` | 上传单个本地文件到指定的远程路径 | `ssh_upload_file` / CLI `upload-file` |
| | `download_file()` | 下载单个远程文件到指定的本地路径（委托 `download()`） | `ssh_download_file` / CLI `download-file` |
| | 下载过滤 | 正则表达式过滤文件名 | `pattern` 参数 |
| | 下载计数 | 返回目录下载的文件数 | `count` 字段 |
| | 下载 sudo | 读取 root 拥有的文件（通过 /tmp 暂存） | `sudo: true` 参数 |
| | 下载覆盖控制 | 本地目标已存在时报错失败（单文件与目录下载均适用） | `overwrite` 参数（默认：true） |
| | 上传覆盖控制 | 远程目标已存在时报错失败（`upload_all_scripts` 则跳过已存在文件） | `overwrite` 参数（默认：true） |
| | 路径限制 | 限制上传/下载路径 | `server.allowed_local_paths` / `server.allowed_remote_paths` |
| **别名系统** | 内联别名 | 直接执行命令字符串 | `aliases[].inline` |
| | 脚本别名 | 上传 + 执行脚本文件 | `aliases[].script` |
| | Sudo 别名 | 以 root 权限执行 | `aliases[].sudo: true` |
| | 继承 | 通过 `extends` 共享别名 | `extends: [_shared/common.yml]` |
| **MCP 工具** | 动态工具 | 每个别名自动暴露为 `ssh_alias.server.name` | 运行时自动生成 |
| | 工具发现 | `ssh_list_servers` / `ssh_list_aliases` | 静态工具 |
| | 读写标记 | 工具标记 readOnly/destructive | 自动设置 |
| **安全** | 命令黑名单 | 正则表达式阻止命令（search 匹配） | `server.blacklist` |
| | 命令白名单 | 仅允许完整匹配的命令（fullmatch） | `server.whitelist` |
| | 参数 shell 转义 | 模板路径按 shell 转义，拒绝目录穿越 | 内置（`_cmd` / `_safe_relpath`） |
| | Sudo stdin 传密 | 密码从不出现在远程 argv | 内置（`sudo -S`） |
| | 路径限制 | 限制上传/下载路径（目录边界匹配） | `server.allowed_local_paths` / `server.allowed_remote_paths` |
| | 代理错误日志 | 代理失败记录到文件 | `proxy_error.log` |
